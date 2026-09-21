"""Authentication errors without transport or UI concerns."""


class AuthenticationError(Exception):
    """Base error for resolving an application user identity."""


class InvalidIdentityError(AuthenticationError):
    """Trusted claims are missing required normalized identity fields."""


class IdentityConflictError(AuthenticationError):
    """Subject and email cannot be safely resolved to one account."""


class UnknownUserError(AuthenticationError):
    """The identity is valid but is not authorized for this installation."""


class UserInactiveError(AuthenticationError):
    """The resolved local user has been disabled."""
