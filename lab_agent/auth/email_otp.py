"""Email OTP cryptography, policy, and database-backed challenge flow."""

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from lab_agent.db.models import EmailLoginChallenge
from lab_agent.db.models.user import utc_now
from lab_agent.repositories.email_login_challenges import EmailLoginChallengeRepository
from lab_agent.utils.config import Config


class AuthConfigurationError(RuntimeError):
    pass


class OtpError(Exception):
    pass


class EmailDomainNotAllowedError(OtpError):
    pass


class OtpRateLimitError(OtpError):
    pass


class OtpInvalidError(OtpError):
    pass


@dataclass(frozen=True, slots=True)
class IssuedOtpChallenge:
    challenge_id: uuid.UUID
    email: str
    code: str
    expires_at: datetime

    def __repr__(self) -> str:
        return f"IssuedOtpChallenge(challenge_id={self.challenge_id!r}, email={self.email!r}, code=<redacted>, expires_at={self.expires_at!r})"


@dataclass(frozen=True, slots=True)
class OtpVerificationResult:
    verified_email: str | None
    # A failed result must be committed by the caller to preserve failed_attempts.
    @property
    def succeeded(self) -> bool:
        return self.verified_email is not None


class EmailDomainPolicy:
    def __init__(self, domains: str):
        self.domains = frozenset(part.strip().lower() for part in domains.split(",") if part.strip())
        if not self.domains:
            raise AuthConfigurationError("Allowed email domains are not configured")

    def normalize(self, email: str) -> str:
        if not isinstance(email, str):
            raise EmailDomainNotAllowedError("Email is not allowed")
        normalized = email.strip().lower()
        if len(normalized) > 320 or normalized.count("@") != 1 or any(c.isspace() for c in normalized):
            raise EmailDomainNotAllowedError("Email is not allowed")
        local, domain = normalized.split("@", 1)
        if not local or domain not in self.domains:
            raise EmailDomainNotAllowedError("Email is not allowed")
        return normalized


class OtpCrypto:
    def __init__(self, secret: str | None):
        self.secret = secret

    def _key(self) -> bytes:
        if not self.secret or not self.secret.strip():
            raise AuthConfigurationError("AUTH_OTP_HMAC_SECRET is not configured")
        return self.secret.encode("utf-8")

    @staticmethod
    def generate_code() -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

    def digest(self, challenge_id: uuid.UUID, code: str) -> str:
        return hmac.new(self._key(), f"otp:{challenge_id}:{code}".encode(), hashlib.sha256).hexdigest()

    def matches(self, challenge_id: uuid.UUID, code: str, digest: str) -> bool:
        candidate = self.digest(challenge_id, code)
        return hmac.compare_digest(candidate, digest)


class EmailOtpService:
    def __init__(self, repository: EmailLoginChallengeRepository, config: Config | None = None):
        self.repository = repository
        self.config = config if config is not None else Config()
        self.policy = EmailDomainPolicy(self.config.allowed_email_domains)
        self.crypto = OtpCrypto(self.config.auth_otp_hmac_secret)
        for value in (self.config.auth_otp_ttl_seconds, self.config.auth_otp_max_attempts,
                      self.config.auth_otp_resend_cooldown_seconds, self.config.auth_otp_max_requests_per_window,
                      self.config.auth_otp_request_window_seconds):
            if value <= 0:
                raise AuthConfigurationError("OTP limits must be positive")

    def issue_challenge(self, email: str) -> IssuedOtpChallenge:
        normalized = self.policy.normalize(email)
        self.crypto._key()  # Fail before any database mutation.
        now = utc_now()
        self.repository.lock_email(normalized)
        recent = self.repository.list_recent_for_email(
            normalized, now - timedelta(seconds=self.config.auth_otp_request_window_seconds)
        )
        if recent and recent[0].created_at > now - timedelta(seconds=self.config.auth_otp_resend_cooldown_seconds):
            raise OtpRateLimitError("OTP request limit reached")
        if len(recent) >= self.config.auth_otp_max_requests_per_window:
            raise OtpRateLimitError("OTP request limit reached")
        self.repository.invalidate_active_for_email(normalized, now)
        challenge_id = uuid.uuid4()
        code = self.crypto.generate_code()
        expires_at = now + timedelta(seconds=self.config.auth_otp_ttl_seconds)
        challenge = EmailLoginChallenge(id=challenge_id, email=normalized, code_hash=self.crypto.digest(challenge_id, code),
                                        created_at=now, expires_at=expires_at)
        self.repository.create(challenge)
        return IssuedOtpChallenge(challenge_id, normalized, code, expires_at)

    def verify_challenge(self, challenge_id: uuid.UUID, email: str, code: str) -> OtpVerificationResult:
        normalized = self.policy.normalize(email)
        self.crypto._key()
        challenge = self.repository.get_for_update(challenge_id)
        now = utc_now()
        if challenge is None or challenge.email != normalized or challenge.invalidated_at is not None or challenge.consumed_at is not None:
            raise OtpInvalidError("OTP unavailable")
        if challenge.expires_at <= now or challenge.failed_attempts >= self.config.auth_otp_max_attempts:
            raise OtpInvalidError("OTP unavailable")
        if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdecimal() or not self.crypto.matches(challenge.id, code, challenge.code_hash):
            self.repository.record_failed_attempt(challenge)
            return OtpVerificationResult(None)
        self.repository.consume(challenge, now)
        return OtpVerificationResult(normalized)
