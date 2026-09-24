"""Orchestrate verified email OTP login within a caller-owned transaction."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from lab_agent.db.models import User, UserRole
from lab_agent.db.models.user import utc_now
from lab_agent.repositories.users import UserRepository

from .email_otp import EmailOtpService
from .errors import UserInactiveError
from .models import CurrentUser
from .sessions import SessionService


@dataclass(frozen=True, slots=True)
class EmailOtpAuthenticationResult:
    current_user: CurrentUser
    session_id: UUID
    session_token: str
    expires_at: datetime

    def __repr__(self) -> str:
        return (
            f"EmailOtpAuthenticationResult(current_user={self.current_user!r}, "
            f"session_id={self.session_id!r}, session_token=<redacted>, "
            f"expires_at={self.expires_at!r})"
        )


@dataclass(frozen=True, slots=True)
class EmailOtpAuthenticationAttempt:
    """A wrong code returns normally so its failed-attempt count can commit."""

    authentication: EmailOtpAuthenticationResult | None

    @property
    def succeeded(self) -> bool:
        return self.authentication is not None


class EmailOtpAuthenticationService:
    def __init__(
        self,
        otp_service: EmailOtpService,
        users: UserRepository,
        sessions: SessionService,
    ):
        self.otp_service = otp_service
        self.users = users
        self.sessions = sessions

    def authenticate(
        self, *, challenge_id: UUID, email: str, code: str
    ) -> EmailOtpAuthenticationAttempt:
        verification = self.otp_service.verify_challenge(challenge_id, email, code)
        if not verification.succeeded:
            # Caller should commit this normal return to persist failed_attempts.
            return EmailOtpAuthenticationAttempt(None)

        verified_email = verification.verified_email
        user = self.users.get_by_email(verified_email)
        if user is None:
            user = self.users.add(
                User(
                    email=verified_email,
                    display_name=verified_email.split("@", 1)[0][:255],
                    role=UserRole.USER,
                    is_active=True,
                    external_subject=None,
                    tenant_id=None,
                    external_object_id=None,
                )
            )
        if not user.is_active:
            raise UserInactiveError("User is inactive")

        self.users.update_last_login(user, last_login_at=utc_now())
        issued = self.sessions.create_session(user.id)
        return EmailOtpAuthenticationAttempt(
            EmailOtpAuthenticationResult(
                current_user=CurrentUser.from_user(user),
                session_id=issued.session_id,
                session_token=issued.raw_token,
                expires_at=issued.expires_at,
            )
        )
