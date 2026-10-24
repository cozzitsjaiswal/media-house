from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from media_house.cli import main
from media_house.errors import ValidationError

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

            parsed = read_harvey_export(path, reviewed_at="2026-10-24T12:34:56Z")

        self.assertEqual(15, len(HARVEY_COLUMNS))
        self.assertEqual(1, parsed.rows_read)
        self.assertTrue(parsed.importable)
        converted = parsed.converted_rows[0]
        self.assertEqual("unknown", converted["region"])
        self.assertEqual("unknown", converted["channel_permission_status"])
        self.assertEqual("email", converted["contact_channel"])
        self.assertEqual("harvey_export", converted["verified_status"])
        self.assertEqual("2026-10-24T12:34:56Z", converted["source_checked_at"])
        notes = json.loads(converted["notes"])
        self.assertTrue(notes["email_present"])
        self.assertEqual("2026-10-24T10:00:00Z", notes["harvey_created_at"])
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

    def test_preview_without_observation_time_keeps_it_unset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "harvey.csv"
            database = root / "media-house.sqlite3"
            for created_at in ("2026-10-24T10:00:00Z", "", "unknown"):
                with self.subTest(created_at=created_at):
                    write_harvey_csv(source, [valid_row(created_at=created_at)])
                    report = import_harvey_export(source, database)

                    converted = report["preview"][0]
                    self.assertEqual("", converted["source_checked_at"])
                    self.assertEqual(
                        created_at or "unknown", json.loads(converted["notes"])["harvey_created_at"]
                    )
                    self.assertFalse(database.exists())

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
                reviewed_at="2026-10-24T14:34:56+02:00",
            )

            self.assertTrue(report["database_write_performed"])
            self.assertEqual(2, report["import_summary"]["prospects_created"])
            with MediaHouseStore(database) as store:
                prospects = store.list_prospects()
                self.assertEqual(2, len(prospects))
                self.assertFalse(prospects[0]["opt_out"])
                self.assertTrue(prospects[1]["opt_out"])
                for prospect in prospects:
                    evidence = store.list_source_evidence(prospect["id"])
                    self.assertEqual("2026-10-24T12:34:56Z", evidence[0]["observed_at"])

    def test_confirmed_import_requires_researcher_before_database_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "harvey.csv"
            database = root / "media-house.sqlite3"
            write_harvey_csv(source, [valid_row()])

            with self.assertRaisesRegex(ValidationError, "researcher"):
                import_harvey_export(
                    source, database, confirm_import=True, reviewed_at="2026-10-24T12:34:56Z"
                )
            self.assertFalse(database.exists())

    def test_confirmed_import_requires_observation_time_before_database_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "harvey.csv"
            database = root / "media-house.sqlite3"
            write_harvey_csv(source, [valid_row()])

            for reviewed_at in (None, "", "not-a-timestamp"):
                with self.subTest(reviewed_at=reviewed_at):
                    with self.assertRaisesRegex(ValidationError, "reviewed.at"):
                        import_harvey_export(
                            source,
                            database,
                            confirm_import=True,
                            researcher="synthetic-reviewer",
                            reviewed_at=reviewed_at,
                        )
                    self.assertFalse(database.exists())

    def test_cli_requires_and_persists_explicit_observation_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "harvey.csv"
            database = root / "media-house.sqlite3"
            write_harvey_csv(source, [valid_row()])
            arguments = [
                "harvey-import", str(source), "--db", str(database),
                "--confirm-import", "--researcher", "synthetic-reviewer",
            ]
            error = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(error):
                self.assertEqual(2, main(arguments))
            self.assertIn("--reviewed-at is required", error.getvalue())
            self.assertFalse(database.exists())

            with redirect_stdout(io.StringIO()):
                self.assertEqual(0, main(arguments + ["--reviewed-at", "2026-10-24T12:34:56Z"]))
            with MediaHouseStore(database) as store:
                evidence = store.list_source_evidence(store.list_prospects()[0]["id"])
                self.assertEqual("2026-10-24T12:34:56Z", evidence[0]["observed_at"])

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

    def test_surplus_csv_values_are_reported_as_invalid_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "surplus.csv"
            path.write_text(
                ",".join(HARVEY_FIELDNAMES)
                + "\n"
                + ",".join([""] * len(HARVEY_FIELDNAMES) + ["surplus"])
                + "\n",
                encoding="utf-8",
            )

            parsed = read_harvey_export(path, reviewed_at="2026-10-24T12:34:56Z")

        self.assertFalse(parsed.importable)
        self.assertEqual(1, parsed.rows_read)
        self.assertEqual("CSV has more values than its header columns", parsed.invalid_rows[0]["error"])


if __name__ == "__main__":
    unittest.main()
