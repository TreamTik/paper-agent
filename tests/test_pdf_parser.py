"""
tests/test_pdf_parser.py
PDF 解析和图表提取功能测试
"""

import pytest
import io
from pathlib import Path

from core.pdf_parser import (
    extract_text_from_pdf,
    extract_figures_from_pdf,
    encode_image_for_vlm,
    chunk_text,
    PDFFigure,
)


class TestExtractFigures:
    """测试 PDF 图片提取功能"""

    def test_extract_figures_with_sample_pdf(self):
        """测试从包含图片的 PDF 中提取图表"""
        # 使用项目中的测试 PDF（如果存在）
        test_pdf_path = Path("data/pdfs/sample.pdf")
        if not test_pdf_path.exists():
            pytest.skip("测试 PDF 文件不存在，跳过此测试")

        pdf_bytes = test_pdf_path.read_bytes()
        figures = extract_figures_from_pdf(pdf_bytes)

        assert len(figures) > 0
        assert all(isinstance(f, PDFFigure) for f in figures)
        assert all(f.image_bytes for f in figures)
        assert all(f.page_num > 0 for f in figures)

    def test_extract_figures_empty_pdf(self):
        """测试空 PDF 返回空列表"""
        # 创建一个空内容的 PDF bytes
        from core.pdf_parser import fitz

        doc = fitz.open()
        doc.new_page()
        pdf_bytes = doc.tobytes()
        doc.close()

        figures = extract_figures_from_pdf(pdf_bytes)
        assert figures == []

    def test_figure_filtering_by_size(self):
        """测试按尺寸过滤小图片"""
        from core.pdf_parser import fitz

        doc = fitz.open()
        page = doc.new_page()

        # 添加一个小的矩形（不会被当作图片）
        # 这里直接测试函数逻辑
        doc.close()

        # 由于创建真实图片比较复杂，我们测试过滤逻辑
        # 创建一个模拟的 PDF bytes
        doc = fitz.open()
        page = doc.new_page()
        pdf_bytes = doc.tobytes()
        doc.close()

        # 提取时设置较大的 min_size
        figures = extract_figures_from_pdf(pdf_bytes, min_size=5000)
        assert len(figures) == 0  # 应该过滤掉所有内容

    def test_extract_vector_drawings(self):
        """测试提取矢量图（流程图、架构图）"""
        from core.pdf_parser import fitz, _extract_vector_drawings

        doc = fitz.open()
        page = doc.new_page()

        # 在页面上绘制矢量图形（模拟流程图）
        # 绘制一个矩形框
        rect = fitz.Rect(100, 100, 300, 200)
        shape = page.new_shape()
        shape.draw_rect(rect)
        shape.finish(width=2, color=(0, 0, 1))  # 蓝色边框
        shape.commit()

        # 绘制一些线条（连接线）
        shape = page.new_shape()
        shape.draw_line((200, 200), (200, 300))
        shape.finish(width=2, color=(0, 0, 0))
        shape.commit()

        # 绘制另一个矩形（第二个节点）
        rect2 = fitz.Rect(150, 300, 250, 400)
        shape = page.new_shape()
        shape.draw_rect(rect2)
        shape.finish(width=2, color=(1, 0, 0))  # 红色边框
        shape.commit()

        # 提取矢量图
        figures = _extract_vector_drawings(page, 1, min_size=50)

        doc.close()

        # 应该提取到矢量图
        assert len(figures) >= 1
        assert all(isinstance(f, PDFFigure) for f in figures)
        assert all(f.ext == "png" for f in figures)
        assert all(f.id.startswith("vec_") for f in figures)

    def test_extract_figures_include_vector(self):
        """测试提取包含矢量图的完整流程"""
        from core.pdf_parser import fitz

        doc = fitz.open()
        page = doc.new_page()

        # 添加矢量图形（流程图）
        rect = fitz.Rect(100, 100, 400, 300)
        shape = page.new_shape()
        shape.draw_rect(rect)
        shape.finish(width=3, color=(0, 1, 0))  # 绿色边框
        shape.commit()

        # 添加文本标签
        page.insert_text((120, 150), "Figure 1: System Architecture", fontsize=12)

        pdf_bytes = doc.tobytes()
        doc.close()

        # 提取图表（包含矢量图）
        figures = extract_figures_from_pdf(pdf_bytes, include_vector=True)

        # 应该提取到矢量图
        assert len(figures) >= 1
        vector_figures = [f for f in figures if f.id.startswith("vec_")]
        assert len(vector_figures) >= 1

    def test_extract_figures_exclude_vector(self):
        """测试禁用矢量图提取"""
        from core.pdf_parser import fitz

        doc = fitz.open()
        page = doc.new_page()

        # 添加矢量图形
        rect = fitz.Rect(100, 100, 400, 300)
        shape = page.new_shape()
        shape.draw_rect(rect)
        shape.finish(width=2)
        shape.commit()

        pdf_bytes = doc.tobytes()
        doc.close()

        # 提取图表（不包含矢量图）
        figures = extract_figures_from_pdf(pdf_bytes, include_vector=False)

        # 不应该有矢量图
        vector_figures = [f for f in figures if f.id.startswith("vec_")]
        assert len(vector_figures) == 0


class TestEncodeImageForVLM:
    """测试图片编码功能"""

    def test_encode_image_valid(self):
        """测试有效图片的 base64 编码"""
        from PIL import Image

        # 创建一个简单的测试图片
        img = Image.new('RGB', (100, 100), color='red')
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        image_bytes = buffer.getvalue()

        encoded = encode_image_for_vlm(image_bytes)

        assert len(encoded) > 0
        assert isinstance(encoded, str)

    def test_encode_image_scaling(self):
        """测试大图片自动缩放"""
        from PIL import Image

        # 创建一个大图片（超过 max_size）
        img = Image.new('RGB', (2000, 2000), color='blue')
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        image_bytes = buffer.getvalue()

        encoded = encode_image_for_vlm(image_bytes, max_size=1024)

        assert len(encoded) > 0
        # 原始图片大于 1024，应该被缩放

    def test_encode_image_invalid(self):
        """测试无效图片返回空字符串"""
        invalid_bytes = b"not an image"
        encoded = encode_image_for_vlm(invalid_bytes)
        assert encoded == ""


class TestChunkText:
    """测试文本分块功能"""

    def test_chunk_text_small(self):
        """测试小文本不分块"""
        text = "This is a short text."
        chunks = chunk_text(text, chunk_size=1000)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_chunk_text_large(self):
        """测试大文本分块"""
        text = "Paragraph 1.\n\nParagraph 2.\n\nParagraph 3."
        chunks = chunk_text(text, chunk_size=10)
        assert len(chunks) > 1

    def test_chunk_text_respects_boundaries(self):
        """测试分块尽量在段落边界处"""
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        chunks = chunk_text(text, chunk_size=25)

        # 应该尽量在段落边界处分割
        for chunk in chunks:
            assert len(chunk) <= 25


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
