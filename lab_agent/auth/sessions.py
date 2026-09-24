"""Opaque session token issuance, lookup, and revocation."""

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from lab_agent.db.models import UserSession
from lab_agent.db.models.user import utc_now
from lab_agent.repositories import UserRepository, UserSessionRepository
from lab_agent.utils.config import Config
from .email_otp import AuthConfigurationError
from .models import CurrentUser


@dataclass(frozen=True, slots=True)
class IssuedSession:
    session_id: uuid.UUID
    raw_token: str
    expires_at: datetime

    def __repr__(self) -> str:
        return f"IssuedSession(session_id={self.session_id!r}, raw_token=<redacted>, expires_at={self.expires_at!r})"


class SessionTokenCrypto:
    def __init__(self, secret: str | None):
        self.secret = secret

    def digest(self, raw_token: str) -> str:
        if not self.secret or not self.secret.strip():
            raise AuthConfigurationError("AUTH_SESSION_HMAC_SECRET is not configured")
        return hmac.new(self.secret.encode(), f"session:{raw_token}".encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def generate_token() -> str:
        return secrets.token_urlsafe(32)


class SessionService:
    def __init__(self, sessions: UserSessionRepository, users: UserRepository, config: Config | None = None):
        self.sessions = sessions
        self.users = users
        self.config = config if config is not None else Config()
        self.crypto = SessionTokenCrypto(self.config.auth_session_hmac_secret)
        if self.config.auth_session_ttl_days <= 0:
            raise AuthConfigurationError("Session lifetime must be positive")

    def create_session(self, user_id: uuid.UUID) -> IssuedSession:
        self.crypto.digest("")  # Fail closed before persistence.
        user = self.users.get_by_id(user_id)
        if user is None or not user.is_active:
            raise ValueError("User unavailable")
        raw_token = self.crypto.generate_token()
        now = utc_now()
        expires_at = now + timedelta(days=self.config.auth_session_ttl_days)
        record = UserSession(user_id=user_id, token_hash=self.crypto.digest(raw_token),
                             created_at=now, expires_at=expires_at, last_seen_at=now)
        self.sessions.create(record)
        return IssuedSession(record.id, raw_token, expires_at)

    def validate_session(self, raw_token: str) -> CurrentUser | None:
        if not isinstance(raw_token, str) or not raw_token:
            return None
        digest = self.crypto.digest(raw_token)
        now = utc_now()
        record = self.sessions.get_active_by_token_hash(digest, now)
        if record is None:
            return None
        user = self.users.get_by_id(record.user_id)
        if user is None or not user.is_active:
            return None
        self.sessions.touch(record, now)
        return CurrentUser.from_user(user)

    def revoke_session(self, raw_token: str) -> bool:
        if not isinstance(raw_token, str) or not raw_token:
            return False
        now = utc_now()
        record = self.sessions.get_active_by_token_hash(self.crypto.digest(raw_token), now)
        if record is None:
            return False
        self.sessions.revoke(record, now)
        return True

    def revoke_all_for_user(self, user_id: uuid.UUID) -> None:
        self.crypto.digest("")
        self.sessions.revoke_all_for_user(user_id, utc_now())
