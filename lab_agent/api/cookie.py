"""Host-only HttpOnly session cookie lifecycle."""

import math
import re
from datetime import datetime, timezone

from starlette.responses import Response

from lab_agent.utils.config import Config


_COOKIE_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")


def cookie_name(config: Config) -> str:
    name = config.auth_cookie_name
    if not isinstance(name, str) or not _COOKIE_NAME.fullmatch(name):
        raise ValueError("Invalid AUTH_COOKIE_NAME")
    return name


def set_session_cookie(response: Response, *, token: str, expires_at: datetime, config: Config) -> None:
    if expires_at.tzinfo is None:
        raise ValueError("Session expiry must be timezone-aware")
    seconds = (expires_at - datetime.now(timezone.utc)).total_seconds()
    if seconds <= 0:
        raise ValueError("Session has already expired")
    response.set_cookie(
        key=cookie_name(config), value=token, max_age=max(1, math.ceil(seconds)),
        expires=expires_at, path="/", secure=config.auth_cookie_secure,
        httponly=True, samesite="lax",
    )


def clear_session_cookie(response: Response, *, config: Config) -> None:
    response.delete_cookie(
        key=cookie_name(config), path="/", secure=config.auth_cookie_secure,
        httponly=True, samesite="lax",
    )
