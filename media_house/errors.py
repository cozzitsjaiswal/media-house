"""Errors raised by the local Media House store."""


class MediaHouseError(Exception):
    """Base class for expected application errors."""


class ValidationError(MediaHouseError):
    """Input failed validation before it could be written."""

    def __init__(self, message: str, *, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or [message]


class NotFoundError(MediaHouseError):
    """A requested local record does not exist."""


class DuplicateError(MediaHouseError):
    """A record cannot be created because it already exists."""


class ApprovalError(MediaHouseError):
    """A draft cannot pass the human approval gate."""
