# @author: liuqinhe
"""摄取拆分：内置知识库与用户上传文档走不同入口，但共用切分与向量化。

关键不变量：写进 Milvus 的每一行都必须带 tenant_id/doc_id/source
（schema 是 enable_dynamic_field=False，缺字段会被 Milvus 拒绝），
且内置文档的 tenant_id 必须是空串、上传文档必须是其所属租户。
"""

from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import KnowledgeDocument
from app.rag import ingest


@pytest.fixture
def captured(monkeypatch, tmp_path):
    """打桩向量化与 Milvus，收集写入的行。"""
    rows: list[dict] = []

    async def _embed(texts):
        return [[0.1, 0.2, 0.3] for _ in texts]

    monkeypatch.setattr(ingest.client, "embed", _embed)
    monkeypatch.setattr(ingest.store, "insert", lambda r: rows.extend(r) or len(r))
    monkeypatch.setattr(ingest.store, "ensure_collection", lambda recreate=False: None)
    monkeypatch.setattr(ingest.retriever, "reset_bm25", lambda: None)
    return rows


@pytest.fixture
async def db(monkeypatch):
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    sess = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(ingest, "AsyncSessionLocal", sess)
    yield sess
    await eng.dispose()


async def _add_doc(sess, tmp_path, *, doc_id, tenant_id, text, name="接口说明.md"):
    path = tmp_path / f"{doc_id}.md"
    path.write_text(text, encoding="utf-8")
    async with sess() as s:
        s.add(
            KnowledgeDocument(
                id=doc_id,
                title=name,
                category="用户上传",
                source_path=str(path),
                tenant_id=tenant_id,
                source="upload",
                status="pending",
                original_filename=name,
                size_bytes=len(text.encode()),
                updated_at=datetime(2026, 9, 4, 12, 0, 0),  # noqa: DTZ001 与列类型一致
            )
        )
        await s.commit()
    return path


# ---------- 上传文档摄取 ----------

async def test_上传切片带租户与来源(captured, db, tmp_path):
    await _add_doc(db, tmp_path, doc_id="doc_up_1", tenant_id="t1",
                   text="# 接口说明\n\n## 鉴权\n用 HMAC-SHA256 签名。")
    await ingest.ingest_upload("doc_up_1")
    assert captured, "应有切片写入"
    for r in captured:
        assert r["tenant_id"] == "t1"
        assert r["doc_id"] == "doc_up_1"
        assert r["source"] == "upload"


async def test_每行都带schema要求的全部字段(captured, db, tmp_path):
    """schema 是 enable_dynamic_field=False，缺字段 Milvus 直接拒绝。"""
    await _add_doc(db, tmp_path, doc_id="doc_up_1", tenant_id="t1",
                   text="# 标题\n\n## 章节\n内容。")
    await ingest.ingest_upload("doc_up_1")
    required = {"embedding", "content", "doc_title", "section", "category",
                "error_code", "version", "tenant_id", "doc_id", "source"}
    for r in captured:
        assert required <= set(r), f"缺字段: {required - set(r)}"


async def test_上传内容中的密钥被脱敏后才入库(captured, db, tmp_path):
    """用户文档里很常见他们自己的 API Key，不能被向量化并长期留存。"""
    await _add_doc(db, tmp_path, doc_id="doc_up_1", tenant_id="t1",
                   text="# 配置\n\n## 密钥\n我们的 key 是 ak_demo_abcdef1234567890abcdef12")
    await ingest.ingest_upload("doc_up_1")
    joined = "\n".join(r["content"] for r in captured)
    assert "ak_demo_abcdef1234567890abcdef12" not in joined


async def test_摄取成功后状态与切片数落库(captured, db, tmp_path):
    await _add_doc(db, tmp_path, doc_id="doc_up_1", tenant_id="t1",
                   text="# 标题\n\n## 章节\n内容。")
    await ingest.ingest_upload("doc_up_1")
    async with db() as s:
        doc = await s.get(KnowledgeDocument, "doc_up_1")
        assert doc.status == "published"
        assert doc.chunk_count == len(captured) > 0


async def test_摄取后重建关键词索引(monkeypatch, captured, db, tmp_path):
    """不重建的话用户上传完立刻搜不到，而上传是在线操作、不可能重启进程。"""
    calls = []
    monkeypatch.setattr(ingest.retriever, "reset_bm25", lambda: calls.append(1))
    await _add_doc(db, tmp_path, doc_id="doc_up_1", tenant_id="t1",
                   text="# 标题\n\n## 章节\n内容。")
    await ingest.ingest_upload("doc_up_1")
    assert calls == [1]


async def test_文档不存在时报错(captured, db):
    with pytest.raises(ValueError, match="不存在"):
        await ingest.ingest_upload("doc_missing")


async def test_内置文档不允许走上传摄取(captured, db, tmp_path):
    """避免误把全局文档按租户重新摄取。"""
    async with db() as s:
        s.add(KnowledgeDocument(id="doc_01", title="接入指南", category="接入",
                                source_path="data/knowledge/01-接入指南.md",
                                tenant_id="", source="builtin", status="published"))
        await s.commit()
    with pytest.raises(ValueError):
        await ingest.ingest_upload("doc_01")


async def test_文件缺失时报可读错误(captured, db, tmp_path):
    await _add_doc(db, tmp_path, doc_id="doc_up_1", tenant_id="t1", text="# x\n\n## y\nz")
    async with db() as s:
        doc = await s.get(KnowledgeDocument, "doc_up_1")
        doc.source_path = str(tmp_path / "gone.md")
        await s.commit()
    with pytest.raises(Exception) as e:
        await ingest.ingest_upload("doc_up_1")
    assert "不存在" in str(e.value) or "文件" in str(e.value)


# ---------- ingest_all 不能吃掉上传文档 ----------

async def test_ingest_all会重新摄取已发布的上传文档(monkeypatch, captured, db, tmp_path):
    """recreate=True 会 drop 整个 collection，把用户上传的切片一并清掉。
    若不重新摄取，一次 `make ingest` 就静默清空所有用户文档。"""
    monkeypatch.setattr(ingest, "KNOWLEDGE_DIR", tmp_path / "empty")
    (tmp_path / "empty").mkdir()
    await _add_doc(db, tmp_path, doc_id="doc_up_1", tenant_id="t1",
                   text="# 标题\n\n## 章节\n内容。")
    async with db() as s:
        doc = await s.get(KnowledgeDocument, "doc_up_1")
        doc.status = "published"
        await s.commit()

    captured.clear()
    await ingest.ingest_all(recreate=True)
    assert any(r["doc_id"] == "doc_up_1" for r in captured), "上传文档没被摄取回来"


async def test_ingest_all跳过未发布的上传文档(monkeypatch, captured, db, tmp_path):
    monkeypatch.setattr(ingest, "KNOWLEDGE_DIR", tmp_path / "empty")
    (tmp_path / "empty").mkdir()
    await _add_doc(db, tmp_path, doc_id="doc_up_1", tenant_id="t1",
                   text="# 标题\n\n## 章节\n内容。")  # status=pending
    captured.clear()
    await ingest.ingest_all(recreate=True)
    assert not any(r["doc_id"] == "doc_up_1" for r in captured)


# ---------- 内置摄取 ----------

async def test_内置切片租户为空串(monkeypatch, captured, db, tmp_path):
    kdir = tmp_path / "knowledge"
    kdir.mkdir()
    (kdir / "01-接入指南.md").write_text("# 接入指南\n\n## 注册\n先实名。", encoding="utf-8")
    monkeypatch.setattr(ingest, "KNOWLEDGE_DIR", kdir)
    await ingest.ingest_builtin(recreate=False)
    assert captured
    for r in captured:
        assert r["tenant_id"] == "", "内置文档必须是全局可见"
        assert r["source"] == "builtin"
        assert r["doc_id"] == "doc_01"
