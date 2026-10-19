"""SQLite-backed prospect, source-evidence, scoring, and approval store."""

from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, TextIO

from .errors import ApprovalError, NotFoundError, ValidationError
from .normalization import (
    canonical_text,
    is_unknown,
    normalize_domain,
    normalize_name,
    normalize_region,
    normalize_token,
    validate_source_url,
)
from . import requirements as requirements_contract
from .scoring import SCORING_DISCLAIMER, calculate_fit_score
from .timeutil import parse_optional_utc_timestamp, parse_utc_timestamp, utc_now

CSV_COLUMNS = (
    "company_name",
    "website",
    "region",
    "industry",
    "source_url",
    "source_checked_at",
    "verified_status",
    "contact_name",
    "contact_role",
    "contact_channel",
    "channel_permission_status",
    "fit_reason",
    "fit_confidence",
    "lead_status",
    "last_contact_at",
    "next_action",
    "next_action_due",
    "opt_out",
    "notes",
)

# These are the fields that must exist in a header.  Contact identity fields
# may be unknown, but the unknown must be written explicitly so an empty cell
# cannot be mistaken for permission or silently turned into fabricated data.
REQUIRED_COLUMNS = (
    "company_name",
    "region",
    "industry",
    "source_url",
    "source_checked_at",
    "verified_status",
    "contact_name",
    "contact_role",
    "contact_channel",
    "channel_permission_status",
    "fit_reason",
    "opt_out",
)

EXPORT_COLUMNS = CSV_COLUMNS + (
    "source_researcher",
    "source_evidence_count",
    "fit_score",
    "score_reasons",
)

PERMISSION_STATUSES = frozenset(
    {"granted", "not_required_confirmed", "unknown", "withdrawn", "blocked"}
)
MEETING_RECORDING_POLICIES = frozenset(
    {"not_recorded", "permission_granted", "permission_denied"}
)
MEETING_NOTES_PERMISSIONS = frozenset({"permission_granted", "not_required_confirmed"})
LEAD_STATUSES = frozenset(
    {
        "new",
        "researched",
        "qualified",
        "draft_ready",
        "outreach_draft",
        "approved_to_contact",
        "contacted",
        "replied",
        "meeting_booked",
        "discovery_complete",
        "proposal_draft",
        "proposal_sent",
        "negotiation",
        "won",
        "lost",
        "nurture",
        "suppressed",
        "withdrawn",
        "blocked",
        "opted_out",
        "unknown",
    }
)

_MACHINE_ACTOR_VALUES = frozenset({"", "unknown", "system", "automation", "automated", "ai", "agent", "bot"})
_MEETING_ADVANCEABLE_LEAD_STATUSES = frozenset(
    {
        "unknown",
        "new",
        "researched",
        "qualified",
        "draft_ready",
        "outreach_draft",
        "approved_to_contact",
        "contacted",
        "replied",
        "meeting_booked",
    }
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    website TEXT,
    normalized_domain TEXT,
    region TEXT NOT NULL,
    normalized_region TEXT NOT NULL,
    industry TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS companies_domain_unique
    ON companies(normalized_domain)
    WHERE normalized_domain IS NOT NULL AND normalized_domain <> '';
CREATE UNIQUE INDEX IF NOT EXISTS companies_name_region_unique
    ON companies(normalized_name, normalized_region)
    WHERE normalized_domain IS NULL OR normalized_domain = '';

CREATE TABLE IF NOT EXISTS prospects (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL UNIQUE REFERENCES companies(id) ON DELETE CASCADE,
    verified_status TEXT NOT NULL,
    fit_reason TEXT NOT NULL,
    fit_confidence REAL,
    fit_score INTEGER NOT NULL DEFAULT 0,
    score_reasons_json TEXT NOT NULL DEFAULT '[]',
    contact_name TEXT NOT NULL DEFAULT 'unknown',
    contact_role TEXT NOT NULL DEFAULT 'unknown',
    contact_channel TEXT NOT NULL DEFAULT 'unknown',
    channel_permission_status TEXT NOT NULL DEFAULT 'unknown',
    opt_out INTEGER CHECK (opt_out IN (0, 1) OR opt_out IS NULL),
    lead_status TEXT NOT NULL DEFAULT 'new',
    last_contact_at TEXT,
    next_action TEXT,
    next_action_due TEXT,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS prospects_status_index ON prospects(lead_status);

CREATE TABLE IF NOT EXISTS source_evidence (
    id INTEGER PRIMARY KEY,
    prospect_id INTEGER NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    researcher TEXT NOT NULL,
    verified_status TEXT NOT NULL,
    fit_reason TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    recorded_at TEXT NOT NULL,
    UNIQUE(prospect_id, source_url, observed_at, researcher)
);
CREATE INDEX IF NOT EXISTS source_evidence_prospect_index ON source_evidence(prospect_id);

CREATE TABLE IF NOT EXISTS meetings (
    id INTEGER PRIMARY KEY,
    prospect_id INTEGER NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
    meeting_at TEXT NOT NULL,
    notes TEXT NOT NULL,
    notes_permission TEXT NOT NULL CHECK (
        notes_permission IN ('permission_granted', 'not_required_confirmed')
    ),
    recording_policy TEXT NOT NULL CHECK (
        recording_policy IN ('not_recorded', 'permission_granted', 'permission_denied')
    ),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS meetings_prospect_index ON meetings(prospect_id);

CREATE TABLE IF NOT EXISTS requirements_documents (
    id INTEGER PRIMARY KEY,
    prospect_id INTEGER NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE RESTRICT,
    document_version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('DRAFT', 'NEEDS_CLARIFICATION', 'READY_FOR_REVIEW', 'APPROVED', 'SUPERSEDED')
    ),
    content_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    approved_hash TEXT,
    supersedes_document_id INTEGER REFERENCES requirements_documents(id) ON DELETE RESTRICT,
    UNIQUE(meeting_id, document_version)
);
CREATE INDEX IF NOT EXISTS requirements_documents_prospect_index ON requirements_documents(prospect_id);
CREATE INDEX IF NOT EXISTS requirements_documents_meeting_index ON requirements_documents(meeting_id);

CREATE TABLE IF NOT EXISTS outreach_drafts (
    id INTEGER PRIMARY KEY,
    prospect_id INTEGER NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING_APPROVAL'
        CHECK (status = 'PENDING_APPROVAL'),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    send_performed INTEGER NOT NULL DEFAULT 0 CHECK (send_performed = 0)
);
CREATE INDEX IF NOT EXISTS outreach_drafts_prospect_index ON outreach_drafts(prospect_id);

CREATE TABLE IF NOT EXISTS service_packages (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    price_amount INTEGER NOT NULL CHECK (price_amount > 0),
    currency TEXT NOT NULL DEFAULT 'INR',
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    delivery_notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS service_packages_active_index ON service_packages(active);

CREATE TABLE IF NOT EXISTS order_requests (
    id INTEGER PRIMARY KEY,
    prospect_id INTEGER NOT NULL REFERENCES prospects(id) ON DELETE RESTRICT,
    package_id INTEGER NOT NULL REFERENCES service_packages(id) ON DELETE RESTRICT,
    package_name TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK (amount > 0),
    currency TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'REQUESTED'
        CHECK (status IN ('REQUESTED', 'CANCELLED')),
    requested_by TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS order_requests_prospect_index ON order_requests(prospect_id);
CREATE INDEX IF NOT EXISTS order_requests_status_index ON order_requests(status);

CREATE TABLE IF NOT EXISTS payment_requests (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES order_requests(id) ON DELETE RESTRICT,
    amount INTEGER NOT NULL CHECK (amount > 0),
    currency TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING_PROVIDER'
        CHECK (status = 'PENDING_PROVIDER'),
    provider_reference TEXT,
    payment_link TEXT,
    upi_uri TEXT,
    provider_call_performed INTEGER NOT NULL DEFAULT 0 CHECK (provider_call_performed = 0),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS payment_requests_order_index ON payment_requests(order_id);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id INTEGER,
    occurred_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    send_performed INTEGER NOT NULL DEFAULT 0 CHECK (send_performed = 0)
);
CREATE INDEX IF NOT EXISTS audit_events_object_index
    ON audit_events(object_type, object_id);
"""


@dataclass(frozen=True)
class ParsedLead:
    """Validated values for one CSV row."""

    company_name: str
    website: str | None
    normalized_domain: str | None
    region: str
    industry: str
    source_url: str
    source_checked_at: str
    verified_status: str
    contact_name: str
    contact_role: str
    contact_channel: str
    channel_permission_status: str
    fit_reason: str
    fit_confidence: float | None
    lead_status: str | None
    last_contact_at: str | None
    next_action: str | None
    next_action_due: str | None
    opt_out: int | None
    notes: str


@dataclass(frozen=True)
class ImportSummary:
    """Counts returned by an atomic CSV import."""

    rows_read: int
    companies_created: int
    companies_updated: int
    prospects_created: int
    prospects_updated: int
    source_evidence_added: int

    def as_dict(self) -> dict[str, int]:
        return {
            "rows_read": self.rows_read,
            "companies_created": self.companies_created,
            "companies_updated": self.companies_updated,
            "prospects_created": self.prospects_created,
            "prospects_updated": self.prospects_updated,
            "source_evidence_added": self.source_evidence_added,
        }


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _parse_opt_out(value: str | None, *, row_number: int) -> int | None:
    text = normalize_token(value)
    if not text:
        raise ValidationError(f"row {row_number}: opt_out must be explicit true, false, or unknown")
    if text in {"1", "true", "yes", "y", "opted_out", "opt_out"}:
        return 1
    if text in {"0", "false", "no", "n", "not_opted_out"}:
        return 0
    if text in {"unknown", "not_known", "not_provided", "not_available", "unclear", "na", "n_a"}:
        return None
    raise ValidationError(f"row {row_number}: opt_out must be true, false, or unknown")


def _parse_permission(value: str | None, *, row_number: int) -> str:
    text = normalize_token(value)
    if not text:
        raise ValidationError(
            f"row {row_number}: channel_permission_status must be explicit (use unknown when unresolved)"
        )
    aliases = {
        "not_required": "not_required_confirmed",
        "not_required_confirmed": "not_required_confirmed",
        "not_required_evidenced": "not_required_confirmed",
        "permitted": "granted",
        "opted_out": "withdrawn",
        "opt_out": "withdrawn",
    }
    result = aliases.get(text, text)
    if result not in PERMISSION_STATUSES:
        raise ValidationError(
            f"row {row_number}: unsupported channel_permission_status {value!r}; "
            "use granted, not_required_confirmed, blocked, withdrawn, or unknown"
        )
    return result


def _parse_contact_value(value: str | None, *, field_name: str, row_number: int) -> str:
    text = canonical_text(value)
    if not text:
        raise ValidationError(f"row {row_number}: {field_name} must be explicit (use unknown)")
    return "unknown" if is_unknown(text) else text


DEFAULT_SERVICE_PACKAGES = (
    {
        "name": "Digital Identity Package",
        "description": (
            "Mobile-friendly digital storefront; business profile and contact information; "
            "WhatsApp contact integration; basic digital presence audit"
        ),
        "price_amount": 4999,
        "currency": "INR",
        "delivery_notes": "Initial configurable package; not proof of purchase or delivery.",
    },
    {
        "name": "Growth Suite",
        "description": (
            "Digital storefront and enhanced business profile; WhatsApp ordering or enquiry funnel; "
            "Google Business Profile optimization assistance; lead capture and conversion tracking"
        ),
        "price_amount": 12999,
        "currency": "INR",
        "delivery_notes": "Initial configurable package; not proof of purchase or delivery.",
    },
)


def _parse_active(value: bool | str | int) -> int:
    if isinstance(value, bool):
        return int(value)
    text = normalize_token(str(value))
    if text in {"1", "true", "yes", "y", "active", "enabled"}:
        return 1
    if text in {"0", "false", "no", "n", "inactive", "disabled"}:
        return 0
    raise ValidationError("active must be true or false")


def _validate_money_amount(value: int | str, *, field_name: str = "price_amount") -> int:
    try:
        amount = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name} must be a positive integer amount") from exc
    if amount <= 0:
        raise ValidationError(f"{field_name} must be a positive integer amount")
    return amount


def _validate_currency(value: str | None) -> str:
    currency = normalize_token(value or "INR").upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValidationError("currency must be a three-letter ISO currency code")
    return currency


def _validate_human_actor(value: str | None, *, field_name: str) -> str:
    actor = canonical_text(value)
    if not actor or normalize_token(actor) in _MACHINE_ACTOR_VALUES:
        raise ValidationError(f"{field_name} must be a non-empty human actor")
    return actor


def _parse_row(row: Mapping[str, str | None], *, row_number: int) -> ParsedLead:
    def required(name: str) -> str:
        value = canonical_text(row.get(name))
        if not value:
            raise ValidationError(f"row {row_number}: {name} is required")
        return value

    company_name = required("company_name")
    region = required("region")
    industry = required("industry")
    fit_reason = required("fit_reason")
    source_url = validate_source_url(required("source_url"))

    try:
        source_checked_at = parse_utc_timestamp(required("source_checked_at"), field_name="source_checked_at")
    except ValueError as exc:
        raise ValidationError(f"row {row_number}: {exc}") from exc

    website = canonical_text(row.get("website")) or None
    try:
        normalized_domain = normalize_domain(website)
    except ValidationError as exc:
        raise ValidationError(f"row {row_number}: {exc}") from exc

    verified_status = normalize_token(required("verified_status"))
    if not verified_status:
        raise ValidationError(f"row {row_number}: verified_status is required")

    contact_channel = _parse_contact_value(
        row.get("contact_channel"), field_name="contact_channel", row_number=row_number
    )
    channel_permission_status = _parse_permission(row.get("channel_permission_status"), row_number=row_number)
    contact_name = _parse_contact_value(row.get("contact_name"), field_name="contact_name", row_number=row_number)
    contact_role = _parse_contact_value(row.get("contact_role"), field_name="contact_role", row_number=row_number)

    confidence_raw = canonical_text(row.get("fit_confidence"))
    if confidence_raw and not is_unknown(confidence_raw):
        try:
            fit_confidence = float(confidence_raw)
        except ValueError as exc:
            raise ValidationError(f"row {row_number}: fit_confidence must be a number from 0 to 1") from exc
        if not 0 <= fit_confidence <= 1:
            raise ValidationError(f"row {row_number}: fit_confidence must be between 0 and 1")
    else:
        fit_confidence = None

    lead_status_raw = normalize_token(row.get("lead_status"))
    lead_status: str | None = lead_status_raw or None
    if lead_status and lead_status not in LEAD_STATUSES:
        raise ValidationError(f"row {row_number}: unsupported lead_status {row.get('lead_status')!r}")

    try:
        last_contact_at = parse_optional_utc_timestamp(row.get("last_contact_at"), field_name="last_contact_at")
        next_action_due = parse_optional_utc_timestamp(row.get("next_action_due"), field_name="next_action_due")
    except ValueError as exc:
        raise ValidationError(f"row {row_number}: {exc}") from exc

    next_action = canonical_text(row.get("next_action")) or None
    notes = canonical_text(row.get("notes"))

    return ParsedLead(
        company_name=company_name,
        website=website,
        normalized_domain=normalized_domain,
        region=region,
        industry=industry,
        source_url=source_url,
        source_checked_at=source_checked_at,
        verified_status=verified_status,
        contact_name=contact_name,
        contact_role=contact_role,
        contact_channel=contact_channel,
        channel_permission_status=channel_permission_status,
        fit_reason=fit_reason,
        fit_confidence=fit_confidence,
        lead_status=lead_status,
        last_contact_at=last_contact_at,
        next_action=next_action,
        next_action_due=next_action_due,
        opt_out=_parse_opt_out(row.get("opt_out"), row_number=row_number),
        notes=notes,
    )


class MediaHouseStore:
    """Small local SQLite store for the first safe revenue-engine milestone."""

    def __init__(self, database: str | os.PathLike[str] = "media_house.sqlite3") -> None:
        self.database = str(database)
        if self.database not in {":memory:", ""}:
            Path(self.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)
        self._seed_default_service_packages()
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "MediaHouseStore":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def _read_csv(self, path: str | os.PathLike[str], *, researcher: str) -> list[ParsedLead]:
        actor = canonical_text(researcher)
        if not actor:
            raise ValidationError("researcher is required")

        parsed_rows: list[ParsedLead] = []
        errors: list[str] = []
        try:
            file_handle: TextIO
            with open(path, "r", encoding="utf-8-sig", newline="") as file_handle:
                reader = csv.DictReader(file_handle)
                fieldnames = reader.fieldnames or []
                missing = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
                if missing:
                    raise ValidationError("CSV is missing required columns: " + ", ".join(missing))
                for row_number, row in enumerate(reader, start=2):
                    if None in row:
                        raise ValidationError(f"row {row_number}: CSV has more values than its header columns")
                    if row is None or all(not canonical_text(value) for value in row.values() if value is not None):
                        continue
                    try:
                        parsed_rows.append(_parse_row(row, row_number=row_number))
                    except ValidationError as exc:
                        errors.extend(exc.errors)
        except FileNotFoundError as exc:
            raise ValidationError(f"CSV file does not exist: {path}") from exc
        except csv.Error as exc:
            raise ValidationError(f"CSV could not be parsed: {exc}") from exc

        if errors:
            raise ValidationError("CSV validation failed", errors=errors)
        return parsed_rows

    def import_csv(self, path: str | os.PathLike[str], *, researcher: str) -> ImportSummary:
        """Validate all rows, then atomically upsert them and source evidence.

        Validation happens before the write transaction, so one invalid row
        cannot leave an earlier valid row partially imported.
        """

        rows = self._read_csv(path, researcher=researcher)
        actor = canonical_text(researcher)
        counts = {
            "companies_created": 0,
            "companies_updated": 0,
            "prospects_created": 0,
            "prospects_updated": 0,
            "source_evidence_added": 0,
        }
        with self._transaction():
            for lead in rows:
                company_id, company_created = self._upsert_company(lead)
                counts["companies_created" if company_created else "companies_updated"] += 1
                prospect_id, prospect_created = self._upsert_prospect(lead, company_id)
                counts["prospects_created" if prospect_created else "prospects_updated"] += 1
                evidence_cursor = self.connection.execute(
                    """
                    INSERT OR IGNORE INTO source_evidence
                        (prospect_id, source_url, observed_at, researcher,
                         verified_status, fit_reason, notes, recorded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        prospect_id,
                        lead.source_url,
                        lead.source_checked_at,
                        actor,
                        lead.verified_status,
                        lead.fit_reason,
                        lead.notes,
                        utc_now(),
                    ),
                )
                counts["source_evidence_added"] += int(evidence_cursor.rowcount > 0)

            # A second source can change the explainable evidence count.  Re-score
            # once after all rows so duplicate rows in the same file are stable.
            for lead in rows:
                company = self._find_company(lead)
                if company is not None:
                    self._refresh_score(company["id"])

        return ImportSummary(rows_read=len(rows), **counts)

    def _find_company(self, lead: ParsedLead) -> sqlite3.Row | None:
        domain_match = None
        if lead.normalized_domain:
            domain_match = self.connection.execute(
                "SELECT * FROM companies WHERE normalized_domain = ?",
                (lead.normalized_domain,),
            ).fetchone()
        # An OSM object URL identifies an object, unlike a general directory
        # page. Retain that identity across discovery runs even if its name,
        # website or operator-supplied geography label changes. Conflicts need
        # human review, not an implicit company merge.
        if lead.verified_status == "discovered" and re.fullmatch(
            r"https://www\.openstreetmap\.org/(node|way|relation)/[1-9][0-9]{0,18}", lead.source_url
        ):
            source_matches = self.connection.execute(
                """
                SELECT DISTINCT c.* FROM companies c
                JOIN prospects p ON p.company_id = c.id
                JOIN source_evidence s ON s.prospect_id = p.id
                WHERE s.source_url = ? LIMIT 2
                """,
                (lead.source_url,),
            ).fetchall()
            if len(source_matches) > 1 or (
                source_matches and domain_match is not None and source_matches[0]["id"] != domain_match["id"]
            ):
                raise ValidationError(f"conflicting company identity for {lead.source_url}; review before importing")
            if source_matches:
                return source_matches[0]
        if lead.normalized_domain:
            return domain_match
        return self.connection.execute(
            """
            SELECT * FROM companies
            WHERE normalized_name = ? AND normalized_region = ?
            ORDER BY id LIMIT 1
            """,
            (normalize_name(lead.company_name), normalize_region(lead.region)),
        ).fetchone()

    def _upsert_company(self, lead: ParsedLead) -> tuple[int, bool]:
        existing = self._find_company(lead)
        now = utc_now()
        if existing is None:
            cursor = self.connection.execute(
                """
                INSERT INTO companies
                    (name, normalized_name, website, normalized_domain, region,
                     normalized_region, industry, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lead.company_name,
                    normalize_name(lead.company_name),
                    lead.website,
                    lead.normalized_domain,
                    lead.region,
                    normalize_region(lead.region),
                    lead.industry,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid), True

        self.connection.execute(
            """
            UPDATE companies
            SET name = ?, normalized_name = ?, website = COALESCE(?, website),
                normalized_domain = COALESCE(?, normalized_domain),
                region = ?, normalized_region = ?, industry = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                lead.company_name,
                normalize_name(lead.company_name),
                lead.website,
                lead.normalized_domain,
                lead.region,
                normalize_region(lead.region),
                lead.industry,
                now,
                existing["id"],
            ),
        )
        return int(existing["id"]), False

    def _upsert_prospect(self, lead: ParsedLead, company_id: int) -> tuple[int, bool]:
        existing_cursor = self.connection.execute(
            "SELECT * FROM prospects WHERE company_id = ?", (company_id,)
        )
        existing = existing_cursor.fetchone()
        now = utc_now()
        if existing is None:
            score = calculate_fit_score(
                region=lead.region,
                industry=lead.industry,
                verified_status=lead.verified_status,
                fit_reason=lead.fit_reason,
                source_evidence_count=1,
                website_present=lead.normalized_domain is not None,
                confidence=lead.fit_confidence,
            )
            cursor = self.connection.execute(
                """
                INSERT INTO prospects
                    (company_id, verified_status, fit_reason, fit_confidence,
                     fit_score, score_reasons_json, contact_name, contact_role,
                     contact_channel, channel_permission_status, opt_out,
                     lead_status, last_contact_at, next_action, next_action_due,
                     notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    lead.verified_status,
                    lead.fit_reason,
                    lead.fit_confidence,
                    score.score,
                    json.dumps(list(score.reasons), ensure_ascii=False),
                    lead.contact_name,
                    lead.contact_role,
                    lead.contact_channel,
                    lead.channel_permission_status,
                    lead.opt_out,
                    lead.lead_status or "new",
                    lead.last_contact_at,
                    lead.next_action,
                    lead.next_action_due,
                    lead.notes,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid), True

        current_opt_out = existing["opt_out"]
        if current_opt_out == 1 or lead.opt_out == 1:
            merged_opt_out: int | None = 1
        elif lead.opt_out is None:
            merged_opt_out = current_opt_out
        else:
            merged_opt_out = lead.opt_out

        # For ordinary imports, an unresolved channel or permission remains
        # unresolved rather than silently satisfying a later source row. OSM
        # discovery is handled separately below because its missing fields are
        # not a contact-permission decision at all.
        if lead.verified_status == "discovered":
            # An OSM discovery row is additional source evidence, not a new
            # contact-permission decision.  Do not erase a human-maintained
            # channel or permission merely because this source did not expose
            # one.  The new row still stores its own unknown values in the
            # source-evidence notes.
            merged_channel = self._merge_discovery_channel(existing["contact_channel"], lead.contact_channel)
            merged_permission = self._merge_discovery_permission(
                existing["channel_permission_status"], lead.channel_permission_status
            )
        else:
            merged_channel = self._merge_contact_channel(existing["contact_channel"], lead.contact_channel)
            merged_permission = self._merge_permission(existing["channel_permission_status"], lead.channel_permission_status)
        incoming_status = lead.lead_status
        merged_status = self._merge_lead_status(existing["lead_status"], incoming_status)
        merged_notes = self._merge_notes(existing["notes"], lead.notes)
        merged_verified_status = self._merge_verified_status(existing["verified_status"], lead.verified_status)
        if (
            lead.verified_status == "discovered"
            and normalize_token(existing["verified_status"]) not in {"", "unknown", "discovered"}
        ):
            # Discovery must not downgrade a verified record or replace a
            # conversation-backed fit rationale with a directory hypothesis.
            merged_fit_reason = existing["fit_reason"]
        else:
            merged_fit_reason = lead.fit_reason
        self.connection.execute(
            """
            UPDATE prospects
            SET verified_status = ?, fit_reason = ?,
                fit_confidence = COALESCE(?, fit_confidence),
                contact_name = ?, contact_role = ?, contact_channel = ?,
                channel_permission_status = ?, opt_out = ?, lead_status = ?,
                last_contact_at = COALESCE(?, last_contact_at),
                next_action = COALESCE(?, next_action),
                next_action_due = COALESCE(?, next_action_due),
                notes = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                merged_verified_status,
                merged_fit_reason,
                lead.fit_confidence,
                self._merge_unknown_text(existing["contact_name"], lead.contact_name),
                self._merge_unknown_text(existing["contact_role"], lead.contact_role),
                merged_channel,
                merged_permission,
                merged_opt_out,
                merged_status,
                lead.last_contact_at,
                lead.next_action,
                lead.next_action_due,
                merged_notes,
                now,
                existing["id"],
            ),
        )
        return int(existing["id"]), False

    @staticmethod
    def _merge_unknown_text(existing: str | None, incoming: str | None) -> str:
        old = canonical_text(existing) or "unknown"
        new = canonical_text(incoming) or "unknown"
        if is_unknown(new) and not is_unknown(old):
            return old
        return "unknown" if is_unknown(new) else new

    @staticmethod
    def _merge_contact_channel(existing: str | None, incoming: str | None) -> str:
        new = canonical_text(incoming) or "unknown"
        return "unknown" if is_unknown(new) else new

    @staticmethod
    def _merge_discovery_channel(existing: str | None, incoming: str | None) -> str:
        """Keep an existing known channel when discovery has no channel fact."""

        old = canonical_text(existing) or "unknown"
        new = canonical_text(incoming) or "unknown"
        if not is_unknown(old):
            return old
        return "unknown" if is_unknown(new) else new

    @staticmethod
    def _merge_permission(existing: str | None, incoming: str) -> str:
        old = normalize_token(existing)
        new = normalize_token(incoming)
        # An explicit block/withdrawal is never weakened by a later unknown.
        if old in {"blocked", "withdrawn"} and new == "unknown":
            return old
        return new or "unknown"

    @staticmethod
    def _merge_discovery_permission(existing: str | None, incoming: str) -> str:
        """Do not treat missing source contact data as a permission change."""

        old = normalize_token(existing)
        new = normalize_token(incoming)
        if old and old != "unknown":
            return old
        return new or "unknown"

    @staticmethod
    def _merge_verified_status(existing: str | None, incoming: str) -> str:
        """A discovery observation cannot downgrade a stronger existing status."""

        old = normalize_token(existing)
        new = normalize_token(incoming)
        if new == "discovered" and old not in {"", "unknown", "discovered"}:
            return canonical_text(existing) or incoming
        return incoming

    @staticmethod
    def _merge_lead_status(existing: str, incoming: str | None) -> str:
        if not incoming:
            return existing
        if incoming == "new" and existing not in {"new", "unknown"}:
            return existing
        return incoming

    @staticmethod
    def _merge_notes(existing: str | None, incoming: str) -> str:
        old = canonical_text(existing)
        new = canonical_text(incoming)
        if not new or new == old:
            return old
        return f"{old}\n{new}" if old else new

    def _refresh_score(self, company_id: int) -> None:
        row = self.connection.execute(
            """
            SELECT c.region, c.industry, c.website, p.*,
                   (SELECT COUNT(*) FROM source_evidence s WHERE s.prospect_id = p.id)
                       AS source_evidence_count
            FROM companies c JOIN prospects p ON p.company_id = c.id
            WHERE c.id = ?
            """,
            (company_id,),
        ).fetchone()
        if row is None:
            return
        score = calculate_fit_score(
            region=row["region"],
            industry=row["industry"],
            verified_status=row["verified_status"],
            fit_reason=row["fit_reason"],
            source_evidence_count=row["source_evidence_count"],
            website_present=bool(row["website"]),
            confidence=row["fit_confidence"],
        )
        self.connection.execute(
            "UPDATE prospects SET fit_score = ?, score_reasons_json = ? WHERE id = ?",
            (score.score, json.dumps(list(score.reasons), ensure_ascii=False), row["id"]),
        )

    def get_prospect(self, prospect_id: int) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT p.*, c.name AS company_name, c.website, c.normalized_domain,
                   c.region, c.industry,
                   (SELECT COUNT(*) FROM source_evidence s WHERE s.prospect_id = p.id)
                       AS source_evidence_count
            FROM prospects p JOIN companies c ON c.id = p.company_id
            WHERE p.id = ?
            """,
            (prospect_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"prospect {prospect_id} was not found")
        result = _row_dict(row)
        result["score_reasons"] = json.loads(result.pop("score_reasons_json"))
        result["fit_confidence"] = (
            float(result["fit_confidence"]) if result["fit_confidence"] is not None else None
        )
        result["opt_out"] = None if result["opt_out"] is None else bool(result["opt_out"])
        result["score_disclaimer"] = SCORING_DISCLAIMER
        return result

    def list_prospects(self, *, lead_status: str | None = None) -> list[dict[str, Any]]:
        if lead_status:
            rows = self.connection.execute(
                "SELECT id FROM prospects WHERE lead_status = ? ORDER BY id", (normalize_token(lead_status),)
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT id FROM prospects ORDER BY id").fetchall()
        return [self.get_prospect(int(row["id"])) for row in rows]

    def list_source_evidence(self, prospect_id: int) -> list[dict[str, Any]]:
        # This lookup also checks that the prospect exists and keeps the public
        # method's failure mode consistent with get_prospect.
        self.get_prospect(prospect_id)
        rows = self.connection.execute(
            """
            SELECT id, prospect_id, source_url, observed_at, researcher,
                   verified_status, fit_reason, notes, recorded_at
            FROM source_evidence
            WHERE prospect_id = ?
            ORDER BY observed_at, id
            """,
            (prospect_id,),
        ).fetchall()
        return [_row_dict(row) for row in rows]

    def record_meeting(
        self,
        prospect_id: int,
        *,
        meeting_at: str,
        notes: str,
        notes_permission: str,
        recording_policy: str,
        actor: str,
    ) -> dict[str, Any]:
        """Record human-authored notes from a completed customer meeting.

        This local milestone stores meeting metadata only.  It never records
        audio, invokes a calendar/provider, or treats notes as approved scope.
        Early lead states advance to ``discovery_complete``; later states are
        preserved so a meeting cannot downgrade proposal or delivery work.
        """

        human_actor = _validate_human_actor(actor, field_name="actor")
        try:
            normalized_meeting_at = parse_utc_timestamp(meeting_at, field_name="meeting_at")
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValidationError(str(exc)) from exc
        meeting_notes = canonical_text(notes)
        if not meeting_notes:
            raise ValidationError("meeting notes are required")
        notes_permission_value = normalize_token(notes_permission)
        if notes_permission_value not in MEETING_NOTES_PERMISSIONS:
            raise ValidationError(
                "notes_permission must be one of: "
                + ", ".join(sorted(MEETING_NOTES_PERMISSIONS))
            )
        policy = normalize_token(recording_policy)
        if policy not in MEETING_RECORDING_POLICIES:
            raise ValidationError(
                "recording_policy must be one of: "
                + ", ".join(sorted(MEETING_RECORDING_POLICIES))
            )

        self.get_prospect(prospect_id)
        created_at = utc_now()
        with self._transaction():
            cursor = self.connection.execute(
                """
                INSERT INTO meetings
                    (prospect_id, meeting_at, notes, notes_permission, recording_policy, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prospect_id,
                    normalized_meeting_at,
                    meeting_notes,
                    notes_permission_value,
                    policy,
                    human_actor,
                    created_at,
                ),
            )
            meeting_id = int(cursor.lastrowid)

            current = self.connection.execute(
                "SELECT lead_status FROM prospects WHERE id = ?", (prospect_id,)
            ).fetchone()
            if current is None:  # pragma: no cover - get_prospect checked this before the transaction.
                raise NotFoundError(f"prospect {prospect_id} was not found")
            current_status = normalize_token(current["lead_status"])
            next_status = self._lead_status_after_meeting(current_status)
            if next_status != current_status:
                self.connection.execute(
                    "UPDATE prospects SET lead_status = ?, updated_at = ? WHERE id = ?",
                    (next_status, created_at, prospect_id),
                )

            self._record_audit(
                actor=human_actor,
                action="meeting_recorded",
                object_type="meeting",
                object_id=meeting_id,
                metadata={
                    "meeting_id": meeting_id,
                    "prospect_id": prospect_id,
                    "meeting_at": normalized_meeting_at,
                    "notes_permission": notes_permission_value,
                    "recording_policy": policy,
                    "lead_status_before": current_status,
                    "lead_status_after": next_status,
                    "notes_recorded": True,
                },
            )
        return self.get_meeting(meeting_id)

    @staticmethod
    def _lead_status_after_meeting(current_status: str | None) -> str:
        normalized = normalize_token(current_status)
        if normalized in _MEETING_ADVANCEABLE_LEAD_STATUSES:
            return "discovery_complete"
        return normalized or "discovery_complete"

    def get_meeting(self, meeting_id: int) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT m.*, p.lead_status, c.name AS company_name
            FROM meetings m
            JOIN prospects p ON p.id = m.prospect_id
            JOIN companies c ON c.id = p.company_id
            WHERE m.id = ?
            """,
            (meeting_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"meeting {meeting_id} was not found")
        return _row_dict(row)

    def list_meetings(self, *, prospect_id: int | None = None) -> list[dict[str, Any]]:
        if prospect_id is not None:
            self.get_prospect(prospect_id)
            rows = self.connection.execute(
                "SELECT id FROM meetings WHERE prospect_id = ? ORDER BY meeting_at, id",
                (prospect_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT id FROM meetings ORDER BY meeting_at, id").fetchall()
        return [self.get_meeting(int(row["id"])) for row in rows]

    def capture_requirements(
        self,
        meeting_id: int,
        document: Mapping[str, object],
        *,
        actor: str,
        status: str | None = None,
        supersedes_document_id: int | None = None,
    ) -> dict[str, Any]:
        """Capture a versioned requirements draft tied to a completed meeting."""

        human_actor = _validate_human_actor(actor, field_name="actor")
        meeting = self.get_meeting(meeting_id)
        prospect = self.get_prospect(int(meeting["prospect_id"]))
        requested_status = status if status is not None else document.get("status", "DRAFT")
        if not isinstance(requested_status, str):
            raise ValidationError("requirements status must be a string")
        requested_status = canonical_text(requested_status)
        if document.get("status") is not None and document.get("status") != requested_status:
            raise ValidationError("document status and requested status do not match")
        if "approval" in document and document["approval"] is not None:
            raise ValidationError("requirements capture cannot include approval metadata")
        if "meeting_id" in document and document["meeting_id"] != meeting_id:
            raise ValidationError("document meeting_id does not match the completed meeting")
        if "prospect_id" in document and document["prospect_id"] != int(meeting["prospect_id"]):
            raise ValidationError("document prospect_id does not match the completed meeting's prospect")

        client = document.get("client")
        if isinstance(client, dict) and isinstance(client.get("business_name"), str):
            if normalize_name(client["business_name"]) != normalize_name(prospect["company_name"]):
                raise ValidationError("client.business_name must match the linked prospect")

        latest = self.connection.execute(
            "SELECT COALESCE(MAX(document_version), 0) AS version FROM requirements_documents WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchone()
        next_version = int(latest["version"]) + 1
        supplied_version = document.get("document_version")
        if supplied_version is not None and supplied_version != next_version:
            raise ValidationError(f"document_version must be the next version ({next_version})")

        approved = self.connection.execute(
            "SELECT id, document_version FROM requirements_documents WHERE meeting_id = ? AND status = 'APPROVED' "
            "ORDER BY document_version DESC LIMIT 1",
            (meeting_id,),
        ).fetchone()
        if approved is not None and supersedes_document_id is None:
            raise ValidationError("an approved baseline requires an explicit superseding version")
        if supersedes_document_id is not None:
            if approved is None or int(approved["id"]) != supersedes_document_id:
                raise ValidationError("supersedes_document_id must identify the current approved baseline")

        prepared = dict(document)
        prepared["meeting_id"] = meeting_id
        prepared["prospect_id"] = int(meeting["prospect_id"])
        prepared["document_version"] = next_version
        prepared["status"] = requested_status
        prepared["approval"] = None
        validated = requirements_contract.validate_document(
            prepared,
            expected_meeting_id=meeting_id,
            expected_prospect_id=int(meeting["prospect_id"]),
            require_ready_content=requested_status == "READY_FOR_REVIEW",
            allow_approved=False,
        )
        digest = requirements_contract.content_hash(validated)
        created_at = utc_now()
        with self._transaction():
            cursor = self.connection.execute(
                """
                INSERT INTO requirements_documents
                    (prospect_id, meeting_id, document_version, status, content_json,
                     content_hash, created_by, created_at, supersedes_document_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(meeting["prospect_id"]),
                    meeting_id,
                    next_version,
                    requested_status,
                    requirements_contract.canonical_json(validated),
                    digest,
                    human_actor,
                    created_at,
                    supersedes_document_id,
                ),
            )
            document_id = int(cursor.lastrowid)
            self._record_audit(
                actor=human_actor,
                action="requirements_created",
                object_type="requirements_document",
                object_id=document_id,
                metadata={
                    "document_id": document_id,
                    "meeting_id": meeting_id,
                    "prospect_id": int(meeting["prospect_id"]),
                    "document_version": next_version,
                    "status": requested_status,
                    "content_hash": digest,
                    "supersedes_document_id": supersedes_document_id,
                },
            )
            if supersedes_document_id is not None:
                self._record_audit(
                    actor=human_actor,
                    action="requirements_superseding_created",
                    object_type="requirements_document",
                    object_id=document_id,
                    metadata={
                        "document_id": document_id,
                        "supersedes_document_id": supersedes_document_id,
                        "document_version": next_version,
                    },
                )
        return self.get_requirements_document(document_id)

    def get_requirements_document(self, document_id: int) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT r.*, m.meeting_at, c.name AS company_name
            FROM requirements_documents r
            JOIN meetings m ON m.id = r.meeting_id
            JOIN prospects p ON p.id = r.prospect_id
            JOIN companies c ON c.id = p.company_id
            WHERE r.id = ?
            """,
            (document_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"requirements document {document_id} was not found")
        result = _row_dict(row)
        try:
            result["document"] = json.loads(result.pop("content_json"))
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"requirements document {document_id} contains invalid JSON") from exc
        return result

    def list_requirements_documents(
        self,
        *,
        prospect_id: int | None = None,
        meeting_id: int | None = None,
    ) -> list[dict[str, Any]]:
        if prospect_id is not None:
            self.get_prospect(prospect_id)
        if meeting_id is not None:
            meeting = self.get_meeting(meeting_id)
            if prospect_id is not None and int(meeting["prospect_id"]) != prospect_id:
                raise ValidationError("meeting_id does not belong to prospect_id")
        clauses: list[str] = []
        values: list[int] = []
        if prospect_id is not None:
            clauses.append("prospect_id = ?")
            values.append(prospect_id)
        if meeting_id is not None:
            clauses.append("meeting_id = ?")
            values.append(meeting_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.connection.execute(
            f"SELECT id FROM requirements_documents{where} ORDER BY meeting_id, document_version, id",
            tuple(values),
        ).fetchall()
        return [self.get_requirements_document(int(row["id"])) for row in rows]

    def export_requirements(
        self,
        document_id: int,
        destination: str | os.PathLike[str] | TextIO,
    ) -> dict[str, object]:
        """Export one schema-compatible document and record the local export."""

        record = self.get_requirements_document(document_id)
        document = requirements_contract.validate_document(
            record["document"],
            expected_meeting_id=int(record["meeting_id"]),
            expected_prospect_id=int(record["prospect_id"]),
            allow_approved=True,
        )
        digest = requirements_contract.content_hash(document)
        if digest != record["content_hash"]:
            raise ValidationError(f"requirements document {document_id} content hash does not match its stored version")
        text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        should_close = False
        if hasattr(destination, "write"):
            handle = destination  # type: ignore[assignment]
            destination_label = "-"
        else:
            path = Path(destination)  # type: ignore[arg-type]
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("w", encoding="utf-8")
            destination_label = str(path)
            should_close = True
        try:
            handle.write(text)
        finally:
            if should_close:
                handle.close()
        with self._transaction():
            self._record_audit(
                actor=record["created_by"],
                action="requirements_exported",
                object_type="requirements_document",
                object_id=document_id,
                metadata={
                    "document_id": document_id,
                    "document_version": record["document_version"],
                    "status": record["status"],
                    "content_hash": digest,
                    "destination": destination_label,
                },
            )
        return document

    def approve_requirements(
        self,
        document_id: int,
        *,
        actor: str,
        approved_at: str | None = None,
    ) -> dict[str, Any]:
        """Explicitly approve a READY_FOR_REVIEW baseline without mutation."""

        try:
            human_actor = _validate_human_actor(actor, field_name="approver")
        except ValidationError as exc:
            raise ApprovalError(str(exc)) from exc
        record = self.get_requirements_document(document_id)
        if record["status"] != "READY_FOR_REVIEW":
            raise ApprovalError("only READY_FOR_REVIEW requirements can be approved")
        document = requirements_contract.validate_document(
            record["document"],
            expected_meeting_id=int(record["meeting_id"]),
            expected_prospect_id=int(record["prospect_id"]),
            require_ready_content=True,
            allow_approved=False,
        )
        digest = requirements_contract.content_hash(document)
        if digest != record["content_hash"]:
            raise ApprovalError("requirements document content hash does not match its stored version")
        try:
            timestamp = (
                requirements_contract.normalize_approval_timestamp(approved_at)
                if approved_at is not None
                else utc_now()
            )
            approved_document = requirements_contract.approve_document(
                document,
                actor=human_actor,
                approved_at=timestamp,
                scope_hash=digest,
            )
        except ValidationError as exc:
            raise ApprovalError(str(exc)) from exc

        with self._transaction():
            self.connection.execute(
                """
                UPDATE requirements_documents
                SET status = 'APPROVED', content_json = ?, approved_by = ?,
                    approved_at = ?, approved_hash = ?
                WHERE id = ?
                """,
                (
                    requirements_contract.canonical_json(approved_document),
                    human_actor,
                    timestamp,
                    digest,
                    document_id,
                ),
            )
            supersedes_id = record["supersedes_document_id"]
            if supersedes_id is not None:
                previous = self.get_requirements_document(int(supersedes_id))
                if previous["status"] != "APPROVED":
                    raise ApprovalError("superseded requirements document is not an approved baseline")
                previous_document = dict(previous["document"])
                previous_document["status"] = "SUPERSEDED"
                previous_document = requirements_contract.validate_document(
                    previous_document,
                    expected_meeting_id=int(previous["meeting_id"]),
                    expected_prospect_id=int(previous["prospect_id"]),
                    allow_approved=True,
                )
                self.connection.execute(
                    "UPDATE requirements_documents SET status = 'SUPERSEDED', content_json = ? WHERE id = ?",
                    (requirements_contract.canonical_json(previous_document), int(supersedes_id)),
                )
                self._record_audit(
                    actor=human_actor,
                    action="requirements_superseded",
                    object_type="requirements_document",
                    object_id=int(supersedes_id),
                    metadata={
                        "superseded_document_id": int(supersedes_id),
                        "superseding_document_id": document_id,
                        "superseding_version": record["document_version"],
                    },
                )
            self._record_audit(
                actor=human_actor,
                action="requirements_approved",
                object_type="requirements_document",
                object_id=document_id,
                metadata={
                    "document_id": document_id,
                    "document_version": record["document_version"],
                    "approved_by": human_actor,
                    "approved_at": timestamp,
                    "scope_version_hash": digest,
                },
            )
        return self.get_requirements_document(document_id)

    def _seed_default_service_packages(self) -> None:
        count = self.connection.execute("SELECT COUNT(*) AS count FROM service_packages").fetchone()["count"]
        if count:
            return
        now = utc_now()
        self.connection.executemany(
            """
            INSERT INTO service_packages
                (name, description, price_amount, currency, active, delivery_notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?)
            """,
            [
                (
                    item["name"],
                    item["description"],
                    item["price_amount"],
                    item["currency"],
                    item["delivery_notes"],
                    now,
                    now,
                )
                for item in DEFAULT_SERVICE_PACKAGES
            ],
        )

    def _record_audit(
        self,
        *,
        actor: str,
        action: str,
        object_type: str,
        object_id: int | None,
        metadata: Mapping[str, Any],
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO audit_events
                (actor, action, object_type, object_id, occurred_at, metadata_json, send_performed)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
            (actor, action, object_type, object_id, utc_now(), json.dumps(dict(metadata), sort_keys=True)),
        )

    def create_service_package(
        self,
        *,
        name: str,
        description: str,
        price_amount: int | str,
        currency: str = "INR",
        active: bool | str | int = True,
        delivery_notes: str = "",
        actor: str,
    ) -> dict[str, Any]:
        package_name = canonical_text(name)
        if not package_name:
            raise ValidationError("package name is required")
        package_description = canonical_text(description)
        if not package_description:
            raise ValidationError("package description is required")
        amount = _validate_money_amount(price_amount)
        package_currency = _validate_currency(currency)
        active_flag = _parse_active(active)
        human_actor = _validate_human_actor(actor, field_name="actor")
        now = utc_now()
        with self._transaction():
            try:
                cursor = self.connection.execute(
                    """
                    INSERT INTO service_packages
                        (name, description, price_amount, currency, active, delivery_notes, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        package_name,
                        package_description,
                        amount,
                        package_currency,
                        active_flag,
                        canonical_text(delivery_notes),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValidationError(f"service package {package_name!r} already exists") from exc
            package_id = int(cursor.lastrowid)
            self._record_audit(
                actor=human_actor,
                action="service_package_created",
                object_type="service_package",
                object_id=package_id,
                metadata={"package_id": package_id, "active": bool(active_flag)},
            )
        return self.get_service_package(package_id)

    def update_service_package(
        self,
        package_id: int,
        *,
        actor: str,
        name: str | None = None,
        description: str | None = None,
        price_amount: int | str | None = None,
        currency: str | None = None,
        active: bool | str | int | None = None,
        delivery_notes: str | None = None,
    ) -> dict[str, Any]:
        human_actor = _validate_human_actor(actor, field_name="actor")
        current = self.get_service_package(package_id)
        values = {
            "name": current["name"] if name is None else canonical_text(name),
            "description": current["description"] if description is None else canonical_text(description),
            "price_amount": current["price_amount"] if price_amount is None else _validate_money_amount(price_amount),
            "currency": current["currency"] if currency is None else _validate_currency(currency),
            "active": int(current["active"]) if active is None else _parse_active(active),
            "delivery_notes": current["delivery_notes"] if delivery_notes is None else canonical_text(delivery_notes),
        }
        if not values["name"]:
            raise ValidationError("package name is required")
        if not values["description"]:
            raise ValidationError("package description is required")
        now = utc_now()
        with self._transaction():
            try:
                self.connection.execute(
                    """
                    UPDATE service_packages
                    SET name = ?, description = ?, price_amount = ?, currency = ?,
                        active = ?, delivery_notes = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        values["name"],
                        values["description"],
                        values["price_amount"],
                        values["currency"],
                        values["active"],
                        values["delivery_notes"],
                        now,
                        package_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValidationError(f"service package {values['name']!r} already exists") from exc
            self._record_audit(
                actor=human_actor,
                action="service_package_updated",
                object_type="service_package",
                object_id=package_id,
                metadata={"package_id": package_id, "active": bool(values["active"])},
            )
        return self.get_service_package(package_id)

    def get_service_package(self, package_id: int) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM service_packages WHERE id = ?", (package_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"service package {package_id} was not found")
        result = _row_dict(row)
        result["active"] = bool(result["active"])
        return result

    def list_service_packages(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        if include_inactive:
            rows = self.connection.execute("SELECT id FROM service_packages ORDER BY id").fetchall()
        else:
            rows = self.connection.execute("SELECT id FROM service_packages WHERE active = 1 ORDER BY id").fetchall()
        return [self.get_service_package(int(row["id"])) for row in rows]

    def create_order_request(
        self,
        prospect_id: int,
        package_id: int,
        *,
        requested_by: str,
        notes: str = "",
    ) -> dict[str, Any]:
        actor = _validate_human_actor(requested_by, field_name="requested_by")
        self.get_prospect(prospect_id)
        package = self.get_service_package(package_id)
        if not package["active"]:
            raise ValidationError(f"service package {package_id} is inactive")
        now = utc_now()
        with self._transaction():
            cursor = self.connection.execute(
                """
                INSERT INTO order_requests
                    (prospect_id, package_id, package_name, amount, currency, status,
                     requested_by, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'REQUESTED', ?, ?, ?, ?)
                """,
                (
                    prospect_id,
                    package_id,
                    package["name"],
                    package["price_amount"],
                    package["currency"],
                    actor,
                    canonical_text(notes),
                    now,
                    now,
                ),
            )
            order_id = int(cursor.lastrowid)
            self._record_audit(
                actor=actor,
                action="order_request_created",
                object_type="order_request",
                object_id=order_id,
                metadata={
                    "order_id": order_id,
                    "prospect_id": prospect_id,
                    "package_id": package_id,
                    "amount": package["price_amount"],
                    "currency": package["currency"],
                    "payment_verified": False,
                },
            )
        return self.get_order_request(order_id)

    def get_order_request(self, order_id: int) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT o.*, c.name AS company_name
            FROM order_requests o
            JOIN prospects p ON p.id = o.prospect_id
            JOIN companies c ON c.id = p.company_id
            WHERE o.id = ?
            """,
            (order_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"order request {order_id} was not found")
        return _row_dict(row)

    def list_order_requests(self, *, prospect_id: int | None = None) -> list[dict[str, Any]]:
        if prospect_id is not None:
            self.get_prospect(prospect_id)
            rows = self.connection.execute(
                "SELECT id FROM order_requests WHERE prospect_id = ? ORDER BY id", (prospect_id,)
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT id FROM order_requests ORDER BY id").fetchall()
        return [self.get_order_request(int(row["id"])) for row in rows]

    def create_payment_request(
        self,
        order_id: int,
        *,
        created_by: str,
        provider_reference: str | None = None,
        payment_link: str | None = None,
        upi_uri: str | None = None,
    ) -> dict[str, Any]:
        actor = _validate_human_actor(created_by, field_name="created_by")
        order = self.get_order_request(order_id)
        if order["status"] != "REQUESTED":
            raise ValidationError(f"order request {order_id} is not payable")
        with self._transaction():
            cursor = self.connection.execute(
                """
                INSERT INTO payment_requests
                    (order_id, amount, currency, status, provider_reference, payment_link,
                     upi_uri, provider_call_performed, created_by, created_at)
                VALUES (?, ?, ?, 'PENDING_PROVIDER', ?, ?, ?, 0, ?, ?)
                """,
                (
                    order_id,
                    order["amount"],
                    order["currency"],
                    canonical_text(provider_reference) or None,
                    canonical_text(payment_link) or None,
                    canonical_text(upi_uri) or None,
                    actor,
                    utc_now(),
                ),
            )
            payment_request_id = int(cursor.lastrowid)
            self._record_audit(
                actor=actor,
                action="payment_request_created",
                object_type="payment_request",
                object_id=payment_request_id,
                metadata={
                    "payment_request_id": payment_request_id,
                    "order_id": order_id,
                    "status": "PENDING_PROVIDER",
                    "provider_call_performed": False,
                    "payment_verified": False,
                },
            )
        return self.get_payment_request(payment_request_id)

    def get_payment_request(self, payment_request_id: int) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM payment_requests WHERE id = ?", (payment_request_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"payment request {payment_request_id} was not found")
        result = _row_dict(row)
        result["provider_call_performed"] = bool(result["provider_call_performed"])
        result["payment_verified"] = False
        return result

    def list_payment_requests(self, *, order_id: int | None = None) -> list[dict[str, Any]]:
        if order_id is not None:
            self.get_order_request(order_id)
            rows = self.connection.execute(
                "SELECT id FROM payment_requests WHERE order_id = ? ORDER BY id", (order_id,)
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT id FROM payment_requests ORDER BY id").fetchall()
        return [self.get_payment_request(int(row["id"])) for row in rows]

    def create_outreach_draft(
        self,
        prospect_id: int,
        *,
        channel: str | None = None,
        content: str,
        created_by: str,
        subject: str = "",
    ) -> dict[str, Any]:
        """Create a draft that remains PENDING_APPROVAL and cannot send."""

        actor = canonical_text(created_by)
        if not actor:
            raise ValidationError("created_by is required")
        body = canonical_text(content)
        if not body:
            raise ValidationError("draft content is required")
        lead = self.get_prospect(prospect_id)
        draft_channel = normalize_token(channel) if channel is not None else normalize_token(lead["contact_channel"])
        if not draft_channel:
            draft_channel = "unknown"
        with self._transaction():
            cursor = self.connection.execute(
                """
                INSERT INTO outreach_drafts
                    (prospect_id, channel, subject, content, status, created_by,
                     created_at, send_performed)
                VALUES (?, ?, ?, ?, 'PENDING_APPROVAL', ?, ?, 0)
                """,
                (prospect_id, draft_channel, canonical_text(subject), body, actor, utc_now()),
            )
        return self.get_draft(int(cursor.lastrowid))

    def get_draft(self, draft_id: int) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT d.*, p.lead_status, p.opt_out, p.channel_permission_status,
                   p.contact_channel, c.name AS company_name
            FROM outreach_drafts d
            JOIN prospects p ON p.id = d.prospect_id
            JOIN companies c ON c.id = p.company_id
            WHERE d.id = ?
            """,
            (draft_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"outreach draft {draft_id} was not found")
        result = _row_dict(row)
        result["send_performed"] = bool(result["send_performed"])
        result["opt_out"] = None if result["opt_out"] is None else bool(result["opt_out"])
        return result

    def list_drafts(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT id FROM outreach_drafts ORDER BY id").fetchall()
        return [self.get_draft(int(row["id"])) for row in rows]

    def approve_draft(self, draft_id: int, *, human_actor: str) -> dict[str, Any]:
        """Approve a draft without sending it or invoking any provider.

        The human actor is an explicit local attestation; this milestone has no
        authentication system.  Known automation labels and blank actors are
        rejected so a caller cannot omit the human gate accidentally.
        """

        actor = canonical_text(human_actor)
        if not actor or normalize_token(actor) in _MACHINE_ACTOR_VALUES:
            raise ApprovalError("a non-empty human actor is required for approval")

        with self._transaction():
            row = self.connection.execute(
                """
                SELECT d.*, p.lead_status, p.opt_out, p.channel_permission_status,
                       p.contact_channel, p.id AS prospect_id
                FROM outreach_drafts d JOIN prospects p ON p.id = d.prospect_id
                WHERE d.id = ?
                """,
                (draft_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"outreach draft {draft_id} was not found")
            if row["approved_at"] is not None:
                raise ApprovalError(f"outreach draft {draft_id} has already been approved")

            problems = self._approval_problems(row)
            if problems:
                raise ApprovalError("approval blocked: " + "; ".join(problems))

            approved_at = utc_now()
            self.connection.execute(
                "UPDATE prospects SET lead_status = 'approved_to_contact', updated_at = ? WHERE id = ?",
                (approved_at, row["prospect_id"]),
            )
            self.connection.execute(
                """
                UPDATE outreach_drafts
                SET approved_by = ?, approved_at = ?
                WHERE id = ?
                """,
                (actor, approved_at, draft_id),
            )
            metadata = {
                "draft_id": draft_id,
                "prospect_id": row["prospect_id"],
                "channel": row["channel"],
                "approval_status": "approved_to_contact",
                "send_performed": False,
                "provider_call_performed": False,
            }
            self.connection.execute(
                """
                INSERT INTO audit_events
                    (actor, action, object_type, object_id, occurred_at, metadata_json, send_performed)
                VALUES (?, 'outreach_draft_approved', 'outreach_draft', ?, ?, ?, 0)
                """,
                (actor, draft_id, approved_at, json.dumps(metadata, sort_keys=True)),
            )

        result = self.get_draft(draft_id)
        result["send_performed"] = False
        result["audit"] = metadata
        return result

    @staticmethod
    def _approval_problems(row: Mapping[str, Any]) -> list[str]:
        problems: list[str] = []
        opt_out = row["opt_out"]
        if opt_out == 1:
            problems.append("lead is opted out")
        elif opt_out is None:
            problems.append("opt-out status is unknown")

        permission = normalize_token(row["channel_permission_status"])
        if permission not in {"granted", "not_required_confirmed"}:
            problems.append(f"channel permission is {permission or 'unknown'}")

        contact_channel = normalize_token(row["contact_channel"])
        if contact_channel in {
            "",
            "unknown",
            "not_known",
            "not_provided",
            "not_available",
            "unavailable",
            "na",
            "n_a",
        }:
            problems.append("contact channel is unknown")
        elif normalize_token(row["channel"]) != contact_channel:
            problems.append("draft channel does not match the permitted contact channel")

        lead_status = normalize_token(row["lead_status"])
        if lead_status in {"blocked", "withdrawn", "suppressed", "opted_out", "unknown"}:
            problems.append(f"lead status is {lead_status}")
        return problems

    def list_audit_events(self, *, action: str | None = None) -> list[dict[str, Any]]:
        if action:
            rows = self.connection.execute(
                "SELECT * FROM audit_events WHERE action = ? ORDER BY id", (action,)
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM audit_events ORDER BY id").fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = _row_dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            item["send_performed"] = bool(item["send_performed"])
            result.append(item)
        return result

    def export_csv(self, destination: str | os.PathLike[str] | TextIO) -> int:
        """Export the current lead view using the template plus explainability columns.

        One row represents one deduplicated prospect.  The latest observed
        source is shown in template columns; ``source_evidence_count`` and
        ``source_researcher`` make it clear that other evidence remains in the
        database and can be read with ``list_source_evidence``.
        """

        rows = self.connection.execute(
            """
            SELECT p.*, c.name AS company_name, c.website, c.region, c.industry,
                   e.source_url, e.observed_at AS source_checked_at,
                   e.researcher AS source_researcher,
                   (SELECT COUNT(*) FROM source_evidence se WHERE se.prospect_id = p.id)
                       AS source_evidence_count
            FROM prospects p
            JOIN companies c ON c.id = p.company_id
            JOIN source_evidence e ON e.id = (
                SELECT se2.id FROM source_evidence se2
                WHERE se2.prospect_id = p.id
                ORDER BY se2.observed_at DESC, se2.id DESC LIMIT 1
            )
            ORDER BY p.id
            """
        ).fetchall()

        should_close = False
        if hasattr(destination, "write"):
            file_handle = destination  # type: ignore[assignment]
        else:
            path = Path(destination)  # type: ignore[arg-type]
            path.parent.mkdir(parents=True, exist_ok=True)
            file_handle = path.open("w", encoding="utf-8", newline="")
            should_close = True
        try:
            writer = csv.DictWriter(file_handle, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                score_reasons = json.loads(row["score_reasons_json"])
                writer.writerow(
                    {
                        "company_name": row["company_name"],
                        "website": row["website"] or "",
                        "region": row["region"],
                        "industry": row["industry"],
                        "source_url": row["source_url"],
                        "source_checked_at": row["source_checked_at"],
                        "verified_status": row["verified_status"],
                        "contact_name": row["contact_name"],
                        "contact_role": row["contact_role"],
                        "contact_channel": row["contact_channel"],
                        "channel_permission_status": row["channel_permission_status"],
                        "fit_reason": row["fit_reason"],
                        "fit_confidence": "" if row["fit_confidence"] is None else row["fit_confidence"],
                        "lead_status": row["lead_status"],
                        "last_contact_at": row["last_contact_at"] or "",
                        "next_action": row["next_action"] or "",
                        "next_action_due": row["next_action_due"] or "",
                        "opt_out": "unknown" if row["opt_out"] is None else ("true" if row["opt_out"] else "false"),
                        "notes": row["notes"],
                        "source_researcher": row["source_researcher"],
                        "source_evidence_count": row["source_evidence_count"],
                        "fit_score": row["fit_score"],
                        "score_reasons": json.dumps(score_reasons, ensure_ascii=False),
                    }
                )
        finally:
            if should_close:
                file_handle.close()
        return len(rows)
