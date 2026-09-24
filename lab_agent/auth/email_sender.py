"""Transport interface and safe errors for verification emails."""

from datetime import datetime
from typing import Protocol


class EmailDeliveryError(RuntimeError):
    """The email could not be delivered; safe to surface without SMTP details."""


class EmailAuthenticationError(EmailDeliveryError):
    """The configured SMTP account could not authenticate."""


class EmailSenderConfigurationError(EmailDeliveryError):
    """SMTP transport configuration is missing or unsupported."""


class EmailSender(Protocol):
    def send_verification_code(
        self, *, recipient: str, code: str, expires_at: datetime
    ) -> None: ...
