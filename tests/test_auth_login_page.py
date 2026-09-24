"""Browser pages call same-origin APIs without handling session tokens."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from lab_agent.api.app import create_app
from lab_agent.api.dependencies import get_auth_config


def client_with_target(target="http://localhost:8501"):
    app = create_app()
    app.dependency_overrides[get_auth_config] = lambda: SimpleNamespace(streamlit_public_url=target)
    return TestClient(app)


def test_login_page_contains_minimal_same_origin_otp_flow():
    with client_with_target() as client:
        response = client.get("/auth/login")
    html = response.text
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert 'type="email"' in html and 'id="code"' in html
    assert 'maxlength="6"' in html and 'autocomplete="one-time-code"' in html
    assert 'fetch("/auth/request-code"' in html and 'fetch("/auth/verify-code"' in html
    assert "window.location.replace(streamlitUrl)" in html
    assert "let challengeId = null" in html and "textContent" in html
    assert "localStorage" not in html and "sessionStorage" not in html
    assert "document.cookie" not in html and "innerHTML" not in html
    assert "https://" not in html and "<script src=" not in html
    assert "session_token" not in html and "SMTP_PASSWORD" not in html
    assert "set-cookie" not in response.headers


def test_login_ignores_untrusted_next_destination():
    with client_with_target("https://labagent.example.edu") as client:
        response = client.get("/auth/login?next=https://evil.example")
    assert response.status_code == 200
    assert "https://labagent.example.edu" in response.text
    assert "evil.example" not in response.text


def test_logout_page_only_posts_to_existing_logout_route():
    with client_with_target() as client:
        response = client.get("/auth/logout-page")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert 'fetch("/auth/logout"' in response.text
    assert "window.location.replace(streamlitUrl)" in response.text
    assert "SessionService" not in response.text and "document.cookie" not in response.text
    assert "set-cookie" not in response.headers
