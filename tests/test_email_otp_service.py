import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lab_agent.auth.email_otp import EmailOtpService, OtpInvalidError, OtpRateLimitError
from lab_agent.db.models.user import utc_now


def settings():
    return SimpleNamespace(allowed_email_domains="mails.tsinghua.edu.cn,mail.tsinghua.edu.cn",
        auth_otp_hmac_secret="fake-secret", auth_otp_ttl_seconds=300, auth_otp_max_attempts=5,
        auth_otp_resend_cooldown_seconds=60, auth_otp_max_requests_per_window=5,
        auth_otp_request_window_seconds=600)


def test_issue_rate_limits_and_invalidates_previous():
    repo = Mock()
    repo.list_recent_for_email.return_value = []
    service = EmailOtpService(repo, settings())
    issued = service.issue_challenge(" User@MAIL.TSINGHUA.EDU.CN ")
    record = repo.create.call_args.args[0]
    assert issued.email == "user@mail.tsinghua.edu.cn"
    assert len(issued.code) == 6 and issued.code.isdecimal()
    assert record.code_hash != issued.code
    assert "code=<redacted>" in repr(issued) and issued.code not in repr(issued)
    repo.lock_email.assert_called_once_with(issued.email)
    repo.invalidate_active_for_email.assert_called_once()
    repo.list_recent_for_email.return_value = [SimpleNamespace(created_at=utc_now())]
    with pytest.raises(OtpRateLimitError):
        service.issue_challenge(issued.email)
    repo.list_recent_for_email.return_value = [SimpleNamespace(created_at=utc_now()-timedelta(seconds=61))]*5
    with pytest.raises(OtpRateLimitError):
        service.issue_challenge(issued.email)


def test_verify_attempt_limit_single_use_and_expiry():
    repo = Mock()
    service = EmailOtpService(repo, settings())
    challenge_id = uuid.uuid4()
    code = "004921"
    challenge = SimpleNamespace(id=challenge_id, email="user@mail.tsinghua.edu.cn",
        code_hash=service.crypto.digest(challenge_id, code), expires_at=utc_now()+timedelta(minutes=5),
        consumed_at=None, invalidated_at=None, failed_attempts=0)
    repo.get_for_update.return_value = challenge
    repo.record_failed_attempt.side_effect = lambda item: setattr(item, "failed_attempts", item.failed_attempts+1)
    for _ in range(5):
        result = service.verify_challenge(challenge_id, challenge.email, "000000")
        assert not result.succeeded
    assert challenge.failed_attempts == 5
    with pytest.raises(OtpInvalidError):
        service.verify_challenge(challenge_id, challenge.email, code)
    challenge.failed_attempts = 0
    repo.consume.side_effect = lambda item, now: setattr(item, "consumed_at", now)
    assert service.verify_challenge(challenge_id, challenge.email, code).verified_email == challenge.email
    with pytest.raises(OtpInvalidError):
        service.verify_challenge(challenge_id, challenge.email, code)
    challenge.consumed_at = None
    challenge.expires_at = utc_now()-timedelta(seconds=1)
    with pytest.raises(OtpInvalidError):
        service.verify_challenge(challenge_id, challenge.email, code)
