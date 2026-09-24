"""Synchronous, verified-TLS SMTP transport for OTP emails."""

import math
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr

from lab_agent.utils.config import Config
from .email_sender import (
    EmailAuthenticationError,
    EmailDeliveryError,
    EmailSenderConfigurationError,
)


def _header_safe(value: str | None) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\r" not in value and "\n" not in value


class SmtpEmailSender:
    """Only delivers mail; challenge issuance and authorization belong elsewhere."""

    def __init__(self, config: Config | None = None):
        self.config = config if config is not None else Config()
        if (
            not _header_safe(self.config.smtp_host)
            or not isinstance(self.config.smtp_port, int)
            or not 1 <= self.config.smtp_port <= 65535
            or not _header_safe(self.config.smtp_username)
            or "@" not in self.config.smtp_username
            or not _header_safe(self.config.smtp_password)
            or not _header_safe(self.config.smtp_from)
            or "@" not in self.config.smtp_from
            or not _header_safe(self.config.smtp_from_name)
            or not self.config.smtp_use_ssl
            or self.config.smtp_timeout_seconds <= 0
        ):
            raise EmailSenderConfigurationError("SMTP configuration is missing or unsupported")

    def send_verification_code(self, *, recipient: str, code: str, expires_at: datetime) -> None:
        if not _header_safe(recipient) or "@" not in recipient:
            raise EmailDeliveryError("Unable to send verification email")
        if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdecimal():
            raise EmailDeliveryError("Unable to send verification email")
        if expires_at.tzinfo is None:
            raise EmailDeliveryError("Unable to send verification email")
        seconds_remaining = (expires_at - datetime.now(timezone.utc)).total_seconds()
        if seconds_remaining <= 0:
            raise EmailDeliveryError("Unable to send verification email")
        minutes_remaining = max(1, math.ceil(seconds_remaining / 60))

        message = EmailMessage()
        message["From"] = formataddr((self.config.smtp_from_name, self.config.smtp_from))
        message["To"] = recipient
        message["Subject"] = "LabAgent verification code"
        message.set_content(
            "Your LabAgent verification code is:\n\n"
            f"{code}\n\n"
            f"This code expires in approximately {minutes_remaining} minute(s).\n\n"
            "If you did not request this code, you can ignore this email.\n"
        )

        try:
            with smtplib.SMTP_SSL(
                self.config.smtp_host,
                self.config.smtp_port,
                context=ssl.create_default_context(),
                timeout=self.config.smtp_timeout_seconds,
            ) as smtp:
                smtp.ehlo()
                smtp.login(self.config.smtp_username, self.config.smtp_password)
                refused = smtp.send_message(message)
                if refused:
                    raise EmailDeliveryError("Unable to send verification email")
        except smtplib.SMTPAuthenticationError:
            raise EmailAuthenticationError("SMTP authentication failed") from None
        except (smtplib.SMTPException, TimeoutError, OSError):
            raise EmailDeliveryError("Unable to send verification email") from None
