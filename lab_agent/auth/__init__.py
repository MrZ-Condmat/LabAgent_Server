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
from .email_sender import EmailSender, EmailDeliveryError, EmailAuthenticationError, EmailSenderConfigurationError
from .smtp_sender import SmtpEmailSender
from .email_otp_delivery import EmailOtpDeliveryService, EmailDeliveryResult

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
    "EmailSender",
    "EmailDeliveryError",
    "EmailAuthenticationError",
    "EmailSenderConfigurationError",
    "SmtpEmailSender",
    "EmailOtpDeliveryService",
    "EmailDeliveryResult",
]
