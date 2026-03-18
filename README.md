# Paper Agent

一个带 Web UI 的**目标导向文献阅读助手**。上传 PDF，获得与你研究目标深度对齐的结构化分析报告，并通过跨文献推理工具综合洞察。

基于 Streamlit + PyMuPDF + 任意 OpenAI 兼容 API 构建。

---

## 功能特性

**核心分析**
- 上传 PDF → LLM 自动生成结构化分析报告
- 长文献 Map-Reduce 策略（分块摘要 → 流式汇总），支持断点续传
- **直接拖放 PDF** 到上传区即可开始分析，支持同时拖入多篇
- 查看报告时工具栏一键 **📂 原文**，直接用系统默认 PDF 阅读器打开原始文件

**文献库**
- PDF 与报告永久存储
- **标签层级分类**：每篇报告 AI 自动生成若干标签；积累一定数量后，点击侧边栏「✨ 整理标签分类」，LLM 将所有零散标签归纳为 4–8 个一级类目，持久化保存；之后侧边栏以 radio 形式展示一级类目，选中后可进一步用二级子标签细化过滤
- 标题搜索、分页浏览、内联重命名与删除
- **阅读标记**：为每篇论文设置阅读状态（未读 / 在读 / 已读 / 精读🔥）与 0–5 星评分（⭐/🌟）
- **高级筛选**：按阅读状态、最低评分组合过滤论文列表
- **📝 我的笔记**：查看报告时可在右侧打开笔记面板，自由记录批注、想法、疑问；笔记独立保存为 `data/notes/mynotes/{stem}_mynote.md`，不影响 AI 生成的原始报告

**跨文献推理工具**
- **💡 Idea 综合器** — 从多篇论文中提炼可复用的创新思路
- **🗺️ 阅读路线图** — 根据当前阅读进展推荐下一步方向
- **⚡ 矛盾检测器** — 自动发现不同论文之间的相互矛盾结论
- **💬 论文问答** — 基于完整报告内容的多轮对话

**文献追踪**
- 从 PDF 中正则提取参考文献（零 token）
- AI 根据研究目标过滤相关文献，附一句话理由
- 解析 arxiv ID，一键下载到 `data/inbox/`
- 结果按论文粒度缓存，研究目标变更后自动失效

**⚙️ 设置页**
- 可视化编辑研究领域、研究目标、输出模板、章节提取配置
- 无需改代码，保存即生效

---

## 快速开始

### 1. 克隆项目并安装依赖

```bash
git clone https://github.com/your-username/paper-agent.git
cd paper-agent
pip install -r requirements.txt
```

### 2. 配置 API 凭证

```bash
cp .env.example .env
```

编辑 `.env`：

```env
OPENAI_API_BASE=https://api.openai.com/v1
OPENAI_API_KEY=sk-your-key-here
MODEL_NAME=gpt-4o
```

`OPENAI_API_BASE` 支持任意 OpenAI 兼容端点（OpenAI 官方、Azure、本地中转等）。
`MODEL_NAME` 不填时默认使用 `claude-sonnet-4-6`。

### 3. 启动

```bash
streamlit run app.py
```

浏览器访问 `http://localhost:8501`。

### 4. 填写你的研究目标

点击侧边栏 **⚙️ 设置**，填写以下配置：

| 文件 | 用途 |
|------|------|
| `00_domain.md` | 一行研究领域描述，嵌入所有 AI system prompt |
| `02_research_goal.md` | 研究目标，AI 分析每篇论文的核心参考依据 |
| `03_output_template.md` | 论文分析输出的 Markdown 模板，LLM 严格遵守 |
| `04_section_keys.json` | 逻辑字段名 → 模板章节标题的映射，供综合/矛盾检测使用 |

保存后立即生效，无需重启。

---

## 目录结构

```
paper-agent/
├── app.py                      # Streamlit 主入口与页面路由
├── core/
│   ├── config.py               # 读取用户配置文件
│   ├── pdf_parser.py           # PDF 文本提取与分块
│   ├── prompt_builder.py       # 构造 Single / Map / Reduce Prompt
│   ├── llm_client.py           # OpenAI 兼容调用（流式 / 非流式 / Map-Reduce）
│   ├── state_manager.py        # 状态 CRUD、PDF 存储、报告保存
│   ├── index_builder.py        # 自动重建 data/index.md
│   ├── idea_synthesizer.py     # Idea 综合 Prompt
│   ├── roadmap_builder.py      # 阅读路线图 Prompt
│   ├── contradiction_detector.py # 矛盾检测 Prompt
│   ├── paper_chat.py           # 多轮问答 messages 构造
│   ├── tag_organizer.py        # LLM 标签归类 → cache/TAG_TAXONOMY.json
│   ├── batch_runner.py         # 批量分析后台线程池
│   └── ref_scout.py            # 参考文献提取、AI 过滤、arxiv 下载
├── data/
│   ├── config/                 # 用户个性化配置（通过设置页编辑）
│   │   ├── 00_domain.md
│   │   ├── 02_research_goal.md
│   │   ├── 03_output_template.md
│   │   └── 04_section_keys.json
│   ├── pdfs/                   # 上传的原始 PDF（永久存储）
│   ├── inbox/                  # 文献追踪下载的待分析 PDF
│   ├── notes/
│   │   ├── papers/             # 论文分析报告 (.md)
│   │   ├── ideas/              # Idea 综合报告
│   │   ├── maps/               # 阅读路线图报告
│   │   ├── contradictions/     # 矛盾检测报告
│   │   ├── chats/              # 问答对话日志
│   │   ├── scout/              # 文献追踪报告
│   │   └── mynotes/            # 用户手写笔记 ({stem}_mynote.md)
│   ├── cache/
│   │   ├── *.json              # 每篇论文的分析状态（含阅读状态与评分）
│   │   └── refs/               # 每篇论文的参考文献缓存
│   └── index.md                # 文献知识库索引（自动生成）
├── docs/
│   └── 01_project_spec.md      # 项目规范与开发记录
├── scripts/
│   ├── rename_refs.py          # 将 arxiv_id.pdf 批量改名为论文真实标题
│   ├── backfill_sha256.py      # 为已有 PDF 补填 SHA256
│   └── migrate_data.py         # 将旧版 data_my/ 迁移为新目录结构 data_new/
├── requirements.txt
├── .env.example                # 环境变量示例
├── .gitignore
└── .env                        # API 凭证（不提交到 git）
```

---

## 配置说明

### 输出模板（`03_output_template.md`）

定义 LLM 为每篇论文生成报告时必须遵守的 Markdown 结构。可以自定义章节名称、增删章节、修改提示语言。

### 章节提取配置（`04_section_keys.json`）

将逻辑字段名（供 Idea 综合器、矛盾检测器内部使用）映射到模板中实际的章节标题关键词。修改模板章节标题后需同步更新此文件。

示例：
```json
{
  "motivation":  "核心痛点",
  "method":      "硬核原理解析",
  "inspiration": "灵感借用",
  "limitations": "作者承认的缺陷"
}
```

### 研究目标（`02_research_goal.md`）

普通 Markdown 文件。AI 在分析论文、生成综合/路线图/矛盾检测报告、过滤参考文献时均会读取此文件。建议写清楚你在研究什么、最关注论文的哪些方面。

---

## 文献追踪工作流

1. 在 **🔭 文献追踪** 中选择要扫描的论文
2. 点击「扫描参考文献」——正则提取，不消耗 token
3. 点击「AI 筛选」——LLM 按研究目标过滤相关文献
4. 结果列表中一键下载到 `data/inbox/`
5. 运行 `scripts/rename_refs.py` 将 `{arxiv_id}.pdf` 改名为论文真实标题
6. 在主界面批量上传 `inbox/` 中的 PDF

---

## 环境变量

| 变量 | 是否必填 | 默认值 | 说明 |
|------|----------|--------|------|
| `OPENAI_API_KEY` | 是 | — | API 密钥 |
| `OPENAI_API_BASE` | 是 | — | API 地址（兼容任意 OpenAI 格式） |
| `MODEL_NAME` | 否 | `claude-sonnet-4-6` | 使用的模型名称 |

---

## 依赖要求

- Python 3.10+
- Streamlit 1.35+
- PyMuPDF 1.24+
- openai 1.30+
- python-dotenv 1.0+
