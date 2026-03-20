"""
core/pdf_parser.py
使用 PyMuPDF 提取 PDF 纯文本，并支持分块以应对超长文档。
新增：图片提取功能（用于 VLM 图表分析）
"""

import fitz  # PyMuPDF
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional
import base64
import io


# 默认单块最大字符数（约 3000 tokens），可按需调整
DEFAULT_CHUNK_SIZE = 12000


@dataclass
class PDFFigure:
    """PDF 中的图片/图表"""
    id: str
    page_num: int
    image_bytes: bytes
    ext: str  # 图片格式：png, jpg, etc.
    caption: str = ""  # 附近文本
    width: int = 0
    height: int = 0


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


def extract_figures_from_pdf(pdf_bytes: bytes, min_size: int = 100, include_vector: bool = True) -> List[PDFFigure]:
    """
    从 PDF 中提取所有图片（包括位图和矢量图）

    Args:
        pdf_bytes: PDF 文件二进制内容
        min_size: 最小图片尺寸（像素），过滤小图标
        include_vector: 是否提取矢量图（流程图、架构图等）

    Returns:
        PDFFigure 列表
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    figures = []

    for page_num, page in enumerate(doc, 1):
        # ===== 1. 提取位图图片 =====
        image_list = page.get_images(full=True)

        for img_index, img in enumerate(image_list):
            xref = img[0]
            base_image = doc.extract_image(xref)

            if not base_image:
                continue

            image_bytes = base_image["image"]
            ext = base_image["ext"]

            # 获取图片尺寸
            try:
                from PIL import Image
                pil_img = Image.open(io.BytesIO(image_bytes))
                width, height = pil_img.size

                # 过滤太小的图片（可能是图标）
                if width < min_size or height < min_size:
                    continue

            except Exception:
                width, height = 0, 0

            # 提取附近文本作为 caption
            caption = _extract_figure_caption(page, img_index)

            figures.append(PDFFigure(
                id=f"fig_{page_num}_{img_index}",
                page_num=page_num,
                image_bytes=image_bytes,
                ext=ext,
                caption=caption,
                width=width,
                height=height
            ))

        # ===== 2. 提取矢量图（流程图、架构图等） =====
        if include_vector:
            vector_figures = _extract_vector_drawings(page, page_num, min_size)
            figures.extend(vector_figures)

    doc.close()
    return figures


def _extract_vector_drawings(page: fitz.Page, page_num: int, min_size: int = 100) -> List[PDFFigure]:
    """
    提取页面上的矢量绘图（流程图、架构图等）
    通过 get_drawings() 获取矢量指令并渲染为图片
    """
    figures = []

    try:
        # 获取页面上的所有绘图（矢量图）
        drawings = page.get_drawings()

        if not drawings:
            return figures

        # 将绘图按空间位置分组（简单的矩形聚类）
        # 策略：按 y 坐标分组，相近的 y 坐标可能属于同一个图
        drawing_groups = _group_drawings_by_position(drawings)

        for group_idx, group in enumerate(drawing_groups):
            try:
                # 计算这组绘图的边界框
                rect = fitz.Rect()
                for item in group:
                    if "rect" in item:
                        rect |= item["rect"]

                # 过滤太小的区域
                if rect.width < min_size or rect.height < min_size:
                    continue

                # 添加边距
                padding = 10
                rect = fitz.Rect(
                    max(0, rect.x0 - padding),
                    max(0, rect.y0 - padding),
                    min(page.rect.width, rect.x1 + padding),
                    min(page.rect.height, rect.y1 + padding)
                )

                # 渲染为图片
                pix = page.get_pixmap(clip=rect, matrix=fitz.Matrix(2, 2))  # 2x 分辨率
                image_bytes = pix.tobytes("png")

                # 提取附近文本作为 caption
                caption = _extract_vector_caption(page, rect)

                figures.append(PDFFigure(
                    id=f"vec_{page_num}_{group_idx}",
                    page_num=page_num,
                    image_bytes=image_bytes,
                    ext="png",
                    caption=caption,
                    width=int(rect.width * 2),
                    height=int(rect.height * 2)
                ))

            except Exception as e:
                print(f"Vector figure extraction error: {e}")
                continue

    except Exception as e:
        print(f"Drawings extraction error: {e}")

    return figures


def _group_drawings_by_position(drawings: list, y_threshold: float = 50) -> list:
    """
    将绘图按垂直位置分组
    简单的聚类策略：y 坐标相近的绘图可能属于同一个图
    """
    if not drawings:
        return []

    # 提取每个绘图的矩形和中心点
    items_with_center = []
    for d in drawings:
        if "rect" in d:
            rect = d["rect"]
            center_y = (rect.y0 + rect.y1) / 2
            items_with_center.append((d, center_y))

    # 按 y 坐标排序
    items_with_center.sort(key=lambda x: x[1])

    # 分组
    groups = []
    current_group = []
    current_y = None

    for item, y in items_with_center:
        if current_y is None or abs(y - current_y) < y_threshold:
            current_group.append(item)
        else:
            if current_group:
                groups.append(current_group)
            current_group = [item]
        current_y = y

    if current_group:
        groups.append(current_group)

    return groups


def _extract_vector_caption(page: fitz.Page, rect: fitz.Rect) -> str:
    """提取矢量图附近的文本（在图的上方或下方搜索）"""
    text = page.get_text("text")
    lines = text.split('\n')

    # 查找包含 figure/fig/table/图 的行
    for line in lines:
        line_lower = line.lower().strip()
        if any(prefix in line_lower for prefix in ['figure', 'fig.', 'table', '图', '表']):
            # 简单返回匹配的行
            return line.strip()

    return ""


def _extract_figure_caption(page: fitz.Page, img_index: int) -> str:
    """提取图片附近的文本（Figure X: ...）"""
    text = page.get_text("text")

    # 查找可能的 caption 行
    lines = text.split('\n')
    for line in lines:
        line_lower = line.lower()
        if f'figure {img_index}' in line_lower or f'fig. {img_index}' in line_lower:
            return line.strip()
        if f'图 {img_index}' in line or f'图表 {img_index}' in line:
            return line.strip()

    return ""


def encode_image_for_vlm(image_bytes: bytes, max_size: int = 1024) -> str:
    """
    将图片转为 base64，并缩放以适应 VLM

    Args:
        image_bytes: 原始图片字节
        max_size: 最大边长（VLM 通常限制 1024 或 2048）

    Returns:
        base64 编码的图片字符串
    """
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))

        # 等比缩放
        width, height = img.size
        if width > max_size or height > max_size:
            ratio = max_size / max(width, height)
            new_size = (int(width * ratio), int(height * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)

        # 转为 RGB（去除 alpha 通道）
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')

        # 保存为 JPEG（体积小）
        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=85)
        encoded = base64.b64encode(buffer.getvalue()).decode('utf-8')

        return encoded

    except Exception as e:
        print(f"Image encoding error: {e}")
        return ""


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
        "figure_count": len(doc.get_images()),
    }
    doc.close()
    return info
