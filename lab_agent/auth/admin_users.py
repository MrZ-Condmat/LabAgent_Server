"""Administrator user management and one-time bootstrap orchestration."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from lab_agent.db.models import User, UserRole
from lab_agent.repositories.admin_users import AdminUserRepository
from lab_agent.utils.config import Config

from .authorization import AdminAuthorizationError, require_admin
from .email_otp import EmailDomainPolicy
from .models import CurrentUser


class AdminUserNotFoundError(LookupError):
    pass


class AdminSafetyError(ValueError):
    pass


class AdminBootstrapError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AdminUserSummary:
    id: UUID
    email: str
    display_name: str
    role: UserRole
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None

    @classmethod
    def from_user(cls, user: User) -> "AdminUserSummary":
        return cls(
            id=user.id, email=user.email, display_name=user.display_name,
            role=user.role, is_active=user.is_active,
            created_at=user.created_at, last_login_at=user.last_login_at,
        )


class AdminUserService:
    """Never trusts a role claim without checking the current database row."""

    def __init__(self, users: AdminUserRepository):
        self.users = users

    def _checked_actor(self, actor: CurrentUser, *, for_update: bool = False) -> User:
        require_admin(actor)
        current = self.users.get_by_id(actor.id, for_update=for_update)
        if current is None or current.role is not UserRole.ADMIN or not current.is_active:
            raise AdminAuthorizationError("Administrator access required")
        return current

    def list_users(self, *, actor: CurrentUser) -> list[AdminUserSummary]:
        self._checked_actor(actor, for_update=True)
        return [AdminUserSummary.from_user(user) for user in self.users.list_users()]

    def get_user_for_admin(self, *, actor: CurrentUser, target_user_id: UUID) -> AdminUserSummary:
        self._checked_actor(actor, for_update=True)
        target = self.users.get_by_id(target_user_id)
        if target is None:
            raise AdminUserNotFoundError("User not found")
        return AdminUserSummary.from_user(target)

    def change_user_role(
        self, *, actor: CurrentUser, target_user_id: UUID, new_role: UserRole | str
    ) -> AdminUserSummary:
        try:
            role = UserRole(new_role)
        except ValueError as exc:
            raise ValueError("Invalid user role") from exc
        require_admin(actor)
        self.users.lock_admin_mutations()
        self._checked_actor(actor, for_update=True)
        target = self.users.get_by_id(target_user_id, for_update=True)
        if target is None:
            raise AdminUserNotFoundError("User not found")
        if target.id == actor.id and role is not UserRole.ADMIN:
            raise AdminSafetyError("You cannot demote your own administrator account")
        if target.role is UserRole.ADMIN and target.is_active and role is not UserRole.ADMIN:
            if self.users.active_admin_count() <= 1:
                raise AdminSafetyError("The last active administrator cannot be removed")
        target.role = role
        self.users.flush()
        return AdminUserSummary.from_user(target)

    def set_user_active(
        self, *, actor: CurrentUser, target_user_id: UUID, is_active: bool
    ) -> AdminUserSummary:
        if not isinstance(is_active, bool):
            raise ValueError("Invalid active state")
        require_admin(actor)
        self.users.lock_admin_mutations()
        self._checked_actor(actor, for_update=True)
        target = self.users.get_by_id(target_user_id, for_update=True)
        if target is None:
            raise AdminUserNotFoundError("User not found")
        if target.id == actor.id and not is_active:
            raise AdminSafetyError("You cannot disable your own administrator account")
        if target.role is UserRole.ADMIN and target.is_active and not is_active:
            if self.users.active_admin_count() <= 1:
                raise AdminSafetyError("The last active administrator cannot be removed")
        target.is_active = is_active
        self.users.flush()
        return AdminUserSummary.from_user(target)


class BootstrapAdminService:
    """One-time maintenance operation; never creates users or logs them in."""

    def __init__(self, users: AdminUserRepository, config: Config | None = None):
        self.users = users
        self.policy = EmailDomainPolicy((config or Config()).allowed_email_domains)

    def bootstrap_first_admin(self, email: str) -> AdminUserSummary:
        normalized = self.policy.normalize(email)
        self.users.lock_admin_mutations()
        if self.users.active_admin_count() != 0:
            raise AdminBootstrapError(
                "An active administrator already exists; use administrator management"
            )
        target = self.users.get_by_email(normalized, for_update=True)
        if target is None:
            raise AdminBootstrapError("User must log in once before bootstrap")
        if not target.is_active:
            raise AdminBootstrapError("Inactive user cannot become administrator")
        target.role = UserRole.ADMIN
        self.users.flush()
        return AdminUserSummary.from_user(target)
