"""Offline FastAPI permission and input-boundary checks for admin routes."""

from datetime import timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from lab_agent.api import admin_routes
from lab_agent.api.app import create_app
from lab_agent.api.dependencies import get_auth_config, get_db_session_factory, require_current_user
from lab_agent.auth.admin_users import AdminUserSummary
from lab_agent.auth.models import CurrentUser
from lab_agent.db.models import UserRole
from lab_agent.db.models.user import utc_now


def identity(role: UserRole, *, active: bool = True) -> CurrentUser:
    return CurrentUser(
        id=uuid4(), external_subject=None, tenant_id=None, external_object_id=None,
        email="actor@mail.tsinghua.edu.cn", display_name="Actor",
        role=role, is_active=active,
    )


@pytest.fixture
def admin_api(monkeypatch):
    app = create_app()
    db = Mock()
    service = Mock()
    holder = {"actor": identity(UserRole.ADMIN)}
    target = AdminUserSummary(
        id=uuid4(), email="target@mail.tsinghua.edu.cn", display_name="Target",
        role=UserRole.USER, is_active=True, created_at=utc_now(), last_login_at=None,
    )
    service.list_users.return_value = [target]
    service.change_user_role.return_value = target
    service.set_user_active.return_value = target
    monkeypatch.setattr(admin_routes, "AdminUserService", lambda repository: service)
    app.dependency_overrides[get_auth_config] = lambda: SimpleNamespace(auth_cookie_name="labagent_session")
    app.dependency_overrides[get_db_session_factory] = lambda: lambda: db
    app.dependency_overrides[require_current_user] = lambda: holder["actor"]
    with TestClient(app) as client:
        yield client, app, service, holder, target


def test_unauthenticated_and_user_are_rejected(admin_api):
    client, app, service, holder, target = admin_api
    del app.dependency_overrides[require_current_user]
    assert client.get("/admin/users").status_code == 401
    assert client.patch(f"/admin/users/{target.id}/role", json={"role": "admin"}).status_code == 401
    assert client.patch(f"/admin/users/{target.id}/active", json={"is_active": False}).status_code == 401
    app.dependency_overrides[require_current_user] = lambda: holder["actor"]
    holder["actor"] = identity(UserRole.USER)
    assert client.get("/admin/users?role=admin&actor_user_id=forged").status_code == 403
    assert client.patch(f"/admin/users/{target.id}/role", json={"role": "admin"}).status_code == 403
    assert client.patch(f"/admin/users/{target.id}/active", json={"is_active": False}).status_code == 403
    holder["actor"] = identity(UserRole.ADMIN, active=False)
    assert client.get("/admin/users").status_code == 403
    service.list_users.assert_not_called()


def test_admin_list_and_mutations_use_only_resolved_identity(admin_api):
    client, _, service, holder, target = admin_api
    listing = client.get("/admin/users")
    assert listing.status_code == 200
    assert listing.json()[0] == {
        "id": str(target.id), "email": target.email, "display_name": "Target",
        "role": "user", "is_active": True,
        "created_at": target.created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "last_login_at": None,
    }
    assert "token" not in listing.text and "hash" not in listing.text
    role = client.patch(f"/admin/users/{target.id}/role", json={"role": "admin"})
    assert role.status_code == 200
    assert service.change_user_role.call_args.kwargs == {
        "actor": holder["actor"], "target_user_id": target.id, "new_role": UserRole.ADMIN,
    }
    active = client.patch(f"/admin/users/{target.id}/active", json={"is_active": False})
    assert active.status_code == 200
    assert service.set_user_active.call_args.kwargs == {
        "actor": holder["actor"], "target_user_id": target.id, "is_active": False,
    }


def test_invalid_or_forged_body_rejected(admin_api):
    client, _, service, _, target = admin_api
    url = f"/admin/users/{target.id}"
    assert client.patch(f"{url}/role", json={"role": "superuser"}).status_code == 422
    assert client.patch(f"{url}/role", json={"role": "admin", "actor_user_id": str(uuid4())}).status_code == 422
    assert client.patch(f"{url}/active", json={"is_active": "false"}).status_code == 422
    assert client.patch(f"{url}/active", json={"is_active": False, "actor_role": "admin"}).status_code == 422
    service.change_user_role.assert_not_called()
    service.set_user_active.assert_not_called()
