"""Offline checks for centralized policy and administrator service rules."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from lab_agent.auth.admin_users import (
    AdminBootstrapError, AdminSafetyError, AdminUserService,
    BootstrapAdminService,
)
from lab_agent.auth.authorization import AdminAuthorizationError, is_admin, require_admin
from lab_agent.auth.models import CurrentUser
from lab_agent.db.models import User, UserRole
from lab_agent.db.models.user import utc_now


def make_user(email: str, role: UserRole = UserRole.USER, active: bool = True) -> User:
    return User(id=uuid4(), email=email, display_name=email.split("@", 1)[0],
                role=role, is_active=active, created_at=utc_now())


class FakeAdminUsers:
    def __init__(self, *users: User):
        self.users = {user.id: user for user in users}
        self.lock_calls = 0
        self.flush_calls = 0

    def lock_admin_mutations(self):
        self.lock_calls += 1

    def list_users(self):
        return list(self.users.values())

    def get_by_id(self, user_id, *, for_update=False):
        return self.users.get(user_id)

    def get_by_email(self, email, *, for_update=False):
        return next((user for user in self.users.values() if user.email == email), None)

    def active_admin_count(self):
        return sum(user.role is UserRole.ADMIN and user.is_active for user in self.users.values())

    def flush(self):
        self.flush_calls += 1


def test_authorization_requires_active_admin_identity():
    admin = make_user("admin@example.com", UserRole.ADMIN)
    ordinary = make_user("user@example.com")
    inactive = make_user("inactive@example.com", UserRole.ADMIN, active=False)
    assert is_admin(CurrentUser.from_user(admin))
    assert require_admin(CurrentUser.from_user(admin)).id == admin.id
    for candidate in (None, CurrentUser.from_user(ordinary), CurrentUser.from_user(inactive)):
        assert not is_admin(candidate)
        with pytest.raises(AdminAuthorizationError):
            require_admin(candidate)


def test_user_cannot_list_or_change_roles_or_active_state():
    actor = make_user("user@example.com")
    target = make_user("target@example.com")
    service = AdminUserService(FakeAdminUsers(actor, target))
    identity = CurrentUser.from_user(actor)
    with pytest.raises(AdminAuthorizationError):
        service.list_users(actor=identity)
    with pytest.raises(AdminAuthorizationError):
        service.get_user_for_admin(actor=identity, target_user_id=target.id)
    with pytest.raises(AdminAuthorizationError):
        service.change_user_role(actor=identity, target_user_id=actor.id, new_role="admin")
    with pytest.raises(AdminAuthorizationError):
        service.change_user_role(actor=identity, target_user_id=target.id, new_role="admin")
    with pytest.raises(AdminAuthorizationError):
        service.set_user_active(actor=identity, target_user_id=target.id, is_active=False)
    assert actor.role is UserRole.USER and target.is_active


def test_admin_can_manage_others_and_stale_identity_is_rechecked():
    actor = make_user("admin@example.com", UserRole.ADMIN)
    target = make_user("target@example.com")
    second = make_user("second@example.com", UserRole.ADMIN)
    repository = FakeAdminUsers(actor, target, second)
    service = AdminUserService(repository)
    identity = CurrentUser.from_user(actor)
    assert len(service.list_users(actor=identity)) == 3
    assert service.get_user_for_admin(actor=identity, target_user_id=target.id).email == target.email
    assert service.change_user_role(actor=identity, target_user_id=target.id, new_role="admin").role is UserRole.ADMIN
    assert service.set_user_active(actor=identity, target_user_id=target.id, is_active=False).is_active is False
    assert service.set_user_active(actor=identity, target_user_id=target.id, is_active=True).is_active is True
    assert service.change_user_role(actor=identity, target_user_id=second.id, new_role="user").role is UserRole.USER
    assert repository.lock_calls == 4 and repository.flush_calls == 4
    actor.role = UserRole.USER  # Immutable CurrentUser is now stale.
    with pytest.raises(AdminAuthorizationError):
        service.list_users(actor=identity)
    with pytest.raises(AdminAuthorizationError):
        service.set_user_active(actor=identity, target_user_id=target.id, is_active=False)


def test_self_protection_and_last_active_admin():
    admin = make_user("admin@example.com", UserRole.ADMIN)
    second = make_user("second@example.com", UserRole.ADMIN)
    repository = FakeAdminUsers(admin, second)
    service = AdminUserService(repository)
    actor = CurrentUser.from_user(admin)
    with pytest.raises(AdminSafetyError, match="own"):
        service.change_user_role(actor=actor, target_user_id=admin.id, new_role="user")
    with pytest.raises(AdminSafetyError, match="own"):
        service.set_user_active(actor=actor, target_user_id=admin.id, is_active=False)
    service.change_user_role(actor=actor, target_user_id=second.id, new_role="user")
    assert repository.active_admin_count() == 1
    second.role = UserRole.ADMIN
    second.is_active = True
    assert service.set_user_active(actor=actor, target_user_id=second.id, is_active=False).is_active is False
    assert repository.active_admin_count() == 1


def test_last_admin_count_is_checked_before_removing_another_admin():
    actor = make_user("actor@example.com", UserRole.ADMIN)
    target = make_user("target@example.com", UserRole.ADMIN)
    repository = FakeAdminUsers(actor, target)
    # Simulate a defensive low count returned from the database. Normal actor
    # validation and self-protection make this branch otherwise unreachable.
    repository.active_admin_count = lambda: 1
    service = AdminUserService(repository)
    with pytest.raises(AdminSafetyError, match="last active"):
        service.change_user_role(actor=CurrentUser.from_user(actor), target_user_id=target.id, new_role="user")
    with pytest.raises(AdminSafetyError, match="last active"):
        service.set_user_active(actor=CurrentUser.from_user(actor), target_user_id=target.id, is_active=False)
    assert target.role is UserRole.ADMIN and target.is_active


def test_invalid_role_and_active_value_rejected():
    admin = make_user("admin@example.com", UserRole.ADMIN)
    service = AdminUserService(FakeAdminUsers(admin))
    with pytest.raises(ValueError, match="Invalid user role"):
        service.change_user_role(actor=CurrentUser.from_user(admin), target_user_id=admin.id, new_role="superuser")
    with pytest.raises(ValueError, match="Invalid active state"):
        service.set_user_active(actor=CurrentUser.from_user(admin), target_user_id=admin.id, is_active="false")


def test_bootstrap_normalizes_existing_active_user_only():
    target = make_user("first@mails.tsinghua.edu.cn")
    repository = FakeAdminUsers(target)
    service = BootstrapAdminService(repository, SimpleNamespace(
        allowed_email_domains="mails.tsinghua.edu.cn,mail.tsinghua.edu.cn"
    ))
    result = service.bootstrap_first_admin(" FIRST@MAILS.TSINGHUA.EDU.CN ")
    assert result.id == target.id and result.role is UserRole.ADMIN
    assert len(repository.users) == 1 and repository.lock_calls == 1
    with pytest.raises(AdminBootstrapError, match="already exists"):
        service.bootstrap_first_admin(target.email)


@pytest.mark.parametrize("target", [None, make_user("disabled@mails.tsinghua.edu.cn", active=False)])
def test_bootstrap_rejects_missing_or_inactive_target(target):
    repository = FakeAdminUsers(*([target] if target else []))
    service = BootstrapAdminService(repository, SimpleNamespace(
        allowed_email_domains="mails.tsinghua.edu.cn,mail.tsinghua.edu.cn"
    ))
    email = target.email if target else "missing@mails.tsinghua.edu.cn"
    with pytest.raises(AdminBootstrapError):
        service.bootstrap_first_admin(email)
    assert repository.active_admin_count() == 0 and repository.flush_calls == 0
