# Lab Agent 修改历史

## 2026-06-25

本文件记录当天对 `G:\labAgent\labAgent` 项目的主要修改、设计决策和验证结果。敏感信息如真实 API key 不在本文记录。

### 1. 项目结构与功能梳理

- 查看并梳理了当前 Lab Agent 项目的整体结构：
  - `lab_agent/agents/`：各类 Agent，例如 ArXiv Daily Agent、后续新增的 Journal Daily Agent。
  - `lab_agent/tools/`：可复用工具模块，例如论文抓取、RSS 解析、打分、报告生成、聊天工具。
  - `lab_agent/web/app.py`：Streamlit 网页前端入口。
  - `lab_agent/config/`：模型、提示词、RSS 源等配置。
  - `scripts/`：命令行和计划任务入口。
  - `reports/`：日报 HTML/JSON 输出目录。
- 确认原有 ArXiv Daily 的核心流程为：
  1. 从 arXiv cond-mat/new 抓取论文列表。
  2. 调用评分模型对论文打分。
  3. 生成 JSON 和 HTML 日报。
  4. 在 Streamlit 前端展示报告，并支持 Chat About Papers。

### 2. ArXiv HTML 日报标题链接化

- 修改 `lab_agent/tools/daily_report_generator.py`：
  - 将 HTML 报告中每篇论文标题从纯文本改为条件链接。
  - 当 `paper.url` 存在时，标题渲染为可点击的 `<a>`，跳转到对应文章摘要页。
  - 当 `paper.url` 不存在时，标题仍渲染为普通文本，避免生成空链接。
  - 标题链接默认保持原标题颜色、不显示下划线；鼠标悬停时变蓝并显示下划线。
  - 保留原有 Abstract 和 PDF 链接位置与功能。
- 明确不回写、不批量重生成历史 HTML 文件；该改动只影响之后新生成的报告。
- 检查过历史报告中无 URL 的文章情况，并确认未来报告只要抓取数据中带有 `url` 就可以成功生成标题链接。

### 3. ArXiv 抓取与报告目录调整

- 修改 `lab_agent/tools/arxiv_daily_scraper.py`：
  - 调整 arXiv 列表页解析逻辑，优先按 `<dt>/<dd>` 成对解析论文条目。
  - 这样可以更稳定地从 arXiv 标识区域拿到论文 ID、摘要页 URL 和 PDF URL。
  - 保留对旧结构或备用结构的 fallback 解析。
- 修改 ArXiv 报告输出路径：
  - ArXiv 日报默认从 `./reports` 调整到 `./reports/ArXiv_reports`。
  - 涉及文件：
    - `lab_agent/agents/arxiv_daily_agent.py`
    - `lab_agent/mcp/mcp_config.json`
    - `lab_agent/mcp/tools/arxiv_daily_tools.py`
    - `scripts/generate_daily_report.py`
    - `lab_agent/web/app.py`
- 目标是让 ArXiv 报告与 Journal 报告目录并列，避免所有 HTML/JSON 混放在同一层。

### 4. 通用日报生成器增强

- 扩展 `DailyReportGenerator`：
  - 新增 `report_title`、`source_name`、`source_url` 参数，用于支持 ArXiv 和 Journal 复用同一个 HTML/JSON 生成器。
  - 新增 `include_priorities` 和 `display_priorities`，用于控制保存和展示哪些优先级。
  - ArXiv 继续展示 Priority 3、Priority 2、Priority 1。
  - Journal Daily 只保存和展示 Priority 3、Priority 2，不保存 Priority 1。
- 将原本三段重复的 Priority HTML 模板改为循环渲染，减少重复逻辑。
- 将 Priority 图标改为 HTML 实体，修复某些环境下 emoji 显示成 `??` 的问题。

### 5. 论文评分提示词和解析增强

- 修改 `lab_agent/config/promptArxivRecommender.txt`：
  - 明确 `Key Relevance` 最多输出 5 个英文关键词或短语。
  - 要求输出保持可解析的固定字段：`Score`、`Reason`、`Key Relevance`。
- 修改 `lab_agent/tools/paper_scorer.py`：
  - 评分客户端改用专门的 DeepSeek scoring API key。
  - 评分模型使用 `DeepSeek-V4-Flash`。
  - 评分请求 `max_tokens` 从 300 提高到 600，避免 `Key Relevance` 或中文理由被截断。
  - 新增单篇论文评分重试机制，失败后最多重试一次。
  - 默认 prompt 中加入 `Key Relevance` 字段要求。

### 6. DeepSeek API 配置拆分

- 修改 `.env.example`：
  - 新增 `DEEPSEEK_SCORING_API_KEY`。
  - 新增 `DEEPSEEK_CHAT_API_KEY`。
  - 新增 `DEEPSEEK_BASE_URL=https://llmapi.paratera.com`。
- 修改 `lab_agent/utils/config.py`：
  - 新增 `deepseek_scoring_api_key`、`deepseek_chat_api_key`、`deepseek_base_url`。
  - `deepseek_base_url` 支持从 `DEEPSEEK_BASE_URL`、`DEEPSEEK_API_URL`、`API_URL`、`BASE_URL` 读取，默认值为 `https://llmapi.paratera.com`。
  - `validate()` 改为检查 DeepSeek scoring/chat 两个 key 是否存在。
  - `to_dict()` 只暴露 key 是否存在，不暴露 key 明文。
- 修改 `lab_agent/config/models.json`：
  - `arxivFilterModel.name` 设置为 `DeepSeek-V4-Flash`。
  - `chatModel.name` 设置为 `DeepSeek-V4-Pro`。
- 修改 `lab_agent/tools/arxiv_chat.py`：
  - Chat About Papers 改用 `DEEPSEEK_CHAT_API_KEY` 和 `DEEPSEEK_BASE_URL`。
- 修改 `tests/test_deepseek.py`：
  - 测试脚本优先读取 `DEEPSEEK_CHAT_API_KEY`。
  - base URL 默认使用 `https://llmapi.paratera.com`。
- 已验证用户本地填写的 scoring/chat key 和两个模型可通过接口调用；未在记录中保存任何 key 明文。

### 7. 新增 Journal Daily RSS 日报模块

- 新增 `lab_agent/config/journal_feeds.json`：
  - 配置 `Nature`：
    - RSS: `https://www.nature.com/nature.rss`
    - 输出目录：`./reports/Nature_reports`
  - 配置 `Nature Physics`：
    - RSS: `https://www.nature.com/nphys.rss`
    - 输出目录：`./reports/Nature_Physics_reports`
  - 后续增加期刊时，可以继续在该 JSON 中追加配置。
- 新增 `lab_agent/tools/rss_daily_scraper.py`：
  - 使用 RSS/Atom feed 抓取期刊文章。
  - 统一输出字段为类似 ArXiv paper 的结构：`id`、`title`、`authors`、`abstract`、`subjects`、`url`、`source`、`journal`、`feed_url` 等。
  - 对 HTML 摘要内容做清洗。
  - 用 URL、ID 或标题去重。
- 新增 `lab_agent/agents/journal_daily_agent.py`：
  - 负责加载期刊配置、抓取 RSS、调用同一个 `PaperScorer` 打分、生成每个期刊的日报。
  - 只保留 Priority 3 和 Priority 2 文章。
  - Priority 1 不输出、不保存到 Journal 报告。
  - 支持任务类型：
    - `generate_daily_report`
    - `generate_all_reports`
    - `generate_summary_report`
    - `list_reports`
    - `get_report`
    - `clear_reports`
    - `list_summary_reports`
    - `get_summary_report`
    - `clear_summary_reports`
    - `list_journals`
- 修改 `lab_agent/agents/__init__.py` 和 `lab_agent/tools/__init__.py`：
  - 导出 `JournalDailyAgent`、`RSSDailyScraper`、`JournalChat`。

### 8. 新增 Journal Summary 报告

- Journal Daily 不只生成各期刊单独报告，还会生成汇总报告：
  - 汇总 HTML/JSON 输出目录：`reports/Journal_Summary_reports`。
  - 汇总报告合并各期刊当日 JSON 中的 `all_papers`。
  - 汇总 JSON 增加：
    - `journal_summaries`
    - `source_reports`
    - `missing_journals`
  - 汇总 HTML 使用 `Journal Daily Summary Report` 标题。
- 前端默认显示 Summary，而不是某个单独期刊。
- Summary 下拉选项显示为 `Summary`，具体期刊显示为 `Nature`、`Nature Physics` 等。

### 9. Streamlit 前端新增 Journal Daily 子栏

- 修改 `lab_agent/web/app.py`：
  - 在主导航中新增与 `ArXiv Daily` 并列的 `Journal Daily` tab。
  - Journal Daily 页面包含：
    - 报告选择下拉框。
    - 默认 `Summary`。
    - 各期刊单独报告入口。
    - 生成、清除、刷新按钮。
    - 报告指标展示。
    - Priority 3 / Priority 2 文章预览。
  - 修复早期新增 tab 后 `tabs[4]` 越界导致的 `IndexError: tuple index out of range`。
  - 修复前端 `??` 图标显示问题，去掉问题 emoji 或改用稳定文本/实体。
- 保持 ArXiv Daily 原有功能可用，ArXiv Daily 仍能生成、列出和显示报告。

### 10. HTML/JSON 前端显示调整

- 对 ArXiv Daily 和 Journal Daily 都做了相同交互调整：
  - `View HTML Report` 变成 toggle 按钮。
  - 第一次点击展开 HTML 报告。
  - 再次点击收起 HTML 报告，让界面恢复简洁。
  - 删除前端的 `View JSON Data` 展示按钮，不再在网页端直接展示 JSON。
- JSON 文件仍会正常生成和保存，只是不在网页端展示。

### 11. Journal Daily Chat About Papers

- 新增 `lab_agent/config/journalChatPrompt.txt`：
  - Journal Daily 专用聊天系统提示词。
  - 强调凝聚态、量子材料、超导、实验技术等方向。
  - 支持在 Summary 下比较多个期刊的文章。
- 新增 `lab_agent/tools/journal_chat.py`：
  - `JournalChat` 继承现有 `ArxivChat`。
  - 复用 `DeepSeek-V4-Pro` chat 模型和 `DEEPSEEK_CHAT_API_KEY`。
  - 使用 Journal 专用 prompt。
  - 按 Priority 和 Journal/source 整理当天文章上下文。
  - 提供 Journal 相关 suggested questions。
- 修改 `lab_agent/web/app.py`：
  - Journal Daily 页面右侧新增 `Chat About Papers`。
  - 与 ArXiv Daily 的聊天体验保持一致：
    - New Conversation。
    - Exchanges 计数。
    - suggested questions。
    - chat input。
  - 根据 Journal Daily 下拉框自动选择聊天上下文：
    - `Summary` 加载当天 Journal Summary 的全部文章。
    - `Nature` / `Nature Physics` 加载对应期刊当天文章。
  - 当对应报告不存在或没有文章时，会清空旧上下文，避免误用上一次打开的报告内容。

### 12. 定时任务入口扩展

- 修改 `scripts/generate_daily_report.py`：
  - 原本只生成 ArXiv Daily。
  - 现在一次运行会依次生成：
    1. ArXiv Daily 报告。
    2. 所有配置期刊的 Journal Daily 报告。
    3. Journal Daily Summary 报告。
  - 保留 `--skip-weekends`。
  - 新增参数：
    - `--journal-reports-base-dir`
    - `--journal-summary-reports-dir`
    - `--skip-arxiv`
    - `--skip-journals`
  - 默认 ArXiv 输出目录为 `./reports/ArXiv_reports`。
  - 默认 Journal 汇总输出目录为 `./reports/Journal_Summary_reports`。
- 因现有 `scripts/run_daily_report.bat` 调用的是 `scripts/generate_daily_report.py`，如果 Windows Task Scheduler 原本调用该 bat，则之后会和 ArXiv Daily 一起触发 Journal Daily。
- 已运行过 `scripts/run_daily_report.bat` 验证，ArXiv、Nature、Nature Physics 和 Journal Summary 都能完成生成或读取缓存。

### 13. 前端与本地效果验证

- 已验证 Journal Daily 页面：
  - tab 能正常打开。
  - 下拉框包含 `Summary`、`Nature`、`Nature Physics`。
  - Summary 默认显示。
  - HTML 报告文件本身正确。
  - 前端不再出现 `Unknown task type: list_summary_reports`。
  - 前端不再出现 `?? Priority` 或 `?? Abstract`。
  - `View HTML Report` 可以展开和收起。
  - 不再显示 `View JSON Data`。
  - Journal Daily 的 Chat About Papers 区域可以加载当天 Summary papers 上下文。
- 已做 Python AST 语法检查：
  - `lab_agent/web/app.py`
  - `lab_agent/tools/journal_chat.py`
  - `lab_agent/tools/__init__.py`

### 14. 当前重要文件清单

- ArXiv 日报：
  - `lab_agent/agents/arxiv_daily_agent.py`
  - `lab_agent/tools/arxiv_daily_scraper.py`
  - `lab_agent/tools/daily_report_generator.py`
  - `reports/ArXiv_reports/`
- Journal 日报：
  - `lab_agent/agents/journal_daily_agent.py`
  - `lab_agent/tools/rss_daily_scraper.py`
  - `lab_agent/config/journal_feeds.json`
  - `reports/Nature_reports/`
  - `reports/Nature_Physics_reports/`
  - `reports/Journal_Summary_reports/`
- 聊天：
  - `lab_agent/tools/arxiv_chat.py`
  - `lab_agent/tools/journal_chat.py`
  - `lab_agent/config/journalChatPrompt.txt`
- 模型与 API 配置：
  - `.env`
  - `.env.example`
  - `lab_agent/utils/config.py`
  - `lab_agent/config/models.json`
- 前端：
  - `lab_agent/web/app.py`
- 定时任务入口：
  - `scripts/generate_daily_report.py`
  - `scripts/run_daily_report.bat`

### 15. Journal Daily RSS journal expansion

- Updated `lab_agent/config/journal_feeds.json` and added 5 RSS feeds on top of Nature and Nature Physics:
  - Nature Materials: `https://www.nature.com/nmat.rss`, reports in `./reports/Nature_Materials_reports`.
  - Nature Communications: `https://www.nature.com/ncomms.rss`, reports in `./reports/Nature_Communications_reports`.
  - Science: `https://www.science.org/action/showFeed?feed=rss&jc=science&type=etoc`, reports in `./reports/Science_reports`.
  - Physical Review Letters: `https://feeds.aps.org/rss/recent/prl.xml`, reports in `./reports/Physical_Review_Letters_reports`.
  - Physical Review B: `https://feeds.aps.org/rss/recent/prb.xml`, reports in `./reports/Physical_Review_B_reports`.
- Journal Daily now has 7 configured journals.
- Because the frontend and scheduled script both read from `journal_feeds.json`, the new journals will automatically appear in the Journal Daily selector and join future single-journal reports plus the Summary report.

### 16. 注意事项

- `.env` 中已经由用户填写真实 API key，后续提交或分享代码时需要避免提交真实密钥。
- `history.md` 不记录任何真实 API key。
- 历史报告不会自动迁移或重生成；新目录结构主要影响之后新生成的报告。
- Journal Daily now has 7 configured journals: Nature, Nature Physics, Nature Materials, Nature Communications, Science, Physical Review Letters, and Physical Review B. More RSS feeds can still be added through `journal_feeds.json`.
- Journal Daily 采用与 ArXiv 相同评分标准，但只保存 Priority 3 和 Priority 2。
- 如果后续需要让旧报告也进入新目录，需要单独做一次迁移脚本或手工迁移；本次未做历史报告回写。


### 16. Journal Daily RSS generation verification

- Tested the 5 newly added RSS feeds through `RSSDailyScraper`:
  - Nature Materials: 8 RSS entries fetched.
  - Nature Communications: 8 RSS entries fetched.
  - Science: 40 RSS entries fetched.
  - Physical Review Letters: 100 RSS entries fetched.
  - Physical Review B: 100 RSS entries fetched.
- Generated 2026-06-25 HTML/JSON reports for all 5 newly added journals:
  - `reports/Nature_Materials_reports/2026-06-25.*`
  - `reports/Nature_Communications_reports/2026-06-25.*`
  - `reports/Science_reports/2026-06-25.*`
  - `reports/Physical_Review_Letters_reports/2026-06-25.*`
  - `reports/Physical_Review_B_reports/2026-06-25.*`
- Rebuilt `reports/Journal_Summary_reports/2026-06-25.html` and `.json` after all new reports were present.
- Updated `lab_agent/agents/journal_daily_agent.py` so Journal Summary HTML now includes a `Journal Breakdown` table listing every configured journal, including journals with zero saved articles.
- Verified the 2026-06-25 Journal Summary includes all 7 journals and has no missing source reports.


## 2026-06-26

### Journal Daily date-window filtering

- Updated `lab_agent/tools/rss_daily_scraper.py` to retain structured RSS dates as `published_date_iso` and `published_datetime` for each article.
- Updated `lab_agent/agents/journal_daily_agent.py` so Journal Daily only processes RSS articles whose published/updated date is the report date or the previous date.
- Journal reports now record RSS metadata in JSON summaries: `date_window_dates`, `rss_entries_fetched`, `date_window_articles`, and `filtered_out_by_date`.
- Journal report HTML and Summary HTML now include an RSS Date Window note showing the today/yesterday date window and RSS counts.
- Journal Summary breakdown now includes RSS entries and today/yesterday window counts per journal.
- Updated `lab_agent/web/app.py` to show the same date-window metrics in the Journal Daily frontend.

### Scheduled task behavior

- Updated `scripts/generate_daily_report.py` so `--skip-weekends` only skips ArXiv generation on weekends.
- Journal Daily still runs when `--skip-weekends` is present, so the existing `scripts/run_daily_report.bat` can keep using the same flag while allowing weekend Journal reports.
- Weekend ArXiv runs print `ArXiv今日无更新` and do not generate an ArXiv HTML/JSON report.

### Verification

- Ran AST syntax checks for `rss_daily_scraper.py`, `journal_daily_agent.py`, `web/app.py`, and `scripts/generate_daily_report.py`.
- Verified the date-window helper keeps only report-date and previous-date articles.
- Verified current RSS feeds expose parseable dates with no missing dates in the sampled entries.
- Ran an offline end-to-end Journal Daily test with fake RSS/scoring data and confirmed old articles are excluded while today/yesterday articles are saved and summarized.


### Overview frontend redesign

- Updated `lab_agent/web/app.py` Overview tab into a cleaner combined dashboard for ArXiv Daily and Journal Daily.
- Removed the old placeholder System Status buttons from Overview. Those buttons only displayed Streamlit messages and did not start or stop backend services.
- Removed the old System Overview block and replaced ArXiv-only status with combined ArXiv + Journal metrics.
- Overview now highlights total Priority 3 papers across ArXiv Daily and Journal Daily, plus Priority 2, total articles, and journal source count.
- Report Details now summarizes both ArXiv Daily and Journal Daily; Journal Daily details include the today/yesterday RSS window metrics when available.
- Moved Configuration and Available Tools into collapsed expanders so they are still accessible but less visually dominant.
- Replaced the old GPT-5-mini Overview assistant UI with `DeepSeek Assistant`, backed by the configured `chatModel` (`DeepSeek-V4-Pro`) and `DEEPSEEK_CHAT_API_KEY`.
- Added `lab_agent/tools/deepseek_assistant.py` as the Overview general assistant client.
- Reduced visible emoji noise on Overview while preserving the red/yellow priority dot markers.

### Overview verification

- Ran `labagent/Scripts/python.exe -m py_compile` for `lab_agent/web/app.py` and `lab_agent/tools/deepseek_assistant.py`.
- Ran Streamlit `AppTest` against `lab_agent/web/app.py`; no Streamlit exceptions were reported.
- Confirmed the Overview assistant reads `DeepSeek-V4-Pro`, `https://llmapi.paratera.com`, and a configured chat API key.

### DeepSeek Assistant config fallback fix

- Fixed a frontend initialization bug where Overview `DeepSeek Assistant` could fail if a stale Streamlit session held an older `Config` object without `deepseek_chat_api_key`.
- `lab_agent/tools/deepseek_assistant.py` now reads `DEEPSEEK_CHAT_API_KEY` and DeepSeek base URL through `getattr(...)` plus environment-variable fallback.
- `lab_agent/web/app.py` Configuration display now uses the same safe config access pattern.
- Verified Python compilation, simulated an old Config object, and ran Streamlit `AppTest` with no exceptions.

### DeepSeek Assistant initialization and config cleanup

- Fixed the remaining Overview initialization issue by retrying `DeepSeekAssistant` when a prior Streamlit session had stored a failed `None` value in `st.session_state`.
- Removed the top-level initialization error banner from normal reruns; assistant initialization errors are now stored in session state and shown only in the assistant panel with a retry button.
- Updated `Config` to load `.env` explicitly from the project root when no custom config path is supplied, so Streamlit can be launched from different working directories without losing DeepSeek keys.
- Cleaned `Config` to keep only the active DeepSeek, web, ArXiv, rate-limit, database, and timezone settings. Removed old OpenAI/Gemini fields from the active config surface.
- Removed the unused GPT-5-mini client/chatbox code and config files:
  - `lab_agent/tools/gpt5_mini_client.py`
  - `lab_agent/tools/gpt5_mini_chatbox.py`
  - `lab_agent/config/gpt5_mini_config.json`
  - `lab_agent/config/gpt5_mini_chatbox_config.json`
- Removed GPT-5-mini fallback branches from ArXiv chat and paper scoring; both now use the configured DeepSeek chat-completions-compatible endpoint.
- Updated `.env.example`, `models.json`, MCP comments, and DeepSeek test script to match the current DeepSeek-only configuration.
- Removed obsolete `tests/test_zhipu.py`.
- Verified py_compile, Streamlit AppTest, and config loading. AppTest reported no errors and the Overview page includes `DeepSeek Assistant`.

### Overview assistant aligned with Journal Daily chat configuration

- Reworked the Overview `DeepSeek Assistant` so it no longer imports or initializes the standalone `DeepSeekAssistant` class.
- The Overview assistant now uses the same DeepSeek chat-completions-compatible configuration pattern as Journal Daily chat:
  - `DEEPSEEK_CHAT_API_KEY`
  - `DEEPSEEK_BASE_URL` / compatible fallback URL variables
  - `chatModel` from `lab_agent/config/models.json`
- Removed `lab_agent/tools/deepseek_assistant.py` and its export from `lab_agent/tools/__init__.py` to avoid stale Streamlit module-cache errors.
- Kept a small session-state cleanup in `app.py` to remove old `deepseek_assistant` / `deepseek_assistant_error` values left by earlier runs.
- Verified py_compile and Streamlit AppTest. Overview now reports no errors and shows `Research and lab planning assistant - DeepSeek-V4-Pro`.


## 2026-06-26 End-of-Day Consolidated Summary

### DeepSeek-only runtime cleanup

- Removed the active OpenAI/GPT and Google/Gemini runtime surfaces from the project configuration.
- Kept the `openai` Python package because the current DeepSeek endpoint is called through an OpenAI-compatible chat-completions SDK interface.
- Removed old GPT-5-mini runtime files and configs from the active codebase:
  - `lab_agent/tools/gpt5_mini_client.py`
  - `lab_agent/tools/gpt5_mini_chatbox.py`
  - `lab_agent/config/gpt5_mini_config.json`
  - `lab_agent/config/gpt5_mini_chatbox_config.json`
- Removed the obsolete `tests/test_zhipu.py` script.
- Removed `google-generativeai` from `requirements.txt`.
- Cleaned `lab_agent/utils/config.py` so it now exposes only the active DeepSeek keys/base URL plus app, web, database, ArXiv, rate-limit, and timezone settings.
- Updated ArXiv chat and paper scoring to remove GPT-5-mini fallback branches; both now use the configured DeepSeek-compatible endpoint.
- Updated stale frontend error text so missing API messages refer to `DEEPSEEK_SCORING_API_KEY` or `DEEPSEEK_CHAT_API_KEY` instead of old OpenAI keys.

### Overview assistant stabilization

- Reworked the Overview `DeepSeek Assistant` to use the same chat model configuration pattern as Journal Daily:
  - `DEEPSEEK_CHAT_API_KEY`
  - `DEEPSEEK_BASE_URL` and compatible fallback URL variables
  - `chatModel` from `lab_agent/config/models.json`
- Removed the standalone `DeepSeekAssistant` class path to avoid stale Streamlit module-cache errors.
- Added cleanup of old `deepseek_assistant` session keys left by earlier Overview implementations.

### Documentation cleanup

- Rewrote `README.md` for the current DeepSeek-based ArXiv Daily and Journal Daily architecture.
- Rewrote `TESTING_GUIDE.md` for the current validation workflow.
- Rewrote `CLAUDE.md` so it no longer describes the old OpenAI/Gemini architecture as active.
- Updated `tests/test_deepseek.py` so the smoke test reads `chatModel` from `lab_agent/config/models.json` instead of using a hardcoded model name.

### Current report and scheduling behavior retained

- ArXiv Daily report generation remains under `reports/ArXiv_reports`.
- Journal Daily per-journal reports and `Journal_Summary_reports` remain unchanged.
- Journal Daily still filters RSS articles by today/yesterday dates and saves only Priority 3 and Priority 2 articles.
- ArXiv weekend behavior remains unchanged: with weekend skipping enabled, it logs that ArXiv has no updates and does not generate an ArXiv report.
- Journal Daily still runs every day.

### Verification

- Ran AST syntax checks for the main web app, ArXiv/Journal agents, scoring/chat tools, RSS/report generation tools, scheduled script, and DeepSeek test script.
- Imported the active configuration, scorer, chat, and agent modules with bytecode writing disabled.
- Ran Streamlit `AppTest` on `lab_agent/web/app.py`; result: `exception_count = 0`, `errors = []`, tabs loaded correctly, and Overview showed `Research and lab planning assistant - DeepSeek-V4-Pro`.
- Confirmed `Config` still reads both DeepSeek keys and the configured base URL, and no longer exposes old OpenAI/Gemini config attributes.
- Scanned the active source/docs for old GPT/Gemini/OpenAI-key configuration strings; remaining `openai` references are limited to the SDK package required for the DeepSeek-compatible API path.
- Attempted to remove Python `__pycache__` generated files. Most stale cache files were cleared; two current cache files were locked by Windows during verification. They are ignored generated artifacts and not active source code.

## 2026-06-27 ArXiv title-link repair

### Problem

- Journal Daily HTML titles were clickable, but ArXiv Daily HTML titles were still plain text.
- The HTML template already had conditional title links using `paper.url`, but current ArXiv JSON reports had empty `id`, `url`, and `pdf_url` fields.
- Because `ArxivDailyAgent` returns cached reports when a same-date JSON/HTML pair already exists, previous template-only changes did not rewrite the cached HTML.

### Root Cause

- Current arXiv list pages put the abstract link directly under the `<dt>` element, for example `<a href="/abs/2606.26222" ...>arXiv:2606.26222</a>`.
- The earlier scraper mainly looked for `span.list-identifier`, so it often failed to extract the arXiv id from the current page structure.
- Without `id`, the scraper emitted empty `url` / `pdf_url`; without `paper.url`, the Jinja template correctly fell back to plain title text.

### Fix

- Updated `lab_agent/tools/arxiv_daily_scraper.py`:
  - Added robust arXiv id extraction from `/abs/...`, `arXiv:...`, and id/text attributes.
  - Added direct `<dt><a href="/abs/...">` parsing for current arXiv list pages.
  - Verified a live arXiv fetch returned 140 papers with 140 ids and 140 abstract URLs.
- Updated `lab_agent/tools/daily_report_generator.py`:
  - Added a fallback that fills `url` and `pdf_url` from a valid arXiv `id` before organizing papers by priority.
  - This protects future reports if an upstream step provides an id but omits links.
- Repaired the latest cached ArXiv report only:
  - `reports/ArXiv_reports/2026-06-26.json`
  - `reports/ArXiv_reports/2026-06-26.html`
  - Matched all 140 current report titles against the live arXiv list page, backfilled `id`, `url`, and `pdf_url`, and regenerated HTML.

### Verification

- Latest ArXiv report now has:
  - 140 papers
  - 140 ids
  - 140 abstract URLs
  - 140 PDF URLs
  - 140 clickable title links in the HTML
- Ran AST checks for `arxiv_daily_scraper.py`, `daily_report_generator.py`, `arxiv_daily_agent.py`, and `web/app.py`.
- Ran Streamlit AppTest; result: `exception_count = 0`, `errors = []`.

## 2026-06-27 Paper scoring max token update

- Updated `lab_agent/tools/paper_scorer.py` so each DeepSeek scoring call now uses `max_tokens=2000` instead of `max_tokens=600`.
- This affects the AI Assessment / `Reason` generation path for ArXiv Daily and Journal Daily scoring.
- Chat model settings in `lab_agent/config/models.json` remain unchanged at `chatModel.maxTokens=1500`.
- Ran AST syntax check for `paper_scorer.py` successfully.

## 2026-06-27 Chat max token update

- Updated `lab_agent/config/models.json` so `chatModel.maxTokens` is now `15000` instead of `1500`.
- This affects chat calls that read `chatModel`, including ArXiv chat, Journal chat, and the Overview DeepSeek Assistant.
- The scoring path remains unchanged at `paper_scorer.py` `max_tokens=2000`.
- Validated `models.json` parses correctly and confirmed `chatModel.name=DeepSeek-V4-Pro`, `chatModel.maxTokens=15000`.

## 2026-06-27 Chat max token update to 50000

- Updated `lab_agent/config/models.json` again so `chatModel.maxTokens` is now `50000`.
- This affects chat paths using `DeepSeek-V4-Pro`: ArXiv chat, Journal chat, and Overview DeepSeek Assistant.
- Scoring remains unchanged at `paper_scorer.py` `max_tokens=2000`.
- Validated `models.json` parses correctly and confirmed `chatModel.maxTokens=50000`.

## 2026-06-27 Article recommender prompt rename

- Renamed `lab_agent/config/promptArxivRecommender.txt` to `lab_agent/config/promptArticleRecommender.txt` because the same scoring prompt is now shared by ArXiv Daily and Journal Daily article scoring.
- Updated `lab_agent/tools/paper_scorer.py` so `PaperScorer._load_prompt_template()` loads `promptArticleRecommender.txt` directly.
- Updated current documentation references in `README.md` and `CLAUDE.md` to use the new prompt filename.
- Verified current code/docs references outside historical notes point to `promptArticleRecommender.txt`.
- Verified `paper_scorer.py` parses successfully and that `PaperScorer` can load the renamed prompt file with a direct stubbed import check.
## 2026-06-27 Journal Daily feed expansion

- Validated and added 6 new Journal Daily RSS feeds to `lab_agent/config/journal_feeds.json`:
  - Physical Review X: `https://feeds.aps.org/rss/recent/prx.xml`
  - PNAS: `https://www.pnas.org/action/showFeed?type=etoc&feed=rss&jc=PNAS`
  - Nature Nanotechnology: `https://www.nature.com/nnano.rss`
  - Science Advances: `https://www.science.org/action/showFeed?feed=rss&jc=sciadv&type=etoc`
  - Reviews of Modern Physics: `https://feeds.aps.org/rss/recent/rmp.xml`
  - Annual Review of Condensed Matter Physics: `https://www.annualreviews.org/action/showFeed?type=etoc&feed=rss&jc=conmatphys`
- Science Advances was requested with `jc=advances`, but that URL returned 404. The working Science journal code is `jc=sciadv`, so the config uses the verified URL.
- JACS (`https://pubs.acs.org/action/showFeed?type=axatoc&feed=rss&jc=jacsat`) was tested but not enabled because ACS currently returns a Cloudflare 403 challenge page instead of RSS XML to automated requests. Alternate ACS feed URL forms were also tested and remained blocked.
- Updated `RSSDailyScraper` request headers to use a browser-like User-Agent plus RSS/XML Accept header. This improves compatibility with publishers such as Annual Reviews.
- Updated `README.md` report directory documentation for the newly enabled journal folders.
- Verification: `journal_feeds.json` parses successfully, has 13 unique enabled journal slugs, `rss_daily_scraper.py` AST parses successfully, and all 6 enabled new feeds returned HTTP 200 with XML entries during validation.
## 2026-06-27 Paper scoring max token update to 5000

- Updated `lab_agent/tools/paper_scorer.py` so DeepSeek scoring calls now use `max_tokens=5000`.
- This affects AI Assessment / `Reason` generation for both ArXiv Daily and Journal Daily because both use `PaperScorer`.
- Verified `paper_scorer.py` parses successfully and confirmed the active scoring call contains `max_tokens=5000`.
## 2026-06-27 Journal Daily HTML article format update

- Updated `lab_agent/tools/daily_report_generator.py` with optional Journal-specific HTML rendering controls:
  - `show_subjects=False` hides the `Subjects:` line for Journal Daily reports.
  - `format_journal_abstract=True` renders journal name and publication date as highlighted metadata before the abstract text.
  - Nature-style RSS prefixes such as `Journal, Published online: DATE; doi:...` are stripped from the displayed abstract body to avoid duplicate metadata.
- Updated `lab_agent/agents/journal_daily_agent.py` so both per-journal reports and `Journal Daily Summary Report` use the new Journal HTML rendering mode.
- ArXiv Daily does not pass these options, so its HTML output remains unchanged and still displays `Subjects:`.
- Verification: generated temporary Journal and ArXiv sample reports. Journal sample hid `Subjects:`, showed highlighted journal/date metadata, stripped the Nature `Published online`/DOI prefix from the abstract body, and ArXiv sample still showed `Subjects:`.
## 2026-06-27 Daily summary of implemented changes

- Expanded Journal Daily from the earlier Nature/Nature Physics/Nature Materials/Nature Communications/Science/PRL/PRB set to 13 enabled RSS sources by adding Physical Review X, PNAS, Nature Nanotechnology, Science Advances, Reviews of Modern Physics, and Annual Review of Condensed Matter Physics.
- Verified the newly enabled RSS feeds return HTTP 200 and parseable XML entries. JACS was intentionally not enabled because ACS returned a Cloudflare 403 challenge page instead of RSS XML to automated requests.
- Updated `RSSDailyScraper` to use a browser-like User-Agent and RSS/XML Accept header for better publisher compatibility.
- Renamed the shared scoring prompt from `promptArxivRecommender.txt` to `promptArticleRecommender.txt`, and updated `PaperScorer`, README, and project context references accordingly.
- Increased DeepSeek scoring `max_tokens` in `PaperScorer` to 5000. This affects both ArXiv Daily and Journal Daily AI Assessment generation.
- Kept chat model `maxTokens` at 50000 for DeepSeek-V4-Pro based chat paths.
- Added Journal-specific HTML rendering controls in `DailyReportGenerator`: Journal Daily hides `Subjects:` and highlights journal name plus publication date before the abstract body, while ArXiv Daily keeps its existing format.
- Enabled the new Journal HTML format for per-journal reports and the Journal Daily Summary report. ArXiv Daily remains unchanged.
- Applied the new Journal HTML format to `reports/Nature_reports/2026-06-26.html` only as a preview sample; its JSON and other reports were not modified.
- Updated documentation/report-directory references where relevant and ran lightweight syntax/config checks for the touched Python and JSON files.

### Operational notes from today

- Future Journal Daily summary reports will include all enabled journals from `lab_agent/config/journal_feeds.json`; currently this is 13 journals.
- Existing historical HTML files are not automatically migrated unless explicitly regenerated or rewritten for preview.
- Journal Daily remains date-window based using RSS published/updated dates for today and yesterday.
- ArXiv Daily remains on its existing weekday-oriented behavior and output format.
## 2026-06-27 Journal HTML date format update

- Updated `lab_agent/tools/daily_report_generator.py` so Journal Daily article metadata dates are normalized to `YYYY-MM-DD` when possible.
- Added parsing support for common RSS date strings such as `24 June 2026`, `June 24, 2026`, ISO dates, and RFC-style dates.
- Updated `.journal-date` CSS to use italic text while keeping the existing color and size behavior.
- Re-rendered only `reports/Nature_reports/2026-06-26.html` as the preview sample. Its article dates now display as `2026-06-24` and `2026-06-22`.
- Verified `daily_report_generator.py` parses successfully and the preview HTML no longer contains the old `24 June 2026` / `22 June 2026` date strings.
## 2026-06-27 Report UI clear-button removal

- Updated `lab_agent/web/app.py` to remove report-clearing buttons from the ArXiv Daily and Journal Daily pages:
  - Removed `Clear All Reports` from ArXiv Daily.
  - Removed `Clear Summary Reports` from Journal Daily Summary.
  - Removed `Clear Journal Reports` from individual Journal Daily reports.
- Kept the backend clear methods in place, but they are no longer exposed as normal page buttons.
- The remaining page actions are now Generate and Refresh.
- Verified `app.py` parses successfully and the removed Clear button labels no longer appear in the Streamlit UI code.

### Button behavior note

- ArXiv Daily `Generate Daily Report` loads today's cached report if it already exists; it does not re-scrape arXiv or re-score papers in that case.
- Individual Journal Daily `Generate Journal Report` also loads today's cached per-journal report if it exists; it does not refetch RSS or re-score in that case.
- Journal Daily `Generate Summary Report` calls the combined journal generation path. Existing per-journal reports are loaded from cache, while missing per-journal reports are fetched/scored. The summary HTML/JSON is regenerated from available per-journal JSON data.
- Refresh buttons only rerun the Streamlit page render/listing and do not fetch RSS/arXiv data or call scoring APIs.
## 2026-06-27 Frontend UI cleanup for ArXiv and Journal Daily

- Updated `lab_agent/web/app.py` navigation: renamed the `Tools` tab to `Database` and left the tab content empty.
- Removed the old `Available Tools` overview expander content and replaced it with an empty `Database` expander for naming consistency.
- Removed decorative emojis from ArXiv Daily UI headings:
  - `ArXiv Daily Paper Recommendations`
  - `Daily Reports`
  - `Chat About Papers`
  - `Suggested questions`
- Removed Refresh buttons from ArXiv Daily and Journal Daily report sections.
- Changed ArXiv Daily and Journal Daily Generate controls from wide primary buttons into smaller, less prominent `Generate` buttons in narrow columns.
- Updated the Journal Daily report selector so `Summary` remains first and all journal names are sorted alphabetically after it.
- Added CSS to bold the first item in the Journal Daily dropdown menu, so `Summary` is emphasized when the dropdown is open. The selected Summary section title is also rendered in bold below the selector.
- Verified `app.py` parses successfully and confirmed the removed Clear/Refresh/old emoji/Tools labels no longer appear in the target UI code.
## 2026-06-28 HTML report modal viewer

- Updated `lab_agent/web/app.py` so ArXiv Daily and Journal Daily `View HTML Report` opens the generated HTML report in a centered Streamlit modal dialog instead of expanding it inline below the button.
- The modal uses Streamlit's native `st.dialog` when available, with a `Close` button inside the dialog and the standard dialog dismiss control.
- Added a fallback path that shows the report inline only if a future environment lacks both `st.dialog` and `st.experimental_dialog`.
- Verified the active project environment uses Streamlit 1.56.0 and supports `st.dialog`.
- Verified `app.py` parses and compiles successfully with the project virtual environment.
## 2026-06-28 HTML report modal and preview cleanup

- Updated `lab_agent/web/app.py` HTML report dialog so the extra lower-left `Close` button is removed. Users now close the modal with Streamlit's native top-right dialog close control.
- Removed the ArXiv Daily per-report `Top Priority Papers` preview section under each report card.
- Removed Journal Daily per-report article preview sections from both Journal Summary reports and individual journal reports. Report cards now keep the metrics plus `View HTML Report`; full article details are read from the HTML modal.
- Kept the Journal Summary `Journal Breakdown` table because it is a source-level summary rather than a per-paper preview.
- Added CSS for Streamlit dialogs to blur the page backdrop and lightly darken the modal overlay when an HTML report dialog is open.
- Verified `lab_agent/web/app.py` compiles successfully with the project virtual environment and confirmed removed preview/Close labels no longer appear in the target UI code.
## 2026-06-28 Frontend layout redesign

- Reworked `lab_agent/web/app.py` from the original top `st.tabs` navigation into a left sidebar card navigation controlled by `st.session_state.active_nav`.
- Added a purple/black/white visual system inspired by the reference screenshots:
  - Active navigation card uses `rgb(102, 8, 116)`.
  - Page headers use a dark-to-purple card gradient with larger title hierarchy.
  - Report, metric, expander, sidebar, and chat areas now use softer cards, rounded corners, and subtle shadows.
- Hid Streamlit's default top toolbar/header/deploy area (`stHeader`, `stToolbar`, `stDeployButton`, `MainMenu`, footer) so the page no longer has the disconnected white strip at the top-right.
- Added reusable report-list helpers:
  - `render_report_search()` stores submitted search queries in `session_state`.
  - `render_report_collection()` shows the latest five reports by default.
  - Older reports are grouped under a `Previous Reports` expander.
  - Submitted date searches show matching older reports directly.
- Updated ArXiv Daily and Journal Daily pages with right-top search forms plus compact `Generate` actions while preserving the existing generation/report APIs.
- Wrapped ArXiv and Journal `Chat About Papers` sections in higher-contrast cards, and replaced default icon-heavy info messages with cleaner custom `.chat-notice` blocks.
- Cleaned remaining decorative chat/report UI icons from the new frontend surfaces.
- Verified `lab_agent/web/app.py` compiles successfully with the project virtual environment.
- Verified the redesigned Streamlit UI in-browser on `http://127.0.0.1:8505`:
  - Left sidebar navigation switches pages.
  - ArXiv Daily and Journal Daily show search controls.
  - Latest report cards and `Previous Reports` grouping render.
  - Journal search returns `Search results` after submitting a date.
  - Streamlit `Deploy`/toolbar text is no longer visible.

## 2026-06-28 Frontend sidebar and report calendar refinement

- Updated `lab_agent/web/app.py` sidebar layout:
  - Removed the left-side `DeepSeek powered` card.
  - Reduced the sidebar width from the previous wide layout to a compact `168px` design.
  - Tightened sidebar navigation spacing, active-card sizing, and brand spacing for the narrower layout.
- Replaced the ArXiv Daily and Journal Daily text search forms with report calendar selectors placed beside the `Reports` heading.
  - Calendar opens in a popover.
  - Dates with reports are clickable.
  - Dates without reports are shown as grey disabled calendar cells.
  - Clicking an available date filters/jumps directly to that day's report.
- Updated report list behavior so a selected calendar date expands only the matching report.
- Cleaned Journal Daily summary layout:
  - Hid the small `Report` label above the report selector.
  - Moved included-journal and report-list guidance text to a muted footer at the bottom of the report list section.
  - Removed the `Journal Breakdown` table from Journal Summary report display while keeping summary metrics and `View HTML Report`.
- Verified with the project virtual environment that `lab_agent/web/app.py` compiles successfully.
- Verified in browser on `http://127.0.0.1:8505`:
  - DeepSeek sidebar card is gone.
  - ArXiv and Journal calendar selectors appear near `Reports`.
  - Calendar unavailable dates are greyed out.
  - Selecting an available ArXiv date jumps to that report.
  - Journal Summary report expansion no longer shows `Journal Breakdown`.

## 2026-06-28 Sidebar brand cleanup

- Updated `lab_agent/web/app.py` sidebar branding:
  - Removed the `Literature intelligence` subtitle under `Lab Agent`.
  - Added CSS to hide Streamlit's sidebar collapse/open control so the left navigation stays fixed and visually cleaner.
- Verified `lab_agent/web/app.py` compiles successfully with the project virtual environment.

## 2026-06-28 Sidebar collapse control fix

- Updated `lab_agent/web/app.py` CSS so Streamlit's real sidebar collapse control is hidden at the container level, not only when the `data-testid` is on a button.
- Covered current and fallback selectors for `stSidebarCollapseButton`, `collapsedControl`, header-style sidebar buttons, and sidebar open/collapse aria/title variants.
- Verified in browser on `http://127.0.0.1:8505` that `[data-testid="stSidebarCollapseButton"]` is now `display: none`, `visibility: hidden`, and `pointer-events: none`.

## 2026-06-28 Sidebar restore after collapse-state issue

- Fixed a sidebar recovery problem caused by hiding both the collapse control and the collapsed-state open control.
- Updated `st.set_page_config()` with `initial_sidebar_state="expanded"` so the app prefers an expanded left sidebar on load.
- Adjusted CSS to hide only the expanded sidebar collapse button, while no longer hiding `collapsedControl` / open-sidebar selectors as a recovery fallback.
- Verified in browser on `http://127.0.0.1:8505` that the left sidebar is visible again and the collapse button is hidden.

## 2026-06-28 Sidebar collapsed-state recovery hardening

- Debugged a case where the left sidebar could remain missing if the browser had already persisted Streamlit's collapsed sidebar state.
- Changed CSS so `stHeader` is no longer fully hidden; it is transparent instead, preserving Streamlit's collapsed-state recovery area.
- Kept toolbar/deploy/status/menu/footer hidden so the top UI remains visually clean.
- Added `ensure_sidebar_expanded()` to automatically click Streamlit's open-sidebar control if the app loads while the sidebar is collapsed.
- Strengthened sidebar CSS with `left: 0`, fixed width, visible state, and `transform: translateX(0)` as a layout fallback.
- Verified in browser on `http://127.0.0.1:8505` that the sidebar is visible, the collapse button is hidden, and Deploy/toolbar remains hidden.

## 2026-06-28 Journal Daily metric card simplification

- Updated `lab_agent/web/app.py` Journal Daily report display so expanded Journal Summary reports and individual journal reports show only three metric cards:
  - `Saved Articles`
  - `Priority 3`
  - `Priority 2`
- Removed frontend metric cards for `Journals`, `RSS Entries`, and `Today/Yesterday` from expanded Journal Daily reports.
- Kept underlying JSON/HTML report data unchanged; this is display-only.
- Verified `lab_agent/web/app.py` compiles successfully and confirmed in browser that the removed metric labels no longer appear in expanded Journal Summary reports.

## 2026-09-18 服务器日报定时任务调整

- 用户反馈服务器上原有的 `0 8 * * *` 任务实际在北京时间 16:00 触发，与 Cron 按 UTC 08:00 执行一致。
- 更新 `SERVER_README.md` 中的两个 Cron 示例，将触发时间改为 `0 2 * * *`，对应北京时间每天 10:00；任务命令和日报生成逻辑未修改，服务器系统时间未修改。
- 本地说明修改已提交为 `edea3f2`（`Document 10 AM China time cron schedule`）。本项目使用 `deploy_to_server.ps1` 通过 SCP/SSH 部署项目文件；Git 提交或项目文件同步不会自动修改服务器的 crontab。
- 用户已在服务器上修改 `zmr` 的 crontab，并通过 `crontab -l` 确认任务为 `0 2 * * * CONDA_EXE=/home/zmr/miniforge3/bin/conda /data/zmr/projects/labAgent_Server/scripts/run_daily_report.sh`。
- 定时配置已核对；北京时间 10:00 的实际运行结果仍需在下一次触发后查看 `logs/daily_report.log` 确认。

## 2026-09-19 服务器定时任务未启动问题修复

- 日志确认 2026-09-19 北京时间 10:00 没有产生新的任务启动记录；此前最后一次记录为 2026-09-18 16:00:01 CST。
- 服务器检查确认 `scripts/run_daily_report.sh` 权限为 `664`（`-rw-rw-r--`），没有执行权限，而 Cron 原配置直接执行该脚本；`cron` 服务状态为 `active`。
- 修改 `deploy_to_server.ps1`，服务器解压部署包后自动为 `scripts/*.sh` 恢复执行权限，避免 Windows 部署包覆盖脚本后再次发生同类问题。
- 更新 `SERVER_README.md` 的 Cron 示例，改为通过 `/usr/bin/bash` 调用日报脚本，使定时启动不依赖脚本自身的执行位。
- 服务器上仍需执行一次 `chmod +x scripts/run_daily_report.sh`，并将现有 crontab 命令改为通过 `/usr/bin/bash` 调用；下一次北京时间 10:00 后再核对运行日志。

### 时区核实与最终 Cron 配置

- 后续检查确认服务器的 `timedatectl`、`/etc/timezone` 和 `/etc/localtime` 均为 `Asia/Shanghai`，Cron 服务没有单独的 `TZ` 环境变量。
- 历史日志中的 16:00 CST 启动记录说明 Cron 进程此前可能保留了旧的 UTC 时区状态；服务器已重启 `cron` 服务，使其重新读取当前上海时区。
- 最终将 `zmr` 用户的任务设置为 `0 10 * * *`，即按服务器本地时间每天 10:00 执行。检查 `/etc/crontab`、`/etc/cron.d`、用户 crontab 和 systemd timers 后，只发现这一条 LabAgent 日报任务，没有重复调度项。
- 服务器脚本权限已恢复为 `775`，Cron 服务状态为 `active`。下一次触发后仍需通过 `logs/daily_report.log` 核对实际运行时间。

## 2026-09-21 Database 聊天嵌入更新

- 更新 `lab_agent/web/app.py` 的 Database 子页面，将 Knowledge Base Chat iframe 地址切换为 `http://166.111.26.183/chatbot/K5FEfFUZ0b0gWgrl`。
- 保持 iframe 的自适应宽度和 `700px` 最小高度，并增加 `clipboard-write` 权限；麦克风权限继续保留。
- 修正聊天服务地址为 `http://166.111.26.183:8080/chat/K5FEfFUZ0b0gWgrl`，使用聊天服务实际监听的 `8080` 端口和 `/chat/` 路径。
