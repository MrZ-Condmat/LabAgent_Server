# LabAgent 多用户架构审计（Task 1）

## 1. 审计范围与结论摘要

本审计依据当前仓库代码与长期计划 `LabAgent_多用户架构改造计划.md` 完成。审计只分析现状，不引入数据库、认证、FastAPI，也不改变现有业务行为。

当前系统是一个 Streamlit 单体应用：`lab_agent/web/app.py` 同时负责页面、样式、会话状态、业务编排、Agent 生命周期、文件读取和 LLM 调用。日报是实验室共享文件数据；三套聊天是浏览器会话内存数据。系统没有用户、角色、conversation ownership 或服务端持久化聊天模型。

最关键的迁移边界如下：

- 保留抓取、RSS 解析、论文评分、报告渲染和现有 Agent 的科研行为。
- 将聊天消息、conversation 元数据和长期记忆迁移到 PostgreSQL。
- `st.session_state` 只保留页面选择、筛选条件、当前 conversation ID 等临时 UI 状态。
- 在 Streamlit 与现有 Agent/工具之间增加 service 和 repository 层。
- 日报短期继续以文件为兼容输出，但 UI 应逐步通过 report service 读取。

## 2. 当前模块地图

| 区域 | 入口/模块 | 当前职责 |
| --- | --- | --- |
| Streamlit 入口 | `lab_agent/web/app.py:2052` | 直接调用 `main()`；启动命令为 `streamlit run lab_agent/web/app.py` |
| 页面路由 | `NAV_ITEMS`、`render_sidebar_navigation()`、`main()` | 通过 `active_nav` 在 Overview、ArXiv Daily、Journal Daily、Database、Logs 间切换 |
| 会话初始化 | `lab_agent/web/app.py:402-490` | 创建 LabAgent、日报 Agent、Chat 对象和聊天列表 |
| ArXiv Agent | `lab_agent/agents/arxiv_daily_agent.py` | 抓取、评分、生成/列出/读取/清除 ArXiv 报告 |
| Journal Agent | `lab_agent/agents/journal_daily_agent.py` | 加载期刊配置、抓取 RSS、日期过滤、评分、生成单刊及汇总报告 |
| Chat | `lab_agent/tools/arxiv_chat.py`、`journal_chat.py` | 保存论文上下文和内部 conversation history，直接调用 OpenAI-compatible API |
| Overview Chat | `lab_agent/web/app.py:1897-2010` | 未封装 Chat class；UI handler 直接建立 OpenAI client 并调用模型 |
| 报告存储 | `DailyReportGenerator`、`HighlightsReportGenerator` | 按日期写入和读取 HTML/JSON 文件 |
| 定时任务 | `scripts/run_daily_report.sh` → `scripts/generate_daily_report.py` | Cron 每日调用；工作日生成 ArXiv，每天生成 Journal，再生成 Highlights |
| Web 启动 | `scripts/run_web_app.sh` | 定位 Conda，读取 host/port，以 Streamlit 启动 `app.py` |
| 部署 | `deploy_to_server.ps1` | 打包并上传代码；保留服务器 `.env`、reports、logs；可重启 Streamlit |
| 配置 | `.env`、`lab_agent/config/*.json`、prompt 文本 | API、运行参数、模型、期刊源和提示词 |
| 可选 MCP | `lab_agent/mcp/` | 提供 ArXiv 报告工具；当前 Streamlit 主路径没有使用 |
| 测试 | `tests/test_deepseek.py` | 顶层脚本形式的真实 API smoke test，不是离线单元测试 |

### 2.1 启动和页面切换

1. `scripts/run_web_app.sh` 将工作目录切换到项目根目录，设置 `LABAGENT_PROJECT_ROOT` 与时区。
2. 脚本执行 `python -m streamlit run lab_agent/web/app.py`。
3. `main()` 加载 `.env`、构造 `Config`、应用全局 CSS，并初始化 session 对象。
4. `render_sidebar_navigation()` 读取/修改 `active_nav`。
5. `main()` 根据页面名调用 `overview_interface()`、`arxiv_daily_interface()`、`journal_daily_interface()`、`database_interface()` 或 `logs_interface()`。

### 2.2 Agent 与 Chat 初始化

- `LabAgent`、`ArxivDailyAgent`、`JournalDailyAgent` 都在 `main()` 中按 Streamlit session 初始化。
- ArXiv/Journal Agent 初始化时会创建有状态的 scraper、scorer 和 report generator。
- `ArxivChat` 与 `JournalChat` 也在 session 中初始化；对象内部持有 OpenAI client、论文列表和模型历史。
- Journal Agent 会在 `journal_feeds.json` 文件签名变化或旧对象缺少必要属性时重建。
- Chat 对象有兼容性检测，用于替换代码热更新后残留的旧 class instance。
- `LabAgent` 当前只保存配置、logger 和空的 `agents` 列表；Streamlit 后续没有实际使用该对象。

## 3. `st.session_state` 全面审计

仓库中的显式 `st.session_state` 访问全部位于 `lab_agent/web/app.py`。Streamlit widget 的 `key=` 也会隐式创建 session key，下面分别列出业务 key 与 widget key。

建议分类：

- **A**：继续保留在 `st.session_state`。
- **B**：后续迁移到 PostgreSQL。
- **C**：改为 service/application object，由应用层管理生命周期。
- **D**：可以删除或合并。

| Session key | 文件/位置 | 当前用途 | 初始化位置 | 修改位置 | 是否需要持久化 | 后续建议 |
| --- | --- | --- | --- | --- | --- | --- |
| `active_nav` | `app.py:34-64` | 当前页面 | `render_sidebar_navigation()` | 侧栏按钮 | 否 | **A**，纯 UI 状态 |
| `agent` | `app.py:417-418` | 保存基本为空壳的 `LabAgent` | `main()` | 仅初始化 | 否 | **D**，确认无调用后删除；若扩展则进入 **C** |
| `arxiv_agent` | `app.py:420-428` | ArXiv 业务对象 | `main()` | 初始化失败时设为 `None` | 否 | **C**，由 report/application service 管理 |
| `journal_agent` | `app.py:430-451` | Journal 业务对象 | `main()` | 配置变化/对象过期时重建 | 否 | **C**，由 report/application service 管理 |
| `journal_config_signature` | `app.py:432-448` | 检测 `journal_feeds.json` 变化 | Journal Agent 初始化后 | Agent 重建后 | 否 | **C**，配置 service/version 管理；短期可留 **A** |
| `arxiv_chat` | `app.py:453-462` | 有状态 ArxivChat 实例 | `main()` | 代码版本不兼容时重建 | 否 | **C**，改为无状态/请求级 chat service |
| `journal_chat` | `app.py:464-472` | 有状态 JournalChat 实例 | `main()` | 代码版本不兼容时重建 | 否 | **C**，改为无状态/请求级 chat service |
| `chat_messages` | `app.py:459,475-476` | ArXiv Chat 可见消息 | `main()` | context/new conversation/handler | **是** | **B**，迁移为 conversations/messages |
| `journal_chat_messages` | `app.py:469,477-478` | Journal Chat 可见消息 | `main()` | context/new conversation/handler | **是** | **B** |
| `overview_chat_messages` | `app.py:488-490` | Overview Chat 可见消息 | `main()` | new conversation/handler | **是** | **B** |
| `overview_chat_history` | `app.py:1897-1901` | 包含 system prompt 的模型输入历史 | 首次进入 Overview Chat | new conversation/handler/截断 | **是（用户/助手消息）** | **B**；system prompt 由 service 生成，不作为普通用户消息重复保存 |
| `arxiv_chat_context_key` | `app.py:458,479-480` | 标识 ArXiv report context | `main()` | context 加载/清空 | 需要保存其语义 | **B/A**：持久化 `conversation_type/report_date/paper_ids`，session 只存当前 conversation ID |
| `journal_chat_context_key` | `app.py:468,481-482` | 标识 Journal scope/date/context | `main()` | context 加载/清空 | 需要保存其语义 | **B/A**：持久化结构化 context metadata |
| `deepseek_assistant_error` | `app.py:485` | 旧实现遗留错误 key | 当前不初始化 | 每次 main 都 pop | 否 | **D** |
| `deepseek_assistant` | `app.py:486` | 旧实现遗留对象 key | 当前不初始化 | 每次 main 都 pop | 否 | **D** |
| `<prefix>_selected_report_date` | `app.py:120-195` | 选择的日报日期 | 日历按需写入 | 日期/清除按钮 | 否 | **A** |
| `<prefix>_calendar_month` | `app.py:120-195` | 日历显示月份 | 日历首次渲染 | 上/下月及选日 | 否 | **A** |
| `journal_daily_report_selector` | `app.py:1291-1335` | Summary 或具体期刊选择 | Streamlit selectbox | 用户选择 | 否 | **A** |

动态日历 prefix 包括：

- `arxiv`
- `journal_summary`
- `journal_<journal_slug>`

因此会产生例如 `arxiv_selected_report_date`、`journal_summary_calendar_month`、`journal_Physical_Review_Letters_selected_report_date` 等 key。

### 3.1 Widget 产生的临时 key

以下 key 由 Streamlit widget 自动进入 session state，全部属于 **A：临时 UI 状态**，无需进入数据库：

- `nav_<page>`
- `<prefix>_prev_month`、`<prefix>_next_month`、`<prefix>_calendar_day_<date>`、`<prefix>_clear_calendar_date`
- `<prefix>_older_report_selector`
- `open_arxiv_html_visible_<date>`、`open_journal_summary_html_visible_<date>`、`open_journal_html_visible_<slug>_<date>`
- `overview_today_highlights`
- `generate_arxiv_daily_compact`、`generate_journal_summary_compact`、`generate_journal_<slug>_compact`
- `suggestion_<index>`、`journal_suggestion_<scope>_<date>_<index>`、`overview_suggestion_<index>`
- `journal_new_conversation`、`overview_new_chat`
- `journal_chat_input`、`overview_chat_input`

ArXiv 的 New Conversation 与 chat input 没有显式 key，Streamlit 会依据 widget identity 管理相应状态；后续拆组件时应补充稳定且命名空间化的 key。

### 3.2 生命周期结论

- 普通 Streamlit rerun：当前 session 的上述对象和列表仍存在。
- context 日期/期刊变化：Chat class 的内部历史和 UI 消息列表被清空，并以新报告重建 system context。
- 新浏览器 session、session 失效或服务器重启：所有聊天、选择状态、Chat/Agent 对象全部丢失。
- 报告 HTML/JSON 位于文件系统，服务器重启后仍存在。
- session state 不是持久化层，也不能稳定代表一个真实用户身份。

## 4. 三套 Chat 审计

## 4.1 Overview Chat

| 项目 | 实际位置 |
| --- | --- |
| UI | `deepseek_assistant_interface()` |
| Handler | `handle_overview_chat_message()` |
| Chat class | 无 |
| 展示历史 | `overview_chat_messages` |
| 模型历史 | `overview_chat_history` |
| Context | 固定 system prompt；没有把日报 JSON/HTML 注入模型上下文 |
| LLM call | Handler 内直接构造 `OpenAI` 并调用 `client.chat.completions.create()` |

```mermaid
flowchart TD
    I[st.chat_input / suggestion] --> H[handle_overview_chat_message]
    H --> D[overview_chat_messages<br/>展示历史]
    H --> M[overview_chat_history<br/>system + 模型历史]
    M --> C[在 app.py 中创建 OpenAI client]
    C --> L[DeepSeek compatible API]
    L --> H
    H --> D
    H --> M
    D --> R[st.chat_message]
```

重要事实：Overview 页面会读取当日日报 summary 显示指标，但 Overview Chat 本身只使用固定 system prompt 和用户对话历史。它没有获得这些 report 数据，因此“询问今日日报”的回答没有代码层面的报告 grounding。

## 4.2 ArXiv Chat

| 项目 | 实际位置 |
| --- | --- |
| UI | `arxiv_chat_interface()` |
| Handler | `handle_chat_message()` |
| Chat class | `ArxivChat` |
| 展示历史 | `chat_messages` |
| 模型历史 | `ArxivChat.conversation_history` |
| Context | 指定日期报告 JSON 中的 `all_papers` |
| LLM call | `ArxivChat.chat()` |

```mermaid
flowchart TD
    F[reports/ArXiv_reports/date.json] --> G[ArxivDailyAgent._get_report]
    G --> P[json_data.all_papers]
    P --> S[ArxivChat.set_papers_context]
    S --> X[system prompt + paper summary]
    I[st.chat_input / suggestion] --> H[handle_chat_message]
    H --> U[chat_messages 展示历史]
    H --> A[ArxivChat.chat]
    X --> A
    A --> CH[conversation_history]
    CH --> L[DeepSeek compatible API]
    L --> A
    A --> U
    U --> R[st.chat_message]
```

## 4.3 Journal Chat

| 项目 | 实际位置 |
| --- | --- |
| UI | `journal_chat_interface()` |
| Handler | `handle_journal_chat_message()` |
| Chat class | `JournalChat(ArxivChat)` |
| 展示历史 | `journal_chat_messages` |
| 模型历史 | 继承的 `conversation_history` |
| Context | Summary 或具体期刊指定日期 JSON 中的 `all_papers` |
| LLM call | 继承的 `ArxivChat.chat()` |

```mermaid
flowchart TD
    F[Journal Summary 或 per-journal date.json] --> G[JournalDailyAgent.process_task get_report]
    G --> P[json_data.all_papers]
    P --> S[JournalChat.set_papers_context]
    S --> X[Journal prompt + paper summary]
    I[st.chat_input / suggestion] --> H[handle_journal_chat_message]
    H --> U[journal_chat_messages 展示历史]
    H --> A[JournalChat.chat]
    X --> A
    A --> CH[conversation_history]
    CH --> L[DeepSeek compatible API]
    L --> A
    A --> U
    U --> R[st.chat_message]
```

### 4.4 双重历史与有状态对象

三套 Chat 都存在双重历史：

- Overview：`overview_chat_messages` 与 `overview_chat_history`。
- ArXiv：`chat_messages` 与 `ArxivChat.conversation_history`。
- Journal：`journal_chat_messages` 与 `JournalChat.conversation_history`。

展示列表不含 system prompt；模型历史包含 system prompt。二者有合理的格式差异，但现在由 UI handler 手动同步，没有唯一权威数据源。模型历史超过 21 条时只保留 system message 加最近 20 条，展示历史不截断，因此长对话会出现“界面看得到、模型已经忘记”的差异。发生 LLM 异常时，用户消息已写入两份历史，助手消息不会写入；未来重试和消息状态需要明确建模。

`ArxivChat`/`JournalChat` 是明确的有状态对象，包含：

- `client`
- `system_prompt`
- `model_config`
- `conversation_history`
- `current_papers`
- `current_context_label`
- `config` 和 `logger`

`JournalChat` 继承全部状态，只覆盖 prompt、论文摘要格式和建议问题。

### 4.5 多用户串用判断

当前 Streamlit 通常为每个浏览器 session 创建独立的 session state，因此主 Web 路径中没有发现 module-level 的共享 Chat singleton，也没有发现 Streamlit cache。现有浏览器 session 之间一般不会直接共用 Chat 对象。

但这并不构成多用户安全隔离：

- 没有认证身份，session 无法映射到稳定 `user_id`。
- 没有 conversation ownership，无法安全恢复、列举或授权访问历史。
- 所有日报文件及生成操作是全局共享的。
- Chat 消息只在内存中，session 丢失即消失。
- `lab_agent/mcp/client.py` 存在 module-level `_mcp_client` singleton，内部持有工具/Agent 状态；当前 Streamlit 没有调用它，但未来接入 Web 请求前必须取消全局可变业务状态或证明线程/用户安全。

## 5. ArXiv / Journal Chat Context 审计

### 5.1 日期和报告选择

- `render_report_calendar()` 从 report generator 列出的 JSON 文件名得到可选日期。
- 未选择日期时，Chat 使用 `today_str()`。
- ArXiv 使用 `ArxivDailyAgent._get_report(active_date)`。
- Journal 根据 `journal_daily_report_selector` 调用 `get_summary_report` 或指定 journal 的 `get_report`。

### 5.2 Context 加载

报告读取同时返回 JSON 与 HTML；Chat 只使用 JSON 的 `all_papers`。`set_papers_context()` 把论文列表保存在 `current_papers`，将部分论文摘要拼入 system message，并清空旧 `conversation_history`。

ArXiv context key 格式：

```text
arxiv:<report_date>:<paper_count>
```

Journal context key 格式：

```text
<Summary 或 report selector>:<report_date>:<paper_count>
```

代码除比较 context key 外，还直接比较 `current_papers != all_papers`，因此相同数量但内容变化的报告仍会触发 context 重载。

### 5.3 为什么 context 改变要清空 conversation

模型历史的第一条 system message 包含当前报告的论文摘要。如果只替换日期/期刊而保留后续对话，旧回答和旧问题会继续引用上一份报告，造成错误归因。因此当前代码同时清空内部模型历史和可见消息列表。

未来 conversation 应保存结构化 metadata：

- `conversation_type`: `overview` / `arxiv` / `journal`
- `report_date`
- `report_type`: `arxiv_daily` / `journal_summary` / `journal_daily`
- `journal_slug`（适用时）
- `paper_ids`（优先稳定的 arXiv ID、DOI 或内部 article ID）
- `report_id` 或 report version/hash
- 可选的 context 构建版本、prompt version、model name

不建议把完整报告正文复制进每个 conversation；应通过稳定 report/article ID 重建 context，必要时保存发送给模型的 context snapshot/hash 以便审计。

## 6. 日报数据流

## 6.1 ArXiv Daily

```mermaid
flowchart LR
    A[arxiv.org/list/cond-mat/new] --> S[ArxivDailyScraper]
    S --> P[PaperScorer]
    P --> AG[ArxivDailyAgent]
    AG --> G[DailyReportGenerator]
    G --> J[reports/ArXiv_reports/date.json]
    G --> H[reports/ArXiv_reports/date.html]
    J --> UI[Streamlit metrics/report list]
    H --> UI
    J --> C[ArxivChat context]
```

- 输入：ArXiv cond-mat/new HTML 页面、评分 prompt、模型配置。
- 中间过程：`ArxivDailyScraper.fetch_daily_papers()` → `PaperScorer.batch_score_papers()` → `DailyReportGenerator.generate_daily_report()`。
- 输出：`reports/ArXiv_reports/YYYY-MM-DD.json` 与 `.html`。
- 已存在同日 JSON 时，Agent 直接读取缓存，不重新抓取/评分。
- JSON 是指标和 Chat 的主要结构化数据源；HTML 只用于展示。

## 6.2 Journal Daily

```mermaid
flowchart LR
    F[journal_feeds.json] --> R[RSSDailyScraper]
    RSS[Configured RSS feeds] --> R
    R --> D[按报告日及前一日过滤]
    D --> P[PaperScorer]
    P --> AG[JournalDailyAgent]
    AG --> PG[Per-journal DailyReportGenerator]
    PG --> PJ[reports/journal_slug_reports/date.json + html]
    PJ --> SG[Summary generator]
    SG --> SJ[reports/Journal_Summary_reports/date.json + html]
    PJ --> UI[Streamlit per-journal]
    SJ --> UI2[Streamlit summary]
    PJ --> C[JournalChat]
    SJ --> C
```

- 输入：`lab_agent/config/journal_feeds.json` 中的 RSS URL。
- 中间过程：RSS 解析 → 按 RSS published/updated 日期保留报告日和前一日 → 全部候选评分。
- 单刊 HTML 默认只展示 Priority 2/3；JSON 保留 Priority 1/2/3 和日期窗口统计。
- Summary 从每个单刊同日 JSON 聚合，写入 `reports/Journal_Summary_reports`。
- 文件名仍为 `YYYY-MM-DD.json/.html`；日期发现逻辑是扫描目录中的 `.json` 文件名并倒序排列。
- Streamlit 和 Journal Chat 同样以 JSON 为结构化主数据，HTML 为展示内容。

## 6.3 Highlights

```mermaid
flowchart LR
    A[ArXiv date.json] --> H[HighlightsReportGenerator]
    J[Journal Summary date.json] --> H
    H --> O[reports/Highlights_reports/date.json + html]
    O --> UI[Overview modal]
```

- Highlights 只合并 ArXiv 与 Journal Summary 中的 Priority 3 项。
- 定时脚本在两个主流水线后生成 Highlights。
- Overview 的 “Today's Highlights” 也以 `force=True` 重新生成同日 Highlights 文件；这属于页面读取动作中的共享写操作。

### 6.4 读取和重复解析

- `DailyReportGenerator.get_report()` 每次都从磁盘读取 JSON，并在 HTML 存在时读取 HTML。
- 同一 Streamlit rerun 中，Overview 指标、报告展示、ArXiv/Journal Chat 可能分别读取相同报告。
- 没有 cache、repository 或文件锁。
- JSON 是权威结构化源；HTML 是从相同数据生成的展示副本，但二者可能因中途写入失败或手工修改而不一致。

## 7. 现有模块保留/改造分类

| 模块 | 当前职责 | 与 Streamlit 耦合程度 | 是否有内部状态 | 后续建议 |
| --- | --- | ---: | ---: | --- |
| `ArxivDailyScraper` | 抓取并解析 ArXiv new 页面 | 无 | requests session | **A 基本保留**；增加接口、超时/重试和可测试 transport 注入 |
| `RSSDailyScraper` | 抓取并规范化 RSS entry | 无 | requests session | **A 基本保留** |
| `ArxivParser` | ArXiv API 查询与解析 | 无 | base URL | **A 基本保留**；当前主日报路径未使用 |
| `PaperScorer` | 加载 prompt/model 并调用评分模型 | 无 | Config、OpenAI client | **B 轻微改造**；由 service 注入 client/config，保留评分算法 |
| `DailyReportGenerator` | 组织优先级、生成 HTML/JSON、直接读写文件 | 无 | 路径和渲染配置 | **B 轻微到中等改造**；保留渲染，拆出 report repository/storage |
| `HighlightsReportGenerator` | 从两个 JSON 聚合 Priority 3 并写文件 | 无 | 路径 | **B 轻微改造**；输入改由 report service 提供 |
| `ArxivDailyAgent` | 编排抓取、评分、报告生成/读取 | UI 通过私有方法直接调用 | scraper/scorer/generator | **B 轻微改造**；公开稳定 service API，注入 repository |
| `JournalDailyAgent` | 编排多 RSS、日期过滤、单刊及汇总报告 | UI 直接 process_task | journals、scraper/scorer、多 generator | **B 轻微到中等改造**；拆 report service 和存储边界 |
| `ArxivChat` | 论文 context、模型历史、LLM 调用 | 由 Streamlit session 持有 | 高 | **C 明显重构**；改为 chat service，历史来自 repository |
| `JournalChat` | Journal 专用 context/prompt | 同上 | 高 | **C 明显重构**；复用统一 chat service/context builder |
| Overview Chat handler | UI 内直接维护历史并调用 LLM | 很高 | session lists | **C 明显重构** |
| `lab_agent/web/app.py` | UI、CSS、状态、编排、文件读取、LLM | 本体 | 高 | **C 渐进拆分** |
| `LabAgent` | 当前为空的顶层容器 | session 中存在但未使用 | config/logger/空列表 | **D 可删除或重新定义** |
| `lab_agent/mcp/` | 可选报告工具和全局 MCP client | 主 UI 未使用 | singleton/tool Agent | **D/B**：确认使用场景；若保留，移除全局用户相关状态 |
| Prompt/config JSON | 科研 prompt、模型和期刊源 | 无 | 文件配置 | **A 基本保留**，后续由配置 service 读取 |

这里的 A/B/C/D 与 session 表中的建议含义不同：本节 A=基本保留、B=轻微改造、C=明显重构、D=可能废弃。

## 8. `lab_agent/web/app.py` 职责拆解

当前文件约 2053 行，包含：

- `main()`：环境、页面配置、对象初始化和路由。
- Navigation：侧栏品牌、页面按钮、`active_nav`。
- Page functions：Overview、ArXiv Daily、Journal Daily、Database、Logs。
- Chat functions：三套 UI 与三个 handler、context 切换、建议问题。
- Styling：大段内联 CSS、页面 header、日历样式、sidebar JavaScript。
- Database iframe：直接嵌入外部聊天地址。
- Agent initialization：构造并初始化日报 Agent、Chat 和 LabAgent。
- Session initialization：UI、消息、context key、兼容性清理。
- Business orchestration：直接生成/读取日报、构造 OpenAI client、调用 Agent 私有方法。

未来建议的渐进归属：

| 当前逻辑 | 推荐位置 |
| --- | --- |
| `main()`、最薄路由 | `web/app.py` |
| Overview/ArXiv/Journal/Database/Logs 页面 | `web/pages/` |
| sidebar、calendar、report viewer、chat renderer | `web/components/` |
| CSS/HTML theme | `web/styles/` |
| chat 编排、context 构建、日报用例 | `services/` |
| conversations/messages/reports 数据访问 | `repositories/` |
| engine/session/models | `db/` |
| 后续 HTTP endpoint 与鉴权依赖 | `api/` |

拆分顺序应先建立 service/repository 边界，再移动 UI 文件。直接按页面切文件而不抽离业务调用，只会把耦合分散到多个文件。

## 9. Migration Risks

### HIGH

1. **不存在身份与 ownership**：没有 user、role 或资源所有权；无法实现私有 conversation，也无法阻止越权读取。
2. **聊天历史存在双份可变状态**：展示历史和模型历史依赖 handler 手动同步，截断策略已产生语义差异。
3. **Chat object 持有 mutable state**：`conversation_history/current_papers/current_context_label` 把用户数据、context 和 API client 混在长生命周期对象中。
4. **UI 直接操作业务对象**：Streamlit 调用 Agent 私有方法和 LLM client，未来无法在统一位置执行认证、授权、审计、事务或配额限制。
5. **共享报告文件被当作数据库**：列举、读取、覆盖、清除都直接操作公共目录；没有 schema、事务、版本、锁或 ownership。
6. **共享写操作缺少并发控制**：Cron 与多个用户手动 Generate/Highlights 可能同时写同名 JSON/HTML，存在部分写入、覆盖和读取中间状态风险。
7. **未认证用户可触发成本和共享数据变更**：当前只要能访问 Streamlit，就能看到共享报告并触发 Generate；后续必须由应用层做 admin 授权。

### MEDIUM

1. **rerun/session 生命周期**：rerun 保留对象，但新 session、断线超时、代码更新和服务器重启会丢失聊天；兼容检测只能处理少数 stale object 情况。
2. **事件循环适配**：`nest_asyncio.apply()` 与多处 `asyncio.run()` 混在 Streamlit UI 中，未来引入 FastAPI/异步数据库后容易出现循环和阻塞边界问题。
3. **路径耦合**：Linux 无显式配置时 `project_root()` 默认 `/data/zmr/projects/labAgent_Server`；本地/测试依赖工作目录和环境变量。
4. **配置耦合**：多个类自行读取 `.env`、models.json 和 prompt；难以按 request 注入配置或测试替身。
5. **已有 DATABASE_URL 具有误导性**：`Config` 和 `.env.example` 声明 SQLite 默认值，但项目没有实际数据库层，也没有任何代码使用该 URL。
6. **测试覆盖不足**：唯一 test 文件会调用真实 LLM；没有离线测试覆盖 session 生命周期、context 隔离、report repository 或并发行为。
7. **Overview Chat 未加载日报 context**：页面文案允许询问报告，但模型输入不包含报告数据，容易产生无依据回答。
8. **MCP singleton**：当前非主路径，但 `_mcp_client` 是全局可变实例；未来暴露给多用户前必须处理隔离和并发。

### LOW

1. CSS、HTML 卡片和页面布局与数据层无关，可在迁移期间保留。
2. Database iframe 是独立展示集成；后续认证一致性与第三方数据策略需要单独设计，但不阻塞数据库基础层。
3. 部分历史兼容代码和未暴露的 clear/generate helper 可在功能基线稳定后清理。

## 10. 推荐代码层

```text
lab_agent/
├── db/
│   ├── engine.py
│   ├── session.py
│   └── models/
├── repositories/
│   ├── users.py
│   ├── conversations.py
│   ├── messages.py
│   └── reports.py
├── services/
│   ├── auth_service.py
│   ├── chat_service.py
│   └── report_service.py
├── agents/
├── tools/
├── api/
└── web/
    ├── app.py
    ├── pages/
    ├── components/
    └── styles/
```

| 层 | 应该做什么 | 不应该做什么 |
| --- | --- | --- |
| `web/` | 渲染、收集输入、保存临时 UI key、调用 service | 直接 SQL、直接构造 LLM client、直接读写报告文件 |
| `api/` | 后续 FastAPI 路由、请求/响应 schema、认证依赖 | 保存业务状态、直接实现科研算法 |
| `services/` | 用例编排、授权、context 构建、事务边界 | 依赖 Streamlit widget、拼接 SQL |
| `repositories/` | 用户范围的数据访问和 ownership filter | 调用 LLM、生成 UI |
| `db/` | engine/session/model/迁移基础 | 页面逻辑、科研流程 |
| `agents/` | 抓取→评分→报告等科研用例 | import Streamlit/FastAPI、保存用户 session |
| `tools/` | 可复用的抓取、解析、评分、渲染、LLM adapter | 维护跨请求的用户 conversation 权威状态 |

FastAPI 后续应与 Streamlit 同处 service 层之上，而不是包裹或复制 Agent 逻辑。

## 11. 架构图

### Current Architecture

```mermaid
flowchart TD
    B[Browser] --> ST[Streamlit app.py]
    ST --> SS[st.session_state]
    SS --> AO[Agent / Chat objects]
    ST --> OA[Overview direct OpenAI call]
    ST --> AA[ArxivDailyAgent]
    ST --> JA[JournalDailyAgent]
    ST --> AC[ArxivChat / JournalChat]
    AA --> T[Scrapers / Scorer / Report generators]
    JA --> T
    AC --> L[DeepSeek / OpenAI compatible API]
    OA --> L
    T --> L
    T --> RF[Shared HTML + JSON report files]
    RF --> ST
    RF --> AC
    CR[Cron] --> SH[run_daily_report.sh]
    SH --> CLI[generate_daily_report.py]
    CLI --> AA
    CLI --> JA
    CLI --> HG[HighlightsReportGenerator]
    HG --> RF
```

### Intermediate Target Architecture

```mermaid
flowchart TD
    B[Browser] --> UI[Streamlit UI]
    UI --> S[Service Layer]
    API[Future FastAPI] --> S
    S --> AUTH[Auth / Authorization]
    S --> R[Repositories]
    R --> PG[(PostgreSQL)]
    S --> AG[Existing Research Agents]
    AG --> TL[Scrapers / Scorers / Report Tools]
    TL --> L[LLM / External Sources]
    SCH[Cron / Scheduler] --> RS[Report Service]
    RS --> AG
    RS --> R
    RS --> FS[HTML/JSON compatibility output]
```

## 12. 迁移总表

| 当前组件 | 当前职责 | 当前状态位置 | 用户相关性 | 后续目标层 | 是否需要修改 | 风险等级 |
| --- | --- | --- | --- | --- | --- | --- |
| `app.py` | UI、状态、编排、LLM、文件读取 | session + 进程 | 高 | `web/` + `services/` | 是，渐进拆分 | HIGH |
| Overview Chat | 通用研究助手 | 两份 session list | 私有 | `chat_service` + conversation/message repository | 是 | HIGH |
| ArXiv Chat | 基于报告的论文问答 | session list + Chat object | 私有 conversation；共享 report | chat service + DB metadata | 是 | HIGH |
| Journal Chat | 基于 summary/单刊报告问答 | session list + Chat object | 私有 conversation；共享 report | chat service + DB metadata | 是 | HIGH |
| `ArxivDailyAgent` | ArXiv 抓取评分报告 | session object / CLI object | 共享 | report service/agent | 轻微 | MEDIUM |
| `JournalDailyAgent` | RSS 抓取评分汇总 | session object / CLI object | 共享 | report service/agent | 轻微到中等 | MEDIUM |
| Report generators | HTML/JSON 生成和文件 CRUD | 对象 + filesystem | 共享 | tool + report repository/storage | 是，拆存储 | MEDIUM |
| Report filesystem | 当前共享日报数据库 | `reports/**` | 共享 | PostgreSQL + 文件兼容输出 | 是 | HIGH |
| Configuration | env、JSON、prompt | 环境/文件 | 全局 | config service/settings | 轻微 | MEDIUM |
| Cron | 每日 pipeline 调度 | 用户 crontab + shell | 管理员/共享 | 继续保留，调用 report service | 轻微 | LOW |
| Deployment | 打包、上传、可选重启 | PowerShell + shell | 管理员 | 继续保留并增加 DB migration/health gate | 后续修改 | MEDIUM |
| Tests | 真实 DeepSeek smoke script | 无隔离 fixture | 全局 | offline unit + integration tests | 是 | HIGH |
| `LabAgent` | 空顶层容器 | session object | 无 | 删除或 application container | 可选 | LOW |
| MCP client/tools | 可选报告工具 | module singleton + Agent | 潜在多用户 | service adapter | 使用前修改 | MEDIUM |
| Database iframe | 外部聊天 UI | 外部服务 | 未与本系统用户关联 | `web/pages/database.py`，后续明确 SSO/数据边界 | 轻微 | LOW/MEDIUM |

## 13. Recommended Next Step

### Task 2：在不改变当前业务行为的情况下，引入 PostgreSQL 基础配置层

当前 `Config.database_url` 和 `.env.example` 已经有 `DATABASE_URL=sqlite:///lab_agent.db`，但没有数据库实现。Task 2 应先消除“配置看似存在、实际未使用”的状态，并建立惰性、可测试的 PostgreSQL 基础设施；不要迁移聊天或日报。

建议只修改/新增：

```text
requirements.txt
.env.example
lab_agent/utils/config.py
lab_agent/db/__init__.py
lab_agent/db/engine.py
lab_agent/db/session.py
tests/test_db_config.py
tests/test_db_session.py
```

建议依赖：

```text
SQLAlchemy>=2.0,<3.0
psycopg[binary]>=3.1,<4.0
```

Alembic 可以在紧随其后的独立任务中加入，避免 Task 2 同时承担基础连接、模型和 migration 三类变化。

建议环境变量：

```text
DATABASE_URL=postgresql+psycopg://labagent:change_me@localhost:5432/labagent
DATABASE_POOL_SIZE=5
DATABASE_MAX_OVERFLOW=5
DATABASE_POOL_TIMEOUT=30
DATABASE_ECHO=false
```

实施约束：

1. 不在 module import 时连接数据库；engine 应惰性创建。
2. 不让 `app.py`、Agent 或 report pipeline 在 Task 2 强制依赖数据库可用。
3. 不增加 models，不迁移 conversation/report，不接入 Streamlit。
4. 不把真实凭据写入仓库；`.env.example` 只放 placeholder。
5. engine/session API 不 import Streamlit 或 FastAPI。
6. 配置测试不连接真实数据库；使用 mock 验证 URL、pool 参数和 session 生命周期。
7. 可增加显式、opt-in 的数据库连接 smoke test，但默认 test suite 必须离线运行。

Task 2 验收标准：现有 Streamlit 与日报脚本行为不变，静态检查通过，离线数据库基础层测试通过，未配置/未启动 PostgreSQL 时现有应用仍可启动。

## 14. 验证策略与测试现状

仓库 README 与 `TESTING_GUIDE.md` 都给出了 `python -m py_compile` 静态检查。该检查不调用 API、不抓取数据、不生成日报，适合本任务。

`tests/test_deepseek.py` 在 import/执行时会直接建立 OpenAI client 并发送真实 chat completion，因此本次不运行。当前没有可安全运行的离线 pytest test case。

审计完成后应执行：

```text
python -m py_compile lab_agent/web/app.py
  lab_agent/agents/arxiv_daily_agent.py
  lab_agent/agents/journal_daily_agent.py
  lab_agent/tools/paper_scorer.py
  lab_agent/tools/arxiv_chat.py
  lab_agent/tools/journal_chat.py
  scripts/generate_daily_report.py
git diff --check
```

