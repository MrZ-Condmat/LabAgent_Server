"""Recover missing dated reports from validated sources, and repair saved scores.

Install alongside rescore_saved_reports.py in a server tools directory. Source
snapshots, original-report backups and a per-paper scoring checkpoint are kept
outside release directories. Run --prepare before --apply.
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def paper_key(paper):
    return str(paper.get("id") or paper.get("url") or ""), str(paper.get("title", ""))


def date_window(day):
    return {day, (date.fromisoformat(day) - timedelta(days=1)).isoformat()}


def collect_window(pools, day):
    """Prefer the first occurrence, allowing saved successful scores to win."""
    papers = {}
    for pool in pools:
        for paper in pool:
            if paper.get("published_date_iso") in date_window(day):
                papers.setdefault(paper_key(paper), dict(paper))
    return list(papers.values())


def pending(paper):
    paper = dict(paper)
    if "score" not in paper or not paper.get("reason"):
        paper.update(score=1, reason="Error in scoring - awaiting dated backfill", key_relevance="N/A")
    return paper


def request(session, url):
    for attempt in range(3):
        try:
            result = session.get(url, timeout=30)
            result.raise_for_status()
            return result
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def arxiv_snapshot(session, url, target):
    from bs4 import BeautifulSoup
    from lab_agent.tools.arxiv_daily_scraper import ArxivDailyScraper

    response = request(session, url)
    soup = BeautifulSoup(response.content, "html.parser")
    match = re.search(r"Showing new listings for \w+, (\d{1,2} \w+ \d{4})", soup.get_text(" ", strip=True))
    if not match:
        raise RuntimeError("Cannot verify the arXiv listing date")
    actual = datetime.strptime(match.group(1), "%d %B %Y").date().isoformat()
    if actual != target:
        raise RuntimeError(f"arXiv listing is {actual}, not requested {target}; refusing to misdate papers")
    scraper = ArxivDailyScraper()
    papers = []
    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd is not None:
            paper = scraper._parse_paper_entry(dd, dt)
            if paper and paper.get("title"):
                papers.append(paper)
    total = re.search(r"Total of ([\d,]+) entries", soup.get_text(" ", strip=True))
    if not total or len(papers) != int(total.group(1).replace(",", "")):
        raise RuntimeError("Incomplete arXiv listing snapshot")
    return {"url": url, "verified_date": actual, "papers": papers}


def nature_archive(session, journal, start, end):
    from bs4 import BeautifulSoup

    if start[:4] != end[:4]:
        raise ValueError("Publisher archive recovery currently requires a single calendar year")
    rows, pages = {}, []
    reached_older = False
    for page in range(1, 31):
        url = f"https://www.nature.com/ncomms/articles?sort=PubDate&year={start[:4]}&page={page}"
        soup = BeautifulSoup(request(session, url).content, "html.parser")
        cards = soup.select("article")
        dates = []
        for card in cards:
            tag, link = card.select_one("time[datetime]"), card.select_one("h3 a[href]")
            if tag is None or link is None:
                continue
            day = tag["datetime"][:10]
            dates.append(day)
            if not start <= day <= end:
                continue
            article_url = urljoin(url, link["href"])
            summary = card.select_one(".c-card__summary")
            abstract = summary.get_text(" ", strip=True) if summary else ""
            if not abstract:
                detail = BeautifulSoup(request(session, article_url).content, "html.parser")
                section = detail.select_one("#Abs1-content, section[data-title='Abstract']")
                meta = detail.select_one("meta[name='description']")
                abstract = section.get_text(" ", strip=True) if section else (meta.get("content", "") if meta else "")
            rows[article_url] = {
                "id": article_url, "url": article_url, "pdf_url": "",
                "title": link.get_text(" ", strip=True),
                "authors": ", ".join(x.get_text(" ", strip=True) for x in card.select("li[itemprop='creator'] span[itemprop='name']")),
                "abstract": abstract or "No abstract supplied by the publisher.",
                "subjects": journal["name"], "journal": journal["name"], "source": journal["name"],
                "published_date": day, "published_date_iso": day,
                "recovery_source": "Publisher article archive", "recovery_url": url,
            }
        if not dates:
            raise RuntimeError(f"No dated article cards on {url}")
        pages.append({"url": url, "oldest": min(dates), "newest": max(dates)})
        if min(dates) < start:
            reached_older = True
            break
    if not reached_older:
        raise RuntimeError("Publisher archive scan did not cover the complete requested date window")
    return {"papers": list(rows.values()), "pages": pages, "verified_window": [start, end]}


def prepare(root, work, journals, days):
    import feedparser
    import requests
    from lab_agent.tools.rss_daily_scraper import RSSDailyScraper

    session = requests.Session()
    session.headers["User-Agent"] = "LabAgent dated-report recovery"
    source_dir = work / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    for journal in journals:
        path = source_dir / (journal["slug"] + ".json")
        if path.exists():
            continue
        response = request(session, journal["feed_url"])
        parsed = feedparser.parse(response.content)
        scraper = RSSDailyScraper()
        rows, seen = [], set()
        for entry in parsed.entries:
            paper = scraper._parse_entry(entry, journal)
            if paper and paper_key(paper) not in seen:
                seen.add(paper_key(paper))
                rows.append(paper)
        if not rows:
            raise RuntimeError(f"No valid RSS entries for {journal['slug']}; refusing to create empty historical reports")
        snapshot = {"feed_url": journal["feed_url"], "captured_at": datetime.now().astimezone().isoformat(), "papers": rows}
        write_json(path, snapshot)
        print("SOURCE_SAVED", journal["slug"], len(rows), flush=True)
    archive = source_dir / "Nature_Communications_archive.json"
    if not archive.exists():
        journal = next(j for j in journals if j["slug"] == "Nature_Communications")
        window_start = (date.fromisoformat(min(days)) - timedelta(days=1)).isoformat()
        snapshot = nature_archive(session, journal, window_start, max(days))
        write_json(archive, snapshot)
        print("PUBLISHER_ARCHIVE_SAVED", len(snapshot["papers"]), flush=True)
    for day in days:
        if date.fromisoformat(day).weekday() >= 5 or (root / "reports/ArXiv_reports" / (day + ".json")).exists():
            continue
        path = source_dir / ("arxiv_" + day + ".json")
        if not path.exists():
            snapshot = arxiv_snapshot(session, "https://arxiv.org/list/cond-mat/new?show=2000", day)
            write_json(path, snapshot)
            print("ARXIV_SAVED", day, len(snapshot["papers"]), flush=True)
    write_json(work / "prepared.json", {"dates": days, "prepared_at": datetime.now().astimezone().isoformat()})


class CheckpointScorer:
    def __init__(self, scorer, path):
        self.scorer, self.path = scorer, path
        self.model = scorer.model_config["name"]
        data = read_json(path) if path.exists() else {"model": self.model, "scores": {}}
        if data["model"] != self.model:
            raise RuntimeError("Model changed since the scoring checkpoint was written")
        self.data = data

    def _score_single_paper(self, paper):
        import hashlib
        key = hashlib.sha256(json.dumps([paper_key(paper), paper.get("abstract", "")], ensure_ascii=False).encode()).hexdigest()
        if key in self.data["scores"]:
            return self.data["scores"][key]
        result = self.scorer._score_single_paper(paper)
        if result.get("reason") and result["reason"] != "Unable to parse response" and result.get("score") in (1, 2, 3):
            self.data["scores"][key] = result
            write_json(self.path, self.data)
        return result


def run(root, work, agent, days):
    from lab_agent.tools.daily_report_generator import DailyReportGenerator
    from lab_agent.tools.highlights_report_generator import HighlightsReportGenerator
    from lab_agent.tools.paper_scorer import PaperScorer
    from rescore_saved_reports import render_report, save_report, score_papers, seed_scoring_cache

    if read_json(work / "prepared.json")["dates"] != days:
        raise RuntimeError("Prepared snapshots do not match the requested dates")
    backup = work / "backup"
    if not (backup / "complete.json").exists():
        for day in days:
            for path in (root / "reports").glob("*_reports/" + day + ".*"):
                if path.suffix in (".json", ".html"):
                    dest = backup / path.parent.name / path.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if not dest.exists():
                        shutil.copy2(path, dest)
        write_json(backup / "complete.json", {"dates": days})
    scorer = CheckpointScorer(PaperScorer(), work / "scoring_checkpoint.json")
    if scorer.model != "DeepSeek-V4.1-Flash":
        raise RuntimeError("Expected DeepSeek-V4.1-Flash for this repair")
    cache, unresolved = {}, set()
    for day in days:
        status = {"state": "running", "date": day, "pid": os.getpid()}
        write_json(work / "status.json", status)
        arxiv_path = root / "reports/ArXiv_reports" / (day + ".json")
        if date.fromisoformat(day).weekday() < 5:
            original = read_json(arxiv_path) if arxiv_path.exists() else {"all_papers": read_json(work / "sources" / ("arxiv_" + day + ".json"))["papers"]}
            papers = [pending(p) for p in original["all_papers"]]
            seed_scoring_cache(papers, cache)
            status["source"] = "ArXiv_reports"; write_json(work / "status.json", status)
            updated, repaired, left = score_papers(papers, scorer, cache)
            unresolved.update(left)
            generator = DailyReportGenerator(reports_dir=str(arxiv_path.parent))
            data, html = render_report(generator, original, updated, day, "arxiv")
            data["summary"]["backfill_source"] = "Verified dated arXiv listing or saved historical report"
            save_report(arxiv_path, data, html)
            print(day, "ArXiv_reports", "saved", len(updated), "papers", repaired, "repaired", len(left), "unresolved", flush=True)
        else:
            print(day, "ArXiv skipped: weekend", flush=True)
        for journal in agent.journals:
            slug = journal["slug"]
            path = root / "reports" / (slug + "_reports") / (day + ".json")
            status["source"] = slug; write_json(work / "status.json", status)
            if path.exists():
                original = read_json(path)
                papers = original["all_papers"]
            else:
                rss = read_json(work / "sources" / (slug + ".json"))
                saved = []
                for existing in sorted(path.parent.glob("*.json")):
                    if min(days) <= existing.stem <= max(days):
                        saved.extend(read_json(existing).get("all_papers", []))
                archive = read_json(work / "sources/Nature_Communications_archive.json")["papers"] if slug == "Nature_Communications" else []
                papers = collect_window([saved, rss["papers"], archive], day)
                rss_in_window = collect_window([rss["papers"]], day)
                original = {"summary": {
                    "date_window_dates": sorted(date_window(day), reverse=True),
                    "date_filter": "RSS/publisher publication date is report date or previous date",
                    "rss_entries_fetched": len(rss["papers"]), "date_window_articles": len(papers),
                    "filtered_out_by_date": len(rss["papers"]) - len(rss_in_window),
                    "backfill_recovery": {"rss_snapshot_at": rss["captured_at"], "publisher_archive_used": bool(archive), "historical_saved_papers_used": True},
                }}
            papers = [pending(p) for p in papers]
            seed_scoring_cache(papers, cache)
            updated, repaired, left = score_papers(papers, scorer, cache)
            unresolved.update(left)
            data, html = render_report(agent.report_generators[slug], original, updated, day, "journal", agent)
            save_report(path, data, html)
            print(day, slug, "saved", len(updated), "papers", repaired, "repaired", len(left), "unresolved", flush=True)
        result = agent._generate_summary_report({"date": day, "force": True})
        if not result.get("success"):
            raise RuntimeError(result.get("error", "Summary generation failed"))
        report = result["report_data"]
        summary_target = root / "reports/Journal_Summary_reports" / (day + ".json")
        save_report(summary_target, report["json_data"], report["html_content"])
        highlights = HighlightsReportGenerator(
            reports_dir=str(work / "highlights_staging"), arxiv_reports_dir=str(root / "reports/ArXiv_reports"),
            journal_summary_reports_dir=str(root / "reports/Journal_Summary_reports"),
        ).generate(day, force=True)
        save_report(root / "reports/Highlights_reports" / (day + ".json"), highlights["json_data"], highlights["html_content"])
        print(day, "SUMMARY_AND_HIGHLIGHTS_REBUILT", flush=True)
    state = "incomplete" if unresolved else "complete"
    write_json(work / "status.json", {"state": state, "dates": days, "unresolved": sorted(unresolved), "finished_at": datetime.now().astimezone().isoformat()})
    print(state.upper(), "dated report backfill", len(unresolved), "unresolved", flush=True)
    return 1 if unresolved else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="start", required=True)
    parser.add_argument("--through", required=True)
    parser.add_argument("--project-root", default="/data/zmr/projects/labAgent_Server")
    parser.add_argument("--work-dir", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    sys.path.insert(0, str(root))
    os.environ["LABAGENT_PROJECT_ROOT"] = str(root)
    os.environ.setdefault("LABAGENT_ENV_FILE", "/data/zmr/projects/labagent_runtime/labagent.env")
    from lab_agent.agents.journal_daily_agent import JournalDailyAgent
    from lab_agent.tools.daily_report_generator import DailyReportGenerator
    from rescore_saved_reports import dates_between

    days = list(dates_between(args.start, args.through))
    work = Path(args.work_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    agent = JournalDailyAgent({"summary_reports_dir": str(work / "summary_staging")})
    agent.journals = agent._load_journal_config()
    agent.report_generators = {j["slug"]: agent._create_report_generator(j) for j in agent.journals}
    agent.summary_generator = DailyReportGenerator(
        reports_dir=str(work / "summary_staging"), report_title="Journal Daily Summary Report",
        source_name="Configured Journal RSS Feeds", source_url="https://www.nature.com",
        include_priorities=("2", "3"), display_priorities=("3", "2"), show_subjects=False, format_journal_abstract=True,
    )
    import fcntl
    with (work / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("This backfill is already running")
        try:
            if args.prepare:
                prepare(root, work, agent.journals, days)
                print("PREPARED", work, flush=True)
                return 0
            return run(root, work, agent, days)
        except Exception as exc:
            write_json(work / "status.json", {"state": "failed", "error_type": type(exc).__name__, "error": str(exc)[:1000]})
            raise


if __name__ == "__main__":
    raise SystemExit(main())
