class MuninError(Exception):
    """Base exception for all munin errors."""


class MuninConfigError(MuninError):
    """Raised when configuration is invalid or cannot be loaded."""


class MuninEmbedError(MuninError):
    """Raised when the embedding server is unreachable or returns an error."""


class MuninDBError(MuninError):
    """Raised when a database operation fails."""
