"""Offline adversarial regressions; schema parity is NOT full JSON Schema validation."""

from __future__ import annotations

import copy
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from media_house import requirements as contract
from media_house.cli import main
from media_house.errors import ApprovalError, ValidationError
from media_house.store import MediaHouseStore
from tests.test_requirements import lead_row, requirements_document, write_csv


SCHEMA_PATH = Path(__file__).resolve().parents[1] / (
    "media_house_complete_documentation/media_house_starter/schemas/client_requirements.schema.json"
)


def complete_document(status: str = "DRAFT") -> dict:
    """Build a synthetic requirements document that also populates optional integrations."""
    document = requirements_document(status=status, all_approved=True)
    document["integrations"] = [{
        "provider": "Synthetic CRM", "purpose": "Read enquiries", "access_confirmed": False,
        "failure_fallback": "Human follow-up",
    }]
    return document


def schema_fields(schema: dict, path: tuple = ()):
    """Enumerate declared fields for test inputs, not a schema-validation engine."""
    for field, definition in schema.get("properties", {}).items():
        child = (*path, field)
        yield child, definition
        yield from schema_fields(definition, child)
    if "items" in schema:
        yield from schema_fields(schema["items"], (*path, 0))


def replace(document: dict, path: tuple, value: object) -> None:
    """Set the value at the given nested path within a document in place."""
    parent = document
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value


def signed_document(status: str = "APPROVED") -> dict:
    """Build a complete document with a valid, hash-matching approval block attached."""
    document = complete_document(status)
    document["approval"] = {
        "approved_by": "founder", "approved_at": "2026-10-20T12:00:00Z",
        "scope_version_hash": contract.content_hash(document),
    }
    return document


class RequirementsContractAdversarialTests(unittest.TestCase):
    """Adversarial checks that the Python contract module matches the JSON schema."""

    @classmethod
    def setUpClass(cls) -> None:
        """Load the client requirements JSON schema once for all test cases."""
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_actual_schema_enums_match_contract_constants(self) -> None:
        """Verify schema enum values match the contract module's constant sets."""
        self.assertEqual("https://json-schema.org/draft/2020-12/schema", self.schema["$schema"])
        enums = {
            ("status",): contract.DOCUMENT_STATUSES,
            ("requirements", 0, "status"): contract.REQUIREMENT_STATUSES,
            ("requirements", 0, "priority"): contract.REQUIREMENT_PRIORITIES,
            ("data_handling", "hosting_mode"): contract.HOSTING_MODES,
        }
        definitions = dict(schema_fields(self.schema))
        for path, expected in enums.items():
            with self.subTest(path=path):
                self.assertEqual(expected, frozenset(definitions[path]["enum"]))
        languages = self.schema["properties"]["client"]["properties"]["preferred_languages"]
        self.assertEqual(contract.LANGUAGES, frozenset(languages["items"]["enum"]))

    def test_declared_nonnullable_fields_reject_null(self) -> None:
        """Verify every schema field not declared nullable rejects a null value."""
        for path, definition in schema_fields(self.schema):
            if path[0] == "approval":
                continue  # Approval has its own lifecycle matrix below.
            allowed = definition.get("type", [])
            if allowed == "null" or isinstance(allowed, list) and "null" in allowed:
                continue
            with self.subTest(path=path):
                document = complete_document()
                replace(document, path, None)
                with self.assertRaises(ValidationError):
                    contract.validate_document(document)

    def test_actual_schema_required_fields_cannot_be_omitted(self) -> None:
        """Verify omitting any schema-required field raises a validation error."""
        nodes = [((), self.schema), *schema_fields(self.schema)]
        for path, definition in nodes:
            if "items" in definition:
                path, definition = (*path, 0), definition["items"]
            for field in definition.get("required", []):
                with self.subTest(path=path, missing=field):
                    document = complete_document()
                    parent = document
                    for key in path:
                        parent = parent[key]
                    del parent[field]
                    with self.assertRaises(ValidationError):
                        contract.validate_document(document)

    def test_schema_nullable_goals_and_fees_and_optional_omissions_are_accepted(self) -> None:
        """Verify nullable goal/fee values and omission of optional sections are both accepted."""
        for value in (None, 0, 1.5, "not measured"):
            with self.subTest(goal_value=value):
                document = complete_document()
                document["goals"][0].update(baseline=value, target=value)
                contract.validate_document(document)
        document = complete_document()
        document["commercial"].update(setup_fee=None, recurring_fee=123.5)
        contract.validate_document(document)
        document.pop("goals")
        document.pop("commercial")
        document.pop("integrations")
        document.pop("ai_constraints")
        document.pop("open_questions_and_risks")
        contract.validate_document(document)

    def test_requirement_ids_are_well_formed_unique_and_statuses_not_promoted(self) -> None:
        """Verify malformed requirement IDs are rejected and valid documents pass through unchanged."""
        for identifier in ("REQ-01", "REQ-001\n", "OTHER-001", "REQ-002"):
            with self.subTest(identifier=identifier):
                document = complete_document()
                document["requirements"][0]["id"] = identifier
                with self.assertRaises(ValidationError):
                    contract.validate_document(document)
        document = requirements_document()
        before = copy.deepcopy(document)
        self.assertEqual(before, contract.validate_document(document))
        self.assertEqual(before, document)

    def test_malformed_enum_types_reject_without_raw_type_errors(self) -> None:
        """Verify non-string values for enum fields raise ValidationError, not raw type errors."""
        paths = (("status",), ("requirements", 0, "status"), ("requirements", 0, "priority"),
                 ("data_handling", "hosting_mode"), ("client", "preferred_languages", 0))
        for path in paths:
            for value in (None, [], {}, True, 1, "UNSUPPORTED"):
                with self.subTest(path=path, value=value):
                    document = complete_document()
                    replace(document, path, value)
                    with self.assertRaises(ValidationError):
                        contract.validate_document(document)

    def test_lifecycle_metadata_and_content_matrix(self) -> None:
        """Verify requirement/criteria emptiness and approval metadata rules across every lifecycle status."""
        for status in sorted(contract.DOCUMENT_STATUSES):
            document = signed_document(status) if status in {"APPROVED", "SUPERSEDED"} else complete_document(status)
            with self.subTest(status=status, case="valid"):
                self.assertEqual(document, contract.validate_document(document))
            for field in ("requirements", "acceptance_criteria"):
                changed = copy.deepcopy(document)
                changed[field] = []
                if status in {"APPROVED", "SUPERSEDED"}:
                    changed["approval"]["scope_version_hash"] = contract.content_hash(changed)
                with self.subTest(status=status, empty=field):
                    if status in {"DRAFT", "NEEDS_CLARIFICATION"}:
                        contract.validate_document(changed)
                    else:
                        with self.assertRaises(ValidationError):
                            contract.validate_document(changed)
            for metadata in (None, {}, {"approved_by": "founder"}, "approved", []):
                changed = copy.deepcopy(document)
                changed["approval"] = metadata
                with self.subTest(status=status, metadata=metadata):
                    if status in contract.CAPTURE_STATUSES and metadata is None:
                        contract.validate_document(changed)
                    else:
                        with self.assertRaises(ValidationError):
                            contract.validate_document(changed)

    def test_approved_states_bind_hash_and_explicit_requirement_labels(self) -> None:
        """Verify APPROVED/SUPERSEDED documents reject mismatched hashes and unresolved requirement statuses."""
        for status in ("APPROVED", "SUPERSEDED"):
            document = signed_document(status)
            for path, value in (
                (("approval", "scope_version_hash"), "0" * 64),
                (("approval", "approved_at"), "2026-10-20T12:00:00"),
                (("approval", "approved_by"), ""),
                (("requirements", 0, "status"), "ASSUMPTION"),
            ):
                with self.subTest(status=status, path=path):
                    changed = copy.deepcopy(document)
                    replace(changed, path, value)
                    with self.assertRaises(ValidationError):
                        contract.validate_document(changed)
        with self.assertRaises(ValidationError):
            contract.approve_document(complete_document("READY_FOR_REVIEW"), actor="founder",
                                      approved_at="2026-10-20T12:00:00Z", scope_hash="0" * 64)

    def test_approval_timestamps_require_explicit_valid_datetime(self) -> None:
        """Verify timestamp normalization rejects malformed values and normalizes valid ones to UTC."""
        for value in ("20261020", "2026-10-20", "2026-10-20T12:00:00", "2026-10-20 12:00:00Z",
                      " 2026-10-20T12:00:00Z", "2026-10-20T12:00:00Z ",
                      "２０２６-10-20T12:00:00Z",
                      "2026-10-20T12:00:00+24:00", "2026-10-20T12:00:00+00:60",
                      "0001-01-01T00:00:00+01:00", "2026-02-30T12:00:00Z", None, []):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                contract.normalize_approval_timestamp(value)
        self.assertEqual("2026-10-20T12:00:00Z",
                         contract.normalize_approval_timestamp("2026-10-20T17:30:00+05:30"))
        self.assertEqual("2026-10-20T12:00:00Z",
                         contract.normalize_approval_timestamp("2026-10-20t12:00:00z"))

    def test_every_material_schema_field_changes_scope_hash(self) -> None:
        """Verify changing any material field alters the content hash while key order does not."""
        document = complete_document()
        document.update(meeting_id=1, prospect_id=1)
        document["requirements"][0].update(actor="Owner", trigger="Enquiry received",
                                            expected_behavior="Queue follow-up", exception_behavior="Human handoff")
        paths = [path for path, _ in schema_fields(self.schema) if path[0] not in {"status", "approval"}]
        paths += [("meeting_id",), ("prospect_id",)]
        paths += [("requirements", 0, field) for field in
                  ("actor", "trigger", "expected_behavior", "exception_behavior")]
        digest = contract.content_hash(document)
        for path in paths:
            with self.subTest(path=path):
                changed = copy.deepcopy(document)
                parent = changed
                for key in path[:-1]:
                    parent = parent[key]
                value = parent[path[-1]]
                replacement = (not value) if isinstance(value, bool) else (
                    value + " changed" if isinstance(value, str) else
                    value + 1 if isinstance(value, (int, float)) else
                    [*value, "changed"] if isinstance(value, list) else
                    {**value, "changed": True} if isinstance(value, dict) else 123
                )
                parent[path[-1]] = replacement
                self.assertNotEqual(digest, contract.content_hash(changed))
        reordered = dict(reversed(list(document.items())))
        self.assertEqual(digest, contract.content_hash(reordered))
        reordered.update(status="SUPERSEDED", approval={"approved_by": "someone"})
        self.assertEqual(digest, contract.content_hash(reordered))

    def test_nonfinite_unicode_and_non_json_extensions_are_validation_errors(self) -> None:
        """Verify non-finite numbers, lone surrogates, and non-JSON values raise ValidationError."""
        for value in (float("nan"), float("inf"), float("-inf"), "\ud800", object()):
            with self.subTest(value=repr(value)):
                document = complete_document()
                document["extension"] = value
                with self.assertRaises(ValidationError):
                    contract.validate_document(document)
                with self.assertRaises(ValidationError):
                    contract.content_hash(document)


class RequirementsStoreAdversarialTests(unittest.TestCase):
    """Adversarial checks of store-level persistence, concurrency, and CLI error handling."""

    def setUp(self) -> None:
        """Create an on-disk store seeded with a prospect and a recorded meeting."""
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.directory = Path(self.tempdir.name)
        self.database = self.directory / "synthetic.sqlite3"
        self.store = MediaHouseStore(self.database)
        self.addCleanup(self.store.close)
        self.store.import_csv(write_csv(self.directory, [lead_row()]), researcher="researcher")
        self.meeting = self.store.record_meeting(
            self.store.list_prospects()[0]["id"], meeting_at="2026-10-20T12:00:00Z",
            notes="Synthetic only", notes_permission="permission_granted", recording_policy="not_recorded",
            actor="founder",
        )

    def capture(self, *, status: str = "READY_FOR_REVIEW", supersedes: int | None = None) -> dict:
        """Capture a complete synthetic document for the fixture meeting, optionally superseding another."""
        document = complete_document(status)
        document.pop("document_version")
        return self.store.capture_requirements(self.meeting["id"], document, actor="founder",
                                               supersedes_document_id=supersedes)

    def approve(self, identifier: int) -> dict:
        """Approve the given requirements document as the founder actor."""
        return self.store.approve_requirements(identifier, actor="founder", approved_at="2026-10-20T12:00:00Z")

    def snapshot(self) -> tuple:
        """Return an immutable snapshot of the requirements and audit tables for later comparison."""
        return tuple(tuple(tuple(row) for row in self.store.connection.execute(f"SELECT * FROM {table} ORDER BY id"))
                     for table in ("requirements_documents", "audit_events"))

    def update(self, identifier: int, **fields) -> None:
        """Directly update allowed columns of a requirements_documents row and commit."""
        self.assertTrue(set(fields) <= {"content_json", "status", "document_version", "content_hash",
                                       "approved_by", "approved_at", "approved_hash", "supersedes_document_id"})
        assignments = ", ".join(f"{key} = ?" for key in fields)
        self.store.connection.execute(f"UPDATE requirements_documents SET {assignments} WHERE id = ?",
                                      (*fields.values(), identifier))
        self.store.connection.commit()

    def cli(self, *args: str) -> tuple[int, str, str]:
        """Run the CLI with the fixture database and capture its exit status and output streams."""
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(["--db", str(self.database), *args])
        return status, stdout.getvalue(), stderr.getvalue()

    def assert_cli_error(self, *args: str) -> None:
        """Assert the CLI invocation fails cleanly with exit code 2 and no traceback."""
        status, stdout, stderr = self.cli(*args)
        self.assertEqual(2, status)
        self.assertEqual("", stdout)
        self.assertIn("error", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_every_state_rejects_tampering_on_get_list_export_and_cli(self) -> None:
        """Verify tampering with any field on any lifecycle state is detected by get/list/export/CLI without side effects."""
        records = [self.capture(status=status) for status in ("DRAFT", "NEEDS_CLARIFICATION", "READY_FOR_REVIEW")]
        baseline = self.approve(self.capture()["id"])
        current = self.approve(self.capture(supersedes=baseline["id"])["id"])
        records += [self.store.get_requirements_document(baseline["id"]), current]
        destination = self.directory / "retained-export.json"
        for record in records:
            approved = record["status"] in {"APPROVED", "SUPERSEDED"}
            corruptions = [
                {"status": "NEEDS_CLARIFICATION" if record["status"] == "DRAFT" else "DRAFT"},
                {"document_version": record["document_version"] + 100},
                {"content_hash": "0" * 64},
                {"approved_by": "other-human"}, {"approved_at": "2026-10-21T12:00:00Z"},
                {"approved_hash": "0" * 64},
            ]
            for field, value in (("approved_by", "other-human"), ("approved_at", "2026-10-21T12:00:00Z"),
                                 ("scope_version_hash", "0" * 64)):
                changed = copy.deepcopy(record["document"])
                changed["approval"] = dict(changed["approval"] or {})
                changed["approval"][field] = value
                corruptions.append({"content_json": json.dumps(changed)})
            changed = copy.deepcopy(record["document"])
            changed.update(status="DRAFT" if approved else "READY_FOR_REVIEW", approval=None)
            if changed["status"] == record["status"]:
                changed["status"] = "DRAFT"
            corruptions.append({"content_json": json.dumps(changed)})
            for changes in corruptions:
                with self.subTest(state=record["status"], changes=changes):
                    original = dict(self.store.connection.execute(
                        "SELECT * FROM requirements_documents WHERE id = ?", (record["id"],)
                    ).fetchone())
                    self.update(record["id"], **changes)
                    before = self.snapshot()
                    destination.write_text("retain this export", encoding="utf-8")
                    try:
                        with self.assertRaises(ValidationError):
                            self.store.get_requirements_document(record["id"])
                        with self.assertRaises(ValidationError):
                            self.store.list_requirements_documents()
                        with self.assertRaises(ValidationError):
                            self.store.export_requirements(record["id"], destination, actor="reviewer")
                        self.assert_cli_error("requirements-show", str(record["id"]))
                        self.assert_cli_error("requirements-list")
                        self.assert_cli_error("requirements-export", str(record["id"]), str(destination),
                                              "--actor", "reviewer")
                        self.assert_cli_error("requirements-approve", str(record["id"]), "--actor", "founder")
                        self.assertEqual("retain this export", destination.read_text(encoding="utf-8"))
                        self.assertEqual(before, self.snapshot())
                        self.assertFalse(self.store.connection.in_transaction)
                    finally:
                        self.update(record["id"], **{field: original[field] for field in changes})

    def test_malformed_enums_and_nulls_fail_through_cli_without_writes(self) -> None:
        """Verify CLI capture rejects malformed enum values and disallowed nulls without writing anything."""
        path = self.directory / "bad-requirements.json"
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        invalid = [(path, None) for path, definition in schema_fields(schema)
                   if path[0] != "approval" and "null" not in definition.get("type", [])]
        for location in (("status",), ("requirements", 0, "status"), ("requirements", 0, "priority"),
                         ("data_handling", "hosting_mode"), ("client", "preferred_languages", 0)):
            invalid += [(location, value) for value in ([], {}, True, 1)]
        for location, value in invalid:
            with self.subTest(location=location, value=value):
                document = complete_document()
                replace(document, location, value)
                path.write_text(json.dumps(document), encoding="utf-8")
                before = self.snapshot()
                self.assert_cli_error("requirements-capture", str(self.meeting["id"]), str(path), "--actor", "founder")
                self.assertEqual(before, self.snapshot())

    def test_capture_rejects_overwritten_invalid_metadata_and_defaults_only_omissions(self) -> None:
        """Verify capture rejects invalid overridden metadata fields but defaults version/status when omitted."""
        for field in ("document_version", "meeting_id", "prospect_id", "status"):
            for value in (None, True, [], {}, 1.0):
                with self.subTest(field=field, value=value):
                    document = complete_document()
                    document[field] = value
                    before = self.snapshot()
                    with self.assertRaises(ValidationError):
                        self.store.capture_requirements(self.meeting["id"], document, actor="founder", status="DRAFT")
                    self.assertEqual(before, self.snapshot())
        document = complete_document()
        document.pop("document_version")
        document.pop("status")
        record = self.store.capture_requirements(self.meeting["id"], document, actor="founder")
        self.assertEqual((1, "DRAFT"), (record["document_version"], record["status"]))

    def test_unlinked_preexisting_review_draft_cannot_create_second_baseline(self) -> None:
        """Verify approving a second unlinked draft after a baseline is already approved raises ApprovalError."""
        first, second = self.capture(), self.capture()
        approved = self.approve(first["id"])
        before = self.snapshot()
        with self.assertRaises(ApprovalError):
            self.approve(second["id"])
        self.assertEqual(before, self.snapshot())
        self.assertEqual(approved, self.store.get_requirements_document(first["id"]))

    def test_stale_sibling_and_double_approvals_preserve_current_baseline(self) -> None:
        """Verify stale sibling drafts and re-approval attempts cannot replace the current approved baseline."""
        first = self.approve(self.capture()["id"])
        second, sibling = self.capture(supersedes=first["id"]), self.capture(supersedes=first["id"])
        current = self.approve(second["id"])
        before = self.snapshot()
        for identifier in (sibling["id"], second["id"], first["id"]):
            with self.subTest(identifier=identifier), self.assertRaises(ApprovalError):
                self.store.approve_requirements(identifier, actor="other-human")
            self.assertEqual(before, self.snapshot())
        self.assertEqual(current, self.store.get_requirements_document(current["id"]))

    def test_late_audit_failure_rolls_back_approval_superseding_and_audits(self) -> None:
        """Verify an audit failure late in approval rolls back both superseding and approval writes."""
        first = self.approve(self.capture()["id"])
        candidate = self.capture(supersedes=first["id"])
        original_audit = self.store._record_audit
        for action in ("requirements_superseded", "requirements_approved"):
            before = self.snapshot()
            def fail_after_insert(**kwargs):
                """Perform the real audit insert, then raise to simulate a late failure for the target action."""
                original_audit(**kwargs)
                if kwargs["action"] == action:
                    raise sqlite3.IntegrityError("synthetic audit failure")
            with self.subTest(action=action), patch.object(self.store, "_record_audit", side_effect=fail_after_insert):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.approve(candidate["id"])
            self.assertEqual(before, self.snapshot())
            self.assertFalse(self.store.connection.in_transaction)
            self.assertEqual(first, self.store.get_requirements_document(first["id"]))
        self.approve(candidate["id"])  # A failed attempt did not poison the connection.

    def test_commit_failure_rolls_back_both_baselines_and_audits(self) -> None:
        """Verify a denied commit during approval rolls back both the predecessor and candidate baselines."""
        first = self.approve(self.capture()["id"])
        candidate = self.capture(supersedes=first["id"])
        before = self.snapshot()
        def deny_commit(action, name, _second, _database, _trigger):
            """Deny the COMMIT transaction action while allowing everything else."""
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION and name == "COMMIT" else sqlite3.SQLITE_OK
        self.store.connection.set_authorizer(deny_commit)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                self.approve(candidate["id"])
        finally:
            self.store.connection.set_authorizer(None)
        self.assertFalse(self.store.connection.in_transaction)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(first, self.store.get_requirements_document(first["id"]))
        self.approve(candidate["id"])

    def race(self, operation) -> list[tuple[str, object]]:
        """Run the given operation concurrently from two threads, each on its own store connection."""
        barrier = threading.Barrier(2, timeout=10)
        def worker(index):
            """Wait for both threads to be ready, then run the operation and classify its outcome."""
            with MediaHouseStore(self.database) as store:
                barrier.wait()
                try:
                    return "ok", operation(store, index)
                except (ValidationError, ApprovalError) as exc:
                    return "rejected", str(exc)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker, index) for index in range(2)]
            return [future.result(timeout=20) for future in futures]

    def test_concurrent_version_allocation_is_unique_and_atomic(self) -> None:
        """Verify concurrent captures without an explicit version each receive a unique, sequential version."""
        document = complete_document()
        document.pop("document_version")
        results = self.race(lambda store, index: store.capture_requirements(
            self.meeting["id"], document, actor=f"human-{index}"))
        self.assertEqual(["ok", "ok"], [outcome for outcome, _ in results])
        self.assertEqual([1, 2], sorted(record["document_version"] for _, record in results))
        self.assertEqual(2, len(self.store.list_audit_events(action="requirements_created")))

    def test_concurrent_duplicate_supplied_version_is_rejected_cleanly(self) -> None:
        """Verify concurrent captures of the same explicit version yield exactly one winner and one rejection."""
        results = self.race(lambda store, index: store.capture_requirements(
            self.meeting["id"], complete_document(), actor=f"human-{index}"))
        self.assertEqual(["ok", "rejected"], sorted(outcome for outcome, _ in results))
        self.assertEqual(1, len(self.store.list_requirements_documents()))
        self.assertEqual(1, len(self.store.list_audit_events(action="requirements_created")))

    def test_concurrent_unlinked_baseline_approvals_have_one_winner(self) -> None:
        """Verify concurrently approving two unlinked drafts leaves exactly one approved baseline."""
        identifiers = [self.capture()["id"], self.capture()["id"]]
        results = self.race(lambda store, index: store.approve_requirements(identifiers[index], actor=f"human-{index}"))
        self.assertEqual(["ok", "rejected"], sorted(outcome for outcome, _ in results))
        self.assertEqual(1, sum(record["status"] == "APPROVED" for record in self.store.list_requirements_documents()))
        self.assertEqual(1, len(self.store.list_audit_events(action="requirements_approved")))

    def test_concurrent_superseding_siblings_have_one_winner(self) -> None:
        """Verify concurrently approving two sibling candidates superseding the same baseline leaves one winner."""
        baseline = self.approve(self.capture()["id"])
        identifiers = [self.capture(supersedes=baseline["id"])["id"] for _ in range(2)]
        results = self.race(lambda store, index: store.approve_requirements(identifiers[index], actor=f"human-{index}"))
        self.assertEqual(["ok", "rejected"], sorted(outcome for outcome, _ in results))
        self.assertEqual("SUPERSEDED", self.store.get_requirements_document(baseline["id"])["status"])
        self.assertEqual(1, sum(record["status"] == "APPROVED" for record in self.store.list_requirements_documents()))
        self.assertEqual(1, len(self.store.list_audit_events(action="requirements_superseded")))
        self.assertEqual(2, len(self.store.list_audit_events(action="requirements_approved")))

    def test_concurrent_double_approval_never_replaces_winner_metadata(self) -> None:
        """Verify concurrently approving the same document twice never overwrites the winning approval metadata."""
        identifier = self.capture()["id"]
        results = self.race(lambda store, index: store.approve_requirements(identifier, actor=f"human-{index}"))
        self.assertEqual(["ok", "rejected"], sorted(outcome for outcome, _ in results))
        winner = next(record for outcome, record in results if outcome == "ok")
        self.assertEqual(winner, self.store.get_requirements_document(identifier))
        events = self.store.list_audit_events(action="requirements_approved")
        self.assertEqual(1, len(events))
        self.assertEqual(winner["approved_by"], events[0]["actor"])

    def test_failed_superseding_validation_keeps_baseline_and_candidate_unchanged(self) -> None:
        """Verify a candidate that fails approval validation leaves the baseline and candidate unchanged."""
        baseline = self.approve(self.capture()["id"])
        document = complete_document("READY_FOR_REVIEW")
        document.pop("document_version")
        document["requirements"][0]["status"] = "ASSUMPTION"
        candidate = self.store.capture_requirements(self.meeting["id"], document, actor="founder",
                                                     supersedes_document_id=baseline["id"])
        before = self.snapshot()
        with self.assertRaises(ApprovalError):
            self.approve(candidate["id"])
        self.assertEqual(before, self.snapshot())
        self.assertEqual(baseline, self.store.get_requirements_document(baseline["id"]))
        ready = self.capture(supersedes=baseline["id"])
        before = self.snapshot()
        self.assert_cli_error("requirements-approve", str(ready["id"]), "--actor", "founder",
                              "--approved-at", "20261020")
        self.assertEqual(before, self.snapshot())

    def test_corrupted_predecessor_aborts_superseding_without_partial_writes(self) -> None:
        """Verify a corrupted predecessor's hash aborts approval of its successor without partial writes."""
        baseline = self.approve(self.capture()["id"])
        candidate = self.capture(supersedes=baseline["id"])
        self.update(baseline["id"], approved_hash="0" * 64)
        before = self.snapshot()
        with self.assertRaises(ApprovalError):
            self.approve(candidate["id"])
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.store.connection.in_transaction)

    def test_superseding_must_be_forward_and_capture_must_link_current_baseline(self) -> None:
        """Verify superseding links must point forward and new captures must reference the current baseline."""
        older, newer = self.capture(), self.capture()
        self.approve(newer["id"])
        self.update(older["id"], supersedes_document_id=newer["id"])
        before = self.snapshot()
        with self.assertRaises(ApprovalError):
            self.approve(older["id"])
        with self.assertRaises(ValidationError):
            self.capture()
        with self.assertRaises(ValidationError):
            self.capture(supersedes=older["id"])
        self.assertEqual(before, self.snapshot())

    def test_failed_capture_audit_does_not_consume_version(self) -> None:
        """Verify a failed capture due to an audit error does not consume a document version number."""
        before = self.snapshot()
        with patch.object(self.store, "_record_audit", side_effect=sqlite3.IntegrityError("synthetic failure")):
            with self.assertRaises(sqlite3.IntegrityError):
                self.capture()
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.store.connection.in_transaction)
        self.assertEqual(1, self.capture()["document_version"])

    def test_malformed_json_unicode_and_deep_nesting_cli_fail_without_tracebacks(self) -> None:
        """Verify malformed JSON, invalid unicode, and deeply nested input fail the CLI cleanly without tracebacks."""
        path = self.directory / "malformed.json"
        document = complete_document()
        document["business_problem"]["client_statement"] = "\ud800"
        invalid = [b"{broken", b"[]", b"null", b"\xff", b'{"value": NaN}',
                   json.dumps(document).encode("utf-8"),
                   ("[" * 1100 + "0" + "]" * 1100).encode("utf-8")]
        for payload in invalid:
            with self.subTest(payload=payload[:30]):
                path.write_bytes(payload)
                before = self.snapshot()
                self.assert_cli_error("requirements-capture", str(self.meeting["id"]), str(path), "--actor", "founder")
                self.assertEqual(before, self.snapshot())

    def test_cli_expected_record_and_sqlite_errors_are_nonzero(self) -> None:
        """Verify missing-record and sqlite audit errors produce nonzero CLI exit codes."""
        self.assert_cli_error("requirements-show", "99999")
        self.assert_cli_error("requirements-approve", "99999", "--actor", "founder")
        self.assert_cli_error("requirements-export", "99999", "-", "--actor", "reviewer")
        identifier = self.capture()["id"]
        before = self.snapshot()
        with patch.object(MediaHouseStore, "_record_audit", side_effect=sqlite3.IntegrityError("synthetic failure")):
            self.assert_cli_error("requirements-approve", str(identifier), "--actor", "founder")
        self.assertEqual(before, self.snapshot())

    def test_oversized_sqlite_identifiers_return_nonzero_without_tracebacks(self) -> None:
        """Verify oversized integer identifiers are rejected by the CLI without raising tracebacks."""
        for args in (("requirements-show", str(2**100)),
                     ("requirements-list", "--meeting-id", str(2**100)),
                     ("requirements-approve", str(2**100), "--actor", "founder")):
            with self.subTest(args=args):
                self.assert_cli_error(*args)

    def test_superseding_identifier_rejects_boolean_and_noninteger_aliases(self) -> None:
        """Verify boolean, float, string, and container values are all rejected as supersedes identifiers."""
        baseline = self.approve(self.capture()["id"])
        before = self.snapshot()
        for value in (True, 1.0, str(baseline["id"]), [], {}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.capture(supersedes=value)
            self.assertEqual(before, self.snapshot())

    def test_deeply_nested_stored_json_is_a_controlled_read_failure(self) -> None:
        """Verify reading back a document whose stored JSON is too deeply nested fails in a controlled way."""
        record = self.capture()
        self.update(record["id"], content_json="[" * 1100 + "0" + "]" * 1100)
        before = self.snapshot()
        with self.assertRaises(ValidationError):
            self.store.get_requirements_document(record["id"])
        self.assert_cli_error("requirements-show", str(record["id"]))
        self.assertEqual(before, self.snapshot())

    def test_real_module_cli_returns_exit_two_for_expected_input_errors(self) -> None:
        """Verify running the CLI as a real subprocess returns exit code 2 for expected input errors."""
        path = self.directory / "bad-input.json"
        path.write_text("[" * 1100 + "0" + "]" * 1100, encoding="utf-8")
        for args in (("requirements-capture", str(self.meeting["id"]), str(path), "--actor", "founder"),
                     ("requirements-show", str(2**100))):
            with self.subTest(command=args[0]):
                process = subprocess.run(
                    [sys.executable, "-m", "media_house.cli", "--db", str(self.database), *args],
                    capture_output=True, text=True, timeout=15, check=False,
                )
                self.assertEqual(2, process.returncode)
                self.assertEqual("", process.stdout)
                self.assertIn("error", process.stderr)
                self.assertNotIn("Traceback", process.stderr)
