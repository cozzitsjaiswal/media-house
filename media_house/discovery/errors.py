"""Expected configuration, source, and saved-discovery failures."""

from ..errors import MediaHouseError


class DiscoveryError(MediaHouseError):
    """A discovery failure, with machine-readable recovery information."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        status: int | None = None,
        retry_after: str | None = None,
    ) -> None:
        """Store the message plus machine-readable recovery metadata."""

        super().__init__(message)
        self.code = code
        self.status = status
        self.retry_after = retry_after
