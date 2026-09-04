# @author: liuqinhe
"""上传文件解析：字节流 → 纯文本。

独立成模块而不是塞进 ingest.py：职责不同（字节流→文本 vs 文本→向量库），
且这里是纯函数，最容易单测。

所有失败都抛 ExtractError，其 message 会原样写进
knowledge_document.error_message 并展示给用户，因此必须是可读的中文，
不能是 pypdf 的英文异常栈。
"""

import io
import logging

from app.config import settings

log = logging.getLogger(__name__)

ALLOWED_EXT = frozenset({".md", ".txt", ".pdf"})

# 内容嗅探用的魔数。文本类没有魔数，靠能否解码判定。
_PDF_MAGIC = b"%PDF-"

# 中文用户导出的 txt 常是 GBK 系编码，utf-8 解不开。
# gb18030 是 GBK 的超集，覆盖面比 gbk 更广。
_ENCODINGS = ("utf-8", "gb18030")


class ExtractError(Exception):
    """解析失败。message 直接面向用户，必须可读。"""


def check_size(data: bytes) -> None:
    """大小校验。放在解析之前，避免为超大文件白做解码。"""
    limit = settings.upload_max_bytes
    if len(data) > limit:
        raise ExtractError(f"文件超过大小上限 {limit // 1024 // 1024} MB，请拆分后再上传。")


def sniff(filename: str, head: bytes) -> str:
    """校验扩展名并做内容嗅探，返回归一化的小写扩展名。

    两层校验都需要：扩展名白名单挡住明显不支持的类型，内容嗅探挡住
    "把别的东西改名成 .pdf" ——只看扩展名的话这种文件会被送进解析器。
    """
    name = (filename or "").strip()
    dot = name.rfind(".")
    if dot < 0:
        raise ExtractError("文件没有扩展名，仅支持 .md、.txt、.pdf。")
    ext = name[dot:].lower()
    if ext not in ALLOWED_EXT:
        raise ExtractError(f"不支持的文件类型 {ext}，仅支持 .md、.txt、.pdf。")
    if ext == ".pdf" and not head.startswith(_PDF_MAGIC):
        raise ExtractError("文件内容不是有效的 PDF（缺少 PDF 文件头），请确认文件未损坏。")
    return ext


def _decode(data: bytes) -> str:
    for enc in _ENCODINGS:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ExtractError("文件编码无法识别，请另存为 UTF-8 编码后重新上传。")


def _pdf_to_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = reader.pages[: settings.upload_max_pages]
        parts = [(p.extract_text() or "") for p in pages]
    except ExtractError:
        raise
    except Exception as e:  # pypdf 的异常类型多且不稳定，统一转成可读的 ExtractError
        log.warning("PDF 解析失败: %s", e)
        raise ExtractError("PDF 解析失败，文件可能已损坏或被加密。") from e

    text = "\n".join(parts).strip()
    if not text:
        # 最常见的真实失败：扫描件只有图像、没有文本层。
        # 必须点明原因，否则用户会反复重试同一个文件。
        raise ExtractError("该 PDF 没有可提取的文本，可能是扫描件（图片版），暂不支持。")
    return text


def to_text(filename: str, data: bytes) -> str:
    """把上传文件解析为纯文本。超出字符上限则截断。"""
    ext = sniff(filename, data[: len(_PDF_MAGIC)])
    text = _pdf_to_text(data) if ext == ".pdf" else _decode(data).strip()
    if not text.strip():
        raise ExtractError("文件内容为空。")

    limit = settings.upload_max_chars
    if len(text) > limit:
        log.info("上传文档超过 %d 字符，已截断", limit)
        text = text[:limit]
    return text
