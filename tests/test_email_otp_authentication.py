"""OTP login orchestration tests without a database or SMTP."""

from datetime import timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest

from lab_agent.auth.email_otp import OtpVerificationResult
from lab_agent.auth.email_otp_authentication import EmailOtpAuthenticationService
from lab_agent.auth.errors import UserInactiveError
from lab_agent.auth.sessions import IssuedSession
from lab_agent.db.models import User, UserRole
from lab_agent.db.models.user import utc_now


EMAIL = "user@mail.tsinghua.edu.cn"


def make_service(*, verified=True, user=None):
    otp, users, sessions = Mock(), Mock(), Mock()
    otp.verify_challenge.return_value = OtpVerificationResult(EMAIL if verified else None)
    users.get_by_email.return_value = user
    users.add.side_effect = lambda created: (setattr(created, "id", uuid4()) or created)
    users.update_last_login.side_effect = lambda target, *, last_login_at: (
        setattr(target, "last_login_at", last_login_at) or target
    )
    sessions.create_session.return_value = IssuedSession(uuid4(), "fake-opaque-token", utc_now())
    return EmailOtpAuthenticationService(otp, users, sessions), otp, users, sessions


def test_existing_admin_reused_without_changing_role_or_entra_identity():
    user = User(id=uuid4(), email=EMAIL, display_name="Existing Admin", role=UserRole.ADMIN,
                is_active=True, external_subject="stable-sub", tenant_id=uuid4(), external_object_id=uuid4())
    service, otp, users, sessions = make_service(user=user)
    challenge_id = uuid4()
    attempt = service.authenticate(challenge_id=challenge_id, email=" USER@MAIL.TSINGHUA.EDU.CN ", code="123456")
    assert attempt.succeeded and attempt.authentication.current_user.id == user.id
    assert attempt.authentication.current_user.role == UserRole.ADMIN
    assert user.role == UserRole.ADMIN and user.external_subject == "stable-sub"
    assert user.tenant_id is not None and user.external_object_id is not None
    assert user.last_login_at.tzinfo == timezone.utc
    assert "fake-opaque-token" not in repr(attempt)
    otp.verify_challenge.assert_called_once_with(challenge_id, " USER@MAIL.TSINGHUA.EDU.CN ", "123456")
    users.get_by_email.assert_called_once_with(EMAIL)
    users.add.assert_not_called()
    sessions.create_session.assert_called_once_with(user.id)


def test_verified_new_email_creates_basic_active_user_only_after_verification():
    service, otp, users, sessions = make_service()
    attempt = service.authenticate(challenge_id=uuid4(), email=EMAIL, code="123456")
    created = users.add.call_args.args[0]
    assert attempt.succeeded and attempt.authentication.current_user.id == created.id
    assert created.email == EMAIL and created.display_name == "user"
    assert created.role == UserRole.USER and created.is_active is True
    assert created.external_subject is None and created.tenant_id is None and created.external_object_id is None
    assert created.last_login_at.tzinfo == timezone.utc
    sessions.create_session.assert_called_once_with(created.id)
    assert otp.verify_challenge.call_count == 1


def test_wrong_code_returns_normally_and_never_accesses_users_or_sessions():
    service, _, users, sessions = make_service(verified=False)
    attempt = service.authenticate(challenge_id=uuid4(), email=EMAIL, code="000000")
    assert not attempt.succeeded and attempt.authentication is None
    users.get_by_email.assert_not_called()
    users.add.assert_not_called()
    sessions.create_session.assert_not_called()


def test_inactive_existing_user_rejected_without_reactivation_or_session():
    user = User(id=uuid4(), email=EMAIL, display_name="Inactive", role=UserRole.USER, is_active=False)
    service, _, users, sessions = make_service(user=user)
    with pytest.raises(UserInactiveError):
        service.authenticate(challenge_id=uuid4(), email=EMAIL, code="123456")
    assert user.is_active is False
    users.update_last_login.assert_not_called()
    sessions.create_session.assert_not_called()
