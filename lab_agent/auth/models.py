"""Provider-neutral identity types used by the application layer."""

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from lab_agent.db.models import UserRole

if TYPE_CHECKING:
    from lab_agent.db.models import User


@dataclass(frozen=True, slots=True)
class IdentityClaims:
    """Identity fields already extracted by a trusted provider adapter."""

    subject: str
    email: str
    display_name: str


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Immutable application identity derived from a persisted user."""

    id: UUID
    external_subject: str | None
    email: str
    display_name: str
    role: UserRole
    is_active: bool

    @classmethod
    def from_user(cls, user: "User") -> "CurrentUser":
        return cls(
            id=user.id,
            external_subject=user.external_subject,
            email=user.email,
            display_name=user.display_name,
            role=user.role,
            is_active=user.is_active,
        )
