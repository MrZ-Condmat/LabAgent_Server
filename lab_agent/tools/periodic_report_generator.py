"""Deterministic Priority 3 digests of shared daily reports; no LLM or DB access."""

from datetime import date, timedelta
import hashlib
import html
import json
import os
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import uuid

from ..utils import project_path, timestamp_str


def period_window(kind: str, run_date: date):
    if kind == "weekly":
        if run_date.weekday() != 4:
            raise ValueError("Weekly reports must end on a Friday")
        return run_date.isoformat(), run_date - timedelta(days=6), run_date
    if kind == "monthly":
        if run_date.day != 1:
            raise ValueError("Monthly reports run on the first day of the following month")
        end = run_date - timedelta(days=1)
        return end.strftime("%Y-%m"), end.replace(day=1), end
    raise ValueError("Expected weekly or monthly")


def safe_url(value):
    value = str(value or "").strip()
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in {"http", "https"} and parsed.netloc else ""
    except ValueError:
        return ""


def article_key(paper):
    doi = str(paper.get("doi") or "").strip().lower()
    if doi:
        return "doi:" + re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi)
    for field in ("id", "url", "pdf_url"):
        value = str(paper.get(field) or "").strip()
        match = re.search(r"(?:arxiv\.org/(?:abs|pdf)/|^)(\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v\d+)?(?:\.pdf)?$", value, re.I)
        if match:
            return "arxiv:" + match.group(1).lower()
        if re.match(r"^https?://(?:dx\.)?doi\.org/", value, re.I):
            return "doi:" + re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value, flags=re.I).lower()
    for field in ("url", "id"):
        value = safe_url(paper.get(field))
        if value:
            parsed = urlsplit(value)
            query = urlencode([(k, v) for k, v in parse_qsl(parsed.query) if not k.lower().startswith("utm_") and k.lower() != "fbclid"])
            return "url:" + urlunsplit(("https", parsed.netloc.lower(), parsed.path.rstrip("/"), query, ""))
    if paper.get("id"):
        return "id:" + str(paper["id"]).strip()
    title = " ".join(str(paper.get("title") or "").casefold().split())
    if title:
        return "title:" + title
    return "metadata:" + hashlib.sha256(json.dumps(paper, sort_keys=True).encode()).hexdigest()


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class PeriodicReportGenerator:
    def __init__(self, *, reports_root=None, arxiv_reports_dir=None, journal_reports_dir=None):
        self.root = Path(reports_root) if reports_root is not None else project_path("reports")
        self.inputs = {
            "arxiv": Path(arxiv_reports_dir) if arxiv_reports_dir is not None else self.root / "ArXiv_reports",
            "journal": Path(journal_reports_dir) if journal_reports_dir is not None else self.root / "Journal_Summary_reports",
        }

    def directory(self, kind):
        if kind not in {"weekly", "monthly"}:
            raise ValueError("Expected weekly or monthly")
        return self.root / ("Weekly_reports" if kind == "weekly" else "Monthly_reports")

    def list_reports(self, kind):
        pattern = r"\d{4}-\d{2}-\d{2}" if kind == "weekly" else r"\d{4}-\d{2}"
        return sorted([p.stem for p in self.directory(kind).glob("*.json") if re.fullmatch(pattern, p.stem)], reverse=True)

    def get_report(self, kind, key):
        if key not in self.list_reports(kind):
            return None
        data = json.loads((self.directory(kind) / f"{key}.json").read_text(encoding="utf-8"))
        if data.get("period_type") != kind or data.get("period_key") != key:
            raise ValueError("Report metadata does not match the selected period")
        return data

    def generate(self, kind, run_date):
        anchor = date.fromisoformat(run_date) if isinstance(run_date, str) else run_date
        key, start, end = period_window(kind, anchor)
        papers, coverage, duplicates, occurrences = {}, [], 0, 0
        day = start
        while day <= end:
            for source, directory in self.inputs.items():
                status = {"date": day.isoformat(), "source": source, "status": "available"}
                path = directory / f"{day.isoformat()}.json"
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(data, dict) or data.get("date", day.isoformat()) != day.isoformat():
                        raise ValueError("Date mismatch")
                    items = data.get("all_papers")
                    if items is None:
                        grouped = data.get("papers_by_priority")
                        if not isinstance(grouped, dict) or any(not isinstance(v, list) for v in grouped.values()):
                            raise ValueError("Invalid report shape")
                        items = [p for group in grouped.values() for p in group]
                    if not isinstance(items, list) or any(not isinstance(p, dict) for p in items):
                        raise ValueError("Invalid paper list")
                    if data.get("missing_journals") or any(
                        str(p.get("reason", "")).startswith("Error in scoring") or p.get("reason") == "Unable to parse response"
                        for p in items
                    ):
                        status["status"] = "incomplete"
                    status["priority_3_occurrences"] = sum(str(p.get("score")) == "3" for p in items)
                    for paper in items:
                        if str(paper.get("score")) != "3":
                            continue
                        occurrences += 1
                        identity = article_key(paper)
                        if identity in papers:
                            duplicates += 1
                            saved = papers[identity]
                        else:
                            saved = dict(paper, score=3, report_dates=[], report_sources=[])
                            papers[identity] = saved
                        if day.isoformat() not in saved["report_dates"]:
                            saved["report_dates"].append(day.isoformat())
                        if source not in saved["report_sources"]:
                            saved["report_sources"].append(source)
                except FileNotFoundError:
                    status["status"] = "scheduled_skip" if source == "arxiv" and day.weekday() >= 5 else "missing"
                except (OSError, ValueError, TypeError):
                    status["status"] = "invalid"
                coverage.append(status)
            day += timedelta(days=1)
        all_papers = sorted(papers.values(), key=lambda p: (p["report_dates"][0], str(p.get("title", "")).casefold()), reverse=True)
        issues = [entry for entry in coverage if entry["status"] not in {"available", "scheduled_skip"}]
        report = {
            "period_type": kind, "period_key": key, "run_date": anchor.isoformat(),
            "start_date": start.isoformat(), "end_date": end.isoformat(), "generation_time": timestamp_str(),
            "date_basis": "source_daily_report_date", "all_papers": all_papers, "coverage": coverage,
            "summary": {"total_priority_3": len(all_papers), "priority_3_occurrences": occurrences,
                        "duplicate_occurrences_removed": duplicates,
                        "arxiv_priority_3": sum("arxiv" in p["report_sources"] for p in all_papers),
                        "journal_priority_3": sum("journal" in p["report_sources"] for p in all_papers),
                        "coverage_complete": not issues, "source_issues": issues},
        }
        directory = self.directory(kind)
        atomic_write(directory / f"{key}.html", self.render_html(report))
        atomic_write(directory / f"{key}.json", json.dumps(report, indent=2, ensure_ascii=False))
        return report

    @staticmethod
    def render_html(report):
        escape = lambda value: html.escape(str(value))
        title = "Weekly Report" if report["period_type"] == "weekly" else "Monthly Report"
        cards = []
        for index, paper in enumerate(report["all_papers"], 1):
            url = safe_url(paper.get("url") or paper.get("id"))
            heading = escape(paper.get("title", "Untitled"))
            if url:
                heading = f'<a href="{escape(url)}" target="_blank" rel="noopener noreferrer">{heading}</a>'
            cards.append(f'<article><h2>{index}. {heading}</h2><p>{escape(paper.get("journal") or ", ".join(paper["report_sources"]))}</p>'
                         f'<p>Daily reports: {escape(", ".join(paper["report_dates"]))}</p>'
                         f'<p>{escape(paper.get("authors", ""))}</p><p>{escape(paper.get("abstract", ""))}</p>'
                         f'<p><strong>Assessment:</strong> {escape(paper.get("reason", ""))}</p></article>')
        warning = "" if report["summary"]["coverage_complete"] else "<p class='warning'>Some source reports are missing or incomplete. Counts may be incomplete.</p>"
        return f'''<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font:16px Arial,sans-serif;max-width:1100px;margin:40px auto;padding:20px;color:#202030}}article{{border:1px solid #ddd;border-left:4px solid #660874;border-radius:8px;padding:18px;margin:16px 0}}h1,a{{color:#660874}}p{{line-height:1.6;white-space:pre-wrap}}.warning{{background:#fff3cd;padding:15px}}</style></head><body>
<h1>{title}: {escape(report['start_date'])} – {escape(report['end_date'])}</h1>
<p>{report['summary']['total_priority_3']} unique Priority 3 articles · ArXiv + Journals · By source daily report date</p>
{warning}{''.join(cards) or '<p>No Priority 3 articles in available reports.</p>'}</body></html>'''
