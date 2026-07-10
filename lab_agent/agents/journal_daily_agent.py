import html
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .base_agent import BaseAgent
from ..tools.daily_report_generator import DailyReportGenerator
from ..tools.paper_scorer import PaperScorer
from ..tools.rss_daily_scraper import RSSDailyScraper
from ..utils import project_path_str, today_str


class JournalDailyAgent(BaseAgent):
    def __init__(self, config: Dict[str, Any]):
        super().__init__("JournalDailyAgent", config)
        self.scraper = None
        self.scorer = None
        self.journals: List[Dict[str, str]] = []
        self.report_generators: Dict[str, DailyReportGenerator] = {}
        self.summary_reports_dir = project_path_str(
            self.config.get("summary_reports_dir", "reports/Journal_Summary_reports")
        )
        self.summary_generator = None

    async def initialize(self) -> None:
        self.logger.info("Initializing Journal Daily Agent")
        self.scraper = RSSDailyScraper()
        self.scorer = PaperScorer()
        self.journals = self._load_journal_config()
        self.report_generators = {
            journal["slug"]: self._create_report_generator(journal)
            for journal in self.journals
        }
        self.summary_generator = DailyReportGenerator(
            reports_dir=self.summary_reports_dir,
            report_title="Journal Daily Summary Report",
            source_name="Configured Journal RSS Feeds",
            source_url="https://www.nature.com",
            include_priorities=("2", "3"),
            display_priorities=("3", "2"),
            show_subjects=False,
            format_journal_abstract=True,
        )
        self.logger.info("Journal Daily Agent initialized with %s journals", len(self.journals))

    async def process_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        task_type = task.get("type", "generate_daily_report")

        if task_type == "generate_daily_report":
            return await self._generate_daily_report(task)
        if task_type == "generate_all_reports":
            return await self._generate_all_reports(task)
        if task_type == "generate_summary_report":
            return self._generate_summary_report(task)
        if task_type == "list_summary_reports":
            return self._list_summary_reports()
        if task_type == "get_summary_report":
            return self._get_summary_report(task.get("date"))
        if task_type == "clear_summary_reports":
            return self._clear_summary_reports()
        if task_type == "list_reports":
            return self._list_reports(task.get("journal"))
        if task_type == "get_report":
            return self._get_report(task.get("journal"), task.get("date"))
        if task_type == "clear_reports":
            return self._clear_reports(task.get("journal"))
        if task_type == "list_journals":
            return {"success": True, "journals": self.journals}

        return {"success": False, "error": f"Unknown task type: {task_type}"}

    async def _generate_all_reports(self, task: Dict[str, Any]) -> Dict[str, Any]:
        report_date = task.get("date") or today_str()
        force = bool(task.get("force", False))
        results = []
        success_count = 0
        for journal in self.journals:
            result = await self._generate_daily_report({
                "type": "generate_daily_report",
                "journal": journal["slug"],
                "date": report_date,
                "force": force,
            })
            results.append(result)
            if result.get("success"):
                success_count += 1

        summary_result = self._generate_summary_report({"date": report_date, "force": True})

        return {
            "success": success_count == len(self.journals) and summary_result.get("success", False),
            "results": results,
            "summary_result": summary_result,
            "success_count": success_count,
            "total_journals": len(self.journals),
            "message": (
                f"Generated {success_count}/{len(self.journals)} journal reports "
                f"and {'created' if summary_result.get('success') else 'failed to create'} the summary report"
            ),
        }

    async def _generate_daily_report(self, task: Dict[str, Any]) -> Dict[str, Any]:
        try:
            journal = self._get_journal(task.get("journal"))
            if journal is None:
                return {"success": False, "error": "Unknown journal"}

            date = task.get("date") or today_str()
            force = bool(task.get("force", False))
            generator = self.report_generators[journal["slug"]]

            existing_report = generator.get_report(date)
            if existing_report and not force:
                summary = existing_report["json_data"].get("summary", {})
                counts = summary.get("priority_counts", {"2": 0, "3": 0})
                p2_count = int(counts.get("2", counts.get(2, 0)) or 0)
                p3_count = int(counts.get("3", counts.get(3, 0)) or 0)
                saved_articles = int(summary.get("saved_articles", p2_count + p3_count) or 0)
                total_scored = int(summary.get("total_scored", summary.get("date_window_articles", summary.get("total_papers", 0))) or 0)
                return {
                    "success": True,
                    "journal": journal,
                    "date": date,
                    "total_papers": saved_articles,
                    "saved_articles": saved_articles,
                    "total_scored": total_scored,
                    "total_in_date_window": summary.get("date_window_articles", total_scored),
                    "filtered_out_priority_1": summary.get("filtered_out_priority_1", max(0, total_scored - saved_articles)),
                    "priority_counts": {"2": p2_count, "3": p3_count},
                    "report_data": {
                        "date": date,
                        "html_content": existing_report["html_content"],
                        "json_data": existing_report["json_data"],
                    },
                    "message": f"Loaded existing {journal['name']} report for {date}",
                    "from_cache": True,
                }

            if force and existing_report:
                for suffix in ("json", "html"):
                    path = os.path.join(generator.reports_dir, f"{date}.{suffix}")
                    if os.path.exists(path):
                        os.remove(path)

            date_window_dates = self._journal_date_window(date)
            articles = self.scraper.fetch_feed_articles(journal)
            if not articles:
                return {
                    "success": False,
                    "journal": journal,
                    "date": date,
                    "date_window_dates": date_window_dates,
                    "error": f"No RSS articles found for {journal['name']}",
                }

            date_window_articles = self._filter_articles_by_update_dates(articles, date_window_dates)
            if date_window_articles:
                scored_articles = self.scorer.batch_score_papers(date_window_articles, batch_size=3)
                kept_articles = [paper for paper in scored_articles if int(paper.get("score", 1)) >= 2]
            else:
                scored_articles = []
                kept_articles = []

            report_data = generator.generate_daily_report(
                kept_articles,
                date,
                json_papers=scored_articles,
                json_include_priorities=("1", "2", "3"),
            )

            priority_counts = {"2": 0, "3": 0}
            all_priority_counts = {"1": 0, "2": 0, "3": 0}
            for paper in scored_articles:
                score = str(paper.get("score", 1))
                if score in all_priority_counts:
                    all_priority_counts[score] += 1
                if score in priority_counts:
                    priority_counts[score] += 1

            report_metadata = {
                "date_filter": "RSS published/updated date is report date or previous date",
                "date_window_dates": date_window_dates,
                "rss_entries_fetched": len(articles),
                "date_window_articles": len(date_window_articles),
                "filtered_out_by_date": len(articles) - len(date_window_articles),
                "total_scored": len(scored_articles),
                "total_updated_articles": len(scored_articles),
                "saved_articles": len(kept_articles),
                "display_priority_counts": priority_counts,
                "filtered_out_priority_1": all_priority_counts["1"],
            }
            self._annotate_journal_report(report_data, report_metadata)

            return {
                "success": True,
                "journal": journal,
                "date": date,
                "date_window_dates": date_window_dates,
                "total_fetched": len(articles),
                "total_in_date_window": len(date_window_articles),
                "total_scored": len(scored_articles),
                "total_papers": len(kept_articles),
                "saved_articles": len(kept_articles),
                "total_updated_articles": len(scored_articles),
                "filtered_out_by_date": len(articles) - len(date_window_articles),
                "filtered_out_priority_1": all_priority_counts["1"],
                "priority_counts": priority_counts,
                "report_data": report_data,
                "message": (
                    f"Generated {journal['name']} report with {len(kept_articles)} "
                    f"priority 2/3 articles from {len(date_window_articles)} today/yesterday RSS articles "
                    f"({len(articles)} RSS entries fetched)"
                ),
            }
        except Exception as e:
            self.logger.error("Error generating journal report: %s", e)
            return {"success": False, "error": str(e)}

    def _journal_date_window(self, date: str) -> List[str]:
        report_day = datetime.strptime(date, "%Y-%m-%d").date()
        previous_day = report_day - timedelta(days=1)
        return [report_day.isoformat(), previous_day.isoformat()]

    def _filter_articles_by_update_dates(self, articles: List[Dict[str, str]], date_window_dates: List[str]) -> List[Dict[str, str]]:
        allowed_dates = set(date_window_dates)
        return [
            article
            for article in articles
            if article.get("published_date_iso") in allowed_dates
        ]

    def _annotate_journal_report(self, report_data: Dict[str, Any], metadata: Dict[str, Any]) -> None:
        json_data = report_data.get("json_data")
        if not json_data:
            return

        json_data.setdefault("summary", {}).update(metadata)
        json_path = report_data.get("json_path")
        if json_path:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(json_data, f, indent=2, ensure_ascii=False)

        html_content = report_data.get("html_content", "")
        html_path = report_data.get("html_path")
        if html_content and html_path:
            report_data["html_content"] = self._add_date_window_note(html_content, metadata)
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(report_data["html_content"])

    def _add_date_window_note(self, html_content: str, metadata: Dict[str, Any]) -> str:
        if "journal-date-window" in html_content:
            return html_content

        date_window_dates = metadata.get("date_window_dates", [])
        date_text = ", ".join(html.escape(str(item)) for item in date_window_dates)
        note = (
            '\n        <div class="journal-date-window" style="margin: 12px 0 20px; padding: 12px 15px; '
            'background: #f1f6f8; border-radius: 5px; color: #2c3e50;">\n'
            f'            <strong>RSS Date Window:</strong> today and yesterday updates ({date_text}). '
            f'RSS entries fetched: {int(metadata.get("rss_entries_fetched", 0))}; '
            f'in date window: {int(metadata.get("date_window_articles", 0))}.\n'
            '        </div>\n'
        )

        marker = '        </div>\n\n'
        if marker in html_content:
            return html_content.replace(marker, '        </div>\n' + note + '\n', 1)

        return html_content.replace('</body>', note + '\n</body>', 1)


    def _generate_summary_report(self, task: Dict[str, Any]) -> Dict[str, Any]:
        date = task.get("date") or today_str()
        force = task.get("force", False)
        date_window_dates = self._journal_date_window(date)

        existing_report = self.summary_generator.get_report(date)
        if existing_report and not force:
            summary = existing_report["json_data"].get("summary", {})
            return {
                "success": True,
                "date": date,
                "total_papers": summary.get("total_papers", 0),
                "priority_counts": summary.get("priority_counts", {"2": 0, "3": 0}),
                "report_data": {
                    "date": date,
                    "html_content": existing_report["html_content"],
                    "json_data": existing_report["json_data"],
                },
                "message": f"Loaded existing Journal Daily summary report for {date}",
                "from_cache": True,
            }

        display_papers = []
        all_updated_papers = []
        journal_summaries = []
        source_reports = []
        missing_journals = []

        for journal in self.journals:
            report = self.report_generators[journal["slug"]].get_report(date)
            if report is None:
                missing_journals.append(journal["name"])
                continue

            data = report["json_data"]
            summary = data.get("summary", {})
            counts = summary.get("priority_counts", {})
            p2_count = int(counts.get("2", counts.get(2, 0)) or 0)
            p3_count = int(counts.get("3", counts.get(3, 0)) or 0)
            saved_articles = int(summary.get("saved_articles", p2_count + p3_count) or 0)
            total_scored = int(summary.get("total_scored", summary.get("date_window_articles", summary.get("total_papers", 0))) or 0)
            filtered_out_priority_1 = int(
                summary.get("filtered_out_priority_1", max(0, total_scored - saved_articles))
                or 0
            )
            journal_summaries.append({
                "name": journal["name"],
                "slug": journal["slug"],
                "total_papers": saved_articles,
                "saved_articles": saved_articles,
                "total_updated_articles": total_scored,
                "rss_entries_fetched": summary.get("rss_entries_fetched", 0),
                "date_window_articles": summary.get("date_window_articles", 0),
                "filtered_out_by_date": summary.get("filtered_out_by_date", 0),
                "total_scored": total_scored,
                "filtered_out_priority_1": filtered_out_priority_1,
                "priority_counts": {
                    "1": filtered_out_priority_1,
                    "2": p2_count,
                    "3": p3_count,
                },
            })
            source_reports.append({
                "name": journal["name"],
                "slug": journal["slug"],
                "html_path": report.get("html_path", ""),
                "json_path": report.get("json_path", ""),
            })

            for paper in data.get("all_papers", []):
                normalized = paper.copy()
                normalized.setdefault("source", journal["name"])
                normalized.setdefault("journal", journal["name"])
                if not normalized.get("subjects"):
                    normalized["subjects"] = journal["name"]
                all_updated_papers.append(normalized)
                if int(normalized.get("score", 1)) >= 2:
                    display_papers.append(normalized)

        if not all_updated_papers and not journal_summaries:
            return {
                "success": False,
                "date": date,
                "error": "No journal reports available to summarize",
                "missing_journals": missing_journals,
            }

        priority_counts = {"2": 0, "3": 0}
        all_priority_counts = {"1": 0, "2": 0, "3": 0}
        for paper in all_updated_papers:
            score = str(paper.get("score", 1))
            if score in all_priority_counts:
                all_priority_counts[score] += 1
            if score in priority_counts:
                priority_counts[score] += 1

        old_json_path = os.path.join(self.summary_reports_dir, f"{date}.json")
        old_html_path = os.path.join(self.summary_reports_dir, f"{date}.html")
        if force:
            for path in (old_json_path, old_html_path):
                if os.path.exists(path):
                    os.remove(path)

        report_data = self.summary_generator.generate_daily_report(
            display_papers,
            date,
            json_papers=all_updated_papers,
            json_include_priorities=("1", "2", "3"),
        )
        json_data = report_data["json_data"]
        json_data["journal_summaries"] = journal_summaries
        json_data["source_reports"] = source_reports
        json_data["missing_journals"] = missing_journals
        total_rss_entries = sum(item.get("rss_entries_fetched", 0) for item in journal_summaries)
        total_date_window_articles = sum(item.get("date_window_articles", 0) for item in journal_summaries)
        total_filtered_out_by_date = sum(item.get("filtered_out_by_date", 0) for item in journal_summaries)
        total_scored = sum(item.get("total_scored", item.get("date_window_articles", 0)) for item in journal_summaries)
        total_filtered_out_priority_1 = sum(item.get("filtered_out_priority_1", 0) for item in journal_summaries)

        json_data["summary"]["priority_counts"] = all_priority_counts
        json_data["summary"]["display_priority_counts"] = priority_counts
        json_data["summary"]["total_papers"] = len(all_updated_papers)
        json_data["summary"]["saved_articles"] = len(display_papers)
        json_data["summary"]["date_filter"] = "RSS published/updated date is report date or previous date"
        json_data["summary"]["date_window_dates"] = date_window_dates
        json_data["summary"]["rss_entries_fetched"] = total_rss_entries
        json_data["summary"]["date_window_articles"] = total_date_window_articles
        json_data["summary"]["filtered_out_by_date"] = total_filtered_out_by_date
        json_data["summary"]["total_scored"] = total_scored
        json_data["summary"]["total_updated_articles"] = total_scored
        json_data["summary"]["filtered_out_priority_1"] = total_filtered_out_priority_1

        with open(report_data["json_path"], "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)

        report_data["html_content"] = self._add_summary_journal_breakdown(
            report_data["html_content"],
            journal_summaries,
            missing_journals,
        )
        report_data["html_content"] = self._add_date_window_note(
            report_data["html_content"],
            {
                "date_window_dates": date_window_dates,
                "rss_entries_fetched": total_rss_entries,
                "date_window_articles": total_date_window_articles,
            },
        )
        with open(report_data["html_path"], "w", encoding="utf-8") as f:
            f.write(report_data["html_content"])

        report_data["json_data"] = json_data

        return {
            "success": True,
            "date": date,
            "total_papers": len(all_updated_papers),
            "saved_articles": len(display_papers),
            "priority_counts": priority_counts,
            "date_window_dates": date_window_dates,
            "rss_entries_fetched": total_rss_entries,
            "date_window_articles": total_date_window_articles,
            "filtered_out_by_date": total_filtered_out_by_date,
            "total_scored": total_scored,
            "filtered_out_priority_1": total_filtered_out_priority_1,
            "journal_summaries": journal_summaries,
            "missing_journals": missing_journals,
            "report_data": report_data,
            "message": f"Generated Journal Daily summary report with {len(all_updated_papers)} updated articles",
        }

    def _add_summary_journal_breakdown(
        self,
        html_content: str,
        journal_summaries: List[Dict[str, Any]],
        missing_journals: List[str],
    ) -> str:
        if "journal-breakdown" in html_content:
            return html_content

        rows = []
        for item in journal_summaries:
            counts = item.get("priority_counts", {})
            p3_count = int(counts.get("3", counts.get(3, 0)) or 0)
            p2_count = int(counts.get("2", counts.get(2, 0)) or 0)
            p1_count = int(
                counts.get("1", counts.get(1, item.get("filtered_out_priority_1", 0)))
                or 0
            )
            total_updated = int(
                item.get(
                    "total_updated_articles",
                    item.get("total_scored", item.get("date_window_articles", 0)),
                )
                or 0
            )
            rows.append(
                "<tr>"
                f"<td>{html.escape(str(item.get('name', 'Unknown')))}</td>"
                f"<td>{int(item.get('date_window_articles', 0))}</td>"
                f"<td>{total_updated}</td>"
                f"<td>{p3_count}</td>"
                f"<td>{p2_count}</td>"
                f"<td>{p1_count}</td>"
                "</tr>"
            )

        missing_block = ""
        if missing_journals:
            missing_names = ", ".join(html.escape(str(name)) for name in missing_journals)
            missing_block = (
                '<p class="missing-journals"><strong>Missing source reports:</strong> '
                f'{missing_names}</p>'
            )

        breakdown = (
            '\n        <div class="journal-breakdown" style="margin: 20px 0; padding: 15px; background: #f8f9fa; border-radius: 5px;">\n'
            '            <h3 style="margin-top: 0; color: #2c3e50;">Journal Breakdown</h3>\n'
            '            <table style="width: 100%; border-collapse: collapse; background: white;">\n'
            '                <thead>\n'
            '                    <tr>\n'
            '                        <th style="text-align: left; padding: 8px; border-bottom: 1px solid #ddd;">Journal</th>\n'
            '                        <th style="text-align: left; padding: 8px; border-bottom: 1px solid #ddd;">Today/Yesterday</th>\n'
            '                        <th style="text-align: left; padding: 8px; border-bottom: 1px solid #ddd;">Total Updated Articles</th>\n'
            '                        <th style="text-align: left; padding: 8px; border-bottom: 1px solid #ddd;">Priority 3</th>\n'
            '                        <th style="text-align: left; padding: 8px; border-bottom: 1px solid #ddd;">Priority 2</th>\n'
            '                        <th style="text-align: left; padding: 8px; border-bottom: 1px solid #ddd;">Priority 1</th>\n'
            '                    </tr>\n'
            '                </thead>\n'
            '                <tbody>\n'
            f"                    {''.join(rows)}\n"
            '                </tbody>\n'
            '            </table>\n'
            f'            {missing_block}\n'
            '        </div>\n'
        )

        summary_end = '</div>\n\n        <h2'
        if summary_end in html_content:
            return html_content.replace('</div>\n\n        <h2', '</div>\n' + breakdown + '\n        <h2', 1)

        return html_content.replace('</body>', breakdown + '\n</body>', 1)


    def _list_summary_reports(self) -> Dict[str, Any]:
        reports = self.summary_generator.list_existing_reports()
        return {"success": True, "reports": reports, "count": len(reports)}

    def _get_summary_report(self, date: str) -> Dict[str, Any]:
        if not date:
            return {"success": False, "error": "Date parameter is required"}

        report = self.summary_generator.get_report(date)
        if report is None:
            return {"success": False, "error": f"No Journal Daily summary report found for {date}"}
        return {"success": True, "report": report}

    def _clear_summary_reports(self) -> Dict[str, Any]:
        count = self.summary_generator.clear_all_reports()
        return {
            "success": True,
            "message": f"Cleared {count} Journal Daily summary report files",
            "count": count,
        }

    def _list_reports(self, journal_key: Optional[str]) -> Dict[str, Any]:
        journal = self._get_journal(journal_key)
        if journal is None:
            return {"success": False, "error": "Unknown journal"}

        reports = self.report_generators[journal["slug"]].list_existing_reports()
        return {"success": True, "journal": journal, "reports": reports, "count": len(reports)}

    def _get_report(self, journal_key: Optional[str], date: str) -> Dict[str, Any]:
        journal = self._get_journal(journal_key)
        if journal is None:
            return {"success": False, "error": "Unknown journal"}
        if not date:
            return {"success": False, "error": "Date parameter is required"}

        report = self.report_generators[journal["slug"]].get_report(date)
        if report is None:
            return {"success": False, "error": f"No report found for {journal['name']} on {date}"}
        return {"success": True, "journal": journal, "report": report}

    def _clear_reports(self, journal_key: Optional[str]) -> Dict[str, Any]:
        journal = self._get_journal(journal_key)
        if journal is None:
            return {"success": False, "error": "Unknown journal"}

        count = self.report_generators[journal["slug"]].clear_all_reports()
        return {
            "success": True,
            "journal": journal,
            "message": f"Cleared {count} {journal['name']} report files",
            "count": count,
        }

    async def cleanup(self) -> None:
        self.logger.info("Cleaning up Journal Daily Agent")
        if self.scraper:
            self.scraper.close()

    def _load_journal_config(self) -> List[Dict[str, str]]:
        config_path = self.config.get("journal_config_path") or project_path_str("lab_agent/config/journal_feeds.json")
        reports_base_dir = project_path_str(self.config.get("reports_base_dir", "reports"))

        with open(config_path, "r", encoding="utf-8-sig") as f:
            config = json.load(f)

        journals = []
        for journal in config.get("journals", []):
            normalized = dict(journal)
            normalized["slug"] = normalized.get("slug") or self._slugify(normalized["name"])
            normalized["reports_dir"] = project_path_str(
                normalized.get("reports_dir") or os.path.join(reports_base_dir, f"{normalized['slug']}_reports")
            )
            journals.append(normalized)

        return journals

    def _create_report_generator(self, journal: Dict[str, str]) -> DailyReportGenerator:
        return DailyReportGenerator(
            reports_dir=journal["reports_dir"],
            report_title=f"{journal['name']} Daily Report",
            source_name=journal["name"],
            source_url=journal["feed_url"],
            include_priorities=("2", "3"),
            display_priorities=("3", "2"),
            show_subjects=False,
            format_journal_abstract=True,
        )

    def _get_journal(self, journal_key: Optional[str]) -> Optional[Dict[str, str]]:
        if not self.journals:
            return None
        if not journal_key:
            return self.journals[0]

        key = str(journal_key).lower()
        for journal in self.journals:
            if key in (journal["slug"].lower(), journal["name"].lower()):
                return journal
        return None

    def _slugify(self, name: str) -> str:
        return "_".join(part for part in name.replace("/", " ").split() if part)
