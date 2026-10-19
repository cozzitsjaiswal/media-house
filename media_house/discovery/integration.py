"""Offline review and explicitly approved CSV imports; no source HTTP calls here."""

from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path

from ..errors import ValidationError
from ..normalization import canonical_text
from ..store import CSV_COLUMNS, MediaHouseStore, _parse_row
from .errors import DiscoveryError
from .overpass import DiscoveryRecord, DiscoveryResult, result_from_dict

FIT_REASON = "Public source category/location match only; customer need is unknown."


def record_to_csv_row(record: DiscoveryRecord) -> dict[str, str]:
    """Represent public source facts without inferring contact identity or need."""
    row = {column: "" for column in CSV_COLUMNS}
    if any(key in record.public_contacts for key in ("email", "contact:email")):
        contact_channel = "email"
    elif any(key in record.public_contacts for key in ("phone", "contact:phone", "mobile", "contact:mobile")):
        contact_channel = "phone"
    else:
        contact_channel = "unknown"
    row.update({
        "company_name": record.name,
        "website": record.website or "",
        "region": record.geography,
        "industry": "; ".join(f"{key}={value}" for key, value in sorted(record.category_tags.items())),
        "source_url": record.source_url,
        "source_checked_at": record.retrieved_at,
        "verified_status": "discovered",
        "contact_name": "unknown",
        "contact_role": "unknown",
        "contact_channel": contact_channel,
        "channel_permission_status": "unknown",
        "fit_reason": FIT_REASON,
        "fit_confidence": "unknown",
        "lead_status": "new",
        "next_action": "Human source verification and customer discovery; permission remains unknown",
        "opt_out": "unknown",
        "notes": json.dumps({
            "discovery": record.as_dict(),
            "geography_evidence": "Operator-supplied query label, not a verified business address",
            "evidence_limits": "OSM source-returned facts only; availability, permission, automation and customer need are unverified",
        }, ensure_ascii=False, sort_keys=True),
    })
    return row


def preview_import(result: DiscoveryResult, *, source_ids: list[str] | None = None) -> dict[str, object]:
    """Validate candidate CSV rows using the store's own pure row validator.

    No SQLite connection is opened. Counts of new/duplicate leads are actual
    writes (zero in a preview), not estimates. The database remains authoritative
    for deduplication and validates the entire selected CSV again on import.
    """
    result = result_from_dict(result.as_dict())
    available = {record.source_object_id for record in result.records}
    selected_ids = set(source_ids) if source_ids is not None else available
    if selected_ids - available:
        raise DiscoveryError("Unknown --source-id selection: " + ", ".join(sorted(selected_ids - available)), code="invalid_selection")
    selected = [record for record in result.records if record.source_object_id in selected_ids]
    rows = [record_to_csv_row(record) for record in selected]
    invalid_rows: list[dict[str, object]] = []
    for number, (record, row) in enumerate(zip(selected, rows), start=2):
        try:
            _parse_row(row, row_number=number)
        except (ValidationError, ValueError) as exc:
            invalid_rows.append({"source_object_id": record.source_object_id, "errors": getattr(exc, "errors", [str(exc)])})
    return {
        "preview_only": True,
        "importable": not invalid_rows,
        "database_deduplication_checked": False,
        "send_performed": False,
        "counts": {
            **result.counts,
            "selected": len(selected),
            "accepted": len(selected) - len(invalid_rows),
            "imported": 0,
            "new_leads": 0,
            "duplicate_records": 0,
            "invalid": result.counts["invalid"] + len(invalid_rows),
            "skipped": result.counts["skipped"] + len(result.records) - len(selected),
        },
        "skipped_reasons": result.skipped_reasons,
        "unselected": len(result.records) - len(selected),
        "invalid_rows": invalid_rows,
        "rows": rows,
    }


def import_discovery(
    result: DiscoveryResult,
    database: str | Path,
    *,
    confirm_import: bool = False,
    researcher: str | None = None,
    source_ids: list[str] | None = None,
) -> dict[str, object]:
    """Preview selected discovery records, or atomically import them when confirmed."""

    preview = preview_import(result, source_ids=source_ids)
    if not confirm_import:
        return preview
    actor = canonical_text(researcher)
    if not actor:
        raise DiscoveryError("--confirm-import requires a nonempty --researcher", code="researcher_required")
    if preview["invalid_rows"]:
        raise ValidationError(
            "Selected discovery records failed CSV validation; nothing imported. Review the preview or select valid --source-id values.",
            errors=[error for item in preview["invalid_rows"] for error in item["errors"]],
        )
    if not preview["rows"]:
        return {**preview, "preview_only": False}
    # The temporary CSV is an adapter boundary, not a second persistence path.
    # import_csv owns all validation, transaction, normalization, scoring and merges.
    with tempfile.TemporaryDirectory(prefix="media-house-discovery-") as directory:
        path = Path(directory) / "approved.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(preview["rows"])
        with MediaHouseStore(database) as store:
            summary = store.import_csv(path, researcher=actor)
    return {
        **preview,
        "preview_only": False,
        "database_deduplication_checked": True,
        "counts": {
            **preview["counts"],
            "imported": summary.rows_read,
            "accepted": summary.rows_read,
            "new_leads": summary.prospects_created,
            "duplicate_records": summary.rows_read - summary.prospects_created,
        },
        "store_import": summary.as_dict(),
    }
