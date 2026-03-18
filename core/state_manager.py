"""
core/state_manager.py
管理每篇论文的分析状态，支持热启动（断点续传）。
状态文件保存在 data/cache/{stem}.json
"""

import json
import hashlib
import datetime
from pathlib import Path

DATA_DIR               = Path(__file__).parent.parent / "data"
CONFIG_DIR             = DATA_DIR / "config"        # 用户个性化配置文件
CACHE_DIR              = DATA_DIR / "cache"         # 分析状态 JSON 与参考文献缓存
PDFS_DIR               = DATA_DIR / "pdfs"
NOTES_DIR              = DATA_DIR / "notes"
REFS_CACHE_DIR         = CACHE_DIR / "refs"         # 每篇论文的参考文献提取/过滤缓存
INBOX_DIR              = DATA_DIR / "inbox"         # AI 推荐后自动下载的待分析 PDF
SCOUT_GLOBAL_CACHE     = CACHE_DIR / "REF_SCOUT_CACHE.json"  # 全局兼容缓存

for _d in (CONFIG_DIR, CACHE_DIR, PDFS_DIR, NOTES_DIR, REFS_CACHE_DIR, INBOX_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# ── 状态结构 ─────────────────────────────────────────────────────────────────
# status 取值：
#   "pending"           — 刚创建，尚未开始
#   "map_in_progress"   — Map 阶段进行中
#   "reduce_pending"    — Map 已全部完成，等待 Reduce
#   "streaming"         — Reduce/直接分析流式输出中
#   "completed"         — 全部完成

_SUBDIR_MAP = {
    "paper":         "papers",
    "synthesis":     "ideas",
    "roadmap":       "maps",
    "contradiction": "contradictions",
    "chat":          "chats",
    "scout":         "scout",
}

def _notes_subdir(note_type: str) -> Path:
    subdir = NOTES_DIR / _SUBDIR_MAP.get(note_type, note_type)
    subdir.mkdir(parents=True, exist_ok=True)
    return subdir


def _state_path(stem: str) -> Path:
    return CACHE_DIR / f"{stem}.json"


def load_state(stem: str) -> dict | None:
    p = _state_path(stem)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def save_state(state: dict):
    p = _state_path(state["stem"])
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def create_state(stem: str, pdf_filename: str, total_chunks: int,
                 entry_type: str = "paper", sha256: str = "") -> dict:
    state = {
        "stem": stem,
        "pdf_filename": pdf_filename,
        "sha256": sha256,            # PDF 内容哈希，空字符串表示旧数据未记录
        "type": entry_type,
        "created_at": datetime.datetime.now().isoformat(),
        "status": "pending",
        "total_chunks": total_chunks,
        "chunk_summaries": [],
        "partial_result": "",
        "final_result": "",
        "note_path": "",
        "read_status": "未读",       # 阅读状态：未读/在读/已读/精读
        "star_rating": 0,            # 评分：0-5
    }
    save_state(state)
    return state


def create_synthesis_state(stem: str, source_titles: list[str],
                            result: str, note_path: str) -> dict:
    """直接创建一条已完成的综合报告状态条目。"""
    state = {
        "stem": stem,
        "pdf_filename": f"💡 Idea 综合（{len(source_titles)} 篇）",
        "type": "synthesis",
        "source_titles": source_titles,
        "created_at": datetime.datetime.now().isoformat(),
        "status": "completed",
        "total_chunks": 0,
        "chunk_summaries": [],
        "partial_result": "",
        "final_result": result,
        "note_path": note_path,
    }
    save_state(state)
    return state


def create_chat_state(stem: str, selected_paper_stems: list[str],
                      note_path: str = "") -> dict:
    """创建一条进行中的多轮对话状态条目。"""
    state = {
        "stem": stem,
        "pdf_filename": f"💬 对话（{len(selected_paper_stems)} 篇论文）",
        "type": "chat",
        "selected_papers": selected_paper_stems,
        "conversation": [],
        "created_at": datetime.datetime.now().isoformat(),
        "status": "in_progress",
        "total_chunks": 0,
        "chunk_summaries": [],
        "partial_result": "",
        "final_result": "",
        "note_path": note_path,
    }
    save_state(state)
    return state


def delete_state(stem: str, delete_note: bool = True):
    """删除状态文件及所有关联文件：MD 笔记、原始 PDF、参考文献缓存。"""
    state = load_state(stem)
    if state:
        # MD 笔记
        if delete_note:
            note_path = Path(state.get("note_path", ""))
            if note_path.exists():
                try:
                    note_path.unlink()
                except Exception:
                    pass
        # 原始 PDF
        pdf_filename = state.get("pdf_filename", "")
        if pdf_filename:
            pdf_path = PDFS_DIR / pdf_filename
            if pdf_path.exists():
                try:
                    pdf_path.unlink()
                except Exception:
                    pass
        # 参考文献提取缓存 & AI 过滤缓存
        for suffix in ("", "_filtered"):
            ref_cache = REFS_CACHE_DIR / f"{stem}{suffix}.json"
            if ref_cache.exists():
                try:
                    ref_cache.unlink()
                except Exception:
                    pass
        # 全局 scout 缓存（兼容旧版）含有该论文数据，直接删除使其失效
        if SCOUT_GLOBAL_CACHE.exists():
            try:
                SCOUT_GLOBAL_CACHE.unlink()
            except Exception:
                pass
    # 状态文件本身
    p = _state_path(stem)
    if p.exists():
        p.unlink()


def update_state(state: dict, **kwargs):
    state.update(kwargs)
    save_state(state)


# ── SHA256 工具 ───────────────────────────────────────────────────────────────
def compute_sha256(pdf_bytes: bytes) -> str:
    return hashlib.sha256(pdf_bytes).hexdigest()


def find_by_sha256(sha256: str) -> dict | None:
    """扫描所有状态文件，返回哈希匹配的第一条（无论 status）。"""
    for p in CACHE_DIR.glob("*.json"):
        try:
            state = json.loads(p.read_text(encoding="utf-8"))
            if state.get("sha256") == sha256 and state.get("type", "paper") == "paper":
                return state
        except Exception:
            pass
    return None


# ── PDF 永久保存 ──────────────────────────────────────────────────────────────
def save_pdf(pdf_bytes: bytes, filename: str) -> Path:
    dest = PDFS_DIR / filename
    dest.write_bytes(pdf_bytes)
    return dest


# ── 报告 Markdown 保存 ────────────────────────────────────────────────────────
def save_note(stem: str, content: str) -> Path:
    date_str = datetime.date.today().isoformat()
    dest = NOTES_DIR / f"{date_str}_{stem}.md"
    dest.write_text(content, encoding="utf-8")
    return dest


# ── 读取全部已完成的分析（供 Library 页面展示）────────────────────────────────
def list_completed() -> list[dict]:
    """
    返回所有 status==completed 的状态，按创建时间倒序排列。
    每条包含: stem, pdf_filename, created_at, note_path, final_result(摘要)
    """
    results = []
    for p in CACHE_DIR.glob("*.json"):
        try:
            state = json.loads(p.read_text(encoding="utf-8"))
            if state.get("status") == "completed":
                results.append(state)
        except Exception:
            pass
    results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return results


def get_note_content(note_path: str) -> str:
    p = Path(note_path)
    if p.exists():
        return p.read_text(encoding="utf-8")
    return "（报告文件未找到）"


# ── 读取中断状态（用于自动恢复提示）─────────────────────────────────────────
RESUMABLE = {"pending", "map_in_progress", "streaming"}

def list_interrupted() -> list[dict]:
    """返回所有未完成且有对应 PDF 文件的状态（可恢复的任务）。"""
    results = []
    for p in CACHE_DIR.glob("*.json"):
        try:
            state = json.loads(p.read_text(encoding="utf-8"))
            if state.get("status") not in RESUMABLE:
                continue
            # 必须有 PDF 才能恢复
            pdf_path = PDFS_DIR / state.get("pdf_filename", "")
            if pdf_path.exists():
                state["_pdf_path"] = str(pdf_path)
                results.append(state)
        except Exception:
            pass
    results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return results
