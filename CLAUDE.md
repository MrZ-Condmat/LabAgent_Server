# Lab Agent Project Context

## Current Architecture

Lab Agent is a Streamlit dashboard and automation project for condensed matter literature monitoring.

The active production paths are:

- ArXiv Daily: scrape arXiv cond-mat/new, score papers, generate HTML/JSON reports, and chat about selected papers.
- Journal Daily: read journal RSS feeds, keep entries dated today or yesterday, score them, generate per-journal reports plus a summary report, and chat about selected reports.
- Overview: combined ArXiv + Journal priority metrics and a DeepSeek Assistant using the same chat model settings as Journal Daily.

## Active AI Configuration

The project now uses DeepSeek-compatible chat-completions endpoints through the `openai` Python package.

- Scoring key: `DEEPSEEK_SCORING_API_KEY`
- Chat key: `DEEPSEEK_CHAT_API_KEY`
- Base URL: `DEEPSEEK_BASE_URL`, defaulting to `https://llmapi.paratera.com`
- Scoring model: `lab_agent/config/models.json` -> `arxivFilterModel`, currently `DeepSeek-V4-Flash`
- Chat model: `lab_agent/config/models.json` -> `chatModel`, currently `DeepSeek-V4-Pro`

Legacy non-DeepSeek runtime paths have been removed from the active codebase. The `openai` package remains because it is the SDK used for the DeepSeek-compatible endpoint.

## Important Files

```text
lab_agent/utils/config.py                       # Environment configuration
lab_agent/config/models.json                    # Scoring/chat model names
lab_agent/config/journal_feeds.json             # Journal RSS feed list
lab_agent/config/promptArticleRecommender.txt     # Scoring prompt
lab_agent/config/arXivChatPrompt.txt            # ArXiv chat prompt
lab_agent/config/journalChatPrompt.txt          # Journal chat prompt
lab_agent/tools/paper_scorer.py                 # DeepSeek scoring
lab_agent/tools/arxiv_chat.py                   # ArXiv report chat
lab_agent/tools/journal_chat.py                 # Journal report chat
lab_agent/tools/arxiv_daily_scraper.py          # arXiv new-page scraper
lab_agent/tools/rss_daily_scraper.py            # RSS parser/date extraction
lab_agent/tools/daily_report_generator.py       # HTML/JSON report generation
lab_agent/agents/arxiv_daily_agent.py           # ArXiv orchestration
lab_agent/agents/journal_daily_agent.py         # Journal orchestration
lab_agent/web/app.py                            # Streamlit UI
scripts/generate_daily_report.py                # Scheduled/CLI report runner
```

## Report Directories

```text
reports/ArXiv_reports/
reports/Journal_Summary_reports/
reports/<Journal_Name>_reports/
```

## Journal Daily Rules

- RSS entries are parsed with structured publication/update dates.
- Only entries dated the report date or the previous date are processed.
- Priority 1 journal articles are not saved to reports.
- Summary reports include every configured journal, including journals with zero saved articles.

## Scheduled Behavior

- ArXiv Daily keeps weekday behavior when `--skip-weekends` is used.
- Weekend ArXiv runs print `ArXiv no updates today` and do not generate ArXiv reports.
- Journal Daily runs every day.

## Development Notes

- Do not commit real `.env` secrets.
- Use `labagent/Scripts/python.exe` in this workspace for verification.
- Restart Streamlit after module-level changes to avoid stale session/module cache.
- Preserve historical reports unless the user explicitly asks to migrate or regenerate them.

## Quick Verification

```bash
labagent\Scripts\python.exe -m py_compile lab_agent\web\app.py scripts\generate_daily_report.py
labagent\Scripts\python.exe tests\test_deepseek.py
```

For frontend sanity checks, use Streamlit AppTest or run:

```bash
labagent\Scripts\streamlit.exe run lab_agent\web\app.py
```
