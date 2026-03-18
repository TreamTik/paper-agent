"""
core/roadmap_builder.py
阅读路线图推荐：分析已有笔记的知识覆盖，生成结构化的下一步阅读建议。
"""

import re
from pathlib import Path
from core import state_manager as sm
from core.idea_synthesizer import extract_key_sections, _first_heading
from core.config import get_domain

RESEARCH_GOAL_FILE = sm.CONFIG_DIR / "02_research_goal.md"

def _roadmap_system() -> str:
    domain = get_domain()
    domain_part = f"专注于 {domain}，" if domain else ""
    return (
        f"你是一位资深的 AI 科研导师，{domain_part}"
        "你的任务是：根据用户当前的文献阅读进展，分析知识覆盖的盲区与薄弱环节，\n"
        "生成一份清晰的、可执行的下一步阅读路线图。\n"
        "不要输出寒暄，直接输出 Markdown 格式的路线图。"
    )


def _extract_coverage(s: dict) -> dict:
    """从单篇笔记提取覆盖信息：标题、标签、方法关键词、局限领域。"""
    md    = s.get("final_result", "")
    title = _first_heading(md)
    secs  = extract_key_sections(md)

    # 提取标签
    tags = []
    for line in md.splitlines():
        if "标签" in line:
            tags = [t.lstrip("#") for t in re.findall(r"#[\w\u4e00-\u9fa5/\-]+", line)]
            break

    return {
        "title":      title,
        "tags":       tags,
        "method":     secs.get("method", "")[:300],
        "limitation": secs.get("limitations", "")[:300],
        "pit":        secs.get("pit", "")[:200],
    }


def build_roadmap_prompt(all_states: list[dict]) -> list[dict]:
    """构造阅读路线图推荐的完整 Prompt。"""
    research_goal = (RESEARCH_GOAL_FILE.read_text(encoding="utf-8")
                     if RESEARCH_GOAL_FILE.exists() else "")

    # 只取论文类条目
    paper_states = [s for s in all_states if s.get("type", "paper") == "paper"]

    # 统计所有标签
    all_tags: list[str] = []
    coverage_blocks: list[str] = []
    for s in paper_states:
        cov = _extract_coverage(s)
        all_tags.extend(cov["tags"])
        block = [f"### 📄 {cov['title']}",
                 f"**标签:** {', '.join(cov['tags']) or '—'}"]
        if cov["method"]:
            block.append(f"**方法关键词:** {cov['method']}")
        if cov["limitation"]:
            block.append(f"**局限性:** {cov['limitation']}")
        if cov["pit"]:
            block.append(f"**可挖坑位:** {cov['pit']}")
        coverage_blocks.append("\n".join(block))

    tag_freq: dict[str, int] = {}
    for t in all_tags:
        tag_freq[t] = tag_freq.get(t, 0) + 1
    covered_tags_str = "、".join(
        f"{t}（{n}篇）" for t, n in sorted(tag_freq.items(), key=lambda x: -x[1])
    ) or "（暂无）"

    papers_text = "\n\n---\n\n".join(coverage_blocks) if coverage_blocks else "（暂无已分析论文）"

    user_content = f"""## 我的研究目标
{research_goal}

## 当前文献库概况
- 已分析论文：{len(paper_states)} 篇
- 已覆盖标签：{covered_tags_str}

## 已阅读论文的覆盖详情

{papers_text}

---

## 你的任务

请分析以上文献库的知识覆盖情况，识别研究盲区，并严格按以下格式输出阅读路线图：

# 🗺️ 阅读路线图推荐

**当前进度:** 已读 {len(paper_states)} 篇 | **生成日期:** {{今日日期}}

---

## ✅ 已覆盖的知识模块
*（列出当前已有较好覆盖的主题，2-4 条）*
- **模块名称:** 覆盖情况描述，已有哪些论文支撑

---

## 🕳️ 知识盲区诊断
*（指出目前为止完全没有覆盖、但对研究目标至关重要的主题）*
- **盲区 1 [紧迫度: 高/中/低]:** 缺少什么 → 对研究的影响是什么

（列出 3-5 个盲区）

---

## 📚 推荐阅读清单

### 🔴 第一优先级：必须补课（直接支撑研究目标）
> 阅读这些方向后，你将能够回答关于研究核心的基本问题

| 方向 | 推荐论文类型 / 关键词 | 补这个的原因 |
|---|---|---|
| ... | ... | ... |

### 🟡 第二优先级：建议阅读（扩展研究视野）
> 阅读这些方向后，你的方案设计会更有说服力

| 方向 | 推荐论文类型 / 关键词 | 补这个的原因 |
|---|---|---|
| ... | ... | ... |

### 🟢 第三优先级：选读（锦上添花）
> 有时间再读，主要用于丰富 Related Work

| 方向 | 推荐论文类型 / 关键词 | 补这个的原因 |
|---|---|---|
| ... | ... | ... |

---

## 🔍 推荐搜索关键词（可直接用于 Google Scholar / Semantic Scholar）

```
第一优先级关键词组合：
- ...

第二优先级关键词组合：
- ...
```

---

## ⚡ 本周阅读计划建议
> 基于你当前知识盲区，推荐本周优先阅读的 2-3 篇论文方向

1. **最优先:** 搜索 [关键词] → 找最新 YYYY 年的综述/代表作 → 原因：...
2. ...
"""

    return [
        {"role": "system", "content": _roadmap_system()},
        {"role": "user",   "content": user_content},
    ]
