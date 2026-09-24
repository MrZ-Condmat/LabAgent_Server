"""Streamlit auth guard tests without launching a Streamlit server."""

import ast
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from lab_agent.auth.models import CurrentUser
from lab_agent.db.models import UserRole
from lab_agent.web import auth as web_auth


class StopCalled(Exception):
    pass


def fake_st(cookies=None):
    return SimpleNamespace(
        context=SimpleNamespace(cookies=cookies or {}),
        title=Mock(), write=Mock(), warning=Mock(), error=Mock(),
        markdown=Mock(), caption=Mock(), stop=Mock(side_effect=StopCalled),
        sidebar=nullcontext(),
    )


def config():
    return SimpleNamespace(auth_cookie_name="labagent_session",
                           auth_gateway_public_url="http://localhost:8000")


def current_user():
    return CurrentUser(id=uuid4(), external_subject=None, tenant_id=None,
                       external_object_id=None, email="user@mail.tsinghua.edu.cn",
                       display_name="User", role=UserRole.USER, is_active=True)


def test_missing_cookie_stops_without_database_access(monkeypatch):
    st = fake_st()
    lookup = Mock(side_effect=AssertionError("database must not be accessed"))
    monkeypatch.setattr(web_auth, "validate_browser_session", lookup)
    with pytest.raises(StopCalled):
        web_auth.require_current_user(config=config(), st_module=st)
    lookup.assert_not_called()
    assert "/auth/login" in st.markdown.call_args.args[0]
    assert 'target="_self"' in st.markdown.call_args.args[0]


def test_valid_cookie_uses_persistent_session_service_each_time(monkeypatch):
    st = fake_st({"labagent_session": "fake-cookie-token"})
    user = current_user()
    lookup = Mock(return_value=user)
    monkeypatch.setattr(web_auth, "validate_browser_session", lookup)
    assert web_auth.require_current_user(config=config(), st_module=st) is user
    assert web_auth.require_current_user(config=config(), st_module=st) is user
    assert lookup.call_count == 2
    assert lookup.call_args.args == ("fake-cookie-token",)
    st.stop.assert_not_called()


def test_invalid_cookie_stops_with_generic_message(monkeypatch):
    st = fake_st({"labagent_session": "fake-invalid-token"})
    monkeypatch.setattr(web_auth, "validate_browser_session", Mock(return_value=None))
    with pytest.raises(StopCalled):
        web_auth.require_current_user(config=config(), st_module=st)
    st.warning.assert_called_once()
    assert "expired or is no longer valid" in st.warning.call_args.args[0]
    assert "fake-invalid-token" not in st.markdown.call_args.args[0]


def test_backend_error_is_not_treated_as_anonymous(monkeypatch):
    st = fake_st({"labagent_session": "fake-cookie-token"})
    monkeypatch.setattr(web_auth, "validate_browser_session", Mock(side_effect=RuntimeError("private DB failure")))
    with pytest.raises(StopCalled):
        web_auth.require_current_user(config=config(), st_module=st)
    st.error.assert_called_once_with("Authentication service is temporarily unavailable.")
    st.warning.assert_not_called()


def test_identity_display_and_logout_link_have_no_token():
    st = fake_st()
    web_auth.render_authenticated_identity(current_user(), config=config(), st_module=st)
    assert "user@mail.tsinghua.edu.cn" in st.caption.call_args.args[0]
    assert "/auth/logout-page" in st.markdown.call_args.args[0]
    assert 'target="_self"' in st.markdown.call_args.args[0]


def test_app_guard_precedes_agent_and_page_rendering():
    app_path = Path(__file__).parents[1] / "lab_agent" / "web" / "app.py"
    module = ast.parse(app_path.read_text(encoding="utf-8-sig"))
    main = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    source = [ast.unparse(node) for node in main.body]
    page_config = next(i for i, line in enumerate(source) if "st.set_page_config(" in line)
    guard = next(i for i, line in enumerate(source) if "require_current_user(" in line)
    styles = next(i for i, line in enumerate(source) if "apply_overview_styles(" in line)
    agents = next(i for i, line in enumerate(source) if "LabAgent()" in line)
    navigation = next(i for i, line in enumerate(source) if "render_sidebar_navigation(" in line)
    assert page_config < guard < styles < agents < navigation


def test_streamlit_app_test_stops_before_existing_pages_without_cookie():
    from streamlit.testing.v1 import AppTest

    app_path = Path(__file__).parents[1] / "lab_agent" / "web" / "app.py"
    app = AppTest.from_file(str(app_path)).run(timeout=20)
    assert [title.value for title in app.title] == ["LabAgent"]
    assert len(app.exception) == 0
    assert not any("ArXiv Daily" in item.value for item in app.markdown)
