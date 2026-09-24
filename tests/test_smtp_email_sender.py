"""SMTP transport tests use mocks and never open a network connection."""

import smtplib
import ssl
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from lab_agent.auth.email_sender import (
    EmailAuthenticationError,
    EmailDeliveryError,
    EmailSenderConfigurationError,
)
from lab_agent.auth.smtp_sender import SmtpEmailSender
from lab_agent.db.models.user import utc_now
from lab_agent.utils.config import Config


def settings(**overrides):
    values = dict(
        smtp_host="mails.tsinghua.edu.cn", smtp_port=465,
        smtp_username="sender@mails.tsinghua.edu.cn", smtp_password="fake-password",
        smtp_from="sender@mails.tsinghua.edu.cn", smtp_from_name="LabAgent",
        smtp_use_ssl=True, smtp_timeout_seconds=20,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_verified_tls_handshake_message_and_close():
    smtp = Mock()
    smtp.send_message.return_value = {}
    manager = Mock()
    manager.__enter__ = Mock(return_value=smtp)
    manager.__exit__ = Mock(return_value=False)
    with patch("lab_agent.auth.smtp_sender.smtplib.SMTP_SSL", return_value=manager) as connect:
        SmtpEmailSender(settings()).send_verification_code(
            recipient="user@mail.tsinghua.edu.cn", code="004921",
            expires_at=utc_now() + timedelta(minutes=5),
        )
    assert connect.call_args.args == ("mails.tsinghua.edu.cn", 465)
    assert connect.call_args.kwargs["timeout"] == 20
    context = connect.call_args.kwargs["context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    smtp.ehlo.assert_called_once_with()
    smtp.login.assert_called_once_with("sender@mails.tsinghua.edu.cn", "fake-password")
    message = smtp.send_message.call_args.args[0]
    assert message["From"] == "LabAgent <sender@mails.tsinghua.edu.cn>"
    assert message["To"] == "user@mail.tsinghua.edu.cn"
    assert message["Subject"] == "LabAgent verification code"
    body = message.get_content()
    assert "004921" in body and "approximately" in body and "minute" in body
    manager.__exit__.assert_called_once()


def test_authentication_error_has_no_credentials():
    smtp = Mock()
    smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"authentication failed")
    manager = Mock()
    manager.__enter__ = Mock(return_value=smtp)
    manager.__exit__ = Mock(return_value=False)
    with patch("lab_agent.auth.smtp_sender.smtplib.SMTP_SSL", return_value=manager):
        with pytest.raises(EmailAuthenticationError) as error:
            SmtpEmailSender(settings()).send_verification_code(
                recipient="user@mail.tsinghua.edu.cn", code="123456",
                expires_at=utc_now() + timedelta(minutes=5),
            )
    assert "fake-password" not in str(error.value)
    assert "123456" not in str(error.value)


@pytest.mark.parametrize("failure", [TimeoutError("timeout"), OSError("offline"), smtplib.SMTPConnectError(421, "unavailable")])
def test_network_errors_are_mapped(failure):
    with patch("lab_agent.auth.smtp_sender.smtplib.SMTP_SSL", side_effect=failure):
        with pytest.raises(EmailDeliveryError, match="Unable to send verification email"):
            SmtpEmailSender(settings()).send_verification_code(
                recipient="user@mail.tsinghua.edu.cn", code="123456",
                expires_at=utc_now() + timedelta(minutes=5),
            )


def test_recipient_refusal_is_mapped():
    smtp = Mock()
    smtp.send_message.side_effect = smtplib.SMTPRecipientsRefused({"user@mail.tsinghua.edu.cn": (550, b"refused")})
    manager = Mock()
    manager.__enter__ = Mock(return_value=smtp)
    manager.__exit__ = Mock(return_value=False)
    with patch("lab_agent.auth.smtp_sender.smtplib.SMTP_SSL", return_value=manager):
        with pytest.raises(EmailDeliveryError, match="Unable to send verification email"):
            SmtpEmailSender(settings()).send_verification_code(
                recipient="user@mail.tsinghua.edu.cn", code="123456",
                expires_at=utc_now() + timedelta(minutes=5),
            )


@pytest.mark.parametrize("override", [
    {"smtp_password": ""}, {"smtp_username": "short-name"}, {"smtp_use_ssl": False},
    {"smtp_from": "bad\r\nBcc: attacker@example.com"},
])
def test_invalid_configuration_fails_closed(override):
    with pytest.raises(EmailSenderConfigurationError):
        SmtpEmailSender(settings(**override))


def test_recipient_header_injection_rejected_without_connecting():
    with patch("lab_agent.auth.smtp_sender.smtplib.SMTP_SSL") as connect:
        with pytest.raises(EmailDeliveryError):
            SmtpEmailSender(settings()).send_verification_code(
                recipient="user@mail.tsinghua.edu.cn\r\nBcc: attacker@example.com",
                code="123456", expires_at=utc_now() + timedelta(minutes=5),
            )
    connect.assert_not_called()


def test_config_defaults_do_not_require_credentials(monkeypatch, tmp_path):
    for name in ("SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM",
                 "SMTP_FROM_NAME", "SMTP_USE_SSL", "SMTP_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    config = Config(config_path=str(tmp_path / "absent.env"))
    assert (config.smtp_host, config.smtp_port, config.smtp_use_ssl,
            config.smtp_from_name, config.smtp_timeout_seconds) == (
                "mails.tsinghua.edu.cn", 465, True, "LabAgent", 20)
    assert config.smtp_username is None and config.smtp_password is None and config.smtp_from is None
    with pytest.raises(EmailSenderConfigurationError):
        SmtpEmailSender(config)
