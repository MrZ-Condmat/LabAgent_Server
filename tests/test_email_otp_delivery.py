from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest

from lab_agent.auth.email_otp import IssuedOtpChallenge
from lab_agent.auth.email_otp_delivery import EmailOtpDeliveryService
from lab_agent.auth.email_sender import EmailDeliveryError
from lab_agent.db.models.user import utc_now


def test_delivery_uses_normalized_issued_challenge_once_and_returns_no_code():
    otp_service, sender = Mock(), Mock()
    issued = IssuedOtpChallenge(uuid4(), "user@mail.tsinghua.edu.cn", "004921", utc_now() + timedelta(minutes=5))
    otp_service.issue_challenge.return_value = issued
    result = EmailOtpDeliveryService(otp_service, sender).request_verification_email(" USER@MAIL.TSINGHUA.EDU.CN ")
    otp_service.issue_challenge.assert_called_once_with(" USER@MAIL.TSINGHUA.EDU.CN ")
    sender.send_verification_code.assert_called_once_with(recipient=issued.email, code=issued.code,
                                                           expires_at=issued.expires_at)
    assert result.challenge_id == issued.challenge_id and result.expires_at == issued.expires_at
    assert not hasattr(result, "code") and issued.code not in repr(result)


def test_delivery_failure_propagates_to_caller_transaction():
    otp_service, sender = Mock(), Mock()
    otp_service.issue_challenge.return_value = IssuedOtpChallenge(
        uuid4(), "user@mail.tsinghua.edu.cn", "123456", utc_now() + timedelta(minutes=5))
    sender.send_verification_code.side_effect = EmailDeliveryError("Unable to send verification email")
    with pytest.raises(EmailDeliveryError):
        EmailOtpDeliveryService(otp_service, sender).request_verification_email("user@mail.tsinghua.edu.cn")
    sender.send_verification_code.assert_called_once()
