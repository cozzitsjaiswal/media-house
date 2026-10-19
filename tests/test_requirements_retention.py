"""Regression coverage for requirements-history deletion retention."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from media_house.store import MediaHouseStore
from tests.test_requirements import lead_row, requirements_document, write_csv


PROSPECT_DELETE_ERROR = "cannot delete prospect with requirements history"


class RequirementsRetentionTests(unittest.TestCase):
    """Tests that prospects and meetings with requirements history cannot be deleted."""

    def setUp(self) -> None:
        """Create a temporary directory for per-test database fixtures."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def _seed_prospect_and_meeting(self, store: MediaHouseStore) -> tuple[int, int]:
        """Import a synthetic lead and record a meeting, returning the prospect and meeting IDs."""
        store.import_csv(write_csv(self.directory, [lead_row()]), researcher="researcher")
        prospect_id = int(store.list_prospects()[0]["id"])
        meeting = store.record_meeting(
            prospect_id,
            meeting_at="2026-10-20T12:00:00Z",
            notes="Synthetic retention fixture",
            notes_permission="permission_granted",
            recording_policy="not_recorded",
            actor="founder",
        )
        return prospect_id, int(meeting["id"])

    def _seed_history(self, store: MediaHouseStore) -> tuple[int, int, list[dict[str, object]]]:
        """Seed a prospect with a draft, approved, and superseding-approved requirements document history."""
        prospect_id, meeting_id = self._seed_prospect_and_meeting(store)

        draft = store.capture_requirements(
            meeting_id, requirements_document(status="DRAFT"), actor="founder"
        )

        ready = requirements_document(status="READY_FOR_REVIEW", all_approved=True)
        ready.pop("document_version")
        approved = store.capture_requirements(meeting_id, ready, actor="founder")
        approved = store.approve_requirements(approved["id"], actor="founder")

        superseding = requirements_document(status="READY_FOR_REVIEW", all_approved=True)
        superseding.pop("document_version")
        superseding["requirements"][0]["description"] = "Updated synthetic retained requirement"
        latest = store.capture_requirements(
            meeting_id,
            superseding,
            actor="founder",
            supersedes_document_id=approved["id"],
        )
        latest = store.approve_requirements(latest["id"], actor="founder")

        records = store.list_requirements_documents(prospect_id=prospect_id)
        self.assertEqual(["DRAFT", "SUPERSEDED", "APPROVED"], [record["status"] for record in records])
        self.assertEqual(draft["id"], records[0]["id"])
        self.assertEqual(approved["id"], records[1]["id"])
        self.assertEqual(latest["id"], records[2]["id"])
        return prospect_id, meeting_id, records

    @staticmethod
    def _counts(store: MediaHouseStore) -> dict[str, int]:
        """Return row counts for each core table, keyed by table name."""
        tables = ("companies", "prospects", "source_evidence", "meetings", "requirements_documents", "audit_events")
        return {
            table: int(store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }

    @staticmethod
    def _requirements_foreign_key_action(store: MediaHouseStore, source_column: str) -> str:
        """Return the ON DELETE action configured for the given foreign key column on requirements_documents."""
        rows = store.connection.execute("PRAGMA foreign_key_list(requirements_documents)").fetchall()
        for row in rows:
            if row[3] == source_column:
                return str(row[6])
        raise AssertionError(f"requirements_documents has no foreign key for {source_column}")

    def test_new_database_blocks_prospect_delete_and_retains_all_history(self) -> None:
        """Verify a newly created database blocks deleting a prospect with requirements history and retains it."""
        database = self.directory / "new.sqlite3"
        with MediaHouseStore(database) as store:
            prospect_id, _meeting_id, before_records = self._seed_history(store)
            self.assertEqual(1, store.connection.execute("PRAGMA foreign_keys").fetchone()[0])
            self.assertEqual("RESTRICT", self._requirements_foreign_key_action(store, "prospect_id"))
            self.assertEqual("RESTRICT", self._requirements_foreign_key_action(store, "meeting_id"))
            before_counts = self._counts(store)

            with self.assertRaisesRegex(sqlite3.IntegrityError, f"^{PROSPECT_DELETE_ERROR}$"):
                store.connection.execute("DELETE FROM prospects WHERE id = ?", (prospect_id,))
            store.connection.rollback()

            self.assertEqual(1, store.connection.execute("PRAGMA foreign_keys").fetchone()[0])
            self.assertEqual(before_counts, self._counts(store))
            self.assertEqual(before_records, store.list_requirements_documents(prospect_id=prospect_id))
            self.assertEqual(before_records[1], store.get_requirements_document(before_records[1]["id"]))
            self.assertEqual(before_records[2], store.get_requirements_document(before_records[2]["id"]))

    def test_prospect_without_requirements_history_remains_deletable(self) -> None:
        """Verify a prospect with no requirements history can still be deleted along with its meeting."""
        database = self.directory / "without-requirements.sqlite3"
        with MediaHouseStore(database) as store:
            prospect_id, meeting_id = self._seed_prospect_and_meeting(store)
            self.assertEqual(0, store.connection.execute("SELECT COUNT(*) FROM requirements_documents").fetchone()[0])

            store.connection.execute("DELETE FROM prospects WHERE id = ?", (prospect_id,))
            store.connection.commit()

            self.assertIsNone(store.connection.execute("SELECT 1 FROM prospects WHERE id = ?", (prospect_id,)).fetchone())
            self.assertIsNone(store.connection.execute("SELECT 1 FROM meetings WHERE id = ?", (meeting_id,)).fetchone())
            self.assertEqual(0, store.connection.execute("SELECT COUNT(*) FROM requirements_documents").fetchone()[0])
            self.assertEqual(1, store.connection.execute("PRAGMA foreign_keys").fetchone()[0])

    def test_existing_legacy_cascade_table_gets_guard_after_reopen(self) -> None:
        """Verify reopening a database with a legacy CASCADE foreign key still blocks deletes with history."""
        database = self.directory / "legacy.sqlite3"
        with MediaHouseStore(database) as store:
            prospect_id, _meeting_id, before_records = self._seed_history(store)

        self._rewrite_requirements_table_to_legacy_cascade(database)
        with sqlite3.connect(database) as legacy:
            legacy.row_factory = sqlite3.Row
            self.assertEqual(
                "CASCADE",
                next(row["on_delete"] for row in legacy.execute("PRAGMA foreign_key_list(requirements_documents)")
                     if row["from"] == "prospect_id"),
            )
            self.assertIsNone(
                legacy.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'trigger' "
                    "AND name = 'prevent_prospect_delete_with_requirements_history'"
                ).fetchone()
            )

        with MediaHouseStore(database) as store:
            self.assertEqual(1, store.connection.execute("PRAGMA foreign_keys").fetchone()[0])
            self.assertEqual("CASCADE", self._requirements_foreign_key_action(store, "prospect_id"))
            before_counts = self._counts(store)

            with self.assertRaisesRegex(sqlite3.IntegrityError, f"^{PROSPECT_DELETE_ERROR}$"):
                store.connection.execute("DELETE FROM prospects WHERE id = ?", (prospect_id,))
            store.connection.rollback()

            self.assertEqual(before_counts, self._counts(store))
            self.assertEqual(before_records, store.list_requirements_documents(prospect_id=prospect_id))
            self.assertEqual(1, store.connection.execute("PRAGMA foreign_keys").fetchone()[0])

    def test_direct_meeting_delete_with_requirements_remains_restricted(self) -> None:
        """Verify deleting a meeting that has requirements history is restricted and retains all data."""
        database = self.directory / "meeting-restrict.sqlite3"
        with MediaHouseStore(database) as store:
            _prospect_id, meeting_id, before_records = self._seed_history(store)
            before_counts = self._counts(store)

            with self.assertRaises(sqlite3.IntegrityError):
                store.connection.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
            store.connection.rollback()

            self.assertEqual(before_counts, self._counts(store))
            self.assertEqual(before_records, store.list_requirements_documents())
            self.assertEqual(1, store.connection.execute("PRAGMA foreign_keys").fetchone()[0])

    def _rewrite_requirements_table_to_legacy_cascade(self, database: Path) -> None:
        """Rewrite requirements_documents to use a legacy ON DELETE CASCADE foreign key, simulating an old schema."""
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("DROP TRIGGER IF EXISTS prevent_prospect_delete_with_requirements_history")
            connection.execute("DROP INDEX IF EXISTS requirements_documents_prospect_index")
            connection.execute("DROP INDEX IF EXISTS requirements_documents_meeting_index")
            connection.execute("ALTER TABLE requirements_documents RENAME TO requirements_documents_legacy")
            connection.executescript(
                """
                CREATE TABLE requirements_documents (
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
                INSERT INTO requirements_documents
                    (id, prospect_id, meeting_id, document_version, status, content_json,
                     content_hash, created_by, created_at, approved_by, approved_at,
                     approved_hash, supersedes_document_id)
                SELECT id, prospect_id, meeting_id, document_version, status, content_json,
                       content_hash, created_by, created_at, approved_by, approved_at,
                       approved_hash, supersedes_document_id
                FROM requirements_documents_legacy;
                DROP TABLE requirements_documents_legacy;
                CREATE INDEX requirements_documents_prospect_index ON requirements_documents(prospect_id);
                CREATE INDEX requirements_documents_meeting_index ON requirements_documents(meeting_id);
                """
            )
            connection.commit()


if __name__ == "__main__":
    unittest.main()
