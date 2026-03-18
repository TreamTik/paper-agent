"""
core/pdf_parser.py
使用 PyMuPDF 提取 PDF 纯文本，并支持分块以应对超长文档。
"""

import fitz  # PyMuPDF
from pathlib import Path


# 默认单块最大字符数（约 3000 tokens），可按需调整
DEFAULT_CHUNK_SIZE = 12000


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """
    从 PDF 二进制内容中提取全文纯文本。
    返回按页拼接的字符串，页间以换行符分隔。
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages_text = []
    for page in doc:
        text = page.get_text("text")
        if text.strip():
            pages_text.append(text)
    doc.close()
    return "\n".join(pages_text)


def chunk_text(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE) -> list[str]:
    """
    将长文本按 chunk_size 字符数切分为若干块。
    切分点尽量落在段落边界（双换行符）以保留语义完整性。
    """
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:])
            break
        # 尝试在段落边界处截断
        boundary = text.rfind("\n\n", start, end)
        if boundary == -1 or boundary <= start:
            boundary = text.rfind("\n", start, end)
        if boundary == -1 or boundary <= start:
            boundary = end
        chunks.append(text[start:boundary])
        start = boundary
    return chunks


def get_pdf_info(pdf_bytes: bytes) -> dict:
    """
    返回 PDF 基本元信息：页数、标题、文件大小（bytes）。
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    meta = doc.metadata or {}
    info = {
        "page_count": doc.page_count,
        "title": meta.get("title", "").strip() or "（未检测到标题）",
        "author": meta.get("author", "").strip() or "（未知作者）",
        "size_bytes": len(pdf_bytes),
    }
    doc.close()
    return info
