"""Streamlit chat workspaces; PostgreSQL remains the history source of truth."""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from lab_agent.auth.models import CurrentUser
from lab_agent.db.models import ConversationType, MessageRole
from lab_agent.repositories.errors import OwnedResourceNotFoundError
from lab_agent.services.chat_conversations import ChatConversationService
from lab_agent.web.chat_context import context_label, hydrate_paper_history, selector_changed


def select_workspace(
    current_user: CurrentUser,
    conversation_type: ConversationType,
    key: str,
    *,
    selector: tuple | None = None,
):
    """Choose an owned conversation or an empty draft; selector changes start a draft."""
    store = ChatConversationService()
    selector_key = f"{key}_selector"
    if selector is not None:
        previous = st.session_state.get(selector_key)
        if selector_changed(previous, selector):
            st.session_state[key] = None
        st.session_state[selector_key] = selector

    recent = store.list_recent(current_user, conversation_type)
    if key not in st.session_state:
        # A new browser session can resume the most recently active conversation.
        st.session_state[key] = recent[0].id if recent else None

    new_col, count_col = st.columns([3, 1])
    with new_col:
        if st.button("New Conversation", key=f"{key}_new", use_container_width=True):
            st.session_state[key] = None
            st.rerun()

    with st.expander("Recent Conversations"):
        for item in recent:
            label = f"{context_label(conversation_type, item.context_metadata)} · {item.title}"
            if st.button(label, key=f"{key}_open_{item.id}", use_container_width=True):
                st.session_state[key] = item.id
                st.rerun()

    conversation = None
    messages = []
    active_id = st.session_state[key]
    if active_id is not None:
        try:
            conversation, messages = store.load(current_user, conversation_type, active_id)
        except (OwnedResourceNotFoundError, ValueError):
            st.session_state[key] = None
            st.warning("Conversation is unavailable.")

    with count_col:
        exchanges = sum(message.role == MessageRole.USER for message in messages)
        if exchanges:
            st.metric("Exchanges", exchanges)

    for message in messages:
        if message.role in (MessageRole.USER, MessageRole.ASSISTANT):
            st.chat_message(message.role.value).write(message.content)
    return store, conversation, messages


def render_paper_chat(
    *,
    current_user: CurrentUser,
    conversation_type: ConversationType,
    key: str,
    selected_context: dict[str, str],
    chat,
    load_papers: Callable[[dict[str, str]], list[dict] | None],
    suggested_questions: Callable[[object, str], list[str]],
    source_label: str,
) -> None:
    st.subheader("Chat About Papers")
    selector = tuple(sorted(selected_context.items()))
    store, conversation, messages = select_workspace(
        current_user, conversation_type, key, selector=selector
    )
    context = conversation.context_metadata if conversation is not None else selected_context
    st.caption(context_label(conversation_type, context))

    try:
        papers = load_papers(context)
    except Exception:
        papers = None
    if papers is None:
        st.warning("The original report context is unavailable. History is readable, but sending is disabled.")
    elif not papers:
        st.info("This report has no papers to discuss.")

    can_send = chat is not None and bool(papers)
    report_date = context.get("report_date", "")
    if can_send:
        chat.set_papers_context(papers, report_date)

    def submit(text: str) -> None:
        try:
            conversation_id = store.append_user(
                current_user, conversation_type, context, text,
                conversation.id if conversation is not None else None,
            )
            st.session_state[key] = conversation_id
            def generate(saved_messages):
                hydrate_paper_history(chat, papers, report_date, saved_messages[:-1])
                with st.spinner("Thinking..."):
                    response = chat.chat(text)
                if not response.get("success"):
                    raise RuntimeError("Model request failed")
                return response["response"]

            store.generate_reply(current_user, conversation_type, conversation_id, generate)
            st.rerun()
        except Exception:
            st.error("Unable to complete the chat request. Your message may have been saved; refresh to check.")

    if not messages and can_send:
        st.markdown("**Quick prompts**")
        for index, question in enumerate(suggested_questions(chat, report_date)[:4]):
            if st.button(question, key=f"{key}_suggestion_{index}", use_container_width=True):
                submit(question)

    prompt = st.chat_input(f"Ask about {source_label}...", key=f"{key}_input", disabled=not can_send)
    if prompt:
        submit(prompt)
