"""Streamlit administrator page guard and navigation wiring checks."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from lab_agent.auth.models import CurrentUser
from lab_agent.db.models import UserRole
from lab_agent.web import admin as web_admin


class StopCalled(Exception):
    pass


def identity(role: UserRole) -> CurrentUser:
    return CurrentUser(
        id=uuid4(), external_subject=None, tenant_id=None, external_object_id=None,
        email="user@mail.tsinghua.edu.cn", display_name="User", role=role,
        is_active=True,
    )


def test_admin_page_rejects_user_before_database_access(monkeypatch):
    fake_st = SimpleNamespace(error=Mock(), stop=Mock(side_effect=StopCalled))
    db_access = Mock(side_effect=AssertionError("non-admin must not query users"))
    monkeypatch.setattr(web_admin, "st", fake_st)
    monkeypatch.setattr(web_admin, "database_session", db_access)
    with pytest.raises(StopCalled):
        web_admin.admin_interface(identity(UserRole.USER), Mock())
    fake_st.error.assert_called_once_with("Administrator access required.")
    db_access.assert_not_called()


def test_navigation_filters_admin_and_page_has_independent_guard():
    path = Path(__file__).parents[1] / "lab_agent" / "web" / "app.py"
    module = ast.parse(path.read_text(encoding="utf-8-sig"))
    nav = next(node for node in module.body if isinstance(node, ast.FunctionDef)
               and node.name == "render_sidebar_navigation")
    main = next(node for node in module.body if isinstance(node, ast.FunctionDef)
                and node.name == "main")
    nav_source = ast.unparse(nav)
    main_source = ast.unparse(main)
    assert "is_admin(current_user)" in nav_source
    assert "if current not in visible_items" in nav_source
    assert "admin_interface(current_user, render_page_header)" in main_source
    page_source = ast.unparse(ast.parse(
        (Path(__file__).parents[1] / "lab_agent" / "web" / "admin.py").read_text(encoding="utf-8-sig")
    ))
    assert "require_admin(current_user)" in page_source
