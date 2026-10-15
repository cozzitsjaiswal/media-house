"""UTC timestamp helpers used by the local store."""

from __future__ import annotations

from datetime import date, datetime, timezone


def utc_now() -> str:
    """Return a second-precision UTC timestamp in canonical ``Z`` form."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc_timestamp(value: str, *, field_name: str) -> str:
    """Parse an ISO date/time and normalize it to UTC.

    A date-only value is interpreted as midnight UTC.  A timezone-less
    datetime is also treated as UTC because the CSV template has no timezone
    column; this assumption is intentionally documented instead of silently
    using the machine's local timezone.
    """

    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} is required")

    try:
        if len(text) == 10:
            parsed: datetime = datetime.combine(date.fromisoformat(text), datetime.min.time())
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date or datetime") from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_optional_utc_timestamp(value: str | None, *, field_name: str) -> str | None:
    """Return a normalized timestamp for a non-empty value, otherwise ``None``."""

    if value is None or not value.strip():
        return None
    return parse_utc_timestamp(value, field_name=field_name)
