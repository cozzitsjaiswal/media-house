"""Conservative normalization helpers for identity and status fields."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

from .errors import ValidationError

UNKNOWN_VALUES = frozenset(
    {
        "",
        "unknown",
        "not known",
        "not_known",
        "not provided",
        "not_provided",
        "not available",
        "not_available",
        "unavailable",
        "n/a",
        "na",
        "none",
    }
)


def canonical_text(value: str | None) -> str:
    """Trim text without inventing a replacement for an unknown value."""

    return "" if value is None else " ".join(value.strip().split())


def normalize_token(value: str | None) -> str:
    """Normalize a status-like token to lower snake_case."""

    text = canonical_text(value).casefold()
    text = re.sub(r"[\s\-/]+", "_", text)
    return text.strip("_")


def is_unknown(value: str | None) -> bool:
    """Whether a value is an explicit, non-fabricated unknown marker."""

    return canonical_text(value).casefold() in UNKNOWN_VALUES


def normalize_name(value: str) -> str:
    """Normalize a company name for fallback identity matching."""

    text = unicodedata.normalize("NFKC", value).casefold()
    # Keep letters/digits from non-Latin scripts while removing punctuation.
    text = "".join(character if character.isalnum() else " " for character in text)
    return " ".join(text.split())


def normalize_region(value: str) -> str:
    """Normalize a region for the name-plus-region fallback key."""

    return normalize_name(value)


def normalize_domain(value: str | None) -> str | None:
    """Return a normalized hostname, or ``None`` for an explicit unknown.

    Website values may be entered as ``example.com`` or as a URL.  The path,
    scheme, port and a leading ``www.`` are not part of the dedupe key.  A
    supplied non-empty malformed website is rejected rather than silently
    becoming an unrelated company.
    """

    text = canonical_text(value)
    if is_unknown(text):
        return None
    if not text:
        return None

    parse_value = text if "://" in text else f"//{text}"
    parsed = urlparse(parse_value)
    hostname = parsed.hostname
    if not hostname or any(character.isspace() for character in text):
        raise ValidationError("website must contain a valid domain when supplied")
    try:
        hostname = hostname.encode("idna").decode("ascii").casefold().rstrip(".")
    except UnicodeError as exc:
        raise ValidationError("website domain could not be normalized") from exc

    if hostname.startswith("www."):
        hostname = hostname[4:]
    if not hostname:
        raise ValidationError("website must contain a valid domain when supplied")
    return hostname


def validate_source_url(value: str) -> str:
    """Validate a public HTTP(S) source URL without making a network call."""

    text = canonical_text(value)
    parsed = urlparse(text)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        raise ValidationError("source_url must be an absolute http(s) URL")
    if any(character.isspace() for character in text):
        raise ValidationError("source_url must not contain whitespace")
    return text
