"""
core/ref_scout.py
从已分析 PDF 中提取参考文献，用 AI 过滤与研究目标相关的条目，
解析 arxiv ID 生成直接下载链接。

设计原则：
- 参考文献提取：纯正则，零 token
- AI 过滤：每批 50 条一次调用，输出仅编号列表，极省 token
- 结果持久化至 data/cache/REF_SCOUT_CACHE.json
"""

import re
import json
import hashlib
import datetime
import urllib.request
import urllib.error
from pathlib import Path

from core import state_manager as sm

GOAL_PATH  = sm.CONFIG_DIR / "02_research_goal.md"
CACHE_PATH = sm.CACHE_DIR / "REF_SCOUT_CACHE.json"
BATCH_SIZE = 50   # 每批 AI 调用的参考文献数量


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 一、参考文献提取（正则，零 token）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_REF_SECTION_RE = re.compile(
    r'(?:\r?\n)[ \t]*(?:References|Bibliography|参考文献|REFERENCES|BIBLIOGRAPHY)'
    r'[ \t]*(?:\r?\n|$)',
    re.IGNORECASE,
)
_SECTION_END_RE = re.compile(
    r'(?:\r?\n)[ \t]*(?:Appendix|Acknowledgment|Acknowledgements?|'
    r'About the [Aa]uthor|附录|致谢)',
    re.IGNORECASE,
)
# 编号式条目分隔：[1]、1. 或 (1) 开头的行
_ITEM_SPLIT_RE = re.compile(
    r'(?:\r?\n)[ \t]*(?:\[\d{1,3}\]|\d{1,3}[.)]\s+|\(\d{1,3}\)\s+)'
)
# 作者-年份式：新的一行以 "Lastname," 开头（大写字母开头的单词加逗号）
_AUTHOR_YEAR_SPLIT_RE = re.compile(
    r'\n(?=[A-Z][a-zA-Z\'\-]{1,30},\s)'
)


def extract_refs_from_text(text: str) -> list[str]:
    """从论文全文提取参考文献条目，返回清理后的字符串列表。"""
    # 统一换行符，方便后续正则匹配
    text = text.replace('\r\n', '\n').replace('\r', '\n')

    m = _REF_SECTION_RE.search(text)
    if not m:
        # 备用：寻找最后一个以 [1] 开头的大段（许多 PDF 丢失章节标题）
        fallback = re.search(r'\n[ \t]*\[1\][ \t]+\S', text)
        if not fallback:
            return []
        ref_text = text[fallback.start():]
    else:
        ref_text = text[m.end():]

    # 截断到下一大段
    end = _SECTION_END_RE.search(ref_text)
    if end:
        ref_text = ref_text[:end.start()]

    # 优先尝试编号式分割
    parts = _ITEM_SPLIT_RE.split(ref_text)

    # 如果编号式分割无效，尝试其他格式
    if len(parts) <= 1:
        # 双空行分割（部分 PDF 用空行隔开每条文献）
        blank_parts = re.split(r'\n\s*\n', ref_text)
        # 作者-年份式：行首 "Lastname, " 触发分割
        author_parts = _AUTHOR_YEAR_SPLIT_RE.split(ref_text)
        # 选择产生有效条目最多的方式
        parts = max(
            [parts, blank_parts, author_parts],
            key=lambda ps: sum(1 for p in ps if 20 < len(p.strip()) < 600),
        )

    results: list[str] = []
    for part in parts:
        clean = re.sub(r'\s+', ' ', part.strip())
        if 20 < len(clean) < 600:
            results.append(clean)

    return results[:200]   # 每篇最多 200 条


def extract_arxiv_id(text: str) -> str | None:
    """从参考文献字符串中提取 arxiv ID（如 2402.12345）。"""
    for pat in (
        r'arXiv[:\s]+(\d{4}\.\d{4,5})(?:v\d+)?',
        r'arxiv\.org/abs/(\d{4}\.\d{4,5})',
    ):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def collect_all_refs(states: list[dict]) -> list[dict]:
    """
    扫描所有已分析论文的 PDF，提取并去重参考文献。
    返回：[{"raw": str, "arxiv_id": str|None, "source_stems": [str]}]
    """
    from core.pdf_parser import extract_text_from_pdf

    ref_map: dict[str, dict] = {}   # 去重键 → entry

    for state in states:
        pdf_path = state.get("_pdf_path", "")
        if not pdf_path or not Path(pdf_path).exists():
            continue
        stem = state["stem"]
        try:
            pdf_bytes = open(pdf_path, "rb").read()
            text      = extract_text_from_pdf(pdf_bytes)
            refs      = extract_refs_from_text(text)
        except Exception:
            continue

        for ref in refs:
            key = ref.lower()[:80]   # 用前 80 字符作去重键
            if key in ref_map:
                if stem not in ref_map[key]["source_stems"]:
                    ref_map[key]["source_stems"].append(stem)
            else:
                ref_map[key] = {
                    "raw":          ref,
                    "arxiv_id":     extract_arxiv_id(ref),
                    "source_stems": [stem],
                }

    return list(ref_map.values())


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 二、AI 相关性过滤（批量，极省 token）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _build_filter_prompt(refs: list[dict], goal_text: str) -> list[dict]:
    numbered = "\n".join(
        f"{i+1}. {r['raw'][:180]}" for i, r in enumerate(refs)
    )
    return [
        {
            "role": "system",
            "content": (
                "你是科研文献筛选专家。根据研究目标，从参考文献列表中找出相关条目。"
                "严格只输出 JSON，格式：\n"
                '{"relevant": [编号列表], "reasons": {"编号": "一句话原因"}}\n'
                "不要输出任何其他文字。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"研究目标（摘要）：\n{goal_text[:600]}\n\n"
                f"参考文献列表：\n{numbered}\n\n"
                "请返回相关条目的编号和一句话原因："
            ),
        },
    ]


def _parse_filter_result(text: str) -> tuple[list[int], dict[str, str]]:
    text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
    s, e = text.find("{"), text.rfind("}")
    if s == -1:
        return [], {}
    try:
        data     = json.loads(text[s:e+1])
        relevant = [int(x) for x in data.get("relevant", [])]
        reasons  = {str(k): v for k, v in data.get("reasons", {}).items()}
        return relevant, reasons
    except Exception:
        return [], {}


def filter_refs_by_goal(refs: list[dict], call_once_fn) -> list[dict]:
    """
    批量过滤参考文献，返回 AI 认为相关的条目（含 reason 字段）。
    call_once_fn: (messages) -> str，使用 llm_client.call_once。
    """
    goal_text = GOAL_PATH.read_text(encoding="utf-8") if GOAL_PATH.exists() else ""
    results: list[dict] = []

    for i in range(0, len(refs), BATCH_SIZE):
        batch    = refs[i : i + BATCH_SIZE]
        messages = _build_filter_prompt(batch, goal_text)
        try:
            output          = call_once_fn(messages)
            idxs, reasons   = _parse_filter_result(output)
            for idx in idxs:
                if 1 <= idx <= len(batch):
                    entry           = dict(batch[idx - 1])
                    entry["reason"] = reasons.get(str(idx), "")
                    results.append(entry)
        except Exception:
            pass

    return results


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 三、缓存持久化
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def save_scout_cache(data: dict):
    data = dict(data)
    data["generated_at"] = datetime.datetime.now().isoformat()
    CACHE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_scout_cache() -> dict | None:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 四、每篇论文的参考文献提取缓存（data/cache/refs/{stem}.json）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def save_paper_refs(stem: str, refs: list[dict]):
    """持久化一篇论文提取到的参考文献列表（零 token，仅正则结果）。"""
    p = sm.REFS_CACHE_DIR / f"{stem}.json"
    p.write_text(
        json.dumps({"stem": stem, "refs": refs,
                    "scanned_at": datetime.datetime.now().isoformat()},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_paper_refs(stem: str) -> list[dict] | None:
    """加载缓存的参考文献列表；未缓存返回 None。"""
    p = sm.REFS_CACHE_DIR / f"{stem}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("refs")
        except Exception:
            pass
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 五、每篇论文的 AI 过滤结果缓存（data/cache/refs/{stem}_filtered.json）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _goal_hash() -> str:
    """研究目标文件内容的 SHA256 前 12 位，用于缓存失效判断。"""
    if GOAL_PATH.exists():
        return hashlib.sha256(GOAL_PATH.read_bytes()).hexdigest()[:12]
    return "no_goal"


def save_paper_filter(stem: str, filtered: list[dict], goal_hash: str):
    """持久化一篇论文的 AI 过滤结果。"""
    p = sm.REFS_CACHE_DIR / f"{stem}_filtered.json"
    p.write_text(
        json.dumps({"stem": stem, "goal_hash": goal_hash,
                    "filtered": filtered,
                    "filtered_at": datetime.datetime.now().isoformat()},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_paper_filter(stem: str) -> tuple[list[dict], str] | None:
    """
    加载缓存的 AI 过滤结果。
    返回 (filtered_refs, goal_hash) 或 None（未缓存）。
    """
    p = sm.REFS_CACHE_DIR / f"{stem}_filtered.json"
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data.get("filtered", []), data.get("goal_hash", "")
        except Exception:
            pass
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 六、已知 arxiv ID 集合（用于"已在库"检测）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_ARXIV_PAT = re.compile(r'(\d{4}\.\d{4,5})')


def get_existing_arxiv_ids() -> set[str]:
    """
    从 data/pdfs/ 和 data/inbox/ 的文件名中提取已知 arxiv ID。
    用于在结果列表中标注"已在库"或"已下载"。
    """
    ids: set[str] = set()
    for d in (sm.PDFS_DIR, sm.INBOX_DIR):
        for f in d.glob("*.pdf"):
            m = _ARXIV_PAT.search(f.name)
            if m:
                ids.add(m.group(1))
    return ids


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 七、自动下载 arxiv PDF 到 inbox/
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_scout_markdown(
    deduped_filtered: list[dict],
    existing_arxiv: set[str],
    n_source_papers: int,
    dl_results: dict | None = None,
) -> str:
    """
    生成文献追踪结果的 Markdown 报告，分三个部分：
      1. 可下载（有 arxiv ID，不在库）
      2. 已在库 / 已下载待分析
      3. 无 arxiv ID（需手动查找）
    dl_results: {"ok": int, "dup": int, "failed": [str]} 可选，下载完成后附加。
    """
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    can_dl   = [r for r in deduped_filtered
                if r.get("arxiv_id") and r["arxiv_id"] not in existing_arxiv]
    already  = [r for r in deduped_filtered
                if r.get("arxiv_id") and r["arxiv_id"] in existing_arxiv]
    no_arxiv = [r for r in deduped_filtered if not r.get("arxiv_id")]

    lines = [
        "# 文献追踪结果",
        "",
        f"**扫描来源**：{n_source_papers} 篇论文参考文献　·　"
        f"**AI 推荐**：{len(deduped_filtered)} 篇（去重后）　·　"
        f"**生成时间**：{now}",
        "",
        "---",
        "",
    ]

    # ── 可下载 ───────────────────────────────────────────────────────────────
    lines += [f"## 可下载论文（有 arXiv ID，共 {len(can_dl)} 篇）", ""]
    if can_dl:
        for r in can_dl:
            aid    = r["arxiv_id"]
            raw    = r["raw"][:200]
            reason = r.get("reason", "")
            lines.append(f"- **[{aid}](https://arxiv.org/abs/{aid})**　{raw}")
            if reason:
                lines.append(f"  - *{reason}*")
    else:
        lines.append("（无）")
    lines.append("")

    # ── 已在库 ───────────────────────────────────────────────────────────────
    lines += [f"## 已在库 / 已下载待分析（共 {len(already)} 篇）", ""]
    if already:
        for r in already:
            aid    = r["arxiv_id"]
            raw    = r["raw"][:200]
            reason = r.get("reason", "")
            lines.append(f"- ✅ **[{aid}](https://arxiv.org/abs/{aid})**　{raw}")
            if reason:
                lines.append(f"  - *{reason}*")
    else:
        lines.append("（无）")
    lines.append("")

    # ── 无 arxiv ID ──────────────────────────────────────────────────────────
    lines += [f"## 无 arXiv ID（需手动查找，共 {len(no_arxiv)} 篇）", ""]
    if no_arxiv:
        for i, r in enumerate(no_arxiv, 1):
            raw    = r["raw"][:250]
            reason = r.get("reason", "")
            lines.append(f"{i}. {raw}")
            if reason:
                lines.append(f"   - *{reason}*")
    else:
        lines.append("（无）")
    lines.append("")

    # ── 下载结果（可选）─────────────────────────────────────────────────────
    if dl_results:
        failed = dl_results.get("failed", [])
        lines += [
            "---",
            "",
            "## 下载结果",
            "",
            f"**成功**：{dl_results.get('ok', 0)} 篇　·　"
            f"**重复跳过**：{dl_results.get('dup', 0)} 篇　·　"
            f"**失败**：{len(failed)} 篇",
            "",
        ]
        if failed:
            lines += ["### 下载失败列表", ""]
            for fm in failed:
                lines.append(f"- {fm}")
            lines.append("")

    return "\n".join(lines)


def download_arxiv_pdf(arxiv_id: str) -> tuple[bool, str, str]:
    """
    将 arxiv PDF 下载到 data/inbox/{arxiv_id}.pdf。
    返回 (success, message, sha256)。
    - 文件已存在：静默跳过，返回 (True, "duplicate", sha256)
    - 下载成功：返回 (True, "ok", sha256)
    - 失败：返回 (False, error_msg, "")
    """
    dest = sm.INBOX_DIR / f"{arxiv_id}.pdf"

    if dest.exists():
        sha = hashlib.sha256(dest.read_bytes()).hexdigest()
        return True, "duplicate", sha

    url = f"https://arxiv.org/pdf/{arxiv_id}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        if len(data) < 1024:
            return False, "响应过小，可能不是有效 PDF", ""
        sha = hashlib.sha256(data).hexdigest()
        # SHA 去重：检查 inbox/ 中是否已有同内容文件
        for f in sm.INBOX_DIR.glob("*.pdf"):
            if hashlib.sha256(f.read_bytes()).hexdigest() == sha:
                return True, "duplicate", sha
        dest.write_bytes(data)
        return True, "ok", sha
    except Exception as e:
        return False, str(e), ""
