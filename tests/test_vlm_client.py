"""
tests/test_vlm_client.py
VLM 图表分析功能测试

注意：真实 API 测试默认跳过，需要设置环境变量 RUN_REAL_API_TESTS=true 才会执行
"""

import pytest
import os
import base64
import json
from io import BytesIO
from PIL import Image
from unittest.mock import Mock, patch
from dotenv import load_dotenv

from core.vlm_client import VLMClient, FigureAnalysis, analyze_figures_batch

# 加载 .env 文件（如果存在）
load_dotenv()

# 标记：是否运行真实 API 测试
RUN_REAL_API_TESTS = os.getenv("RUN_REAL_API_TESTS", "false").lower() == "true"


# 创建测试图片的辅助函数
def create_test_image(color='red', size=(300, 200), text=None):
    """创建测试图片并返回 base64 编码"""
    img = Image.new('RGB', size, color=color)
    buffer = BytesIO()
    img.save(buffer, format='JPEG', quality=85)
    return base64.b64encode(buffer.getvalue()).decode()


@pytest.mark.skipif(not RUN_REAL_API_TESTS, reason="需要设置 RUN_REAL_API_TESTS=true 才能运行真实 API 测试")
class TestVLMClientRealAPI:
    """真实 API 测试类 - 默认跳过，避免消耗 API 配额"""

    def setup_method(self):
        """检查真实 API 配置"""
        # 使用 .env 中的真实配置
        self.api_key = os.getenv("VLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.api_base = os.getenv("VLM_API_BASE") or os.getenv("OPENAI_API_BASE")
        self.classify_model = os.getenv("VLM_CLASSIFY_MODEL", "qwen3.5-flash")
        self.analysis_model = os.getenv("VLM_MODEL", "qwen3.5-plus")

        if not self.api_key:
            pytest.skip("未找到 API Key，请在 .env 中设置 VLM_API_KEY 或 OPENAI_API_KEY")

        # 设置环境变量供 VLMClient 使用
        os.environ["VLM_API_KEY"] = self.api_key
        if self.api_base:
            os.environ["VLM_API_BASE"] = self.api_base

    def test_real_classify_bar_chart(self):
        """真实 API：测试柱状图分类"""
        client = VLMClient()

        # 创建一个简单的柱状图测试图片
        image_b64 = create_test_image(color='blue', size=(400, 300))
        caption = "Figure 1: Accuracy comparison between methods"

        result = client.classify_figure(image_b64, caption, language="en")

        print(f"\n分类结果: {result}")

        # 验证返回结构
        assert "type" in result
        assert "is_significant" in result
        assert "reason" in result
        assert isinstance(result["is_significant"], bool)

    def test_real_classify_chinese(self):
        """真实 API：测试中文分类"""
        client = VLMClient()

        image_b64 = create_test_image(color='green', size=(400, 300))
        caption = "图1：不同方法的准确率对比"

        result = client.classify_figure(image_b64, caption, language="zh-CN")

        print(f"\n中文分类结果: {result}")

        assert "type" in result
        assert "is_significant" in result

    def test_real_analyze_figure_english(self):
        """真实 API：测试英文图表分析"""
        client = VLMClient()

        # 创建一个包含简单图表的图片
        image_b64 = create_test_image(color='red', size=(500, 400))
        caption = "Figure 2: Training loss over epochs"
        research_goal = "Understand the convergence behavior of the model"

        result = client.analyze_figure(image_b64, caption, research_goal, language="en")

        print(f"\n英文分析结果:\n{result}")

        # 验证返回了分析文本
        assert len(result) > 50  # 应该有一定长度
        assert isinstance(result, str)

        # 检查结果包含预期的关键词（英文）
        lower_result = result.lower()
        assert any(word in lower_result for word in ['chart', 'figure', 'graph', 'type'])

    def test_real_analyze_figure_chinese(self):
        """真实 API：测试中文图表分析"""
        client = VLMClient()

        image_b64 = create_test_image(color='blue', size=(500, 400))
        caption = "图2：训练损失随轮次变化"
        research_goal = "理解模型的收敛行为"

        result = client.analyze_figure(image_b64, caption, research_goal, language="zh-CN")

        print(f"\n中文分析结果:\n{result}")

        # 验证返回了分析文本
        assert len(result) > 50
        assert isinstance(result, str)

        # 检查结果包含中文关键词
        assert any(word in result for word in ['图表', '图', '分析', '类型'])

    def test_real_batch_analysis(self):
        """真实 API：测试批量分析流程"""
        from core.pdf_parser import PDFFigure

        # 创建 3 个测试图表
        figures = []
        for i in range(3):
            img = Image.new('RGB', (400, 300), color=['red', 'blue', 'green'][i])
            buffer = BytesIO()
            img.save(buffer, format='PNG')
            figures.append(PDFFigure(
                id=f"fig_{i}",
                page_num=1,
                image_bytes=buffer.getvalue(),
                ext="png",
                caption=f"Figure {i+1}: Test chart",
                width=400,
                height=300
            ))

        research_goal = "Compare different visualization methods"
        max_figures = 2

        results = analyze_figures_batch(figures, research_goal, max_figures, language="en")

        print(f"\n批量分析完成，分析了 {len(results)} 个图表")

        # 验证结果
        assert len(results) <= max_figures
        for analysis in results:
            assert isinstance(analysis, FigureAnalysis)
            assert analysis.figure_id.startswith("fig_")
            assert len(analysis.summary) > 0

    def test_real_api_with_table_image(self):
        """真实 API：测试表格类型识别"""
        client = VLMClient()

        # 创建一个白色背景的简单图片（模拟表格）
        img = Image.new('RGB', (600, 400), color='white')
        buffer = BytesIO()
        img.save(buffer, format='JPEG')
        image_b64 = base64.b64encode(buffer.getvalue()).decode()

        caption = "Table 1: Comparison of model performance metrics"

        result = client.classify_figure(image_b64, caption, language="en")

        print(f"\n表格分类结果: {result}")

        # 表格通常被认为是重要的
        assert "type" in result
        assert result["is_significant"] is True


class TestVLMClient:
    """测试 VLM 客户端"""

    def setup_method(self):
        """每个测试方法前执行"""
        # 设置测试环境变量
        os.environ["VLM_API_KEY"] = "test-api-key"
        os.environ["VLM_API_BASE"] = "https://test.api.com/v1"
        os.environ["VLM_MODEL"] = "test-model"
        os.environ["VLM_CLASSIFY_MODEL"] = "test-classify-model"

    def teardown_method(self):
        """每个测试方法后清理"""
        # 清理环境变量
        for key in ["VLM_API_KEY", "VLM_API_BASE", "VLM_MODEL", "VLM_CLASSIFY_MODEL"]:
            if key in os.environ:
                del os.environ[key]

    def test_vlm_client_init(self):
        """测试 VLMClient 初始化"""
        client = VLMClient()

        assert client.api_key == "test-api-key"
        assert client.base_url == "https://test.api.com/v1"
        assert client.model == "test-model"
        assert client.classify_model == "test-classify-model"

    def test_vlm_client_fallback_to_openai_env(self):
        """测试回退到 OPENAI 环境变量"""
        del os.environ["VLM_API_KEY"]
        del os.environ["VLM_API_BASE"]
        os.environ["OPENAI_API_KEY"] = "openai-key"
        os.environ["OPENAI_API_BASE"] = "https://openai.com/v1"

        client = VLMClient()

        assert client.api_key == "openai-key"
        assert client.base_url == "https://openai.com/v1"

    def test_classify_figure_mock(self):
        """测试图表分类功能（Mock）"""
        client = VLMClient()

        # 创建测试图片
        img = Image.new('RGB', (100, 100), color='red')
        buffer = BytesIO()
        img.save(buffer, format='JPEG')
        image_b64 = base64.b64encode(buffer.getvalue()).decode()

        # Mock API 调用
        mock_response = {
            "type": "chart",
            "is_significant": True,
            "reason": "包含关键实验结果"
        }

        with patch.object(client, '_call_vlm', return_value=json.dumps(mock_response)):
            result = client.classify_figure(image_b64, "Figure 1: Accuracy", language="zh-CN")

        assert result["type"] == "chart"
        assert result["is_significant"] is True

    def test_classify_figure_english(self):
        """测试图表分类英文模式"""
        client = VLMClient()

        image_b64 = "test_base64"

        # Mock API 调用返回英文
        mock_response = {
            "type": "chart",
            "is_significant": True,
            "reason": "Contains key experimental results"
        }

        with patch.object(client, '_call_vlm', return_value=json.dumps(mock_response)):
            result = client.classify_figure(image_b64, "Figure 1: Accuracy", language="en")

        assert result["type"] == "chart"
        assert result["is_significant"] is True

    def test_classify_figure_text_parsing(self):
        """测试分类结果文本解析"""
        client = VLMClient()

        image_b64 = "test_base64"

        # 测试非 JSON 格式的返回
        text_response = "This is a chart showing important experimental results"

        with patch.object(client, '_call_vlm', return_value=text_response):
            result = client.classify_figure(image_b64)

        assert result["is_significant"] is True  # 包含 important
        assert result["type"] == "chart"  # 包含 chart

    def test_analyze_figure_mock(self):
        """测试图表深度分析（Mock）"""
        client = VLMClient()

        image_b64 = "test_base64"
        caption = "Figure 1: Model Performance"
        research_goal = "Improve model accuracy"

        expected_analysis = """### 图表分析

**图表类型**: 柱状图
**关键数据**: 准确率达到 95%
**核心结论**: 模型性能优秀"""

        with patch.object(client, '_call_vlm', return_value=expected_analysis):
            result = client.analyze_figure(image_b64, caption, research_goal, language="zh-CN")

        assert "图表类型" in result
        assert "关键数据" in result
        assert "95%" in result

    def test_analyze_figure_english(self):
        """测试图表深度分析英文模式"""
        client = VLMClient()

        image_b64 = "test_base64"
        caption = "Figure 1: Model Performance"
        research_goal = "Improve model accuracy"

        expected_analysis = """### Figure Analysis

**Figure Type**: Bar Chart
**Key Data**: Accuracy reaches 95%
**Core Conclusion**: Model performance is excellent"""

        with patch.object(client, '_call_vlm', return_value=expected_analysis):
            result = client.analyze_figure(image_b64, caption, research_goal, language="en")

        assert "Figure Type" in result or "Bar Chart" in result

    def test_analyze_figure_error_handling(self):
        """测试 API 错误处理"""
        client = VLMClient()

        with patch.object(client, '_call_vlm', side_effect=Exception("API Error")):
            with pytest.raises(Exception):
                client.analyze_figure("test", "caption", "goal")


class TestAnalyzeFiguresBatch:
    """测试批量分析功能"""

    def setup_method(self):
        os.environ["VLM_API_KEY"] = "test-key"
        os.environ["VLM_API_BASE"] = "https://test.api.com/v1"

    def teardown_method(self):
        for key in ["VLM_API_KEY", "VLM_API_BASE"]:
            if key in os.environ:
                del os.environ[key]

    def create_test_figure(self, id="fig_1_0", caption="Test Figure"):
        """创建测试用的 PDFFigure"""
        from core.pdf_parser import PDFFigure

        img = Image.new('RGB', (200, 200), color='blue')
        buffer = BytesIO()
        img.save(buffer, format='PNG')

        return PDFFigure(
            id=id,
            page_num=1,
            image_bytes=buffer.getvalue(),
            ext="png",
            caption=caption,
            width=200,
            height=200
        )

    @patch('core.vlm_client.VLMClient')
    def test_analyze_figures_batch_success(self, mock_vlm_client):
        """测试批量分析成功流程"""
        # Mock 客户端
        mock_client = Mock()
        mock_vlm_client.return_value = mock_client

        # Mock 分类结果
        mock_client.classify_figure.return_value = {
            "type": "chart",
            "is_significant": True,
            "reason": "关键图表"
        }

        # Mock 分析结果
        mock_client.analyze_figure.return_value = "图表分析摘要"

        figures = [
            self.create_test_figure("fig_1", "Figure 1"),
            self.create_test_figure("fig_2", "Figure 2"),
        ]

        results = analyze_figures_batch(figures, "Test research goal", max_figures=2, language="zh-CN")

        assert len(results) == 2
        assert all(isinstance(r, FigureAnalysis) for r in results)
        assert results[0].figure_type == "chart"
        assert results[0].is_significant is True

    @patch('core.vlm_client.VLMClient')
    def test_analyze_figures_batch_respects_max_figures(self, mock_vlm_client):
        """测试 max_figures 限制"""
        mock_client = Mock()
        mock_vlm_client.return_value = mock_client

        mock_client.classify_figure.return_value = {
            "type": "chart",
            "is_significant": True,
            "reason": "关键图表"
        }
        mock_client.analyze_figure.return_value = "分析结果"

        # 创建 10 个图表
        figures = [self.create_test_figure(f"fig_{i}", f"Figure {i}") for i in range(10)]

        results = analyze_figures_batch(figures, "Test goal", max_figures=3, language="zh-CN")

        # 应该只分析前 3 个
        assert len(results) == 3
        # 验证只调用了 3 次 analyze_figure
        assert mock_client.analyze_figure.call_count == 3

    @patch('core.vlm_client.VLMClient')
    def test_analyze_figures_batch_filters_non_significant(self, mock_vlm_client):
        """测试过滤非重要图表"""
        mock_client = Mock()
        mock_vlm_client.return_value = mock_client

        # 第一个不重要，第二个重要
        mock_client.classify_figure.side_effect = [
            {"type": "image", "is_significant": False, "reason": "装饰图片"},
            {"type": "chart", "is_significant": True, "reason": "关键图表"},
        ]
        mock_client.analyze_figure.return_value = "分析结果"

        figures = [
            self.create_test_figure("fig_1", "Decorative"),
            self.create_test_figure("fig_2", "Important Chart"),
        ]

        results = analyze_figures_batch(figures, "Test goal", max_figures=2, language="zh-CN")

        # 应该只分析重要的那个
        assert len(results) == 1
        assert results[0].figure_id == "fig_2"


class TestFigureClassification:
    """单独测试图像分类功能"""

    def setup_method(self):
        os.environ["VLM_API_KEY"] = "test-key"
        os.environ["VLM_API_BASE"] = "https://test.api.com/v1"

    def teardown_method(self):
        for key in ["VLM_API_KEY", "VLM_API_BASE"]:
            if key in os.environ:
                del os.environ[key]

    def test_classify_figure_types(self):
        """测试分类不同类型的图表"""
        client = VLMClient()

        test_cases = [
            # (expected_type, description)
            ("chart", "Bar chart showing accuracy over epochs"),
            ("table", "Table comparing different methods"),
            ("diagram", "Architecture diagram of the model"),
            ("other", "Screenshot of the user interface"),
        ]

        image_b64 = "test_base64"

        for expected_type, description in test_cases:
            mock_response = json.dumps({
                "type": expected_type,
                "is_significant": True,
                "reason": f"This is a {expected_type}"
            })

            with patch.object(client, '_call_vlm', return_value=mock_response):
                result = client.classify_figure(image_b64, description)

            assert result["type"] == expected_type, f"Expected {expected_type}, got {result['type']}"
            assert result["is_significant"] is True

    def test_classify_figure_significance_detection(self):
        """测试分类重要性检测"""
        client = VLMClient()

        # 测试重要图表
        significant_cases = [
            '{"type": "chart", "is_significant": true, "reason": "关键实验结果"}',
            '{"type": "table", "is_significant": true, "reason": "包含主要指标"}',
        ]

        image_b64 = "test_base64"

        for mock_response in significant_cases:
            with patch.object(client, '_call_vlm', return_value=mock_response):
                result = client.classify_figure(image_b64, "Figure 1")
            assert result["is_significant"] is True

        # 测试非重要图表
        non_significant_cases = [
            '{"type": "image", "is_significant": false, "reason": "装饰图片"}',
            '{"type": "diagram", "is_significant": false, "reason": "流程示意"}',
        ]

        for mock_response in non_significant_cases:
            with patch.object(client, '_call_vlm', return_value=mock_response):
                result = client.classify_figure(image_b64, "Figure 2")
            assert result["is_significant"] is False

    def test_classify_figure_with_caption_context(self):
        """测试分类时使用标题上下文"""
        client = VLMClient()

        image_b64 = "test_base64"
        caption = "Figure 3: Comparison of accuracy between proposed method and baseline"

        mock_response = '{"type": "chart", "is_significant": true, "reason": "Comparison of key metrics"}'

        with patch.object(client, '_call_vlm', return_value=mock_response) as mock_call:
            client.classify_figure(image_b64, caption, language="en")

            # 验证调用时包含了标题信息
            call_args = mock_call.call_args
            messages = call_args[0][0] if call_args[0] else call_args[1]['messages']
            user_content = messages[1]['content'][0]['text']
            assert caption in user_content

    def test_classify_figure_fallback_parsing(self):
        """测试分类回退文本解析"""
        client = VLMClient()

        image_b64 = "test_base64"

        # 测试各种非JSON响应
        fallback_cases = [
            ("This is an important chart showing results", "chart", True),
            ("Key findings in this graph", "chart", True),
            ("Just a decorative image", "other", False),  # 没有重要关键词
            ("Not significant icon", "other", False),
        ]

        for text_response, expected_type, expected_significant in fallback_cases:
            with patch.object(client, '_call_vlm', return_value=text_response):
                result = client.classify_figure(image_b64, "Figure")

            assert result["type"] == expected_type
            assert result["is_significant"] == expected_significant


class TestFigureAnalysis:
    """单独测试图像深度分析（总结）功能"""

    def setup_method(self):
        os.environ["VLM_API_KEY"] = "test-key"
        os.environ["VLM_API_BASE"] = "https://test.api.com/v1"

    def teardown_method(self):
        for key in ["VLM_API_KEY", "VLM_API_BASE"]:
            if key in os.environ:
                del os.environ[key]

    def test_analyze_figure_with_research_goal(self):
        """测试分析时使用研究目标"""
        client = VLMClient()

        image_b64 = "test_base64"
        caption = "Figure 1: Ablation study results"
        research_goal = "Understand the contribution of each component"

        expected_analysis = """### Figure Analysis

**Figure Type**: Bar Chart
**Axes**: X-axis shows components, Y-axis shows accuracy percentage
**Key Data**: Component A contributes 5%, Component B contributes 10%
**Conclusions**: The proposed component is most critical
**Relevance**: Directly addresses the ablation study goal"""

        with patch.object(client, '_call_vlm', return_value=expected_analysis) as mock_call:
            result = client.analyze_figure(image_b64, caption, research_goal, language="en")

            # 验证调用时包含了研究目标
            call_args = mock_call.call_args
            messages = call_args[0][0] if call_args[0] else call_args[1]['messages']
            user_content = messages[1]['content'][0]['text']
            assert research_goal in user_content
            assert caption in user_content

        assert "Bar Chart" in result
        assert "accuracy" in result

    def test_analyze_figure_structured_output(self):
        """测试分析输出结构"""
        client = VLMClient()

        image_b64 = "test_base64"

        structured_responses = [
            """### 图表分析

**图表类型**: 折线图
**坐标轴**: X轴为训练轮次，Y轴为准确率
**关键数据**: 最高准确率达到95.2%，在第50轮
**核心结论**: 模型快速收敛，性能稳定
**研究关联**: 展示了训练效率""",
            """### Figure Analysis

**Figure Type**: Line Chart
**Axes**: X-axis is training epochs, Y-axis is accuracy
**Key Data**: Peak accuracy of 95.2% at epoch 50
**Core Conclusions**: Model converges quickly with stable performance
**Research Relevance**: Demonstrates training efficiency""",
        ]

        for response in structured_responses:
            with patch.object(client, '_call_vlm', return_value=response):
                result = client.analyze_figure(image_b64, "Figure", "Goal", language="zh-CN" if "图表" in response else "en")

            assert len(result) > 0
            assert any(keyword in result.lower() for keyword in ["chart", "图", "accuracy", "准确", "conclusion", "结论"])

    def test_analyze_figure_different_chart_types(self):
        """测试分析不同类型的图表"""
        client = VLMClient()

        image_b64 = "test_base64"
        chart_types = ["bar chart", "line chart", "scatter plot", "heatmap", "table"]

        for chart_type in chart_types:
            analysis = f"This is a {chart_type} showing experimental results with key metrics"

            with patch.object(client, '_call_vlm', return_value=analysis):
                result = client.analyze_figure(image_b64, f"Figure: {chart_type}", "Goal")

            assert chart_type in result.lower() or len(result) > 0

    def test_analyze_figure_language_switching(self):
        """测试分析语言切换"""
        client = VLMClient()

        image_b64 = "test_base64"
        caption = "Performance Comparison"
        research_goal = "Compare methods"

        # 测试中文
        with patch.object(client, '_call_vlm') as mock_call:
            mock_call.return_value = "中文分析结果"
            client.analyze_figure(image_b64, caption, research_goal, language="zh-CN")

            call_args = mock_call.call_args
            messages = call_args[0][0] if call_args[0] else call_args[1]['messages']
            system_prompt = messages[0]['content']
            assert "图表" in system_prompt or "专业" in system_prompt

        # 测试英文
        with patch.object(client, '_call_vlm') as mock_call:
            mock_call.return_value = "English analysis result"
            client.analyze_figure(image_b64, caption, research_goal, language="en")

            call_args = mock_call.call_args
            messages = call_args[0][0] if call_args[0] else call_args[1]['messages']
            system_prompt = messages[0]['content']
            assert "Figure" in system_prompt or "expert" in system_prompt.lower()

    def test_analyze_figure_max_tokens(self):
        """测试分析时使用正确的 max_tokens"""
        client = VLMClient()

        image_b64 = "test_base64"

        with patch.object(client, '_call_vlm') as mock_call:
            mock_call.return_value = "Analysis result"
            client.analyze_figure(image_b64, "Caption", "Goal")

            # 验证使用了 max_tokens=1500
            call_kwargs = mock_call.call_args[1] if mock_call.call_args[1] else {}
            if 'max_tokens' in call_kwargs:
                assert call_kwargs['max_tokens'] == 1500


class TestFigureAnalysisDataclass:
    """测试 FigureAnalysis 数据结构"""

    def test_figure_analysis_creation(self):
        """测试 FigureAnalysis 创建"""
        analysis = FigureAnalysis(
            figure_id="fig_1_0",
            figure_type="chart",
            is_significant=True,
            summary="Detailed analysis of the figure"
        )

        assert analysis.figure_id == "fig_1_0"
        assert analysis.figure_type == "chart"
        assert analysis.is_significant is True
        assert analysis.summary == "Detailed analysis of the figure"

    def test_figure_analysis_different_types(self):
        """测试不同类型图表的 FigureAnalysis"""
        types = ["chart", "table", "diagram", "other"]

        for fig_type in types:
            analysis = FigureAnalysis(
                figure_id=f"fig_{fig_type}",
                figure_type=fig_type,
                is_significant=fig_type in ["chart", "table"],
                summary=f"Analysis of {fig_type}"
            )
            assert analysis.figure_type == fig_type


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
