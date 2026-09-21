"""Small repository-specific error types."""


class OwnedResourceNotFoundError(LookupError):
    """A requested private resource was not visible to the supplied owner."""
