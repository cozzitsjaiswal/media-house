from __future__ import annotations

import io
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch
from urllib.parse import parse_qs, quote, unquote, urlsplit

import media_house.discovery.overpass as overpass_module
from media_house.cli import main
from media_house.finder import core as finder_core
from media_house.finder import FinderError, classify_saved_input, import_saved_input
from media_house.store import MediaHouseStore


# This is deliberately derived from the test file.  No test below creates,
# probes, or removes a database at this path.
WORKSPACE = Path(__file__).resolve().parents[1]
_CLASSIFICATIONS = {
    "NEW",
    "EXACT_DUPLICATE",
    "PROBABLE_DUPLICATE",
    "AMBIGUOUS_DUPLICATE",
    "CONFLICTING_IDENTITY",
    "MALFORMED",
}
_REPORT_KEYS = {
    "format",
    "schema_version",
    "mode",
    "preview_only",
    "database_write_performed",
    "network_performed",
    "provider_call_performed",
    "outreach_performed",
    "counts",
    "records",
    "all_new",
    "importable",
    "imported",
    "rejected",
}


class _FaultingConnection:
    """A guarded SQLite connection that fails on a later evidence insert."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        object.__setattr__(self, "_connection", connection)
        object.__setattr__(self, "_evidence_inserts", 0)

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        statement = str(args[0]) if args else ""
        normalized = " ".join(statement.upper().split())
        if normalized.startswith("INSERT") and "FINDER_SOURCE_EVIDENCE" in normalized:
            count = int(self._evidence_inserts) + 1
            object.__setattr__(self, "_evidence_inserts", count)
            if count > 1:
                raise sqlite3.IntegrityError("synthetic late evidence failure")
        return self._connection.execute(*args, **kwargs)

    def executemany(self, statement: str, sequence: Any) -> Any:
        normalized = " ".join(str(statement).upper().split())
        if normalized.startswith("INSERT") and "FINDER_SOURCE_EVIDENCE" in normalized:
            values = list(sequence)
            if values:
                self._connection.execute(statement, values[0])
                object.__setattr__(self, "_evidence_inserts", int(self._evidence_inserts) + 1)
            if len(values) > 1:
                raise sqlite3.IntegrityError("synthetic late evidence failure")
            return None
        return self._connection.executemany(statement, sequence)

    def __enter__(self) -> "_FaultingConnection":
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> Any:
        return self._connection.__exit__(exc_type, exc_value, traceback)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"_connection", "_evidence_inserts"}:
            object.__setattr__(self, name, value)
        else:
            setattr(self._connection, name, value)


class _InjectedStorageFailure(finder_core.StorageFailure):
    def __init__(
        self,
        message: str,
        *,
        code: str = "synthetic_storage_failure",
        installed: bool = False,
        cleanup_required: bool = False,
        durability_confirmed: bool = False,
        state: str = "failed",
    ) -> None:
        Exception.__init__(self, message)
        self.code = code
        self.installed = installed
        self.cleanup_required = cleanup_required
        self.durability_confirmed = durability_confirmed
        self.state = state


class _SyntheticTarget:
    def __init__(
        self,
        image: bytes | None,
        *,
        exists: bool = False,
        memory: bool = False,
        verify_failure: _InjectedStorageFailure | None = None,
        verify_failure_on: int | None = None,
        install_failure: _InjectedStorageFailure | None = None,
        close_failure: _InjectedStorageFailure | None = None,
    ) -> None:
        self.image = image
        self.memory = memory
        self.exists = exists
        self.installed = False
        self.state = "ready"
        self.verify_failure = verify_failure
        self.verify_failure_on = verify_failure_on
        self.install_failure = install_failure
        self.close_failure = close_failure
        self.verify_calls = 0

    def __enter__(self) -> "_SyntheticTarget":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        if self.close_failure is not None:
            raise self.close_failure
        self.close()

    def read_image(self) -> bytes | None:
        return self.image

    def verify_entry(self) -> None:
        self.verify_calls += 1
        if self.verify_failure is not None and (
            self.verify_failure_on is None or self.verify_calls == self.verify_failure_on
        ):
            raise self.verify_failure

    def install_image(self, image: bytes) -> None:
        self.installed = True
        self.image = image
        if self.install_failure is not None:
            raise self.install_failure

    def close(self) -> None:
        return None


class FinderImageCapabilityTests(unittest.TestCase):
    def test_missing_sqlite_deserialize_fails_closed(self) -> None:
        class SerializeOnlyConnection:
            @staticmethod
            def serialize(*args: Any, **kwargs: Any) -> bytes:
                return b"synthetic-image"

        with patch.object(finder_core.sqlite3, "Connection", SerializeOnlyConnection):
            with self.assertRaises(FinderError) as raised:
                finder_core._memory_connection(b"synthetic-image", mode="classify")

        self.assertEqual("unsupported_sqlite_image", raised.exception.code)

    def test_missing_sqlite_serialize_fails_closed(self) -> None:
        class DeserializeOnlyConnection:
            @staticmethod
            def deserialize(*args: Any, **kwargs: Any) -> None:
                return None

        target = _SyntheticTarget(None)
        with patch.object(finder_core.sqlite3, "Connection", DeserializeOnlyConnection):
            with self.assertRaises(FinderError) as raised:
                finder_core._open_target_connection(target, mode="import", require_serialize=True)

        self.assertEqual("unsupported_sqlite_image", raised.exception.code)


class FirstCustomerFinderTests(unittest.TestCase):
    """Synthetic-only contract tests for the isolated First Customer Finder."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="media-house-finder-")
        self.directory = Path(self.temp.name).resolve()
        self.database = self.directory / "synthetic.sqlite3"
        self.input_path = self.directory / "saved.json"
        self.fail_late_insert = False
        self.connection_calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

        # Capture the real primitive only inside the guard.  All test fixture
        # and readback connections go through sqlite3.connect after this patch.
        self._original_sqlite_connect = sqlite3.connect
        self.connect_guard = patch.object(sqlite3, "connect", side_effect=self._guarded_connect)
        self.connect_guard.start()
        self.addCleanup(self.connect_guard.stop)
        self.addCleanup(self.temp.cleanup)

        self.assertTrue(self.directory.is_relative_to(Path(tempfile.gettempdir()).resolve()))
        self.assertFalse(self.directory.is_relative_to(WORKSPACE))

    def _guarded_connect(self, database: Any, *args: Any, **kwargs: Any) -> Any:
        """Reject every connection except this test's disposable database."""

        try:
            raw = os.fspath(database)
        except TypeError as exc:
            raise AssertionError(f"non-path SQLite target: {database!r}") from exc
        if not isinstance(raw, str):
            raise AssertionError(f"non-text SQLite target: {raw!r}")
        self.connection_calls.append((raw, args, dict(kwargs)))

        if raw == ":memory:":
            return self._original_sqlite_connect(database, *args, **kwargs)

        is_uri = raw.startswith("file:")
        if is_uri:
            parsed = urlsplit(raw)
            query = parse_qs(parsed.query, keep_blank_values=True)
            if query.get("mode") != ["ro"]:
                raise AssertionError(f"non-read-only SQLite URI: {raw}")
            if parsed.netloc not in {"", "localhost"}:
                raise AssertionError(f"remote SQLite URI: {raw}")
            decoded = unquote(parsed.path)
            candidate = Path(decoded)
            if not candidate.is_absolute():
                raise AssertionError(f"relative SQLite URI: {raw}")
        else:
            candidate = Path(raw).expanduser()

        resolved = candidate.resolve(strict=False)
        temp_root = Path(tempfile.gettempdir()).resolve()
        if not resolved.is_relative_to(temp_root):
            raise AssertionError(f"SQLite target is outside tempfile root: {raw}")
        if resolved == WORKSPACE or WORKSPACE in resolved.parents:
            raise AssertionError(f"real workspace database access: {raw}")
        if not resolved.is_relative_to(self.directory):
            raise AssertionError(f"SQLite target is outside this test's temp root: {raw}")

        connect_kwargs = dict(kwargs)
        # Keep lock/race tests short while preserving SQLite's normal behavior.
        connect_kwargs.setdefault("timeout", 0.2)
        if is_uri:
            connect_kwargs["uri"] = True
        connection = self._original_sqlite_connect(raw if is_uri else str(candidate), *args, **connect_kwargs)
        if self.fail_late_insert and resolved.parent == self.directory:
            return _FaultingConnection(connection)
        return connection

    def db_connect(self, database: str | os.PathLike[str] | None = None, **kwargs: Any) -> Any:
        """Open a synthetic connection through the active guard."""

        target: str | os.PathLike[str] = self.database if database is None else database
        return sqlite3.connect(str(target), **kwargs)

    def record(self, *, source_object_id: str = "source-1", **changes: object) -> dict[str, object]:
        record: dict[str, object] = {
            "source_object_id": source_object_id,
            "source_url": f"https://directory.example/objects/{source_object_id}",
            "company_name": "Acme Services",
            "region": "Maharashtra, India",
            "industry": "professional services",
            "observed_at": "2026-10-14T12:00:00Z",
            "website": "https://acme.example/about",
        }
        record.update(changes)
        return record

    def save(self, records: list[object], **envelope_changes: object) -> Path:
        envelope: dict[str, object] = {
            "format": "media-house-first-customer-finder",
            "schema_version": 1,
            "records": records,
        }
        envelope.update(envelope_changes)
        self.input_path.write_text(json.dumps(envelope, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return self.input_path

    def novelty_evidence(self, records: list[object]) -> dict[str, object]:
        canonical = json.dumps(
            records,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return {
            "source_id": "synthetic-authoritative-source",
            "snapshot_id": "synthetic-snapshot-1",
            "completeness": "complete",
            "authoritative": True,
            "no_prior_match": True,
            "record_count": len(records),
            "records_sha256": hashlib.sha256(canonical).hexdigest(),
        }

    def _serialized_fixture(self, records: list[object]) -> bytes:
        batch = finder_core._parse_batch({"records": records})
        self.assertFalse(batch.malformed)
        connection = self._original_sqlite_connect(":memory:")
        try:
            finder_core._create_schema(connection)
            finder_core._insert_new(connection, batch.candidates, researcher="fixture")
            connection.commit()
            return connection.serialize(name="main")
        finally:
            connection.close()

    def seed_database(self, records: list[object], database: str | os.PathLike[str] | None = None) -> Path:
        path = self.database if database is None else Path(database)
        path.write_bytes(self._serialized_fixture(records))
        return path

    def synthetic_target(
        self,
        records: list[object] | None = None,
        *,
        exists: bool = False,
        memory: bool = False,
        **kwargs: Any,
    ) -> _SyntheticTarget:
        image = None if records is None else self._serialized_fixture(records)
        return _SyntheticTarget(image, exists=exists, memory=memory, **kwargs)

    def cli(self, *args: str) -> tuple[int, str, str]:
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            status = main(["finder", *args])
        return status, output.getvalue(), error.getvalue()

    def file_bytes(self, database: str | os.PathLike[str] | None = None) -> bytes | None:
        path = self.database if database is None else Path(database)
        return path.read_bytes() if path.exists() else None

    def table_exists(self, table: str, database: str | os.PathLike[str] | None = None) -> bool:
        path = self.database if database is None else Path(database)
        if not path.exists():
            return False
        connection = self.db_connect(path)
        try:
            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            return row is not None
        finally:
            connection.close()

    def rows(self, table: str, database: str | os.PathLike[str] | None = None) -> list[dict[str, Any]]:
        path = self.database if database is None else Path(database)
        if not path.exists() or not self.table_exists(table, path):
            return []
        connection = self.db_connect(path)
        connection.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()]
        finally:
            connection.close()

    def schema_names(self, database: str | os.PathLike[str] | None = None) -> list[str]:
        path = self.database if database is None else Path(database)
        if not path.exists():
            return []
        connection = self.db_connect(path)
        try:
            return [
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
                ).fetchall()
            ]
        finally:
            connection.close()

    def trigger_names(self, database: str | os.PathLike[str] | None = None) -> list[str]:
        path = self.database if database is None else Path(database)
        if not path.exists():
            return []
        connection = self.db_connect(path)
        try:
            return [
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name"
                ).fetchall()
            ]
        finally:
            connection.close()

    def logical_snapshot(self, database: str | os.PathLike[str] | None = None) -> dict[str, object]:
        path = self.database if database is None else Path(database)
        if not path.exists():
            return {"schema": [], "triggers": [], "finder_records": [], "finder_source_evidence": []}
        connection = self.db_connect(path)
        connection.row_factory = sqlite3.Row
        try:
            schema = [
                tuple(row)
                for row in connection.execute(
                    "SELECT type, name, tbl_name, sql FROM sqlite_master "
                    "WHERE type IN ('table', 'index', 'trigger') ORDER BY type, name"
                ).fetchall()
            ]
            return {
                "schema": schema,
                "triggers": self.trigger_names(path),
                "finder_records": [
                    dict(row) for row in connection.execute("SELECT * FROM finder_records ORDER BY id").fetchall()
                ]
                if self.table_exists("finder_records", path)
                else [],
                "finder_source_evidence": [
                    dict(row)
                    for row in connection.execute("SELECT * FROM finder_source_evidence ORDER BY id").fetchall()
                ]
                if self.table_exists("finder_source_evidence", path)
                else [],
            }
        finally:
            connection.close()

    def assert_report_shape(self, report: dict[str, Any], total: int | None = None) -> None:
        self.assertTrue(_REPORT_KEYS.issubset(report.keys()), sorted(_REPORT_KEYS - set(report)))
        self.assertEqual("media-house-first-customer-finder", report["format"])
        self.assertEqual(1, report["schema_version"])
        self.assertIn(report["mode"], {"classify", "import"})
        if total is not None:
            self.assertEqual(total, report["counts"]["total"])
            self.assertEqual(total, len(report["records"]))
        self.assertEqual(
            {"total", *_CLASSIFICATIONS},
            set(report["counts"]),
        )
        self.assertEqual(
            report["counts"]["total"],
            sum(report["counts"][name] for name in _CLASSIFICATIONS),
        )
        for item in report["records"]:
            self.assertIn(item["classification"], _CLASSIFICATIONS)
            self.assertIn("index", item)
            self.assertIn("reason", item)
        self.assertFalse(report["network_performed"])
        self.assertFalse(report["provider_call_performed"])
        self.assertFalse(report["outreach_performed"])

    def report_or_error(self, operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            report = operation()
        except FinderError as exc:
            self.assertIsNotNone(exc.report)
            assert exc.report is not None
            report = exc.report
        self.assert_report_shape(report)
        return report

    def assert_refused(self, operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        report = self.report_or_error(operation)
        self.assertTrue(report["rejected"])
        self.assertFalse(report["database_write_performed"])
        self.assertIn("error", report)
        return report

    def assert_no_database(self, database: str | os.PathLike[str] | None = None) -> None:
        path = self.database if database is None else Path(database)
        self.assertFalse(path.exists(), f"unexpected synthetic database creation: {path}")

    def test_v1_batch_is_ambiguous_and_never_imported_from_an_empty_snapshot(self) -> None:
        records = [
            self.record(
                source_object_id="new-1",
                website="https://one.example/about",
            ),
            self.record(
                source_object_id="new-2",
                source_url="https://directory.example/objects/new-2",
                company_name="Beta Studio",
                region="Karnataka, India",
                website="https://two.example/",
            ),
        ]
        self.save(records)

        classified = classify_saved_input(self.input_path, self.database)
        self.assert_report_shape(classified, total=2)
        self.assertEqual(2, classified["counts"]["AMBIGUOUS_DUPLICATE"])
        self.assertFalse(classified["all_new"])
        self.assertTrue(classified["rejected"])
        self.assert_no_database()

        report = import_saved_input(self.input_path, self.database, confirm_import=True, researcher="founder")

        self.assert_report_shape(report, total=2)
        self.assertEqual(2, report["counts"]["AMBIGUOUS_DUPLICATE"])
        self.assertFalse(report["all_new"])
        self.assertFalse(report["importable"])
        self.assertTrue(report["rejected"])
        self.assertTrue(report["preview_only"])
        self.assertFalse(report["database_write_performed"])
        self.assertEqual(0, report["imported"])
        self.assert_no_database()

    def test_synthetic_fixture_preserves_insert_defaults_and_evidence(self) -> None:
        records = [
            self.record(
                source_object_id="fixture-1",
                website="https://one.example/about",
            ),
            self.record(
                source_object_id="fixture-2",
                source_url="https://directory.example/fixture-2",
                company_name="Beta Studio",
                region="Karnataka, India",
                website="https://two.example/",
            ),
        ]
        self.seed_database(records)

        self.assertEqual(2, len(self.rows("finder_records")))
        self.assertEqual(2, len(self.rows("finder_source_evidence")))
        tables = set(self.schema_names())
        self.assertTrue({"finder_records", "finder_source_evidence"}.issubset(tables))
        self.assertFalse(tables & {"companies", "prospects", "source_evidence", "outreach_drafts"})

        first = self.rows("finder_records")[0]
        self.assertEqual("discovered", first["verified_status"])
        self.assertIn("customer need is unknown", first["fit_reason"])
        self.assertEqual(0, first["customer_pain_confirmed"])
        self.assertEqual(0, first["qualified"])
        self.assertEqual("unknown", first["contact_channel"])
        self.assertEqual("unknown", first["channel_permission_status"])
        self.assertEqual("new", first["lead_status"])
        self.assertEqual("unknown", first["opt_out"])
        self.assertEqual("fixture", self.rows("finder_source_evidence")[0]["researcher"])

    def test_exact_domain_name_region_and_near_name_duplicates_do_not_write(self) -> None:
        self.save([self.record(source_object_id="existing", website="https://acme.example/about")])
        self.seed_database([self.record(source_object_id="existing", website="https://acme.example/about")])
        before_bytes = self.file_bytes()
        before_logical = self.logical_snapshot()

        cases = (
            ("exact", self.record(source_object_id="existing"), "EXACT_DUPLICATE"),
            (
                "domain",
                self.record(
                    source_object_id="different-domain-id",
                    source_url="https://other-directory.example/item",
                    company_name="A Different Display Name",
                    region="Gujarat, India",
                    website="https://www.acme.example/contact",
                ),
                "PROBABLE_DUPLICATE",
            ),
            (
                "name-region",
                self.record(
                    source_object_id="name-region-id",
                    source_url="https://other-directory.example/name-region",
                    website=None,
                ),
                "AMBIGUOUS_DUPLICATE",
            ),
            (
                "near-name",
                self.record(
                    source_object_id="near-name-id",
                    source_url="https://other-directory.example/near-name",
                    company_name="Acme Service",
                    website=None,
                ),
                "PROBABLE_DUPLICATE",
            ),
        )
        for label, candidate, expected in cases:
            with self.subTest(label=label):
                self.save([candidate])
                classified = classify_saved_input(self.input_path, self.database)
                self.assert_report_shape(classified, total=1)
                self.assertEqual(expected, classified["records"][0]["classification"])
                self.assertFalse(classified["database_write_performed"])

                rejected = import_saved_input(
                    self.input_path,
                    self.database,
                    confirm_import=True,
                    researcher="founder",
                )
                self.assert_report_shape(rejected, total=1)
                self.assertTrue(rejected["rejected"])
                self.assertFalse(rejected["database_write_performed"])
                self.assertEqual(before_bytes, self.file_bytes())
                self.assertEqual(before_logical, self.logical_snapshot())

    def test_multiple_ambiguous_existing_matches_are_not_first_wins(self) -> None:
        self.seed_database(
            [
                self.record(source_object_id="ambiguous-a", website=None),
                self.record(
                    source_object_id="ambiguous-b",
                    source_url="https://other-directory.example/ambiguous-b",
                    website=None,
                ),
            ]
        )

        before = self.file_bytes()
        self.save(
            [
                self.record(
                    source_object_id="ambiguous-c",
                    source_url="https://other-directory.example/ambiguous-c",
                    website=None,
                )
            ]
        )
        report = classify_saved_input(self.input_path, self.database)
        self.assert_report_shape(report, total=1)
        self.assertEqual("AMBIGUOUS_DUPLICATE", report["records"][0]["classification"])
        rejected = import_saved_input(self.input_path, self.database, confirm_import=True, researcher="founder")
        self.assertTrue(rejected["rejected"])
        self.assertEqual(before, self.file_bytes())

    def test_in_batch_duplicate_conflict_and_malformed_records_report_full_batch_and_write_zero_rows(self) -> None:
        records: list[object] = [
            self.record(source_object_id="duplicate"),
            self.record(source_object_id="duplicate", company_name="Changed Duplicate"),
            self.record(
                source_object_id="conflict-a",
                source_url="https://directory.example/shared-object",
                website="https://conflict-a.example",
            ),
            self.record(
                source_object_id="conflict-b",
                source_url="https://directory.example/shared-object",
                company_name="Conflict B",
                website="https://conflict-b.example",
            ),
            {**self.record(source_object_id="malformed"), "source_url": "file:///not-http"},
            self.record(source_object_id="valid-new", website="https://valid-new.example"),
        ]
        self.save(records)

        report = import_saved_input(self.input_path, self.database, confirm_import=True, researcher="founder")

        self.assert_report_shape(report, total=len(records))
        self.assertEqual(list(range(1, len(records) + 1)), [item["index"] for item in report["records"]])
        self.assertGreaterEqual(report["counts"]["MALFORMED"], 3)
        self.assertGreaterEqual(report["counts"]["CONFLICTING_IDENTITY"], 2)
        self.assertFalse(report["all_new"])
        self.assertTrue(report["rejected"])
        self.assertEqual(0, report["imported"])
        self.assertFalse(report["database_write_performed"])
        self.assert_no_database()

    def test_preexisting_rows_and_evidence_are_unchanged_when_unresolved_batch_is_rejected(self) -> None:
        self.seed_database([self.record(source_object_id="protected")])
        connection = self.db_connect()
        try:
            connection.execute(
                """
                UPDATE finder_records
                SET verified_status = 'human_verified',
                    fit_reason = 'Human-confirmed pain',
                    customer_pain_confirmed = 1,
                    qualified = 1,
                    contact_channel = 'email',
                    channel_permission_status = 'granted',
                    lead_status = 'qualified',
                    opt_out = 'false',
                    notes = 'protected note'
                WHERE source_object_id = 'protected'
                """
            )
            connection.commit()
        finally:
            connection.close()

        before_records = self.rows("finder_records")
        before_evidence = self.rows("finder_source_evidence")
        before_bytes = self.file_bytes()

        self.save(
            [
                self.record(
                    source_object_id="different-new",
                    source_url="https://new-directory.example/different-new",
                    company_name="Different New Company",
                    region="Delhi, India",
                    website="https://different-new.example",
                )
            ]
        )
        report = import_saved_input(self.input_path, self.database, confirm_import=True, researcher="researcher-2")

        self.assertTrue(report["rejected"])
        self.assertEqual(1, report["counts"]["AMBIGUOUS_DUPLICATE"])
        self.assertEqual(0, report["imported"])
        self.assertFalse(report["database_write_performed"])
        after_records = self.rows("finder_records")
        after_evidence = self.rows("finder_source_evidence")
        self.assertEqual(before_records, after_records)
        self.assertEqual(before_evidence, after_evidence)
        self.assertEqual(before_bytes, self.file_bytes())

    def test_repeated_import_is_idempotent_with_identical_bytes_and_evidence(self) -> None:
        records = [
            self.record(source_object_id="repeat-a", website="https://repeat-a.example"),
            self.record(
                source_object_id="repeat-b",
                source_url="https://directory.example/repeat-b",
                company_name="Repeat B",
                website="https://repeat-b.example",
            ),
        ]
        self.seed_database(records)
        self.save(records)
        before = self.file_bytes()
        before_records = self.rows("finder_records")
        before_evidence = self.rows("finder_source_evidence")

        second = import_saved_input(self.input_path, self.database, confirm_import=True, researcher="founder")

        self.assertEqual(0, second["imported"])
        self.assertTrue(second["rejected"])
        self.assertFalse(second["database_write_performed"])
        self.assertEqual(2, second["counts"]["EXACT_DUPLICATE"])
        self.assertEqual(before, self.file_bytes())
        self.assertEqual(before_records, self.rows("finder_records"))
        self.assertEqual(before_evidence, self.rows("finder_source_evidence"))

    def test_preview_confirmation_and_researcher_gates_do_not_create_a_database(self) -> None:
        self.save([self.record()])

        preview = import_saved_input(self.input_path, self.database)
        self.assert_report_shape(preview, total=1)
        self.assertTrue(preview["preview_only"])
        self.assertFalse(preview["all_new"])
        self.assertFalse(preview["importable"])
        self.assertEqual(1, preview["counts"]["AMBIGUOUS_DUPLICATE"])
        self.assertFalse(preview["database_write_performed"])
        self.assertTrue(preview["rejected"])
        self.assert_no_database()

        missing = import_saved_input(self.input_path, self.database, confirm_import=True)
        self.assert_report_shape(missing, total=1)
        self.assertTrue(missing["rejected"])
        self.assertIn("researcher", missing["error"])
        self.assertFalse(missing["database_write_performed"])
        self.assert_no_database()

        blank = import_saved_input(self.input_path, self.database, confirm_import=True, researcher="  ")
        self.assertTrue(blank["rejected"])
        self.assertFalse(blank["database_write_performed"])
        self.assert_no_database()

        status, output, error = self.cli(
            "import",
            str(self.input_path),
            "--synthetic-db",
            str(self.database),
            "--confirm-import",
        )
        self.assertEqual(2, status)
        self.assertIn("researcher", json.loads(output)["error"])
        self.assertIn("error", error)
        self.assert_no_database()

    def test_empty_input_is_not_all_new_and_does_not_create_a_database(self) -> None:
        self.save([])

        classified = classify_saved_input(self.input_path, self.database)
        imported = import_saved_input(
            self.input_path,
            self.database,
            confirm_import=True,
            researcher="founder",
        )

        for report in (classified, imported):
            self.assert_report_shape(report, total=0)
            self.assertFalse(report["all_new"])
            self.assertFalse(report["importable"])
            self.assertTrue(report["rejected"])
            self.assertFalse(report["database_write_performed"])
        self.assert_no_database()

    def test_validated_novelty_contract_is_required_and_allows_new_on_empty_target(self) -> None:
        candidate = self.record(source_object_id="novelty-contract")

        self.save([candidate])
        without_contract = classify_saved_input(self.input_path, self.database)
        self.assertEqual("AMBIGUOUS_DUPLICATE", without_contract["records"][0]["classification"])
        self.assertFalse(without_contract["novelty_evidence_valid"])
        self.assert_no_database()

        self.save([candidate], novelty_evidence=self.novelty_evidence([candidate]))
        with_contract = classify_saved_input(self.input_path, self.database)
        self.assertEqual("NEW", with_contract["records"][0]["classification"])
        self.assertTrue(with_contract["novelty_evidence_valid"])
        self.assertTrue(with_contract["all_new"])
        self.assertTrue(with_contract["importable"])
        self.assert_no_database()

        imported = import_saved_input(
            self.input_path,
            self.database,
            confirm_import=True,
            researcher="founder",
        )
        self.assertFalse(imported["rejected"])
        self.assertTrue(imported["database_write_performed"])
        self.assertEqual(1, imported["imported"])
        self.assertTrue(self.database.exists())

    def test_invalid_novelty_contract_fails_before_storage_access(self) -> None:
        candidate = self.record(source_object_id="invalid-novelty")
        contract = self.novelty_evidence([candidate])
        contract["records_sha256"] = "0" * 64
        self.save([candidate], novelty_evidence=contract)

        with patch.object(finder_core, "open_target", side_effect=AssertionError("storage must not open")):
            report = self.assert_refused(lambda: classify_saved_input(self.input_path, self.database))
        self.assertIn("records_sha256", report["error"])
        self.assert_no_database()

    def test_cli_classify_returns_nonzero_for_rejected_batch(self) -> None:
        self.seed_database([self.record(source_object_id="cli-existing")])
        self.save([self.record(source_object_id="cli-existing")])

        status, output, error = self.cli(
            "classify",
            str(self.input_path),
            "--synthetic-db",
            str(self.database),
        )

        report = json.loads(output)
        self.assertEqual(2, status)
        self.assertTrue(report["rejected"])
        self.assertEqual("EXACT_DUPLICATE", report["records"][0]["classification"])
        self.assertIn("error", error)

    def test_unknown_missing_and_punctuation_required_identifiers_are_malformed(self) -> None:
        records: list[object] = []
        unknown_source = self.record(source_object_id="unknown-source")
        unknown_source["source_object_id"] = "unknown"
        records.append(unknown_source)

        missing_source = self.record(source_object_id="missing-source")
        missing_source.pop("source_object_id")
        records.append(missing_source)

        unknown_company = self.record(source_object_id="unknown-company", company_name="N/A")
        records.append(unknown_company)
        records.append(self.record(source_object_id="unknown-region", region="unknown"))
        records.append(self.record(source_object_id="unknown-industry", industry="not provided"))
        records.append(self.record(source_object_id="unknown-url", source_url="unknown"))
        records.append(self.record(source_object_id="punctuation-industry", industry="---"))
        missing_company = self.record(source_object_id="missing-company")
        missing_company.pop("company_name")
        records.append(missing_company)
        missing_region = self.record(source_object_id="missing-region")
        missing_region.pop("region")
        records.append(missing_region)
        missing_industry = self.record(source_object_id="missing-industry")
        missing_industry.pop("industry")
        records.append(missing_industry)
        missing_source_url = self.record(source_object_id="missing-url")
        missing_source_url.pop("source_url")
        records.append(missing_source_url)
        records.append(self.record(source_object_id="missing-time", observed_at=None))
        records.append(self.record(source_object_id="punctuation-name", company_name="!!!"))
        self.save(records)

        report = self.report_or_error(lambda: classify_saved_input(self.input_path, self.database))
        self.assert_report_shape(report, total=len(records))
        self.assertEqual(len(records), report["counts"]["MALFORMED"])
        self.assertTrue(all(item["classification"] == "MALFORMED" for item in report["records"]))
        self.assert_no_database()

        imported = self.report_or_error(
            lambda: import_saved_input(self.input_path, self.database, confirm_import=True, researcher="founder")
        )
        self.assertTrue(imported["rejected"])
        self.assertFalse(imported["database_write_performed"])
        self.assert_no_database()

    def test_malformed_envelopes_json_types_urls_websites_times_and_flags_fail_closed(self) -> None:
        malformed_envelopes: list[bytes] = [
            b"{",
            b"[]",
            b'{"format":"media-house-first-customer-finder","format":"duplicate",'
            b'"schema_version":1,"records":[]}',
            json.dumps(
                {"format": "media-house-first-customer-finder", "schema_version": True, "records": []}
            ).encode("utf-8"),
            json.dumps(
                {"format": "media-house-first-customer-finder", "schema_version": 1, "records": {}}
            ).encode("utf-8"),
            json.dumps(
                {
                    "format": "media-house-first-customer-finder",
                    "schema_version": 1,
                    "records": [],
                    "unexpected": True,
                }
            ).encode("utf-8"),
            b'{"format":"media-house-first-customer-finder","schema_version":1,"records":[NaN]}',
        ]
        for number, payload in enumerate(malformed_envelopes):
            with self.subTest(envelope=number):
                self.input_path.write_bytes(payload)
                report = self.assert_refused(lambda: classify_saved_input(self.input_path, self.database))
                self.assertEqual("classify", report["mode"])
                self.assert_no_database()

        records: list[object] = [
            self.record(source_object_id="bad-type", company_name=3),
            {**self.record(source_object_id="bad-extra"), "qualified": True},
            {**self.record(source_object_id="bad-both-time"), "retrieved_at": "2026-10-14T12:00:00Z"},
            self.record(source_object_id="bad-time", observed_at="not-a-time"),
            self.record(source_object_id="bad-time-type", observed_at=3),
            self.record(source_object_id="bad-source-credentials", source_url="https://u:p@directory.example/x"),
            self.record(source_object_id="bad-source-scheme", source_url="ftp://directory.example/x"),
            self.record(source_object_id="bad-source-host", source_url="https://[invalid/x"),
            self.record(source_object_id="bad-website-scheme", website="ftp://example.com"),
            self.record(source_object_id="bad-website-file", website="file:///tmp/example"),
            self.record(source_object_id="bad-website-credentials", website="https://u:p@example.com"),
            self.record(source_object_id="bad-website-type", website=4),
            self.record(source_object_id="bad-flag", automation_status="unknown"),
            self.record(source_object_id="bad-qualified-flag", customer_pain_confirmed=True),
            self.record(source_object_id="bad-permission-flag", permission="granted"),
        ]
        self.save(records)
        report = self.report_or_error(lambda: classify_saved_input(self.input_path, self.database))
        self.assert_report_shape(report, total=len(records))
        self.assertEqual(len(records), report["counts"]["MALFORMED"])
        self.assert_no_database()

    def test_input_size_and_batch_count_are_bounded_before_writes(self) -> None:
        oversized = self.record(source_object_id="oversized", company_name="x" * (1024 * 1024))
        self.save([oversized])
        self.assertGreater(self.input_path.stat().st_size, 1024 * 1024)
        oversized_report = self.report_or_error(lambda: classify_saved_input(self.input_path, self.database))
        self.assertTrue(oversized_report["rejected"])
        self.assert_no_database()

        records = [
            self.record(
                source_object_id=f"bounded-{number}",
                source_url=f"https://directory.example/bounded-{number}",
                company_name=f"Bounded Company {number}",
                website=f"https://bounded-{number}.example",
            )
            for number in range(201)
        ]
        self.save(records)
        batch_report = self.report_or_error(lambda: classify_saved_input(self.input_path, self.database))
        self.assertTrue(batch_report["rejected"])
        self.assert_no_database()

    def test_website_addition_and_removal_are_uncertain_not_new_or_exact(self) -> None:
        addition_db = self.directory / "website-addition.sqlite3"
        removal_db = self.directory / "website-removal.sqlite3"

        self.save([self.record(source_object_id="website-add", website=None)])
        self.seed_database([self.record(source_object_id="website-add", website=None)], addition_db)
        self.save([self.record(source_object_id="website-add", website="https://added.example")])
        addition = classify_saved_input(self.input_path, addition_db)

        self.save([self.record(source_object_id="website-remove", website="https://removed.example")])
        self.seed_database(
            [self.record(source_object_id="website-remove", website="https://removed.example")],
            removal_db,
        )
        self.save([self.record(source_object_id="website-remove", website=None)])
        removal = classify_saved_input(self.input_path, removal_db)

        for report in (addition, removal):
            self.assert_report_shape(report, total=1)
            self.assertEqual("CONFLICTING_IDENTITY", report["records"][0]["classification"])
            self.assertTrue(report["rejected"])
            self.assertFalse(report["database_write_performed"])

    def test_existing_unrelated_partial_mismatched_databases_and_triggers_are_refused_unchanged(self) -> None:
        cases = {
            "unrelated": (
                "CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT)",
            ),
            "partial-crm": (
                "CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT)",
                "CREATE TABLE prospects (id INTEGER PRIMARY KEY, company_id INTEGER)",
            ),
            "partial": (
                "CREATE TABLE finder_records (id INTEGER PRIMARY KEY, source_object_id TEXT)",
            ),
            "mismatch": (
                "CREATE TABLE finder_records (id INTEGER PRIMARY KEY, source_object_id TEXT NOT NULL, source_url TEXT NOT NULL)",
            ),
        }
        self.save([self.record(source_object_id="schema-input")])
        for label, statements in cases.items():
            with self.subTest(database=label):
                path = self.directory / f"{label}.sqlite3"
                connection = self.db_connect(path)
                try:
                    for statement in statements:
                        connection.execute(statement)
                    connection.commit()
                finally:
                    connection.close()
                before_bytes = self.file_bytes(path)
                before_logical = self.logical_snapshot(path)
                self.assert_refused(lambda path=path: classify_saved_input(self.input_path, path))
                self.assert_refused(
                    lambda path=path: import_saved_input(
                        self.input_path, path, confirm_import=True, researcher="founder"
                    )
                )
                self.assertEqual(before_bytes, self.file_bytes(path))
                self.assertEqual(before_logical, self.logical_snapshot(path))

        trigger_db = self.directory / "trigger.sqlite3"
        self.seed_database([self.record(source_object_id="trigger-existing")], trigger_db)
        connection = self.db_connect(trigger_db)
        try:
            connection.execute(
                """
                CREATE TRIGGER synthetic_finder_blocker
                BEFORE INSERT ON finder_records
                BEGIN
                    SELECT RAISE(ABORT, 'synthetic trigger');
                END
                """
            )
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(["synthetic_finder_blocker"], self.trigger_names(trigger_db))
        before_bytes = self.file_bytes(trigger_db)
        before_logical = self.logical_snapshot(trigger_db)
        self.save(
            [
                self.record(
                    source_object_id="trigger-new",
                    company_name="Trigger New",
                    region="Delhi, India",
                    website="https://trigger-new.example",
                )
            ]
        )
        self.assert_refused(lambda: classify_saved_input(self.input_path, trigger_db))
        self.assert_refused(
            lambda: import_saved_input(self.input_path, trigger_db, confirm_import=True, researcher="founder")
        )
        self.assertEqual(before_bytes, self.file_bytes(trigger_db))
        self.assertEqual(before_logical, self.logical_snapshot(trigger_db))

    def test_symlink_hardlink_workspace_and_sqlite_uri_targets_are_rejected(self) -> None:
        self.save([self.record()])

        symlink_target = self.directory / "symlink-target.sqlite3"
        symlink_target.write_bytes(b"synthetic target")
        symlink_path = self.directory / "symlink.sqlite3"
        os.symlink(symlink_target, symlink_path)
        with self.assertRaises(FinderError):
            classify_saved_input(self.input_path, symlink_path)
        self.assertEqual(b"synthetic target", symlink_target.read_bytes())

        hardlink_target = self.directory / "hardlink-target.sqlite3"
        hardlink_target.write_bytes(b"synthetic hardlink")
        hardlink_path = self.directory / "hardlink.sqlite3"
        os.link(hardlink_target, hardlink_path)
        with self.assertRaises(FinderError):
            import_saved_input(self.input_path, hardlink_path, confirm_import=True, researcher="founder")
        self.assertEqual(b"synthetic hardlink", hardlink_target.read_bytes())

        workspace_path = WORKSPACE / "finder-test-must-never-open.sqlite3"
        with self.assertRaises(FinderError):
            classify_saved_input(self.input_path, workspace_path)

        uri_path = self.directory / "uri.sqlite3"
        uri = f"file:{quote(str(uri_path), safe='/')}?mode=ro"
        with self.assertRaises(FinderError):
            classify_saved_input(self.input_path, uri)
        self.assertFalse(uri_path.exists())

    def test_memory_database_is_the_only_non_file_target_allowed(self) -> None:
        self.save([self.record(source_object_id="memory-only")])
        report = import_saved_input(self.input_path, ":memory:", confirm_import=True, researcher="founder")
        self.assert_report_shape(report, total=1)
        self.assertEqual(1, report["counts"]["AMBIGUOUS_DUPLICATE"])
        self.assertTrue(report["rejected"])
        self.assertFalse(report["database_write_performed"])
        self.assertEqual(0, report["imported"])
        self.assert_no_database()

    def test_late_insert_failure_rolls_back_rows_and_schema(self) -> None:
        records = [
            self.record(source_object_id="late-a", website="https://late-a.example"),
            self.record(
                source_object_id="late-b",
                source_url="https://directory.example/late-b",
                company_name="Late B",
                website="https://late-b.example",
            ),
        ]
        batch = finder_core._parse_batch({"records": records})
        connection = self._original_sqlite_connect(":memory:")
        faulting = _FaultingConnection(connection)
        try:
            faulting.execute("BEGIN IMMEDIATE")
            finder_core._create_schema(faulting)
            with self.assertRaises(sqlite3.IntegrityError):
                finder_core._insert_new(faulting, batch.candidates, researcher="fixture")
            faulting.rollback()
            self.assertEqual([], connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall())
        finally:
            connection.close()

    def test_unavailable_backend_fails_before_any_sqlite_connection(self) -> None:
        self.save([self.record(source_object_id="backend-unavailable")])
        failure = _InjectedStorageFailure("Linux storage is unavailable", code="unsupported_storage")
        self.connection_calls.clear()

        with patch.object(finder_core, "open_target", side_effect=failure):
            report = self.report_or_error(
                lambda: classify_saved_input(self.input_path, self.database)
            )

        self.assertTrue(report["rejected"])
        self.assertFalse(report["database_write_performed"])
        self.assertEqual([], self.connection_calls)

    def test_target_deletion_or_substitution_is_rejected_at_the_backend_seam(self) -> None:
        self.save([self.record(source_object_id="target-race")])
        failure = _InjectedStorageFailure(
            "target entry changed during snapshot read",
            code="target_changed",
            state="entry_changed",
        )
        target = self.synthetic_target(
            [self.record(source_object_id="stored")],
            exists=True,
            verify_failure=failure,
            verify_failure_on=2,
        )
        self.connection_calls.clear()

        with patch.object(finder_core, "open_target", return_value=target):
            report = self.report_or_error(
                lambda: classify_saved_input(self.input_path, self.database)
            )

        self.assertTrue(report["rejected"])
        self.assertFalse(report["database_write_performed"])
        self.assertEqual([], self.connection_calls)
        self.assertEqual(2, target.verify_calls)

    def test_preinstallation_storage_failure_receipt_has_no_database_write(self) -> None:
        candidate = self.record(source_object_id="preinstall")
        self.save([candidate])
        batch = finder_core._parse_batch({"records": [candidate]})
        forced_report = finder_core._build_report(
            "import",
            batch.raw_records,
            batch.parsed,
            batch.malformed,
            {1: ("NEW", "synthetic backend seam")},
            novelty_evidence_valid=True,
        )
        failure = _InjectedStorageFailure(
            "anonymous image could not be installed",
            code="install_failed",
            state="pre_install_failure",
            cleanup_required=True,
        )
        target = self.synthetic_target(install_failure=failure)

        with patch.object(finder_core, "_classify_batch", return_value=forced_report), patch.object(
            finder_core, "open_target", return_value=target
        ):
            report = import_saved_input(
                self.input_path,
                self.database,
                confirm_import=True,
                researcher="founder",
            )

        self.assertTrue(report["rejected"])
        self.assertFalse(report["importable"])
        self.assertFalse(report["database_write_performed"])
        self.assertTrue(report["preview_only"])
        self.assertEqual(0, report["imported"])
        self.assertEqual("pre_install_failure", report["installation_state"])
        self.assertTrue(report["cleanup_required"])

    def test_postinstallation_storage_failure_receipt_preserves_committed_count(self) -> None:
        candidate = self.record(source_object_id="postinstall")
        self.save([candidate])
        batch = finder_core._parse_batch({"records": [candidate]})
        forced_report = finder_core._build_report(
            "import",
            batch.raw_records,
            batch.parsed,
            batch.malformed,
            {1: ("NEW", "synthetic backend seam")},
            novelty_evidence_valid=True,
        )
        failure = _InjectedStorageFailure(
            "directory durability cleanup is uncertain",
            code="cleanup_failed",
            installed=True,
            cleanup_required=True,
            state="installed_cleanup_uncertain",
        )
        target = self.synthetic_target(install_failure=failure)

        with patch.object(finder_core, "_classify_batch", return_value=forced_report), patch.object(
            finder_core, "open_target", return_value=target
        ):
            report = import_saved_input(
                self.input_path,
                self.database,
                confirm_import=True,
                researcher="founder",
            )

        self.assertTrue(report["rejected"])
        self.assertFalse(report["importable"])
        self.assertTrue(report["database_write_performed"])
        self.assertFalse(report["preview_only"])
        self.assertEqual(1, report["imported"])
        self.assertEqual("installed_cleanup_uncertain", report["installation_state"])
        self.assertTrue(report["cleanup_required"])

    def test_close_failure_preserves_the_already_built_installation_receipt(self) -> None:
        candidate = self.record(source_object_id="close-failure")
        self.save([candidate])
        batch = finder_core._parse_batch({"records": [candidate]})
        forced_report = finder_core._build_report(
            "import",
            batch.raw_records,
            batch.parsed,
            batch.malformed,
            {1: ("NEW", "synthetic backend seam")},
            novelty_evidence_valid=True,
        )
        failure = _InjectedStorageFailure(
            "target descriptor cleanup failed",
            code="close_failed",
            installed=True,
            cleanup_required=True,
            state="installed_close_uncertain",
        )
        target = self.synthetic_target(close_failure=failure)

        with patch.object(finder_core, "_classify_batch", return_value=forced_report), patch.object(
            finder_core, "open_target", return_value=target
        ):
            report = import_saved_input(
                self.input_path,
                self.database,
                confirm_import=True,
                researcher="founder",
            )

        self.assertTrue(report["rejected"])
        self.assertTrue(report["database_write_performed"])
        self.assertEqual(1, report["imported"])
        self.assertEqual("installed_close_uncertain", report["installation_state"])

    def test_confirmed_import_reclassifies_after_a_new_duplicate_appears(self) -> None:
        candidate = self.record(
            source_object_id="race-candidate",
            source_url="https://candidate-directory.example/race-candidate",
            company_name="Race Candidate",
            website="https://race-shared.example/about",
        )
        self.save([candidate])
        preview = import_saved_input(self.input_path, self.database)
        self.assertFalse(preview["all_new"])
        self.assertEqual(1, preview["counts"]["AMBIGUOUS_DUPLICATE"])
        self.assertFalse(preview["database_write_performed"])
        self.assert_no_database()

        self.seed_database(
            [
                self.record(
                    source_object_id="race-existing",
                    source_url="https://existing-directory.example/race-existing",
                    company_name="Race Existing",
                    region="Gujarat, India",
                    website="https://race-shared.example/",
                )
            ]
        )

        self.save([candidate])
        report = import_saved_input(self.input_path, self.database, confirm_import=True, researcher="founder")
        self.assertEqual("PROBABLE_DUPLICATE", report["records"][0]["classification"])
        self.assertTrue(report["rejected"])
        self.assertFalse(report["database_write_performed"])
        self.assertEqual(1, len(self.rows("finder_records")))

    def test_classification_is_read_only_and_uses_memory_sqlite_image(self) -> None:
        self.save([self.record(source_object_id="read-only")])
        self.seed_database([self.record(source_object_id="read-only")])
        before = self.file_bytes()
        self.connection_calls.clear()

        report = classify_saved_input(self.input_path, self.database)

        self.assert_report_shape(report, total=1)
        self.assertFalse(report["database_write_performed"])
        self.assertEqual(before, self.file_bytes())
        self.assertTrue(self.connection_calls)
        self.assertTrue(all(raw == ":memory:" for raw, _, _ in self.connection_calls))

    def test_finder_classify_and_confirmed_import_never_call_network_store_outreach_or_provider(self) -> None:
        self.save([self.record(source_object_id="isolated", website="https://isolated.example")])
        cli_database = self.directory / "cli-isolated.sqlite3"
        failure = AssertionError("finder operation reached a forbidden integration")
        with patch("media_house.cli.discover", side_effect=failure) as cli_discover, \
             patch.object(overpass_module, "discover", side_effect=failure) as overpass_discover, \
             patch("media_house.cli.MediaHouseStore", side_effect=failure), \
             patch("media_house.discovery.integration.MediaHouseStore", side_effect=failure), \
             patch.object(MediaHouseStore, "__init__", side_effect=failure), \
             patch.object(MediaHouseStore, "_upsert_company", side_effect=failure), \
             patch.object(MediaHouseStore, "_upsert_prospect", side_effect=failure), \
             patch.object(MediaHouseStore, "import_csv", side_effect=failure), \
             patch.object(MediaHouseStore, "create_outreach_draft", side_effect=failure), \
             patch.object(MediaHouseStore, "approve_draft", side_effect=failure), \
             patch("urllib.request.OpenerDirector.open", side_effect=failure), \
             patch("urllib.request.urlopen", side_effect=failure), \
             patch("socket.socket", side_effect=failure), \
             patch("socket.create_connection", side_effect=failure), \
             patch("socket.getaddrinfo", side_effect=failure):
            cli_status, cli_output, cli_error = self.cli(
                "classify",
                str(self.input_path),
                "--synthetic-db",
                str(cli_database),
            )
            self.assertEqual(2, cli_status, cli_error)
            cli_classified = json.loads(cli_output)
            cli_status, cli_output, cli_error = self.cli(
                "import",
                str(self.input_path),
                "--synthetic-db",
                str(cli_database),
                "--confirm-import",
                "--researcher",
                "founder",
            )
            self.assertEqual(2, cli_status, cli_error)
            cli_imported = json.loads(cli_output)

        self.assert_report_shape(cli_classified, total=1)
        self.assert_report_shape(cli_imported, total=1)
        self.assertTrue(cli_classified["rejected"])
        self.assertTrue(cli_imported["rejected"])
        self.assertFalse(cli_imported["database_write_performed"])
        self.assertFalse(cli_database.exists())
        cli_discover.assert_not_called()
        overpass_discover.assert_not_called()
        self.assertFalse(cli_classified["network_performed"])
        self.assertFalse(cli_imported["network_performed"])
        self.assertFalse(cli_imported["provider_call_performed"])
        self.assertFalse(cli_imported["outreach_performed"])

    def test_finder_ignores_implicit_media_house_db_and_requires_explicit_synthetic_db(self) -> None:
        self.save([self.record(source_object_id="explicit-only")])
        implicit = self.directory / "implicit.sqlite3"
        with patch.dict(os.environ, {"MEDIA_HOUSE_DB": str(implicit)}), patch(
            "media_house.cli.DEFAULT_DATABASE", str(implicit)
        ):
            status, output, error = self.cli(
                "classify",
                str(self.input_path),
                "--synthetic-db",
                str(self.database),
            )
            self.assertEqual(2, status, error)
            self.assertTrue(json.loads(output)["rejected"])
            self.assertFalse(json.loads(output)["database_write_performed"])
            self.assert_no_database(implicit)

            status, output, error = self.cli(
                "import",
                str(self.input_path),
                "--synthetic-db",
                str(self.database),
                "--confirm-import",
                "--researcher",
                "founder",
            )
            self.assertEqual(2, status, error)
            self.assertTrue(json.loads(output)["rejected"])
            self.assertFalse(json.loads(output)["database_write_performed"])
            self.assertFalse(self.database.exists())
            self.assert_no_database(implicit)

        parser_output, parser_error = io.StringIO(), io.StringIO()
        with redirect_stdout(parser_output), redirect_stderr(parser_error):
            with self.assertRaises(SystemExit) as caught:
                main(["finder", "classify", str(self.input_path)])
        self.assertEqual(2, caught.exception.code)
        self.assert_no_database(implicit)


if __name__ == "__main__":
    unittest.main()
