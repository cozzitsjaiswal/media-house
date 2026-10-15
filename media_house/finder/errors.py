"""Errors for the isolated first-customer finder.

The synthetic-only filesystem guard is a scope boundary, not identity or
authentication protection. A future production persistence path remains
unapproved until those controls are designed separately.
"""

from __future__ import annotations

from typing import Any

from ..errors import MediaHouseError


class FinderError(MediaHouseError):
    """A saved-input or synthetic-database operation failed closed.

    The attached report, when present, is a local processing receipt rather
    than proof of source truth, operator identity, or authorization.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "finder_error",
        report: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.report = report
