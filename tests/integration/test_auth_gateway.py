"""Real PostgreSQL auth API flow with a fake SMTP transport."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from lab_agent.api.app import create_app
from lab_agent.api.dependencies import get_auth_config, get_db_session_factory, get_email_sender
from lab_agent.auth.email_sender import EmailDeliveryError
from lab_agent.db.models import EmailLoginChallenge, User, UserSession
from lab_agent.db.models.user import utc_now
from lab_agent.db.session import database_session
from lab_agent.repositories.users import UserRepository


pytestmark = pytest.mark.integration
EMAIL = "user@mail.tsinghua.edu.cn"


class RecordingFakeEmailSender:
    def __init__(self):
        self.calls = []
        self.fail = False

    def send_verification_code(self, *, recipient, code, expires_at):
        self.calls.append((recipient, code, expires_at))
        if self.fail:
            raise EmailDeliveryError("private SMTP response")


@pytest.fixture
def gateway(integration_session_factory):
    app = create_app()
    sender = RecordingFakeEmailSender()
    config = SimpleNamespace(
        allowed_email_domains="mails.tsinghua.edu.cn,mail.tsinghua.edu.cn",
        auth_otp_hmac_secret="integration-fake-otp-key",
        auth_otp_ttl_seconds=300,
        auth_otp_max_attempts=5,
        auth_otp_resend_cooldown_seconds=60,
        auth_otp_max_requests_per_window=5,
        auth_otp_request_window_seconds=600,
        auth_session_hmac_secret="integration-fake-session-key",
        auth_session_ttl_days=30,
        auth_cookie_name="labagent_session",
        auth_cookie_secure=False,
    )
    app.dependency_overrides[get_auth_config] = lambda: config
    app.dependency_overrides[get_db_session_factory] = lambda: integration_session_factory
    app.dependency_overrides[get_email_sender] = lambda: sender
    with TestClient(app) as client:
        yield client, sender, integration_session_factory


def table_count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def request_code(client):
    response = client.post("/auth/request-code", json={"email": " USER@MAIL.TSINGHUA.EDU.CN "})
    assert response.status_code == 202
    return response.json()["challenge_id"]


def test_full_request_verify_cookie_me_logout_flow(gateway):
    client, sender, factory = gateway
    challenge_id = request_code(client)
    assert len(sender.calls) == 1
    recipient, code, expires_at = sender.calls[0]
    assert recipient == EMAIL and len(code) == 6 and code.isdecimal()
    with database_session(factory) as session:
        challenge = session.get(EmailLoginChallenge, challenge_id)
        assert challenge.code_hash != code and challenge.expires_at == expires_at

    verified = client.post("/auth/verify-code", json={"email": EMAIL, "challenge_id": challenge_id, "code": code})
    assert verified.status_code == 200
    assert verified.json()["user"]["email"] == EMAIL
    assert code not in verified.text and "token_hash" not in verified.text
    assert "labagent_session=" in verified.headers["set-cookie"]
    raw_token = client.cookies.get("labagent_session")
    assert raw_token and raw_token not in verified.text
    with database_session(factory) as session:
        assert table_count(session, User) == 1 and table_count(session, UserSession) == 1
        stored = session.scalar(select(UserSession))
        assert stored.token_hash != raw_token and session.get(EmailLoginChallenge, challenge_id).consumed_at is not None

    me = client.get("/auth/me")
    assert me.status_code == 200 and me.json()["user"]["email"] == EMAIL
    assert raw_token not in me.text
    logout = client.post("/auth/logout")
    assert logout.status_code == 204 and "max-age=0" in logout.headers["set-cookie"].lower()
    with database_session(factory) as session:
        assert session.scalar(select(UserSession)).revoked_at is not None
    assert client.get("/auth/me", headers={"Cookie": f"labagent_session={raw_token}"}).status_code == 401
    assert client.post("/auth/logout").status_code == 204


def test_wrong_code_http_401_persists_attempt_without_user_or_session(gateway):
    client, sender, factory = gateway
    challenge_id = request_code(client)
    correct = sender.calls[0][1]
    wrong = "999999" if correct != "999999" else "888888"
    response = client.post("/auth/verify-code", json={"email": EMAIL, "challenge_id": challenge_id, "code": wrong})
    assert response.status_code == 401 and "set-cookie" not in response.headers
    with database_session(factory) as session:
        assert session.get(EmailLoginChallenge, challenge_id).failed_attempts == 1
        assert table_count(session, User) == 0 and table_count(session, UserSession) == 0


def test_rate_limit_and_smtp_failure_do_not_send_duplicate_or_invalidate_old(gateway):
    client, sender, factory = gateway
    challenge_id = request_code(client)
    limited = client.post("/auth/request-code", json={"email": EMAIL})
    assert limited.status_code == 429 and len(sender.calls) == 1
    with database_session(factory) as session:
        session.get(EmailLoginChallenge, challenge_id).created_at = utc_now() - timedelta(seconds=61)
    sender.fail = True
    failed = client.post("/auth/request-code", json={"email": EMAIL})
    assert failed.status_code == 503 and "private SMTP response" not in failed.text
    with database_session(factory) as session:
        assert table_count(session, EmailLoginChallenge) == 1
        assert session.get(EmailLoginChallenge, challenge_id).invalidated_at is None


def test_inactive_user_gets_403_without_session_cookie(gateway):
    client, sender, factory = gateway
    with database_session(factory) as session:
        UserRepository(session).add(User(email=EMAIL, display_name="Disabled", is_active=False))
    challenge_id = request_code(client)
    response = client.post("/auth/verify-code", json={"email": EMAIL,
                           "challenge_id": challenge_id, "code": sender.calls[0][1]})
    assert response.status_code == 403 and "set-cookie" not in response.headers
    with database_session(factory) as session:
        assert table_count(session, UserSession) == 0


def test_me_rejects_unknown_expired_revoked_and_inactive_sessions(gateway):
    client, sender, factory = gateway
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Cookie": "labagent_session=unknown"}).status_code == 401
    challenge_id = request_code(client)
    verified = client.post("/auth/verify-code", json={"email": EMAIL, "challenge_id": challenge_id,
                                                       "code": sender.calls[0][1]})
    assert verified.status_code == 200
    token = client.cookies.get("labagent_session")
    tampered = client.get("/auth/me", headers={"Cookie": f"labagent_session={token}x"})
    assert tampered.status_code == 401 and "max-age=0" in tampered.headers["set-cookie"].lower()
    with database_session(factory) as session:
        stored = session.scalar(select(UserSession))
        stored.expires_at = utc_now() - timedelta(seconds=1)
    assert client.get("/auth/me", headers={"Cookie": f"labagent_session={token}"}).status_code == 401
    with database_session(factory) as session:
        session.scalar(select(UserSession)).expires_at = utc_now() + timedelta(days=1)
        session.scalar(select(UserSession)).revoked_at = utc_now()
    assert client.get("/auth/me", headers={"Cookie": f"labagent_session={token}"}).status_code == 401
    with database_session(factory) as session:
        session.scalar(select(UserSession)).revoked_at = None
        session.scalar(select(User)).is_active = False
    assert client.get("/auth/me", headers={"Cookie": f"labagent_session={token}"}).status_code == 401
