"""Real PostgreSQL validation of the three private persistent chat workspaces."""

from uuid import uuid4

import pytest

from lab_agent.auth.models import CurrentUser
from lab_agent.db.models import ConversationType, MessageRole, User, UserRole
from lab_agent.db.session import database_session
from lab_agent.repositories.errors import OwnedResourceNotFoundError
from lab_agent.repositories.users import UserRepository
from lab_agent.services.chat_conversations import ChatConversationService
from lab_agent.web.chat_context import arxiv_context, journal_context


pytestmark = pytest.mark.integration


def add_identity(factory, name, role=UserRole.USER):
    with database_session(factory) as session:
        user = UserRepository(session).add(User(
            email=f"{name}@mails.tsinghua.edu.cn", display_name=name, role=role,
        ))
        return CurrentUser.from_user(user)


def test_three_kinds_persist_across_service_instances_and_keep_context(integration_session_factory):
    user = add_identity(integration_session_factory, "chat-owner")
    service = ChatConversationService(integration_session_factory)
    overview = service.append_user(user, ConversationType.OVERVIEW, {}, "  My first question  ")
    arxiv = service.append_user(user, ConversationType.ARXIV, arxiv_context("2026-09-20"), "ArXiv question")
    journal_meta = journal_context("2026-09-20", {"slug": "prl", "name": "PRL"})
    journal = service.append_user(user, ConversationType.JOURNAL, journal_meta, "Journal question")
    service.append_assistant(user, ConversationType.OVERVIEW, overview, "First answer")
    service.append_user(user, ConversationType.OVERVIEW, {}, "Second question", overview)
    service.append_assistant(user, ConversationType.OVERVIEW, overview, "Second answer")

    # A fresh service/session models a page refresh, re-login or process restart.
    fresh = ChatConversationService(integration_session_factory)
    assert [c.id for c in fresh.list_recent(user, ConversationType.OVERVIEW)] == [overview]
    assert [c.id for c in fresh.list_recent(user, ConversationType.ARXIV)] == [arxiv]
    assert [c.id for c in fresh.list_recent(user, ConversationType.JOURNAL)] == [journal]
    conversation, messages = fresh.load(user, ConversationType.OVERVIEW, overview)
    assert conversation.title == "My first question"
    assert [(m.sequence_number, m.role, m.content) for m in messages] == [
        (1, MessageRole.USER, "  My first question  "),
        (2, MessageRole.ASSISTANT, "First answer"),
        (3, MessageRole.USER, "Second question"),
        (4, MessageRole.ASSISTANT, "Second answer"),
    ]
    assert fresh.load(user, ConversationType.ARXIV, arxiv)[0].context_metadata == arxiv_context("2026-09-20")
    assert fresh.load(user, ConversationType.JOURNAL, journal)[0].context_metadata == journal_meta
    new_overview = fresh.append_user(user, ConversationType.OVERVIEW, {}, "New conversation")
    assert new_overview != overview
    assert {item.id for item in fresh.list_recent(user, ConversationType.OVERVIEW)} == {overview, new_overview}
    with pytest.raises(ValueError, match="context has changed"):
        fresh.append_user(user, ConversationType.ARXIV, arxiv_context("2026-09-24"), "Wrong date", arxiv)
    with pytest.raises(ValueError, match="context has changed"):
        fresh.append_user(user, ConversationType.JOURNAL, journal_context("2026-09-24", {"slug": "nature", "name": "Nature"}), "Wrong journal", journal)
    assert len(fresh.load(user, ConversationType.ARXIV, arxiv)[1]) == 1
    assert fresh.load(user, ConversationType.JOURNAL, journal)[0].context_metadata == journal_meta


def test_user_and_admin_cannot_cross_private_chat_boundaries(integration_session_factory):
    user = add_identity(integration_session_factory, "regular-chat")
    admin = add_identity(integration_session_factory, "admin-chat", UserRole.ADMIN)
    service = ChatConversationService(integration_session_factory)
    user_id = service.append_user(user, ConversationType.OVERVIEW, {}, "User secret")
    admin_id = service.append_user(admin, ConversationType.OVERVIEW, {}, "Admin secret")
    assert [item.id for item in service.list_recent(user, ConversationType.OVERVIEW)] == [user_id]
    assert [item.id for item in service.list_recent(admin, ConversationType.OVERVIEW)] == [admin_id]
    for actor, foreign_id in ((user, admin_id), (admin, user_id)):
        with pytest.raises(OwnedResourceNotFoundError, match="Conversation not found"):
            service.load(actor, ConversationType.OVERVIEW, foreign_id)
        with pytest.raises(OwnedResourceNotFoundError, match="Conversation not found"):
            service.append_user(actor, ConversationType.OVERVIEW, {}, "Intrusion", foreign_id)
        with pytest.raises(OwnedResourceNotFoundError, match="Conversation not found"):
            service.append_assistant(actor, ConversationType.OVERVIEW, foreign_id, "Intrusion")
    with pytest.raises(OwnedResourceNotFoundError):
        service.load(user, ConversationType.OVERVIEW, uuid4())
    with pytest.raises(OwnedResourceNotFoundError):
        service.load(user, ConversationType.ARXIV, user_id)


def test_model_failure_keeps_user_turn_without_fake_assistant(integration_session_factory):
    user = add_identity(integration_session_factory, "failure-chat")
    service = ChatConversationService(integration_session_factory)
    conversation_id = service.append_user(user, ConversationType.OVERVIEW, {}, "Please answer")
    # A second session can read the committed user turn while the fake model runs.
    def failing_model(messages):
        assert [(message.sequence_number, message.role) for message in messages] == [(1, MessageRole.USER)]
        assert len(ChatConversationService(integration_session_factory).load(
            user, ConversationType.OVERVIEW, conversation_id
        )[1]) == 1
        raise RuntimeError("fake model failure")

    with pytest.raises(RuntimeError, match="fake model failure"):
        service.generate_reply(user, ConversationType.OVERVIEW, conversation_id, failing_model)
    _, messages = ChatConversationService(integration_session_factory).load(
        user, ConversationType.OVERVIEW, conversation_id
    )
    assert [(message.sequence_number, message.role) for message in messages] == [(1, MessageRole.USER)]
    service.append_user(user, ConversationType.OVERVIEW, {}, "Continue", conversation_id)
    assert [m.sequence_number for m in service.load(user, ConversationType.OVERVIEW, conversation_id)[1]] == [1, 2]
    service.generate_reply(user, ConversationType.OVERVIEW, conversation_id, lambda _: "Recovered answer")
    assert [m.sequence_number for m in service.load(user, ConversationType.OVERVIEW, conversation_id)[1]] == [1, 2, 3]
