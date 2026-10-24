from __future__ import annotations

import csv
import json
import tempfile
import unittest

from media_house.errors import ValidationError
from pathlib import Path

from media_house.harvey_import import (
    HARVEY_COLUMNS,
    MAX_HARVEY_EXPORT_BYTES,
    import_harvey_export,
    read_harvey_export,
)
from media_house.store import MediaHouseStore


HARVEY_FIELDNAMES = [
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
]


def write_harvey_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HARVEY_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def valid_row(**overrides: str) -> dict[str, str]:
    row = {
        "email": "owner@example.test",
        "first_name": "Synthetic",
        "last_name": "Owner",
        "company_name": "Harvey Synthetic Co",
        "title": "Owner",
        "website": "https://harvey-synthetic.example",
        "linkedin_url": "",
        "industry": "retail",
        "personalization": "Public source note",
        "email_status": "verified",
        "score": "8",
        "seniority": "owner",
        "source": "synthetic_fixture",
        "status": "new",
        "created_at": "2026-10-24T10:00:00Z",
    }
    row.update(overrides)
    return row


class HarveyImportTests(unittest.TestCase):
    def test_preview_converts_only_explicit_evidence_and_keeps_permission_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "harvey.csv"
            write_harvey_csv(path, [valid_row()])

            parsed = read_harvey_export(path)

        self.assertEqual(15, len(HARVEY_COLUMNS))
        self.assertEqual(1, parsed.rows_read)
        self.assertTrue(parsed.importable)
        converted = parsed.converted_rows[0]
        self.assertEqual("unknown", converted["region"])
        self.assertEqual("unknown", converted["channel_permission_status"])
        self.assertEqual("email", converted["contact_channel"])
        self.assertEqual("harvey_export", converted["verified_status"])
        notes = json.loads(converted["notes"])
        self.assertTrue(notes["email_present"])
        self.assertNotIn("owner@example.test", converted["notes"])

    def test_invalid_rows_are_previewed_without_creating_a_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "harvey.csv"
            database = root / "media-house.sqlite3"
            write_harvey_csv(source, [valid_row(website="")])

            report = import_harvey_export(source, database)

            self.assertFalse(report["importable"])
            self.assertFalse(report["database_write_performed"])
            self.assertFalse(database.exists())
            self.assertEqual(1, len(report["invalid_rows"]))

    def test_confirmed_import_is_atomic_and_uses_explicit_researcher(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "harvey.csv"
            database = root / "media-house.sqlite3"
            write_harvey_csv(
                source,
                [
                    valid_row(),
                    valid_row(
                        company_name="Harvey Opted Out Co",
                        website="https://harvey-opted-out.example",
                        status="opted_out",
                    ),
                ],
            )

            report = import_harvey_export(
                source,
                database,
                confirm_import=True,
                researcher="synthetic-reviewer",
            )

            self.assertTrue(report["database_write_performed"])
            self.assertEqual(2, report["import_summary"]["prospects_created"])
            with MediaHouseStore(database) as store:
                prospects = store.list_prospects()
                self.assertEqual(2, len(prospects))
                self.assertFalse(prospects[0]["opt_out"])
                self.assertTrue(prospects[1]["opt_out"])

    def test_confirmed_import_requires_researcher_before_database_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "harvey.csv"
            database = root / "media-house.sqlite3"
            write_harvey_csv(source, [valid_row()])

            with self.assertRaises(ValidationError):
                import_harvey_export(source, database, confirm_import=True)
            self.assertFalse(database.exists())

    def test_oversized_export_is_rejected_before_csv_or_database_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "oversized.csv"
            source.write_bytes(b"x" * (MAX_HARVEY_EXPORT_BYTES + 1))
            database = root / "media-house.sqlite3"

            with self.assertRaises(ValidationError):
                import_harvey_export(source, database)
            self.assertFalse(database.exists())

    def test_malformed_or_duplicate_headers_are_rejected_safely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            malformed = root / "malformed.csv"
            malformed.write_text(
                ",".join(HARVEY_FIELDNAMES) + "\n\"unterminated,field\n",
                encoding="utf-8",
            )
            duplicate = root / "duplicate.csv"
            duplicate.write_text(
                ",".join(HARVEY_FIELDNAMES[:-1] + ["created_at", "created_at"]) + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValidationError):
                read_harvey_export(malformed)
            with self.assertRaises(ValidationError):
                read_harvey_export(duplicate)


if __name__ == "__main__":
    unittest.main()
