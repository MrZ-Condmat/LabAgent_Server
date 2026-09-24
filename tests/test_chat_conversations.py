"""Offline checks for private chat helpers and context reconstruction."""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from lab_agent.db.models import ConversationType, MessageRole
from lab_agent.services.chat_conversations import conversation_title, model_history
from lab_agent.web.chat_context import arxiv_context, context_label, hydrate_paper_history, journal_context, selector_changed
from lab_agent.web.chat_reports import load_arxiv_papers, load_journal_papers
from lab_agent.web.chat_ui import cancel_pending_delete, delete_confirmed_conversation


def test_title_and_runtime_history_are_deterministic_and_exclude_system():
    assert conversation_title("  First\n  question  ") == "First question"
    assert conversation_title("x" * 110) == "x" * 97 + "..."
    messages = [
        SimpleNamespace(role=MessageRole.SYSTEM, content="private prompt"),
        SimpleNamespace(role=MessageRole.USER, content="question"),
        SimpleNamespace(role=MessageRole.ASSISTANT, content="answer"),
    ]
    assert model_history(messages) == [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
    ]


def test_report_contexts_are_structured_and_labelled():
    assert arxiv_context("2026-09-20") == {"report_date": "2026-09-20"}
    journal = {"slug": "prl", "name": "Physical Review Letters"}
    context = journal_context("2026-09-20", journal)
    assert context == {
        "report_date": "2026-09-20", "report_type": "journal",
        "journal_slug": "prl", "journal_name": "Physical Review Letters",
    }
    assert context_label(ConversationType.JOURNAL, context) == "Physical Review Letters · 2026-09-20"
    assert journal_context("2026-09-24", None)["report_type"] == "summary"
    with pytest.raises(ValueError):
        arxiv_context("today")
    old_arxiv = tuple(sorted(arxiv_context("2026-09-20").items()))
    new_arxiv = tuple(sorted(arxiv_context("2026-09-24").items()))
    assert selector_changed(old_arxiv, new_arxiv)
    assert not selector_changed(old_arxiv, old_arxiv)
    assert not selector_changed(None, old_arxiv)
    old_journal = tuple(sorted(context.items()))
    new_journal = tuple(sorted(journal_context("2026-09-24", {"slug": "nature", "name": "Nature"}).items()))
    assert selector_changed(old_journal, new_journal)


def test_paper_history_is_rebuilt_once_without_duplicate_turns():
    class FakeChat:
        def set_papers_context(self, papers, label):
            self.papers = papers
            self.label = label
            self.conversation_history = [{"role": "system", "content": "runtime papers"}]

    chat = FakeChat()
    messages = [
        SimpleNamespace(role=MessageRole.USER, content="first"),
        SimpleNamespace(role=MessageRole.ASSISTANT, content="reply"),
    ]
    for _ in range(2):
        hydrate_paper_history(chat, [{"title": "paper"}], "2026-09-20", messages)
        assert [item["role"] for item in chat.conversation_history] == ["system", "user", "assistant"]
        assert chat.label == "2026-09-20"


def test_report_loaders_use_persisted_context_not_current_page_selection():
    class ArxivAgent:
        def _get_report(self, date):
            assert date == "2026-09-20"
            return {"success": True, "report": {"json_data": {"all_papers": [{"title": "old arxiv"}]}}}

    class JournalAgent:
        async def process_task(self, task):
            assert task == {"type": "get_report", "journal": "prl", "date": "2026-09-20"}
            return {"success": True, "report": {"json_data": {"all_papers": [{"title": "old journal"}]}}}

    assert load_arxiv_papers(ArxivAgent(), arxiv_context("2026-09-20")) == [{"title": "old arxiv"}]
    saved_context = journal_context("2026-09-20", {"slug": "prl", "name": "PRL"})
    # The page may now select a different date/journal; the loader sees only saved context.
    assert load_journal_papers(JournalAgent(), saved_context) == [{"title": "old journal"}]
    assert load_arxiv_papers(None, saved_context) is None


@pytest.mark.parametrize("key", (
    "overview_active_conversation_id",
    "arxiv_active_conversation_id",
    "journal_active_conversation_id",
))
def test_delete_confirmation_helpers_preserve_or_clear_active_selection(key):
    store = Mock()
    actor = SimpleNamespace(id=uuid4())
    active = uuid4()
    inactive = uuid4()
    state = {key: active, f"{key}_pending_delete": inactive}

    cancel_pending_delete(state, key)
    assert state[key] == active
    assert f"{key}_pending_delete" not in state
    store.delete_conversation.assert_not_called()

    state[f"{key}_pending_delete"] = inactive
    delete_confirmed_conversation(store, actor, state, key, inactive)
    store.delete_conversation.assert_called_once_with(actor, inactive)
    assert state[key] == active
    assert f"{key}_pending_delete" not in state

    store.reset_mock()
    state[f"{key}_pending_delete"] = active
    delete_confirmed_conversation(store, actor, state, key, active)
    store.delete_conversation.assert_called_once_with(actor, active)
    assert state[key] is None
    assert f"{key}_pending_delete" not in state
