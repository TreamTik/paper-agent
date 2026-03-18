"""
core/contradiction_detector.py
矛盾检测器：扫描多篇笔记，找出不同论文对同一问题的相互矛盾结论。
矛盾点往往正是新论文的切入口。
"""

import re
from pathlib import Path
from core.idea_synthesizer import extract_key_sections, _first_heading
from core.config import get_domain
from core.state_manager import CONFIG_DIR

RESEARCH_GOAL_FILE = CONFIG_DIR / "02_research_goal.md"

def _contradiction_system() -> str:
    domain = get_domain()
    domain_part = f"专注于 {domain}，" if domain else ""
    return (
        f"你是一位严谨的 AI 科研分析师，{domain_part}"
        "你的任务是：对给定的多篇论文摘要，进行系统性的跨文献矛盾分析，\n"
        "找出不同论文在核心观点、实验结论、方法有效性上的相互矛盾，\n"
        "并解释为何这些矛盾对研究者具有价值。\n"
        "不要输出寒暄，直接输出 Markdown 格式的矛盾分析报告。"
    )


def _extract_claims(s: dict) -> dict:
    """提取单篇笔记中的核心主张字段：方法、结论、局限、负面结果。"""
    md    = s.get("final_result", "")
    secs  = extract_key_sections(md)

    # 额外提取 Results 段落
    results = ""
    for line in md.splitlines():
        if re.search(r"最终结论|📊.*Results", line):
            idx = md.find(line)
            snippet = md[idx:idx+500]
            results = re.sub(r"\*\*|__", "", snippet)[:400]
            break

    return {
        "title":    _first_heading(md) or s.get("pdf_filename", s.get("stem", "")),
        "method":   secs.get("method", "")[:400],
        "results":  results,
        "limits":   secs.get("limitations", "")[:400],
        "negative": secs.get("negative", "")[:300],
        "pit":      secs.get("pit", "")[:300],
    }


def build_contradiction_prompt(all_states: list[dict]) -> list[dict]:
    """构造矛盾检测的完整 Prompt。"""
    research_goal = (RESEARCH_GOAL_FILE.read_text(encoding="utf-8")
                     if RESEARCH_GOAL_FILE.exists() else "")

    paper_states = [s for s in all_states if s.get("type", "paper") == "paper"]

    claim_blocks = []
    for s in paper_states:
        c = _extract_claims(s)
        block = [f"### 📄 论文：{c['title']}"]
        if c["method"]:
            block.append(f"**核心方法/主张:** {c['method']}")
        if c["results"]:
            block.append(f"**主要结论:** {c['results']}")
        if c["limits"]:
            block.append(f"**局限性:** {c['limits']}")
        if c["negative"]:
            block.append(f"**Negative Results:** {c['negative']}")
        claim_blocks.append("\n".join(block))

    papers_text = "\n\n---\n\n".join(claim_blocks)

    user_content = f"""## 我的研究目标
{research_goal}

## 各论文核心主张摘要（共 {len(paper_states)} 篇）

{papers_text}

---

## 你的任务

请仔细对比以上所有论文的核心主张与结论，寻找相互矛盾、相互质疑或相互限定的地方，严格按以下格式输出：

# ⚡ 矛盾检测报告

**分析论文数:** {len(paper_states)} 篇 | **生成日期:** {{今日日期}}

---

## 🔴 强矛盾（直接相互否定，研究价值最高）

### 矛盾 1：{{矛盾主题}}
- **论文 A 的主张:** [论文名] 认为 ...
- **论文 B 的主张:** [论文名] 认为 ...
- **矛盾本质:** 这两个结论为何不能同时成立？背后的假设差异是什么？
- **🔬 研究价值:** 这个矛盾意味着什么？我们的研究可以怎样切入来解决/解释它？
- **可能的解释:** 是实验设置不同？数据集规模不同？还是方法本质的差异导致的？

（如有更多强矛盾，继续输出，格式相同）

---

## 🟡 弱矛盾（结论存在张力，但各有适用场景）

### 矛盾 {{n}}：{{矛盾主题}}
- **论文 A:** ...
- **论文 B:** ...
- **张力所在:** ...
- **研究价值:** ...

（如有更多弱矛盾，继续输出）

---

## 🟢 表面矛盾（看似矛盾，实则互补）

- **案例:** 论文A说X，论文B说Y，但实际上它们研究的是不同的场景/粒度/指标，并不真正矛盾。
- **启示:** 这种"伪矛盾"反而说明该领域缺少统一的评估框架，这本身就是一个研究机会。

---

## 💡 矛盾驱动的研究机会总结

基于以上矛盾，我们可以：
1. **实验设计:** 设计能同时覆盖矛盾两方场景的统一 Benchmark，一次性验证哪种结论更普适
2. **理论解释:** 提出一个能统一解释表面矛盾的理论框架
3. **方法改进:** 在矛盾点上针对性改进，明确声明"在X条件下，我们的方法优于A和B"

（根据实际矛盾内容填充具体建议）
"""

    return [
        {"role": "system", "content": _contradiction_system()},
        {"role": "user",   "content": user_content},
    ]
