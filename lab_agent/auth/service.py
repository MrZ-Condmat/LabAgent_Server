"""Provider-independent authentication application service."""

from dataclasses import dataclass

from lab_agent.db.models import User, UserRole
from lab_agent.db.models.user import utc_now
from lab_agent.repositories import UserRepository

from .errors import (
    IdentityConflictError,
    InvalidIdentityError,
    UnknownUserError,
    UserInactiveError,
)
from .models import CurrentUser, IdentityClaims


@dataclass(frozen=True, slots=True)
class _NormalizedIdentity:
    subject: str
    email: str
    display_name: str


class AuthenticationService:
    """Resolve trusted, normalized provider claims to a local CurrentUser."""

    def __init__(
        self,
        user_repository: UserRepository,
        *,
        allow_auto_provision: bool = False,
    ):
        self.user_repository = user_repository
        self.allow_auto_provision = allow_auto_provision

    def resolve_user(self, claims: IdentityClaims) -> CurrentUser:
        identity = self._normalize_identity(claims)
        subject_user = self.user_repository.get_by_external_subject(identity.subject)
        email_user = self.user_repository.get_by_email(identity.email)

        if (
            subject_user is not None
            and email_user is not None
            and subject_user.id != email_user.id
        ):
            raise IdentityConflictError("Identity claims conflict with an account")

        user = subject_user
        if user is None and email_user is not None:
            if email_user.external_subject not in (None, identity.subject):
                raise IdentityConflictError("Identity claims conflict with an account")
            user = email_user

        if user is None:
            if not self.allow_auto_provision:
                raise UnknownUserError("User is not authorized")
            login_time = utc_now()
            user = self.user_repository.add(
                User(
                    external_subject=identity.subject,
                    email=identity.email,
                    display_name=identity.display_name,
                    role=UserRole.USER,
                    is_active=True,
                    last_login_at=login_time,
                )
            )
            return CurrentUser.from_user(user)

        if not user.is_active:
            raise UserInactiveError("User account is inactive")

        user = self.user_repository.update_login_identity(
            user,
            external_subject=identity.subject,
            email=identity.email,
            display_name=identity.display_name,
            last_login_at=utc_now(),
        )
        return CurrentUser.from_user(user)

    @staticmethod
    def _normalize_identity(claims: IdentityClaims) -> _NormalizedIdentity:
        if not isinstance(claims.subject, str):
            raise InvalidIdentityError("Identity subject is invalid")
        subject = claims.subject.strip()
        if not subject or len(subject) > 255:
            raise InvalidIdentityError("Identity subject is invalid")

        if not isinstance(claims.email, str):
            raise InvalidIdentityError("Identity email is invalid")
        email = claims.email.strip().lower()
        if (
            not email
            or len(email) > 320
            or email.count("@") != 1
            or any(character.isspace() for character in email)
        ):
            raise InvalidIdentityError("Identity email is invalid")
        local_part, domain = email.split("@", maxsplit=1)
        if not local_part or not domain:
            raise InvalidIdentityError("Identity email is invalid")

        display_name = (
            claims.display_name.strip()
            if isinstance(claims.display_name, str)
            else ""
        )
        if not display_name:
            display_name = email
        if len(display_name) > 255:
            raise InvalidIdentityError("Identity display name is invalid")

        return _NormalizedIdentity(
            subject=subject,
            email=email,
            display_name=display_name,
        )
