"""Provider-neutral authentication abstractions."""

from .errors import (
    AuthenticationError,
    IdentityConflictError,
    InvalidIdentityError,
    UnknownUserError,
    UserInactiveError,
)
from .models import CurrentUser, IdentityClaims
from .service import AuthenticationService
from .email_otp import EmailDomainPolicy, EmailOtpService, IssuedOtpChallenge, OtpVerificationResult
from .sessions import IssuedSession, SessionService

__all__ = [
    "AuthenticationError",
    "AuthenticationService",
    "CurrentUser",
    "IdentityClaims",
    "IdentityConflictError",
    "InvalidIdentityError",
    "UnknownUserError",
    "UserInactiveError",
    "EmailDomainPolicy",
    "EmailOtpService",
    "IssuedOtpChallenge",
    "OtpVerificationResult",
    "IssuedSession",
    "SessionService",
]
