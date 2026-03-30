"""
app.py — 带 Web UI 的目标导向文献阅读助手
UI: 浅色主题 · 侧边栏=文献库+标签云 · 主区=上传+报告
"""

import os
import sys
import subprocess

import re
import json
import streamlit as st
from pathlib import Path
from core.pdf_parser import extract_text_from_pdf, chunk_text, get_pdf_info
from core.llm_client import analyze_paper, stream_analysis, call_once
from core import state_manager as sm
from core import batch_runner
from core.idea_synthesizer import build_synthesis_prompt
from core.roadmap_builder import build_roadmap_prompt
from core.contradiction_detector import build_contradiction_prompt
from core.paper_chat import build_chat_messages
from core.tag_organizer import (build_organize_prompt, parse_taxonomy,
                                 load_taxonomy, save_taxonomy, tags_for_categories)
from core.ref_scout import (collect_all_refs, filter_refs_by_goal,
                             save_scout_cache, load_scout_cache,
                             save_paper_refs, load_paper_refs,
                             save_paper_filter, load_paper_filter,
                             _goal_hash, get_existing_arxiv_ids,
                             download_arxiv_pdf, build_scout_markdown,
                             BATCH_SIZE,
                             _build_filter_prompt, _parse_filter_result, GOAL_PATH)


def _resume_from_pdf(state: dict) -> tuple[str, str]:
    pdf_path = state.get("_pdf_path", "")
    filename = state.get("pdf_filename", state["stem"])
    if pdf_path:
        pdf_bytes = open(pdf_path, "rb").read()
        return extract_text_from_pdf(pdf_bytes), filename
    return "", filename


st.set_page_config(
    page_title="Paper Agent",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
/* 隐藏无用元素 */
#MainMenu, footer { visibility: hidden; }
[data-testid="stStatusWidget"] { display: none !important; }
[data-testid="stSidebar"] { min-width: 290px; max-width: 330px; }

/* 1. 让所有主按钮文字靠左、自然换行，回归原生排版 */
[data-testid="stSidebar"] .stButton > button {
    text-align: left !important;
    white-space: pre-wrap !important;
    line-height: 1.4 !important;
    padding: 0.5rem 0.6rem !important;
    border-radius: 8px !important;
    min-height: 2.8rem;
}

/* 2. 把右侧的 "⋮" 菜单按钮变成极简的透明按钮，去除边框 */
[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] > [data-testid="column"]:nth-child(2) .stButton > button {
    text-align: center !important;
    padding: 0 !important;
    background: transparent !important; /* 透明背景 */
    border: none !important;            /* 无边框 */
    box-shadow: none !important;
    font-weight: bold;
    font-size: 1.4rem !important;       /* 稍微调大三个点 */
    color: #888;                        /* 低调的灰色 */
}

/* 鼠标悬浮在 "⋮" 上时稍微加深背景 */
[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] > [data-testid="column"]:nth-child(2) .stButton > button:hover {
    background: rgba(0,0,0,0.05) !important;
    color: #333;
}

/* 拖放上传区域：增大、高亮，方便从文件管理器直接拖入 PDF */
[data-testid="stFileUploaderDropzone"] {
    min-height: 160px !important;
    border: 2px dashed #4A90D9 !important;
    border-radius: 12px !important;
    background: linear-gradient(135deg, #f8f9ff 0%, #eef2ff 100%) !important;
    transition: background 0.2s, border-color 0.2s;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
}
[data-testid="stFileUploaderDropzone"]:hover,
[data-testid="stFileUploaderDropzone"]:focus-within {
    background: linear-gradient(135deg, #eef2ff 0%, #dde4ff 100%) !important;
    border-color: #2563EB !important;
}
</style>
""", unsafe_allow_html=True)

# 导入 components 用于回到顶部按钮
import streamlit.components.v1 as components

# ═══════════════════════════════════════════════════════════════════════════════
# 回到顶部浮动按钮（所有页面显示）
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<style>
#back-to-top {
    position: fixed;
    bottom: 30px;
    right: 30px;
    width: 50px;
    height: 50px;
    border-radius: 50%;
    background: #4A90D9;
    color: white;
    border: none;
    box-shadow: 0 2px 12px rgba(74, 144, 217, 0.4);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 20px;
    opacity: 0.9;
    transition: all 0.3s ease;
    z-index: 9999;
}
#back-to-top:hover {
    opacity: 1;
    transform: translateY(-3px);
    box-shadow: 0 4px 16px rgba(74, 144, 217, 0.5);
}
</style>
<button id="back-to-top" title="回到顶部"><i class="fas fa-arrow-up"></i></button>
""", unsafe_allow_html=True)

# 使用 components 执行 JavaScript（设置小高度确保渲染）
components.html("""
<script>
(function() {
    const btn = parent.document.getElementById('back-to-top');
    if (btn) {
        btn.addEventListener('click', function() {
            const main = parent.document.querySelector('.stMain');
            if (main) {
                main.scrollTo({ top: 0, behavior: 'smooth' });
            }
        });
    }
})();
</script>
""", height=1)


# ── 工具函数 ──────────────────────────────────────────────────────────────────
def format_size(n: int) -> str:
    if n < 1024: return f"{n} B"
    if n < 1024**2: return f"{n/1024:.1f} KB"
    return f"{n/1024**2:.1f} MB"

def safe_stem(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", Path(name).stem)[:80].strip()

def _open_pdf(path: str):
    """用系统默认 PDF 阅读器打开文件。"""
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.run(["open", path], check=False)
    else:
        subprocess.run(["xdg-open", path], check=False)

def _first_heading(md: str) -> str:
    for line in md.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""

def _tags(md: str) -> list[str]:
    for line in md.splitlines():
        if "标签" in line:
            return [t.lstrip("#") for t in re.findall(r"#[\w\u4e00-\u9fa5/\-]+", line)]
    return []


READ_STATUSES = ["未读", "在读", "已读", "精读"]
_STATUS_ICON  = {"未读": "", "在读": "📖", "已读": "✅", "精读": "🔥"}
_STAR_CHARS   = ["☆", "⭐", "⭐⭐", "⭐⭐⭐", "⭐⭐⭐⭐", "🌟🌟🌟🌟🌟"]


def _star_str(n: int) -> str:
    """将 0-5 的评分转为字符串，0 返回空串。"""
    if n <= 0:
        return ""
    if n == 5:
        return "🌟🌟🌟🌟🌟"
    return "⭐" * min(n, 5)


def _cat_paper_selector(papers: list[dict], key: str) -> tuple[list[str], dict]:
    """
    带一级类目快速预选 + 复选框列表的论文选择器。
    返回 (chosen_titles, title→state 映射)。
    papers 只传 type==paper 的列表。
    """
    taxonomy = load_taxonomy()
    cats = taxonomy.get("categories", {}) if taxonomy else {}

    # title→state 映射（保证顺序）
    options: dict[str, dict] = {}
    for s in papers:
        title = _first_heading(s.get("final_result", "")) or s.get("pdf_filename", s["stem"])
        options[title] = s

    # ── 一级类目快速预选 ──────────────────────────────────────────────────────
    if cats:
        chosen_cat = st.selectbox(
            "📂 按类目快速预选", options=["全部"] + list(cats.keys()),
            key=f"{key}_cat",
            help="选一个类目，下方自动勾选该类目的论文；可再手动调整",
        )
    else:
        chosen_cat = "全部"

    # 类目变化时重置复选框状态
    cat_init_key = f"{key}_cat_init"
    if st.session_state.get(cat_init_key) != chosen_cat:
        if chosen_cat != "全部" and cats:
            sub_tags = {t.lstrip("#") for t in cats.get(chosen_cat, [])}
            for s in papers:
                checked = any(tag in sub_tags
                              for tag in _tags(s.get("final_result", "")))
                st.session_state[f"{key}_cb_{s['stem']}"] = checked
        else:
            for s in papers:
                st.session_state[f"{key}_cb_{s['stem']}"] = True
        st.session_state[cat_init_key] = chosen_cat

    # ── 全选 / 取消全选 + 计数 ─────────────────────────────────────────────
    c1, c2, c3 = st.columns([1, 1.5, 5])
    with c1:
        if st.button("☑ 全选", key=f"{key}_selall"):
            for s in papers:
                st.session_state[f"{key}_cb_{s['stem']}"] = True
            st.rerun()
    with c2:
        if st.button("☐ 取消全选", key=f"{key}_deselall"):
            for s in papers:
                st.session_state[f"{key}_cb_{s['stem']}"] = False
            st.rerun()
    n_checked = sum(
        1 for s in papers
        if st.session_state.get(f"{key}_cb_{s['stem']}", True)
    )
    with c3:
        st.caption(f"已选 **{n_checked}** / {len(papers)} 篇")

    # ── 复选框列表（可滚动容器，显示完整标题）────────────────────────────────
    chosen_states: list[dict] = []
    with st.container(height=320):
        for s in papers:
            stem  = s["stem"]
            title = _first_heading(s.get("final_result", "")) or s.get("pdf_filename", s["stem"])
            cb_key = f"{key}_cb_{stem}"
            if cb_key not in st.session_state:
                st.session_state[cb_key] = True
            if st.checkbox(title, key=cb_key):
                chosen_states.append(s)

    chosen_titles = [
        _first_heading(s.get("final_result", "")) or s.get("pdf_filename", s["stem"])
        for s in chosen_states
    ]
    return chosen_titles, options


def _display_name(s: dict) -> str:
    """优先返回用户自定义名称，否则按类型生成默认名。"""
    if s.get("custom_name"):
        return s["custom_name"]
    t = s.get("type", "paper")
    if t == "paper":
        return _first_heading(s.get("final_result", "")) or s.get("pdf_filename", s["stem"])
    if t == "synthesis":
        return f"综合报告（{len(s.get('source_titles', []))} 篇）"
    if t == "roadmap":
        return "阅读路线图"
    if t == "contradiction":
        return f"矛盾检测（{len(s.get('source_titles', []))} 篇）"
    if t == "chat":
        return f"对话（{len(s.get('selected_papers', []))} 篇）"
    return s.get("pdf_filename", s["stem"])


def _sidebar_item(s: dict, label: str, active: bool,
                  on_click_keys: dict,
                  item_key: str, del_key: str, ren_key: str,
                  on_delete=None, extra_clear: list | None = None,
                  show_meta_edit: bool = False):
    """渲染侧边栏单条目：主按钮 + ⋮ 弹出式管理菜单（重命名/删除/评分）"""
    stem = s["stem"]

    # 原生列布局：主按钮占大头，右侧留一点点给菜单按钮
    c_main, c_menu = st.columns([8.5, 1.5], vertical_alignment="center")

    with c_main:
        # 主按钮
        if st.button(label, key=item_key, use_container_width=True):
            for k, v in on_click_keys.items():
                if v is None:
                    st.session_state.pop(k, None)
                else:
                    st.session_state[k] = v
            for k in (extra_clear or []):
                st.session_state.pop(k, None)
            st.rerun()

    with c_menu:
        # 现代 UI 魔法：原生弹出菜单
        with st.popover("⋮", use_container_width=True):
            st.markdown("**管理文献**")

            # 1. 重命名模块
            cur_name = s.get("custom_name") or _display_name(s)
            new_name = st.text_input("重命名为", value=cur_name, key=f"ri_{stem}", label_visibility="collapsed")
            if st.button("💾 保存修改", key=f"rk_{stem}", use_container_width=True):
                loaded = sm.load_state(stem)
                if loaded:
                    sm.update_state(loaded, custom_name=new_name.strip())
                st.rerun()

            # 2. 阅读状态 + 评分（仅论文类型）
            if show_meta_edit:
                st.divider()
                cur_status = s.get("read_status", "未读")
                status_idx = READ_STATUSES.index(cur_status) if cur_status in READ_STATUSES else 0
                new_status = st.selectbox("阅读状态", READ_STATUSES, index=status_idx,
                                          key=f"rs_{stem}")
                cur_stars = s.get("star_rating", 0)
                new_stars = st.selectbox(
                    "评分", options=list(range(6)), index=cur_stars,
                    format_func=lambda x: "☆ 未评分" if x == 0 else ("🌟🌟🌟🌟🌟" if x == 5 else "⭐" * x),
                    key=f"sr_{stem}",
                )
                if st.button("💾 保存状态", key=f"sm_{stem}", use_container_width=True):
                    loaded = sm.load_state(stem)
                    if loaded:
                        sm.update_state(loaded, read_status=new_status, star_rating=new_stars)
                    st.rerun()

            st.divider()

            # 3. 删除模块
            if st.button("🗑️ 确认删除", key=f"rx_{stem}", type="primary", use_container_width=True):
                sm.delete_state(stem)
                if on_delete:
                    on_delete()
                st.rerun()


# ── 侧边栏：文献库 ────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 📚 文献库")
    all_completed = sm.list_completed()

    if not all_completed:
        st.caption("暂无已分析的论文，请上传 PDF 开始分析。")
    else:
        all_tags: list[str] = []
        for s in all_completed:
            all_tags.extend(_tags(s.get("final_result", "")))
        unique_tags = sorted(set(all_tags))

        taxonomy = load_taxonomy()
        selected_tags: list[str] = []

        # ── 一级类目导航（radio 单击即联动）─────────────────────────────────
        if unique_tags:
            cats = taxonomy.get("categories", {}) if taxonomy else {}

            if cats:
                st.markdown("**🏷️ 分类浏览**")
                cat_options = ["全部"] + list(cats.keys())
                chosen_cat = st.radio(
                    "类目", options=cat_options,
                    label_visibility="collapsed",
                    key="tag_cat_radio",
                )
                active_cat = None if chosen_cat == "全部" else chosen_cat

                # ── 二级子标签（选中类目后展开）────────────────────────────
                if active_cat:
                    sub_tags = cats[active_cat]
                    chosen_subs = st.multiselect(
                        "细分", options=sub_tags,
                        label_visibility="collapsed",
                        placeholder=f"显示「{active_cat}」全部文献，可进一步细分…",
                        key="tag_sub_multi",
                    )
                    selected_tags = chosen_subs if chosen_subs else list(sub_tags)

                # 整理入口（折叠在 expander 里，不占主视线）
                with st.expander("⚙️ 标签管理", expanded=False):
                    gen_date = taxonomy.get("generated_at", "")[:10] if taxonomy else ""
                    st.caption(f"{len(cats)} 个类目 · {len(unique_tags)} 个标签 · {gen_date}")
                    if st.button("🔄 重新整理标签", use_container_width=True, key="btn_retag"):
                        st.session_state["run_tag_organize"] = True
                        st.rerun()

            else:
                # 尚未归类：平铺标签 + 整理入口
                st.markdown("**🏷️ 标签筛选**")
                selected_tags = st.multiselect(
                    "选择标签", options=unique_tags,
                    label_visibility="collapsed", placeholder="选择标签…",
                )
                if len(unique_tags) >= 5:
                    st.caption("标签较多？可以整理成分类")
                if st.button("✨ 整理标签分类", use_container_width=True, key="btn_tag"):
                    st.session_state["run_tag_organize"] = True
                    st.rerun()

            st.divider()

        # 执行标签整理
        if st.session_state.get("run_tag_organize") and unique_tags:
            with st.spinner("AI 正在归纳标签类目，请稍候…"):
                try:
                    messages = build_organize_prompt(unique_tags)
                    chunks   = list(stream_analysis(messages))
                    result   = "".join(chunks)
                    cats     = parse_taxonomy(result)
                    if cats:
                        save_taxonomy(cats)
                        st.toast(f"✅ 标签已归纳为 {len(cats)} 个类目", icon="🏷️")
                    else:
                        st.error("❌ LLM 返回格式解析失败，请重试")
                except Exception as e:
                    st.error(f"❌ 整理失败：{e}")
            st.session_state.pop("run_tag_organize", None)
            st.rerun()

        # ── 搜索框（在分类过滤结果里再搜）──────────────────────────────────
        query = st.text_input("🔍 搜索", placeholder="按标题搜索…",
                              label_visibility="collapsed")

        # ── 高级筛选（阅读状态 + 最低评分）──────────────────────────────────
        with st.expander("🔽 筛选（状态 · 评分）", expanded=False):
            filter_statuses = st.multiselect(
                "阅读状态", options=READ_STATUSES, default=[],
                placeholder="不限状态…", key="filter_read_status",
            )
            min_stars = st.selectbox(
                "最低评分", options=list(range(6)), index=0,
                format_func=lambda x: "不限" if x == 0 else ("🌟🌟🌟🌟🌟 及以上" if x == 5 else "⭐" * x + " 及以上"),
                key="filter_min_stars",
            )
        filter_statuses = st.session_state.get("filter_read_status", [])
        min_stars       = st.session_state.get("filter_min_stars", 0)

        # 论文 vs 复合报告 分开处理
        all_papers   = [s for s in all_completed if s.get("type", "paper") == "paper"]
        non_papers   = [s for s in all_completed if s.get("type", "paper") != "paper"]

        # 标签过滤：只作用于论文，综合报告/路线图/矛盾/对话始终显示
        display_papers = all_papers
        if selected_tags:
            norm_selected = {t.lstrip("#") for t in selected_tags}
            display_papers = [s for s in all_papers
                              if any(t in norm_selected
                                     for t in _tags(s.get("final_result", "")))]

        # 阅读状态过滤
        if filter_statuses:
            display_papers = [s for s in display_papers
                              if s.get("read_status", "未读") in filter_statuses]

        # 最低评分过滤
        if min_stars and min_stars > 0:
            display_papers = [s for s in display_papers
                              if s.get("star_rating", 0) >= min_stars]

        # 搜索过滤：仅按标题匹配
        if query:
            q = query.lower()
            display_papers = [s for s in display_papers
                              if q in s.get("custom_name", "").lower()
                              or q in (_first_heading(s.get("final_result", "")) or "").lower()
                              or q in s.get("pdf_filename", "").lower()]
            non_papers = [s for s in non_papers
                          if q in s.get("custom_name", "").lower()
                          or q in _display_name(s).lower()]

        st.caption(f"{len(display_papers)} / {len(all_papers)} 篇论文")
        st.divider()

        selected = st.session_state.get("selected_stem")

        # 论文与综合报告分组显示
        papers        = display_papers
        syntheses     = [s for s in non_papers if s.get("type") == "synthesis"]
        roadmaps      = [s for s in non_papers if s.get("type") == "roadmap"]
        contradictions= [s for s in non_papers if s.get("type") == "contradiction"]

        # ── 论文列表（分页）──────────────────────────────────────────────────
        PAGE_SIZE = 10
        total_pages = max(1, (len(papers) + PAGE_SIZE - 1) // PAGE_SIZE)
        if selected and st.session_state.get("_lib_page_stem") != selected:
            for _pi, _ps in enumerate(papers):
                if _ps["stem"] == selected:
                    st.session_state["lib_page"] = _pi // PAGE_SIZE
                    st.session_state["_lib_page_stem"] = selected
                    break
        lib_page = min(st.session_state.get("lib_page", 0), total_pages - 1)
        page_papers = papers[lib_page * PAGE_SIZE:(lib_page + 1) * PAGE_SIZE]

        for s in page_papers:
            stem  = s["stem"]
            date  = s.get("created_at", "")[:10]
            title = _display_name(s)
            short = title[:50] + ("…" if len(title) > 50 else "")
            icon  = "▶ " if stem == selected else ""
            stars = _star_str(s.get("star_rating", 0))
            st_icon = _STATUS_ICON.get(s.get("read_status", "未读"), "")
            meta  = f" {stars}" if stars else ""
            meta += f" {st_icon}" if st_icon else ""
            def _paper_del(stem=stem):
                if st.session_state.get("selected_stem") == stem:
                    st.session_state.pop("selected_stem", None)
            _sidebar_item(
                s, f"{icon}📄 {short}{meta}",
                active=(stem == selected),
                on_click_keys={"selected_stem": stem, "trigger_stem": None,
                               "trigger_text": None, "idea_mode": None},
                item_key=f"lib_{stem}", del_key=f"del_{stem}", ren_key=f"ren_{stem}",
                on_delete=_paper_del,
                show_meta_edit=True,
            )

        if total_pages > 1:
            col_prev, col_info, col_next = st.columns([1, 3, 1])
            with col_prev:
                if st.button("◀", key="lib_prev", disabled=(lib_page == 0)):
                    st.session_state["lib_page"] = lib_page - 1
                    st.rerun()
            with col_info:
                st.caption(f"{lib_page + 1} / {total_pages} 页")
            with col_next:
                if st.button("▶", key="lib_next", disabled=(lib_page >= total_pages - 1)):
                    st.session_state["lib_page"] = lib_page + 1
                    st.rerun()

        if syntheses:
            st.caption("── Idea 综合报告 ──")
            for s in syntheses:
                stem  = s["stem"]
                date  = s.get("created_at", "")[:10]
                title = _display_name(s)
                short = title[:50] + ("…" if len(title) > 50 else "")
                icon  = "▶ " if stem == selected else ""
                def _syn_del(stem=stem):
                    if st.session_state.get("selected_stem") == stem:
                        st.session_state.pop("selected_stem", None)
                _sidebar_item(
                    s, f"{icon}💡 {short}",
                    active=(stem == selected),
                    on_click_keys={"selected_stem": stem, "trigger_stem": None, "idea_mode": None},
                    item_key=f"lib_{stem}", del_key=f"del_{stem}", ren_key=f"ren_{stem}",
                    on_delete=_syn_del,
                )

        if roadmaps:
            st.caption("── 阅读路线图 ──")
            for s in roadmaps:
                stem  = s["stem"]
                date  = s.get("created_at", "")[:10]
                title = _display_name(s)
                short = title[:50] + ("…" if len(title) > 50 else "")
                icon  = "▶ " if stem == selected else ""
                def _rm_del(stem=stem):
                    if st.session_state.get("selected_stem") == stem:
                        st.session_state.pop("selected_stem", None)
                _sidebar_item(
                    s, f"{icon}🗺️ {short}",
                    active=(stem == selected),
                    on_click_keys={"selected_stem": stem, "trigger_stem": None, "roadmap_mode": None},
                    item_key=f"lib_{stem}", del_key=f"del_{stem}", ren_key=f"ren_{stem}",
                    on_delete=_rm_del,
                )

        if contradictions:
            st.caption("── 矛盾检测报告 ──")
            for s in contradictions:
                stem  = s["stem"]
                date  = s.get("created_at", "")[:10]
                title = _display_name(s)
                short = title[:50] + ("…" if len(title) > 50 else "")
                icon  = "▶ " if stem == selected else ""
                def _con_del(stem=stem):
                    if st.session_state.get("selected_stem") == stem:
                        st.session_state.pop("selected_stem", None)
                _sidebar_item(
                    s, f"{icon}⚡ {short}",
                    active=(stem == selected),
                    on_click_keys={"selected_stem": stem, "trigger_stem": None, "contradiction_mode": None},
                    item_key=f"lib_{stem}", del_key=f"del_{stem}", ren_key=f"ren_{stem}",
                    on_delete=_con_del,
                )

        # Collect chat states (including in_progress)
        chats_all = []
        for p in sm.CACHE_DIR.glob("*.json"):
            try:
                s = json.loads(p.read_text(encoding="utf-8"))
                if s.get("type") == "chat":
                    chats_all.append(s)
            except Exception:
                pass
        chats_all.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        if chats_all:
            st.caption("── 对话记录 ──")
            active_chat = st.session_state.get("active_chat_stem")
            for s in chats_all:
                stem  = s["stem"]
                date  = s.get("created_at", "")[:10]
                title = _display_name(s)
                short = title[:50] + ("…" if len(title) > 50 else "")
                icon  = "▶ " if stem == active_chat else ""
                def _chat_del(stem=stem):
                    if st.session_state.get("active_chat_stem") == stem:
                        st.session_state["active_chat_stem"] = ""
                        st.session_state.pop("chat_paper_states", None)
                _sidebar_item(
                    s, f"{icon}💬 {short}",
                    active=(stem == active_chat),
                    on_click_keys={"chat_mode": True, "active_chat_stem": stem,
                                   "selected_stem": None, "idea_mode": None,
                                   "roadmap_mode": None, "contradiction_mode": None},
                    item_key=f"lib_{stem}", del_key=f"del_{stem}", ren_key=f"ren_{stem}",
                    on_delete=_chat_del,
                )

        # ── 工具按钮区 ────────────────────────────────────────────────────────
        st.divider()
        if st.button("💡 Idea 综合器", use_container_width=True, type="primary"):
            st.session_state["idea_mode"]          = True
            st.session_state["roadmap_mode"]       = False
            st.session_state["chat_mode"]          = False
            st.session_state["contradiction_mode"] = False
            st.session_state.pop("active_chat_stem", None)
            st.session_state.pop("selected_stem", None)
            st.session_state.pop("trigger_stem", None)
            st.rerun()
        if st.button("🗺️ 阅读路线图", use_container_width=True):
            st.session_state["roadmap_mode"]       = True
            st.session_state["idea_mode"]          = False
            st.session_state["chat_mode"]          = False
            st.session_state["contradiction_mode"] = False
            st.session_state.pop("active_chat_stem", None)
            st.session_state.pop("selected_stem", None)
            st.session_state.pop("trigger_stem", None)
            st.rerun()
        if st.button("⚡ 矛盾检测器", use_container_width=True):
            st.session_state["contradiction_mode"] = True
            st.session_state["idea_mode"]          = False
            st.session_state["roadmap_mode"]       = False
            st.session_state["chat_mode"]          = False
            st.session_state.pop("active_chat_stem", None)
            st.session_state.pop("selected_stem", None)
            st.session_state.pop("trigger_stem", None)
            st.rerun()
        if st.button("💬 论文问答", use_container_width=True):
            st.session_state["chat_mode"]          = True
            st.session_state["idea_mode"]          = False
            st.session_state["roadmap_mode"]       = False
            st.session_state["contradiction_mode"] = False
            st.session_state.pop("scout_mode", None)
            st.session_state.pop("scout_note_stem", None)
            st.session_state.pop("scout_dl_results", None)
            st.session_state.pop("active_chat_stem", None)
            st.session_state.pop("selected_stem", None)
            st.session_state.pop("trigger_stem", None)
            st.rerun()
        if st.button("🔭 文献追踪", use_container_width=True):
            st.session_state["scout_mode"]         = True
            st.session_state["idea_mode"]          = False
            st.session_state["roadmap_mode"]       = False
            st.session_state["contradiction_mode"] = False
            st.session_state["chat_mode"]          = False
            st.session_state.pop("settings_mode", None)
            st.session_state.pop("selected_stem", None)
            st.session_state.pop("trigger_stem", None)
            st.rerun()
        if st.button("⚙️ 设置", use_container_width=True):
            st.session_state["settings_mode"]      = True
            st.session_state["idea_mode"]          = False
            st.session_state["roadmap_mode"]       = False
            st.session_state["contradiction_mode"] = False
            st.session_state["chat_mode"]          = False
            st.session_state.pop("scout_mode", None)
            st.session_state.pop("selected_stem", None)
            st.session_state.pop("trigger_stem", None)
            st.rerun()

        # ═══════════════════════════════════════════════════════════════════
        # 回到首页按钮（仅非上传页面显示）
        # ═══════════════════════════════════════════════════════════════════
        is_home_page = not any([
            st.session_state.get("selected_stem"),
            st.session_state.get("idea_mode"),
            st.session_state.get("roadmap_mode"),
            st.session_state.get("contradiction_mode"),
            st.session_state.get("scout_mode"),
            st.session_state.get("chat_mode"),
            st.session_state.get("settings_mode"),
        ])
        if not is_home_page:
            st.divider()
            if st.button("🏠 回到首页", use_container_width=True, type="secondary"):
                # 清除所有模式标志，回到上传页面
                for key in ["selected_stem", "idea_mode", "roadmap_mode",
                            "contradiction_mode", "scout_mode", "chat_mode",
                            "settings_mode", "active_chat_stem", "trigger_stem"]:
                    st.session_state.pop(key, None)
                st.rerun()


# ── 主区域 ────────────────────────────────────────────────────────────────────
selected_stem = st.session_state.get("selected_stem")
trigger_stem  = st.session_state.get("trigger_stem")
idea_mode          = st.session_state.get("idea_mode", False)
roadmap_mode       = st.session_state.get("roadmap_mode", False)
contradiction_mode = st.session_state.get("contradiction_mode", False)
scout_mode         = st.session_state.get("scout_mode", False)
chat_mode          = st.session_state.get("chat_mode", False)
settings_mode      = st.session_state.get("settings_mode", False)
active_chat_stem   = st.session_state.get("active_chat_stem", "")

# ══ F1：论文问答 - 选择论文 ═══════════════════════════════════════════════════
if chat_mode and not active_chat_stem and not trigger_stem:
    st.title("💬 论文问答助手")
    st.caption("选择若干篇已分析的论文，AI 将基于论文内容回答你的问题。")
    st.divider()

    all_done   = sm.list_completed()
    papers_only = [s for s in all_done if s.get("type", "paper") == "paper"]

    if not papers_only:
        st.info("文献库为空，请先上传并分析至少一篇论文。")
        if st.button("← 返回"):
            st.session_state["chat_mode"] = False
            st.rerun()
    else:
        chosen_titles, options = _cat_paper_selector(papers_only, "chat")
        chosen_states = [options[t] for t in chosen_titles]

        col_run, col_back = st.columns([2, 8])
        with col_run:
            start_btn = st.button("🚀 开始对话", type="primary",
                                  disabled=len(chosen_states) == 0)
        with col_back:
            if st.button("← 返回"):
                st.session_state["chat_mode"] = False
                st.rerun()

        if start_btn and chosen_states:
            import datetime as _dt
            date_str = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            chat_stem = f"CHAT_{date_str}"
            note_dir  = sm._notes_subdir("chat")
            note_path = note_dir / f"{_dt.date.today().isoformat()}_{chat_stem}.md"
            # Write header
            header_lines = ["# 论文问答对话记录\n",
                            f"**论文：** {', '.join(chosen_titles)}\n",
                            f"**时间：** {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"]
            note_path.write_text("".join(header_lines), encoding="utf-8")
            selected_stems = [s["stem"] for s in chosen_states]
            sm.create_chat_state(chat_stem, selected_stems, str(note_path))
            st.session_state["active_chat_stem"] = chat_stem
            st.session_state["chat_paper_states"] = chosen_states
            st.rerun()

# ══ F2：论文问答 - 对话界面 ═══════════════════════════════════════════════════
elif chat_mode and active_chat_stem and not trigger_stem:
    import datetime as _dt
    state = sm.load_state(active_chat_stem)
    if state is None:
        st.error("对话状态未找到。")
        st.session_state["active_chat_stem"] = ""
        st.session_state["chat_mode"] = False
        st.rerun()
    else:
        # Reconstruct paper states if returning from sidebar
        chat_paper_states = st.session_state.get("chat_paper_states")
        if not chat_paper_states:
            paper_stems = state.get("selected_papers", [])
            chat_paper_states = [sm.load_state(s) for s in paper_stems]
            missing = [paper_stems[i] for i, s in enumerate(chat_paper_states) if s is None]
            chat_paper_states = [s for s in chat_paper_states if s]
            st.session_state["chat_paper_states"] = chat_paper_states
            if missing:
                st.warning(
                    f"⚠️ 对话依赖的 {len(missing)} 篇论文已被删除，回答将基于剩余论文：`{'`、`'.join(missing)}`"
                )

        n_papers = len(chat_paper_states)
        titles   = [_first_heading(s.get("final_result","")) or s.get("pdf_filename", s["stem"])
                    for s in chat_paper_states]
        st.title("💬 论文问答助手")
        st.caption("基于 " + "  ·  ".join(f"`{t[:30]}`" for t in titles[:4])
                   + (f" 等 {n_papers} 篇" if n_papers > 4 else ""))

        col_back, _ = st.columns([1, 9])
        with col_back:
            if st.button("← 新建对话"):
                st.session_state["active_chat_stem"] = ""
                st.session_state.pop("chat_paper_states", None)
                st.rerun()

        st.divider()

        # Display existing conversation
        conversation = state.get("conversation", [])
        for msg in conversation:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        note_path = Path(state.get("note_path", ""))

        # Chat input
        user_q = st.chat_input("提出你的问题…")
        if user_q:
            with st.chat_message("user"):
                st.markdown(user_q)

            with st.chat_message("assistant"):
                placeholder = st.empty()
                chunks = []
                messages = build_chat_messages(chat_paper_states, conversation, user_q)
                try:
                    for delta in stream_analysis(messages):
                        chunks.append(delta)
                        placeholder.markdown("".join(chunks))
                    answer = "".join(chunks)
                except Exception as e:
                    answer = f"（回答失败：{e}）"
                    placeholder.error(answer)

            # Append to note file
            if note_path.exists():
                with open(note_path, "a", encoding="utf-8") as nf:
                    nf.write(f"\n\n**Q:** {user_q}\n\n**A:** {answer}\n\n")

            # Update state
            conversation.append({"role": "user", "content": user_q})
            conversation.append({"role": "assistant", "content": answer})
            sm.update_state(state, conversation=conversation)
            st.rerun()

# ══ C：Idea 综合器 ════════════════════════════════════════════════════════════
elif idea_mode and not trigger_stem:
    all_done = sm.list_completed()

    st.title("💡 Idea 综合器")
    st.caption("选择若干篇已分析的论文，AI 将跨文献推理，生成面向你研究目标的可行创新方向。")
    st.divider()

    if not all_done:
        st.info("文献库为空，请先上传并分析至少一篇论文。")
    else:
        # 只用原始论文，不含综合报告/路线图等
        papers_only_syn = [s for s in all_done if s.get("type", "paper") == "paper"]
        chosen_titles, options = _cat_paper_selector(papers_only_syn, "synthesis")
        chosen_states = [options[t] for t in chosen_titles]

        col_run, col_back = st.columns([2, 8])
        with col_run:
            run_btn = st.button("🚀 开始综合", type="primary",
                                disabled=len(chosen_states) == 0)
        with col_back:
            if st.button("← 返回"):
                st.session_state.pop("idea_mode", None)
                st.rerun()

        if run_btn and chosen_states:
            st.divider()
            messages = build_synthesis_prompt(chosen_states)
            progress = st.empty()
            report   = st.empty()
            chunks   = []
            progress.caption(f"正在综合 {len(chosen_states)} 篇论文…")
            try:
                for delta in stream_analysis(messages):
                    chunks.append(delta)
                    report.markdown("".join(chunks))

                import datetime
                result    = "".join(chunks)
                date_str  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                stem      = f"SYNTHESIS_{date_str}"
                save_path = sm._notes_subdir("synthesis") / f"{datetime.date.today().isoformat()}_{stem}.md"
                save_path.write_text(result, encoding="utf-8")

                source_titles = [
                    _first_heading(s.get("final_result","")) or s.get("pdf_filename", s["stem"])
                    for s in chosen_states
                ]
                sm.create_synthesis_state(stem, source_titles, result, str(save_path))

                progress.caption("✅ 综合完成，已保存至文献库")
                st.toast("🎉 Idea 综合完成！", icon="💡")
                st.session_state["idea_mode"]      = False
                st.session_state["selected_stem"]  = stem
                st.session_state["just_completed"] = True
                st.rerun()

            except Exception as e:
                progress.empty()
                st.error(f"❌ 综合失败：{e}")

# ══ D：阅读路线图 ═════════════════════════════════════════════════════════════
elif roadmap_mode and not trigger_stem:
    st.title("🗺️ 阅读路线图推荐")
    st.caption("AI 分析当前文献库的知识覆盖与盲区，给出下一步应该读哪些论文的具体建议。")
    st.divider()

    all_done = sm.list_completed()
    papers_only = [s for s in all_done if s.get("type", "paper") == "paper"]

    if not papers_only:
        st.info("文献库中暂无已分析的论文，请先上传并分析至少一篇。")
        if st.button("← 返回"):
            st.session_state.pop("roadmap_mode", None)
            st.rerun()
    else:
        col_info, col_back = st.columns([6, 1])
        with col_back:
            if st.button("← 返回"):
                st.session_state.pop("roadmap_mode", None)
                st.rerun()

        # 一级类目快速过滤（路线图不逐篇选，但可按类目聚焦）
        taxonomy = load_taxonomy()
        cats = taxonomy.get("categories", {}) if taxonomy else {}
        if cats:
            rm_cat = st.selectbox(
                "📂 按类目聚焦生成", options=["全部"] + list(cats.keys()),
                key="roadmap_cat",
                help="选一个类目，路线图将基于该类目下的论文生成；选「全部」则基于所有论文",
            )
            if rm_cat != "全部":
                sub_tags = {t.lstrip("#") for t in cats.get(rm_cat, [])}
                roadmap_papers = [
                    s for s in papers_only
                    if any(tag in sub_tags for tag in _tags(s.get("final_result", "")))
                ]
                if not roadmap_papers:
                    st.warning(f"「{rm_cat}」类目下暂无已分析的论文，将使用全部论文。")
                    roadmap_papers = papers_only
            else:
                roadmap_papers = papers_only
        else:
            roadmap_papers = papers_only

        with col_info:
            st.caption(f"基于 **{len(roadmap_papers)}** 篇论文生成路线图推荐")

        if st.button("🚀 生成路线图", type="primary"):
            st.divider()
            messages = build_roadmap_prompt(roadmap_papers)
            progress = st.empty()
            report   = st.empty()
            chunks   = []
            progress.caption("正在分析知识覆盖与盲区…")
            try:
                from core.llm_client import stream_analysis as _stream
                for delta in _stream(messages):
                    chunks.append(delta)
                    report.markdown("".join(chunks))

                import datetime
                result    = "".join(chunks)
                date_str  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                stem      = f"ROADMAP_{date_str}"
                save_path = sm._notes_subdir("roadmap") / f"{datetime.date.today().isoformat()}_{stem}.md"
                save_path.write_text(result, encoding="utf-8")

                # 写入文献库（synthesis 类型复用）
                sm.create_synthesis_state(stem, [s.get("pdf_filename","") for s in papers_only],
                                          result, str(save_path))
                # 覆盖 pdf_filename 让侧边栏显示正确
                st_obj = sm.load_state(stem)
                sm.update_state(st_obj, pdf_filename="🗺️ 阅读路线图",
                                type="roadmap")

                progress.caption("✅ 路线图生成完成")
                st.toast("🗺️ 阅读路线图已生成！", icon="🗺️")
                st.session_state["roadmap_mode"]  = False
                st.session_state["selected_stem"] = stem
                st.session_state["just_completed"] = True
                st.rerun()

            except Exception as e:
                progress.empty()
                st.error(f"❌ 生成失败：{e}")

# ══ E：矛盾检测器 ══════════════════════════════════════════════════════════════
elif contradiction_mode and not trigger_stem:
    st.title("⚡ 矛盾检测器")
    st.caption("AI 对比所有论文的核心主张，识别相互矛盾的结论——矛盾点往往正是你的创新切入口。")
    st.divider()

    all_done    = sm.list_completed()
    papers_only = [s for s in all_done if s.get("type", "paper") == "paper"]

    if len(papers_only) < 2:
        st.info("至少需要 **2 篇**已分析的论文才能进行矛盾检测。")
        if st.button("← 返回"):
            st.session_state.pop("contradiction_mode", None)
            st.rerun()
    else:
        chosen_titles, options = _cat_paper_selector(papers_only, "contradiction")
        chosen_states = [options[t] for t in chosen_titles]

        col_run, col_back = st.columns([2, 8])
        with col_run:
            run_btn = st.button("🚀 开始检测", type="primary",
                                disabled=len(chosen_states) < 2)
        with col_back:
            if st.button("← 返回"):
                st.session_state.pop("contradiction_mode", None)
                st.rerun()

        if len(chosen_states) < 2:
            st.caption("请至少选择 2 篇论文。")

        _CONTRADICTION_WARN = 20
        if 2 <= len(chosen_states) > _CONTRADICTION_WARN:
            st.warning(
                f"⚠️ 已选 {len(chosen_states)} 篇论文，prompt 较大，首次响应可能需要 **2-5 分钟**，请耐心等待。"
                f"建议每次不超过 {_CONTRADICTION_WARN} 篇以获得最佳速度。"
            )

        if run_btn and len(chosen_states) >= 2:
            st.divider()
            messages = build_contradiction_prompt(chosen_states)
            progress = st.empty()
            report   = st.empty()
            chunks   = []
            got_first = False
            try:
                with st.spinner(f"正在对比 {len(chosen_states)} 篇论文的核心主张，等待模型响应…"):
                    gen = stream_analysis(messages)
                    first_delta = next(gen, None)
                if first_delta is None:
                    st.error("模型未返回任何内容，请重试。")
                else:
                    chunks.append(first_delta)
                    report.markdown("".join(chunks))
                    for delta in gen:
                        chunks.append(delta)
                        report.markdown("".join(chunks))

                    import datetime
                    result    = "".join(chunks)
                    date_str  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    stem      = f"CONTRADICTION_{date_str}"
                    save_path = sm._notes_subdir("contradiction") / f"{datetime.date.today().isoformat()}_{stem}.md"
                    save_path.write_text(result, encoding="utf-8")

                    source_titles = list(chosen_titles)
                    sm.create_synthesis_state(stem, source_titles, result, str(save_path))
                    st_obj = sm.load_state(stem)
                    sm.update_state(st_obj, pdf_filename="⚡ 矛盾检测报告", type="contradiction")

                    progress.caption("✅ 矛盾检测完成，已保存至文献库")
                    st.toast("⚡ 矛盾检测完成！", icon="⚡")
                    st.session_state["contradiction_mode"] = False
                st.session_state["selected_stem"]      = stem
                st.session_state["just_completed"]     = True
                st.rerun()

            except Exception as e:
                progress.empty()
                st.error(f"❌ 检测失败：{e}")

# ══ F：文献追踪 ═══════════════════════════════════════════════════════════════
elif scout_mode and not trigger_stem:
    st.title("🔭 文献追踪")
    st.caption("从已分析论文的参考文献中，发现值得阅读的新文献——基于你的研究目标智能筛选。")
    st.divider()

    from core.ref_scout import extract_refs_from_text, extract_arxiv_id

    # ── 论文选择器 ────────────────────────────────────────────────────────────
    all_done_scout   = sm.list_completed()
    papers_only_scout = [s for s in all_done_scout if s.get("type", "paper") == "paper"]

    if not papers_only_scout:
        st.info("还没有已分析的论文，请先上传并分析论文。")
    else:
        taxonomy_scout = load_taxonomy()
        cats_scout     = taxonomy_scout.get("categories", {}) if taxonomy_scout else {}

        # 一级类目快速预选
        if cats_scout:
            scout_cat = st.selectbox(
                "📂 按类目预选论文",
                options=["（不预选）"] + list(cats_scout.keys()),
                key="scout_cat",
                help="选一个类目，下方自动勾选该类目的论文；默认不预选",
            )
        else:
            scout_cat = "（不预选）"

        # 类目变化时重置复选框状态
        if st.session_state.get("scout_cat_init") != scout_cat:
            if scout_cat != "（不预选）" and cats_scout:
                sub_tags = {t.lstrip("#") for t in cats_scout.get(scout_cat, [])}
                for s in papers_only_scout:
                    checked = any(tag in sub_tags
                                  for tag in _tags(s.get("final_result", "")))
                    st.session_state[f"scout_paper_cb_{s['stem']}"] = checked
            else:
                for s in papers_only_scout:
                    st.session_state[f"scout_paper_cb_{s['stem']}"] = False
            st.session_state["scout_cat_init"] = scout_cat

        sc1, sc2, sc3 = st.columns([1, 1.5, 5])
        with sc1:
            if st.button("☑ 全选", key="scout_paper_selall"):
                for s in papers_only_scout:
                    st.session_state[f"scout_paper_cb_{s['stem']}"] = True
                st.rerun()
        with sc2:
            if st.button("☐ 取消全选", key="scout_paper_deselall"):
                for s in papers_only_scout:
                    st.session_state[f"scout_paper_cb_{s['stem']}"] = False
                st.rerun()

        selected_stems_scout: list[str] = []
        with st.container(height=220):
            for s in papers_only_scout:
                stem  = s["stem"]
                title = _first_heading(s.get("final_result", "")) or s.get("pdf_filename", stem)
                cb_key = f"scout_paper_cb_{stem}"
                if cb_key not in st.session_state:
                    st.session_state[cb_key] = False  # 默认不选
                has_refs   = (sm.REFS_CACHE_DIR / f"{stem}.json").exists()
                has_filter = (sm.REFS_CACHE_DIR / f"{stem}_filtered.json").exists()
                icon = "✅" if has_filter else ("📄" if has_refs else "○")
                if st.checkbox(f"{icon} {title}", key=cb_key):
                    selected_stems_scout.append(stem)

        n_sel         = len(selected_stems_scout)
        n_need_scan   = sum(1 for stem in selected_stems_scout
                            if not (sm.REFS_CACHE_DIR / f"{stem}.json").exists())
        n_need_filter = sum(1 for stem in selected_stems_scout
                            if not (sm.REFS_CACHE_DIR / f"{stem}_filtered.json").exists())
        with sc3:
            st.caption(
                f"已选 **{n_sel}** 篇　·　待扫描 **{n_need_scan}** 篇　·　"
                f"待AI筛选 **{n_need_filter}** 篇　（✅=已过滤 📄=已扫描 ○=未扫描）"
            )

        # ── 操作按钮 ─────────────────────────────────────────────────────────
        col_scan, col_filter, col_back = st.columns([2, 2.5, 5])
        with col_scan:
            scan_btn = st.button(
                "📖 扫描参考文献",
                disabled=n_sel == 0,
                type="primary" if n_need_scan > 0 and n_sel > 0 else "secondary",
                use_container_width=True,
            )
        with col_filter:
            filter_btn = st.button(
                "🤖 AI 筛选（增量）",
                disabled=n_sel == 0,
                type="primary" if n_need_filter > 0 and n_sel > 0 else "secondary",
                use_container_width=True,
            )
        with col_back:
            if st.button("← 返回", use_container_width=True):
                st.session_state.pop("scout_mode", None)
                st.session_state.pop("scout_note_stem", None)
                st.session_state.pop("scout_dl_results", None)
                st.rerun()

        # ── Step 1：扫描（仅扫描未缓存的论文）──────────────────────────────
        if scan_btn and selected_stems_scout:
            from core.pdf_parser import extract_text_from_pdf
            state_map_scout = {s["stem"]: s for s in papers_only_scout}
            stems_to_scan   = [st_ for st_ in selected_stems_scout
                               if not (sm.REFS_CACHE_DIR / f"{st_}.json").exists()]
            stems_cached    = [st_ for st_ in selected_stems_scout
                               if (sm.REFS_CACHE_DIR / f"{st_}.json").exists()]

            if not stems_to_scan:
                st.info(f"✅ 所有 {n_sel} 篇论文的参考文献均已缓存，无需重新扫描。")
            else:
                with st.status(
                    f"扫描 {len(stems_to_scan)} 篇（{len(stems_cached)} 篇使用缓存）…",
                    expanded=True,
                ) as status:
                    prog = st.progress(0)
                    for i, stem in enumerate(stems_to_scan):
                        state = state_map_scout[stem]
                        fname = state.get("pdf_filename", "")
                        pdf_path = sm.PDFS_DIR / fname if fname else None
                        prog.progress((i + 1) / len(stems_to_scan),
                                      text=f"{i+1}/{len(stems_to_scan)} {fname[:45]}")
                        st.write(f"📄 {fname[:60]}")
                        if not pdf_path or not pdf_path.exists():
                            st.caption("　　⚠️ 找不到 PDF，跳过")
                            continue
                        try:
                            text     = extract_text_from_pdf(pdf_path.read_bytes())
                            raw_refs = extract_refs_from_text(text)
                            ref_dicts = [{"raw": r, "arxiv_id": extract_arxiv_id(r)}
                                         for r in raw_refs]
                            save_paper_refs(stem, ref_dicts)
                            st.caption(f"　　✅ 提取 {len(raw_refs)} 条，已缓存")
                        except Exception as exc:
                            st.caption(f"　　❌ 解析失败：{exc}")
                    prog.empty()
                    status.update(
                        label=f"扫描完成：{len(stems_to_scan)} 篇新增，{len(stems_cached)} 篇缓存命中",
                        state="complete",
                    )
                st.toast("✅ 扫描完成", icon="🔭")
                st.rerun()

        # ── Step 2：AI 过滤（增量，仅处理未缓存或 goal 变化的论文）────────
        if filter_btn and selected_stems_scout:
            cur_goal_hash = _goal_hash()
            goal_text     = GOAL_PATH.read_text(encoding="utf-8") if GOAL_PATH.exists() else ""

            stems_to_filter: list[str] = []
            stems_filter_skip: list[str] = []
            stems_no_refs: list[str] = []
            for stem in selected_stems_scout:
                cached_f = load_paper_filter(stem)
                if cached_f and cached_f[1] == cur_goal_hash:
                    stems_filter_skip.append(stem)
                elif load_paper_refs(stem) is not None:
                    stems_to_filter.append(stem)
                else:
                    stems_no_refs.append(stem)

            if stems_no_refs:
                st.warning(f"⚠️ {len(stems_no_refs)} 篇论文尚未扫描参考文献，请先点击「📖 扫描参考文献」：" +
                           "、".join(stems_no_refs[:3]))

            if not stems_to_filter:
                if stems_filter_skip:
                    st.info(f"✅ 所有选中论文的 AI 筛选均已缓存（{len(stems_filter_skip)} 篇），无需重新筛选。")
                # stems_no_refs 的情况已在上方显示 warning，这里不再重复
            else:
                filter_errors: list[str] = []
                prog = st.progress(
                    0,
                    text=f"AI 筛选 {len(stems_to_filter)} 篇新论文（{len(stems_filter_skip)} 篇缓存）…",
                )
                for si, stem in enumerate(stems_to_filter):
                    refs_for_stem = load_paper_refs(stem) or []
                    filtered_for_stem: list[dict] = []
                    n_batches = max(1, (len(refs_for_stem) + BATCH_SIZE - 1) // BATCH_SIZE)
                    failed_batches: list[tuple] = []
                    for bi in range(n_batches):
                        batch    = refs_for_stem[bi * BATCH_SIZE: (bi + 1) * BATCH_SIZE]
                        messages = _build_filter_prompt(batch, goal_text)
                        overall  = (si + (bi + 1) / n_batches) / len(stems_to_filter)
                        prog.progress(overall,
                                      text=f"论文 {si+1}/{len(stems_to_filter)}，批次 {bi+1}/{n_batches}…")
                        try:
                            output     = call_once(messages, max_tokens=1500)
                            idxs, reasons = _parse_filter_result(output)
                            for idx in idxs:
                                if 1 <= idx <= len(batch):
                                    entry           = dict(batch[idx - 1])
                                    entry["reason"] = reasons.get(str(idx), "")
                                    filtered_for_stem.append(entry)
                        except Exception as _filter_exc:
                            failed_batches.append((bi + 1, str(_filter_exc)))
                    save_paper_filter(stem, filtered_for_stem, cur_goal_hash)
                    if failed_batches:
                        err_detail = "；".join(f"批次{b}：{e}" for b, e in failed_batches)
                        filter_errors.append(
                            f"论文 `{stem}`：{len(failed_batches)}/{n_batches} 批次失败 — {err_detail}"
                        )
                prog.empty()
                # 把错误存入 session_state，rerun 后仍能显示
                if filter_errors:
                    st.session_state["scout_filter_errors"] = filter_errors
                else:
                    st.session_state.pop("scout_filter_errors", None)
                st.toast(
                    f"🎯 AI 筛选完成（新增 {len(stems_to_filter)} 篇，缓存 {len(stems_filter_skip)} 篇）",
                    icon="🔭",
                )
                st.rerun()

        # ── 展示 AI 推荐结果 ─────────────────────────────────────────────────
        # rerun 后显示上次筛选的 API 错误（若有）
        if st.session_state.get("scout_filter_errors"):
            for _err in st.session_state["scout_filter_errors"]:
                st.error(f"⚠️ AI 调用失败：{_err}")

        if selected_stems_scout:
            cur_goal_hash_display = _goal_hash()
            all_filtered_scout: list[dict] = []
            n_filtered_papers = 0
            stale_stems: list[str] = []
            for stem in selected_stems_scout:
                cached_f = load_paper_filter(stem)
                if cached_f:
                    if cached_f[1] == cur_goal_hash_display:
                        all_filtered_scout.extend(cached_f[0])
                        n_filtered_papers += 1
                    else:
                        stale_stems.append(stem)
            if stale_stems:
                _state_map_disp = {s["stem"]: s for s in papers_only_scout}
                _stale_titles = [
                    _first_heading(_state_map_disp[st_].get("final_result", "")) or st_
                    for st_ in stale_stems if st_ in _state_map_disp
                ]
                st.warning(
                    f"⚠️ {len(stale_stems)} 篇论文的筛选结果基于旧研究目标，已跳过——"
                    f"请重新运行「🤖 AI 筛选（增量）」：{'、'.join(_stale_titles[:5])}"
                )

            if n_filtered_papers > 0:
                # 去重（arxiv_id 优先，否则用原文前 80 字符）
                seen_keys: set[str] = set()
                deduped_filtered: list[dict] = []
                for r in all_filtered_scout:
                    dk = r.get("arxiv_id") or r["raw"].lower()[:80]
                    if dk not in seen_keys:
                        seen_keys.add(dk)
                        deduped_filtered.append(r)

                existing_arxiv = get_existing_arxiv_ids()

                # ── 保存 Markdown 报告（有无结果均保存）────────────────────
                import datetime as _dt_scout
                _scout_note_dir  = sm._notes_subdir("scout")
                _scout_note_stem = st.session_state.get("scout_note_stem")
                if not _scout_note_stem:
                    _scout_note_stem = f"SCOUT_{_dt_scout.datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    st.session_state["scout_note_stem"] = _scout_note_stem
                _scout_note_path = (
                    _scout_note_dir
                    / f"{_dt_scout.date.today().isoformat()}_{_scout_note_stem}.md"
                )
                _dl_results = st.session_state.get("scout_dl_results")
                _scout_note_path.write_text(
                    build_scout_markdown(
                        deduped_filtered, existing_arxiv, n_filtered_papers, _dl_results
                    ),
                    encoding="utf-8",
                )
                # ──────────────────────────────────────────────────────────

                if not deduped_filtered:
                    st.info(
                        f"🔍 AI 筛选完成（{n_filtered_papers} 篇论文），"
                        f"未发现与研究目标相关的新论文。　📄 已保存至 `{_scout_note_path}`"
                    )
                else:
                    st.markdown(
                        f"### 🎯 AI 推荐阅读（共 {len(deduped_filtered)} 篇，来自 {n_filtered_papers} 篇论文）"
                    )
                    st.caption(
                        f"✅=已在库/已下载待分析　⬇=可下载（arxiv）　"
                        f"📄 已保存至 `{_scout_note_path}`"
                    )

                    fa1, fa2, _ = st.columns([1, 1.5, 6])
                    with fa1:
                        if st.button("☑ 全选", key="scout_selall"):
                            for i in range(len(deduped_filtered)):
                                st.session_state[f"scout_cb_{i}"] = True
                            st.rerun()
                    with fa2:
                        if st.button("☐ 取消全选", key="scout_deselall"):
                            for i in range(len(deduped_filtered)):
                                st.session_state[f"scout_cb_{i}"] = False
                            st.rerun()

                    selected_for_dl: list[dict] = []
                    with st.container(height=400):
                        for i, r in enumerate(deduped_filtered):
                            cb_key   = f"scout_cb_{i}"
                            arxiv_id = r.get("arxiv_id")
                            if arxiv_id and arxiv_id in existing_arxiv:
                                badge = "✅"
                            elif arxiv_id:
                                badge = "⬇"
                            else:
                                badge = "○"
                            label = f"{badge} {r['raw'][:120]}"
                            if cb_key not in st.session_state:
                                st.session_state[cb_key] = True
                            if st.checkbox(label, key=cb_key):
                                selected_for_dl.append(r)
                            if r.get("reason"):
                                st.caption(f"　　↳ {r['reason']}")

                    # ── 下载区 ───────────────────────────────────────────────────
                    st.divider()
                    arxiv_new   = [r for r in selected_for_dl
                                   if r.get("arxiv_id") and r["arxiv_id"] not in existing_arxiv]
                    already_have = [r for r in selected_for_dl
                                    if r.get("arxiv_id") and r["arxiv_id"] in existing_arxiv]
                    no_arxiv    = [r for r in selected_for_dl if not r.get("arxiv_id")]
                    st.caption(
                        f"已勾选 **{len(selected_for_dl)}** 条　·　"
                        f"可下载 **{len(arxiv_new)}** 条　·　"
                        f"已在库/已下载 **{len(already_have)}** 条（跳过）　·　"
                        f"无 arxiv ID **{len(no_arxiv)}** 条（需手动查找）"
                    )

                    if arxiv_new:
                        if st.button(
                            f"⬇ 自动下载 {len(arxiv_new)} 篇到 data/inbox/",
                            type="primary",
                            use_container_width=True,
                            key="scout_dl_btn",
                        ):
                            prog_dl  = st.progress(0, text="开始下载…")
                            n_ok, n_dup, failed_dl = 0, 0, []
                            for di, r in enumerate(arxiv_new):
                                aid = r["arxiv_id"]
                                prog_dl.progress(
                                    (di + 1) / len(arxiv_new),
                                    text=f"下载 {aid} ({di+1}/{len(arxiv_new)})…",
                                )
                                ok, msg, _sha = download_arxiv_pdf(aid)
                                if ok and msg == "duplicate":
                                    n_dup += 1
                                elif ok:
                                    n_ok += 1
                                else:
                                    failed_dl.append(f"{aid}: {msg}")
                            prog_dl.empty()
                            # 保存下载结果到 session_state，rerun 后更新 Markdown
                            st.session_state["scout_dl_results"] = {
                                "ok": n_ok, "dup": n_dup, "failed": failed_dl
                            }
                            parts = [f"✅ 下载 {n_ok} 篇"]
                            if n_dup:
                                parts.append(f"重复跳过 {n_dup} 篇")
                            if failed_dl:
                                parts.append(f"失败 {len(failed_dl)} 篇")
                            st.toast("　·　".join(parts), icon="📥")
                            if failed_dl:
                                with st.expander(f"⚠️ {len(failed_dl)} 篇下载失败"):
                                    for fm in failed_dl:
                                        st.caption(fm)
                            st.rerun()

                    # 提示待分析 + 重命名提示
                    pending_pdfs = list(sm.INBOX_DIR.glob("*.pdf"))
                    if pending_pdfs:
                        st.info(
                            f"📥 `data/inbox/` 中有 **{len(pending_pdfs)}** 篇待分析论文，"
                            f"可在「上传 & 分析」页面批量分析。  \n"
                            f"如需将文件名改为论文标题，运行：`python scripts/rename_refs.py`"
                        )

            elif n_filtered_papers == 0 and any(
                (sm.REFS_CACHE_DIR / f"{st_}.json").exists()
                for st_ in selected_stems_scout
            ):
                st.info("已扫描参考文献，点击「🤖 AI 筛选（增量）」开始过滤。")
            elif selected_stems_scout:
                st.info("请先「📖 扫描参考文献」，再进行 AI 筛选。")

# ══ G：设置页 ════════════════════════════════════════════════════════════════
elif settings_mode and not trigger_stem:
    import json as _json
    from core.config import _DOMAIN_FILE, _SECTION_KEYS_FILE
    from core.state_manager import CONFIG_DIR as _CONFIG_DIR

    _GOAL_FILE_S     = _CONFIG_DIR / "02_research_goal.md"
    _TEMPLATE_FILE_S = _CONFIG_DIR / "03_output_template.md"

    st.title("⚙️ 个性化设置")
    st.caption("修改后点击「💾 保存所有配置」，下次调用 AI 功能时即刻生效。")
    st.divider()

    def _read_file(p) -> str:
        return p.read_text(encoding="utf-8") if p.exists() else ""

    # ── 领域描述 ─────────────────────────────────────────────────────────────
    st.subheader("🏷️ 研究领域描述")
    st.caption("一行简短描述，嵌入所有 AI system prompt 的领域定语（留空则不插入领域词）。")
    new_domain = st.text_input(
        "领域描述",
        value=_read_file(_DOMAIN_FILE).strip().lstrip("#").strip(),
        placeholder="例：机器学习系统优化与编译器自动调优",
        label_visibility="collapsed",
    )

    st.divider()

    # ── 研究目标 ─────────────────────────────────────────────────────────────
    st.subheader("🎯 研究目标（02_research_goal.md）")
    st.caption("AI 分析每篇论文、生成综合/路线图/矛盾检测时的核心参考依据。")
    new_goal = st.text_area(
        "研究目标",
        value=_read_file(_GOAL_FILE_S),
        height=220,
        label_visibility="collapsed",
    )

    st.divider()

    # ── 输出模板 ─────────────────────────────────────────────────────────────
    st.subheader("📄 论文分析模板（03_output_template.md）")
    st.caption("每篇论文被分析时 AI 严格遵守的输出格式，修改后需同步更新下方「章节提取配置」。")
    new_template = st.text_area(
        "输出模板",
        value=_read_file(_TEMPLATE_FILE_S),
        height=300,
        label_visibility="collapsed",
    )

    st.divider()

    # ── 章节提取配置 ─────────────────────────────────────────────────────────
    st.subheader("🔑 章节提取配置（04_section_keys.json）")
    st.caption(
        "告诉 Idea 综合器、矛盾检测器如何从分析报告中提取各关键字段。\n"
        "**key**（左）= 逻辑字段名，**value**（右）= 模板中对应的章节标题关键词（部分匹配即可）。\n"
        "改了输出模板的章节标题后，务必同步修改这里的 value。"
    )
    raw_section_keys = _read_file(_SECTION_KEYS_FILE)
    if not raw_section_keys:
        from core.config import _DEFAULT_SECTION_KEYS as _dsk
        raw_section_keys = _json.dumps(_dsk, ensure_ascii=False, indent=2)
    new_section_keys = st.text_area(
        "章节提取配置 JSON",
        value=raw_section_keys,
        height=200,
        label_visibility="collapsed",
    )

    st.divider()

    col_save, col_back = st.columns([2, 8])
    with col_save:
        save_btn = st.button("💾 保存所有配置", type="primary", use_container_width=True)
    with col_back:
        if st.button("← 返回", use_container_width=True):
            st.session_state.pop("settings_mode", None)
            st.rerun()

    if save_btn:
        errors = []
        # 验证 JSON
        try:
            parsed = _json.loads(new_section_keys)
            if not isinstance(parsed, dict):
                errors.append("章节提取配置必须是 JSON 对象（{...}）")
        except _json.JSONDecodeError as je:
            errors.append(f"章节提取配置 JSON 格式错误：{je}")

        if errors:
            for e in errors:
                st.error(e)
        else:
            _DOMAIN_FILE.write_text(new_domain.strip() + "\n", encoding="utf-8")
            _GOAL_FILE_S.write_text(new_goal, encoding="utf-8")
            _TEMPLATE_FILE_S.write_text(new_template, encoding="utf-8")
            _SECTION_KEYS_FILE.write_text(
                _json.dumps(parsed, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            st.success("✅ 配置已保存，下次 AI 调用时生效。")
            st.toast("配置保存成功", icon="⚙️")

# ══ A：查看已有报告 ═══════════════════════════════════════════════════════════
elif selected_stem and not trigger_stem:
    state = sm.load_state(selected_stem)
    if state and state.get("status") == "completed":
        final  = state["final_result"]
        note_p = state.get("note_path", "")

        # 完成提醒（仅刚分析完时弹一次）
        if st.session_state.pop("just_completed", False):
            icon_hint = {"synthesis":"💡","roadmap":"🗺️","contradiction":"⚡"}.get(
                state.get("type",""), "✅")
            msg = {"synthesis":"Idea 综合完成！",
                   "roadmap":"阅读路线图已生成！",
                   "contradiction":"矛盾检测完成！"}.get(
                state.get("type",""), "分析完成！报告已保存至 data/notes/")
            st.toast(msg, icon=icon_hint)

        # 工具栏
        is_synthesis = state.get("type") in ("synthesis", "roadmap", "contradiction")

        # 我的笔记文件路径
        import datetime as _dt_note
        _mynote_dir = sm.NOTES_DIR / "mynotes"
        _mynote_dir.mkdir(exist_ok=True)
        mynote_path = _mynote_dir / f"{selected_stem}_mynote.md"

        btn_cols = st.columns([1, 1, 1, 1, 1, 3])
        with btn_cols[0]:
            if note_p and Path(note_p).exists():
                with open(note_p, "rb") as f:
                    st.download_button("⬇️ 下载", f,
                                       file_name=Path(note_p).name,
                                       mime="text/markdown")
        with btn_cols[1]:
            if not is_synthesis and st.button("🔄 重新分析"):
                sm.update_state(state, status="pending",
                                chunk_summaries=[], partial_result="", final_result="")
                st.session_state.pop("selected_stem", None)
                st.rerun()
        with btn_cols[2]:
            if st.button("📤 上传下一篇" if not is_synthesis else "📤 上传论文"):
                st.session_state.pop("selected_stem", None)
                st.rerun()
        with btn_cols[3]:
            _note_open = st.session_state.get("show_mynote", False)
            if st.button("📝 笔记", key="btn_toggle_mynote",
                         type="primary" if _note_open else "secondary"):
                st.session_state["show_mynote"] = not _note_open
                st.rerun()
        with btn_cols[4]:
            _pdf_file = state.get("pdf_filename", "")
            _pdf_path = sm.PDFS_DIR / _pdf_file if _pdf_file else None
            _pdf_exists = bool(_pdf_path and _pdf_path.exists())
            if st.button("📂 原文", disabled=not _pdf_exists,
                         help="用系统默认 PDF 阅读器打开原文" if _pdf_exists else "PDF 文件不存在"):
                _open_pdf(str(_pdf_path))

        # ── 阅读状态 + 评分快速设置（仅论文类型）────────────────────────────
        if not is_synthesis:
            rc1, rc2, _ = st.columns([2, 3, 5])
            with rc1:
                cur_status = state.get("read_status", "未读")
                sidx = READ_STATUSES.index(cur_status) if cur_status in READ_STATUSES else 0
                new_status = st.selectbox(
                    "阅读状态", READ_STATUSES, index=sidx,
                    key=f"view_status_{selected_stem}",
                )
                if new_status != cur_status:
                    sm.update_state(state, read_status=new_status)
                    st.rerun()
            with rc2:
                cur_stars = state.get("star_rating", 0)
                new_stars = st.selectbox(
                    "评分", options=list(range(6)), index=cur_stars,
                    format_func=lambda x: "☆ 未评分" if x == 0 else ("🌟🌟🌟🌟🌟" if x == 5 else "⭐" * x),
                    key=f"view_stars_{selected_stem}",
                )
                if new_stars != cur_stars:
                    sm.update_state(state, star_rating=new_stars)
                    st.rerun()

        # 综合/路线图报告显示来源论文
        if is_synthesis:
            sources = state.get("source_titles", [])
            prefix  = {"synthesis":"基于论文：",
                       "roadmap":"覆盖论文：",
                       "contradiction":"对比论文："}.get(state.get("type",""), "来源：")
            if sources:
                st.caption(prefix + "  ·  ".join(f"`{t[:30]}`" for t in sources[:6])
                           + (f" 等 {len(sources)} 篇" if len(sources) > 6 else ""))

        if st.session_state.get("show_mynote"):
            existing_note = mynote_path.read_text(encoding="utf-8") if mynote_path.exists() else ""
            col_main, col_note = st.columns([3, 2])
            with col_main:
                with st.container(height=750, border=False):
                    st.markdown(final)
            with col_note:
                st.markdown("**✏️ 我的笔记**")
                note_text = st.text_area(
                    "note",
                    value=existing_note,
                    height=680,
                    label_visibility="collapsed",
                    key=f"mynote_{selected_stem}",
                    placeholder="在此记录你的想法、疑问、批注…",
                )
                nc1, nc2 = st.columns([1, 2])
                with nc1:
                    if st.button("💾 保存", key="save_mynote", use_container_width=True):
                        mynote_path.write_text(note_text, encoding="utf-8")
                        st.toast("笔记已保存！", icon="✅")
                with nc2:
                    if mynote_path.exists():
                        _mtime = _dt_note.datetime.fromtimestamp(mynote_path.stat().st_mtime)
                        st.caption(f"上次保存：{_mtime.strftime('%m-%d %H:%M')}")
        else:
            st.markdown(final)
    else:
        st.warning("报告未找到，请重新上传分析。")

# ══ B：上传 & 分析 ════════════════════════════════════════════════════════════
else:
    st.title("🔬 Paper Agent")
    st.caption("上传一篇或多篇论文 PDF（可从文件管理器拖入），AI 将基于你的研究目标生成结构化阅读笔记")

    # 中断恢复横幅：仅当任务确实中断（后台无活跃线程）且有历史进度时才显示
    for itask in sm.list_interrupted():
        stem_i   = itask["stem"]
        status_i = itask.get("status", "")

        # 后台线程仍在跑 → 正常运行中，不是中断，跳过
        if batch_runner.is_alive(stem_i):
            continue

        # pending 且没有任何历史进度 → 刚上传还没开始，不算中断，跳过
        has_progress = bool(itask.get("chunk_summaries") or itask.get("partial_result"))
        if status_i == "pending" and not has_progress:
            continue

        # 本次会话已手动忽略的中断任务，不再显示横幅
        if stem_i in st.session_state.get("dismissed_interrupted", set()):
            continue

        fname_i = itask.get("pdf_filename", stem_i)
        done_i  = len(itask.get("chunk_summaries", []))
        total_i = itask.get("total_chunks", "?")
        hint = {
            "pending":         f"提取关键信息中断（已完成 {done_i}/{total_i} 块）",
            "map_in_progress": f"提取关键信息中断（已完成 {done_i}/{total_i} 块）",
            "streaming":       "报告生成中断",
        }.get(status_i, status_i)
        c_msg, c_resume, c_dismiss = st.columns([5, 1, 1])
        with c_msg:
            st.warning(f"⚡ **{fname_i}** 上次分析中断（{hint}），可一键恢复。")
        with c_resume:
            if st.button("▶ 恢复", key=f"resume_{stem_i}"):
                full_text_r, fname_r = _resume_from_pdf(itask)
                if full_text_r:
                    st.session_state.update({
                        "trigger_stem":     stem_i,
                        "trigger_text":     full_text_r,
                        "trigger_filename": fname_r,
                    })
                    st.session_state.pop("selected_stem", None)
                    st.rerun()
                else:
                    st.error("PDF 文件未找到，无法恢复。")
        with c_dismiss:
            if st.button("✕ 忽略", key=f"dismiss_{stem_i}"):
                dismissed = st.session_state.get("dismissed_interrupted", set())
                dismissed.add(stem_i)
                st.session_state["dismissed_interrupted"] = dismissed
                st.rerun()

    st.divider()

    # 始终使用批量上传模式（支持拖入单个或多个 PDF）
    batch_mode = True

    # ── 批量上传模式 ──────────────────────────────────────────────────────
    if batch_mode and not trigger_stem and not st.session_state.get("batch_files"):
        uploaded_files = st.file_uploader(
            "拖入或点击上传论文 PDF（支持多选，也可拖入单个文件）",
            type=["pdf"],
            accept_multiple_files=True,
            label_visibility="visible",
        )

        if uploaded_files:
            st.caption(f"已选择 {len(uploaded_files)} 个文件")

            # 预处理：检查每个文件状态
            file_info = []
            for uf in uploaded_files:
                pdf_bytes = uf.read()
                uf.seek(0)  # 重置文件指针以便后续读取
                stem = safe_stem(uf.name)
                sha256 = sm.compute_sha256(pdf_bytes)
                existing = sm.find_by_sha256(sha256)
                if existing is None:
                    existing = sm.load_state(stem)
                    if existing and existing.get("sha256","") not in ("", sha256):
                        existing = None
                if existing:
                    stem = existing["stem"]

                already_done = existing and existing.get("status") == "completed"
                skip_reason  = ""
                if already_done:
                    if sm.find_by_sha256(sha256):
                        skip_reason = "哈希匹配，已分析"
                    else:
                        skip_reason = "文件名匹配，已分析"
                file_info.append({
                    "name": uf.name,
                    "stem": stem,
                    "sha256": sha256,
                    "bytes": pdf_bytes,
                    "existing": existing,
                    "done": already_done,
                    "skip_reason": skip_reason,
                })

            # 显示状态
            done_count = sum(1 for f in file_info if f["done"])
            todo_count = len(file_info) - done_count

            col_stat1, col_stat2 = st.columns(2)
            col_stat1.metric("✅ 已完成（跳过）", done_count)
            col_stat2.metric("⏳ 待分析", todo_count)

            # 显示文件列表，标注跳过原因
            with st.expander("📋 文件列表", expanded=True):
                for i, f in enumerate(file_info, 1):
                    if f["done"]:
                        st.caption(f"{i}. ✅ {f['name']}  —  *{f['skip_reason']}，跳过*")
                    else:
                        st.caption(f"{i}. ⏳ {f['name']}  —  待分析")

            if todo_count == 0:
                st.success("所有文件均已分析完成！")
            else:
                # 并行/顺序选择
                parallel_mode = st.checkbox("🚀 并行处理（同时分析多篇，速度更快）", value=False)
                if parallel_mode:
                    st.warning("⚠️ 并行模式会同时调用多个 API，请确保你的 API 配额充足")

                radio_result_language = st.radio(
                    "分析报告语言",
                    [":rainbow[中文]", "***English***🌏"],
                    captions=[
                        "输出中文分析报告，最小化理解难度.",
                        "Write analysis report in English, preserve the best of the paper."
                    ]
                )
                if radio_result_language == ":rainbow[中文]":
                    result_language = "zh-CN"
                else:
                    result_language = "en-US"

                col_run, col_back = st.columns([1, 3])
                with col_run:
                    if st.button("🚀 开始批量分析", type="primary"):
                        # 将语言设置传递给每个文件
                        result_language = "zh-CN" if radio_result_language == ":rainbow[中文]" else "en-US"
                        for f in file_info:
                            f["result_language"] = result_language
                        st.session_state["batch_files"] = file_info
                        st.session_state["batch_index"] = 0
                        st.session_state["batch_parallel"] = parallel_mode
                        st.session_state["result_language"] = result_language
                        st.rerun()
                with col_back:
                    if st.button("← 返回"):
                        st.session_state.pop("batch_files", None)
                        st.rerun()

    # ── 批量分析执行中 ────────────────────────────────────────────────────
    elif batch_mode and st.session_state.get("batch_files") and not trigger_stem:
        import time

        batch_files   = st.session_state["batch_files"]
        parallel_mode = st.session_state.get("batch_parallel", False)
        todo_files    = [f for f in batch_files if not f["done"]]

        # ══ 并行模式 ══════════════════════════════════════════════════════
        if parallel_mode:
            # 首次进入：一次性启动所有线程
            if not st.session_state.get("batch_parallel_started"):
                for f in todo_files:
                    batch_runner.start(f)
                st.session_state["batch_parallel_started"] = True
                st.session_state["batch_parallel_stems"] = [f["stem"] for f in todo_files]

            stems      = st.session_state["batch_parallel_stems"]
            total_n    = len(stems)
            done_n     = batch_runner.count_completed(stems)
            still_runs = batch_runner.any_alive(stems)

            st.title("📚 批量并行分析")
            st.progress(done_n / max(total_n, 1),
                        text=f"总进度：{done_n} / {total_n} 篇完成")
            st.divider()

            # 每篇的可折叠进度卡
            stem_to_info = {f["stem"]: f for f in todo_files}
            for stem in stems:
                pct, label = batch_runner.paper_progress(stem)
                state      = sm.load_state(stem) or {}
                status     = state.get("status", "pending")
                icon       = "✅" if status == "completed" else ("❌" if "❌" in label else "⏳")
                fname      = stem_to_info.get(stem, {}).get("name", stem)

                with st.expander(f"{icon} **{fname}** — {label}", expanded=False):
                    st.progress(pct)
                    if "❌" in label:
                        st.error(label)
                    elif status == "completed":
                        final = state.get("final_result", "")
                        st.caption(final[:400] + ("…" if len(final) > 400 else ""))
                        if st.button("查看完整报告", key=f"batch_view_{stem}"):
                            st.session_state["selected_stem"] = stem
                            for k in ("batch_files", "batch_parallel_started",
                                      "batch_parallel_stems", "batch_parallel"):
                                st.session_state.pop(k, None)
                            st.rerun()
                    else:
                        partial = state.get("partial_result", "")
                        if partial:
                            st.caption(partial[-300:])
                        elif status == "map_in_progress":
                            done_c = len(state.get("chunk_summaries", []))
                            total_c = state.get("total_chunks", "?")
                            st.caption(f"已完成 {done_c}/{total_c} 块的关键信息提取")

            # 全部完成
            if not still_runs and done_n == total_n:
                st.success("🎉 全部分析完成！")
                if st.button("✓ 完成，进入文献库"):
                    for k in ("batch_files", "batch_index", "batch_parallel",
                              "batch_parallel_started", "batch_parallel_stems"):
                        st.session_state.pop(k, None)
                    st.rerun()
            elif still_runs:
                # 自动轮询刷新
                time.sleep(2)
                st.rerun()

        # ══ 顺序模式 ══════════════════════════════════════════════════════
        else:
            batch_index = st.session_state.get("batch_index", 0)

            # 找下一个待分析的文件
            while batch_index < len(batch_files):
                if not batch_files[batch_index]["done"]:
                    break
                batch_index += 1
                st.session_state["batch_index"] = batch_index

            total_files = len(batch_files)

            if batch_index >= total_files:
                st.success("🎉 批量分析全部完成！")
                with st.expander("📋 已完成列表", expanded=True):
                    for i, f in enumerate(batch_files, 1):
                        st.write(f"{i}. ✅ {f['name']}")
                if st.button("✓ 完成"):
                    for k in ("batch_files", "batch_index", "batch_parallel"):
                        st.session_state.pop(k, None)
                    st.rerun()
            else:
                current = batch_files[batch_index]
                with st.spinner("读取 PDF…"):
                    full_text = extract_text_from_pdf(current["bytes"])
                sm.save_pdf(current["bytes"], current["name"])
                st.session_state.update({
                    "trigger_stem":     current["stem"],
                    "trigger_text":     full_text,
                    "trigger_pdf_bytes": current["bytes"],  # 用于 VLM 图表分析
                    "trigger_filename": current["name"],
                    "trigger_sha256":   current["sha256"],
                    "batch_analyzing":  True,
                })
                st.rerun()

    # ── 分析进行中（uploader 上方已渲染，内容接在 divider 后，布局不跳动）──
    if trigger_stem:
        trigger_text = st.session_state.get("trigger_text", "")
        filename     = st.session_state.get("trigger_filename", trigger_stem)

        # 批量顺序模式：显示总进度头
        if st.session_state.get("batch_analyzing"):
            batch_files = st.session_state.get("batch_files", [])
            batch_index = st.session_state.get("batch_index", 0)
            total_files = len(batch_files)
            st.caption(f"📚 批量分析：第 {batch_index + 1} / {total_files} 篇")
            st.progress((batch_index) / max(total_files, 1),
                        text=f"已完成 {batch_index}/{total_files} 篇")
            st.subheader(f"⏳ 正在分析：{filename}")
            st.divider()

        state = sm.load_state(trigger_stem)
        if state is None or state.get("status") == "pending":
            state = sm.create_state(trigger_stem, filename,
                                    len(chunk_text(trigger_text)),
                                    sha256=st.session_state.get("trigger_sha256",""))
            # 保存语言设置（顺序模式）
            result_lang = st.session_state.get("result_language", "zh-CN")
            sm.update_state(state, result_language=result_lang)

        if state.get("status") == "completed":
            st.session_state["selected_stem"] = trigger_stem
            for k in ("trigger_stem", "trigger_text", "trigger_filename"):
                st.session_state.pop(k, None)
            st.rerun()
        else:
            progress_bar = st.progress(0, text="准备中…")
            report_area  = st.empty()
            accumulated  = [state.get("partial_result", "")]

            def on_progress(step, current, total):
                pct = max(5, int(current / max(total, 1) * 90))
                progress_bar.progress(pct, text=step)

            try:
                trigger_pdf_bytes = st.session_state.get("trigger_pdf_bytes")
                for delta in analyze_paper(state, trigger_text, trigger_pdf_bytes, on_progress):
                    accumulated.append(delta)
                    report_area.markdown("".join(accumulated))

                progress_bar.progress(100, text="✅ 完成")

                # 批量模式：自动进入下一篇
                if st.session_state.get("batch_analyzing"):
                    batch_index = st.session_state.get("batch_index", 0)
                    st.session_state["batch_index"] = batch_index + 1
                    st.session_state.pop("batch_analyzing", None)
                    for k in ("trigger_stem", "trigger_text", "trigger_filename", "trigger_sha256"):
                        st.session_state.pop(k, None)
                    st.toast(f"✅ 完成 {batch_index + 1} 篇", icon="✅")
                    st.rerun()

                # 单个模式：正常完成
                st.session_state["selected_stem"] = trigger_stem
                st.session_state["just_completed"] = True   # 触发完成提醒
                for k in ("trigger_stem", "trigger_text", "trigger_filename"):
                    st.session_state.pop(k, None)
                st.rerun()

            except Exception as e:
                progress_bar.empty()

                # 批量模式：记录错误，继续下一篇
                if st.session_state.get("batch_analyzing"):
                    st.error(f"❌ 分析出错：{e}")
                    batch_index = st.session_state.get("batch_index", 0)
                    st.session_state["batch_index"] = batch_index + 1
                    st.session_state.pop("batch_analyzing", None)
                    for k in ("trigger_stem", "trigger_text", "trigger_filename", "trigger_sha256"):
                        st.session_state.pop(k, None)

                    if st.button("继续下一篇"):
                        st.rerun()
                else:
                    # 单个模式：显示错误，保留断点
                    st.error(f"❌ 分析出错：{e}\n\n进度已保存，下次上传同一文件可从断点续跑。")
