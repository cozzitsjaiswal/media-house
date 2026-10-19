from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from media_house.cli import main
from media_house.store import MediaHouseStore
from tests.test_requirements import lead_row, requirements_document, write_csv


class RequirementsCLIEncodingTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.database = self.directory / "synthetic.sqlite3"
        self.store = MediaHouseStore(self.database)
        self.addCleanup(self.store.close)
        self.store.import_csv(write_csv(self.directory, [lead_row()]), researcher="researcher")
        self.prospect_id = self.store.list_prospects()[0]["id"]
        self.meeting = self.store.record_meeting(
            self.prospect_id, meeting_at="2026-10-20T12:00:00Z", notes="Synthetic only",
            notes_permission="permission_granted", recording_policy="not_recorded", actor="founder",
        )
        self.record = self.store.capture_requirements(
            self.meeting["id"], requirements_document(), actor="founder"
        )
        self.document = requirements_document()
        self.document.pop("document_version")
        self.document["business_problem"]["client_statement"] = "कृत्रिम गरज — 店舗の要件 — متطلبات تجريبية"
        self.input_path = self.directory / "requirements.json"
        self.input_path.write_text(json.dumps(self.document, ensure_ascii=False), encoding="utf-8")
        self.meeting_args = [
            "meeting-record", "--prospect-id", str(self.prospect_id),
            "--meeting-at", "2026-10-20T12:00:00Z", "--notes-permission", "permission_granted",
            "--recording-policy", "not_recorded",
        ]
        self.before = tuple(self.store.connection.iterdump())
        self.audits = self.store.list_audit_events()

    def cli(self, *args: str | bytes) -> subprocess.CompletedProcess[bytes]:
        if os.name != "posix" and any(isinstance(arg, bytes) for arg in args):
            self.skipTest("invalid-byte argv requires POSIX subprocess arguments")
        return subprocess.run(
            [sys.executable, "-m", "media_house.cli", "--db", str(self.database), *args],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=15, check=False,
        )

    def assert_rejected(self, *args: str | bytes) -> subprocess.CompletedProcess[bytes]:
        result = self.cli(*args)
        self.assertEqual(self.before, tuple(self.store.connection.iterdump()))
        self.assertEqual(self.audits, self.store.list_audit_events())
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertEqual(b"", result.stdout)
        self.assertTrue(result.stderr.startswith(b"error: "), result.stderr)
        self.assertIn(b"utf-8", result.stderr.lower())
        self.assertNotIn(b"Traceback", result.stderr)
        self.assertNotIn(b'"exported"', result.stderr)
        return result

    def test_capture_malformed_actor_is_controlled_and_atomic(self) -> None:
        self.assert_rejected(
            "requirements-capture", str(self.meeting["id"]), str(self.input_path),
            "--actor", b"operator-\xff",
        )

    def test_meeting_malformed_actor_is_controlled_and_atomic(self) -> None:
        self.assert_rejected(
            *self.meeting_args, "--notes", "Synthetic only", "--actor", b"operator-\xff"
        )

    def test_meeting_malformed_notes_are_controlled_and_atomic(self) -> None:
        self.assert_rejected(
            *self.meeting_args, "--notes", b"notes-\xff", "--actor", "founder"
        )

    def test_export_malformed_actor_handling_remains_controlled_and_atomic(self) -> None:
        destination = self.directory / "export.json"
        destination.write_bytes(b"Retain synthetic output\n")
        for output in (str(destination), "-"):
            with self.subTest(output=output):
                result = self.assert_rejected(
                    "requirements-export", str(self.record["id"]), output,
                    "--actor", b"operator-\xff",
                )
                self.assertIn(b"actor", result.stderr.lower())
                self.assertEqual(b"Retain synthetic output\n", destination.read_bytes())

    def test_capture_accepts_multilingual_actor_and_document_without_approval(self) -> None:
        actor = "मीरा 李 سلمى"
        result = self.cli(
            "requirements-capture", str(self.meeting["id"]), str(self.input_path), "--actor", actor
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(b"", result.stderr)
        captured = json.loads(result.stdout)
        self.assertEqual("DRAFT", captured["status"])
        self.assertEqual(2, captured["document_version"])
        self.assertIsNone(captured["document"]["approval"])
        self.assertEqual(
            self.document["business_problem"]["client_statement"],
            captured["document"]["business_problem"]["client_statement"],
        )
        self.assertEqual(captured, self.store.get_requirements_document(captured["id"]))
        events = [event for event in self.store.list_audit_events() if event not in self.audits]
        self.assertEqual(1, len(events))
        self.assertEqual("requirements_created", events[0]["action"])
        self.assertEqual(actor, events[0]["actor"])
        self.assertEqual([], self.store.list_audit_events(action="requirements_approved"))

    def test_meeting_accepts_multilingual_actor_and_notes(self) -> None:
        actor, notes = "मीरा 李 سلمى", "कृत्रिम बैठक — 会議メモ — ملاحظات تجريبية"
        result = self.cli(*self.meeting_args, "--notes", notes, "--actor", actor)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(b"", result.stderr)
        meeting = json.loads(result.stdout)
        self.assertEqual(notes, meeting["notes"])
        self.assertIn(meeting, self.store.list_meetings(prospect_id=self.prospect_id))
        events = [event for event in self.store.list_audit_events() if event not in self.audits]
        self.assertEqual(1, len(events))
        self.assertEqual(actor, events[0]["actor"])
        self.assertEqual([], self.store.list_audit_events(action="requirements_approved"))

    def test_export_accepts_multilingual_actor_without_approval(self) -> None:
        actor = "मीरा 李 سلمى"
        result = self.cli("requirements-export", str(self.record["id"]), "-", "--actor", actor)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(b"", result.stderr)
        self.assertEqual(self.record["document"], json.loads(result.stdout))
        self.assertEqual(self.record, self.store.get_requirements_document(self.record["id"]))
        events = self.store.list_audit_events(action="requirements_exported")
        self.assertEqual(1, len(events))
        self.assertEqual(actor, events[0]["actor"])
        self.assertEqual([], self.store.list_audit_events(action="requirements_approved"))

    def test_non_encoding_programming_errors_still_propagate(self) -> None:
        for error in (ValueError("synthetic bug"), TypeError("synthetic bug"), RuntimeError("synthetic bug")):
            with self.subTest(error=type(error).__name__):
                output, errors = io.StringIO(), io.StringIO()
                with patch("media_house.cli.MediaHouseStore", side_effect=error), \
                        redirect_stdout(output), redirect_stderr(errors):
                    with self.assertRaises(type(error)) as raised:
                        main(["--db", str(self.database), "meeting-list"])
                self.assertIs(error, raised.exception)
                self.assertEqual("", output.getvalue())
                self.assertEqual("", errors.getvalue())
                self.assertEqual(self.before, tuple(self.store.connection.iterdump()))


if __name__ == "__main__":
    unittest.main()
