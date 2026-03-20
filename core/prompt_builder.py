"""
core/prompt_builder.py
拼接完整 Prompt：研究目标 + 输出模板 + PDF 文本
"""

from pathlib import Path
from core.config import get_domain
from core.state_manager import CONFIG_DIR

RESEARCH_GOAL_FILE   = CONFIG_DIR / "02_research_goal.md"
OUTPUT_TEMPLATE_FILE = CONFIG_DIR / "03_output_template.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _single_chunk_system(language: str = "zh-CN") -> str:
    domain = get_domain()
    domain_part = f"专注于 {domain}。\n" if domain else ""
    lang_instruction = "请使用中文输出分析报告。\n" if language == "zh-CN" else "Please write the analysis report in English.\n"
    return (
        f"你是一位专业的学术论文分析助手，{domain_part}"
        "你的任务是：根据用户的研究目标，对输入的论文全文进行深度分析，并严格按照指定的输出模板输出结果。\n"
        f"{lang_instruction}"
        "不要输出任何寒暄或额外的解释，直接输出 Markdown 格式的分析报告。"
    )

def build_single_prompt(paper_text: str, language: str = "zh-CN", figure_analysis: str = "") -> list[dict]:
    """PDF 文本较短时，直接用完整文本一次分析。"""
    research_goal = _read(RESEARCH_GOAL_FILE)
    output_template = _read(OUTPUT_TEMPLATE_FILE)

    # 如果有图表分析，添加说明
    figure_section = ""
    if figure_analysis:
        if language == "zh-CN":
            figure_section = f"""

## 关键图表分析（重要！必须包含在报告中）
以下是对论文中关键图表的详细分析结果。**请注意**：
1. 请在报告中单独列出"关键图表分析"栏目
2. **不要直接复制**以下内容，请用自己的语言**重新组织、润色表达**
3. 确保语言流畅、专业，与报告整体风格保持一致
4. 将各图表的分析整合成连贯的段落，可适当调整顺序和结构

{figure_analysis}"""
        else:
            figure_section = f"""

## Key Figure Analysis (Important! Must include in report)
Below is detailed analysis of key figures from the paper. **Please note**:
1. Include a separate "Key Figure Analysis" section in your report
2. **Do not copy verbatim** - please **reorganize and polish** the content in your own words
3. Ensure smooth, professional language consistent with the report style
4. Integrate analyses into coherent paragraphs, adjusting order and structure as needed

{figure_analysis}"""

    user_content = f"""## 研究目标
{research_goal}

## 期望输出格式（请严格遵守）
{output_template}{figure_section}

## 论文全文
{paper_text}"""
    return [
        {"role": "system", "content": _single_chunk_system(language)},
        {"role": "user",   "content": user_content},
    ]


# ── Map 阶段：对单个 Chunk 提取关键信息 ──────────────────────────────────────
def _map_system(language: str = "zh-CN") -> str:
    domain = get_domain()
    domain_part = f"专注于 {domain}。\n" if domain else ""
    lang_instruction = "输出结构化的中文摘要要点。\n" if language == "zh-CN" else "Output structured English summary points.\n"
    return (
        f"你是一位专业的学术论文摘要助手，{domain_part}"
        "你的任务是：从给定的论文片段中，提取与研究目标高度相关的关键信息，"
        f"{lang_instruction}"
        "不超过 800 字，不需要完整报告格式，只提取核心内容。"
    )

def build_map_prompt(chunk: str, chunk_idx: int, total: int, language: str = "zh-CN") -> list[dict]:
    """Map 阶段：提取单块关键信息。"""
    research_goal = _read(RESEARCH_GOAL_FILE)
    user_content = f"""## 研究目标（仅供参考方向）
{research_goal}

## 论文片段 [{chunk_idx}/{total}]
{chunk}

请提取与研究目标相关的关键信息要点（方法、数据集、指标、局限性等）。"""
    return [
        {"role": "system", "content": _map_system(language)},
        {"role": "user",   "content": user_content},
    ]


# ── Reduce 阶段：基于所有 Chunk 摘要生成最终报告 ─────────────────────────────
def build_reduce_prompt(chunk_summaries: list[str], language: str, figure_analysis: str = "") -> list[dict]:
    """Reduce 阶段：汇总所有 Chunk 摘要，生成完整分析报告。"""
    research_goal = _read(RESEARCH_GOAL_FILE)
    output_template = _read(OUTPUT_TEMPLATE_FILE)
    combined = "\n\n---\n\n".join(
        f"### 片段 {i+1} 要点摘要\n{s}" for i, s in enumerate(chunk_summaries)
    )

    # 如果有图表分析，添加说明
    figure_section = ""
    if figure_analysis:
        if language == "zh-CN":
            figure_section = f"""

## 关键图表分析（重要！必须包含在报告中）
以下是对论文中关键图表的详细分析结果。**请注意**：
1. 请在最终报告中单独列出"关键图表分析"栏目
2. **不要直接复制**以下内容，请用自己的语言**重新组织、润色表达**
3. 确保语言流畅、专业，与报告整体风格保持一致
4. 将各图表的分析整合成连贯的段落，可适当调整顺序和结构

{figure_analysis}"""
        else:
            figure_section = f"""

## Key Figure Analysis (Important! Must include in report)
Below is detailed analysis of key figures from the paper. **Please note**:
1. Include a separate "Key Figure Analysis" section
2. **Do not copy verbatim** - please **reorganize and polish** the content in your own words
3. Ensure smooth, professional language consistent with the report style
4. Integrate analyses into coherent paragraphs, adjusting order and structure as needed

{figure_analysis}"""

    user_content = f"""## 研究目标
{research_goal}

## 期望输出格式（请严格遵守）
{output_template}{figure_section}

## 各章节要点摘要（来自对长文的逐块提取）
{combined}

请基于以上所有摘要要点，综合生成完整的分析报告。  **使用{"中文" if language=='zh-CN' else "英文"}进行总结**"""
    return [
        {"role": "system", "content": _single_chunk_system(language)},
        {"role": "user",   "content": user_content},
    ]
