"""Generate daily ArXiv and journal reports once.

This script is intended for scheduled execution from cron or systemd timers on
Linux servers. It resolves project-relative paths under the LabAgent server root
so report output is stable even when launched from a scheduler.
"""

import argparse
import asyncio
import sys
from pathlib import Path


LOCAL_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(LOCAL_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(LOCAL_PROJECT_ROOT))

from lab_agent.agents.arxiv_daily_agent import ArxivDailyAgent  # noqa: E402
from lab_agent.agents.journal_daily_agent import JournalDailyAgent  # noqa: E402
from lab_agent.tools.highlights_report_generator import HighlightsReportGenerator  # noqa: E402
from lab_agent.utils import load_project_dotenv, now, project_path_str, today_str  # noqa: E402


DEFAULT_ARXIV_URL = "https://arxiv.org/list/cond-mat/new"


async def generate_arxiv_report(args: argparse.Namespace, report_date: str) -> bool:
    agent = ArxivDailyAgent({"reports_dir": args.reports_dir})
    await agent.initialize()

    try:
        result = await agent.process_task(
            {
                "type": "generate_daily_report",
                "date": report_date,
                "url": args.url,
            }
        )
    finally:
        await agent.cleanup()

    if result.get("success"):
        cache_note = " (from cache)" if result.get("from_cache") else ""
        print(
            f"ArXiv report {report_date} complete{cache_note}: "
            f"{result.get('total_papers', 0)} papers"
        )
        print(f"ArXiv priority counts: {result.get('priority_counts', {})}")
        return True

    print(f"ArXiv report generation failed: {result.get('error', 'unknown error')}")
    return False


async def generate_journal_reports(args: argparse.Namespace, report_date: str) -> bool:
    agent = JournalDailyAgent(
        {
            "reports_base_dir": args.journal_reports_base_dir,
            "summary_reports_dir": args.journal_summary_reports_dir,
        }
    )
    await agent.initialize()

    try:
        result = await agent.process_task(
            {
                "type": "generate_all_reports",
                "date": report_date,
            }
        )
    finally:
        await agent.cleanup()

    for item in result.get("results", []):
        journal_name = item.get("journal", {}).get("name", "Unknown journal")
        if item.get("success"):
            cache_note = " (from cache)" if item.get("from_cache") else ""
            print(
                f"{journal_name} report {report_date} complete{cache_note}: "
                f"{item.get('total_papers', 0)} saved articles from "
                f"{item.get('total_in_date_window', item.get('total_scored', 0))} today/yesterday articles "
                f"({item.get('total_fetched', 0)} RSS entries fetched)"
            )
        else:
            print(f"{journal_name} report generation failed: {item.get('error', 'unknown error')}")

    summary_result = result.get("summary_result", {})
    if summary_result.get("success"):
        print(
            f"Journal summary report {report_date} complete: "
            f"{summary_result.get('total_papers', 0)} saved articles from "
            f"{summary_result.get('date_window_articles', 0)} today/yesterday articles "
            f"({summary_result.get('rss_entries_fetched', 0)} RSS entries fetched)"
        )
        print(f"Journal priority counts: {summary_result.get('priority_counts', {})}")
    else:
        print(
            "Journal summary report generation failed: "
            f"{summary_result.get('error', 'unknown error')}"
        )

    if result.get("success"):
        print(result.get("message", "Journal reports complete"))
        return True

    print(result.get("message", "One or more journal reports failed"))
    return False


def generate_highlights_report(args: argparse.Namespace, report_date: str) -> bool:
    generator = HighlightsReportGenerator(
        reports_dir=args.highlights_reports_dir,
        arxiv_reports_dir=args.reports_dir,
        journal_summary_reports_dir=args.journal_summary_reports_dir,
    )
    result = generator.generate(report_date, force=True)
    if result.get("success"):
        summary = result.get("json_data", {}).get("summary", {})
        print(
            f"Today's highlights report {report_date} complete: "
            f"{summary.get('total_priority_3', 0)} Priority 3 papers "
            f"(ArXiv {summary.get('arxiv_priority_3', 0)}, "
            f"Journal {summary.get('journal_priority_3', 0)})"
        )
        return True

    print(f"Today's highlights report generation failed: {result.get('error', 'unknown error')}")
    return False


async def run_once(args: argparse.Namespace) -> int:
    load_project_dotenv()

    report_date = args.date or today_str()
    is_weekend = now().weekday() >= 5

    successes = []
    if not args.skip_arxiv:
        if args.skip_weekends and is_weekend:
            print(f"ArXiv no updates today ({report_date}); skipping ArXiv report generation.")
        else:
            successes.append(await generate_arxiv_report(args, report_date))

    if not args.skip_journals:
        successes.append(await generate_journal_reports(args, report_date))

    if not args.skip_highlights:
        successes.append(generate_highlights_report(args, report_date))

    if not successes:
        print("Nothing to generate: ArXiv, journal, and highlights reports were skipped.")
        return 0

    return 0 if all(successes) else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate one combined daily report run.")
    parser.add_argument("--date", help="Report date in YYYY-MM-DD format. Defaults to today in DEFAULT_TIMEZONE.")
    parser.add_argument("--url", default=DEFAULT_ARXIV_URL, help="ArXiv list URL to scrape.")
    parser.add_argument("--reports-dir", default="reports/ArXiv_reports", help="Directory for ArXiv reports.")
    parser.add_argument(
        "--journal-reports-base-dir",
        default="reports",
        help="Base directory for per-journal reports.",
    )
    parser.add_argument(
        "--journal-summary-reports-dir",
        default="reports/Journal_Summary_reports",
        help="Directory for combined Journal Daily summary reports.",
    )
    parser.add_argument(
        "--highlights-reports-dir",
        default="reports/Highlights_reports",
        help="Directory for combined Priority 3 highlights reports.",
    )
    parser.add_argument(
        "--skip-arxiv",
        action="store_true",
        help="Do not generate the ArXiv Daily report.",
    )
    parser.add_argument(
        "--skip-journals",
        action="store_true",
        help="Do not generate Journal Daily reports.",
    )
    parser.add_argument(
        "--skip-highlights",
        action="store_true",
        help="Do not generate the combined Priority 3 highlights report.",
    )
    parser.add_argument(
        "--skip-weekends",
        action="store_true",
        help="Skip ArXiv generation on Saturdays/Sundays in DEFAULT_TIMEZONE; Journal Daily still runs.",
    )
    args = parser.parse_args()
    args.reports_dir = project_path_str(args.reports_dir)
    args.journal_reports_base_dir = project_path_str(args.journal_reports_base_dir)
    args.journal_summary_reports_dir = project_path_str(args.journal_summary_reports_dir)
    args.highlights_reports_dir = project_path_str(args.highlights_reports_dir)
    return args


def main() -> int:
    return asyncio.run(run_once(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())