"""Provider-independent authentication application service."""

from dataclasses import dataclass
from uuid import UUID

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
    tenant_id: UUID | None
    object_id: UUID | None
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
        tenant_user = None
        if identity.tenant_id is not None and identity.object_id is not None:
            tenant_user = self.user_repository.get_by_tenant_object(
                identity.tenant_id,
                identity.object_id,
            )
        email_user = self.user_repository.get_by_email(identity.email)

        matched_users = [
            user
            for user in (subject_user, tenant_user, email_user)
            if user is not None
        ]
        if len({user.id for user in matched_users}) > 1:
            raise IdentityConflictError("Identity claims conflict with an account")

        user = subject_user or tenant_user or email_user
        if user is not None and user.external_subject not in (None, identity.subject):
            raise IdentityConflictError("Identity claims conflict with an account")
        if user is not None and identity.tenant_id is not None:
            existing_pair = (user.tenant_id, user.external_object_id)
            incoming_pair = (identity.tenant_id, identity.object_id)
            if existing_pair != (None, None) and existing_pair != incoming_pair:
                raise IdentityConflictError("Identity claims conflict with an account")

        if user is None:
            if not self.allow_auto_provision:
                raise UnknownUserError("User is not authorized")
            login_time = utc_now()
            user = self.user_repository.add(
                User(
                    external_subject=identity.subject,
                    tenant_id=identity.tenant_id,
                    external_object_id=identity.object_id,
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
            tenant_id=(
                identity.tenant_id
                if identity.tenant_id is not None
                else user.tenant_id
            ),
            external_object_id=(
                identity.object_id
                if identity.object_id is not None
                else user.external_object_id
            ),
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

        tenant_id = claims.tenant_id
        object_id = claims.object_id
        if tenant_id is not None and not isinstance(tenant_id, UUID):
            raise InvalidIdentityError("Identity tenant is invalid")
        if object_id is not None and not isinstance(object_id, UUID):
            raise InvalidIdentityError("Identity object is invalid")
        if (tenant_id is None) != (object_id is None):
            raise InvalidIdentityError("Identity tenant and object must be provided together")

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
            tenant_id=tenant_id,
            object_id=object_id,
            email=email,
            display_name=display_name,
        )
