# Lab Agent Testing Guide

This guide covers the current DeepSeek-based ArXiv Daily and Journal Daily system.

## 1. Environment Setup

```bash
cd G:\labAgent\labAgent
labagent\Scripts\python.exe --version
labagent\Scripts\python.exe -m pip install -r requirements.txt
```

Create `.env` from `.env.example` and set:

```bash
DEEPSEEK_SCORING_API_KEY=...
DEEPSEEK_CHAT_API_KEY=...
DEEPSEEK_BASE_URL=https://llmapi.paratera.com
DEFAULT_TIMEZONE=Asia/Shanghai
```

## 2. Static Checks

```bash
labagent\Scripts\python.exe -m py_compile ^
  lab_agent\web\app.py ^
  lab_agent\agents\arxiv_daily_agent.py ^
  lab_agent\agents\journal_daily_agent.py ^
  lab_agent\tools\paper_scorer.py ^
  lab_agent\tools\arxiv_chat.py ^
  lab_agent\tools\journal_chat.py ^
  scripts\generate_daily_report.py
```

Expected result: no output and exit code 0.

## 3. Streamlit Startup

```bash
labagent\Scripts\streamlit.exe run lab_agent\web\app.py
```

Expected tabs:

- Overview
- ArXiv Daily
- Journal Daily
- Tools
- Logs

Overview should show combined ArXiv + Journal priority metrics and `DeepSeek Assistant - DeepSeek-V4-Pro`.

## 4. ArXiv Daily Manual Test

1. Open the `ArXiv Daily` tab.
2. Click `Generate Daily Report`.
3. Confirm a report appears under `reports/ArXiv_reports`.
4. Click `View HTML Report` once to show the report, and again to hide it.
5. Confirm paper titles are clickable and point to arXiv abstract pages.
6. Use `Chat About Papers` to ask a paper-specific question.

Expected behavior:

- Uses `DeepSeek-V4-Flash` scoring model.
- Uses `DeepSeek-V4-Pro` chat model.
- Priority 1/2/3 counts are shown for ArXiv reports.

## 5. Journal Daily Manual Test

1. Open the `Journal Daily` tab.
2. Select `Summary` or an individual journal.
3. Click `Generate Journal Daily Summary` if needed.
4. Confirm summary reports are written under `reports/Journal_Summary_reports`.
5. Confirm per-journal reports are written under their own report folders.
6. Confirm Journal Daily only saves Priority 3 and Priority 2 articles.
7. Confirm RSS date-window metrics show today/yesterday filtering.
8. Use `Chat About Papers` to ask about the selected summary or journal report.

Expected behavior:

- RSS entries are fetched from `lab_agent/config/journal_feeds.json`.
- Articles outside today/yesterday are filtered out before scoring.
- Summary includes every configured journal.

## 6. Scheduled Script Test

```bash
labagent\Scripts\python.exe scripts\generate_daily_report.py --skip-weekends
```

Expected behavior:

- On weekdays, ArXiv and Journal reports can both run.
- On weekends, ArXiv prints `ArXiv?????` and does not generate ArXiv HTML/JSON.
- Journal Daily still runs every day.

## 7. API Smoke Test

```bash
labagent\Scripts\python.exe tests\test_deepseek.py
```

Expected result: the script sends a short chat-completions request to the configured DeepSeek-compatible endpoint.

## 8. Common Issues

- `DEEPSEEK_SCORING_API_KEY` missing: scoring cannot initialize.
- `DEEPSEEK_CHAT_API_KEY` missing: chat panels cannot initialize.
- Old Streamlit error remains after code changes: restart Streamlit to clear session/module cache.
- No RSS articles in Journal Daily: the feed may not contain entries dated today or yesterday.
- ArXiv has no weekend report: this is expected when weekend skipping is enabled.
