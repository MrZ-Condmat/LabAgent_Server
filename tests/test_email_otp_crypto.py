import uuid
from unittest.mock import patch

import pytest

from lab_agent.auth.email_otp import AuthConfigurationError, EmailDomainNotAllowedError, EmailDomainPolicy, OtpCrypto


def test_six_digits_including_leading_zero_and_context_bound_digest():
    with patch("lab_agent.auth.email_otp.secrets.randbelow", return_value=4921):
        code = OtpCrypto.generate_code()
    assert code == "004921"
    first, second = uuid.uuid4(), uuid.uuid4()
    crypto = OtpCrypto("fake-otp-secret")
    digest = crypto.digest(first, code)
    assert digest == crypto.digest(first, code)
    assert digest != crypto.digest(second, code)
    assert digest != code
    assert crypto.matches(first, code, digest)
    assert not crypto.matches(first, "004922", digest)


def test_missing_secret_fails_closed():
    with pytest.raises(AuthConfigurationError):
        OtpCrypto(None).digest(uuid.uuid4(), "123456")


def test_email_domain_exact_match_and_normalization():
    policy = EmailDomainPolicy("mails.tsinghua.edu.cn,mail.tsinghua.edu.cn")
    assert policy.normalize(" User@MAILS.TSINGHUA.EDU.CN ") == "user@mails.tsinghua.edu.cn"
    assert policy.normalize("user@mail.tsinghua.edu.cn") == "user@mail.tsinghua.edu.cn"
    for address in ("user@gmail.com", "user@eviltsinghua.edu.cn", "user@sub.mails.tsinghua.edu.cn"):
        with pytest.raises(EmailDomainNotAllowedError):
            policy.normalize(address)
