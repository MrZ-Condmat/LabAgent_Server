from sqlalchemy import CheckConstraint, UniqueConstraint, Uuid

from lab_agent.db.base import Base
from lab_agent.db.models import EmailLoginChallenge, UserSession


def test_challenge_schema():
    table = EmailLoginChallenge.__table__
    assert Base.metadata.tables["email_login_challenges"] is table
    assert isinstance(table.c.id.type, Uuid) and table.c.id.primary_key
    assert table.c.email.type.length == 320 and not table.c.email.nullable
    assert table.c.code_hash.type.length == 128 and not table.c.code_hash.nullable
    assert table.c.failed_attempts.default.arg == 0
    assert any(isinstance(item, CheckConstraint) and item.name == "ck_email_challenges_attempts" for item in table.constraints)
    assert {item.name for item in table.indexes} == {"ix_email_challenges_email_created", "ix_email_challenges_expires"}
    for field in ("created_at", "expires_at", "consumed_at", "invalidated_at"):
        assert table.c[field].type.timezone
    assert "user_id" not in table.c


def test_session_schema():
    table = UserSession.__table__
    assert Base.metadata.tables["user_sessions"] is table
    assert isinstance(table.c.id.type, Uuid) and table.c.id.primary_key
    assert table.c.user_id.nullable is False
    foreign_key = next(iter(table.c.user_id.foreign_keys))
    assert foreign_key.target_fullname == "users.id" and foreign_key.ondelete == "CASCADE"
    assert any(isinstance(item, UniqueConstraint) and item.name == "uq_user_sessions_token_hash" for item in table.constraints)
    for field in ("created_at", "expires_at", "last_seen_at", "revoked_at"):
        assert table.c[field].type.timezone
