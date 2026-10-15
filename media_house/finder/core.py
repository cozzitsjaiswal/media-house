"""Offline, insert-only First Customer Finder persistence.

The Finder accepts saved JSON only.  It never discovers data, sends outreach,
or opens a writable SQLite file.  Existing files are read through the
capability-checked Linux image backend; new files are installed only through
that backend's anonymous-image, no-replace path.
"""

from __future__ import annotations

import difflib
import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from ..errors import ValidationError
from ..normalization import canonical_text, is_unknown, normalize_name, normalize_region, normalize_token
from ..timeutil import parse_utc_timestamp, utc_now
from .errors import FinderError
from .linux_storage import StorageFailure, open_target as _linux_open_target

FORMAT = "media-house-first-customer-finder"
SCHEMA_VERSION = 1
FIT_REASON = "Source-derived facts only; customer need is unknown."
MAX_INPUT_BYTES = 1024 * 1024
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_RECORDS = 200
NEAR_NAME_SIMILARITY_THRESHOLD = 0.92
_NOVELTY_EVIDENCE_FIELD = "novelty_evidence"
_NOVELTY_EVIDENCE_FIELDS = frozenset(
    {
        "source_id",
        "snapshot_id",
        "completeness",
        "authoritative",
        "no_prior_match",
        "record_count",
        "records_sha256",
    }
)
_HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_REQUIRED_RECORD_FIELDS = frozenset(
    {"source_object_id", "source_url", "company_name", "region", "industry", "observed_at"}
)
_RECORD_FIELDS = _REQUIRED_RECORD_FIELDS | {"website", "retrieved_at"}
_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
_MACHINE_RESEARCHER_VALUES = frozenset(
    {"system", "automation", "automated", "ai", "agent", "ai_agent", "bot", "robot", "machine"}
)
_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)

CLASSIFICATIONS = (
    "NEW",
    "EXACT_DUPLICATE",
    "PROBABLE_DUPLICATE",
    "AMBIGUOUS_DUPLICATE",
    "CONFLICTING_IDENTITY",
    "MALFORMED",
)

_EXPECTED_TABLE_COLUMNS = {
    "finder_records": (
        ("id", "INTEGER", 0, 1),
        ("source_object_id", "TEXT", 1, 0),
        ("source_url", "TEXT", 1, 0),
        ("company_name", "TEXT", 1, 0),
        ("normalized_name", "TEXT", 1, 0),
        ("region", "TEXT", 1, 0),
        ("normalized_region", "TEXT", 1, 0),
        ("industry", "TEXT", 1, 0),
        ("website", "TEXT", 0, 0),
        ("normalized_domain", "TEXT", 0, 0),
        ("observed_at", "TEXT", 1, 0),
        ("verified_status", "TEXT", 1, 0),
        ("fit_reason", "TEXT", 1, 0),
        ("customer_pain_confirmed", "INTEGER", 1, 0),
        ("qualified", "INTEGER", 1, 0),
        ("contact_channel", "TEXT", 1, 0),
        ("channel_permission_status", "TEXT", 1, 0),
        ("lead_status", "TEXT", 1, 0),
        ("opt_out", "TEXT", 1, 0),
        ("notes", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
    ),
    "finder_source_evidence": (
        ("id", "INTEGER", 0, 1),
        ("finder_record_id", "INTEGER", 1, 0),
        ("source_object_id", "TEXT", 1, 0),
        ("source_url", "TEXT", 1, 0),
        ("company_name", "TEXT", 1, 0),
        ("region", "TEXT", 1, 0),
        ("industry", "TEXT", 1, 0),
        ("website", "TEXT", 0, 0),
        ("normalized_domain", "TEXT", 0, 0),
        ("observed_at", "TEXT", 1, 0),
        ("researcher", "TEXT", 1, 0),
        ("recorded_at", "TEXT", 1, 0),
    ),
}

_EXPECTED_INDEXES = {
    "finder_records_source_object_unique": ("finder_records", 1, 0, ("source_object_id",)),
    "finder_records_source_url_unique": ("finder_records", 1, 0, ("source_url",)),
    "finder_records_domain_unique": ("finder_records", 1, 1, ("normalized_domain",)),
    "finder_source_evidence_record_index": ("finder_source_evidence", 0, 0, ("finder_record_id",)),
}
_EXPECTED_OBJECTS = {
    *{("table", name) for name in _EXPECTED_TABLE_COLUMNS},
    *{("index", name) for name in _EXPECTED_INDEXES},
}

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS finder_records (
        id INTEGER PRIMARY KEY,
        source_object_id TEXT NOT NULL,
        source_url TEXT NOT NULL,
        company_name TEXT NOT NULL,
        normalized_name TEXT NOT NULL,
        region TEXT NOT NULL,
        normalized_region TEXT NOT NULL,
        industry TEXT NOT NULL,
        website TEXT,
        normalized_domain TEXT,
        observed_at TEXT NOT NULL,
        verified_status TEXT NOT NULL,
        fit_reason TEXT NOT NULL,
        customer_pain_confirmed INTEGER NOT NULL,
        qualified INTEGER NOT NULL,
        contact_channel TEXT NOT NULL,
        channel_permission_status TEXT NOT NULL,
        lead_status TEXT NOT NULL,
        opt_out TEXT NOT NULL,
        notes TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS finder_records_source_object_unique ON finder_records(source_object_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS finder_records_source_url_unique ON finder_records(source_url)",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS finder_records_domain_unique
        ON finder_records(normalized_domain)
        WHERE normalized_domain IS NOT NULL AND normalized_domain <> ''
    """,
    """
    CREATE TABLE IF NOT EXISTS finder_source_evidence (
        id INTEGER PRIMARY KEY,
        finder_record_id INTEGER NOT NULL REFERENCES finder_records(id),
        source_object_id TEXT NOT NULL,
        source_url TEXT NOT NULL,
        company_name TEXT NOT NULL,
        region TEXT NOT NULL,
        industry TEXT NOT NULL,
        website TEXT,
        normalized_domain TEXT,
        observed_at TEXT NOT NULL,
        researcher TEXT NOT NULL,
        recorded_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS finder_source_evidence_record_index ON finder_source_evidence(finder_record_id)",
)


@dataclass(frozen=True)
class _Candidate:
    index: int
    source_object_id: str
    source_url: str
    company_name: str
    normalized_name: str
    region: str
    normalized_region: str
    industry: str
    website: str | None
    normalized_domain: str | None
    observed_at: str


@dataclass(frozen=True)
class _NoveltyEvidence:
    source_id: str
    snapshot_id: str
    completeness: str
    authoritative: bool
    no_prior_match: bool
    record_count: int
    records_sha256: str


@dataclass(frozen=True)
class _ParsedBatch:
    raw_records: list[object]
    parsed: dict[int, _Candidate]
    malformed: dict[int, tuple[str | None, str]]
    novelty_evidence: _NoveltyEvidence | None

    @property
    def candidates(self) -> list[_Candidate]:
        return list(self.parsed.values())


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value!r}")


def _empty_report(mode: str, *, error: str | None = None) -> dict[str, Any]:
    report: dict[str, Any] = {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "preview_only": True,
        "database_write_performed": False,
        "network_performed": False,
        "provider_call_performed": False,
        "outreach_performed": False,
        "counts": {"total": 0, **{classification: 0 for classification in CLASSIFICATIONS}},
        "records": [],
        "all_new": False,
        "importable": False,
        "imported": 0,
        "rejected": error is not None,
    }
    if error is not None:
        report["error"] = error
    return report


def _error(mode: str, message: str, *, code: str) -> FinderError:
    return FinderError(message, code=code, report=_empty_report(mode, error=message))


def _is_unknown_placeholder(value: str) -> bool:
    normalized = normalize_token(value)
    collapsed = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return is_unknown(value) or normalized in {
        "n_a", "na", "nil", "null", "tbd", "not_applicable", "not_known", "not_provided", "not_available", "unavailable"
    } or collapsed in {
        "n_a", "na", "none", "null", "nil", "unknown", "tbd", "not_applicable", "not_known", "not_provided", "not_available", "unavailable"
    }


def _safe_text(value: object, field: str, *, reject_unknown: bool = True, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field} contains invalid Unicode") from exc
    if any(unicodedata.category(character) in {"Cc", "Cf", "Cn", "Co", "Cs"} for character in value):
        raise ValueError(f"{field} contains a control or format character")
    text = canonical_text(value)
    if not text:
        if allow_empty:
            return ""
        raise ValueError(f"{field} must be nonempty")
    if reject_unknown and _is_unknown_placeholder(text):
        raise ValueError(f"{field} must not be an unknown placeholder")
    return text


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError) as exc:
        raise ValueError(f"records could not be canonically serialized: {exc}") from exc


def _records_sha256(records: list[object]) -> str:
    return hashlib.sha256(_canonical_json_bytes(records)).hexdigest()


def _validate_novelty_evidence(raw: object, records: list[object]) -> _NoveltyEvidence:
    if not isinstance(raw, dict):
        raise ValueError(f"{_NOVELTY_EVIDENCE_FIELD} must be an object")
    extra = sorted(set(raw) - _NOVELTY_EVIDENCE_FIELDS)
    missing = sorted(_NOVELTY_EVIDENCE_FIELDS - set(raw))
    if extra:
        raise ValueError("unknown novelty evidence fields: " + ", ".join(extra))
    if missing:
        raise ValueError("missing novelty evidence fields: " + ", ".join(missing))
    source_id = _safe_text(raw.get("source_id"), "novelty_evidence.source_id")
    snapshot_id = _safe_text(raw.get("snapshot_id"), "novelty_evidence.snapshot_id")
    if raw.get("completeness") != "complete":
        raise ValueError("novelty_evidence.completeness must be exactly 'complete'")
    if type(raw.get("authoritative")) is not bool or raw.get("authoritative") is not True:
        raise ValueError("novelty_evidence.authoritative must be true")
    if type(raw.get("no_prior_match")) is not bool or raw.get("no_prior_match") is not True:
        raise ValueError("novelty_evidence.no_prior_match must be true")
    record_count = raw.get("record_count")
    if type(record_count) is not int or record_count < 1 or record_count != len(records):
        raise ValueError("novelty_evidence.record_count must equal the nonempty records length")
    digest = raw.get("records_sha256")
    if not isinstance(digest, str) or not _HEX_SHA256_RE.fullmatch(digest):
        raise ValueError("novelty_evidence.records_sha256 must be a lowercase SHA-256 hex digest")
    if digest != _records_sha256(records):
        raise ValueError("novelty_evidence.records_sha256 does not match canonical records")
    return _NoveltyEvidence(source_id, snapshot_id, "complete", True, True, record_count, digest)


def _normalized_hostname(hostname: str | None, field: str) -> str:
    if not hostname:
        raise ValueError(f"{field} must contain a hostname")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None:
        return address.compressed.casefold()
    try:
        normalized = hostname.encode("idna").decode("ascii").casefold().rstrip(".")
    except UnicodeError as exc:
        raise ValueError(f"{field} hostname could not be normalized") from exc
    if not normalized or len(normalized) > 253 or _is_unknown_placeholder(normalized):
        raise ValueError(f"{field} must contain a valid hostname")
    labels = normalized.split(".")
    if any(not label or len(label) > 63 or not _HOST_LABEL_RE.fullmatch(label) for label in labels):
        raise ValueError(f"{field} must contain valid hostname labels")
    return normalized


def _parsed_http_url(value: str, field: str, *, allow_bare_domain: bool = False) -> tuple[str, str]:
    text = _safe_text(value, field)
    if any(character.isspace() for character in text):
        raise ValueError(f"{field} must not contain whitespace")
    has_scheme = "://" in text
    if not has_scheme and not allow_bare_domain:
        raise ValueError(f"{field} must be an absolute http(s) URL")
    parse_text = text if has_scheme else f"//{text}"
    try:
        parsed = urlparse(parse_text)
        scheme = parsed.scheme.casefold()
        hostname = parsed.hostname
        username = parsed.username
        password = parsed.password
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must contain a valid HTTP(S) host") from exc
    if has_scheme and scheme not in {"http", "https"}:
        raise ValueError(f"{field} must use http or https")
    if not hostname or username is not None or password is not None:
        raise ValueError(f"{field} must not contain credentials and must contain a hostname")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError(f"{field} must contain a valid port")
    if not has_scheme and (parsed.path or parsed.params or parsed.query or parsed.fragment):
        raise ValueError(f"{field} must be a bare domain or an HTTP(S) URL")
    return text, _normalized_hostname(hostname, field)


def _validate_finder_source_url(value: str) -> str:
    return _parsed_http_url(value, "source_url")[0]


def _normalize_finder_website(value: object) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    text = _safe_text(value, "website", reject_unknown=False, allow_empty=True)
    if not text or _is_unknown_placeholder(text):
        return None, None
    website, domain = _parsed_http_url(text, "website", allow_bare_domain=True)
    return website, domain.removeprefix("www.")


def _read_json(path: str | os.PathLike[str], *, mode: str) -> Mapping[str, object]:
    try:
        with open(path, "rb") as handle:
            payload = handle.read(MAX_INPUT_BYTES + 1)
            if len(payload) > MAX_INPUT_BYTES:
                raise ValueError(f"saved finder JSON exceeds the {MAX_INPUT_BYTES}-byte limit")
            value = json.loads(payload, object_pairs_hook=_json_object, parse_constant=_reject_constant)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise _error(mode, f"saved finder JSON could not be read: {exc}", code="invalid_input") from exc
    if not isinstance(value, dict):
        raise _error(mode, "saved finder JSON envelope must be an object", code="invalid_envelope")
    allowed = {"format", "schema_version", "records", _NOVELTY_EVIDENCE_FIELD}
    extra = sorted(set(value) - allowed)
    if extra:
        raise _error(mode, "unknown envelope fields: " + ", ".join(extra), code="invalid_envelope")
    if value.get("format") != FORMAT or type(value.get("schema_version")) is not int or value.get("schema_version") != SCHEMA_VERSION:
        raise _error(mode, "unsupported finder JSON format or schema_version", code="invalid_envelope")
    if not isinstance(value.get("records"), list):
        raise _error(mode, "finder JSON envelope records must be a list", code="invalid_envelope")
    if len(value["records"]) > MAX_RECORDS:
        raise _error(mode, f"finder JSON records exceed the {MAX_RECORDS}-record limit", code="invalid_envelope")
    if _NOVELTY_EVIDENCE_FIELD in value:
        try:
            _validate_novelty_evidence(value[_NOVELTY_EVIDENCE_FIELD], value["records"])
        except (TypeError, UnicodeError, ValueError) as exc:
            raise _error(mode, f"invalid novelty evidence contract: {exc}", code="invalid_novelty_evidence") from exc
    return value


def _parse_record(raw: object, index: int) -> tuple[_Candidate | None, str | None, str | None]:
    source_object_id: str | None = None
    if isinstance(raw, dict) and isinstance(raw.get("source_object_id"), str):
        source_object_id = canonical_text(raw["source_object_id"])
    if not isinstance(raw, dict):
        return None, source_object_id, "record must be an object"
    extra = sorted(set(raw) - _RECORD_FIELDS)
    if extra:
        return None, source_object_id, "unknown record fields: " + ", ".join(extra)
    if "observed_at" in raw and "retrieved_at" in raw:
        return None, source_object_id, "use exactly one of observed_at or retrieved_at"
    if "observed_at" not in raw and "retrieved_at" not in raw:
        return None, source_object_id, "observed_at is required"
    try:
        source_object_id = _safe_text(raw.get("source_object_id"), "source_object_id")
        if not any(character.isalnum() for character in source_object_id):
            raise ValueError("source_object_id must contain an alphanumeric character")
        source_url = _validate_finder_source_url(_safe_text(raw.get("source_url"), "source_url"))
        company_name = _safe_text(raw.get("company_name"), "company_name")
        region = _safe_text(raw.get("region"), "region")
        industry = _safe_text(raw.get("industry"), "industry")
        if not normalize_name(company_name):
            raise ValueError("company_name must contain letters or digits")
        if not normalize_region(region):
            raise ValueError("region must contain letters or digits")
        if not normalize_name(industry):
            raise ValueError("industry must contain letters or digits")
        timestamp_field = "observed_at" if "observed_at" in raw else "retrieved_at"
        observed_at = parse_utc_timestamp(_safe_text(raw.get(timestamp_field), timestamp_field), field_name=timestamp_field)
        website, normalized_domain = _normalize_finder_website(raw.get("website"))
    except (AttributeError, TypeError, ValueError, ValidationError) as exc:
        return None, source_object_id, str(exc)
    return (
        _Candidate(
            index=index,
            source_object_id=source_object_id,
            source_url=source_url,
            company_name=company_name,
            normalized_name=normalize_name(company_name),
            region=region,
            normalized_region=normalize_region(region),
            industry=industry,
            website=website,
            normalized_domain=normalized_domain,
            observed_at=observed_at,
        ),
        source_object_id,
        None,
    )


def _parse_batch(envelope: Mapping[str, object]) -> _ParsedBatch:
    raw_records = envelope["records"]
    assert isinstance(raw_records, list)
    novelty_evidence = None
    if _NOVELTY_EVIDENCE_FIELD in envelope:
        novelty_evidence = _validate_novelty_evidence(envelope[_NOVELTY_EVIDENCE_FIELD], raw_records)
    parsed: dict[int, _Candidate] = {}
    malformed: dict[int, tuple[str | None, str]] = {}
    raw_ids: dict[str, list[int]] = defaultdict(list)
    for index, raw in enumerate(raw_records, start=1):
        if isinstance(raw, dict) and isinstance(raw.get("source_object_id"), str):
            identifier = canonical_text(raw["source_object_id"])
            if identifier:
                raw_ids[identifier].append(index)
        candidate, identifier, reason = _parse_record(raw, index)
        if candidate is None:
            malformed[index] = (identifier, reason or "record is malformed")
        else:
            parsed[index] = candidate
    for identifier, indexes in raw_ids.items():
        if len(indexes) > 1:
            for index in indexes:
                malformed[index] = (identifier, "duplicate input source_object_id")
                parsed.pop(index, None)
    return _ParsedBatch(raw_records, parsed, malformed, novelty_evidence)


def _database_error(mode: str, message: str, *, code: str = "database_schema_invalid") -> FinderError:
    return FinderError(message, code=code, report=_empty_report(mode, error=message))


def _raw_schema_objects(connection: sqlite3.Connection, *, mode: str) -> set[tuple[str, str]]:
    try:
        attached = connection.execute("PRAGMA database_list").fetchall()
        if any(row[1] != "main" for row in attached):
            raise _database_error(mode, "synthetic database has unexpected attached schemas")
        rows = connection.execute(
            "SELECT type, name FROM main.sqlite_master WHERE type IN ('table', 'index', 'view', 'trigger')"
        ).fetchall()
        return {(row[0], row[1]) for row in rows}
    except FinderError:
        raise
    except sqlite3.Error as exc:
        raise _database_error(mode, f"synthetic database schema could not be inspected: {exc}") from exc


def _validate_schema(connection: sqlite3.Connection, *, mode: str) -> None:
    actual = _raw_schema_objects(connection, mode=mode)
    if actual != _EXPECTED_OBJECTS:
        unexpected = sorted(actual - _EXPECTED_OBJECTS)
        missing = sorted(_EXPECTED_OBJECTS - actual)
        details: list[str] = []
        if unexpected:
            details.append("unexpected objects: " + ", ".join(f"{kind}:{name}" for kind, name in unexpected))
        if missing:
            details.append("missing objects: " + ", ".join(f"{kind}:{name}" for kind, name in missing))
        raise _database_error(mode, "synthetic database is not an intact Finder-only schema (" + "; ".join(details) + ")")
    try:
        for table, expected_columns in _EXPECTED_TABLE_COLUMNS.items():
            rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
            actual_columns = tuple((row[1], row[2], int(row[3]), int(row[5])) for row in rows)
            if actual_columns != expected_columns:
                raise _database_error(mode, f"synthetic database table {table} has an unexpected schema")
            expected_indexes = {
                name: (unique, partial, columns)
                for name, (table_name, unique, partial, columns) in _EXPECTED_INDEXES.items()
                if table_name == table
            }
            rows = connection.execute(f"PRAGMA index_list({table})").fetchall()
            if {row[1] for row in rows} != set(expected_indexes):
                raise _database_error(mode, f"synthetic database table {table} has unexpected indexes")
            for row in rows:
                name = row[1]
                unique, partial, columns = expected_indexes[name]
                if int(row[2]) != unique or int(row[4]) != partial:
                    raise _database_error(mode, f"synthetic database index {name} has unexpected constraints")
                index_columns = tuple(item[2] for item in connection.execute(f"PRAGMA index_info({name})").fetchall())
                if index_columns != columns:
                    raise _database_error(mode, f"synthetic database index {name} has unexpected columns")
        foreign_keys = connection.execute("PRAGMA foreign_key_list(finder_source_evidence)").fetchall()
        if len(foreign_keys) != 1 or tuple(foreign_keys[0][2:5]) != ("finder_records", "finder_record_id", "id"):
            raise _database_error(mode, "synthetic database has an unexpected Finder evidence relationship")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise _database_error(mode, "synthetic database contains a foreign-key violation")
    except FinderError:
        raise
    except sqlite3.Error as exc:
        raise _database_error(mode, f"synthetic database schema could not be validated: {exc}") from exc


def _stored_text(value: object, field: str) -> str:
    text = _safe_text(value, field)
    if value != text:
        raise ValueError(f"stored {field} is not canonical")
    return text


def _candidate_from_stored(row: sqlite3.Row, index: int) -> _Candidate:
    source_object_id = _stored_text(row["source_object_id"], "source_object_id")
    if not any(character.isalnum() for character in source_object_id):
        raise ValueError("stored source_object_id must contain an alphanumeric character")
    source_url = _validate_finder_source_url(_stored_text(row["source_url"], "source_url"))
    company_name = _stored_text(row["company_name"], "company_name")
    region = _stored_text(row["region"], "region")
    industry = _stored_text(row["industry"], "industry")
    normalized_name = normalize_name(company_name)
    normalized_region = normalize_region(region)
    if not normalized_name or not normalized_region or not normalize_name(industry):
        raise ValueError("stored identity fields must contain letters or digits")
    observed_at = parse_utc_timestamp(_stored_text(row["observed_at"], "observed_at"), field_name="observed_at")
    website, normalized_domain = _normalize_finder_website(row["website"])
    return _Candidate(
        index=index,
        source_object_id=source_object_id,
        source_url=source_url,
        company_name=company_name,
        normalized_name=normalized_name,
        region=region,
        normalized_region=normalized_region,
        industry=industry,
        website=website,
        normalized_domain=normalized_domain,
        observed_at=observed_at,
    )


def _read_existing_connection(connection: sqlite3.Connection, *, mode: str) -> list[_Candidate]:
    try:
        rows = connection.execute(
            """
            SELECT source_object_id, source_url, company_name, normalized_name,
                   region, normalized_region, industry, website, normalized_domain,
                   observed_at
            FROM finder_records ORDER BY id
            """
        ).fetchall()
    except sqlite3.Error as exc:
        raise _database_error(mode, f"synthetic Finder records could not be read: {exc}", code="database_read_failed") from exc
    result: list[_Candidate] = []
    for index, row in enumerate(rows, start=1):
        try:
            result.append(_candidate_from_stored(row, index=0))
        except (AttributeError, TypeError, ValueError, ValidationError) as exc:
            raise _database_error(
                mode,
                f"synthetic Finder record {index} has malformed identity fields: {exc}",
                code="database_row_invalid",
            ) from exc
    return result


def _require_image_apis(*, mode: str, deserialize: bool, serialize: bool) -> None:
    if deserialize and not callable(getattr(sqlite3.Connection, "deserialize", None)):
        raise _database_error(mode, "this Python SQLite build does not support image deserialization", code="unsupported_sqlite_image")
    if serialize and not callable(getattr(sqlite3.Connection, "serialize", None)):
        raise _database_error(mode, "this Python SQLite build does not support image serialization", code="unsupported_sqlite_image")


def _memory_connection(image: bytes | None, *, mode: str) -> sqlite3.Connection:
    if image is not None:
        _require_image_apis(mode=mode, deserialize=True, serialize=False)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if image is not None:
            connection.deserialize(image, name="main")
            connection.execute("PRAGMA foreign_keys = ON")
        return connection
    except FinderError:
        if connection is not None:
            connection.close()
        raise
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        if connection is not None:
            connection.close()
        code = "database_image_invalid" if image is not None else "database_read_failed"
        raise _database_error(mode, f"synthetic database image could not be opened: {exc}", code=code) from exc


def _target_image(target: Any, *, mode: str) -> bytes | None:
    try:
        target.verify_entry()
        image = target.read_image()
        target.verify_entry()
    except StorageFailure:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise _database_error(mode, f"synthetic database image could not be read: {exc}", code="database_read_failed") from exc
    if image is not None and not isinstance(image, bytes):
        raise _database_error(mode, "synthetic database image is not a byte string", code="database_image_invalid")
    if image is not None and len(image) > MAX_IMAGE_BYTES:
        raise _database_error(mode, f"synthetic database image exceeds the {MAX_IMAGE_BYTES}-byte limit", code="database_image_invalid")
    if image is None and bool(getattr(target, "exists", False)) and not bool(getattr(target, "memory", False)):
        raise _database_error(mode, "existing synthetic database has no readable image", code="database_read_failed")
    return image


def _open_target_connection(target: Any, *, mode: str, require_serialize: bool = False) -> tuple[sqlite3.Connection, bytes | None]:
    # The active linux_storage target intentionally exposes only image reads;
    # this keeps SQLite away from filesystem paths and journals.
    _require_image_apis(
        mode=mode,
        deserialize=bool(getattr(target, "exists", False)) and not bool(getattr(target, "memory", False)),
        serialize=require_serialize,
    )
    image = _target_image(target, mode=mode)
    return _memory_connection(image, mode=mode), image


def _same_stable_identity(left: _Candidate, right: _Candidate) -> bool:
    return left.source_object_id == right.source_object_id or left.source_url == right.source_url


def _same_identity_facts(left: _Candidate, right: _Candidate) -> bool:
    return (
        left.source_object_id == right.source_object_id
        and left.source_url == right.source_url
        and left.normalized_domain == right.normalized_domain
        and left.normalized_name == right.normalized_name
        and left.normalized_region == right.normalized_region
    )


def _near_name_match(left: _Candidate, right: _Candidate) -> bool:
    if left.normalized_region != right.normalized_region or left.normalized_name == right.normalized_name:
        return False
    return difflib.SequenceMatcher(
        None, left.normalized_name, right.normalized_name, autojunk=False
    ).ratio() >= NEAR_NAME_SIMILARITY_THRESHOLD


def _classify_candidates(
    candidates: list[_Candidate],
    existing: list[_Candidate],
    *,
    novelty_evidence_valid: bool,
) -> dict[int, tuple[str, str]]:
    domain_identity_counts: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for row in existing:
        if row.normalized_domain:
            domain_identity_counts[row.normalized_domain].add((row.source_object_id, row.source_url))
    result: dict[int, tuple[str, str]] = {}
    for candidate in candidates:
        conflicts = False
        exact = False
        probable = False
        ambiguous = False
        for other in existing + candidates:
            if other is candidate:
                continue
            if _same_stable_identity(candidate, other):
                if _same_identity_facts(candidate, other):
                    exact = True
                else:
                    conflicts = True
                continue
            if candidate.normalized_domain and candidate.normalized_domain == other.normalized_domain:
                probable = True
                continue
            if candidate.normalized_name == other.normalized_name and candidate.normalized_region == other.normalized_region:
                ambiguous = True
            elif _near_name_match(candidate, other):
                probable = True
        if candidate.normalized_domain and len(domain_identity_counts[candidate.normalized_domain]) > 1:
            conflicts = True
        if conflicts:
            result[candidate.index] = (
                "CONFLICTING_IDENTITY",
                "stable source identity conflicts with domain/name/region or a domain maps to multiple existing identities",
            )
        elif exact:
            result[candidate.index] = (
                "EXACT_DUPLICATE",
                "same stable source identity and identity facts already exist",
            )
        elif probable:
            result[candidate.index] = (
                "PROBABLE_DUPLICATE",
                "normalized website domain or near-identical company name "
                f"(SequenceMatcher ratio >= {NEAR_NAME_SIMILARITY_THRESHOLD:.2f}) matches a different source identity",
            )
        elif ambiguous:
            result[candidate.index] = (
                "AMBIGUOUS_DUPLICATE",
                "normalized company name and region match without consistent stable/domain evidence",
            )
        elif novelty_evidence_valid:
            result[candidate.index] = (
                "NEW",
                "no identity collision was detected and the saved input has a valid novelty-evidence contract",
            )
        else:
            result[candidate.index] = (
                "AMBIGUOUS_DUPLICATE",
                "no identity collision was detected, but the saved input has no valid novelty-evidence contract",
            )
    return result


def _build_report(
    mode: str,
    raw_records: list[object],
    parsed: dict[int, _Candidate],
    malformed: dict[int, tuple[str | None, str]],
    classifications: dict[int, tuple[str, str]],
    *,
    novelty_evidence_valid: bool = False,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    counts = {classification: 0 for classification in CLASSIFICATIONS}
    for index, _raw in enumerate(raw_records, start=1):
        if index in malformed:
            source_object_id, reason = malformed[index]
            classification = "MALFORMED"
        else:
            candidate = parsed[index]
            source_object_id = candidate.source_object_id
            classification, reason = classifications[index]
        counts[classification] += 1
        records.append({"index": index, "source_object_id": source_object_id, "classification": classification, "reason": reason})
    all_new = bool(raw_records) and not malformed and all(classification == "NEW" for classification, _reason in classifications.values())
    return {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "preview_only": True,
        "database_write_performed": False,
        "network_performed": False,
        "provider_call_performed": False,
        "outreach_performed": False,
        "counts": {"total": len(raw_records), **counts},
        "records": records,
        "novelty_evidence_valid": novelty_evidence_valid,
        "all_new": all_new,
        "importable": all_new,
        "imported": 0,
        "rejected": not all_new,
    }


def _classify_batch(batch: _ParsedBatch, existing: list[_Candidate], *, mode: str) -> dict[str, Any]:
    novelty_evidence_valid = batch.novelty_evidence is not None
    classifications = _classify_candidates(
        batch.candidates,
        existing,
        novelty_evidence_valid=novelty_evidence_valid,
    )
    return _build_report(
        mode,
        batch.raw_records,
        batch.parsed,
        batch.malformed,
        classifications,
        novelty_evidence_valid=novelty_evidence_valid,
    )


def _classify_target(target: Any, batch: _ParsedBatch, *, mode: str) -> dict[str, Any]:
    connection, image = _open_target_connection(target, mode=mode)
    try:
        existing: list[_Candidate] = []
        if image is not None:
            _validate_schema(connection, mode=mode)
            existing = _read_existing_connection(connection, mode=mode)
        return _classify_batch(batch, existing, mode=mode)
    finally:
        connection.close()


def open_target(
    database: str | os.PathLike[str],
    *,
    workspace_root: Path,
    expected_exists: bool | None = None,
) -> Any:
    """Expose only the capability-checked anonymous-image storage seam."""

    return _linux_open_target(
        database,
        workspace_root=workspace_root,
        expected_exists=expected_exists,
    )


def _storage_failure_report(report: dict[str, Any], failure: StorageFailure, *, candidate_count: int) -> dict[str, Any]:
    installed = bool(failure.installed)
    report["error"] = str(failure)
    report["rejected"] = True
    report["importable"] = False
    report["database_write_performed"] = installed
    report["preview_only"] = not installed
    report["imported"] = candidate_count if installed else 0
    report["installation_state"] = failure.state
    report["storage_code"] = failure.code
    report["installed"] = installed
    report["durability_confirmed"] = bool(getattr(failure, "durability_confirmed", False))
    report["cleanup_required"] = bool(getattr(failure, "cleanup_required", False))
    report["cleanup_artifacts"] = list(getattr(failure, "artifacts", ()))
    report["cleanup_errors"] = list(getattr(failure, "cleanup_errors", ()))
    return report


def _storage_failure_error(mode: str, failure: StorageFailure) -> FinderError:
    return FinderError(str(failure), code=failure.code, report=_storage_failure_report(_empty_report(mode), failure, candidate_count=0))


def _with_target(
    database: str | os.PathLike[str],
    *,
    mode: str,
    operation: Any,
    candidate_count: int,
    expected_exists: bool | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] | None = None
    try:
        with open_target(database, workspace_root=_WORKSPACE_ROOT, expected_exists=expected_exists) as target:
            result = operation(target)
    except StorageFailure as exc:
        if result is not None:
            return _storage_failure_report(result, exc, candidate_count=candidate_count)
        raise _storage_failure_error(mode, exc) from exc
    except (OSError, TypeError, ValueError) as exc:
        raise _error(mode, f"synthetic database target could not be opened: {exc}", code="invalid_database_path") from exc
    assert result is not None
    return result


def classify_saved_input(
    input_path: str | os.PathLike[str],
    synthetic_db: str | os.PathLike[str],
) -> dict[str, Any]:
    """Classify saved JSON without opening a writable database connection."""

    envelope = _read_json(input_path, mode="classify")
    batch = _parse_batch(envelope)
    if not batch.raw_records:
        return _classify_batch(batch, [], mode="classify")
    return _with_target(
        synthetic_db,
        mode="classify",
        operation=lambda target: _classify_target(target, batch, mode="classify"),
        candidate_count=0,
        expected_exists=_target_exists_hint(synthetic_db),
    )


def _target_exists_hint(database: str | os.PathLike[str]) -> bool | None:
    """Capture existence only as a race guard; storage remains authoritative."""

    try:
        raw = os.fspath(database)
    except TypeError:
        return None
    if raw == ":memory:":
        return False
    if not isinstance(raw, str) or not raw or raw.casefold().startswith(("file:", "sqlite:")):
        return None
    if any(part == ".." for part in raw.replace("\\", "/").split("/")):
        return None
    try:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        workspace = _WORKSPACE_ROOT.resolve(strict=False)
        if path == workspace or workspace in path.parents:
            return None
        return os.path.lexists(os.fspath(path))
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def _create_schema(connection: sqlite3.Connection) -> None:
    for statement in SCHEMA_STATEMENTS:
        connection.execute(statement)


def _insert_new(connection: sqlite3.Connection, candidates: list[_Candidate], *, researcher: str) -> None:
    recorded_at = utc_now()
    for candidate in candidates:
        cursor = connection.execute(
            """
            INSERT INTO finder_records
                (source_object_id, source_url, company_name, normalized_name,
                 region, normalized_region, industry, website, normalized_domain,
                 observed_at, verified_status, fit_reason, customer_pain_confirmed,
                 qualified, contact_channel, channel_permission_status, lead_status,
                 opt_out, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'discovered', ?, 0, 0,
                    'unknown', 'unknown', 'new', 'unknown', '', ?)
            """,
            (
                candidate.source_object_id,
                candidate.source_url,
                candidate.company_name,
                candidate.normalized_name,
                candidate.region,
                candidate.normalized_region,
                candidate.industry,
                candidate.website,
                candidate.normalized_domain,
                candidate.observed_at,
                FIT_REASON,
                recorded_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO finder_source_evidence
                (finder_record_id, source_object_id, source_url, company_name,
                 region, industry, website, normalized_domain, observed_at,
                 researcher, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(cursor.lastrowid),
                candidate.source_object_id,
                candidate.source_url,
                candidate.company_name,
                candidate.region,
                candidate.industry,
                candidate.website,
                candidate.normalized_domain,
                candidate.observed_at,
                researcher,
                recorded_at,
            ),
        )


def _validate_researcher(value: object) -> str:
    text = _safe_text(value, "researcher")
    if normalize_token(text) in _MACHINE_RESEARCHER_VALUES:
        raise ValueError("researcher must identify a human operator")
    return text


def _report_rejection(report: dict[str, Any], message: str) -> dict[str, Any]:
    report["error"] = message
    report["rejected"] = True
    report["importable"] = False
    return report


def _confirmed_target_import(target: Any, batch: _ParsedBatch, *, researcher: str) -> dict[str, Any]:
    connection: sqlite3.Connection | None = None
    report: dict[str, Any] | None = None
    committed = False
    try:
        connection, _image = _open_target_connection(target, mode="import", require_serialize=True)
        connection.execute("BEGIN IMMEDIATE")
        target.verify_entry()
        objects = _raw_schema_objects(connection, mode="import")
        schema_present = bool(objects)
        if bool(getattr(target, "exists", False)) or schema_present:
            _validate_schema(connection, mode="import")
            existing = _read_existing_connection(connection, mode="import")
        else:
            existing = []
        report = _classify_batch(batch, existing, mode="import")
        if not report["all_new"]:
            connection.rollback()
            return _report_rejection(report, "confirmed import requires every record to be NEW")
        if bool(getattr(target, "exists", False)):
            connection.rollback()
            return _report_rejection(report, "existing synthetic database snapshots are read-only")
        if not schema_present:
            _create_schema(connection)
        _insert_new(connection, batch.candidates, researcher=researcher)
        target.verify_entry()
        connection.commit()
        committed = True
        image = connection.serialize(name="main")
        target.verify_entry()
        if not bool(getattr(target, "memory", False)):
            target.install_image(image)
    except FinderError:
        if connection is not None and not committed:
            connection.rollback()
        raise
    except StorageFailure as exc:
        if connection is not None and not committed:
            connection.rollback()
        if report is not None:
            return _storage_failure_report(report, exc, candidate_count=len(batch.candidates))
        raise _storage_failure_error("import", exc) from exc
    except (OSError, sqlite3.Error) as exc:
        if connection is not None and not committed:
            connection.rollback()
        if report is not None:
            return _report_rejection(report, f"synthetic database write failed: {exc}")
        raise _database_error("import", f"synthetic database write failed: {exc}", code="database_write_failed") from exc
    finally:
        if connection is not None:
            connection.close()
    assert report is not None
    report["preview_only"] = False
    report["database_write_performed"] = True
    report["imported"] = len(batch.candidates)
    report["rejected"] = False
    return report


def import_saved_input(
    input_path: str | os.PathLike[str],
    synthetic_db: str | os.PathLike[str],
    *,
    confirm_import: bool = False,
    researcher: str | None = None,
) -> dict[str, Any]:
    """Preview by default; insert only an entirely NEW confirmed batch."""

    envelope = _read_json(input_path, mode="import")
    batch = _parse_batch(envelope)
    if not batch.raw_records:
        report = _classify_batch(batch, [], mode="import")
        if researcher is not None:
            try:
                _validate_researcher(researcher)
            except ValueError as exc:
                _report_rejection(report, str(exc))
        return report
    if not confirm_import:
        def preview(target: Any) -> dict[str, Any]:
            report = _classify_target(target, batch, mode="import")
            if researcher is not None:
                try:
                    _validate_researcher(researcher)
                except ValueError as exc:
                    _report_rejection(report, str(exc))
            return report
        return _with_target(
            synthetic_db,
            mode="import",
            operation=preview,
            candidate_count=0,
            expected_exists=_target_exists_hint(synthetic_db),
        )
    try:
        actor = _validate_researcher(researcher)
    except ValueError as exc:
        report = _classify_batch(batch, [], mode="import")
        return _report_rejection(report, str(exc))
    if batch.malformed:
        report = _classify_batch(batch, [], mode="import")
        return _report_rejection(report, "confirmed import requires every record to be valid and NEW")
    return _with_target(
        synthetic_db,
        mode="import",
        operation=lambda target: _confirmed_target_import(target, batch, researcher=actor),
        candidate_count=len(batch.candidates),
        expected_exists=_target_exists_hint(synthetic_db),
    )


classify_file = classify_saved_input
import_file = import_saved_input
