# @author: liuqinhe
"""文档上传接口：校验闸门、配额、租户隔离、后台处理状态机。

上传是本项目唯一让外部内容进入检索链路的入口，校验闸门必须在
落盘与建记录**之前**全部通过——先落盘再校验等于给了写任意文件的机会。
"""

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Base, get_db
from app.deps import CurrentUser, get_current_user
from app.main import app
from app.models import KnowledgeDocument
from app.rag import ingest


def _user(tenant="t1", uid="u1", role="customer_dev"):
    return CurrentUser(
        user_id=uid, username=uid, display_name=uid, role=role, tenant_id=tenant
    )


@pytest.fixture
def env(monkeypatch, tmp_path):
    """文件型 SQLite + 打桩向量化/Milvus + 上传目录指向 tmp。

    用文件库而不是 :memory:：内存库每个连接都是一个独立的库，而
    TestClient 在自己的事件循环里跑应用，与固件不共享连接，
    建的表在应用那边看不见。文件库天然共享，也不必操心跨循环复用连接。
    """
    from app.api import documents

    db_file = tmp_path / "test.db"
    # 建表走同步引擎：不涉及事件循环，最省事也最不容易出错
    sync_eng = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(sync_eng)

    eng = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    sess = async_sessionmaker(eng, expire_on_commit=False)

    async def _override_db():
        async with sess() as s:
            yield s

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: _user()

    # 后台任务与 ingest 都用独立会话（请求会话在响应后已关闭）
    monkeypatch.setattr(documents, "AsyncSessionLocal", sess)
    monkeypatch.setattr(ingest, "AsyncSessionLocal", sess)

    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(documents, "UPLOAD_DIR", upload_dir)

    inserted: list[dict] = []

    async def _embed(texts):
        return [[0.1, 0.2, 0.3] for _ in texts]

    monkeypatch.setattr(ingest.client, "embed", _embed)
    monkeypatch.setattr(ingest.store, "insert", lambda r: inserted.extend(r))
    monkeypatch.setattr(ingest.retriever, "reset_bm25", lambda: None)
    monkeypatch.setattr(documents.store, "delete_by_doc", lambda d: 1)
    monkeypatch.setattr(documents.retriever, "reset_bm25", lambda: None)

    class Env:
        client = TestClient(app)
        rows = inserted
        dir = upload_dir
        mod = documents

        @staticmethod
        def seed(**kw):
            """用同步会话塞一条文档记录，避开事件循环。"""
            with Session(sync_eng) as s:
                s.add(KnowledgeDocument(**kw))
                s.commit()

    yield Env()
    app.dependency_overrides.clear()


def _upload(env, content=b"# \xe6\xa0\x87\xe9\xa2\x98\n\n## \xe7\xab\xa0\xe8\x8a\x82\n\xe5\x86\x85\xe5\xae\xb9\xe3\x80\x82",
            name="my.md", ctype="text/markdown"):
    return env.client.post(
        "/api/documents", files={"file": (name, io.BytesIO(content), ctype)}
    )


_BUILTIN = {
    "id": "doc_01", "title": "接入指南", "category": "接入",
    "source_path": "data/knowledge/01-接入指南.md",
    "tenant_id": "", "source": "builtin", "status": "published",
}


# ---------- 认证 ----------

def test_未登录被拒(env):
    app.dependency_overrides.pop(get_current_user)
    r = _upload(env)
    assert r.status_code == 401


# ---------- 校验闸门 ----------

def test_不支持的类型被拒(env):
    r = _upload(env, content=b"MZ\x90\x00", name="evil.exe")
    assert r.status_code == 400
    assert ".exe" in r.json()["detail"] or "支持" in r.json()["detail"]


def test_伪造扩展名被拒(env):
    r = _upload(env, content=b"MZ\x90\x00\x03", name="payload.pdf")
    assert r.status_code == 400
    assert "PDF" in r.json()["detail"]


def test_超大文件被拒(env, monkeypatch):
    monkeypatch.setattr(settings, "upload_max_bytes", 16)
    r = _upload(env, content=b"x" * 100, name="big.txt")
    assert r.status_code == 400


def test_校验失败不落盘不建记录(env, monkeypatch):
    """先落盘再校验等于给了写任意文件的机会。"""
    _upload(env, content=b"MZ\x90\x00", name="evil.exe")
    assert not env.dir.exists() or not list(env.dir.rglob("*"))
    r = env.client.get("/api/documents")
    assert all(d["source"] != "upload" for d in r.json()["documents"])


# ---------- 存储路径安全 ----------

def test_存储路径只用doc_id不用原文件名(env):
    r = _upload(env, name="report.md")
    doc_id = r.json()["doc_id"]
    files = list(env.dir.rglob("*.md"))
    assert len(files) == 1
    assert files[0].name == f"{doc_id}.md"
    assert "report" not in files[0].name


def test_路径穿越文件名不逃出上传目录(env):
    r = _upload(env, name="../../../../etc/passwd.md")
    assert r.status_code == 200
    files = [p for p in env.dir.rglob("*") if p.is_file()]
    assert len(files) == 1
    assert env.dir.resolve() in files[0].resolve().parents


def test_原文件名仅作展示保留(env):
    r = _upload(env, name="我的接口说明.md")
    doc_id = r.json()["doc_id"]
    r2 = env.client.get(f"/api/documents/{doc_id}")
    assert r2.json()["original_filename"] == "我的接口说明.md"


def test_上传目录不在知识库目录内(env):
    """落进 data/knowledge 会被 ingest_builtin 当作全局文档摄取，
    当场废掉租户隔离。"""
    from app.rag.ingest import KNOWLEDGE_DIR

    assert KNOWLEDGE_DIR.resolve() not in env.mod.UPLOAD_DIR.resolve().parents
    assert env.mod.UPLOAD_DIR.name == "uploads"


# ---------- 配额 ----------

def test_超过文档数配额被拒(env, monkeypatch):
    monkeypatch.setattr(settings, "upload_max_docs_per_tenant", 1)
    assert _upload(env).status_code == 200
    r = _upload(env)
    assert r.status_code == 400
    assert "上限" in r.json()["detail"] or "配额" in r.json()["detail"]


def test_超过切片配额被拒(env, monkeypatch):
    monkeypatch.setattr(settings, "upload_max_chunks_per_tenant", 1)
    _upload(env)  # 第一篇产生若干切片
    r = _upload(env)
    assert r.status_code == 400


def test_配额按租户各自计算(env, monkeypatch):
    monkeypatch.setattr(settings, "upload_max_docs_per_tenant", 1)
    assert _upload(env).status_code == 200
    app.dependency_overrides[get_current_user] = lambda: _user(tenant="t2", uid="u2")
    assert _upload(env).status_code == 200, "别的租户不该被 t1 的用量挤占"


# ---------- 后台处理状态机 ----------

def test_上传成功后状态为published(env):
    """TestClient 会在响应后同步执行后台任务。"""
    doc_id = _upload(env).json()["doc_id"]
    r = env.client.get(f"/api/documents/{doc_id}")
    assert r.json()["status"] == "published"
    assert r.json()["chunk_count"] > 0


def test_返回体带初始状态(env):
    body = _upload(env).json()
    assert body["doc_id"].startswith("doc_up_")
    assert body["status"] in ("pending", "processing", "published")


def test_处理失败时状态与原因写入(env, monkeypatch):
    """后台任务抛异常只会进日志，用户能看到的必须是记录里的失败原因。"""
    async def _boom(doc_id):
        from app.rag.extract import ExtractError

        raise ExtractError("该 PDF 没有可提取的文本，可能是扫描件（图片版），暂不支持。")

    monkeypatch.setattr(env.mod, "ingest_upload", _boom)
    doc_id = _upload(env).json()["doc_id"]
    body = env.client.get(f"/api/documents/{doc_id}").json()
    assert body["status"] == "failed"
    assert "扫描件" in body["error_message"]


def test_未预料的异常也不外抛(env, monkeypatch):
    async def _boom(doc_id):
        raise RuntimeError("milvus 连接超时")

    monkeypatch.setattr(env.mod, "ingest_upload", _boom)
    doc_id = _upload(env).json()["doc_id"]
    body = env.client.get(f"/api/documents/{doc_id}").json()
    assert body["status"] == "failed"
    assert body["error_message"]


def test_失败原因长度被截断(env, monkeypatch):
    """error_message 列是 VARCHAR(255)，超长会导致写库失败、状态卡在 processing。"""
    async def _boom(doc_id):
        raise RuntimeError("x" * 5000)

    monkeypatch.setattr(env.mod, "ingest_upload", _boom)
    doc_id = _upload(env).json()["doc_id"]
    body = env.client.get(f"/api/documents/{doc_id}").json()
    assert len(body["error_message"]) <= 255


# ---------- 列表与租户隔离 ----------

def test_列表只含本租户上传与全局内置(env):
    doc_id = _upload(env).json()["doc_id"]
    app.dependency_overrides[get_current_user] = lambda: _user(tenant="t2", uid="u2")
    other = _upload(env).json()["doc_id"]

    app.dependency_overrides[get_current_user] = lambda: _user()
    ids = [d["id"] for d in env.client.get("/api/documents").json()["documents"]]
    assert doc_id in ids
    assert other not in ids


def test_内置文档在列表里不可删(env):
    env.seed(**_BUILTIN)
    docs = {d["id"]: d for d in env.client.get("/api/documents").json()["documents"]}
    assert docs["doc_01"]["deletable"] is False


def test_跨租户读取详情被拒(env):
    doc_id = _upload(env).json()["doc_id"]
    app.dependency_overrides[get_current_user] = lambda: _user(tenant="t2", uid="u2")
    assert env.client.get(f"/api/documents/{doc_id}").status_code == 403


def test_不存在的文档返回404(env):
    assert env.client.get("/api/documents/doc_up_nope").status_code == 404


# ---------- 删除 ----------

def test_删除会清切片与文件(env):
    doc_id = _upload(env).json()["doc_id"]
    assert list(env.dir.rglob("*.md"))
    r = env.client.delete(f"/api/documents/{doc_id}")
    assert r.status_code == 200
    assert env.client.get(f"/api/documents/{doc_id}").status_code == 404
    assert not list(env.dir.rglob("*.md")), "磁盘文件未清理"


def test_跨租户删除被拒(env):
    doc_id = _upload(env).json()["doc_id"]
    app.dependency_overrides[get_current_user] = lambda: _user(tenant="t2", uid="u2")
    assert env.client.delete(f"/api/documents/{doc_id}").status_code == 403
    app.dependency_overrides[get_current_user] = lambda: _user()
    assert env.client.get(f"/api/documents/{doc_id}").status_code == 200


def test_不允许删除内置文档(env):
    env.seed(**_BUILTIN)
    r = env.client.delete("/api/documents/doc_01")
    assert r.status_code == 403
    assert "内置" in r.json()["detail"]


def test_删除后重建关键词索引(env, monkeypatch):
    calls = []
    monkeypatch.setattr(env.mod.retriever, "reset_bm25", lambda: calls.append(1))
    doc_id = _upload(env).json()["doc_id"]
    env.client.delete(f"/api/documents/{doc_id}")
    assert calls, "不重建的话已删文档仍会被关键词检索命中"
