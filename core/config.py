"""
core/config.py
集中管理用户可配置的领域描述与 section 提取键。
配置来源：
  data/config/00_domain.md        — 研究领域描述（一行）
  data/config/04_section_keys.json — section 标题 → 逻辑 key 的映射
"""

import json
from pathlib import Path
from core.state_manager import CONFIG_DIR

_DOMAIN_FILE       = CONFIG_DIR / "00_domain.md"
_SECTION_KEYS_FILE = CONFIG_DIR / "04_section_keys.json"

# 默认 section 映射（04_section_keys.json 不存在或解析失败时使用）
_DEFAULT_SECTION_KEYS: dict[str, str] = {
    "motivation":  "核心痛点",
    "method":      "硬核原理解析",
    "inspiration": "灵感借用",
    "limitations": "作者承认的缺陷",
    "pit":         "我们可以挖的坑位",
    "appendix":    "附录精华提炼",
    "negative":    "Negative Results",
}


def get_domain() -> str:
    """返回用户配置的研究领域描述（去掉 Markdown # 前缀和空行）。"""
    if _DOMAIN_FILE.exists():
        text = _DOMAIN_FILE.read_text(encoding="utf-8").strip()
        return text.lstrip("#").strip()
    return ""


def get_section_keys() -> dict[str, str]:
    """
    返回 section 标题映射：
      {"motivation": "核心痛点", "method": "硬核原理解析", ...}
    读取 04_section_keys.json，失败时返回内置默认值。
    """
    if _SECTION_KEYS_FILE.exists():
        try:
            data = json.loads(_SECTION_KEYS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception:
            pass
    return dict(_DEFAULT_SECTION_KEYS)
