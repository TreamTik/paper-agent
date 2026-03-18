"""
core/paper_chat.py
构建多轮论文问答的 messages 列表。
"""

from pathlib import Path
from core.state_manager import CONFIG_DIR

RESEARCH_GOAL_FILE = CONFIG_DIR / "02_research_goal.md"

SYSTEM_PROMPT = (
    "你是一位专业的学术研究助手，熟悉以下论文内容。"
    "请根据用户的问题，结合论文内容给出准确、深入的回答。"
    "回答时请引用具体论文内容支持你的观点，使用中文回答。"
)


def build_chat_messages(
    selected_states: list[dict],
    conversation_history: list[dict],
    user_question: str,
) -> list[dict]:
    paper_snippets = []
    for i, s in enumerate(selected_states, 1):
        title = s.get("pdf_filename", s.get("stem", f"论文{i}"))
        content = s.get("final_result", "")
        paper_snippets.append(f"【论文{i}：{title}】\n{content}")

    papers_block = "\n\n---\n\n".join(paper_snippets)

    research_goal = (RESEARCH_GOAL_FILE.read_text(encoding="utf-8")
                     if RESEARCH_GOAL_FILE.exists() else "")
    goal_block = f"\n\n## 用户研究目标\n{research_goal}" if research_goal else ""

    system_content = f"{SYSTEM_PROMPT}{goal_block}\n\n以下是论文内容摘要：\n\n{papers_block}"

    messages: list[dict] = [{"role": "system", "content": system_content}]
    messages.extend(conversation_history)
    messages.append({"role": "user", "content": user_question})
    return messages
