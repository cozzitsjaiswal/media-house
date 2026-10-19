"""Synthetic regressions for export destinations and cancelled approvals."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import chdir, closing
from pathlib import Path
from unittest.mock import patch

from media_house import store as store_module
from media_house.errors import ValidationError
from media_house.store import MediaHouseStore
from tests.test_requirements import lead_row, requirements_document, write_csv


def seed_requirements(store, directory, *, ready=False):
    """Seed a store with a prospect, meeting, and captured requirements document."""
    store.import_csv(write_csv(directory, [lead_row()]), researcher="researcher")
    meeting = store.record_meeting(
        store.list_prospects()[0]["id"], meeting_at="2026-10-20T12:00:00Z",
        notes="Synthetic persistence fixture", notes_permission="permission_granted",
        recording_policy="not_recorded", actor="founder",
    )
    return store.capture_requirements(
        meeting["id"],
        requirements_document(status="READY_FOR_REVIEW" if ready else "DRAFT", all_approved=ready),
        actor="founder",
    )


WAL_EXPORT_CHILD = """
import json
import os
import sys
from pathlib import Path
from media_house.errors import ValidationError
from media_house.store import MediaHouseStore
from tests.test_requirements_persistence_safety import seed_requirements

database = Path(sys.argv[1])
store = MediaHouseStore(database)
assert store.connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
store.connection.execute("PRAGMA wal_autocheckpoint=0")
record = seed_requirements(store, database.parent)
tables = ("prospects", "meetings", "requirements_documents")
before = [store.connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in tables]
filename = next(row["file"] for row in store.connection.execute("PRAGMA database_list") if row["name"] == "main")
wal = Path(filename + "-wal")
assert wal.is_file() and wal.stat().st_size > 32
rejected = False
try:
    store.export_requirements(record["id"], wal, actor="reviewer")
except ValidationError:
    rejected = True
print(json.dumps({"record": record, "before": before, "wal": str(wal), "rejected": rejected,
                  "audits": len(store.list_audit_events(action="requirements_exported"))}), flush=True)
os._exit(0)  # Deliberately bypass connection close/checkpoint and interpreter cleanup.
"""


class RequirementsPersistenceSafetyTests(unittest.TestCase):
    """Tests that exports and approvals never corrupt or bypass SQLite persistence safety."""

    def setUp(self):
        """Create a temporary directory for per-test database fixtures."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def assert_rejected_without_effects(self, store, record, destination):
        """Assert exporting to the given destination is rejected as an active SQLite path with no side effects."""
        before = tuple(store.connection.iterdump())
        names = set(self.directory.rglob("*"))
        link = os.readlink(destination) if destination.is_symlink() else None
        contents = destination.read_bytes() if destination.is_file() else None
        with patch.object(Path, "mkdir", autospec=True, side_effect=Path.mkdir) as mkdir, \
                patch.object(store_module.tempfile, "NamedTemporaryFile", wraps=tempfile.NamedTemporaryFile) as stage, \
                patch.object(store, "_record_audit", wraps=store._record_audit) as audit, \
                patch.object(store_module.os, "replace", wraps=os.replace) as replace:
            with self.assertRaisesRegex(ValidationError, "active SQLite"):
                store.export_requirements(record["id"], destination, actor="reviewer")
            for effect in (mkdir, stage, audit, replace):
                effect.assert_not_called()
        self.assertFalse(store.connection.in_transaction)
        self.assertEqual(before, tuple(store.connection.iterdump()))
        self.assertEqual(names, set(self.directory.rglob("*")))
        self.assertEqual(link, os.readlink(destination) if destination.is_symlink() else None)
        self.assertEqual(contents, destination.read_bytes() if destination.is_file() else None)

    def test_cwd_change_cannot_replace_connected_database(self):
        """Verify changing the working directory cannot be used to export over the connected database."""
        opened, elsewhere = self.directory / "opened", self.directory / "elsewhere"
        opened.mkdir()
        elsewhere.mkdir()
        database = opened / "independent-review.sqlite3"
        with chdir(opened), MediaHouseStore(database.name) as store:
            record = seed_requirements(store, opened)
            self.assertEqual(database.name, store.database)
            with chdir(elsewhere):
                self.assert_rejected_without_effects(store, record, database)
        with MediaHouseStore(database) as reopened:
            self.assertEqual(record, reopened.get_requirements_document(record["id"]))
            self.assertEqual(1, len(reopened.list_prospects()))

    def test_active_main_and_companion_paths_and_aliases_are_rejected(self):
        """Verify exporting to the main database file, its WAL/SHM/journal companions, or symlink aliases is rejected."""
        for alias in (False, True):
            for suffix in ("", "-wal", "-shm", "-journal"):
                with self.subTest(alias=alias, suffix=suffix):
                    directory = self.directory / f"case-{alias}-{suffix or 'main'}"
                    directory.mkdir()
                    database = directory / "synthetic.sqlite3"
                    with MediaHouseStore(database) as store:
                        self.assertEqual("wal", store.connection.execute("PRAGMA journal_mode=WAL").fetchone()[0])
                        store.connection.execute("PRAGMA wal_autocheckpoint=0")
                        record = seed_requirements(store, directory)
                        destination = Path(str(database) + suffix)
                        if alias:
                            link = directory / "output-alias"
                            link.symlink_to(destination)
                            destination = link
                        self.assert_rejected_without_effects(store, record, destination)

    def test_symlink_opened_database_protects_sqlite_and_resolved_companion_names(self):
        """Verify a database opened through a symlink still protects both the linked and resolved companion paths."""
        for resolved in (False, True):
            for suffix in ("", "-wal", "-shm", "-journal"):
                with self.subTest(resolved=resolved, suffix=suffix):
                    directory = self.directory / f"case-{resolved}-{suffix or 'main'}"
                    directory.mkdir()
                    database = directory / "real.sqlite3"
                    with MediaHouseStore(database):
                        pass
                    linked_database = directory / "linked.sqlite3"
                    linked_database.symlink_to(database)
                    with MediaHouseStore(linked_database) as store:
                        self.assertEqual("wal", store.connection.execute("PRAGMA journal_mode=WAL").fetchone()[0])
                        store.connection.execute("PRAGMA wal_autocheckpoint=0")
                        record = seed_requirements(store, directory)
                        filename = Path(next(
                            row["file"] for row in store.connection.execute("PRAGMA database_list")
                            if row["name"] == "main"
                        ))
                        self.assertEqual(database, filename.resolve())
                        basis = filename.resolve() if resolved else filename
                        self.assert_rejected_without_effects(store, record, Path(str(basis) + suffix))

    def test_cwd_change_allows_unrelated_same_named_files_and_streams(self):
        """Verify exporting to unrelated files that merely share the database's relative name or to streams succeeds."""
        opened, elsewhere = self.directory / "opened", self.directory / "elsewhere"
        opened.mkdir()
        elsewhere.mkdir()
        database = opened / "independent-review.sqlite3"
        with chdir(opened), MediaHouseStore(database.name) as store:
            record = seed_requirements(store, opened)
            with chdir(elsewhere):
                for suffix in ("", "-wal", "-shm", "-journal"):
                    with self.subTest(suffix=suffix):
                        destination = Path(database.name + suffix)
                        destination.write_text("Previous unrelated output", encoding="utf-8")
                        before = len(store.list_audit_events(action="requirements_exported"))
                        exported = store.export_requirements(record["id"], destination, actor="reviewer")
                        self.assertEqual(record["document"], exported)
                        self.assertEqual(exported, json.loads(destination.read_text(encoding="utf-8")))
                        self.assertEqual(before + 1, len(store.list_audit_events(action="requirements_exported")))
                stream = io.StringIO()
                store.export_requirements(record["id"], stream, actor="reviewer")
                self.assertFalse(stream.closed)
                self.assertEqual(record["document"], json.loads(stream.getvalue()))
                self.assertEqual([], list(elsewhere.glob(".requirements-*.tmp")))
        with MediaHouseStore(database) as reopened:
            self.assertEqual(record, reopened.get_requirements_document(record["id"]))

    def test_wal_recovery_after_companion_export_and_abrupt_exit(self):
        """Verify committed WAL data survives a rejected companion export followed by an abrupt process exit."""
        for symlink in (False, True):
            with self.subTest(symlink=symlink):
                directory = self.directory / f"case-{symlink}"
                directory.mkdir()
                database = directory / "synthetic.sqlite3"
                with MediaHouseStore(database):
                    pass
                opened = database
                if symlink:
                    opened = directory / "linked.sqlite3"
                    opened.symlink_to(database)
                result = subprocess.run(
                    [sys.executable, "-c", WAL_EXPORT_CHILD, str(opened)],
                    cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                    timeout=15, check=False,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual([1, 1, 1], report["before"])
                self.assertTrue(Path(report["wal"]).is_file())
                with closing(sqlite3.connect(opened)) as reader:
                    recovered = [reader.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                                 for table in ("prospects", "meetings", "requirements_documents")]
                    self.assertEqual(
                        [1, 1, 1], recovered,
                        "committed synthetic rows were lost after WAL export and abrupt exit",
                    )
                    self.assertEqual(0, reader.execute(
                        "SELECT COUNT(*) FROM audit_events WHERE action = 'requirements_exported'"
                    ).fetchone()[0])
                self.assertTrue(report["rejected"])
                self.assertEqual(0, report["audits"])
                self.assertEqual([], list(directory.glob(".requirements-*.tmp")))
                with MediaHouseStore(opened) as reopened:
                    self.assertEqual(report["record"], reopened.get_requirements_document(report["record"]["id"]))

    def test_cancelled_superseding_approval_rolls_back_and_can_retry(self):
        """Verify a cancellation raised mid-approval rolls back superseding fully and allows a later successful retry."""
        for cancellation in (KeyboardInterrupt("injected cancellation"), SystemExit("injected exit")):
            with self.subTest(cancellation=type(cancellation).__name__):
                directory = self.directory / type(cancellation).__name__
                directory.mkdir()
                database = directory / "synthetic.sqlite3"
                with MediaHouseStore(database) as store:
                    first = seed_requirements(store, directory, ready=True)
                    first = store.approve_requirements(first["id"], actor="founder")
                    document = requirements_document(status="READY_FOR_REVIEW", all_approved=True)
                    document["document_version"] = 2
                    document["requirements"][0]["description"] = "Updated synthetic requirement"
                    candidate = store.capture_requirements(
                        first["meeting_id"], document, actor="founder", supersedes_document_id=first["id"],
                    )
                    before = tuple(store.connection.iterdump())
                    original_audit = store._record_audit

                    def cancel_after_supersession(**kwargs):
                        """Perform the real audit insert, then raise the injected cancellation after superseding."""
                        original_audit(**kwargs)
                        if kwargs["action"] == "requirements_superseded":
                            self.assertTrue(store.connection.in_transaction)
                            self.assertEqual("SUPERSEDED", store.get_requirements_document(first["id"])["status"])
                            self.assertEqual("APPROVED", store.get_requirements_document(candidate["id"])["status"])
                            raise cancellation

                    with patch.object(store, "_record_audit", side_effect=cancel_after_supersession):
                        with self.assertRaises(type(cancellation)) as caught:
                            store.approve_requirements(candidate["id"], actor="founder")
                    self.assertIs(cancellation, caught.exception)
                    self.assertFalse(store.connection.in_transaction, "cancellation left the approval transaction open")
                    self.assertEqual(before, tuple(store.connection.iterdump()))
                    self.assertEqual(first, store.get_requirements_document(first["id"]))
                    self.assertEqual(candidate, store.get_requirements_document(candidate["id"]))
                    with MediaHouseStore(database) as persisted:
                        self.assertEqual(before, tuple(persisted.connection.iterdump()))
                    approved = store.approve_requirements(candidate["id"], actor="founder")
                    superseded = store.get_requirements_document(first["id"])
                    self.assertEqual("APPROVED", approved["status"])
                    self.assertEqual("SUPERSEDED", superseded["status"])
                    self.assertEqual(2, len(store.list_audit_events(action="requirements_approved")))
                    self.assertEqual(1, len(store.list_audit_events(action="requirements_superseded")))
                with MediaHouseStore(database) as reopened:
                    self.assertEqual(approved, reopened.get_requirements_document(candidate["id"]))
                    self.assertEqual(superseded, reopened.get_requirements_document(first["id"]))
                    self.assertEqual(2, len(reopened.list_audit_events(action="requirements_approved")))
                    self.assertEqual(1, len(reopened.list_audit_events(action="requirements_superseded")))

    def test_failed_begin_does_not_roll_back_caller_owned_transaction(self):
        """Verify a failed approval inside a caller-owned transaction leaves that transaction intact for the caller."""
        database = self.directory / "synthetic.sqlite3"
        with MediaHouseStore(database) as store:
            record = seed_requirements(store, self.directory, ready=True)
            before = tuple(store.connection.iterdump())
            store.connection.execute("BEGIN IMMEDIATE")
            store._record_audit(
                actor="founder", action="synthetic_caller_pending", object_type="requirements_document",
                object_id=record["id"], metadata={"synthetic_only": True},
            )
            try:
                with self.assertRaisesRegex(sqlite3.OperationalError, "within a transaction"):
                    store.approve_requirements(record["id"], actor="founder")
                self.assertTrue(store.connection.in_transaction)
                self.assertEqual(1, len(store.list_audit_events(action="synthetic_caller_pending")))
                self.assertEqual(record, store.get_requirements_document(record["id"]))
                with closing(sqlite3.connect(database)) as reader:
                    self.assertEqual(0, reader.execute(
                        "SELECT COUNT(*) FROM audit_events WHERE action = 'synthetic_caller_pending'"
                    ).fetchone()[0])
            finally:
                store.connection.rollback()
            self.assertEqual(before, tuple(store.connection.iterdump()))
