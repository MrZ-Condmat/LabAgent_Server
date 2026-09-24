"""Central authorization policy for trusted, session-derived identities."""

from lab_agent.db.models import UserRole

from .models import CurrentUser


class AdminAuthorizationError(PermissionError):
    """The current identity may not perform an administrator operation."""


def is_admin(current_user: CurrentUser | None) -> bool:
    return (
        isinstance(current_user, CurrentUser)
        and current_user.is_active
        and current_user.role is UserRole.ADMIN
    )


def require_admin(current_user: CurrentUser | None) -> CurrentUser:
    if not is_admin(current_user):
        raise AdminAuthorizationError("Administrator access required")
    return current_user
