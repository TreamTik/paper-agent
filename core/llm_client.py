"""
core/llm_client.py
- Map 阶段并行化（ThreadPoolExecutor）
- 流式输出时逐 delta 写文件（incremental write + flush）
- 热启动支持
"""

import os
import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from openai import OpenAI
from dotenv import load_dotenv

from core.prompt_builder import build_single_prompt, build_map_prompt, build_reduce_prompt
from core.pdf_parser import chunk_text
from core import state_manager as sm
from core.index_builder import rebuild_index

load_dotenv()

DIRECT_THRESHOLD = 24_000
MAP_WORKERS      = 4
MAX_OUTPUT_TOKENS = 16_000   # 最终报告允许的最大 token 数
MAP_OUTPUT_TOKENS = 1_500    # 单块摘要上限
# 超时设置：connect=15s，读取相邻两个 chunk 最长等待 300s
_TIMEOUT = httpx.Timeout(connect=15.0, read=300.0, write=15.0, pool=15.0)


def _get_client() -> OpenAI:
    return OpenAI(
        api_key=os.getenv("OPENAI_API_KEY", ""),
        base_url=os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1"),
        timeout=_TIMEOUT,
    )

def _model() -> str:
    return os.getenv("MODEL_NAME", "claude-sonnet-4-6")

def _note_path(stem: str) -> Path:
    date_str = datetime.date.today().isoformat()
    papers_dir = sm._notes_subdir("paper")
    return papers_dir / f"{date_str}_{stem}.md"


# ── 流式调用 ──────────────────────────────────────────────────────────────────
def stream_analysis(messages: list[dict]):
    client = _get_client()
    with client.chat.completions.create(
        model=_model(),
        messages=messages,
        stream=True,
        temperature=0.3,
        max_tokens=MAX_OUTPUT_TOKENS,
    ) as stream:
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


# ── 非流式单次调用（供 ref_scout 等轻量任务使用）─────────────────────────────
def call_once(messages: list[dict], max_tokens: int = 2000) -> str:
    """非流式调用，返回完整字符串。适合 token 消耗小的批量过滤任务。"""
    client = _get_client()
    resp = client.chat.completions.create(
        model=_model(),
        messages=messages,
        stream=False,
        temperature=0.2,
        max_tokens=max_tokens,
    )
    if not resp.choices:
        return ""
    return resp.choices[0].message.content or ""


# ── 非流式调用（Map 单块）────────────────────────────────────────────────────
def _map_one(args: tuple) -> tuple[int, str]:
    """供 ThreadPoolExecutor 调用：返回 (chunk_index, summary)。"""
    idx, chunk_text_str, total = args
    messages = build_map_prompt(chunk_text_str, idx + 1, total)
    client = _get_client()
    resp = client.chat.completions.create(
        model=_model(),
        messages=messages,
        stream=False,
        temperature=0.3,
        max_tokens=MAP_OUTPUT_TOKENS,
    )
    if not resp.choices:
        return idx, ""
    return idx, resp.choices[0].message.content or ""


# ── 对外主入口 ────────────────────────────────────────────────────────────────
def analyze_paper(state: dict, full_text: str, progress_callback=None):
    """
    yield 最终报告文本增量，同时逐 delta 写入 note 文件（incremental flush）。
    支持热启动：Map 已完成的块跳过，partial_result 恢复后继续。
    """
    chunks = chunk_text(full_text)
    total  = len(chunks)

    # 确定本次 note 文件路径（热启动时沿用已存路径）
    note_p = Path(state.get("note_path") or "")
    if not note_p.name:
        note_p = _note_path(state["stem"])
    sm.update_state(state, note_path=str(note_p))

    # ── 直接分析（短文本）────────────────────────────────────────────────────
    if len(full_text) <= DIRECT_THRESHOLD or total == 1:
        if progress_callback:
            progress_callback("正在分析…", 1, 1)
        sm.update_state(state, status="streaming")
        messages = build_single_prompt(full_text)
        yield from _stream_and_write(state, messages, note_p)
        return

    # ── Map 阶段（并行）──────────────────────────────────────────────────────
    already_done = len(state.get("chunk_summaries", []))
    summaries: list[str] = list(state.get("chunk_summaries", []))
    remaining = total - already_done

    if remaining > 0:
        sm.update_state(state, status="map_in_progress")
        completed_count = already_done

        tasks = [(i, chunks[i], total) for i in range(already_done, total)]

        # 用 dict 收集乱序结果，保证最终顺序正确
        results: dict[int, str] = {}
        with ThreadPoolExecutor(max_workers=min(MAP_WORKERS, remaining)) as pool:
            future_map = {pool.submit(_map_one, t): t[0] for t in tasks}
            for future in as_completed(future_map):
                idx, summary = future.result()
                results[idx] = summary
                completed_count += 1
                if progress_callback:
                    progress_callback(
                        f"提取关键信息 {completed_count}/{total} 块…",
                        completed_count, total + 1
                    )
                # 把已完成的连续块持久化
                summaries_so_far = list(state.get("chunk_summaries", []))
                # append all newly completed in order
                for j in range(len(summaries_so_far), total):
                    if j in results:
                        summaries_so_far.append(results[j])
                    else:
                        break
                sm.update_state(state, chunk_summaries=summaries_so_far)

        # 按顺序整理所有摘要
        summaries = list(state.get("chunk_summaries", []))
        for i in range(len(summaries), total):
            summaries.append(results.get(i, ""))
        sm.update_state(state, chunk_summaries=summaries)

    # ── Reduce 阶段 ───────────────────────────────────────────────────────────
    if progress_callback:
        progress_callback("正在生成最终报告…", total, total + 1)
    sm.update_state(state, status="streaming")
    messages = build_reduce_prompt(summaries)
    yield from _stream_and_write(state, messages, note_p)


# ── 流式写文件辅助 ────────────────────────────────────────────────────────────
def _stream_and_write(state: dict, messages: list[dict], note_p: Path):
    """
    流式调用 LLM，每个 delta 即时写入文件并 flush，yield 给 UI。

    续写逻辑：
    - 若 partial_result 非空，将其作为 assistant prefill 加入 messages，
      LLM 从中断处真正续写（而非重新生成全文）。
    - 文件若已存在则追加（"a"），不存在则写入已有 partial 后追加（"w"）。
    - 不再 yield partial：app.py 的 accumulated 已初始化为 partial_result，
      避免重复显示。
    """
    partial = state.get("partial_result", "")
    accumulated = partial

    if partial:
        # 让 LLM 从 partial 末尾续写，而非重新生成
        messages = messages + [{"role": "assistant", "content": partial}]
        if note_p.exists():
            # 文件完好：直接追加新 delta
            file_mode = "w_partial_skip"   # 特殊标记，见下方处理
        else:
            # 文件丢失：先把已有 partial 写进去，再追加
            file_mode = "w_partial_restore"
    else:
        file_mode = "w"

    if file_mode == "w_partial_skip":
        # 追加新内容到已有文件
        with open(note_p, "a", encoding="utf-8") as nf:
            for delta in stream_analysis(messages):
                accumulated += delta
                nf.write(delta)
                nf.flush()
                if len(accumulated) % 300 < len(delta) + 1:
                    sm.update_state(state, partial_result=accumulated)
                yield delta
    else:
        # "w" 或 "w_partial_restore"：从头建文件
        with open(note_p, "w", encoding="utf-8") as nf:
            if file_mode == "w_partial_restore":
                nf.write(partial)
                nf.flush()
            for delta in stream_analysis(messages):
                accumulated += delta
                nf.write(delta)
                nf.flush()
                if len(accumulated) % 300 < len(delta) + 1:
                    sm.update_state(state, partial_result=accumulated)
                yield delta

    # 全部完成
    sm.update_state(
        state,
        status="completed",
        final_result=accumulated,
        partial_result="",
        note_path=str(note_p),
    )
    # 阶段 5：重建全局索引
    try:
        rebuild_index()
    except Exception:
        pass  # 索引构建失败不影响主流程
