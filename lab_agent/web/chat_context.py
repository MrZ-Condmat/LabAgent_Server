"""Small, testable descriptions of report-bound chat contexts."""

from __future__ import annotations

from datetime import date

from lab_agent.db.models import ConversationType


def _report_date(value: str) -> str:
    return date.fromisoformat(value).isoformat()


def arxiv_context(report_date: str) -> dict[str, str]:
    return {"report_date": _report_date(report_date)}


def journal_context(report_date: str, journal: dict | None) -> dict[str, str]:
    context = {"report_date": _report_date(report_date)}
    if journal is None:
        return {**context, "report_type": "summary"}
    return {
        **context,
        "report_type": "journal",
        "journal_slug": journal["slug"],
        "journal_name": journal["name"],
    }


def context_label(conversation_type: ConversationType, context: dict) -> str:
    if conversation_type == ConversationType.OVERVIEW:
        return "Overview"
    report_date = context.get("report_date", "Unknown date")
    if conversation_type == ConversationType.ARXIV:
        return f"ArXiv · {report_date}"
    if context.get("report_type") == "summary":
        return f"Journal Summary · {report_date}"
    return f"{context.get('journal_name', 'Journal')} · {report_date}"


def selector_changed(previous: tuple | None, selected: tuple) -> bool:
    """A change in date or journal must leave the old conversation untouched."""
    return previous is not None and previous != selected


def hydrate_paper_history(chat, papers: list[dict], report_date: str, messages: list) -> None:
    """Replace runtime history once per model call, without duplicating stored turns."""
    chat.set_papers_context(papers, report_date)
    chat.conversation_history.extend(
        {"role": message.role.value, "content": message.content}
        for message in messages[-20:]
        if message.role.value in ("user", "assistant")
    )
