import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from lab_agent.auth.sessions import SessionService
from lab_agent.db.models import User, UserRole
from lab_agent.db.models.user import utc_now


def settings():
    return SimpleNamespace(auth_session_hmac_secret="fake-session-secret", auth_session_ttl_days=30)


def test_session_create_validate_revoke_and_inactive_user():
    sessions, users = Mock(), Mock()
    user = User(id=uuid.uuid4(), email="user@example.com", display_name="User", role=UserRole.USER,
                is_active=True, external_subject=None, tenant_id=None, external_object_id=None)
    users.get_by_id.return_value = user
    sessions.create.side_effect = lambda record: (setattr(record, "id", uuid.uuid4()) or record)
    service = SessionService(sessions, users, settings())
    issued = service.create_session(user.id)
    record = sessions.create.call_args.args[0]
    assert issued.raw_token != record.token_hash
    assert len(issued.raw_token) >= 40
    assert issued.expires_at == record.expires_at
    assert record.last_seen_at == record.created_at
    assert "raw_token=<redacted>" in repr(issued) and issued.raw_token not in repr(issued)
    sessions.get_active_by_token_hash.return_value = record
    assert service.validate_session(issued.raw_token).id == user.id
    users.get_by_id.return_value = User(id=user.id, email=user.email, display_name=user.display_name,
        role=UserRole.USER, is_active=False, external_subject=None, tenant_id=None, external_object_id=None)
    assert service.validate_session(issued.raw_token) is None
    users.get_by_id.return_value = user
    assert service.revoke_session(issued.raw_token)
    sessions.revoke.assert_called_once()
    service.revoke_all_for_user(user.id)
    sessions.revoke_all_for_user.assert_called_once()
    sessions.get_active_by_token_hash.return_value = None
    assert service.validate_session(issued.raw_token) is None
