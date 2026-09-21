"""Concurrent message allocation against real PostgreSQL row locks."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import text

from lab_agent.db.models import MessageRole, User
from lab_agent.repositories import (
    ConversationRepository,
    MessageRepository,
    UserRepository,
)


pytestmark = pytest.mark.integration


def test_concurrent_appends_allocate_unique_contiguous_sequences(
    integration_session_factory,
):
    setup_session = integration_session_factory()
    user = UserRepository(setup_session).add(
        User(email="concurrency@example.com", display_name="Concurrency")
    )
    conversation = ConversationRepository(setup_session).create_for_user(user.id)
    setup_session.commit()
    user_id = user.id
    conversation_id = conversation.id
    setup_session.close()

    worker_count = 5
    messages_per_worker = 2
    barrier = Barrier(worker_count)

    def append_messages(worker_number: int) -> None:
        session = integration_session_factory()
        try:
            session.execute(text("SET LOCAL lock_timeout = '20s'"))
            barrier.wait(timeout=20)
            repository = MessageRepository(session)
            for message_number in range(messages_per_worker):
                repository.append_message_for_user_conversation(
                    user_id,
                    conversation_id,
                    role=MessageRole.USER,
                    content=f"worker-{worker_number}-message-{message_number}",
                )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(append_messages, index) for index in range(worker_count)]
        for future in futures:
            future.result(timeout=60)

    verification_session = integration_session_factory()
    try:
        messages = MessageRepository(
            verification_session
        ).list_for_user_conversation(user_id, conversation_id)
    finally:
        verification_session.close()

    expected_count = worker_count * messages_per_worker
    sequences = [message.sequence_number for message in messages]
    assert len(messages) == expected_count
    assert sequences == list(range(1, expected_count + 1))
    assert len(set(sequences)) == expected_count
