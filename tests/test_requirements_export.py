"""Export failure boundaries: SQLite audit and file delivery are NOT jointly atomic."""

from __future__ import annotations

import errno
import io
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from media_house import store as store_module
from media_house.cli import main
from media_house.errors import ValidationError
from media_house.store import MediaHouseStore
from tests.test_requirements import lead_row, requirements_document, write_csv


class FaultingStage:
    """A real temporary file with one injected write/flush/close failure."""

    def __init__(self, handle, phase: str):
        self.handle = handle
        self.phase = phase
        self.name = handle.name

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.handle.close()
        if self.phase == "close":
            raise OSError("injected staging close failure")

    def write(self, payload):
        if self.phase in {"write", "short_write"}:
            count = self.handle.write(payload[:8])
            if self.phase == "write":
                raise OSError("injected partial staging write failure")
            return count
        return self.handle.write(payload)

    def flush(self):
        if self.phase == "flush":
            raise OSError("injected staging flush failure")
        return self.handle.flush()

    def fileno(self):
        return self.handle.fileno()


class RequirementsExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.directory = Path(self.tempdir.name)
        self.database = self.directory / "synthetic.sqlite3"
        self.store = MediaHouseStore(self.database)
        self.addCleanup(self.store.close)
        self.store.import_csv(write_csv(self.directory, [lead_row()]), researcher="researcher")
        meeting = self.store.record_meeting(
            self.store.list_prospects()[0]["id"], meeting_at="2026-10-20T12:00:00Z",
            notes="Synthetic only", notes_permission="permission_granted",
            recording_policy="not_recorded", actor="document-author",
        )
        self.record = self.store.capture_requirements(
            meeting["id"], requirements_document(), actor="document-author"
        )
        self.output_dir = self.directory / "exports"
        self.output_dir.mkdir()
        self.destination = self.output_dir / "requirements.json"
        self.previous = b"Keep these pre-existing bytes\n"
        self.destination.write_bytes(self.previous)
        self.unrelated = self.output_dir / ".requirements-unrelated.tmp"
        self.unrelated.write_bytes(b"unrelated user file")
        self.original_names = set(self.output_dir.iterdir())

    def snapshot(self) -> tuple:
        return tuple(tuple(tuple(row) for row in self.store.connection.execute(f"SELECT * FROM {table} ORDER BY id"))
                     for table in ("requirements_documents", "audit_events"))

    def events(self) -> list[dict]:
        return self.store.list_audit_events(action="requirements_exported")

    def export(self, destination=None, *, actor="actual-exporter"):
        return self.store.export_requirements(
            self.record["id"], self.destination if destination is None else destination, actor=actor
        )

    def assert_retained_and_clean(self) -> None:
        self.assertEqual(self.previous, self.destination.read_bytes())
        self.assertEqual(self.original_names, set(self.output_dir.iterdir()))
        self.assertEqual(b"unrelated user file", self.unrelated.read_bytes())
        self.assertFalse(self.store.connection.in_transaction)

    def cli(self, *args: str):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = main(["--db", str(self.database), *args])
        return status, output.getvalue(), errors.getvalue()

    def test_success_stages_and_commits_actual_exporter_before_replacement(self) -> None:
        real_replace = os.replace
        real_factory = tempfile.NamedTemporaryFile
        stages = []
        def factory(**kwargs):
            self.assertEqual(self.output_dir, Path(kwargs["dir"]))
            self.assertFalse(kwargs["delete"])
            handle = real_factory(**kwargs)
            stages.append(Path(handle.name))
            return handle
        def replace_after_audit(source, destination):
            self.assertEqual(stages[0], Path(source))
            self.assertEqual(self.previous, self.destination.read_bytes())
            self.assertFalse(self.store.connection.in_transaction)
            self.assertEqual(self.record["document"], json.loads(Path(source).read_text(encoding="utf-8")))
            self.assertEqual(0o600, stat.S_IMODE(Path(source).stat().st_mode))
            with sqlite3.connect(self.database) as reader:
                actor, metadata = reader.execute(
                    "SELECT actor, metadata_json FROM audit_events WHERE action = 'requirements_exported'"
                ).fetchone()
            self.assertEqual("actual exporter", actor)
            self.assertFalse(json.loads(metadata)["delivery_confirmed"])
            real_replace(source, destination)
        with patch.object(store_module.tempfile, "NamedTemporaryFile", side_effect=factory), \
                patch.object(store_module.os, "replace", side_effect=replace_after_audit):
            returned = self.export(actor="  actual   exporter \n")
        self.assertEqual(self.record["document"], returned)
        self.assertEqual(returned, json.loads(self.destination.read_text(encoding="utf-8")))
        self.assertEqual(self.original_names, set(self.output_dir.iterdir()))
        self.assertFalse(stages[0].exists())
        event = self.events()[0]
        self.assertEqual("actual exporter", event["actor"])
        self.assertEqual("document-author", event["metadata"]["created_by"])
        self.assertEqual("file", event["metadata"]["destination_type"])
        self.assertEqual("audit_before_delivery", event["metadata"]["delivery_order"])
        self.assertEqual(self.record, self.store.get_requirements_document(self.record["id"]))

    def test_audit_insert_failure_rolls_back_and_preserves_destination(self) -> None:
        before = self.snapshot()
        real_audit = self.store._record_audit
        def fail_after_insert(**kwargs):
            real_audit(**kwargs)
            raise sqlite3.IntegrityError("injected audit insertion failure")
        with patch.object(self.store, "_record_audit", side_effect=fail_after_insert), \
                patch.object(store_module.os, "replace") as replace:
            with self.assertRaises(sqlite3.IntegrityError):
                self.export()
        replace.assert_not_called()
        self.assertEqual(before, self.snapshot())
        self.assert_retained_and_clean()

    def test_commit_failure_rolls_back_and_preserves_destination(self) -> None:
        before = self.snapshot()
        def deny_commit(action, name, _second, _database, _trigger):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION and name == "COMMIT" else sqlite3.SQLITE_OK
        self.store.connection.set_authorizer(deny_commit)
        try:
            with patch.object(store_module.os, "replace") as replace:
                with self.assertRaises(sqlite3.DatabaseError):
                    self.export()
            replace.assert_not_called()
        finally:
            self.store.connection.set_authorizer(None)
        self.assertEqual(before, self.snapshot())
        self.assert_retained_and_clean()
        self.export()  # Rollback left the connection usable.

    def test_invalid_actors_reject_before_output_audit_or_directory_creation(self) -> None:
        before = self.snapshot()
        missing = self.directory / "not-created" / "nested" / "out.json"
        for actor in (None, "", " \n ", "unknown", "system", "automation", "agent", "\ud800", True, 42, [], {}):
            with self.subTest(actor=actor):
                with self.assertRaises(ValidationError):
                    self.export(actor=actor)
                with self.assertRaises(ValidationError):
                    self.export(missing, actor=actor)
                self.assertFalse(missing.parent.exists())
                self.assertEqual(before, self.snapshot())
                self.assert_retained_and_clean()

    def test_missing_actor_is_an_intentional_required_api_argument(self) -> None:
        before = self.snapshot()
        with self.assertRaises(TypeError):
            self.store.export_requirements(self.record["id"], self.destination)
        self.assertEqual(before, self.snapshot())
        self.assert_retained_and_clean()

    def test_staging_write_flush_close_and_short_write_failures_cleanup(self) -> None:
        real_factory = tempfile.NamedTemporaryFile
        before = self.snapshot()
        for phase in ("write", "short_write", "flush", "close"):
            with self.subTest(phase=phase):
                def factory(**kwargs):
                    return FaultingStage(real_factory(**kwargs), phase)
                with patch.object(store_module.tempfile, "NamedTemporaryFile", side_effect=factory), \
                        patch.object(store_module.os, "replace") as replace:
                    with self.assertRaises(OSError):
                        self.export()
                replace.assert_not_called()
                self.assertEqual(before, self.snapshot())
                self.assert_retained_and_clean()

    def test_staging_fsync_failure_cleans_before_audit(self) -> None:
        before = self.snapshot()
        with patch.object(store_module.os, "fsync", side_effect=OSError("injected disk sync failure")):
            with self.assertRaises(OSError):
                self.export()
        self.assertEqual(before, self.snapshot())
        self.assert_retained_and_clean()

    def test_parent_and_temporary_creation_permission_failures_are_clean(self) -> None:
        before = self.snapshot()
        for target in ((Path, "mkdir"), (store_module.tempfile, "NamedTemporaryFile")):
            with self.subTest(target=target[1]), patch.object(*target, side_effect=PermissionError("injected permission denial")):
                with self.assertRaises(PermissionError):
                    self.export()
            self.assertEqual(before, self.snapshot())
            self.assert_retained_and_clean()

    def test_serialization_failure_is_controlled_and_has_no_side_effects(self) -> None:
        before = self.snapshot()
        real_dumps = json.dumps
        missing = self.directory / "not-created" / "out.json"
        for error in (TypeError("injected serialization failure"), ValueError("injected value error"),
                      RecursionError("injected serialization depth failure")):
            with self.subTest(error=type(error).__name__):
                def failing_dumps(value, **kwargs):
                    if kwargs.get("indent") == 2:
                        raise error
                    return real_dumps(value, **kwargs)
                with patch.object(store_module.json, "dumps", side_effect=failing_dumps):
                    with self.assertRaises(ValidationError):
                        self.export()
                    with self.assertRaises(ValidationError):
                        self.export(missing)
                self.assertFalse(missing.parent.exists())
                self.assertEqual(before, self.snapshot())
                self.assert_retained_and_clean()

    def test_integrity_failure_does_not_create_output_parents_or_audit(self) -> None:
        self.store.connection.execute("UPDATE requirements_documents SET content_hash = ? WHERE id = ?",
                                      ("0" * 64, self.record["id"]))
        self.store.connection.commit()
        before = self.snapshot()
        missing = self.directory / "not-created" / "out.json"
        with self.assertRaises(ValidationError):
            self.export()
        with self.assertRaises(ValidationError):
            self.export(missing)
        self.assertFalse(missing.parent.exists())
        self.assertEqual(before, self.snapshot())
        self.assert_retained_and_clean()

    def test_replacement_failure_preserves_destination_but_keeps_committed_audit(self) -> None:
        before = self.snapshot()
        with patch.object(store_module.os, "replace", side_effect=PermissionError("injected replacement failure")):
            with self.assertRaisesRegex(OSError, "audit committed but delivery failed/unconfirmed"):
                self.export()
        self.assertEqual(before[0], self.snapshot()[0])
        self.assertEqual(len(before[1]) + 1, len(self.snapshot()[1]))
        self.assertEqual(1, len(self.events()))
        self.assertEqual("actual-exporter", self.events()[0]["actor"])
        self.assertFalse(self.events()[0]["metadata"]["delivery_confirmed"])
        self.assert_retained_and_clean()

    def test_missing_destination_stays_absent_on_audit_or_replacement_failure(self) -> None:
        missing = self.output_dir / "absent.json"
        before = self.snapshot()
        with patch.object(self.store, "_record_audit", side_effect=sqlite3.IntegrityError("injected failure")):
            with self.assertRaises(sqlite3.IntegrityError):
                self.export(missing)
        self.assertFalse(missing.exists())
        self.assertEqual(before, self.snapshot())
        self.assert_retained_and_clean()
        with patch.object(store_module.os, "replace", side_effect=OSError("injected failure")):
            with self.assertRaisesRegex(OSError, "audit committed"):
                self.export(missing)
        self.assertFalse(missing.exists())
        self.assertEqual(1, len(self.events()))
        self.assert_retained_and_clean()

    def test_new_parent_directories_are_supported_and_temporary_file_removed(self) -> None:
        destination = self.directory / "new" / "nested" / "out.json"
        exported = self.export(destination)
        self.assertEqual(exported, json.loads(destination.read_text(encoding="utf-8")))
        self.assertEqual({destination}, set(destination.parent.iterdir()))
        self.assertEqual(0o600, stat.S_IMODE(destination.stat().st_mode))
        self.assertEqual(1, len(self.events()))

    def test_destination_directory_is_not_removed_on_failed_replacement(self) -> None:
        destination = self.output_dir / "directory"
        destination.mkdir()
        child = destination / "retained.txt"
        child.write_text("retain this", encoding="utf-8")
        with self.assertRaisesRegex(OSError, "audit committed"):
            self.export(destination)
        self.assertEqual("retain this", child.read_text(encoding="utf-8"))
        self.assertEqual(self.original_names | {destination}, set(self.output_dir.iterdir()))
        self.assertEqual(1, len(self.events()))

    def test_active_database_and_symlink_alias_are_rejected_without_audit(self) -> None:
        alias = self.output_dir / "database-link"
        alias.symlink_to(self.database)
        before = self.snapshot()
        for destination in (self.database, alias):
            with self.subTest(destination=destination), self.assertRaises(ValidationError):
                self.export(destination)
        self.assertTrue(alias.is_symlink())
        self.assertEqual(before, self.snapshot())
        self.assertEqual([], self.events())
        alias.unlink()
        self.assert_retained_and_clean()

    def test_existing_symlink_is_replaced_not_its_unrelated_target(self) -> None:
        target = self.output_dir / "unrelated-target.txt"
        target.write_text("retain target", encoding="utf-8")
        link = self.output_dir / "export-link.json"
        link.symlink_to(target)
        exported = self.export(link)
        self.assertFalse(link.is_symlink())
        self.assertEqual("retain target", target.read_text(encoding="utf-8"))
        self.assertEqual(exported, json.loads(link.read_text(encoding="utf-8")))
        self.assertEqual(self.original_names | {target, link}, set(self.output_dir.iterdir()))

    def test_stream_success_commits_exporter_before_output_and_does_not_close_stream(self) -> None:
        case = self
        class ObservedStream(io.StringIO):
            def write(self, text):
                case.assertFalse(case.store.connection.in_transaction)
                case.assertEqual(1, len(case.events()))
                case.assertEqual("stream-exporter", case.events()[0]["actor"])
                return super().write(text)
        stream = ObservedStream()
        exported = self.export(stream, actor="stream-exporter")
        self.assertFalse(stream.closed)
        self.assertEqual(exported, json.loads(stream.getvalue()))
        metadata = self.events()[0]["metadata"]
        self.assertEqual("stream", metadata["destination_type"])
        self.assertEqual("-", metadata["destination"])
        self.assertFalse(metadata["delivery_confirmed"])
        self.assert_retained_and_clean()

    def test_stream_is_untouched_when_audit_insert_or_commit_fails(self) -> None:
        stream = io.StringIO("retain stream bytes")
        before = self.snapshot()
        with patch.object(self.store, "_record_audit", side_effect=sqlite3.IntegrityError("injected failure")):
            with self.assertRaises(sqlite3.IntegrityError):
                self.export(stream)
        self.assertEqual("retain stream bytes", stream.getvalue())
        def deny_commit(action, name, _second, _database, _trigger):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION and name == "COMMIT" else sqlite3.SQLITE_OK
        self.store.connection.set_authorizer(deny_commit)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                self.export(stream)
        finally:
            self.store.connection.set_authorizer(None)
        self.assertEqual("retain stream bytes", stream.getvalue())
        self.assertFalse(stream.closed)
        self.assertEqual(before, self.snapshot())
        self.assert_retained_and_clean()

    def test_stream_write_short_write_and_flush_failures_keep_audit_and_may_emit_data(self) -> None:
        for phase in ("write", "short_write", "flush"):
            with self.subTest(phase=phase):
                class FailingStream(io.StringIO):
                    def write(self, text):
                        if phase in {"write", "short_write"}:
                            count = super().write(text[:8])
                            if phase == "write":
                                raise OSError("injected partial stream failure")
                            return count
                        return super().write(text)
                    def flush(self):
                        if phase == "flush":
                            raise OSError("injected stream flush failure")
                        super().flush()
                stream = FailingStream()
                previous_events = len(self.events())
                with self.assertRaisesRegex(OSError, "audit committed but delivery failed/unconfirmed"):
                    self.export(stream)
                self.assertFalse(stream.closed)
                self.assertTrue(stream.getvalue())
                self.assertEqual(previous_events + 1, len(self.events()))
                self.assertFalse(self.events()[-1]["metadata"]["delivery_confirmed"])
                self.assert_retained_and_clean()

    def test_new_empty_parents_may_remain_after_failed_export_but_no_temp_remains(self) -> None:
        destination = self.directory / "new" / "nested" / "out.json"
        before = self.snapshot()
        with patch.object(self.store, "_record_audit", side_effect=sqlite3.IntegrityError("injected failure")):
            with self.assertRaises(sqlite3.IntegrityError):
                self.export(destination)
        self.assertTrue(destination.parent.is_dir())
        self.assertEqual([], list(destination.parent.iterdir()))
        self.assertEqual(before, self.snapshot())

    def test_cleanup_permission_failure_reports_owned_path_and_audit_state(self) -> None:
        for failure_phase in ("audit", "replacement"):
            with self.subTest(failure_phase=failure_phase):
                previous_events = len(self.events())
                target = self.store if failure_phase == "audit" else store_module.os
                method = "_record_audit" if failure_phase == "audit" else "replace"
                error = sqlite3.IntegrityError("injected failure") if failure_phase == "audit" else OSError("injected failure")
                with patch.object(target, method, side_effect=error), \
                        patch.object(Path, "unlink", side_effect=PermissionError("injected cleanup denial")):
                    with self.assertRaisesRegex(OSError, "cleanup failed") as raised:
                        self.export()
                owned = set(self.output_dir.iterdir()) - self.original_names
                self.assertEqual(1, len(owned))
                remaining = owned.pop()
                self.assertIn(str(remaining), str(raised.exception))
                self.assertEqual(failure_phase == "replacement", "audit committed" in str(raised.exception))
                self.assertEqual(previous_events + (failure_phase == "replacement"), len(self.events()))
                self.assertFalse(self.store.connection.in_transaction)
                remaining.unlink()  # Deliberate fault removed: clean only this fixture's owned file.
                self.assert_retained_and_clean()

    def test_cli_file_and_stdout_success_attribute_supplied_exporters(self) -> None:
        status, output, errors = self.cli("requirements-export", str(self.record["id"]), str(self.destination),
                                          "--actor", "file-exporter")
        self.assertEqual(0, status)
        self.assertEqual("", output)
        self.assertTrue(json.loads(errors)["exported"])
        self.assertEqual("file-exporter", self.events()[-1]["actor"])
        status, output, errors = self.cli("requirements-export", str(self.record["id"]), "-",
                                          "--actor", "stdout-exporter")
        self.assertEqual(0, status)
        self.assertEqual(self.record["document"], json.loads(output))
        self.assertEqual("", errors)
        self.assertEqual("stdout-exporter", self.events()[-1]["actor"])

    def test_cli_invalid_actors_do_not_modify_destination_or_audits(self) -> None:
        before = self.snapshot()
        for actor in ("", " \n ", "system", "automation", "unknown"):
            with self.subTest(actor=actor):
                status, output, errors = self.cli("requirements-export", str(self.record["id"]),
                                                  str(self.destination), "--actor", actor)
                self.assertEqual(2, status)
                self.assertEqual("", output)
                self.assertIn("human actor", errors)
                self.assertNotIn("Traceback", errors)
                self.assertEqual(before, self.snapshot())
                self.assert_retained_and_clean()

    def test_cli_missing_actor_is_exit_two_without_destination_changes(self) -> None:
        before = self.snapshot()
        result = subprocess.run(
            [sys.executable, "-m", "media_house.cli", "--db", str(self.database), "requirements-export",
             str(self.record["id"]), str(self.destination)],
            capture_output=True, text=True, timeout=15, check=False,
        )
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("--actor", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(before, self.snapshot())
        self.assert_retained_and_clean()

    def test_cli_self_referential_symlink_is_exit_two_without_side_effects(self) -> None:
        before = tuple(self.store.connection.iterdump())
        fixture_bytes = {
            path: path.read_bytes()
            for path in (self.directory / "requirements-leads.csv", self.destination, self.unrelated)
        }
        loop_path = self.output_dir / "loop.json"
        try:
            loop_path.symlink_to(loop_path.name)
        except NotImplementedError as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")
        except OSError as exc:
            if exc.errno in {errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP}:
                self.skipTest(f"symlink creation is unavailable: {exc}")
            raise
        self.addCleanup(loop_path.unlink, missing_ok=True)

        result = subprocess.run(
            [sys.executable, "-m", "media_house.cli", "--db", str(self.database), "requirements-export",
             str(self.record["id"]), str(loop_path), "--actor", "reviewer"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertEqual("", result.stdout)
        self.assertTrue(
            result.stderr.startswith("error: requirements export destination could not be resolved:"),
            result.stderr,
        )
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn('"exported"', result.stderr)
        self.assertEqual([], self.events())
        self.assertEqual(before, tuple(self.store.connection.iterdump()))
        self.assertEqual(fixture_bytes, {path: path.read_bytes() for path in fixture_bytes})
        self.assertTrue(loop_path.is_symlink())
        self.assertEqual(loop_path.name, os.readlink(loop_path))
        self.assertEqual(self.original_names | {loop_path}, set(self.output_dir.iterdir()))
        self.assertFalse(self.store.connection.in_transaction)

    def test_cli_audit_failure_is_exit_two_without_file_changes_or_success_message(self) -> None:
        before = self.snapshot()
        with patch.object(MediaHouseStore, "_record_audit", side_effect=sqlite3.IntegrityError("injected failure")):
            status, output, errors = self.cli("requirements-export", str(self.record["id"]),
                                              str(self.destination), "--actor", "cli-exporter")
        self.assertEqual(2, status)
        self.assertEqual("", output)
        self.assertIn("error", errors)
        self.assertNotIn("exported", errors)
        self.assertNotIn("Traceback", errors)
        self.assertEqual(before, self.snapshot())
        self.assert_retained_and_clean()

    def test_cli_replacement_failure_reports_committed_audit_without_success_message(self) -> None:
        with patch.object(store_module.os, "replace", side_effect=PermissionError("injected failure")):
            status, output, errors = self.cli("requirements-export", str(self.record["id"]),
                                              str(self.destination), "--actor", "cli-exporter")
        self.assertEqual(2, status)
        self.assertEqual("", output)
        self.assertIn("audit committed but delivery failed/unconfirmed", errors)
        self.assertNotIn('"exported"', errors)
        self.assertNotIn("Traceback", errors)
        self.assertEqual("cli-exporter", self.events()[0]["actor"])
        self.assertFalse(self.events()[0]["metadata"]["delivery_confirmed"])
        self.assert_retained_and_clean()

    def test_cli_commit_failure_rolls_back_and_cleans_without_success_message(self) -> None:
        before = self.snapshot()
        real_transaction = MediaHouseStore._transaction
        def deny_commit(action, name, _second, _database, _trigger):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION and name == "COMMIT" else sqlite3.SQLITE_OK
        @contextmanager
        def failing_commit(store):
            store.connection.set_authorizer(deny_commit)
            try:
                with real_transaction(store):
                    yield
            finally:
                store.connection.set_authorizer(None)
        with patch.object(MediaHouseStore, "_transaction", failing_commit):
            status, output, errors = self.cli("requirements-export", str(self.record["id"]),
                                              str(self.destination), "--actor", "cli-exporter")
        self.assertEqual(2, status)
        self.assertEqual("", output)
        self.assertIn("error", errors)
        self.assertNotIn('"exported"', errors)
        self.assertNotIn("Traceback", errors)
        self.assertEqual(before, self.snapshot())
        self.assert_retained_and_clean()

    def test_cli_serialization_failure_is_nonzero_without_side_effects(self) -> None:
        before = self.snapshot()
        real_dumps = json.dumps
        def failing_dumps(value, **kwargs):
            if kwargs.get("indent") == 2:
                raise TypeError("injected serialization failure")
            return real_dumps(value, **kwargs)
        with patch.object(store_module.json, "dumps", side_effect=failing_dumps):
            status, output, errors = self.cli("requirements-export", str(self.record["id"]),
                                              str(self.destination), "--actor", "cli-exporter")
        self.assertEqual(2, status)
        self.assertEqual("", output)
        self.assertIn("could not be serialized", errors)
        self.assertNotIn("Traceback", errors)
        self.assertEqual(before, self.snapshot())
        self.assert_retained_and_clean()

    def test_closed_stream_failure_keeps_audit_but_is_reported_as_unconfirmed(self) -> None:
        stream = io.StringIO()
        stream.close()
        with self.assertRaisesRegex(OSError, "audit committed but delivery failed/unconfirmed"):
            self.export(stream)
        self.assertTrue(stream.closed)
        self.assertEqual(1, len(self.events()))
        self.assertFalse(self.events()[0]["metadata"]["delivery_confirmed"])
        self.assert_retained_and_clean()
