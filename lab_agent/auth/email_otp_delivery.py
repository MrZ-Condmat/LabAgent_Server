"""Issue an OTP challenge and deliver its plaintext code once."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from .email_otp import EmailOtpService
from .email_sender import EmailSender


@dataclass(frozen=True, slots=True)
class EmailDeliveryResult:
    challenge_id: UUID
    expires_at: datetime


class EmailOtpDeliveryService:
    def __init__(self, otp_service: EmailOtpService, sender: EmailSender):
        self.otp_service = otp_service
        self.sender = sender

    def request_verification_email(self, email: str) -> EmailDeliveryResult:
        """Caller owns commit/rollback. A send failure must roll back the challenge.

        SMTP success followed by DB commit failure can leave an unusable email;
        the user can request a new code. No distributed transaction is attempted.
        """
        issued = self.otp_service.issue_challenge(email)
        self.sender.send_verification_code(
            recipient=issued.email, code=issued.code, expires_at=issued.expires_at
        )
        return EmailDeliveryResult(issued.challenge_id, issued.expires_at)
