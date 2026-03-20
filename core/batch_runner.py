"""
core/batch_runner.py
在后台线程中并行运行多篇论文的分析。
线程池是模块级全局变量，跨 Streamlit rerun 保持存活。
"""

import threading
from core import state_manager as sm
from core.pdf_parser import extract_text_from_pdf, chunk_text
from core.llm_client import analyze_paper

# stem → Thread
_threads: dict[str, threading.Thread] = {}
# stem → error message
_errors: dict[str, str] = {}
_errors_lock = threading.Lock()


def start(file_info: dict):
    """为单篇论文启动后台分析线程。
    已完成（SHA256 或 stem 匹配到 completed 状态）则直接跳过，不启动线程。
    """
    stem = file_info["stem"]

    # 先按哈希查：不管 stem 是否相同，内容一致就认为已分析
    existing = sm.find_by_sha256(file_info["sha256"])
    if existing and existing.get("status") == "completed":
        return
    # 再按 stem 查
    state = sm.load_state(stem)
    if state and state.get("status") == "completed":
        return
    # 线程已在运行
    if stem in _threads and _threads[stem].is_alive():
        return

    with _errors_lock:
        _errors.pop(stem, None)
    t = threading.Thread(target=_run, args=(file_info,), daemon=True)
    _threads[stem] = t
    t.start()


def _run(file_info: dict):
    stem = file_info["stem"]
    try:
        # 线程启动后再检查一次，防止并发重入
        existing = sm.find_by_sha256(file_info["sha256"])
        if existing and existing.get("status") == "completed":
            return
        state = sm.load_state(stem)
        if state and state.get("status") == "completed":
            return

        full_text = extract_text_from_pdf(file_info["bytes"])
        sm.save_pdf(file_info["bytes"], file_info["name"])

        if state is None or state.get("status") == "pending":
            state = sm.create_state(
                stem, file_info["name"],
                len(chunk_text(full_text)),
                sha256=file_info["sha256"],
            )
            # 保存语言设置
            if file_info.get("result_language"):
                sm.update_state(state, result_language=file_info["result_language"])

        # 消费生成器，analyze_paper 内部负责写文件 + 更新状态
        pdf_bytes = file_info.get("bytes")
        for _ in analyze_paper(state, full_text, pdf_bytes):
            pass

    except Exception as e:
        with _errors_lock:
            _errors[stem] = str(e)
        try:
            s = sm.load_state(stem)
            if s and s.get("status") not in ("completed",):
                sm.update_state(s, status="error")
        except Exception:
            pass


def is_alive(stem: str) -> bool:
    return stem in _threads and _threads[stem].is_alive()


def get_error(stem: str) -> str:
    with _errors_lock:
        return _errors.get(stem, "")


def any_alive(stems: list[str]) -> bool:
    return any(is_alive(s) for s in stems)


def count_completed(stems: list[str]) -> int:
    n = 0
    for s in stems:
        state = sm.load_state(s)
        if state and state.get("status") == "completed":
            n += 1
    return n


def paper_progress(stem: str) -> tuple[float, str]:
    """返回 (进度 0-1, 状态描述)。"""
    state = sm.load_state(stem)
    if state is None:
        return 0.0, "等待中…"
    status = state.get("status", "pending")
    err = get_error(stem)
    if err:
        return 0.0, f"❌ {err}"
    if status == "completed":
        return 1.0, "✅ 已完成"
    if status == "error":
        return 0.0, "❌ 分析出错"
    if status == "map_in_progress":
        done = len(state.get("chunk_summaries", []))
        total = max(state.get("total_chunks", 1), 1)
        return done / total * 0.8, f"提取关键信息 {done}/{total} 块…"
    if status == "streaming":
        partial_len = len(state.get("partial_result", ""))
        # 无法精确知道进度，用字节数做估算，最多到 95%
        return min(0.8 + partial_len / 20000 * 0.15, 0.95), "生成报告中…"
    if is_alive(stem):
        return 0.02, "启动中…"
    return 0.0, "等待中…"
