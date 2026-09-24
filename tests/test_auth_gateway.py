"""FastAPI TestClient checks with in-memory service doubles only."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from lab_agent.api import auth_routes
from lab_agent.api.app import create_app
from lab_agent.api.dependencies import get_auth_config, get_db_session_factory, get_email_sender
from lab_agent.auth.email_otp import OtpRateLimitError
from lab_agent.auth.email_otp_authentication import EmailOtpAuthenticationAttempt, EmailOtpAuthenticationResult
from lab_agent.auth.email_otp_delivery import EmailDeliveryResult
from lab_agent.auth.email_sender import EmailDeliveryError
from lab_agent.auth.errors import UserInactiveError
from lab_agent.auth.models import CurrentUser
from lab_agent.db.models import UserRole
from lab_agent.db.models.user import utc_now


EMAIL = "user@mail.tsinghua.edu.cn"


@pytest.fixture
def gateway():
    app = create_app()
    config = SimpleNamespace(auth_cookie_name="labagent_session", auth_cookie_secure=False)
    db = Mock()
    app.dependency_overrides[get_auth_config] = lambda: config
    app.dependency_overrides[get_db_session_factory] = lambda: lambda: db
    app.dependency_overrides[get_email_sender] = lambda: Mock()
    with TestClient(app) as client:
        yield client, db


def test_health_has_no_database_or_smtp_dependency():
    with TestClient(create_app()) as client:
        assert client.get("/healthz").json() == {"status": "ok"}


def test_request_code_returns_only_safe_metadata(gateway, monkeypatch):
    client, db = gateway
    delivery = Mock()
    result = EmailDeliveryResult(uuid4(), utc_now() + timedelta(minutes=5))
    delivery.request_verification_email.return_value = result
    monkeypatch.setattr(auth_routes, "build_delivery_service", lambda session, sender, config: delivery)
    response = client.post("/auth/request-code", json={"email": EMAIL})
    assert response.status_code == 202
    assert response.json()["challenge_id"] == str(result.challenge_id)
    assert response.json()["status"] == "verification_code_sent"
    assert "code" not in response.json() and "set-cookie" not in response.headers
    delivery.request_verification_email.assert_called_once_with(EMAIL)
    db.commit.assert_called_once()


@pytest.mark.parametrize("error,status", [(OtpRateLimitError("limited"), 429),
                                           (EmailDeliveryError("private SMTP detail"), 503)])
def test_request_code_errors_are_stable_and_rollback(gateway, monkeypatch, error, status):
    client, db = gateway
    delivery = Mock()
    delivery.request_verification_email.side_effect = error
    monkeypatch.setattr(auth_routes, "build_delivery_service", lambda session, sender, config: delivery)
    response = client.post("/auth/request-code", json={"email": EMAIL})
    assert response.status_code == status
    assert "private SMTP detail" not in response.text
    assert "set-cookie" not in response.headers
    db.rollback.assert_called_once()


def auth_result():
    user = CurrentUser(id=uuid4(), external_subject="private-sub", tenant_id=None,
                       external_object_id=None, email=EMAIL, display_name="User",
                       role=UserRole.USER, is_active=True)
    result = EmailOtpAuthenticationResult(user, uuid4(), "fake-opaque-token", utc_now() + timedelta(minutes=17))
    return user, result


def test_verify_sets_cookie_without_token_or_internal_identity_in_json(gateway, monkeypatch):
    client, db = gateway
    user, result = auth_result()
    service = Mock()
    service.authenticate.return_value = EmailOtpAuthenticationAttempt(result)
    monkeypatch.setattr(auth_routes, "build_authentication_service", lambda session, config: service)
    response = client.post("/auth/verify-code", json={"email": EMAIL, "challenge_id": str(uuid4()), "code": "004921"})
    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert response.json()["user"] == {"id": str(user.id), "email": EMAIL, "display_name": "User", "role": "user"}
    assert "fake-opaque-token" not in response.text
    assert "private-sub" not in response.text and "token_hash" not in response.text
    header = response.headers["set-cookie"].lower()
    assert "labagent_session=" in header and "httponly" in header
    assert "samesite=lax" in header and "path=/" in header and "secure" not in header
    assert "max-age=" in header and "expires=" in header
    db.commit.assert_called_once()


def test_wrong_code_commits_before_http_401(gateway, monkeypatch):
    client, db = gateway
    service = Mock()
    service.authenticate.return_value = EmailOtpAuthenticationAttempt(None)
    monkeypatch.setattr(auth_routes, "build_authentication_service", lambda session, config: service)
    response = client.post("/auth/verify-code", json={"email": EMAIL, "challenge_id": str(uuid4()), "code": "000000"})
    assert response.status_code == 401 and response.json()["detail"] == "Invalid or expired verification code."
    assert "set-cookie" not in response.headers
    db.commit.assert_called_once()
    db.rollback.assert_not_called()


def test_inactive_user_rejected_without_cookie(gateway, monkeypatch):
    client, db = gateway
    service = Mock()
    service.authenticate.side_effect = UserInactiveError("internal account status")
    monkeypatch.setattr(auth_routes, "build_authentication_service", lambda session, config: service)
    response = client.post("/auth/verify-code", json={"email": EMAIL, "challenge_id": str(uuid4()), "code": "123456"})
    assert response.status_code == 403 and "set-cookie" not in response.headers
    assert "internal account status" not in response.text
    db.rollback.assert_called_once()


def test_me_and_logout_use_existing_session_service(gateway, monkeypatch):
    client, db = gateway
    user, _ = auth_result()
    service = Mock()
    service.validate_session.return_value = user
    monkeypatch.setattr(auth_routes, "build_session_service", lambda session, config: service)
    assert client.get("/auth/me").status_code == 401
    client.cookies.set("labagent_session", "fake-opaque-token")
    me = client.get("/auth/me")
    assert me.status_code == 200 and me.json()["user"]["id"] == str(user.id)
    assert "fake-opaque-token" not in me.text
    service.validate_session.assert_called_once_with("fake-opaque-token")
    logout = client.post("/auth/logout")
    assert logout.status_code == 204 and "max-age=0" in logout.headers["set-cookie"].lower()
    service.revoke_session.assert_called_once_with("fake-opaque-token")
    service.validate_session.return_value = None
    assert client.post("/auth/logout").status_code == 204
    assert client.get("/auth/me").status_code == 401


def test_invalid_cookie_is_cleared(gateway, monkeypatch):
    client, _ = gateway
    service = Mock()
    service.validate_session.return_value = None
    monkeypatch.setattr(auth_routes, "build_session_service", lambda session, config: service)
    client.cookies.set("labagent_session", "tampered")
    response = client.get("/auth/me")
    assert response.status_code == 401 and "max-age=0" in response.headers["set-cookie"].lower()
