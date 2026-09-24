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
from .email_otp_authentication import (
    EmailOtpAuthenticationAttempt,
    EmailOtpAuthenticationResult,
    EmailOtpAuthenticationService,
)
from .authorization import AdminAuthorizationError, is_admin, require_admin
from .admin_users import (
    AdminBootstrapError, AdminSafetyError, AdminUserNotFoundError,
    AdminUserService, AdminUserSummary, BootstrapAdminService,
)

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
    "EmailOtpAuthenticationAttempt",
    "EmailOtpAuthenticationResult",
    "EmailOtpAuthenticationService",
    "AdminAuthorizationError",
    "is_admin",
    "require_admin",
    "AdminBootstrapError",
    "AdminSafetyError",
    "AdminUserNotFoundError",
    "AdminUserService",
    "AdminUserSummary",
    "BootstrapAdminService",
]
