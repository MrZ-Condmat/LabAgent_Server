"""Repair saved daily reports by rescoring papers whose API calls failed.

This reads the papers already stored in report JSON files. It does not fetch
today's arXiv page or current RSS feeds for an historical date.
"""

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from openai import APIConnectionError, APIStatusError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lab_agent.agents.journal_daily_agent import JournalDailyAgent  # noqa: E402
from lab_agent.tools.daily_report_generator import DailyReportGenerator  # noqa: E402
from lab_agent.tools.highlights_report_generator import HighlightsReportGenerator  # noqa: E402
from lab_agent.tools.paper_scorer import PaperScorer  # noqa: E402
from lab_agent.utils import project_path_str  # noqa: E402


def needs_scoring(paper):
    reason = str(paper.get("reason", ""))
    return reason.startswith("Error in scoring") or reason == "Unable to parse response"


def seed_scoring_cache(papers, cache):
    """Reuse saved source scores when resuming before rebuilding a summary."""
    for paper in papers:
        if needs_scoring(paper) or not paper.get("reason"):
            continue
        if paper.get("score") not in (1, 2, 3):
            continue
        key = (str(paper.get("id", "")), str(paper.get("title", "")))
        cache[key] = {
            "score": paper["score"],
            "reason": paper["reason"],
            "key_relevance": paper.get("key_relevance", "N/A"),
        }


def dates_between(start, end):
    day = date.fromisoformat(start)
    last = date.fromisoformat(end)
    if day > last:
        raise ValueError("--from must be no later than --through")
    while day <= last:
        yield day.isoformat()
        day += timedelta(days=1)


def read_report(path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def replace_file(path, content):
    temporary = path.with_name(path.name + ".rescore.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def score_papers(papers, scorer, cache):
    updated = []
    repaired = 0
    unresolved = []
    for paper in papers:
        paper = dict(paper)
        if needs_scoring(paper):
            key = (str(paper.get("id", "")), str(paper.get("title", "")))
            if key not in cache:
                for attempt in range(1, 4):
                    try:
                        # This loop handles both transport errors and invalid
                        # responses, with three total requests per paper.
                        result = scorer._score_single_paper(paper)
                    except APIConnectionError as exc:
                        # Includes APITimeoutError. Preserve the original error
                        # marker if retries fail so a later run can repair it.
                        result = None
                        print(
                            f"Temporary API error for {key!r}: {type(exc).__name__}; "
                            f"attempt {attempt}/3",
                            flush=True,
                        )
                    except APIStatusError as exc:
                        if exc.status_code not in (408, 409, 429) and exc.status_code < 500:
                            raise
                        result = None
                        print(
                            f"Temporary API error for {key!r}: HTTP {exc.status_code}; "
                            f"attempt {attempt}/3",
                            flush=True,
                        )
                    if (
                        result is not None
                        and result.get("score") in (1, 2, 3)
                        and result.get("reason")
                        and result["reason"] != "Unable to parse response"
                    ):
                        cache[key] = result
                        break
                    if result is not None:
                        print(f"Unusable model response for {key!r}; attempt {attempt}/3", flush=True)
                    if attempt < 3:
                        time.sleep(5 * attempt)
                else:
                    cache[key] = None
                    print(f"UNRESOLVED: {key!r}; keeping original failure marker", flush=True)
            if cache[key] is None:
                unresolved.append(key)
            else:
                paper.update(cache[key])
                repaired += 1
        updated.append(paper)
    return updated, repaired, unresolved


def render_report(generator, original, papers, report_date, kind, journal_agent=None):
    displayed = papers if kind == "arxiv" else [p for p in papers if int(p.get("score", 1)) >= 2]
    html_groups = generator._organize_by_priority(displayed)
    json_groups = generator._organize_by_priority(papers, include_priorities=("1", "2", "3"))
    output = generator._generate_json_report(json_groups, report_date)
    old_summary = original.get("summary", {})
    counts = output["summary"]["priority_counts"]
    output["summary"].update(old_summary)
    output["summary"].update({
        "total_papers": len(papers),
        "priority_counts": counts,
    })
    if kind != "arxiv":
        output["summary"].update({
            "saved_articles": len(displayed),
            "total_scored": len(papers),
            "total_updated_articles": len(papers),
            "display_priority_counts": {"2": counts["2"], "3": counts["3"]},
            "filtered_out_priority_1": counts["1"],
        })
    for field in ("journal_summaries", "source_reports", "missing_journals"):
        if field in original:
            output[field] = original[field]
    html = generator._generate_html_report(html_groups, report_date)
    if kind != "arxiv":
        html = journal_agent._add_date_window_note(html, output["summary"])
    if kind == "summary":
        html = journal_agent._add_summary_journal_breakdown(
            html, output.get("journal_summaries", []), output.get("missing_journals", [])
        )
    return output, html


def save_report(json_path, data, html):
    replace_file(json_path, json.dumps(data, indent=2, ensure_ascii=False))
    replace_file(json_path.with_suffix(".html"), html)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="start", required=True, help="First report date, YYYY-MM-DD")
    parser.add_argument("--through", required=True, help="Last report date, YYYY-MM-DD")
    parser.add_argument("--apply", action="store_true", help="Call the scoring API and replace reports")
    parser.add_argument("--reports-root", default="reports")
    args = parser.parse_args()
    reports_root = Path(project_path_str(args.reports_root))
    journal_agent = JournalDailyAgent({"summary_reports_dir": str(reports_root / "Journal_Summary_reports")})
    journal_agent.journals = journal_agent._load_journal_config()
    for journal in journal_agent.journals:
        journal["reports_dir"] = str(reports_root / f"{journal['slug']}_reports")
    journal_agent.report_generators = {
        journal["slug"]: journal_agent._create_report_generator(journal)
        for journal in journal_agent.journals
    }
    journal_agent.summary_generator = DailyReportGenerator(
        reports_dir=str(reports_root / "Journal_Summary_reports"),
        report_title="Journal Daily Summary Report",
        source_name="Configured Journal RSS Feeds",
        source_url="https://www.nature.com",
        include_priorities=("2", "3"),
        display_priorities=("3", "2"),
        show_subjects=False,
        format_journal_abstract=True,
    )
    arxiv_generator = DailyReportGenerator(reports_dir=str(reports_root / "ArXiv_reports"))
    scorer = PaperScorer() if args.apply else None
    cache = {}
    unresolved_keys = set()
    for report_date in dates_between(args.start, args.through):
        sources = [("arxiv", arxiv_generator, reports_root / "ArXiv_reports" / f"{report_date}.json")]
        sources += [
            ("journal", journal_agent.report_generators[j["slug"]],
             reports_root / f"{j['slug']}_reports" / f"{report_date}.json")
            for j in journal_agent.journals
        ]
        sources += [("summary", journal_agent.summary_generator,
                     reports_root / "Journal_Summary_reports" / f"{report_date}.json")]
        revised_journals = {}
        for kind, generator, path in sources:
            original = read_report(path)
            if original is None:
                print(f"{report_date} MISSING {path.parent.name}", flush=True)
                continue
            papers = original.get("all_papers", [])
            failed = sum(needs_scoring(p) for p in papers)
            print(f"{report_date} {path.parent.name}: {len(papers)} papers, {failed} to rescore", flush=True)
            if not args.apply:
                continue
            if kind != "summary":
                seed_scoring_cache(papers, cache)
            if kind == "summary" and revised_journals:
                summaries = [dict(item) for item in original.get("journal_summaries", [])]
                for item in summaries:
                    counts = revised_journals.get(item.get("slug"))
                    if counts is None:
                        continue
                    item["priority_counts"] = counts
                    item["saved_articles"] = counts["2"] + counts["3"]
                    item["total_papers"] = item["saved_articles"]
                    item["filtered_out_priority_1"] = counts["1"]
                original["journal_summaries"] = summaries
            if not failed and kind != "summary":
                if kind == "journal":
                    revised_journals[path.parent.name.rsplit("_reports", 1)[0]] = {
                        str(k): len(v) for k, v in original.get("papers_by_priority", {}).items()
                    }
                continue
            updated, repaired, unresolved = score_papers(papers, scorer, cache)
            unresolved_keys.update(unresolved)
            data, html = render_report(generator, original, updated, report_date, kind, journal_agent)
            if kind == "journal":
                revised_journals[path.parent.name.rsplit("_reports", 1)[0]] = data["summary"]["priority_counts"]
            save_report(path, data, html)
            print(
                f"{report_date} {path.parent.name}: saved, {repaired} repaired, "
                f"{len(unresolved)} unresolved",
                flush=True,
            )
        if args.apply and (
            (reports_root / "ArXiv_reports" / f"{report_date}.json").exists()
            or (reports_root / "Journal_Summary_reports" / f"{report_date}.json").exists()
        ):
            highlights = HighlightsReportGenerator(
                reports_dir=str(reports_root / "Highlights_reports"),
                arxiv_reports_dir=str(reports_root / "ArXiv_reports"),
                journal_summary_reports_dir=str(reports_root / "Journal_Summary_reports"),
            )
            highlights.generate(report_date, force=True)
            print(f"{report_date} Highlights_reports: rebuilt", flush=True)
    if unresolved_keys:
        print(f"INCOMPLETE: {len(unresolved_keys)} papers still need scoring", flush=True)
        return 1
    if args.apply:
        print("COMPLETE: all available failed scores repaired", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
