"""Local, dependency-free Media House revenue-engine milestone.

The SQLite-backed store supports researched prospects and human-reviewed
outreach drafts. The separate discovery adapter can query Overpass only when
explicitly invoked; there is no background sender or outbound messaging client.
"""

from .errors import (
    ApprovalError,
    DuplicateError,
    MediaHouseError,
    NotFoundError,
    ValidationError,
)
from .scoring import FitScore, SCORING_DISCLAIMER, calculate_fit_score
from .store import CSV_COLUMNS, ImportSummary, MediaHouseStore

__all__ = [
    "ApprovalError",
    "CSV_COLUMNS",
    "DuplicateError",
    "FitScore",
    "ImportSummary",
    "MediaHouseError",
    "MediaHouseStore",
    "NotFoundError",
    "SCORING_DISCLAIMER",
    "ValidationError",
    "calculate_fit_score",
]
