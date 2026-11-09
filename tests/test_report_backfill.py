"""Offline recovery tests: no server, database or model requests."""

import importlib.util
import json
import logging
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
from openai import APITimeoutError, PermissionDeniedError, RateLimitError
import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tools(monkeypatch):
    rescore = load_script("rescore_saved_reports")
    monkeypatch.setitem(sys.modules, "rescore_saved_reports", rescore)
    monkeypatch.setattr(rescore.time, "sleep", lambda _: None)
    return rescore, load_script("backfill_daily_reports")


def failed_paper(paper_id="test"):
    return {"id": paper_id, "title": paper_id, "score": 1,
            "reason": "Error in scoring - defaulted to low priority", "key_relevance": "N/A"}


VALID = {"score": 3, "reason": "Directly relevant to the lab.", "key_relevance": "cuprates"}


@pytest.mark.parametrize("kind", ["scoring", "chat"])
def test_model_defaults_match_the_committed_configuration(monkeypatch, kind):
    from lab_agent.tools.paper_scorer import PaperScorer
    from lab_agent.tools.arxiv_chat import ArxivChat

    configuration = json.loads((ROOT / "lab_agent/config/models.json").read_text(encoding="utf-8"))
    cls, section = (PaperScorer, "arxivFilterModel") if kind == "scoring" else (ArxivChat, "chatModel")
    assert configuration[section]["name"] == "DeepSeek-V4.1-Flash"
    instance = cls.__new__(cls)
    instance.logger = logging.getLogger("test_model_defaults")

    def missing(*args, **kwargs):
        raise FileNotFoundError("test fixture: absent model configuration")

    monkeypatch.setattr("builtins.open", missing)
    assert instance._load_model_config()["name"] == "DeepSeek-V4.1-Flash"


class SequenceScorer:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = 0

    def _score_single_paper(self, paper):
        self.calls += 1
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


def test_timeout_retains_failure_and_continues_with_next_paper(tools):
    rescore, _ = tools
    request = httpx.Request("POST", "https://example.invalid/chat/completions")
    scorer = SequenceScorer([APITimeoutError(request=request) for _ in range(3)] + [VALID])
    cache = {}
    first = failed_paper("first")
    output, repaired, unresolved = rescore.score_papers([first, failed_paper("next")], scorer, cache)
    assert scorer.calls == 4
    assert repaired == 1 and unresolved == [("first", "first")]
    assert output[0] == first
    assert output[1]["score"] == 3
    rescore.score_papers([first], scorer, cache)
    assert scorer.calls == 4  # A duplicate summary does not repeat failed retries.


@pytest.mark.parametrize("kind", ["timeout", "rate_limit", "empty"])
def test_transient_or_empty_response_recovers_on_retry(tools, kind):
    rescore, _ = tools
    request = httpx.Request("POST", "https://example.invalid/chat/completions")
    initial = {
        "timeout": APITimeoutError(request=request),
        "rate_limit": RateLimitError("busy", response=httpx.Response(429, request=request), body={}),
        "empty": {"score": 1, "reason": "Unable to parse response", "key_relevance": "N/A"},
    }[kind]
    scorer = SequenceScorer([initial, VALID])
    output, repaired, unresolved = rescore.score_papers([failed_paper()], scorer, {})
    assert scorer.calls == 2 and repaired == 1 and not unresolved
    assert output[0]["reason"] == VALID["reason"]


def test_permission_failure_is_not_silently_skipped(tools):
    rescore, _ = tools
    request = httpx.Request("POST", "https://example.invalid/chat/completions")
    scorer = SequenceScorer([PermissionDeniedError(
        "denied", response=httpx.Response(403, request=request), body={}
    )])
    with pytest.raises(PermissionDeniedError):
        rescore.score_papers([failed_paper()], scorer, {})
    assert scorer.calls == 1


def test_valid_low_priority_and_saved_source_scores_are_preserved(tools):
    rescore, _ = tools
    low = dict(failed_paper(), reason="Valid low relevance", key_relevance="N/A")
    cache = {}
    rescore.seed_scoring_cache([low], cache)
    scorer = SequenceScorer([])
    output, repaired, unresolved = rescore.score_papers([low], scorer, cache)
    assert output == [low] and repaired == 0 and not unresolved and scorer.calls == 0
    output, repaired, unresolved = rescore.score_papers([failed_paper()], scorer, cache)
    assert output == [low] and repaired == 1 and not unresolved and scorer.calls == 0


def test_date_windows_deduplicate_and_prefer_saved_assessment(tools):
    _, backfill = tools
    saved = dict(failed_paper(), **VALID, published_date_iso="2026-11-07")
    fresh = dict(saved, reason="Another assessment")
    assert backfill.collect_window([[saved], [fresh]], "2026-11-08") == [saved]
    assert backfill.collect_window([[saved]], "2026-11-09") == []
    assert backfill.pending(saved) == saved


def listing_html(day="Monday, 9 November 2026", total=1):
    return f'''<h3>Showing new listings for {day}</h3><p>Total of {total} entries</p>
    <dl><dt><a href="/abs/2611.00001">arXiv:2611.00001</a></dt><dd>
    <div class="list-title">Title: A test paper</div>
    <div class="list-authors">Authors: Test Author</div>
    <p class="mathjax">A sufficiently detailed abstract about cuprate superconductivity.</p>
    </dd></dl>'''.encode()


@pytest.mark.parametrize("day,total,error", [
    ("Tuesday, 10 November 2026", 1, "refusing to misdate"),
    ("Monday, 9 November 2026", 2, "Incomplete arXiv listing"),
])
def test_arxiv_rejects_wrong_date_or_incomplete_listing(tools, monkeypatch, day, total, error):
    _, backfill = tools
    monkeypatch.setattr(backfill, "request", lambda *args: SimpleNamespace(content=listing_html(day, total)))
    with pytest.raises(RuntimeError, match=error):
        backfill.arxiv_snapshot(None, "https://example.invalid/list", "2026-11-09")


def test_arxiv_accepts_verified_dated_listing(tools, monkeypatch):
    _, backfill = tools
    monkeypatch.setattr(backfill, "request", lambda *args: SimpleNamespace(content=listing_html()))
    snapshot = backfill.arxiv_snapshot(None, "https://example.invalid/list", "2026-11-09")
    assert snapshot["verified_date"] == "2026-11-09"
    assert len(snapshot["papers"]) == 1
    assert snapshot["papers"][0]["id"] == "2611.00001"


def test_publisher_snapshot_includes_previous_day(tools, monkeypatch, tmp_path):
    _, backfill = tools
    root, work = tmp_path / "project", tmp_path / "work"
    work.mkdir()
    journal = {"slug": "Nature_Communications", "name": "Nature Communications"}
    backfill.write_json(work / "sources/Nature_Communications.json", {"papers": []})
    windows = []

    def archive(session, feed, start, end):
        windows.append((start, end))
        return {"papers": [], "verified_window": [start, end]}

    monkeypatch.setattr(backfill, "nature_archive", archive)
    backfill.prepare(root, work, [journal], ["2026-11-08"])
    assert windows == [("2026-11-07", "2026-11-08")]


def test_publisher_cross_year_window_fails_before_fetching(tools):
    _, backfill = tools
    with pytest.raises(ValueError, match="single calendar year"):
        backfill.nature_archive(None, {}, "2025-12-31", "2026-01-02")


def test_dated_repair_backup_aggregates_and_durable_resume(tools, monkeypatch, tmp_path):
    _, backfill = tools
    root, work = tmp_path / "project", tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("LABAGENT_PROJECT_ROOT", str(root))
    from lab_agent.agents.journal_daily_agent import JournalDailyAgent
    from lab_agent.tools.daily_report_generator import DailyReportGenerator
    import lab_agent.tools.paper_scorer as scorer_module

    class FakeScorer:
        calls = 0

        def __init__(self):
            self.model_config = {"name": "DeepSeek-V4.1-Flash"}

        def _score_single_paper(self, paper):
            FakeScorer.calls += 1
            return dict(VALID)

    monkeypatch.setattr(scorer_module, "PaperScorer", FakeScorer)
    journal = {"slug": "Nature", "name": "Nature", "feed_url": "https://example.invalid/feed",
               "reports_dir": str(root / "reports/Nature_reports")}
    agent = JournalDailyAgent({"summary_reports_dir": str(work / "summary_staging")})
    agent.journals = [journal]
    agent.report_generators = {"Nature": agent._create_report_generator(journal)}
    agent.summary_generator = DailyReportGenerator(
        reports_dir=str(work / "summary_staging"), include_priorities=("2", "3"),
        display_priorities=("3", "2"), show_subjects=False,
    )
    failed = dict(failed_paper(), published_date_iso="2026-11-07")
    valid = dict(failed_paper("valid"), reason="Original valid Priority 1", published_date_iso="2026-11-06")
    backfill.write_json(root / "reports/Nature_reports/2026-11-07.json", {"all_papers": [failed, valid], "summary": {}})
    (root / "reports/Journal_Summary_reports").mkdir()
    (root / "reports/Highlights_reports").mkdir()
    days = ["2026-11-07", "2026-11-08", "2026-11-09"]
    backfill.write_json(work / "prepared.json", {"dates": days})
    backfill.write_json(work / "sources/Nature.json", {"captured_at": "test", "papers": [
        {"id": "eight", "title": "day8", "published_date_iso": "2026-11-08"},
        {"id": "nine", "title": "day9", "published_date_iso": "2026-11-09"},
    ]})
    backfill.write_json(work / "sources/arxiv_2026-11-09.json", {"papers": [{"id": "arxiv", "title": "day9 arxiv", "abstract": "test"}]})
    assert backfill.run(root, work, agent, days) == 0
    assert FakeScorer.calls == 4
    original = backfill.read_json(work / "backup/Nature_reports/2026-11-07.json")
    assert original["all_papers"] == [failed, valid]
    saved = backfill.read_json(root / "reports/Nature_reports/2026-11-07.json")["all_papers"]
    assert next(p for p in saved if p["id"] == "valid") == valid
    for day in days:
        source = backfill.read_json(root / "reports/Nature_reports" / (day + ".json"))
        summary = backfill.read_json(root / "reports/Journal_Summary_reports" / (day + ".json"))
        assert len(summary["all_papers"]) == len(source["all_papers"]) == 2
        assert not any(p["reason"].startswith("Error in scoring") for p in summary["all_papers"])
        highlights = backfill.read_json(root / "reports/Highlights_reports" / (day + ".json"))
        expected = sum(p["score"] == 3 for p in summary["all_papers"]) + (1 if day == "2026-11-09" else 0)
        assert len(highlights["all_papers"]) == expected
        assert (root / "reports/Highlights_reports" / (day + ".html")).exists()
    assert backfill.run(root, work, agent, days) == 0
    assert FakeScorer.calls == 4
    assert backfill.read_json(work / "status.json")["state"] == "complete"
    checkpoint = backfill.CheckpointScorer(FakeScorer(), work / "scoring_checkpoint.json")
    assert len(checkpoint.data["scores"]) == 4
    other = FakeScorer()
    other.model_config["name"] = "different-model"
    with pytest.raises(RuntimeError, match="Model changed"):
        backfill.CheckpointScorer(other, work / "scoring_checkpoint.json")
