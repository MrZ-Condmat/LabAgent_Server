"""Reload the exact shared report named by a persisted conversation context."""

import asyncio


def load_arxiv_papers(agent, context: dict) -> list[dict] | None:
    if agent is None:
        return None
    result = agent._get_report(context["report_date"])
    if not result.get("success"):
        return None
    return result["report"]["json_data"].get("all_papers", [])


def load_journal_papers(agent, context: dict) -> list[dict] | None:
    if agent is None:
        return None
    if context["report_type"] == "summary":
        task = {"type": "get_summary_report", "date": context["report_date"]}
    elif context["report_type"] == "journal":
        task = {
            "type": "get_report",
            "journal": context["journal_slug"],
            "date": context["report_date"],
        }
    else:
        return None
    result = asyncio.run(agent.process_task(task))
    if not result.get("success"):
        return None
    return result["report"]["json_data"].get("all_papers", [])
