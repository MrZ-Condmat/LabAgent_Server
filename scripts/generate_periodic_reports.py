"""Generate due Priority 3 weekly/monthly digests from saved daily JSON only."""

import argparse
from datetime import date
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lab_agent.tools.periodic_report_generator import PeriodicReportGenerator
from lab_agent.utils import load_project_dotenv, today_str


def generate_due_reports(run_date, *, reports_root=None, arxiv_reports_dir=None, journal_reports_dir=None, period="due"):
    anchor = date.fromisoformat(run_date)
    kinds = (["weekly"] if anchor.weekday() == 4 else []) + (["monthly"] if anchor.day == 1 else [])
    if period != "due":
        kinds = [period]
    results = []
    if not kinds:
        return results
    generator = PeriodicReportGenerator(reports_root=reports_root, arxiv_reports_dir=arxiv_reports_dir,
                                        journal_reports_dir=journal_reports_dir)
    for kind in kinds:
        report = generator.generate(kind, anchor)
        summary = report["summary"]
        print(f"{kind.title()} report {report['period_key']}: {summary['total_priority_3']} unique Priority 3 articles; "
              f"source coverage {'complete' if summary['coverage_complete'] else 'INCOMPLETE'}.")
        results.append(report)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-date", help="Anchor date YYYY-MM-DD; weekly requires Friday, monthly requires day 1.")
    parser.add_argument("--period", choices=("due", "weekly", "monthly"), default="due")
    parser.add_argument("--reports-root", help="Root containing shared input/output report directories.")
    args = parser.parse_args(argv)
    load_project_dotenv()
    try:
        results = generate_due_reports(args.run_date or today_str(), reports_root=args.reports_root, period=args.period)
    except (OSError, ValueError):
        print("Periodic report generation failed; check dates and report directory access.", file=sys.stderr)
        return 1
    return 0 if all(r["summary"]["coverage_complete"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
