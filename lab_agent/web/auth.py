"""Streamlit authentication gate backed by persistent PostgreSQL sessions."""

import logging
from html import escape
from urllib.parse import urlsplit

import streamlit as st

from lab_agent.auth.models import CurrentUser
from lab_agent.auth.sessions import SessionService
from lab_agent.db.session import SessionFactory, database_session
from lab_agent.repositories.user_sessions import UserSessionRepository
from lab_agent.repositories.users import UserRepository
from lab_agent.utils.config import Config


logger = logging.getLogger(__name__)


def auth_gateway_link(config: Config, path: str) -> str:
    """Construct a fixed auth route from a trusted HTTP(S) origin."""
    parsed = urlsplit(config.auth_gateway_public_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or not path.startswith("/auth/")
    ):
        raise ValueError("Invalid LABAGENT_AUTH_GATEWAY_PUBLIC_URL")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def validate_browser_session(
    token: str, *, config: Config, session_factory: SessionFactory | None = None
) -> CurrentUser | None:
    """Validate on every rerun; SessionService also touches last_seen_at."""
    with database_session(session_factory) as session:
        users = UserRepository(session)
        service = SessionService(UserSessionRepository(session), users, config)
        return service.validate_session(token)


def _same_tab_link(label: str, url: str, *, st_module=st) -> None:
    st_module.markdown(
        f'<a href="{escape(url, quote=True)}" target="_self">{escape(label)}</a>',
        unsafe_allow_html=True,
    )


def require_current_user(
    *, config: Config, st_module=st, session_factory: SessionFactory | None = None
) -> CurrentUser:
    """Stop before existing pages or agents run unless the cookie is valid."""
    try:
        token = st_module.context.cookies.get(config.auth_cookie_name)
        login_url = auth_gateway_link(config, "/auth/login")
    except Exception:
        logger.exception("Authentication context is unavailable")
        st_module.error("Authentication service is temporarily unavailable.")
        st_module.stop()

    if not isinstance(token, str) or not token:
        st_module.title("LabAgent")
        st_module.write("Sign in with your school email to continue.")
        _same_tab_link("Sign in", login_url, st_module=st_module)
        st_module.stop()

    try:
        current_user = validate_browser_session(token, config=config, session_factory=session_factory)
    except Exception:
        logger.exception("Authentication validation failed")
        st_module.error("Authentication service is temporarily unavailable.")
        st_module.stop()

    if current_user is None:
        st_module.title("LabAgent")
        st_module.warning("Your session has expired or is no longer valid.")
        _same_tab_link("Sign in again", login_url, st_module=st_module)
        st_module.stop()

    return current_user


def render_authenticated_identity(current_user: CurrentUser, *, config: Config, st_module=st) -> None:
    display_name = current_user.display_name or current_user.email.split("@", 1)[0]
    logout_url = auth_gateway_link(config, "/auth/logout-page")
    with st_module.sidebar:
        st_module.caption(f"{display_name} · {current_user.email} · {current_user.role.value}")
        _same_tab_link("Logout", logout_url, st_module=st_module)
