"""
core/tag_organizer.py
用 LLM 将零散标签归纳为分层类目，并持久化到 data/cache/TAG_TAXONOMY.json。
"""

import json
import re
import datetime
from pathlib import Path

from core import state_manager as sm

TAXONOMY_PATH = sm.CACHE_DIR / "TAG_TAXONOMY.json"


def build_organize_prompt(tags: list[str]) -> list[dict]:
    tag_list = "、".join(f"#{t}" for t in tags)
    return [
        {
            "role": "system",
            "content": (
                "你是一个科研文献标签分类专家，擅长将细粒度标签归纳为高层次类目。"
                "请严格按要求输出 JSON，不要输出任何额外文字。"
            ),
        },
        {
            "role": "user",
            "content": f"""以下是从多篇论文中提取的原始标签（共 {len(tags)} 个）：

{tag_list}

请将这些标签归类为 4~8 个高层次一级类目。要求：
1. 每个类目用简洁的中文命名（2~6 字）
2. 所有原始标签都需归入至少一个类目
3. 允许一个标签同时属于多个类目
4. 严格输出 JSON，格式如下，不包含任何其他内容：
{{
  "类目名1": ["原始标签A", "原始标签B", ...],
  "类目名2": ["原始标签C", ...],
  ...
}}""",
        },
    ]


def parse_taxonomy(llm_output: str) -> dict | None:
    """从 LLM 输出中提取 JSON 对象，容错处理 markdown 代码块。"""
    # 去掉 ```json ... ``` 包裹
    text = re.sub(r"```(?:json)?\s*", "", llm_output).strip().rstrip("`").strip()
    # 找第一个 { 到最后一个 }
    start = text.find("{")
    end   = text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def load_taxonomy() -> dict | None:
    """返回 {"generated_at": ..., "categories": {...}} 或 None。"""
    if TAXONOMY_PATH.exists():
        try:
            return json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def save_taxonomy(categories: dict):
    data = {
        "generated_at": datetime.datetime.now().isoformat(),
        "categories": categories,
    }
    TAXONOMY_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def tags_for_categories(selected_categories: list[str], taxonomy: dict) -> list[str]:
    """将选中的类目展开为原始标签列表。"""
    cats = taxonomy.get("categories", {})
    result: list[str] = []
    for cat in selected_categories:
        result.extend(cats.get(cat, []))
    return list(set(result))
