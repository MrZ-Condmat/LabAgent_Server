# Lab Agent

Lab Agent is a Streamlit-based research assistant for condensed matter physics literature monitoring and laboratory workflow support.

The current system focuses on two daily literature pipelines:

- ArXiv Daily: fetches arXiv cond-mat/new papers, scores relevance with DeepSeek, and generates HTML/JSON reports.
- Journal Daily: fetches configured journal RSS feeds, keeps articles updated today and yesterday, scores relevance with DeepSeek, and generates per-journal plus summary reports.

## Current Features

- DeepSeek-based paper scoring with a dedicated scoring API key.
- DeepSeek-based chat with a separate chat API key.
- ArXiv Daily HTML/JSON reports under `reports/ArXiv_reports`.
- Journal Daily HTML/JSON reports under per-journal folders plus `reports/Journal_Summary_reports`.
- Clickable paper titles in generated HTML reports.
- Streamlit tabs for Overview, ArXiv Daily, Journal Daily, Tools, and Logs.
- Report-specific chat panels for ArXiv and Journal papers.
- Overview DeepSeek Assistant using the same chat model configuration as Journal Daily.
- Scheduled script support for weekday ArXiv reports and daily Journal reports.

## Installation

```bash
pip install -r requirements.txt
cp .env.example .env
```

## Configuration

Edit `.env` in the project root:

```bash
DEEPSEEK_SCORING_API_KEY=your_deepseek_scoring_api_key_here
DEEPSEEK_CHAT_API_KEY=your_deepseek_chat_api_key_here
DEEPSEEK_BASE_URL=https://llmapi.paratera.com

DEBUG=false
LOG_LEVEL=INFO
STREAMLIT_PORT=8501
STREAMLIT_HOST=localhost
DEFAULT_TIMEZONE=Asia/Shanghai
```

Model names and token settings live in `lab_agent/config/models.json`:

- `arxivFilterModel`: scoring/filtering model, currently `DeepSeek-V4-Flash`.
- `chatModel`: chat model, currently `DeepSeek-V4-Pro`.

Journal RSS feeds live in `lab_agent/config/journal_feeds.json`.

## Run The Web App

```bash
streamlit run lab_agent/web/app.py
```

Or use the Windows helper:

```bat
scripts\run_web_app.bat
```

## Generate Reports From CLI

```bash
python scripts/generate_daily_report.py
```

The scheduled helper uses:

```bat
scripts\run_daily_report.bat
```

Current scheduling behavior:

- ArXiv Daily keeps weekday-only behavior when `--skip-weekends` is used.
- On weekends, ArXiv generation is skipped and logs `ArXiv no updates today`.
- Journal Daily still runs every day and filters RSS entries to today and yesterday.

## Report Layout

```text
reports/
  ArXiv_reports/
  Journal_Summary_reports/
  Nature_reports/
  Nature_Physics_reports/
  Nature_Materials_reports/
  Nature_Communications_reports/
  Science_reports/
  Physical_Review_Letters_reports/
  Physical_Review_B_reports/
  Physical_Review_X_reports/
  PNAS_reports/
  Nature_Nanotechnology_reports/
  Science_Advances_reports/
  Reviews_of_Modern_Physics_reports/
  Annual_Review_of_Condensed_Matter_Physics_reports/
```

## Main Project Structure

```text
lab_agent/
  agents/
    arxiv_daily_agent.py
    journal_daily_agent.py
  tools/
    arxiv_daily_scraper.py
    rss_daily_scraper.py
    paper_scorer.py
    daily_report_generator.py
    arxiv_chat.py
    journal_chat.py
  config/
    models.json
    journal_feeds.json
    promptArticleRecommender.txt
    arXivChatPrompt.txt
    journalChatPrompt.txt
  web/
    app.py
scripts/
  generate_daily_report.py
  run_daily_report.bat
  run_web_app.bat
```

## Verification

Basic static check:

```bash
labagent\Scripts\python.exe -m py_compile lab_agent\web\app.py lab_agent\tools\paper_scorer.py lab_agent\tools\arxiv_chat.py lab_agent\tools\journal_chat.py
```

Optional API smoke test:

```bash
labagent\Scripts\python.exe tests\test_deepseek.py
```

## Troubleshooting

- If scoring fails, check `DEEPSEEK_SCORING_API_KEY` and `DEEPSEEK_BASE_URL`.
- If chat fails, check `DEEPSEEK_CHAT_API_KEY` and `DEEPSEEK_BASE_URL`.
- If Streamlit still shows an old error after code changes, restart the Streamlit process to clear module/session cache.
- If no Journal Daily papers appear, inspect RSS dates; Journal Daily only keeps entries dated today or yesterday.
