"""Real PostgreSQL OTP, session, and concurrent consumption checks."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from lab_agent.auth.email_otp import EmailOtpService, OtpInvalidError
from lab_agent.auth.sessions import SessionService
from lab_agent.db.models import EmailLoginChallenge, User, UserSession
from lab_agent.db.models.user import utc_now
from lab_agent.repositories.email_login_challenges import EmailLoginChallengeRepository
from lab_agent.repositories.user_sessions import UserSessionRepository
from lab_agent.repositories.users import UserRepository

pytestmark = pytest.mark.integration


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


def test_constraints_and_user_session_cascade(integration_session_factory):
    session = integration_session_factory()
    now = utc_now()
    try:
        user = User(email="user@example.com", display_name="User")
        session.add(user)
        session.flush()
        session.add(UserSession(user_id=user.id, token_hash="test-hash", expires_at=now+timedelta(days=30), last_seen_at=now))
        session.commit()
        with pytest.raises(IntegrityError):
            session.execute(text("INSERT INTO user_sessions (id,user_id,token_hash,expires_at,last_seen_at) VALUES (:id,:uid,'test-hash',:exp,:seen)"),
                {"id": uuid4(), "uid": user.id, "exp": now+timedelta(days=30), "seen": now})
        session.rollback()
        with pytest.raises(IntegrityError):
            session.execute(text("INSERT INTO email_login_challenges (id,email,code_hash,expires_at,failed_attempts) VALUES (:id,'user@example.com','hash',:exp,-1)"),
                {"id": uuid4(), "exp": now+timedelta(minutes=5)})
        session.rollback()
        session.execute(delete(User).where(User.id == user.id))
        session.commit()
        assert session.scalar(select(func.count()).select_from(UserSession)) == 0
    finally:
        session.close()


def test_resend_invalidates_old_and_wrong_attempt_persists(integration_session_factory):
    session = integration_session_factory()
    try:
        service = EmailOtpService(EmailLoginChallengeRepository(session), Settings())
        first = service.issue_challenge("user@mail.tsinghua.edu.cn")
        session.commit()
        challenge = session.get(EmailLoginChallenge, first.challenge_id)
        challenge.created_at = utc_now()-timedelta(seconds=61)
        session.commit()
        second = service.issue_challenge(first.email)
        session.commit()
        session.expire_all()
        assert session.get(EmailLoginChallenge, first.challenge_id).invalidated_at is not None
        with pytest.raises(OtpInvalidError):
            service.verify_challenge(first.challenge_id, first.email, first.code)
        result = service.verify_challenge(second.challenge_id, second.email, "999999" if second.code != "999999" else "888888")
        assert not result.succeeded
        session.commit()  # The caller must persist a failed attempt.
        session.expire_all()
        assert session.get(EmailLoginChallenge, second.challenge_id).failed_attempts == 1
    finally:
        session.close()


def test_one_time_code_under_two_concurrent_transactions(integration_session_factory):
    setup = integration_session_factory()
    try:
        issued = EmailOtpService(EmailLoginChallengeRepository(setup), Settings()).issue_challenge("user@mail.tsinghua.edu.cn")
        setup.commit()
    finally:
        setup.close()
    barrier = Barrier(2)

    def verify():
        session = integration_session_factory()
        try:
            barrier.wait(timeout=10)
            result = EmailOtpService(EmailLoginChallengeRepository(session), Settings()).verify_challenge(
                issued.challenge_id, issued.email, issued.code)
            session.commit()
            return result.succeeded
        except OtpInvalidError:
            session.rollback()
            return False
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=30) for future in [pool.submit(verify) for _ in range(2)]]
    assert sorted(results) == [False, True]


def test_session_validation_expiration_revocation_and_inactive_user(integration_session_factory):
    session = integration_session_factory()
    try:
        user = User(email="session@example.com", display_name="Session User")
        session.add(user)
        session.flush()
        service = SessionService(UserSessionRepository(session), UserRepository(session), Settings())
        issued = service.create_session(user.id)
        session.commit()
        stored = session.get(UserSession, issued.session_id)
        assert stored.token_hash != issued.raw_token
        assert service.validate_session(issued.raw_token).id == user.id
        session.commit()

        user.is_active = False
        session.commit()
        assert service.validate_session(issued.raw_token) is None
        user.is_active = True
        session.commit()

        stored.expires_at = utc_now() - timedelta(seconds=1)
        session.commit()
        assert service.validate_session(issued.raw_token) is None
        stored.expires_at = utc_now() + timedelta(days=1)
        session.commit()

        assert service.revoke_session(issued.raw_token)
        session.commit()
        assert stored.revoked_at is not None
        assert service.validate_session(issued.raw_token) is None

        another = service.create_session(user.id)
        session.commit()
        assert service.validate_session(another.raw_token).id == user.id
        service.revoke_all_for_user(user.id)
        session.commit()
        assert service.validate_session(another.raw_token) is None
    finally:
        session.close()
