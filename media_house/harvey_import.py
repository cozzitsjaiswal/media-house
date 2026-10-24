"""Safe import of a reviewed Harvey prospect CSV export.

Harvey remains an optional external operator tool. This adapter consumes its local CSV
export only; it does not import Harvey's runtime, credentials, dashboard, network clients,
or outbound sender. Imported rows retain explicit unknowns where Media House cannot prove
consent, fit, region, or contact identity from the export.
"""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from datetime import date, datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from .errors import ValidationError
from .normalization import canonical_text, normalize_token, validate_source_url
from .store import CSV_COLUMNS, ImportSummary, MediaHouseStore
from .timeutil import parse_utc_timestamp


MAX_HARVEY_EXPORT_BYTES = 8 * 1024 * 1024
HARVEY_COLUMNS = frozenset(
    {
        "email",
        "first_name",
        "last_name",
        "company_name",
        "title",
        "website",
        "linkedin_url",
        "industry",
        "personalization",
        "email_status",
        "score",
        "seniority",
        "source",
        "status",
        "created_at",
    }
)
_HARVEY_OPT_OUT_STATUSES = frozenset({"opted_out", "opt_out", "unsubscribed", "suppressed"})


@dataclass(frozen=True)
class HarveyExportData:
    rows_read: int
    converted_rows: tuple[dict[str, str], ...]
    invalid_rows: tuple[dict[str, object], ...]

    @property
    def importable(self) -> bool:
        return not self.invalid_rows


def _read_export_text(path: Path) -> str:
    try:
        size = path.stat().st_size
        if size > MAX_HARVEY_EXPORT_BYTES:
            raise ValidationError(
                f"Harvey export exceeds the {MAX_HARVEY_EXPORT_BYTES}-byte safety limit"
            )
        payload = path.read_bytes()
    except (OSError, UnicodeError) as exc:
        raise ValidationError(f"Harvey export could not be read: {exc}") from exc
    if len(payload) > MAX_HARVEY_EXPORT_BYTES:
        raise ValidationError(f"Harvey export exceeds the {MAX_HARVEY_EXPORT_BYTES}-byte safety limit")
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError("Harvey export must be UTF-8 CSV") from exc


def _field(row: dict[str, str | None], name: str) -> str:
    value = row.get(name)
    return canonical_text(value)


def _convert_row(row: dict[str, str | None], *, row_number: int) -> dict[str, str]:
    company_name = _field(row, "company_name")
    if not company_name:
        raise ValidationError(f"row {row_number}: company_name is required")

    website = _field(row, "website")
    if not website:
        raise ValidationError(
            f"row {row_number}: website/source URL is required because Media House cannot fabricate evidence"
        )
    try:
        source_url = validate_source_url(website)
        created_at = _field(row, "created_at")
        if len(created_at) == 10:
            created_at = datetime.combine(date.fromisoformat(created_at), datetime.min.time(), tzinfo=timezone.utc).isoformat()
        source_checked_at = parse_utc_timestamp(created_at, field_name="created_at")
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"row {row_number}: {exc}") from exc
    email = _field(row, "email")
    first_name = _field(row, "first_name")
    last_name = _field(row, "last_name")
    contact_name = " ".join(part for part in (first_name, last_name) if part) or "unknown"
    email_status = normalize_token(_field(row, "email_status")) or "unknown"
    source = _field(row, "source") or "unknown"
    source_status = normalize_token(_field(row, "status")) or "unknown"
    opt_out = "true" if source_status in _HARVEY_OPT_OUT_STATUSES else "unknown"
    notes = json.dumps(
        {
            "source_system": "harvey",
            "source": source,
            "harvey_status": source_status,
            "harvey_email_status": email_status,
            "email_present": bool(email),
            "linkedin_url": _field(row, "linkedin_url") or "unknown",
            "seniority": _field(row, "seniority") or "unknown",
            "score": _field(row, "score") or "unknown",
            "personalization": _field(row, "personalization") or "unknown",
            "fit_boundary": "Harvey export evidence only; fit is not independently verified.",
            "permission_boundary": "Contact channel presence is not outreach permission.",
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return {
        "company_name": company_name,
        "website": website,
        "region": "unknown",
        "industry": _field(row, "industry") or "unknown",
        "source_url": source_url,
        "source_checked_at": source_checked_at,
        "verified_status": "harvey_export",
        "contact_name": contact_name,
        "contact_role": _field(row, "title") or "unknown",
        "contact_channel": "email" if email else "unknown",
        "channel_permission_status": "unknown",
        "fit_reason": "Imported from Harvey export; customer need and fit remain unverified.",
        "fit_confidence": "unknown",
        "lead_status": "new",
        "last_contact_at": "",
        "next_action": "Human-review imported Harvey evidence",
        "next_action_due": "",
        "opt_out": opt_out,
        "notes": notes,
    }


def read_harvey_export(path: str | os.PathLike[str]) -> HarveyExportData:
    """Parse and conservatively convert a Harvey CSV without opening SQLite."""

    source = Path(path)
    text = _read_export_text(source)
    try:
        reader = csv.DictReader(io.StringIO(text))
        fieldnames = reader.fieldnames or []
        if reader.fieldnames is None:
            raise ValidationError("Harvey export must contain a CSV header")
    except csv.Error as exc:
        raise ValidationError(f"Harvey export could not be parsed: {exc}") from exc
    if not fieldnames:
        raise ValidationError("Harvey export must contain a CSV header")
    missing = sorted(HARVEY_COLUMNS.difference(fieldnames))
    if missing:
        raise ValidationError("Harvey export is missing required columns: " + ", ".join(missing))

    converted: list[dict[str, str]] = []
    invalid: list[dict[str, object]] = []
    rows_read = 0
    for row_number, row in enumerate(reader, start=2):
        if row is None or all(not canonical_text(value) for value in row.values() if value is not None):
            continue
        rows_read += 1
        if None in row:
            invalid.append({"row": row_number, "error": "CSV has more values than its header columns"})
            continue
        try:
            converted.append(_convert_row(row, row_number=row_number))
        except (ValidationError, csv.Error) as exc:
            invalid.append({"row": row_number, "error": str(exc)})
    return HarveyExportData(rows_read, tuple(converted), tuple(invalid))


def write_media_house_csv(rows: tuple[dict[str, str], ...], destination: TextIO) -> None:
    writer = csv.DictWriter(destination, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)


def import_harvey_export(
    source: str | os.PathLike[str],
    database: str | os.PathLike[str],
    *,
    confirm_import: bool = False,
    researcher: str | None = None,
) -> dict[str, object]:
    """Preview or explicitly import a reviewed Harvey export atomically."""

    parsed = read_harvey_export(source)
    report: dict[str, object] = {
        "source": str(source),
        "rows_read": parsed.rows_read,
        "converted_rows": len(parsed.converted_rows),
        "invalid_rows": list(parsed.invalid_rows),
        "preview": list(parsed.converted_rows[:5]),
        "importable": parsed.importable,
        "confirm_import": confirm_import,
        "database_write_performed": False,
        "send_performed": False,
    }
    if not parsed.importable:
        return report
    if not confirm_import:
        return report
    if not isinstance(researcher, str) or not canonical_text(researcher):
        raise ValidationError("--researcher is required for a confirmed Harvey import")
    if not parsed.converted_rows:
        report["database_write_performed"] = False
        report["import_summary"] = ImportSummary(0, 0, 0, 0, 0, 0).as_dict()
        return report

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", suffix=".csv", delete=False) as handle:
            temp_path = Path(handle.name)
            write_media_house_csv(parsed.converted_rows, handle)
        with MediaHouseStore(database) as store:
            summary = store.import_csv(temp_path, researcher=researcher)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
    report["database_write_performed"] = True
    report["import_summary"] = summary.as_dict()
    return report
