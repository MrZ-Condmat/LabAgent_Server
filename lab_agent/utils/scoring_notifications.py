"""Per-run scoring failure collection and best-effort daily SMTP summary."""

from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date
import logging
import os

from .config import Config
from .runtime import project_path


LOGGER = logging.getLogger(__name__)
_ACTIVE = ContextVar("daily_scoring_failures", default=None)
_SOURCE = ContextVar("daily_scoring_source", default="Unknown")


@dataclass
class ScoringFailures:
    total: int = 0
    sources: Counter = field(default_factory=Counter)
    samples: list = field(default_factory=list)


@contextmanager
def collect_scoring_failures():
    failures = ScoringFailures()
    token = _ACTIVE.set(failures)
    try:
        yield failures
    finally:
        _ACTIVE.reset(token)


@contextmanager
def scoring_source(name):
    token = _SOURCE.set(name)
    try:
        yield
    finally:
        _SOURCE.reset(token)


def record_scoring_failure(paper, category):
    failures = _ACTIVE.get()
    if failures is None:
        return
    source = _SOURCE.get()
    failures.total += 1
    failures.sources[source] += 1
    if len(failures.samples) < 20:
        # Public article metadata only. Never include raw exception/API responses.
        title = " ".join(str(paper.get("title") or "Untitled").split())[:200]
        identifier = " ".join(str(paper.get("id") or paper.get("doi") or "unknown").split())[:150]
        failures.samples.append(f"{source}: {identifier} | {title} | {category}")


def notify_scoring_failures(failures, report_date, *, config=None, sender_factory=None):
    """Send at most one summary per report date. Notification failure cannot fail reports."""
    if not failures.total:
        return False
    marker = None
    reservation = None
    sent = False
    owns_reservation = False
    try:
        settings = config if config is not None else Config()
        if not settings.scoring_failure_notify_enabled:
            return False
        report_date = date.fromisoformat(report_date).isoformat()
        directory = project_path("logs/scoring_notifications")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        marker = directory / f"{report_date}.sent"
        reservation = directory / f"{report_date}.sending"
        if marker.exists():
            return False
        try:
            fd = os.open(reservation, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return False
        owns_reservation = True
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write("Daily scoring notification reservation.\n")
        if marker.exists():
            return False
        if sender_factory is None:
            from lab_agent.auth.smtp_sender import SmtpEmailSender
            sender_factory = SmtpEmailSender
        sender = sender_factory(settings)
        body = (
            f"LabAgent daily scoring failures\nReport date: {report_date}\n"
            f"Articles with final scoring failures: {failures.total}\n\n"
            + "\n".join(f"{source}: {count}" for source, count in sorted(failures.sources.items()))
            + "\n\nFailed articles (up to 20):\n" + "\n".join(failures.samples)
            + "\n\nThese articles may have received the existing fallback priority 1.\n"
            "Check logs/daily_report.log for diagnosis and decide whether to rescore.\n"
            "No raw API error messages or credentials are included in this email.\n"
        )
        sender.send_notification(recipient=settings.smtp_from,
                                 subject=f"LabAgent scoring failures: {report_date}", body=body)
        sent = True
        os.replace(reservation, marker)
        LOGGER.info("Daily scoring failure notification sent.")
        return True
    except Exception:
        LOGGER.warning("Unable to complete scoring failure notification; inspect SMTP configuration and notification state.")
        return False
    finally:
        # After SMTP success, preserve a reservation if marker promotion failed.
        # A crash/ambiguous SMTP result requires operator review, not automatic duplicate mail.
        if owns_reservation and reservation is not None and not sent:
            try:
                reservation.unlink(missing_ok=True)
            except OSError:
                pass
