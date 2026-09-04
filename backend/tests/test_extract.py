# @author: liuqinhe
"""上传文件解析：字节流 → 纯文本。纯函数，最容易也最该测透。

失败原因必须可读并直接展示给用户——"处理失败"这种文案等于没说。
"""

import io

import pytest

from app.config import settings
from app.rag import extract
from app.rag.extract import ExtractError


def minimal_pdf(*page_texts: str) -> bytes:
    """手工拼一个带文本层的最小 PDF（含正确 xref 偏移）。

    pypdf 只能造空白页、写不进文本，而"空白页"等价于扫描件——
    只用它测出来的永远是失败路径。要验证 PDF 正常提取必须自己拼。
    """
    n_pages = len(page_texts)
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(n_pages))
    font_no = 3 + n_pages * 2
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode(),
    ]
    for i, text in enumerate(page_texts):
        content = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
            f"/Contents {4 + i * 2} 0 R "
            f"/Resources << /Font << /F1 {font_no} 0 R >> >> >>".encode()
        )
        objs.append(
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content)
        )
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1,
        xref_at,
    )
    return bytes(out)


# ---------- 扩展名与内容嗅探 ----------

def test_支持的扩展名():
    assert extract.ALLOWED_EXT == frozenset({".md", ".txt", ".pdf"})


def test_扩展名大小写不敏感():
    assert extract.sniff("A.MD", b"# x") == ".md"
    assert extract.sniff("a.TXT", b"x") == ".txt"


def test_不支持的扩展名被拒():
    with pytest.raises(ExtractError) as e:
        extract.sniff("evil.exe", b"MZ\x90\x00")
    assert ".exe" in str(e.value) or "支持" in str(e.value)


def test_没有扩展名被拒():
    with pytest.raises(ExtractError):
        extract.sniff("noext", b"x")


def test_伪造扩展名被拒():
    """把可执行文件改名成 .pdf 必须挡住——这是扩展名白名单挡不住的那一层。"""
    with pytest.raises(ExtractError) as e:
        extract.sniff("payload.pdf", b"MZ\x90\x00\x03\x00\x00\x00")
    assert "PDF" in str(e.value)


def test_真pdf魔数通过嗅探():
    assert extract.sniff("a.pdf", b"%PDF-1.7\n%...") == ".pdf"


def test_文本类不做魔数校验():
    """md/txt 没有魔数，靠能否解码来判定（在 to_text 里）。"""
    assert extract.sniff("a.md", b"any bytes") == ".md"


# ---------- 文本解析 ----------

def test_markdown原样返回():
    out = extract.to_text("a.md", "# 标题\n\n正文内容".encode())
    assert "# 标题" in out and "正文内容" in out


def test_纯文本原样返回():
    assert extract.to_text("a.txt", "普通文本".encode()) == "普通文本"


def test_GB18030中文能解码():
    """中文用户导出的 txt 常是 GBK 系编码，直接按 utf-8 解会乱码或抛错。"""
    data = "接口调用说明".encode("gb18030")
    assert extract.to_text("a.txt", data) == "接口调用说明"


def test_无法解码的二进制被拒():
    with pytest.raises(ExtractError) as e:
        extract.to_text("a.txt", bytes(range(256)) * 4)
    assert "编码" in str(e.value)


def test_空文件被拒():
    with pytest.raises(ExtractError) as e:
        extract.to_text("a.md", b"   \n\n  ")
    assert "空" in str(e.value)


def test_超过字符上限被截断(monkeypatch):
    monkeypatch.setattr(settings, "upload_max_chars", 100)
    out = extract.to_text("a.txt", ("x" * 500).encode())
    assert len(out) == 100


# ---------- PDF ----------

def test_pdf提取文本():
    """PDF 的正常路径。只测失败路径的话，提取逻辑本身等于没验证。"""
    out = extract.to_text("a.pdf", minimal_pdf("Hello API SIGN_INVALID"))
    assert "SIGN_INVALID" in out


def test_多页pdf按顺序拼接():
    out = extract.to_text("a.pdf", minimal_pdf("first page", "second page"))
    assert out.index("first") < out.index("second")


def test_扫描版pdf报可读错误():
    """空白页没有文本层，等价于扫描件——最常见的真实失败，必须点明原因，
    否则用户会拿同一个文件反复重试。"""
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    with pytest.raises(ExtractError) as e:
        extract.to_text("a.pdf", buf.getvalue())
    assert "扫描" in str(e.value)


def test_坏pdf报可读错误():
    with pytest.raises(ExtractError) as e:
        extract.to_text("a.pdf", b"%PDF-1.7\n\x00\x01 truncated garbage")
    assert "PDF" in str(e.value)
    # 不能把 pypdf 的英文栈信息直接抛给用户
    assert "Traceback" not in str(e.value)


def test_pdf页数上限(monkeypatch):
    """防一份两千页的 PDF 把向量化成本炸掉。"""
    monkeypatch.setattr(settings, "upload_max_pages", 2)
    out = extract.to_text("a.pdf", minimal_pdf("page one", "page two", "page three"))
    assert "page one" in out and "page two" in out
    assert "page three" not in out


# ---------- 大小校验 ----------

def test_超过大小上限被拒(monkeypatch):
    monkeypatch.setattr(settings, "upload_max_bytes", 10)
    with pytest.raises(ExtractError) as e:
        extract.check_size(b"x" * 11)
    assert "大小" in str(e.value) or "MB" in str(e.value)


def test_未超过大小上限通过(monkeypatch):
    monkeypatch.setattr(settings, "upload_max_bytes", 10)
    extract.check_size(b"x" * 10)


def test_错误消息是中文且可直接展示():
    """error_message 会原样进数据库、原样给用户看。"""
    with pytest.raises(ExtractError) as e:
        extract.sniff("a.exe", b"MZ")
    msg = str(e.value)
    assert msg and not msg.startswith("Traceback")
    assert any("\u4e00" <= ch <= "\u9fff" for ch in msg), "失败原因应为中文"
