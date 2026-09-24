from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from lab_agent.db.models import EmailLoginChallenge, UserSession
from lab_agent.db.models.user import utc_now
from lab_agent.repositories.email_login_challenges import EmailLoginChallengeRepository
from lab_agent.repositories.user_sessions import UserSessionRepository


def sql(statement):
    return str(statement.compile(dialect=postgresql.dialect()))


def test_challenge_queries_and_mutations_leave_commit_to_caller():
    session = Mock(spec=Session)
    session.scalars.return_value.all.return_value = []
    repo = EmailLoginChallengeRepository(session)
    challenge_id = uuid4()
    repo.lock_email("user@example.com")
    assert "pg_advisory_xact_lock" in sql(session.execute.call_args.args[0])
    repo.get_for_update(challenge_id)
    assert "FOR UPDATE" in sql(session.scalar.call_args.args[0])
    repo.list_recent_for_email("user@example.com", utc_now()-timedelta(minutes=10))
    assert "email_login_challenges.email" in sql(session.scalars.call_args.args[0])
    repo.invalidate_active_for_email("user@example.com", utc_now())
    assert "invalidated_at IS NULL" in sql(session.execute.call_args.args[0])
    challenge = EmailLoginChallenge(email="user@example.com", code_hash="digest", failed_attempts=0,
                                    expires_at=utc_now()+timedelta(minutes=5))
    repo.create(challenge)
    repo.record_failed_attempt(challenge)
    repo.consume(challenge, utc_now())
    assert challenge.failed_attempts == 1 and challenge.consumed_at is not None
    session.commit.assert_not_called()


def test_session_queries_and_mutations_leave_commit_to_caller():
    session = Mock(spec=Session)
    repo = UserSessionRepository(session)
    now = utc_now()
    record = UserSession(user_id=uuid4(), token_hash="digest", expires_at=now+timedelta(days=30), last_seen_at=now)
    repo.create(record)
    repo.get_active_by_token_hash("digest", now)
    query = sql(session.scalar.call_args.args[0])
    assert "token_hash" in query and "revoked_at IS NULL" in query and "expires_at >" in query
    repo.touch(record, now)
    repo.revoke(record, now)
    repo.revoke_all_for_user(record.user_id, now)
    assert "user_sessions.user_id" in sql(session.execute.call_args.args[0])
    session.commit.assert_not_called()
