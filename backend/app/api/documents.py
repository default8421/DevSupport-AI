# @author: liuqinhe
"""用户自定义文档上传接口。

这是本项目唯一让外部内容进入检索链路的入口，因此校验闸门必须在
落盘与建记录**之前**全部通过：先落盘再校验就等于给了写任意文件的机会。

上传文件落 data/uploads/{tenant_id}/{doc_id}{ext}，绝不落 data/knowledge/
——后者的 *.md 会被 ingest_builtin 当作全局文档摄取，当场废掉租户隔离。
"""

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import AsyncSessionLocal, get_db
from app.deps import CurrentUser, assert_tenant_access, get_current_user
from app.models import KnowledgeDocument
from app.rag import extract, retriever, store
from app.rag.ingest import ingest_upload

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])

# documents.py: parents[0]=api, [1]=app, [2]=backend, [3]=项目根
# 与 data/knowledge 并列但互不包含，避免被内置摄取扫到
UPLOAD_DIR = Path(__file__).resolve().parents[3] / "data" / "uploads"

ERROR_MSG_MAX = 255  # 与 knowledge_document.error_message 列宽一致


def _view(d: KnowledgeDocument) -> dict:
    return {
        "id": d.id,
        "title": d.title,
        "category": d.category,
        "status": d.status,
        "source": d.source,
        "chunk_count": d.chunk_count,
        "size_bytes": d.size_bytes,
        "original_filename": d.original_filename,
        "error_message": d.error_message,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
        # 内置文档由运营维护，客户不可删
        "deletable": d.source == "upload",
    }


async def _check_quota(db: AsyncSession, tenant_id: str) -> None:
    """租户配额。防单租户把向量库和向量化成本吃满。"""
    docs, chunks = (
        await db.execute(
            select(
                func.count(KnowledgeDocument.id),
                func.coalesce(func.sum(KnowledgeDocument.chunk_count), 0),
            ).where(
                KnowledgeDocument.tenant_id == tenant_id,
                KnowledgeDocument.source == "upload",
            )
        )
    ).one()

    if docs >= settings.upload_max_docs_per_tenant:
        raise HTTPException(
            400, f"已达文档数量上限 {settings.upload_max_docs_per_tenant} 篇，请先删除不再需要的文档。"
        )
    if chunks >= settings.upload_max_chunks_per_tenant:
        raise HTTPException(
            400, f"已达知识库容量上限 {settings.upload_max_chunks_per_tenant} 个切片，请先删除部分文档。"
        )


async def _process(doc_id: str) -> None:
    """后台处理上传文档。

    用独立会话：请求的 db 会话在响应返回后已关闭，后台任务不能复用。

    任何异常都必须在这里落地成 status=failed + error_message。
    后台任务往外抛异常只会进日志，用户界面上会永远停在 processing，
    看不到任何原因。
    """
    try:
        async with AsyncSessionLocal() as s:
            doc = await s.get(KnowledgeDocument, doc_id)
            if doc is None:
                return
            doc.status = "processing"
            await s.commit()

        await ingest_upload(doc_id)
    except Exception as e:  # noqa: BLE001  刻意兜住一切，见上方 docstring
        reason = (
            str(e)
            if isinstance(e, extract.ExtractError)
            else f"文档处理失败：{type(e).__name__}"
        )
        log.warning("上传文档 %s 处理失败: %s", doc_id, e)
        async with AsyncSessionLocal() as s:
            doc = await s.get(KnowledgeDocument, doc_id)
            if doc is not None:
                doc.status = "failed"
                # 列宽 255，超长会写库失败、状态卡死在 processing
                doc.error_message = reason[:ERROR_MSG_MAX]
                await s.commit()


@router.post("")
async def upload_document(
    background: BackgroundTasks,
    file: UploadFile,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    data = await file.read()

    # 校验闸门：全部通过之后才落盘、才建记录
    try:
        extract.check_size(data)
        ext = extract.sniff(file.filename or "", data[:8])
    except extract.ExtractError as e:
        raise HTTPException(400, str(e)) from e
    await _check_quota(db, user.tenant_id)

    doc_id = f"doc_up_{uuid.uuid4().hex[:16]}"
    # 路径只由 doc_id 与扩展名拼成。绝不使用用户提供的文件名——
    # 那是路径穿越（../../etc/passwd）的入口。原名只作展示存库。
    target = UPLOAD_DIR / user.tenant_id / f"{doc_id}{ext}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)

    title = (file.filename or doc_id)[:128]
    db.add(
        KnowledgeDocument(
            id=doc_id,
            title=title,
            category="用户上传",
            source_path=str(target),
            chunk_count=0,
            status="pending",
            tenant_id=user.tenant_id,
            source="upload",
            uploaded_by=user.user_id,
            original_filename=title,
            size_bytes=len(data),
        )
    )
    await db.commit()

    background.add_task(_process, doc_id)
    return {"doc_id": doc_id, "status": "pending", "filename": title}


@router.get("")
async def list_documents(
    user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    """本租户上传的文档 + 全局内置文档。"""
    rows = (
        await db.execute(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.tenant_id.in_(["", user.tenant_id]))
            .order_by(KnowledgeDocument.source, KnowledgeDocument.id)
        )
    ).scalars().all()
    return {"documents": [_view(d) for d in rows]}


async def _load(db: AsyncSession, doc_id: str, user: CurrentUser) -> KnowledgeDocument:
    doc = await db.get(KnowledgeDocument, doc_id)
    if doc is None:
        raise HTTPException(404, "文档不存在")
    if doc.tenant_id:  # 空串是全局文档，人人可读
        assert_tenant_access(user, doc.tenant_id)
    return doc


@router.get("/{doc_id}")
async def document_detail(
    doc_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return _view(await _load(db, doc_id, user))


@router.delete("/{doc_id}")
async def delete_document(
    doc_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    doc = await _load(db, doc_id, user)
    if doc.source != "upload":
        raise HTTPException(403, "内置文档由平台维护，不可删除")

    # 先删切片：切片残留会让检索命中已删内容，比元信息残留严重得多
    store.delete_by_doc(doc_id)
    path = Path(doc.source_path)
    await db.delete(doc)
    await db.commit()
    path.unlink(missing_ok=True)
    retriever.reset_bm25()
    return {"doc_id": doc_id, "deleted": True}
