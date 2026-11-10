"""Daily scoring notification tests never connect to an LLM or SMTP server."""

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lab_agent.utils import scoring_notifications as n
from lab_agent.tools.paper_scorer import PaperScorer
from scripts import generate_daily_report as daily


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(n, "project_path", lambda path: tmp_path / path)
    return SimpleNamespace(scoring_failure_notify_enabled=True, smtp_from="sender@example.org")


def failures():
    with n.collect_scoring_failures() as result:
        with n.scoring_source("ArXiv"):
            n.record_scoring_failure({"id": "paper-1", "title": "Example paper"}, "TimeoutError")
        with n.scoring_source("Journal"):
            n.record_scoring_failure({"doi": "10.test/fake", "title": "Journal paper"}, "ValueError")
    return result


def test_summary_to_smtp_from_once_per_date(settings):
    sender = Mock()
    factory = Mock(return_value=sender)
    assert n.notify_scoring_failures(failures(), "2026-11-10", config=settings, sender_factory=factory)
    kwargs = sender.send_notification.call_args.kwargs
    assert kwargs["recipient"] == settings.smtp_from
    assert "ArXiv: 1" in kwargs["body"] and "Journal: 1" in kwargs["body"]
    assert "paper-1" in kwargs["body"]
    assert not n.notify_scoring_failures(failures(), "2026-11-10", config=settings, sender_factory=factory)
    sender.send_notification.assert_called_once()
    assert n.notify_scoring_failures(failures(), "2026-11-11", config=settings, sender_factory=factory)


def test_no_failures_or_disabled_does_not_construct_sender(settings):
    factory = Mock()
    assert not n.notify_scoring_failures(n.ScoringFailures(), "2026-11-10", config=settings, sender_factory=factory)
    settings.scoring_failure_notify_enabled = False
    assert not n.notify_scoring_failures(failures(), "2026-11-10", config=settings, sender_factory=factory)
    factory.assert_not_called()


def test_notification_failure_is_safe_and_retryable(settings, caplog):
    sender = Mock()
    sender.send_notification.side_effect = RuntimeError("secret-password-do-not-print")
    assert not n.notify_scoring_failures(failures(), "2026-11-10", config=settings, sender_factory=lambda c: sender)
    assert "secret-password" not in caplog.text
    sender.send_notification.side_effect = None
    assert n.notify_scoring_failures(failures(), "2026-11-10", config=settings, sender_factory=lambda c: sender)


def test_concurrent_run_does_not_remove_other_reservation(settings, tmp_path):
    path = tmp_path / "logs/scoring_notifications/2026-11-10.sending"
    path.parent.mkdir(parents=True)
    path.write_text("another run", encoding="utf-8")
    factory = Mock()
    assert not n.notify_scoring_failures(failures(), "2026-11-10", config=settings, sender_factory=factory)
    assert path.read_text() == "another run"
    factory.assert_not_called()


def test_failed_marker_promotion_prevents_repeat_mail(settings, monkeypatch):
    sender = Mock()
    monkeypatch.setattr(n.os, "replace", Mock(side_effect=OSError("disk failure")))
    assert not n.notify_scoring_failures(failures(), "2026-11-10", config=settings, sender_factory=lambda c: sender)
    assert not n.notify_scoring_failures(failures(), "2026-11-10", config=settings, sender_factory=lambda c: sender)
    sender.send_notification.assert_called_once()


def test_only_exhausted_retries_count_as_failed(monkeypatch):
    scorer = object.__new__(PaperScorer)
    scorer.logger = logging.getLogger("scorer-test")
    scorer._score_single_paper = Mock(side_effect=[TimeoutError("fake"), {"score": 1, "reason": "Low relevance"}])
    monkeypatch.setattr("lab_agent.tools.paper_scorer.time.sleep", lambda _: None)
    paper = {"id": "example", "title": "Example"}
    with n.collect_scoring_failures() as first:
        assert scorer.score_papers([paper])[0]["score"] == 1
    assert first.total == 0
    scorer._score_single_paper = Mock(side_effect=TimeoutError("fake"))
    with n.collect_scoring_failures() as second:
        assert scorer.score_papers([paper])[0]["score"] == 1
    assert second.total == 1
    assert scorer._score_single_paper.call_count == 2


def test_unparseable_response_counts_as_failure():
    scorer = object.__new__(PaperScorer)
    scorer.logger = logging.getLogger("scorer-test")
    scorer._score_single_paper_with_retry = Mock(return_value={"score": 1, "reason": "Unable to parse response"})
    with n.collect_scoring_failures() as result:
        scorer.score_papers([{"title": "Example"}])
    assert result.total == 1


def test_run_once_summarizes_even_if_later_report_fails(monkeypatch):
    async def arxiv(args, report_date):
        n.record_scoring_failure({"title": "Example"}, "TimeoutError")
        return True
    async def journal(args, report_date):
        raise ValueError("fake report error")
    monkeypatch.setattr(daily, "load_project_dotenv", lambda: None)
    monkeypatch.setattr(daily, "generate_arxiv_report", arxiv)
    monkeypatch.setattr(daily, "generate_journal_reports", journal)
    notify = Mock()
    monkeypatch.setattr(daily, "notify_scoring_failures", notify)
    args = SimpleNamespace(date="2026-11-10", skip_arxiv=False, skip_weekends=False,
                           skip_journals=False, skip_highlights=True)
    with pytest.raises(ValueError):
        asyncio.run(daily.run_once(args))
    assert notify.call_args.args[0].total == 1
    assert notify.call_args.args[0].sources["ArXiv"] == 1
