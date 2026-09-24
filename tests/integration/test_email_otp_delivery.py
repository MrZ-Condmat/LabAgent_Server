"""PostgreSQL transaction behavior with fake email transports only."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from lab_agent.auth.email_otp import EmailOtpService
from lab_agent.auth.email_otp_delivery import EmailOtpDeliveryService
from lab_agent.auth.email_sender import EmailDeliveryError
from lab_agent.db.models import EmailLoginChallenge
from lab_agent.db.models.user import utc_now
from lab_agent.db.session import database_session
from lab_agent.repositories.email_login_challenges import EmailLoginChallengeRepository


pytestmark = pytest.mark.integration


class Settings:
    allowed_email_domains = "mails.tsinghua.edu.cn,mail.tsinghua.edu.cn"
    auth_otp_hmac_secret = "integration-fake-secret"
    auth_otp_ttl_seconds = 300
    auth_otp_max_attempts = 5
    auth_otp_resend_cooldown_seconds = 60
    auth_otp_max_requests_per_window = 5
    auth_otp_request_window_seconds = 600


class RecordingFakeEmailSender:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def send_verification_code(self, *, recipient, code, expires_at):
        self.calls.append((recipient, code, expires_at))
        if self.fail:
            raise EmailDeliveryError("Unable to send verification email")


def delivery_service(session, sender):
    return EmailOtpDeliveryService(
        EmailOtpService(EmailLoginChallengeRepository(session), Settings()), sender
    )


def test_successful_fake_delivery_commits_one_hashed_challenge(integration_session_factory):
    sender = RecordingFakeEmailSender()
    with database_session(integration_session_factory) as session:
        result = delivery_service(session, sender).request_verification_email(
            " USER@MAIL.TSINGHUA.EDU.CN "
        )
    assert len(sender.calls) == 1
    recipient, code, expires_at = sender.calls[0]
    assert recipient == "user@mail.tsinghua.edu.cn"
    assert len(code) == 6 and code.isascii() and code.isdecimal()
    assert expires_at == result.expires_at
    with database_session(integration_session_factory) as session:
        stored = session.get(EmailLoginChallenge, result.challenge_id)
        assert stored.email == recipient and stored.expires_at == expires_at
        assert stored.code_hash != code
        assert EmailOtpService(EmailLoginChallengeRepository(session), Settings()).crypto.matches(
            stored.id, code, stored.code_hash
        )
        assert session.scalar(select(func.count()).select_from(EmailLoginChallenge)) == 1


def test_failed_fake_delivery_rolls_back_invalidation_and_new_challenge(integration_session_factory):
    with database_session(integration_session_factory) as session:
        original = delivery_service(session, RecordingFakeEmailSender()).request_verification_email(
            "user@mail.tsinghua.edu.cn"
        )
    with database_session(integration_session_factory) as session:
        session.get(EmailLoginChallenge, original.challenge_id).created_at = utc_now() - timedelta(seconds=61)

    sender = RecordingFakeEmailSender(fail=True)
    with pytest.raises(EmailDeliveryError):
        with database_session(integration_session_factory) as session:
            delivery_service(session, sender).request_verification_email("user@mail.tsinghua.edu.cn")
    assert len(sender.calls) == 1

    with database_session(integration_session_factory) as session:
        previous = session.get(EmailLoginChallenge, original.challenge_id)
        assert previous.invalidated_at is None
        assert session.scalar(select(func.count()).select_from(EmailLoginChallenge)) == 1
