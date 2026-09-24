"""PostgreSQL RBAC, bootstrap, ownership and concurrent admin mutations."""

from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from lab_agent.api.app import create_app
from lab_agent.api.dependencies import get_auth_config, get_db_session_factory
from lab_agent.auth.admin_users import (
    AdminBootstrapError, AdminSafetyError, AdminUserService, BootstrapAdminService,
)
from lab_agent.auth.authorization import AdminAuthorizationError
from lab_agent.auth.models import CurrentUser
from lab_agent.auth.sessions import SessionService
from lab_agent.db.models import MessageRole, User, UserRole
from lab_agent.db.session import database_session
from lab_agent.repositories.admin_users import AdminUserRepository
from lab_agent.repositories.conversations import ConversationRepository
from lab_agent.repositories.messages import MessageRepository
from lab_agent.repositories.user_sessions import UserSessionRepository
from lab_agent.repositories.users import UserRepository


pytestmark = pytest.mark.integration
DOMAINS = SimpleNamespace(allowed_email_domains="mails.tsinghua.edu.cn,mail.tsinghua.edu.cn")
SESSION_CONFIG = SimpleNamespace(
    auth_session_hmac_secret="fake-integration-session-key", auth_session_ttl_days=30,
    auth_cookie_name="labagent_session", auth_cookie_secure=False,
)


def add_user(session, local: str, role: UserRole = UserRole.USER, active: bool = True) -> User:
    return UserRepository(session).add(User(
        email=f"{local}@mails.tsinghua.edu.cn", display_name=local,
        role=role, is_active=active,
    ))


def active_admin_count(session) -> int:
    return session.scalar(select(func.count()).select_from(User).where(
        User.role == UserRole.ADMIN, User.is_active.is_(True)
    ))


def test_bootstrap_only_existing_active_user_and_caller_commit(integration_session_factory):
    with database_session(integration_session_factory) as session:
        target = add_user(session, "first")
        disabled = add_user(session, "disabled", active=False)
        target_id = target.id
        disabled_email = disabled.email

    with pytest.raises(AdminBootstrapError, match="log in once"):
        with database_session(integration_session_factory) as session:
            BootstrapAdminService(AdminUserRepository(session), DOMAINS).bootstrap_first_admin(
                "missing@mails.tsinghua.edu.cn"
            )
    with pytest.raises(AdminBootstrapError, match="Inactive"):
        with database_session(integration_session_factory) as session:
            BootstrapAdminService(AdminUserRepository(session), DOMAINS).bootstrap_first_admin(disabled_email)

    with database_session(integration_session_factory) as session:
        result = BootstrapAdminService(AdminUserRepository(session), DOMAINS).bootstrap_first_admin(
            " FIRST@MAILS.TSINGHUA.EDU.CN "
        )
        assert result.id == target_id and result.role is UserRole.ADMIN
    with database_session(integration_session_factory) as session:
        assert active_admin_count(session) == 1
        assert session.get(User, target_id).role is UserRole.ADMIN
        assert session.scalar(select(func.count()).select_from(User)) == 2
    with pytest.raises(AdminBootstrapError, match="already exists"):
        with database_session(integration_session_factory) as session:
            BootstrapAdminService(AdminUserRepository(session), DOMAINS).bootstrap_first_admin(disabled_email)


def test_bootstrap_cli_uses_existing_user_and_refuses_repeat(integration_session_factory, monkeypatch, capsys):
    cli = import_module("lab_agent.admin.__main__")
    monkeypatch.setattr(cli, "database_session", lambda: database_session(integration_session_factory))
    monkeypatch.setenv("LABAGENT_ALLOWED_EMAIL_DOMAINS", DOMAINS.allowed_email_domains)
    with database_session(integration_session_factory) as session:
        target = add_user(session, "operator")
        target_id = target.id
    assert cli.main(["bootstrap-admin", " OPERATOR@MAILS.TSINGHUA.EDU.CN "]) == 0
    assert "operator@mails.tsinghua.edu.cn" in capsys.readouterr().out
    with database_session(integration_session_factory) as session:
        assert session.get(User, target_id).role is UserRole.ADMIN
        assert session.scalar(select(func.count()).select_from(User)) == 1
    with pytest.raises(SystemExit) as repeated:
        cli.main(["bootstrap-admin", "operator@mails.tsinghua.edu.cn"])
    assert repeated.value.code == 2


def test_admin_management_self_protection_and_rollback(integration_session_factory):
    with database_session(integration_session_factory) as session:
        admin = add_user(session, "admin", UserRole.ADMIN)
        target = add_user(session, "target")
        actor = CurrentUser.from_user(admin)
        admin_id, target_id = admin.id, target.id

    with database_session(integration_session_factory) as session:
        service = AdminUserService(AdminUserRepository(session))
        assert {user.email for user in service.list_users(actor=actor)} == {
            "admin@mails.tsinghua.edu.cn", "target@mails.tsinghua.edu.cn"
        }
        service.change_user_role(actor=actor, target_user_id=target_id, new_role="admin")
    with database_session(integration_session_factory) as session:
        assert active_admin_count(session) == 2
        service = AdminUserService(AdminUserRepository(session))
        with pytest.raises(AdminSafetyError, match="own"):
            service.change_user_role(actor=actor, target_user_id=admin_id, new_role="user")
        with pytest.raises(AdminSafetyError, match="own"):
            service.set_user_active(actor=actor, target_user_id=admin_id, is_active=False)
        service.set_user_active(actor=actor, target_user_id=target_id, is_active=False)
    with database_session(integration_session_factory) as session:
        assert active_admin_count(session) == 1
        service = AdminUserService(AdminUserRepository(session))
        service.set_user_active(actor=actor, target_user_id=target_id, is_active=True)
        service.change_user_role(actor=actor, target_user_id=target_id, new_role="user")
    with database_session(integration_session_factory) as session:
        assert active_admin_count(session) == 1
        assert session.get(User, target_id).role is UserRole.USER

    with pytest.raises(RuntimeError, match="rollback"):
        with database_session(integration_session_factory) as session:
            AdminUserService(AdminUserRepository(session)).change_user_role(
                actor=actor, target_user_id=target_id, new_role="admin"
            )
            raise RuntimeError("rollback")
    with database_session(integration_session_factory) as session:
        assert session.get(User, target_id).role is UserRole.USER


@pytest.mark.parametrize("operation", ["demote", "disable"])
def test_two_admins_cannot_concurrently_remove_each_other(integration_session_factory, operation):
    with database_session(integration_session_factory) as session:
        first = add_user(session, "first", UserRole.ADMIN)
        second = add_user(session, "second", UserRole.ADMIN)
        actors = (CurrentUser.from_user(first), CurrentUser.from_user(second))
    gate = Barrier(2)

    def worker(actor: CurrentUser, target: CurrentUser) -> str:
        gate.wait(timeout=15)
        try:
            with database_session(integration_session_factory) as session:
                service = AdminUserService(AdminUserRepository(session))
                if operation == "demote":
                    service.change_user_role(actor=actor, target_user_id=target.id, new_role="user")
                else:
                    service.set_user_active(actor=actor, target_user_id=target.id, is_active=False)
            return "success"
        except AdminAuthorizationError:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, actors[0], actors[1]), pool.submit(worker, actors[1], actors[0])]
        results = [future.result(timeout=20) for future in futures]
    assert sorted(results) == ["denied", "success"]
    with database_session(integration_session_factory) as session:
        assert active_admin_count(session) == 1


def test_concurrent_bootstrap_allows_only_one(integration_session_factory):
    with database_session(integration_session_factory) as session:
        emails = (add_user(session, "first").email, add_user(session, "second").email)
    gate = Barrier(2)

    def worker(email: str) -> str:
        gate.wait(timeout=15)
        try:
            with database_session(integration_session_factory) as session:
                BootstrapAdminService(AdminUserRepository(session), DOMAINS).bootstrap_first_admin(email)
            return "success"
        except AdminBootstrapError:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, email) for email in emails]
        results = [future.result(timeout=20) for future in futures]
    assert sorted(results) == ["denied", "success"]
    with database_session(integration_session_factory) as session:
        assert active_admin_count(session) == 1


def test_admin_still_cannot_read_other_users_private_data(db_session):
    admin = add_user(db_session, "admin", UserRole.ADMIN)
    owner = add_user(db_session, "owner")
    conversation = ConversationRepository(db_session).create_for_user(owner.id)
    MessageRepository(db_session).append_message_for_user_conversation(
        owner.id, conversation.id, role=MessageRole.USER, content="private research"
    )
    db_session.commit()
    assert ConversationRepository(db_session).get_for_user(admin.id, conversation.id) is None
    assert MessageRepository(db_session).list_for_user_conversation(admin.id, conversation.id) == []


def test_admin_api_uses_live_cookie_and_service_authorization(integration_session_factory):
    with database_session(integration_session_factory) as session:
        admin = add_user(session, "admin", UserRole.ADMIN)
        ordinary = add_user(session, "ordinary")
        admin_id, ordinary_id = admin.id, ordinary.id
        users = UserRepository(session)
        sessions = SessionService(UserSessionRepository(session), users, SESSION_CONFIG)
        admin_token = sessions.create_session(admin_id).raw_token
        ordinary_token = sessions.create_session(ordinary_id).raw_token

    app = create_app()
    app.dependency_overrides[get_db_session_factory] = lambda: integration_session_factory
    app.dependency_overrides[get_auth_config] = lambda: SESSION_CONFIG
    with TestClient(app) as client:
        url = f"/admin/users/{ordinary_id}"
        assert client.get("/admin/users").status_code == 401
        client.cookies.set("labagent_session", ordinary_token)
        assert client.get("/admin/users?role=admin&actor_user_id={admin_id}").status_code == 403
        assert client.patch(f"{url}/role", json={"role": "admin"}).status_code == 403
        client.cookies.set("labagent_session", admin_token)
        assert client.get("/admin/users").status_code == 200
        assert client.patch(f"{url}/role", json={"role": "superuser"}).status_code == 422
        assert client.patch(f"{url}/role", json={"role": "admin", "actor_user_id": str(admin_id)}).status_code == 422
        assert client.patch(f"/admin/users/{admin_id}/active", json={"is_active": False}).status_code == 409
        assert client.patch(f"{url}/role", json={"role": "admin"}).status_code == 200
        client.cookies.set("labagent_session", ordinary_token)
        assert client.get("/admin/users").status_code == 200
        client.cookies.set("labagent_session", admin_token)
        assert client.patch(f"{url}/role", json={"role": "user"}).status_code == 200
        client.cookies.set("labagent_session", ordinary_token)
        assert client.get("/admin/users").status_code == 403
        client.cookies.set("labagent_session", admin_token)
        assert client.patch(f"{url}/role", json={"role": "admin"}).status_code == 200
        assert client.patch(f"{url}/active", json={"is_active": False}).status_code == 200
        client.cookies.set("labagent_session", ordinary_token)
        assert client.get("/auth/me").status_code == 401
    with database_session(integration_session_factory) as session:
        assert session.get(User, ordinary_id).role is UserRole.ADMIN
        assert session.get(User, ordinary_id).is_active is False
        assert active_admin_count(session) == 1
