from datetime import timedelta
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from types import SimpleNamespace

import pytest
from starlette.responses import Response

from lab_agent.api.cookie import clear_session_cookie, set_session_cookie
from lab_agent.db.models.user import utc_now


def config(*, secure=True):
    return SimpleNamespace(auth_cookie_name="labagent_session", auth_cookie_secure=secure)


@pytest.mark.parametrize("secure", [True, False])
def test_session_cookie_security_and_actual_expiration(secure):
    response = Response()
    expiry = utc_now() + timedelta(minutes=17)
    set_session_cookie(response, token="fake-opaque-token", expires_at=expiry, config=config(secure=secure))
    header = response.headers["set-cookie"]
    cookie = SimpleCookie()
    cookie.load(header)
    stored = cookie["labagent_session"]
    assert stored.value == "fake-opaque-token"
    assert stored["httponly"] and stored["samesite"].lower() == "lax"
    assert stored["path"] == "/" and stored["domain"] == ""
    assert bool(stored["secure"]) is secure
    assert 16 * 60 <= int(stored["max-age"]) <= 17 * 60
    assert abs((parsedate_to_datetime(stored["expires"]) - expiry).total_seconds()) < 2


def test_clearing_cookie_uses_same_name_and_path():
    response = Response()
    clear_session_cookie(response, config=config())
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    stored = cookie["labagent_session"]
    assert stored["path"] == "/" and stored["max-age"] == "0"
    assert stored["domain"] == "" and stored["httponly"]
