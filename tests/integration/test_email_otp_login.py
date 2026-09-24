"""Real PostgreSQL OTP login, transaction, and concurrency checks."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from lab_agent.auth.email_otp import EmailOtpService, OtpInvalidError
from lab_agent.auth.email_otp_authentication import EmailOtpAuthenticationService
from lab_agent.auth.errors import UserInactiveError
from lab_agent.auth.sessions import SessionService
from lab_agent.db.models import EmailLoginChallenge, User, UserRole, UserSession
from lab_agent.db.models.user import utc_now
from lab_agent.db.session import database_session
from lab_agent.repositories.email_login_challenges import EmailLoginChallengeRepository
from lab_agent.repositories.user_sessions import UserSessionRepository
from lab_agent.repositories.users import UserRepository


pytestmark = pytest.mark.integration
EMAIL = "user@mail.tsinghua.edu.cn"


class Settings:
    allowed_email_domains = "mails.tsinghua.edu.cn,mail.tsinghua.edu.cn"
    auth_otp_hmac_secret = "integration-fake-secret"
    auth_otp_ttl_seconds = 300
    auth_otp_max_attempts = 5
    auth_otp_resend_cooldown_seconds = 60
    auth_otp_max_requests_per_window = 5
    auth_otp_request_window_seconds = 600
    auth_session_hmac_secret = "integration-fake-session-secret"
    auth_session_ttl_days = 30


def otp_service(session):
    return EmailOtpService(EmailLoginChallengeRepository(session), Settings())


def login_service(session):
    users = UserRepository(session)
    sessions = SessionService(UserSessionRepository(session), users, Settings())
    return EmailOtpAuthenticationService(otp_service(session), users, sessions), sessions


def issue(session_factory, email=EMAIL):
    with database_session(session_factory) as session:
        return otp_service(session).issue_challenge(email)


def counts(session):
    return tuple(session.scalar(select(func.count()).select_from(model)) for model in (User, UserSession))


def test_first_login_provisions_user_and_hashed_session(integration_session_factory):
    issued = issue(integration_session_factory)
    with database_session(integration_session_factory) as session:
        login, _ = login_service(session)
        attempt = login.authenticate(challenge_id=issued.challenge_id, email=" USER@MAIL.TSINGHUA.EDU.CN ", code=issued.code)
        assert attempt.succeeded
        result = attempt.authentication
    with database_session(integration_session_factory) as session:
        assert counts(session) == (1, 1)
        user = session.get(User, result.current_user.id)
        challenge = session.get(EmailLoginChallenge, issued.challenge_id)
        stored_session = session.get(UserSession, result.session_id)
        assert user.email == EMAIL and user.display_name == "user"
        assert user.role == UserRole.USER and user.is_active is True
        assert user.external_subject is None and user.tenant_id is None and user.external_object_id is None
        assert user.last_login_at is not None and user.last_login_at.utcoffset() == timedelta(0)
        assert challenge.consumed_at is not None and challenge.code_hash != issued.code
        assert stored_session.user_id == user.id and stored_session.token_hash != result.session_token
        _, sessions = login_service(session)
        assert sessions.validate_session(result.session_token).id == user.id
    with pytest.raises(OtpInvalidError):
        with database_session(integration_session_factory) as session:
            login_service(session)[0].authenticate(challenge_id=issued.challenge_id, email=EMAIL, code=issued.code)
    with database_session(integration_session_factory) as session:
        assert counts(session) == (1, 1)


def test_existing_admin_reused_with_entra_identity_preserved(integration_session_factory):
    with database_session(integration_session_factory) as session:
        user = User(email=EMAIL, display_name="Existing", role=UserRole.ADMIN, is_active=True,
                    external_subject="stable-sub", tenant_id=uuid4(), external_object_id=uuid4())
        UserRepository(session).add(user)
        original = (user.id, user.external_subject, user.tenant_id, user.external_object_id)
    issued = issue(integration_session_factory)
    with database_session(integration_session_factory) as session:
        attempt = login_service(session)[0].authenticate(challenge_id=issued.challenge_id, email=EMAIL, code=issued.code)
        assert attempt.authentication.current_user.id == original[0]
        assert attempt.authentication.current_user.role == UserRole.ADMIN
    with database_session(integration_session_factory) as session:
        assert counts(session) == (1, 1)
        user = session.get(User, original[0])
        assert (user.id, user.external_subject, user.tenant_id, user.external_object_id) == original
        assert user.role == UserRole.ADMIN and user.last_login_at is not None


def test_wrong_code_commits_attempt_without_creating_user_or_session(integration_session_factory):
    issued = issue(integration_session_factory)
    wrong = "999999" if issued.code != "999999" else "888888"
    with database_session(integration_session_factory) as session:
        attempt = login_service(session)[0].authenticate(challenge_id=issued.challenge_id, email=EMAIL, code=wrong)
        assert not attempt.succeeded
    with database_session(integration_session_factory) as session:
        challenge = session.get(EmailLoginChallenge, issued.challenge_id)
        assert challenge.failed_attempts == 1 and challenge.consumed_at is None
        assert counts(session) == (0, 0)
    for _ in range(4):
        with database_session(integration_session_factory) as session:
            assert not login_service(session)[0].authenticate(challenge_id=issued.challenge_id, email=EMAIL, code=wrong).succeeded
    with pytest.raises(OtpInvalidError):
        with database_session(integration_session_factory) as session:
            login_service(session)[0].authenticate(challenge_id=issued.challenge_id, email=EMAIL, code=issued.code)
    with database_session(integration_session_factory) as session:
        assert session.get(EmailLoginChallenge, issued.challenge_id).failed_attempts == 5
        assert counts(session) == (0, 0)


def test_inactive_user_rejected_without_session(integration_session_factory):
    with database_session(integration_session_factory) as session:
        user = User(email=EMAIL, display_name="Inactive", role=UserRole.USER, is_active=False)
        UserRepository(session).add(user)
    issued = issue(integration_session_factory)
    with pytest.raises(UserInactiveError):
        with database_session(integration_session_factory) as session:
            login_service(session)[0].authenticate(challenge_id=issued.challenge_id, email=EMAIL, code=issued.code)
    with database_session(integration_session_factory) as session:
        assert counts(session) == (1, 0)
        assert session.get(User, user.id).is_active is False
        assert session.get(EmailLoginChallenge, issued.challenge_id).consumed_at is None


def test_expired_invalidated_and_email_mismatch_reject_without_user(integration_session_factory):
    issued = issue(integration_session_factory)
    with pytest.raises(OtpInvalidError):
        with database_session(integration_session_factory) as session:
            login_service(session)[0].authenticate(
                challenge_id=issued.challenge_id, email="other@mail.tsinghua.edu.cn", code=issued.code)
    with database_session(integration_session_factory) as session:
        session.get(EmailLoginChallenge, issued.challenge_id).expires_at = utc_now() - timedelta(seconds=1)
    with pytest.raises(OtpInvalidError):
        with database_session(integration_session_factory) as session:
            login_service(session)[0].authenticate(challenge_id=issued.challenge_id, email=EMAIL, code=issued.code)
    with database_session(integration_session_factory) as session:
        challenge = session.get(EmailLoginChallenge, issued.challenge_id)
        challenge.expires_at = utc_now() + timedelta(minutes=5)
        challenge.invalidated_at = utc_now()
    with pytest.raises(OtpInvalidError):
        with database_session(integration_session_factory) as session:
            login_service(session)[0].authenticate(challenge_id=issued.challenge_id, email=EMAIL, code=issued.code)
    with database_session(integration_session_factory) as session:
        assert counts(session) == (0, 0)


def test_session_creation_error_rolls_back_consumption_and_new_user(integration_session_factory):
    issued = issue(integration_session_factory)
    with pytest.raises(RuntimeError, match="injected session failure"):
        with database_session(integration_session_factory) as session:
            login, sessions = login_service(session)
            sessions.create_session = lambda user_id: (_ for _ in ()).throw(RuntimeError("injected session failure"))
            login.authenticate(challenge_id=issued.challenge_id, email=EMAIL, code=issued.code)
    with database_session(integration_session_factory) as session:
        assert session.get(EmailLoginChallenge, issued.challenge_id).consumed_at is None
        assert counts(session) == (0, 0)


def test_two_workers_same_otp_create_exactly_one_user_and_session(integration_session_factory):
    issued = issue(integration_session_factory)
    barrier = Barrier(2)

    def worker():
        try:
            barrier.wait(timeout=10)
            with database_session(integration_session_factory) as session:
                attempt = login_service(session)[0].authenticate(
                    challenge_id=issued.challenge_id, email=EMAIL, code=issued.code)
                return attempt.succeeded
        except OtpInvalidError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=30) for future in [pool.submit(worker) for _ in range(2)]]
    assert sorted(results) == [False, True]
    with database_session(integration_session_factory) as session:
        assert counts(session) == (1, 1)
        assert session.get(EmailLoginChallenge, issued.challenge_id).consumed_at is not None
