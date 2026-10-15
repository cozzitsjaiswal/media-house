"""Offline saved-input First Customer Finder with an isolated SQLite schema."""

from .core import (
    CLASSIFICATIONS,
    FIT_REASON,
    FORMAT,
    SCHEMA_VERSION,
    classify_file,
    classify_saved_input,
    import_file,
    import_saved_input,
)
from .errors import FinderError

__all__ = [
    "CLASSIFICATIONS",
    "FIT_REASON",
    "FORMAT",
    "SCHEMA_VERSION",
    "FinderError",
    "classify_file",
    "classify_saved_input",
    "import_file",
    "import_saved_input",
]
