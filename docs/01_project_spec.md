# 角色与任务
你是一个资深的 Python 全栈工程师和 AI 开发者。我们将一起开发一个"带 Web UI 的目标导向文献阅读助手"。
请仔细阅读本规范，并严格按照步骤进行开发。

# 注意事项：
绝对不要读取或分析 data/ 目录下的任何文件。你的任务只负责编写代码，PDF 的解析和总结工作由你写出来的 Python 程序调用 API 来完成，而不是由你（Claude Code 本身）来读。

# 技术栈选型
- 前端框架: Streamlit 1.35+（用于文件上传、进度展示、Markdown 渲染）
- 后端逻辑: Python 3.10+
- PDF 解析: PyMuPDF（用于提取纯文本）
- LLM 接口: 官方 `openai` 包（注意：兼容第三方中转 API）
- 环境变量: `python-dotenv`

# 核心系统配置（极为重要）
1. **API 配置**：必须使用 `.env` 文件读取凭证。
   - `OPENAI_API_BASE`：OpenAI 兼容的 API 地址（`.env` 读取）
   - `OPENAI_API_KEY`：从 `.env` 读取
   - 模型: 通过 `MODEL_NAME` 环境变量指定，默认 `claude-sonnet-4-6`
2. **长 PDF 处理策略**：Map-Reduce。用 PyMuPDF 提取全文，按 12,000 字符分块；
   每块非流式摘要（Map），最后流式汇总生成最终报告（Reduce）。
   阈值 `DIRECT_THRESHOLD=24000` 字符以内直接一次分析。
3. **输出长度**：`max_tokens=16000`，超时 `httpx.Timeout(read=300s)`。

# 目录结构
```
paper-agent/
├── app.py                      # Streamlit 主入口，页面路由与 UI
├── core/                       # 后端逻辑模块
│   ├── config.py               # 集中读取用户配置
│   ├── pdf_parser.py           # PDF 提取、分块、元信息
│   ├── prompt_builder.py       # 构造 Single / Map / Reduce 三种 Prompt
│   ├── llm_client.py           # OpenAI 兼容调用，流式/非流式/Map-Reduce 主逻辑
│   ├── state_manager.py        # 状态 CRUD、PDF 永久存储、报告保存、Library 查询
│   ├── index_builder.py        # 重建 index.md（论文表格 + 标签索引）
│   ├── idea_synthesizer.py     # 构造 Idea 综合 Prompt
│   ├── roadmap_builder.py      # 构造阅读路线图 Prompt
│   ├── contradiction_detector.py # 构造矛盾检测 Prompt
│   ├── paper_chat.py           # 构造多轮论文问答 messages 列表
│   ├── tag_organizer.py        # 标签归类：LLM 归纳一级类目 → 持久化 TAG_TAXONOMY.json
│   ├── batch_runner.py         # 批量并行分析后台线程池（daemon threads）
│   └── ref_scout.py            # 文献追踪：正则提取参考文献 + AI 过滤 + arxiv 下载
├── data/
│   ├── config/                 # 用户个性化配置（通过设置页编辑，随 git 提交模板）
│   │   ├── 00_domain.md        # 研究领域描述（一行），供所有 system prompt 插值
│   │   ├── 02_research_goal.md # 研究目标，AI 分析时的核心参考依据
│   │   ├── 03_output_template.md # 论文分析输出模板，LLM 严格遵守
│   │   └── 04_section_keys.json  # section 标题→逻辑 key 映射，供 idea/contradiction 提取
│   ├── pdfs/                   # 上传的原始 PDF 永久存储
│   ├── inbox/                  # 文献追踪自动下载的待分析 PDF（按 arxiv_id 命名）
│   ├── notes/
│   │   ├── papers/             # 论文分析报告 MD       (YYYY-MM-DD_{stem}.md)
│   │   ├── ideas/              # Idea 综合报告 MD
│   │   ├── maps/               # 阅读路线图 MD
│   │   ├── contradictions/     # 矛盾检测报告 MD
│   │   ├── chats/              # 多轮问答对话日志 MD
│   │   ├── scout/              # 文献追踪报告 MD
│   │   └── mynotes/            # 用户手写笔记 ({stem}_mynote.md)
│   ├── cache/
│   │   ├── *.json              # 每篇论文的分析状态 JSON   ({stem}.json)
│   │   ├── TAG_TAXONOMY.json   # 标签归类持久化
│   │   ├── REF_SCOUT_CACHE.json # 文献追踪扫描缓存（兼容旧版）
│   │   └── refs/               # 每篇论文的参考文献缓存 JSON（{stem}.json / {stem}_filtered.json）
│   └── index.md                # 全局文献知识库索引（自动生成）
├── docs/
│   └── 01_project_spec.md      # 本文件：项目规范与开发记录
├── scripts/
│   ├── rename_refs.py          # 将 inbox/ 中 arxiv_id.pdf 改名为论文标题
│   ├── backfill_sha256.py      # 为已有 PDF 补填 SHA256
│   └── migrate_data.py         # 将旧版 data_my/ 迁移为新目录结构 data_new/
├── requirements.txt
├── README.md
├── .env.example                # 环境变量示例
├── .gitignore
└── .env                        # API 凭证（不提交）
```

# 核心业务流
1. 用户在「上传 & 分析」页面上传 PDF。
2. PDF 原文永久保存至 `data/pdfs/`。
3. 后端提取纯文本，在 `data/cache/` 创建状态文件（含热启动字段）。
4. 读取 `data/config/02_research_goal.md` 和 `data/config/03_output_template.md` 拼接 Prompt，发送给 LLM。
5. 流式输出报告；每 300 字符将中间结果写回状态文件（`partial_result`），同时逐 delta flush 到 note 文件。
   Map 阶段每完成一块即持久化 `chunk_summaries`，支持断点续传。
6. 分析完成后：报告保存至 `data/notes/papers/`，状态置为 `completed`，自动重建 `index.md`。
7. UI 弹出 toast 完成提醒，工具栏出现「上传下一篇」按钮。
8. 「文献库」侧边栏读取所有 `completed` 状态，展示论文列表，可标签过滤、搜索、点击查看完整报告。

# UI 设计规范
- 浅色主题（Streamlit 默认，不覆盖全局颜色）
- 侧边栏 = 文献库：
  - 顶部：**标签分类导航**（radio 一级类目单击即过滤，选中后展示二级 multiselect）
  - 搜索框：仅按标题搜索（`custom_name` / `_first_heading` / `pdf_filename`，不搜全文）
  - 论文列表（分页，每页 10 篇）+ 综合报告 / 路线图 / 矛盾 / 对话（始终显示，不受标签过滤影响）
  - 所有条目支持 ✏️ 内联重命名 + 🗑 删除
  - 工具按钮区：💡 Idea 综合器 · 🗺️ 阅读路线图 · ⚡ 矛盾检测器 · 💬 论文问答 · 🔭 文献追踪
- 主区六种模式（路由优先级从上到下）：
  1. F1/F2：论文问答（选论文 → 对话界面）
  2. C：Idea 综合器
  3. D：阅读路线图
  4. E：矛盾检测器
  5. F：**文献追踪**
  6. G：**⚙️ 设置**（编辑领域描述/研究目标/输出模板/章节提取配置）
  7. A：查看已有报告（selected_stem）
  8. B：上传 & 分析（默认）

# 模块划分
| 文件 | 职责 |
|---|---|
| `app.py` | Streamlit 主入口，页面路由与 UI |
| `core/pdf_parser.py` | PDF 提取、分块、元信息 |
| `core/config.py` | 集中读取用户配置：`get_domain()`（领域描述）/ `get_section_keys()`（章节提取映射），带默认值兜底 |
| `core/prompt_builder.py` | 构造 Single / Map / Reduce 三种 Prompt |
| `core/llm_client.py` | OpenAI 兼容调用，流式（`stream_analysis`）/ 非流式（`call_once`）/ Map-Reduce 主逻辑 |
| `core/state_manager.py` | 状态 CRUD（含 `delete_state`）、PDF 永久存储、报告保存、Library 查询、notes 子目录管理 |
| `core/index_builder.py` | 重建 index.md（论文表格 + 标签索引） |
| `core/idea_synthesizer.py` | 构造 Idea 综合 Prompt |
| `core/roadmap_builder.py` | 构造阅读路线图 Prompt |
| `core/contradiction_detector.py` | 构造矛盾检测 Prompt |
| `core/paper_chat.py` | 构造多轮论文问答 messages 列表（含完整 `final_result` 无截断 + 研究目标注入） |
| `core/tag_organizer.py` | 标签归类：提取所有标签 → LLM 归纳一级类目 → 持久化 TAG_TAXONOMY.json |
| `core/batch_runner.py` | 批量并行分析后台线程池（daemon threads，跨 rerun 保持存活） |
| `core/ref_scout.py` | **文献追踪**：正则提取参考文献 + 批量 AI 过滤 + arxiv ID 解析 + 缓存持久化 |

# 已完成阶段
- [x] 阶段 1：PDF 上传 + 纯文本提取（无 LLM）
- [x] 阶段 2：LLM 接入 + 流式输出 + 自动保存
- [x] 阶段 3：UI 美化 + 永久存储 + 热启动续传 + Library 页面
- [x] 阶段 4：Map 并行化 + 流式增量写文件 + 标签云过滤 + 隐藏转圈图标
- [x] 阶段 5：index.md 自动重建 + 分析完成 toast 提醒 + 「上传下一篇」按钮
- [x] 阶段 6：Idea 综合器 + 阅读路线图 + 矛盾检测器（跨文献推理工具集）
- [x] 阶段 7：notes 子目录重构（papers/ideas/maps/contradictions/chats）+ 多轮论文问答（💬 论文问答，流式回答 + 对话历史持久化）+ **📝 我的笔记**（查看报告时右侧面板自由记录批注，保存至 `mynotes/{stem}_mynote.md`，不覆盖 AI 报告）
- [x] 阶段 8：批量上传分析（📚 批量上传模式，自动跳过已分析，顺序处理未完成论文，支持错误恢复，详细进度显示，可选并行处理）
- [x] 阶段 9：标签分层归类（✨ 整理标签分类，LLM 将零散标签归纳为 4~8 个一级类目，持久化 TAG_TAXONOMY.json）
- [x] 阶段 10：侧边栏全面升级
  - 论文列表分页（每页 10 篇，◀ / ▶ 翻页，自动跳到当前选中论文所在页）
  - 所有侧边栏条目（论文 / 综合报告 / 路线图 / 矛盾检测 / 对话）统一支持 ✏️ 内联重命名 + 🗑 删除
  - `delete_state()` 同时删除 JSON 状态文件 + MD 笔记文件
  - 论文问答：去掉 `final_result` 2000 字符截断（完整传入）；注入研究目标；回答后 `st.rerun()` 修复"没反应"问题
  - 读超时从 90s 提升至 300s
- [x] 阶段 11：标签系统重构
  - 侧边栏标签过滤改为 **radio 一级类目**（单击即联动，不再藏在 expander 里）
  - 选中类目后显示二级子标签 multiselect，不选=该类全部文献
  - **标签过滤只作用于论文，综合报告/路线图/矛盾/对话始终显示**
  - 修复 `#` 前缀不匹配 bug
  - 搜索框改为**仅按标题搜索**
- [x] 阶段 12：功能页论文选择器升级
  - 综合器 / 矛盾检测 / 聊天：均只展示 `type==paper` 的原始论文
  - 新增 `_cat_paper_selector(papers, key)` 通用组件：一级类目下拉 + 全选/取消全选 + 可滚动复选框列表
  - 路线图：新增类目 selectbox，可按类目聚焦生成
  - `llm_client.py` 新增 `call_once()` 非流式单次调用公共函数
- [x] 阶段 13：文献追踪（🔭 文献追踪）
  - `core/ref_scout.py`：正则提取参考文献（零 token）+ 批量 AI 过滤（每批 50 条，一次调用，极省 token）+ arxiv ID 解析
  - AI 过滤参考研究目标，输出相关文献列表 + 一句话理由
  - **每篇论文参考文献提取结果缓存** `data/cache/refs/{stem}.json`，避免重复解析 PDF
  - **每篇论文 AI 过滤结果缓存** `data/cache/refs/{stem}_filtered.json`，含 `goal_hash` 字段；研究目标变更时自动失效
  - 结果持久化至 `data/cache/REF_SCOUT_CACHE.json`（兼容旧版）
  - UI：论文选择器 → 扫描 → AI 筛选 → 结果列表（✅已在库/⬇可下载/○无ID）→ 一键下载（SHA 去重静默跳过）
  - `scripts/rename_refs.py`：将 `data/inbox/` 中 `{arxiv_id}.pdf` 批量改名为论文真实标题（调用 arxiv API，纯标准库）
- [x] 阶段 13 Bug 修复：
  - 展示时校验 goal_hash，过期缓存给出 warning 提示
  - Bug A：删除论文时清理全局 scout 缓存残留
  - Bug B：删除论文后检测对话上下文残缺并告知用户
  - Bug C：Windows 下 index.md 链接失效（`os.path.relpath()` fallback）
  - Bug D：`batch_runner` `_errors` 字典加线程锁
  - Bug E：AI 过滤批次失败 warning 展示
- [x] 阶段 13 体验优化：侧边栏去时间戳、标题截断上限提升至 50 字符；矛盾检测器首 token 冻结（spinner 包住等待阶段）
- [x] 阶段 14：多用户配置支持（⚙️ 设置页）
  - 新增 `data/config/` 目录，存放 4 个用户配置文件（原在 `docs/`）
  - `core/config.py` 集中提供 `get_domain()` / `get_section_keys()`，读取失败时内置默认值兜底
  - 4 个 core 模块 system prompt 领域词改为 `get_domain()` 动态读取；`_SECTION_PATTERNS` 从 `get_section_keys()` 动态生成
  - 设置页：可视化编辑 4 个配置文件；JSON 格式校验通过后才写盘；保存即生效，无需重启
  - 开源适配：`data/config/` 随 git 提交（含通用示例内容），真实用户数据在本地不上传
- [x] 阶段 15：阅读标记与评分系统
  - 每篇论文 JSON 状态新增 `read_status`（未读/在读/已读/精读）和 `star_rating`（0-5）字段，旧数据用 `.get()` 默认值兜底，无需迁移
  - 侧边栏 `⋮` 菜单新增「阅读状态 + 评分」编辑区，「💾 保存状态」一键写入 JSON
  - 查看报告主区域（mode A）工具栏下方新增快速设置行：阅读状态 selectbox + 评分 selectbox，选择即自动保存、立即刷新
  - 侧边栏论文条目标题后追加状态图标（📖 在读 · ✅ 已读 · 🔥 精读）与评分（⭐×N，满分显示 🌟×5）
  - 侧边栏搜索框下方新增「🔽 筛选（状态·评分）」折叠面板：阅读状态多选 + 最低评分下拉，与标签过滤、搜索叠加生效
  - 视觉细节：星级使用 ⭐ emoji（金黄色），5 星满分改为 🌟×5；精读图标由 🔍 改为 🔥（更突出）
- [x] 阶段 16：一键打开原始 PDF
  - 查看报告工具栏新增「📂 原文」按钮（工具栏扩展为 6 列）
  - 点击后调用系统默认 PDF 阅读器打开 `data/pdfs/` 中的原始文件
  - PDF 不存在时按钮自动置灰并显示提示；跨平台支持（Windows `os.startfile` / macOS `open` / Linux `xdg-open`）
- [x] 阶段 17：合并上传模式 + 拖放增强
  - 删除「单个上传 / 批量上传」切换按钮，始终使用多文件上传控件（`accept_multiple_files=True`，同时支持单篇和多篇）
  - 上传区域 CSS 增强：蓝色虚线边框 + 浅蓝渐变背景 + hover 高亮，最小高度 160px，方便从文件管理器直接拖入 PDF
  - 删除切换按钮后残留的第二条 `st.divider()`，消除 UI 上连续两条横线的视觉问题

# 性能与体验细节
- Map 阶段使用 `ThreadPoolExecutor(max_workers=4)` 并行调用，N 块耗时 ≈ 1 块
- 流式输出时每个 delta 立即 `nf.write(delta); nf.flush()`，文件与屏幕同步增长
- 右上角运行指示器通过 CSS 隐藏（`[data-testid="stStatusWidget"]`）
- 标签从报告"🏷️ 标签"行正则提取；`_tags()` 返回不含 `#` 的 tag，过滤时与 taxonomy 统一 lstrip("#") 比较
- 中断检测：启动时扫描 cache/ 目录，发现未完成任务展示恢复横幅
- 读超时设为 300s（`httpx.Timeout(read=300.0)`），避免长问答/大报告生成超时
- 阅读状态/评分字段缺失时用 `.get("read_status","未读")` / `.get("star_rating",0)` 兜底，兼容所有旧版 cache JSON
- 文献追踪：参考文献扫描使用正则（零 token），AI 过滤每批约 1500 token

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 后续功能建议（待讨论，暂不落实）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 优先级高（直接服务于 idea 产出）

1. **研究空白探测 (Gap Detector)**
   - 扫描所有笔记的"局限性"与"未来工作"字段
   - 聚类相似的"抱怨点"，输出一张"无人涉足的研究空白地图"

2. **假设生成器 (Hypothesis Generator)**
   - 给定研究目标，结合已有笔记，自动生成若干可验证的科研假设

## 优先级中（科研工程支撑）

3. **Related Work 草稿生成**
   - 给定论文标题和摘要，从笔记库中检索相关文献
   - 自动生成 Related Work 段落初稿（含引用占位符）

4. **方法对比表 (Method Comparison Table)**
   - 选定多篇笔记，自动提取并对齐：方法/数据集/指标/局限性
   - 生成 LaTeX 或 Markdown 对比表

## 优先级低（锦上添花）

5. **每日科研摘要 (Daily Digest)**
   - 定时任务：每天汇总昨日新增笔记的核心结论

6. **知识图谱可视化**
   - 以论文为节点，以"方法继承/数据集共用/相互引用"为边
   - 用 pyvis 渲染交互式知识图谱
