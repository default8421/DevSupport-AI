# @author: liuqinhe
"""知识库 ingest：切片 → 向量化 → 写入 Milvus，并登记文档元信息。

两个入口，职责分开：
- `ingest_builtin()` 摄取 data/knowledge/*.md，租户为空串（全局可见）
- `ingest_upload(doc_id)` 摄取单个用户上传文档，租户取自文档记录

`ingest_all()` 是两者的组合，供 `make ingest` 用。它必须重新摄取已发布的
上传文档——recreate=True 会 drop 整个 collection，不补回来的话一次
`make ingest` 就静默清空了所有用户上传的内容。
"""

import re
from pathlib import Path

from sqlalchemy import select

from app.db import AsyncSessionLocal
from app.guardrail import desensitize
from app.llm import client
from app.models import KnowledgeDocument
from app.rag import extract, retriever, store

# 知识库原始资料位于项目根目录 data/knowledge（与后端代码分离，便于运营维护与用户查看）
# ingest.py: parents[0]=rag, [1]=app, [2]=backend, [3]=项目根
KNOWLEDGE_DIR = Path(__file__).resolve().parents[3] / "data" / "knowledge"

CATEGORY_MAP = {
    "01": "接入",
    "02": "鉴权",
    "03": "错误码",
    "04": "回调",
    "05": "限流",
    "06": "计费",
    "07": "数据质量",
    "08": "FAQ",
}

ERROR_CODE_RE = re.compile(r"^([A-Z][A-Z_]+)（")
MAX_CHARS = 600
OVERLAP = 80


def _category(filename: str) -> str:
    prefix = filename.split("-")[0]
    return CATEGORY_MAP.get(prefix, "其它")


def _split_long(text: str) -> list[str]:
    """长段落按字符窗口切分，带 overlap。"""
    if len(text) <= MAX_CHARS:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        end = min(start + MAX_CHARS, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = end - OVERLAP
    return chunks


def chunk_markdown(md: str, doc_title: str) -> list[dict]:
    """按 ## 章节切片，长章节再按窗口切分。"""
    lines = md.splitlines()
    sections: list[tuple[str, list[str]]] = []
    current_title, buf = doc_title, []
    for line in lines:
        if line.startswith("## "):
            if buf:
                sections.append((current_title, buf))
            current_title, buf = line[3:].strip(), [line]
        elif line.startswith("# "):
            continue  # H1 作为文档标题，已单独处理
        else:
            buf.append(line)
    if buf:
        sections.append((current_title, buf))

    chunks = []
    for section_title, body_lines in sections:
        body = "\n".join(body_lines).strip()
        if not body:
            continue
        err_match = ERROR_CODE_RE.match(section_title)
        error_code = err_match.group(1) if err_match else ""
        for piece in _split_long(body):
            chunks.append(
                {"section": section_title, "content": piece.strip(), "error_code": error_code}
            )
    return chunks


async def _embed_all(contents: list[str]) -> list[list[float]]:
    """批量向量化（DashScope 单次上限保守取 10）。"""
    out: list[list[float]] = []
    for i in range(0, len(contents), 10):
        out.extend(await client.embed(contents[i : i + 10]))
    return out


def _rows(
    chunks: list[dict],
    embeddings: list[list[float]],
    *,
    doc_title: str,
    category: str,
    tenant_id: str,
    doc_id: str,
    source: str,
) -> list[dict]:
    """拼 Milvus 插入行。

    schema 是 enable_dynamic_field=False，因此每行必须带齐全部字段——
    包括 tenant_id/doc_id/source，缺一个 Milvus 就拒绝整批。
    """
    return [
        {
            "embedding": emb,
            "content": c["content"],
            "doc_title": doc_title,
            "section": c["section"],
            "category": category,
            "error_code": c["error_code"],
            "version": "v1",
            "tenant_id": tenant_id,
            "doc_id": doc_id,
            "source": source,
        }
        for c, emb in zip(chunks, embeddings)
    ]


async def ingest_upload(doc_id: str) -> dict:
    """摄取单个用户上传文档。跑在 FastAPI 后台任务里，故用异步会话。

    解析失败会抛 ExtractError，由调用方写入 error_message。
    """
    async with AsyncSessionLocal() as s:
        doc = await s.get(KnowledgeDocument, doc_id)
        if doc is None:
            raise ValueError(f"文档不存在: {doc_id}")
        if doc.source != "upload":
            # 防误把全局内置文档按租户重新摄取，那会让它对其他租户不可见
            raise ValueError(f"文档 {doc_id} 不是上传文档，不能走上传摄取")
        tenant_id, path_str, title = doc.tenant_id, doc.source_path, doc.title

    path = Path(path_str)
    if not path.exists():
        raise extract.ExtractError("上传文件已不存在，请重新上传。")

    text = extract.to_text(path.name, path.read_bytes())
    # 用户文档里很常见他们自己的 API Key。必须在切分与向量化之前脱敏，
    # 否则密钥会被长期留存在向量库里，并在引用片段里回显。
    text = desensitize.desensitize_text(text)

    chunks = chunk_markdown(text, title)
    if not chunks:
        raise extract.ExtractError("文档没有可索引的内容。")

    embeddings = await _embed_all([c["content"] for c in chunks])
    rows = _rows(
        chunks, embeddings,
        doc_title=title, category="用户上传",
        tenant_id=tenant_id, doc_id=doc_id, source="upload",
    )
    store.insert(rows)

    async with AsyncSessionLocal() as s:
        doc = await s.get(KnowledgeDocument, doc_id)
        doc.chunk_count = len(rows)
        doc.status = "published"
        doc.error_message = None
        await s.commit()

    # 上传是在线操作，用户上传完必须马上能搜到，不可能等进程重启
    retriever.reset_bm25()
    return {"doc_id": doc_id, "chunks": len(rows)}


async def ingest_all(recreate: bool = True) -> dict:
    """内置知识库 + 重新摄取所有已发布的上传文档。供 `make ingest` 使用。"""
    stats = await ingest_builtin(recreate=recreate)

    async with AsyncSessionLocal() as s:
        ids = (
            await s.execute(
                select(KnowledgeDocument.id).where(
                    KnowledgeDocument.source == "upload",
                    KnowledgeDocument.status == "published",
                )
            )
        ).scalars().all()

    restored = 0
    for doc_id in ids:
        try:
            await ingest_upload(doc_id)
            restored += 1
        except Exception as e:  # noqa: BLE001  单个文档失败不该中断整体重建
            print(f"[ingest] 上传文档 {doc_id} 重新摄取失败：{e}")
    if ids:
        print(f"[ingest] 已重新摄取上传文档 {restored}/{len(ids)} 篇")
    return {**stats, "uploads_restored": restored}


async def ingest_builtin(recreate: bool = True) -> dict:
    """摄取 data/knowledge/*.md。切片租户为空串，对所有租户可见。"""
    store.ensure_collection(recreate=recreate)
    files = sorted(KNOWLEDGE_DIR.glob("*.md"))
    total_chunks = 0
    doc_records = []

    for f in files:
        md = f.read_text(encoding="utf-8")
        first_line = md.splitlines()[0] if md else f.stem
        doc_title = first_line[2:].strip() if first_line.startswith("# ") else f.stem
        category = _category(f.name)
        chunks = chunk_markdown(md, doc_title)
        doc_id = f"doc_{f.stem.split('-')[0]}"

        embeddings = await _embed_all([c["content"] for c in chunks])
        rows = _rows(
            chunks, embeddings,
            doc_title=doc_title, category=category,
            tenant_id="", doc_id=doc_id, source="builtin",
        )
        store.insert(rows)
        total_chunks += len(rows)
        doc_records.append(
            {
                "id": doc_id,
                "title": doc_title,
                "category": category,
                "source_path": str(f.relative_to(KNOWLEDGE_DIR.parent.parent)),
                "chunk_count": len(rows),
            }
        )
        print(f"[ingest] {f.name} -> {len(rows)} chunks (category={category})")

    # 登记文档元信息到 MySQL。用异步会话与 ingest_upload 保持一致：
    # 两者都在 async 函数里，混用同步会话会在事件循环里做阻塞 IO。
    async with AsyncSessionLocal() as s:
        for rec in doc_records:
            existing = await s.get(KnowledgeDocument, rec["id"])
            if existing:
                existing.chunk_count = rec["chunk_count"]
                existing.title = rec["title"]
                existing.category = rec["category"]
                existing.source_path = rec["source_path"]
                existing.tenant_id = ""
                existing.source = "builtin"
            else:
                s.add(KnowledgeDocument(
                    status="published", version="v1",
                    tenant_id="", source="builtin", **rec,
                ))
        await s.commit()

    retriever.reset_bm25()
    return {"documents": len(files), "chunks": total_chunks}
