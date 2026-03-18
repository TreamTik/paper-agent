"""
core/idea_synthesizer.py
Idea 综合器：从多篇 MD 笔记中提取关键字段，交给 LLM 做跨文献推理，
生成面向 LLM4Kernel/CUDA 排序研究的可行创新方向。
"""

import re
from pathlib import Path
from core import state_manager as sm
from core.config import get_domain, get_section_keys

RESEARCH_GOAL_FILE = sm.CONFIG_DIR / "02_research_goal.md"

# ── 从单篇 MD 笔记提取关键字段 ───────────────────────────────────────────────

def _build_section_patterns(keys: dict[str, str]) -> list[tuple[str, str]]:
    """根据 section key 映射动态生成正则模式列表。"""
    patterns = []
    for logical_key, header in keys.items():
        # method 段落用更严格的终止符（匹配原有行为）
        if logical_key == "method":
            pat = rf"{re.escape(header)}.*?(?=\n  - \*\*🍎|##|\Z)"
        else:
            pat = rf"{re.escape(header)}.*?(?=\n- \*\*|##|\Z)"
        patterns.append((logical_key, pat))
    return patterns


def extract_key_sections(md: str) -> dict[str, str]:
    """从一篇笔记 MD 中提取各关键字段，返回 dict。"""
    result = {}
    for key, pattern in _build_section_patterns(get_section_keys()):
        m = re.search(pattern, md, re.S | re.IGNORECASE)
        if m:
            text = m.group(0).strip()
            text = re.sub(r"\*\*|__", "", text)
            result[key] = text[:600]
    return result


def _first_heading(md: str) -> str:
    for line in md.splitlines():
        if line.strip().startswith("# "):
            return line.strip()[2:].strip()
    return "（未命名论文）"


# ── 构造综合器 Prompt ─────────────────────────────────────────────────────────
_SYNTH_SYSTEM_TEMPLATE = (
    "你是一位资深的 AI 科研导师，{domain_part}"
    "你的任务是：基于用户提供的多篇论文笔记摘要，进行跨文献的创新性推理，"
    "输出面向用户研究目标的具体可行创新方向。\n"
    "不要输出寒暄，直接输出 Markdown 格式的分析报告。"
)

def _synth_system() -> str:
    domain = get_domain()
    domain_part = f"专注于 {domain}，" if domain else ""
    return _SYNTH_SYSTEM_TEMPLATE.format(domain_part=domain_part)

def build_synthesis_prompt(selected_states: list[dict]) -> list[dict]:
    """
    selected_states: 已完成的论文状态列表（含 final_result 字段）
    返回可直接发给 LLM 的 messages。
    """
    research_goal = (RESEARCH_GOAL_FILE.read_text(encoding="utf-8")
                     if RESEARCH_GOAL_FILE.exists() else "")

    # 为每篇论文提取摘要字段
    paper_blocks = []
    for s in selected_states:
        md    = s.get("final_result", "")
        title = _first_heading(md)
        secs  = extract_key_sections(md)
        block = [f"### 📄 {title}"]
        if secs.get("motivation"):
            block.append(f"**核心痛点:** {secs['motivation']}")
        if secs.get("inspiration"):
            block.append(f"**灵感借用:** {secs['inspiration']}")
        if secs.get("limitations"):
            block.append(f"**作者承认的缺陷:** {secs['limitations']}")
        if secs.get("pit"):
            block.append(f"**可挖坑位:** {secs['pit']}")
        if secs.get("negative"):
            block.append(f"**Negative Results:** {secs['negative']}")
        if secs.get("appendix"):
            block.append(f"**附录精华:** {secs['appendix']}")
        paper_blocks.append("\n".join(block))

    papers_text = "\n\n---\n\n".join(paper_blocks)

    user_content = f"""## 我的研究目标
{research_goal}

## 已阅读论文的关键信息摘要（共 {len(selected_states)} 篇）

{papers_text}

---

## 你的任务

请基于以上所有论文的痛点、局限、坑位和负面结果，进行跨文献综合推理，严格按以下格式输出：

# 💡 跨文献 Idea 综合报告

**涵盖论文:** {len(selected_states)} 篇 | **生成日期:** {{今日日期}}

---

## 🗺️ 研究空白地图 (Research Gap Map)
*（归纳所有论文共同指向的、尚无人解决的核心问题，2-4 条）*
- **空白 1:** ...
- **空白 2:** ...

---

## 🚀 可行创新方向

### 方向 1：{{方向名称}}
- **核心 Idea:** 一句话描述这个方向在做什么
- **来源推导:** 论文A的【某局限】+ 论文B的【某方法】→ 组合产生的新思路
- **具体做法:** 3-5 步可执行的研究路径
- **预期贡献:** 相比现有工作，我们能在哪个指标/能力上超越？
- **风险点:** 这个方向最可能在哪里翻车？

（重复以上格式，输出 3-5 个方向，按可行性从高到低排序）

---

## ⚡ 立即可执行的 Next Steps（本周内可开始）
1. ...
2. ...
3. ...

---

## 🚫 避坑清单（从 Negative Results 提炼）
- **不要做:** ... （原因：论文X已证明此路不通）
- **不要做:** ...
"""

    return [
        {"role": "system", "content": _synth_system()},
        {"role": "user",   "content": user_content},
    ]
