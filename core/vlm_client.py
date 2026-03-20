"""
core/vlm_client.py
VLM（Vision Language Model）图表分析客户端
支持 GPT-4o, Claude 3, Qwen 等多模态模型
"""

import os
import base64
from typing import Optional, List, Dict
from dataclasses import dataclass


@dataclass
class FigureAnalysis:
    """图表分析结果"""
    figure_id: str
    figure_type: str          # chart, table, diagram, other
    is_significant: bool      # 是否是关键图表
    summary: str              # 分析摘要


class VLMClient:
    """VLM 客户端封装"""

    def __init__(self):
        self.api_key = os.getenv("VLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.base_url = os.getenv("VLM_API_BASE") or os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
        # 分类使用轻量级模型，分析使用强模型
        self.model = os.getenv("VLM_MODEL", "qwen3.5-plus")  # 深度分析
        self.classify_model = os.getenv("VLM_CLASSIFY_MODEL", "qwen3.5-flash")  # 轻量分类

    def _classify_system_prompt(self, language: str = "zh-CN") -> str:
        """分类阶段的系统提示词"""
        if language == "zh-CN":
            return """你是一个学术论文图表分类助手。请判断图片类型并评估其重要性。

【图片类型】
- chart: 数据图表（柱状图、折线图、散点图、热力图等）
- table: 表格
- diagram: 流程图、架构图、模型结构图、概念图、算法流程图等
- other: 其他图片（照片、示意图等）

【重要性判断标准】is_significant 为 true 当且仅当图片属于以下类别：
1. 实验结果图表（chart/table）- 包含数据、指标、对比结果
2. 模型架构图 - 展示模型结构、网络设计、组件关系
3. 流程图 - 算法流程、数据处理流程、系统流程
4. 概念图 - 核心概念、框架设计、方法论
5. 消融研究/对比分析图

【不重要图片】装饰性图片、重复性示例、纯插图等标记为 false，并在 reason 中说明原因。

返回 JSON 格式：{"type": "chart|table|diagram|other", "is_significant": true|false, "reason": "简要说明判断理由"}"""
        else:
            return """You are an academic paper figure classification assistant. Determine the figure type and assess its importance.

【Figure Types】
- chart: Data charts (bar, line, scatter, heatmap, etc.)
- table: Tables
- diagram: Flowcharts, architecture diagrams, model structures, concept diagrams, algorithm pipelines
- other: Other images (photos, illustrations, etc.)

【Importance Criteria】is_significant is true if the figure is:
1. Experimental results (chart/table) - contains data, metrics, comparisons
2. Model architecture - shows model structure, network design, component relationships
3. Flowcharts - algorithm flow, data processing pipeline, system workflow
4. Conceptual diagrams - core concepts, framework design, methodology
5. Ablation studies / comparative analysis

【Not Significant】Decorative images, repetitive examples, pure illustrations should be false with reason explaining why.

Return JSON format: {"type": "chart|table|diagram|other", "is_significant": true|false, "reason": "brief explanation of the judgment"}"""

    def classify_figure(self, image_b64: str, caption: str = "", language: str = "zh-CN") -> Dict:
        """
        轻量级分类：判断图片类型和重要性
        使用轻量模型（如 qwen3.5-flash）
        """
        # 根据语言调整用户提示
        if language == "zh-CN":
            user_text = f"图表标题/附近文本：{caption}\n\n请判断图片类型和重要性。"
        else:
            user_text = f"Figure caption/nearby text: {caption}\n\nPlease determine the figure type and importance."

        messages = [
            {
                "role": "system",
                "content": self._classify_system_prompt(language)
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": user_text
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}
                    }
                ]
            }
        ]

        try:
            result = self._call_vlm(messages, self.classify_model, max_tokens=300)
            # 简单解析
            import json
            import re

            # 尝试提取 JSON
            json_match = re.search(r'\{[^}]+\}', result)
            if json_match:
                parsed = json.loads(json_match.group())
                # 输出不重要图片的原因到标准输出
                if not parsed.get("is_significant", True):
                    reason = parsed.get("reason", "未提供原因")
                    print(f"[VLM 分类] 跳过不重要的图片: {reason}")
                return parsed

            # 回退：文本解析
            is_significant = any(kw in result.lower() for kw in ['关键', '重要', 'key', 'important', '核心', 'core'])
            figure_type = "chart" if any(kw in result.lower() for kw in ['图表', 'graph', 'chart']) else "other"
            return {"type": figure_type, "is_significant": is_significant, "reason": result}

        except Exception as e:
            print(f"Classification error: {e}")
            return {"type": "unknown", "is_significant": True, "reason": "classification failed"}

    def _analyze_system_prompt(self, language: str = "zh-CN") -> str:
        """分析阶段的系统提示词"""
        if language == "zh-CN":
            return """你是一位专业的学术论文图表分析专家。请根据图表类型进行针对性分析：

【数据图表 chart】（柱状图、折线图、散点图、热力图等）：
1. **图表类型**：具体类型名称
2. **坐标轴/维度**：X轴/Y轴代表什么，单位是什么
3. **关键数据**：最重要的数据点、极值、基准线、对比关系
4. **核心结论**：图表传达的主要发现或趋势
5. **研究关联**：与研究目标的相关性

【表格 table】：
1. **表格结构**：行/列分别代表什么
2. **关键数据**：最重要的数值、最优结果、对比关系
3. **核心结论**：数据揭示的规律或发现
4. **研究关联**：与研究目标的相关性

【流程图/架构图 diagram】：
1. **图表类型**：流程图、架构图、模型结构图、概念图等
2. **整体结构**：主要组件、模块、阶段
3. **关键流程/关系**：数据流向、组件交互、核心算法步骤
4. **核心设计**：创新点、关键决策、与 baseline 的差异
5. **研究关联**：该设计如何支撑研究目标

请用结构化格式输出，针对图表类型突出重点。"""
        else:
            return """You are a professional academic paper figure analysis expert. Please analyze according to the figure type:

【Data Charts】 (bar, line, scatter, heatmap, etc.):
1. **Figure Type**: Specific type name
2. **Axes/Dimensions**: What X/Y axes represent, units
3. **Key Data**: Important data points, extrema, baselines, comparisons
4. **Core Conclusions**: Main findings or trends
5. **Research Relevance**: Relation to research goal

【Tables】:
1. **Structure**: What rows/columns represent
2. **Key Data**: Important values, best results, comparisons
3. **Core Conclusions**: Patterns or insights revealed
4. **Research Relevance**: Relation to research goal

【Diagrams/Architecture】 (flowcharts, architecture diagrams, model structures):
1. **Diagram Type**: Flowchart, architecture, model structure, concept map
2. **Overall Structure**: Main components, modules, stages
3. **Key Flows/Relations**: Data flow, component interactions, algorithm steps
4. **Core Design**: Innovations, key decisions, differences from baseline
5. **Research Relevance**: How this design supports the research goal

Output in structured format, highlighting key points based on figure type."""

    def analyze_figure(self, image_b64: str, caption: str, research_goal: str, language: str = "zh-CN") -> str:
        """
        深度分析图表内容
        使用强模型（如 qwen3.5-plus）
        """
        # 根据语言调整用户提示
        if language == "zh-CN":
            user_text = f"研究目标：{research_goal}\n\n图表标题：{caption}\n\n请详细分析："
        else:
            user_text = f"Research Goal: {research_goal}\n\nFigure Caption: {caption}\n\nPlease analyze in detail:"

        messages = [
            {
                "role": "system",
                "content": self._analyze_system_prompt(language)
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": user_text
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}
                    }
                ]
            }
        ]

        return self._call_vlm(messages, self.model, max_tokens=1500)

    def _call_vlm(self, messages: list, model: str, max_tokens: int = 1000) -> str:
        """调用 VLM API"""
        try:
            import openai
            client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)

            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.3
            )

            return response.choices[0].message.content or ""

        except Exception as e:
            print(f"VLM API error: {e}")
            raise


def analyze_figures_batch(figures: List, research_goal: str, max_figures: int = 5, language: str = "zh-CN") -> List[FigureAnalysis]:
    """
    批量分析图表（分类 + 深度分析）

    Args:
        figures: PDFFigure 列表
        research_goal: 研究目标文本
        max_figures: 最多分析几张重要图表
        language: 输出语言，"zh-CN" 或 "en"

    Returns:
        FigureAnalysis 列表
    """
    from core.pdf_parser import encode_image_for_vlm

    client = VLMClient()
    results = []
    key_figures = []

    # Step 1: 分类所有图表
    print(f"Classifying {len(figures)} figures...")
    for fig in figures:
        image_b64 = encode_image_for_vlm(fig.image_bytes)
        if not image_b64:
            continue

        classification = client.classify_figure(image_b64, fig.caption, language)
        fig.classification = classification

        if classification.get("is_significant", True):
            key_figures.append(fig)

    # Step 2: 深度分析重要图表（限制数量）
    print(f"Analyzing {min(len(key_figures), max_figures)} significant figures...")
    for fig in key_figures[:max_figures]:
        image_b64 = encode_image_for_vlm(fig.image_bytes)
        analysis_text = client.analyze_figure(image_b64, fig.caption, research_goal, language)

        results.append(FigureAnalysis(
            figure_id=fig.id,
            figure_type=fig.classification.get("type", "unknown"),
            is_significant=True,
            summary=analysis_text
        ))

    return results
