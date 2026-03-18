"""
core/index_builder.py  —  阶段 5
每次分析完成后，自动重建 data/index.md。
内容：统计概览 + 标签索引 + 按时间倒序的论文表格（含一句话核心结论）。
"""

import re
import os
import datetime
from pathlib import Path
from core import state_manager as sm

INDEX_PATH = sm.DATA_DIR / "index.md"


# ── 从 final_result 提取一句话核心结论 ───────────────────────────────────────
def _one_line_conclusion(md: str) -> str:
    """提取"最终结论"或"Results"小节第一个非空行作为一句话摘要。"""
    capture = False
    for line in md.splitlines():
        line = line.strip()
        if re.search(r"最终结论|Results|📊", line):
            capture = True
            continue
        if capture and line and not line.startswith("#") and not line.startswith("**"):
            # 去掉 markdown 粗体等符号，截取前 80 字符
            clean = re.sub(r"[*_`]", "", line)
            return clean[:100] + ("…" if len(clean) > 100 else "")
    return "—"


def _tags(md: str) -> list[str]:
    for line in md.splitlines():
        if "标签" in line:
            return [t.lstrip("#") for t in re.findall(r"#[\w\u4e00-\u9fa5/\-]+", line)]
    return []


def _make_link(title: str, note_p: str) -> str:
    """生成 Markdown 链接；路径为空或转换失败时退化为纯文本标题。"""
    if not note_p:
        return title
    try:
        rel = Path(note_p).relative_to(INDEX_PATH.parent)
        return f"[{title}]({rel.as_posix()})"
    except ValueError:
        pass
    try:
        rel_str = os.path.relpath(note_p, INDEX_PATH.parent).replace("\\", "/")
        return f"[{title}]({rel_str})"
    except ValueError:
        return title
    for line in md.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""


# ── 主函数：重建 index.md ─────────────────────────────────────────────────────
def rebuild_index() -> None:
    completed = sm.list_completed()
    if not completed:
        return

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    total = len(completed)

    # 收集所有标签及其对应论文
    tag_map: dict[str, list[dict]] = {}
    for s in completed:
        for t in _tags(s.get("final_result", "")):
            tag_map.setdefault(t, []).append(s)
    unique_tags = sorted(tag_map.keys())

    lines = [
        "# 📖 Paper Agent — 文献知识库索引",
        "",
        f"> 自动生成 · 最后更新：{now}  ",
        f"> 已分析论文 **{total}** 篇 · 涉及标签 **{len(unique_tags)}** 个",
        "",
        "---",
        "",
        "## 📊 论文列表（按分析时间倒序）",
        "",
        "| # | 论文标题 | 日期 | 标签 | 核心结论 |",
        "|---|---|---|---|---|",
    ]

    for i, s in enumerate(completed, 1):
        final  = s.get("final_result", "")
        title  = _first_heading(final) or s.get("pdf_filename", s["stem"])
        date   = s.get("created_at", "")[:10]
        tags   = " ".join(f"`#{t}`" for t in _tags(final))
        concl  = _one_line_conclusion(final)
        note_p = s.get("note_path", "")
        link   = _make_link(title, note_p)
        lines.append(f"| {i} | {link} | {date} | {tags} | {concl} |")

    lines += [
        "",
        "---",
        "",
        "## 🏷️ 标签索引",
        "",
    ]

    for tag in unique_tags:
        papers = tag_map[tag]
        lines.append(f"### #{tag}  ({len(papers)} 篇)")
        for s in papers:
            final  = s.get("final_result", "")
            title  = _first_heading(final) or s.get("pdf_filename", s["stem"])
            note_p = s.get("note_path", "")
            link   = _make_link(title, note_p)
            concl  = _one_line_conclusion(final)
            lines.append(f"- {link} — {concl}")
        lines.append("")

    INDEX_PATH.write_text("\n".join(lines), encoding="utf-8")
