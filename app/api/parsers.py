"""简历文件文本提取：PDF（pypdf）/ docx（python-docx）。

只提取、不持久化——文本返回前端后走既有 resume_analysis 管道（架构简化：
无附件表、无 schema 迁移）。解析失败统一 ValueError，由端点转 400。
"""

from __future__ import annotations

from io import BytesIO

from docx import Document
from pypdf import PdfReader

SUPPORTED_EXTENSIONS = (".pdf", ".docx")


def extract_text(filename: str, data: bytes) -> str:
    """按扩展名分发提取；不支持的类型 / 解析失败 / 提取为空均抛 ValueError。"""
    lower = filename.lower()
    if lower.endswith(".pdf"):
        extractor = extract_pdf
    elif lower.endswith(".docx"):
        extractor = extract_docx
    else:
        raise ValueError("仅支持 PDF / docx 文件")
    try:
        text = extractor(data).strip()
    except ValueError:
        raise
    except Exception as exc:  # 损坏文件：pypdf / zip 各自的解析异常
        raise ValueError(f"文件解析失败: {type(exc).__name__}") from exc
    if not text:
        raise ValueError("未能从文件中提取到文本（可能是扫描件或空文档）")
    return text


def extract_pdf(data: bytes) -> str:
    reader = PdfReader(BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def extract_docx(data: bytes) -> str:
    doc = Document(BytesIO(data))
    parts = [paragraph.text for paragraph in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    parts.append(cell.text)
    return "\n".join(parts)
