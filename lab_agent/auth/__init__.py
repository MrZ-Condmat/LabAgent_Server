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

__all__ = [
    "AuthenticationError",
    "AuthenticationService",
    "CurrentUser",
    "IdentityClaims",
    "IdentityConflictError",
    "InvalidIdentityError",
    "UnknownUserError",
    "UserInactiveError",
]
