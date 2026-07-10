
import html
import json
import os
from typing import Any, Dict, List, Optional

from ..utils import project_path_str, timestamp_str, today_str


class HighlightsReportGenerator:
    # Generate a combined Priority 3 highlights report from ArXiv and Journal Daily JSON files.

    def __init__(
        self,
        reports_dir: str = "reports/Highlights_reports",
        arxiv_reports_dir: str = "reports/ArXiv_reports",
        journal_summary_reports_dir: str = "reports/Journal_Summary_reports",
    ):
        self.reports_dir = project_path_str(reports_dir)
        self.arxiv_reports_dir = project_path_str(arxiv_reports_dir)
        self.journal_summary_reports_dir = project_path_str(journal_summary_reports_dir)
        os.makedirs(self.reports_dir, exist_ok=True)

    def generate(self, date: Optional[str] = None, force: bool = True) -> Dict[str, Any]:
        if date is None:
            date = today_str()

        html_path = os.path.join(self.reports_dir, f"{date}.html")
        json_path = os.path.join(self.reports_dir, f"{date}.json")

        if not force and os.path.exists(html_path) and os.path.exists(json_path):
            report = self.get_report(date)
            if report:
                return {"success": True, "date": date, "from_cache": True, "report": report}

        arxiv_papers, arxiv_status = self._load_priority_papers(
            os.path.join(self.arxiv_reports_dir, f"{date}.json"),
            source="ArXiv Daily",
            source_type="arxiv",
        )
        journal_papers, journal_status = self._load_priority_papers(
            os.path.join(self.journal_summary_reports_dir, f"{date}.json"),
            source="Journal Daily",
            source_type="journal",
        )
        all_papers = arxiv_papers + journal_papers

        json_data = {
            "date": date,
            "generation_time": timestamp_str(),
            "summary": {
                "total_priority_3": len(all_papers),
                "arxiv_priority_3": len(arxiv_papers),
                "journal_priority_3": len(journal_papers),
                "sources": {
                    "arxiv": arxiv_status,
                    "journal": journal_status,
                },
            },
            "all_papers": all_papers,
            "papers_by_source": {
                "ArXiv Daily": arxiv_papers,
                "Journal Daily": journal_papers,
            },
        }
        html_content = self._generate_html(json_data)

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)

        return {
            "success": True,
            "date": date,
            "html_path": html_path,
            "json_path": json_path,
            "html_content": html_content,
            "json_data": json_data,
        }

    def get_report(self, date: str) -> Optional[Dict[str, Any]]:
        html_path = os.path.join(self.reports_dir, f"{date}.html")
        json_path = os.path.join(self.reports_dir, f"{date}.json")
        if not os.path.exists(html_path) or not os.path.exists(json_path):
            return None
        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        with open(json_path, "r", encoding="utf-8") as f:
            json_data = json.load(f)
        return {
            "date": date,
            "html_path": html_path,
            "json_path": json_path,
            "html_content": html_content,
            "json_data": json_data,
        }

    def _load_priority_papers(self, json_path: str, source: str, source_type: str) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        status = {"available": False, "path": json_path, "total_papers": 0, "priority_3": 0}
        if not os.path.exists(json_path):
            status["error"] = "report_not_found"
            return [], status

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        all_papers = data.get("all_papers", [])
        if not all_papers:
            grouped = data.get("papers_by_priority", {})
            all_papers = []
            for papers in grouped.values():
                all_papers.extend(papers or [])

        priority_3 = []
        for paper in all_papers:
            if str(paper.get("score", "")) != "3":
                continue
            normalized = dict(paper)
            normalized["highlight_source"] = source
            normalized["highlight_source_type"] = source_type
            if source_type == "journal" and not normalized.get("journal"):
                normalized["journal"] = normalized.get("source", "Journal Daily")
            priority_3.append(normalized)

        status.update({
            "available": True,
            "total_papers": len(all_papers),
            "priority_3": len(priority_3),
        })
        return priority_3, status

    def _generate_html(self, report: Dict[str, Any]) -> str:
        date = report["date"]
        summary = report["summary"]
        arxiv_papers = report["papers_by_source"].get("ArXiv Daily", [])
        journal_papers = report["papers_by_source"].get("Journal Daily", [])
        status_note = self._status_note(summary.get("sources", {}))
        sections = [
            self._section_html("ArXiv Daily", arxiv_papers),
            self._section_html("Journal Daily", journal_papers),
        ]

        return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Today's Highlights - {html.escape(date)}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; background-color: #f5f5f5; color: #111827; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
        h1 {{ color: #2c3e50; border-bottom: 3px solid #660874; padding-bottom: 10px; }}
        h2 {{ color: #34495e; margin-top: 30px; }}
        .summary {{ background: #f1e7f4; padding: 15px; border-radius: 5px; margin: 20px 0; color: #2a1032; }}
        .status-note {{ color: #6b7280; font-size: 13px; margin-top: 10px; }}
        .paper {{ margin: 20px 0; padding: 20px; background: #f8f9fa; border-radius: 5px; border-left: 5px solid #e74c3c; }}
        .paper-title {{ font-weight: bold; font-size: 17px; color: #2c3e50; margin-bottom: 8px; }}
        .paper-title a {{ color: #2c3e50; text-decoration: none; }}
        .paper-title a:hover {{ color: #3498db; text-decoration: underline; }}
        .paper-meta {{ color: #7f8c8d; font-size: 14px; margin-bottom: 8px; }}
        .paper-abstract {{ margin: 10px 0; line-height: 1.55; }}
        .score-info {{ background: #ecf0f1; padding: 10px; border-radius: 3px; margin: 10px 0; font-size: 14px; }}
        .links {{ margin: 10px 0 0; }}
        .links a {{ margin-right: 15px; color: #3498db; text-decoration: none; }}
        .links a:hover {{ text-decoration: underline; }}
        .no-papers {{ color: #7f8c8d; font-style: italic; padding: 10px 0; }}
        .source-chip {{ display: inline-block; margin-bottom: 8px; padding: 4px 9px; border-radius: 999px; background: #ffffff; border: 1px solid #ead7ee; color: #660874; font-size: 12px; font-weight: 700; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Today's Highlights - {html.escape(date)}</h1>
        <div class="summary">
            <strong>Summary:</strong> {summary["total_priority_3"]} Priority 3 papers found
            | ArXiv Daily: {summary["arxiv_priority_3"]}
            | Journal Daily: {summary["journal_priority_3"]}
            {status_note}
        </div>
        {''.join(sections)}
    </div>
</body>
</html>'''

    def _status_note(self, source_status: Dict[str, Any]) -> str:
        notes = []
        if not source_status.get("arxiv", {}).get("available"):
            notes.append("ArXiv report not available")
        if not source_status.get("journal", {}).get("available"):
            notes.append("Journal summary report not available")
        if not notes:
            return ""
        return '<div class="status-note">' + html.escape("; ".join(notes)) + ".</div>"

    def _section_html(self, source_name: str, papers: List[Dict[str, Any]]) -> str:
        heading = f"<h2>Priority 3 - {html.escape(source_name)} ({len(papers)} papers)</h2>"
        if not papers:
            return heading + '<div class="no-papers">No Priority 3 papers in this source.</div>'
        return heading + "\n" + "\n".join(self._paper_html(paper, idx + 1) for idx, paper in enumerate(papers))

    def _paper_html(self, paper: Dict[str, Any], index: int) -> str:
        title = html.escape(str(paper.get("title", "No title")))
        url = str(paper.get("url", "") or "")
        pdf_url = str(paper.get("pdf_url", "") or "")
        title_html = f'<a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">{title}</a>' if url else title
        authors = html.escape(str(paper.get("authors", "No authors")))
        abstract = html.escape(str(paper.get("abstract", "No abstract available")))
        reason = html.escape(str(paper.get("reason", "No AI assessment")))
        relevance = html.escape(str(paper.get("key_relevance", "")))
        journal = html.escape(str(paper.get("journal") or paper.get("source") or paper.get("highlight_source") or ""))
        chip = f'<span class="source-chip">{journal}</span>' if journal else ""
        abstract_link = f'<a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">Abstract</a>' if url else ""
        pdf_link = f'<a href="{html.escape(pdf_url)}" target="_blank" rel="noopener noreferrer">PDF</a>' if pdf_url else ""
        links = f'<div class="links">{abstract_link} {pdf_link}</div>' if abstract_link or pdf_link else ""
        return f'''
        <div class="paper">
            {chip}
            <div class="paper-title">{index}. {title_html}</div>
            <div class="paper-meta"><strong>Authors:</strong> {authors}</div>
            <div class="paper-abstract">{abstract}</div>
            <div class="score-info"><strong>AI Assessment:</strong> {reason}<br><strong>Key Relevance:</strong> {relevance}</div>
            {links}
        </div>'''
