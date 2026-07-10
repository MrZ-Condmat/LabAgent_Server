import os
import json
import logging
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import List, Dict, Optional
from jinja2 import Template
from ..utils import project_path_str, timestamp_str, today_str


class DailyReportGenerator:
    def __init__(
        self,
        reports_dir: str = "reports",
        report_title: str = "ArXiv Daily Report",
        source_name: str = "ArXiv cond-mat/new",
        source_url: str = "https://arxiv.org/list/cond-mat/new",
        include_priorities: tuple = ("1", "2", "3"),
        display_priorities: tuple = ("3", "2", "1"),
        show_subjects: bool = True,
        format_journal_abstract: bool = False,
    ):
        self.reports_dir = project_path_str(reports_dir)
        self.report_title = report_title
        self.source_name = source_name
        self.source_url = source_url
        self.include_priorities = tuple(str(priority) for priority in include_priorities)
        self.display_priorities = tuple(str(priority) for priority in display_priorities)
        self.show_subjects = show_subjects
        self.format_journal_abstract = format_journal_abstract
        self.logger = logging.getLogger("tools.daily_report_generator")
        
        # Create reports directory if it doesn't exist
        os.makedirs(reports_dir, exist_ok=True)
        
    def generate_daily_report(
        self,
        papers: List[Dict],
        date: str = None,
        json_papers: Optional[List[Dict]] = None,
        json_include_priorities: Optional[tuple] = None,
    ) -> Dict[str, str]:
        if date is None:
            date = today_str()
            
        # Check if report already exists
        report_path = os.path.join(self.reports_dir, f"{date}.json")
        if os.path.exists(report_path):
            self.logger.info(f"Report for {date} already exists")
            with open(report_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        
        # Separate papers by priority. HTML can intentionally render a filtered
        # subset while JSON preserves a fuller machine-readable record.
        priority_papers = self._organize_by_priority(papers)
        json_priority_papers = priority_papers
        if json_papers is not None:
            json_priority_papers = self._organize_by_priority(
                json_papers,
                include_priorities=json_include_priorities or self.include_priorities,
            )
        
        # Generate HTML and JSON reports
        html_content = self._generate_html_report(priority_papers, date)
        json_data = self._generate_json_report(json_priority_papers, date)
        
        # Save reports
        html_path = os.path.join(self.reports_dir, f"{date}.html")
        json_path = os.path.join(self.reports_dir, f"{date}.json")
        
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
            
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        
        self.logger.info(f"Generated daily report for {date}: {len(papers)} display papers")
        
        return {
            'date': date,
            'html_path': html_path,
            'json_path': json_path,
            'html_content': html_content,
            'json_data': json_data
        }
    
    def _organize_by_priority(
        self,
        papers: List[Dict],
        include_priorities: Optional[tuple] = None,
    ) -> Dict[str, List[Dict]]:
        priorities = tuple(str(priority) for priority in (include_priorities or self.include_priorities))
        priority_papers = {priority: [] for priority in priorities}
        
        for paper in papers:
            score = str(paper.get('score', 1))  # Convert to string
            if score in priority_papers:
                priority_papers[score].append(self._ensure_paper_links(paper))
        
        return priority_papers

    def _ensure_paper_links(self, paper: Dict) -> Dict:
        """Fill arXiv abstract/PDF links when an arXiv id is available."""
        updated = dict(paper)
        arxiv_id = self._extract_arxiv_id(str(updated.get('id', '')))
        if not arxiv_id:
            arxiv_id = self._extract_arxiv_id(str(updated.get('url', '')))

        if arxiv_id:
            updated['id'] = arxiv_id
            if not updated.get('url'):
                updated['url'] = f'https://arxiv.org/abs/{arxiv_id}'
            if not updated.get('pdf_url'):
                updated['pdf_url'] = f'https://arxiv.org/pdf/{arxiv_id}.pdf'

        return updated

    def _extract_arxiv_id(self, value: str) -> str:
        match = re.search(r'(?:/abs/|arXiv:)?(\d{4}\.\d{4,5})(?:v\d+)?', value or '', re.IGNORECASE)
        return match.group(1) if match else ''
    
    def _prepare_papers_for_html(self, priority_papers: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
        if not self.format_journal_abstract:
            return priority_papers

        return {
            priority: [self._prepare_journal_paper_for_html(paper) for paper in papers]
            for priority, papers in priority_papers.items()
        }

    def _prepare_journal_paper_for_html(self, paper: Dict) -> Dict:
        prepared = dict(paper)
        journal_name = prepared.get("journal") or prepared.get("source") or self.source_name
        prepared["html_journal_name"] = journal_name
        prepared["html_journal_date"] = self._journal_display_date(prepared)
        prepared["html_abstract"] = self._journal_abstract_body(
            str(prepared.get("abstract", "")),
            str(journal_name or ""),
        )
        return prepared

    def _journal_display_date(self, paper: Dict) -> str:
        abstract = str(paper.get("abstract", ""))
        for pattern in (
            r"Published\s+online:\s*([^;]+)",
            r"Published:\s*([^;]+)",
            r"Publication date:\s*([^;]+)",
        ):
            match = re.search(pattern, abstract, re.IGNORECASE)
            if match:
                return self._format_journal_display_date(match.group(1).strip())

        for key in ("published_date_iso", "published_datetime", "published_date"):
            value = str(paper.get(key, "")).strip()
            if not value:
                continue
            return self._format_journal_display_date(value)

        return ""

    def _format_journal_display_date(self, value: str) -> str:
        text = (value or "").strip()
        if not text:
            return ""

        iso_match = re.search(r"\d{4}-\d{2}-\d{2}", text)
        if iso_match:
            return iso_match.group(0)

        cleaned = re.sub(r"\b(\d{1,2})(st|nd|rd|th)\b", r"\1", text, flags=re.IGNORECASE)
        for fmt in (
            "%d %B %Y",
            "%d %b %Y",
            "%B %d, %Y",
            "%b %d, %Y",
            "%Y/%m/%d",
            "%m/%d/%Y",
        ):
            try:
                return datetime.strptime(cleaned, fmt).date().isoformat()
            except ValueError:
                pass

        try:
            return parsedate_to_datetime(cleaned).date().isoformat()
        except Exception:
            return text

    def _journal_abstract_body(self, abstract: str, journal_name: str) -> str:
        original = (abstract or "").strip()
        text = original

        if journal_name:
            text = re.sub(rf"^{re.escape(journal_name)}\s*,\s*", "", text, flags=re.IGNORECASE)

        text = re.sub(
            r"^Published\s+online:\s*[^;]+;\s*doi:\s*\S+\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"^Published\s+online:\s*[^;]+;\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^doi:\s*\S+\s*", "", text, flags=re.IGNORECASE)

        return text.strip() or original or "No abstract available"

    def _generate_html_report(self, priority_papers: Dict[str, List[Dict]], date: str) -> str:
        html_template = """
<!DOCTYPE html>
<html>
<head>
    <title>{{ report_title }} - {{ date }}</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; background-color: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        h1 { color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px; }
        h2 { color: #34495e; margin-top: 30px; }
        .priority-3 { border-left: 5px solid #e74c3c; }
        .priority-2 { border-left: 5px solid #f39c12; }
        .priority-1 { border-left: 5px solid #95a5a6; }
        .paper { margin: 20px 0; padding: 20px; background: #f8f9fa; border-radius: 5px; }
        .paper-title { font-weight: bold; font-size: 16px; color: #2c3e50; margin-bottom: 8px; }
        .paper-title a { color: #2c3e50; text-decoration: none; }
        .paper-title a:hover { color: #3498db; text-decoration: underline; }
        .paper-authors { color: #7f8c8d; font-size: 14px; margin-bottom: 8px; }
        .paper-abstract { margin: 10px 0; line-height: 1.5; }
        .journal-abstract-meta { display: inline-flex; gap: 8px; align-items: baseline; margin-right: 8px; font-weight: 700; }
        .journal-name { color: #1f4e79; }
        .journal-date { color: #8a5a00; font-style: italic; }
        .paper-subjects { font-size: 12px; color: #95a5a6; margin: 8px 0; }
        .score-info { background: #ecf0f1; padding: 8px; border-radius: 3px; margin: 8px 0; font-size: 14px; }
        .links { margin: 10px 0; }
        .links a { margin-right: 15px; color: #3498db; text-decoration: none; }
        .links a:hover { text-decoration: underline; }
        .summary { background: #e8f4f8; padding: 15px; border-radius: 5px; margin: 20px 0; }
        .no-papers { color: #7f8c8d; font-style: italic; }
    </style>
</head>
<body>
    <div class="container">
        <h1>{{ report_title }} - {{ date }}</h1>
        
        <div class="summary">
            <strong>Summary:</strong>
            {{ total_papers }} papers found
            {% for priority in display_priorities %}
            | {{ priority_labels[priority]['summary'] }}: {{ priority_counts[priority] }} papers
            {% endfor %}
        </div>

        {% for priority in display_priorities %}
        {% if priority_papers[priority] %}
        <h2>{{ priority_labels[priority]['icon'] }} {{ priority_labels[priority]['heading'] }} ({{ priority_counts[priority] }} papers)</h2>
        {% for paper in priority_papers[priority] %}
        <div class="paper priority-{{ priority }}">
            <div class="paper-title">
                {% if paper.url %}<a href="{{ paper.url }}" target="_blank" rel="noopener noreferrer">{{ paper.title }}</a>{% else %}{{ paper.title }}{% endif %}
            </div>
            <div class="paper-authors"><strong>Authors:</strong> {{ paper.authors }}</div>
            {% if show_subjects and paper.subjects %}<div class="paper-subjects"><strong>Subjects:</strong> {{ paper.subjects }}</div>{% endif %}
            {% if format_journal_abstract %}
            <div class="paper-abstract journal-abstract">
                <span class="journal-abstract-meta">
                    {% if paper.html_journal_name %}<span class="journal-name">{{ paper.html_journal_name }}</span>{% endif %}
                    {% if paper.html_journal_date %}<span class="journal-date">{{ paper.html_journal_date }}</span>{% endif %}
                </span>
                <span class="journal-abstract-text">{{ paper.html_abstract }}</span>
            </div>
            {% else %}
            <div class="paper-abstract">{{ paper.abstract }}</div>
            {% endif %}
            <div class="score-info">
                <strong>AI Assessment:</strong> {{ paper.reason }}<br>
                <strong>Key Relevance:</strong> {{ paper.key_relevance }}
            </div>
            <div class="links">
                {% if paper.url %}<a href="{{ paper.url }}" target="_blank">Abstract</a>{% endif %}
                {% if paper.pdf_url %}<a href="{{ paper.pdf_url }}" target="_blank">PDF</a>{% endif %}
            </div>
        </div>
        {% endfor %}
        {% endif %}
        {% endfor %}

        <div style="margin-top: 40px; padding: 20px; background: #ecf0f1; border-radius: 5px; text-align: center; color: #7f8c8d;">
            Generated on {{ generation_time }} | Source: <a href="{{ source_url }}" target="_blank">{{ source_name }}</a>
        </div>
    </div>
</body>
</html>
        """
        
        template = Template(html_template)
        
        priority_counts = {i: len(papers) for i, papers in priority_papers.items()}
        total_papers = sum(priority_counts.values())
        html_priority_papers = self._prepare_papers_for_html(priority_papers)
        priority_labels = {
            "3": {
                "icon": "&#128308;",
                "summary": "Priority 3",
                "heading": "Priority 3 - High Relevance",
            },
            "2": {
                "icon": "&#128993;",
                "summary": "Priority 2",
                "heading": "Priority 2 - Medium Relevance",
            },
            "1": {
                "icon": "&#9898;",
                "summary": "Priority 1",
                "heading": "Priority 1 - Lower Relevance",
            },
        }
        
        return template.render(
            date=date,
            report_title=self.report_title,
            source_name=self.source_name,
            source_url=self.source_url,
            priority_papers=html_priority_papers,
            priority_counts=priority_counts,
            show_subjects=self.show_subjects,
            format_journal_abstract=self.format_journal_abstract,
            priority_labels=priority_labels,
            display_priorities=self.display_priorities,
            total_papers=total_papers,
            generation_time=timestamp_str()
        )
    
    def _generate_json_report(self, priority_papers: Dict[str, List[Dict]], date: str) -> Dict:
        priority_counts = {i: len(papers) for i, papers in priority_papers.items()}
        
        return {
            'date': date,
            'generation_time': timestamp_str(),
            'summary': {
                'total_papers': sum(priority_counts.values()),
                'priority_counts': priority_counts
            },
            'papers_by_priority': priority_papers,
            'all_papers': [paper for papers in priority_papers.values() for paper in papers]
        }
    
    def list_existing_reports(self) -> List[str]:
        """List all existing daily reports"""
        if not os.path.exists(self.reports_dir):
            return []
            
        reports = []
        for filename in os.listdir(self.reports_dir):
            if filename.endswith('.json') and not filename.startswith('.'):
                date = filename[:-5]  # Remove .json extension
                reports.append(date)
        
        return sorted(reports, reverse=True)  # Most recent first
    
    def get_report(self, date: str) -> Optional[Dict]:
        """Get existing report for a specific date"""
        json_path = os.path.join(self.reports_dir, f"{date}.json")
        html_path = os.path.join(self.reports_dir, f"{date}.html")
        
        if not os.path.exists(json_path):
            return None
            
        with open(json_path, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
        
        html_content = ""
        if os.path.exists(html_path):
            with open(html_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
        
        return {
            'date': date,
            'json_data': json_data,
            'html_content': html_content,
            'html_path': html_path,
            'json_path': json_path
        }
    
    def clear_all_reports(self) -> int:
        """Clear all stored reports"""
        if not os.path.exists(self.reports_dir):
            return 0
            
        count = 0
        for filename in os.listdir(self.reports_dir):
            if filename.endswith(('.json', '.html')):
                os.remove(os.path.join(self.reports_dir, filename))
                count += 1
                
        self.logger.info(f"Cleared {count} report files")
        return count
