"""L1: 简历文件提取器 — PDF（pypdf）/ docx（python-docx）。

依据 specs/02-in-progress/api/resume-upload-jd.md 的 AC1。
"""

from __future__ import annotations

from io import BytesIO

import pytest
from docx import Document

from app.api.parsers import extract_docx, extract_pdf, extract_text


def _make_docx(*paragraphs: str) -> bytes:
    doc = Document()
    for paragraph in paragraphs:
        doc.add_paragraph(paragraph)
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "技能：Java"
    table.rows[0].cells[1].text = "MySQL"
    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _bare_docx(*paragraphs: str) -> bytes:
    doc = Document()
    for paragraph in paragraphs:
        doc.add_paragraph(paragraph)
    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _minimal_pdf(text: str) -> bytes:
    """手写最小 PDF（Helvetica 文本对象），xref 偏移在构造时计算，pypdf 可提取。"""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    body = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(body)
    body += f"xref\n0 {len(objects) + 1}\n".encode()
    body += b"0000000000 65535 f \n"
    for offset in offsets:
        body += f"{offset:010d} 00000 n \n".encode()
    body += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    return bytes(body)


def test_extract_docx_chinese_paragraphs_and_tables():
    data = _make_docx("张三", "3 年后端开发")
    text = extract_docx(data)
    assert "张三" in text
    assert "3 年后端开发" in text
    assert "技能：Java" in text  # 表格单元格文本
    assert "MySQL" in text


def test_extract_pdf_ascii_roundtrip():
    text = extract_pdf(_minimal_pdf("Hello OfferCoach Resume"))
    assert "Hello OfferCoach Resume" in text


def test_extract_text_rejects_unknown_extension():
    with pytest.raises(ValueError, match="仅支持"):
        extract_text("resume.txt", b"whatever")


def test_extract_text_rejects_corrupt_docx():
    with pytest.raises(ValueError, match="解析失败"):
        extract_text("resume.docx", b"not a zip file")


def test_extract_text_rejects_corrupt_pdf():
    with pytest.raises(ValueError, match="解析失败"):
        extract_text("resume.pdf", b"garbage bytes, no %PDF header")


def test_extract_text_rejects_empty_result():
    with pytest.raises(ValueError, match="提取到文本"):
        extract_text("resume.docx", _bare_docx("   "))
