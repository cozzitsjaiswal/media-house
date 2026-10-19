from __future__ import annotations

import copy
import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from media_house.cli import main
from media_house.errors import ApprovalError, NotFoundError, ValidationError
from media_house.requirements import content_hash
from media_house.store import CSV_COLUMNS, MediaHouseStore


def lead_row(**overrides: str) -> dict[str, str]:
    row = {column: "" for column in CSV_COLUMNS}
    row.update(
        {
            "company_name": "Requirements Test Co",
            "website": "https://requirements.example",
            "region": "Maharashtra, India",
            "industry": "retail",
            "source_url": "https://directory.example/requirements-test",
            "source_checked_at": "2026-10-13",
            "verified_status": "verified",
            "contact_name": "unknown",
            "contact_role": "owner",
            "contact_channel": "email",
            "channel_permission_status": "granted",
            "fit_reason": "Synthetic requirements workflow fixture.",
            "fit_confidence": "0.8",
            "lead_status": "qualified",
            "opt_out": "false",
            "notes": "Synthetic only",
        }
    )
    row.update(overrides)
    return row


def write_csv(directory: Path, rows: list[dict[str, str]]) -> Path:
    path = directory / "requirements-leads.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def requirements_document(*, status: str = "DRAFT", all_approved: bool = False) -> dict[str, object]:
    statuses = (
        ("APPROVED", "OUT_OF_SCOPE", "APPROVED", "OUT_OF_SCOPE", "APPROVED", "OUT_OF_SCOPE", "APPROVED")
        if all_approved
        else (
            "CLIENT_STATED",
            "OBSERVED",
            "PROPOSED",
            "ASSUMPTION",
            "OPEN_QUESTION",
            "APPROVED",
            "OUT_OF_SCOPE",
        )
    )
    return {
        "client": {
            "business_name": "Requirements Test Co",
            "industry": "retail",
            "region": "Maharashtra, India",
            "preferred_languages": ["en"],
            "decision_maker_role": "Owner",
        },
        "document_version": 1,
        "status": status,
        "approval": None,
        "business_problem": {
            "client_statement": "Synthetic client statement about manual enquiry follow-up.",
            "current_workflow": ["Receive enquiry", "Record it in a spreadsheet", "Follow up manually"],
            "impact_evidence": ["Synthetic evidence only"],
            "existing_systems": ["Spreadsheet"],
        },
        "goals": [{"metric": "follow-up time", "baseline": None, "target": None, "measurement_method": "Owner review"}],
        "requirements": [
            {
                "id": f"REQ-{index:03d}",
                "status": requirement_status,
                "priority": "MUST" if index == 1 else "SHOULD",
                "description": f"Synthetic requirement {index}",
                "acceptance_test": f"Verify synthetic requirement {index}",
                "notes": f"Synthetic source/status note for {requirement_status}",
            }
            for index, requirement_status in enumerate(statuses, start=1)
        ],
        "scope": {
            "included": ["Enquiry follow-up workflow"],
            "excluded": ["Full ERP replacement"],
            "dependencies": ["Client confirms source-of-truth workflow"],
            "change_control": "Written change request required",
        },
        "data_handling": {
            "hosting_mode": "UNDECIDED",
            "data_categories": ["Business enquiries"],
            "external_processors": [],
            "retention": "To be agreed before implementation",
            "export_and_deletion": "Manual export and deletion plan required",
            "health_data_in_scope": False,
        },
        "integrations": [],
        "ai_constraints": {
            "allowed_actions": ["Draft follow-up suggestions"],
            "human_approval_actions": ["External communication"],
            "human_handoff": "Founder review",
            "ai_disclosure": "Disclose AI assistance where applicable",
        },
        "commercial": {
            "currency": "INR",
            "setup_fee": None,
            "recurring_fee": None,
            "payment_milestones": [],
            "support_scope": "Not approved in this requirements draft",
        },
        "acceptance_criteria": [
            {
                "id": "AC-001",
                "steps": ["Use synthetic enquiry", "Run the scoped workflow"],
                "expected_result": "The enquiry is visible for human follow-up",
                "evidence": "Synthetic test output",
            }
        ],
        "open_questions_and_risks": ["Which system is the authoritative source of truth?"],
    }


class RequirementsWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MediaHouseStore(":memory:")
        self.tempdir = tempfile.TemporaryDirectory()
        self.directory = Path(self.tempdir.name)
        self.store.import_csv(write_csv(self.directory, [lead_row()]), researcher="human-researcher")
        prospect = self.store.list_prospects()[0]
        self.meeting = self.store.record_meeting(
            prospect["id"],
            meeting_at="2026-10-20T10:00:00+05:30",
            notes="Synthetic meeting notes with no real customer data.",
            notes_permission="permission_granted",
            recording_policy="not_recorded",
            actor="founder",
        )

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def capture(self, document: dict[str, object], **kwargs: object) -> dict[str, object]:
        return self.store.capture_requirements(
            int(self.meeting["id"]), document, actor="founder", **kwargs
        )

    def test_capture_is_tied_to_meeting_and_preserves_all_requirement_statuses(self) -> None:
        record = self.capture(requirements_document())
        document = record["document"]
        self.assertEqual(int(self.meeting["id"]), record["meeting_id"])
        self.assertEqual(int(self.meeting["prospect_id"]), record["prospect_id"])
        self.assertEqual(1, record["document_version"])
        self.assertEqual("DRAFT", record["status"])
        self.assertIsNone(document["approval"])
        self.assertEqual(
            ["CLIENT_STATED", "OBSERVED", "PROPOSED", "ASSUMPTION", "OPEN_QUESTION", "APPROVED", "OUT_OF_SCOPE"],
            [item["status"] for item in document["requirements"]],
        )
        self.assertEqual(content_hash(document), record["content_hash"])
        self.assertEqual(1, len(self.store.list_audit_events(action="requirements_created")))

    def test_unknown_meeting_and_invalid_status_or_schema_are_rejected(self) -> None:
        with self.assertRaises(NotFoundError):
            self.store.capture_requirements(999, requirements_document(), actor="founder")

        invalid_status = requirements_document()
        invalid_status["requirements"][0]["status"] = "INFERRED"
        with self.assertRaises(ValidationError):
            self.capture(invalid_status)

        missing_scope = requirements_document()
        del missing_scope["scope"]
        with self.assertRaises(ValidationError):
            self.capture(missing_scope)

        invalid_data = requirements_document()
        invalid_data["data_handling"]["hosting_mode"] = "UNKNOWN_HOSTING"
        with self.assertRaises(ValidationError):
            self.capture(invalid_data)

    def test_ready_draft_exports_without_false_approval_and_records_export(self) -> None:
        document = requirements_document(status="READY_FOR_REVIEW")
        record = self.capture(document)
        output = io.StringIO()
        exported = self.store.export_requirements(int(record["id"]), output)
        self.assertEqual("READY_FOR_REVIEW", exported["status"])
        self.assertIsNone(exported["approval"])
        self.assertEqual(exported, json.loads(output.getvalue()))
        self.assertEqual("READY_FOR_REVIEW", self.store.get_requirements_document(int(record["id"]))["status"])
        self.assertEqual(1, len(self.store.list_audit_events(action="requirements_exported")))

    def test_approval_rejects_nonhuman_actor_invalid_timestamp_and_unresolved_statuses(self) -> None:
        record = self.capture(requirements_document(status="READY_FOR_REVIEW"))
        with self.assertRaises(ApprovalError):
            self.store.approve_requirements(int(record["id"]), actor="automation")
        with self.assertRaises(ApprovalError):
            self.store.approve_requirements(
                int(record["id"]), actor="founder", approved_at="2026-10-20"
            )
        with self.assertRaises(ApprovalError):
            self.store.approve_requirements(int(record["id"]), actor="founder")
        self.assertEqual("READY_FOR_REVIEW", self.store.get_requirements_document(int(record["id"]))["status"])

    def test_explicit_approval_records_version_hash_and_audit(self) -> None:
        document = requirements_document(status="READY_FOR_REVIEW", all_approved=True)
        record = self.capture(document)
        before_hash = record["content_hash"]
        approved = self.store.approve_requirements(
            int(record["id"]), actor="founder", approved_at="2026-10-20T12:00:00Z"
        )
        self.assertEqual("APPROVED", approved["status"])
        self.assertEqual("APPROVED", approved["document"]["status"])
        self.assertEqual("founder", approved["approved_by"])
        self.assertEqual("2026-10-20T12:00:00Z", approved["approved_at"])
        self.assertEqual(before_hash, approved["content_hash"])
        self.assertEqual(before_hash, approved["document"]["approval"]["scope_version_hash"])
        events = self.store.list_audit_events(action="requirements_approved")
        self.assertEqual(1, len(events))
        self.assertEqual(before_hash, events[0]["metadata"]["scope_version_hash"])

    def test_approved_baseline_requires_explicit_new_version_and_superseding_audit(self) -> None:
        first = self.capture(requirements_document(status="READY_FOR_REVIEW", all_approved=True))
        first = self.store.approve_requirements(int(first["id"]), actor="founder")
        unchanged = copy.deepcopy(first["document"])
        with self.assertRaises(ValidationError):
            self.capture(unchanged)

        second_document = copy.deepcopy(first["document"])
        second_document.pop("approval", None)
        second_document.pop("meeting_id", None)
        second_document.pop("prospect_id", None)
        second_document["document_version"] = 2
        second_document["status"] = "READY_FOR_REVIEW"
        second_document["requirements"][0]["description"] = "Updated synthetic approved requirement"
        second_document["requirements"][0]["status"] = "APPROVED"
        second = self.capture(
            second_document,
            supersedes_document_id=int(first["id"]),
        )
        approved_second = self.store.approve_requirements(int(second["id"]), actor="founder")

        previous = self.store.get_requirements_document(int(first["id"]))
        self.assertEqual("SUPERSEDED", previous["status"])
        self.assertEqual("APPROVED", approved_second["status"])
        self.assertEqual("Updated synthetic approved requirement", approved_second["document"]["requirements"][0]["description"])
        self.assertEqual(first["content_hash"], previous["content_hash"])
        self.assertEqual(1, len(self.store.list_audit_events(action="requirements_superseded")))

    def test_cli_capture_list_export_and_approve_use_synthetic_data(self) -> None:
        database = self.directory / "requirements-cli.sqlite3"
        with MediaHouseStore(database) as store:
            store.import_csv(write_csv(self.directory, [lead_row()]), researcher="human-researcher")
            prospect = store.list_prospects()[0]
            meeting = store.record_meeting(
                prospect["id"],
                meeting_at="2026-10-20T10:00:00Z",
                notes="Synthetic CLI meeting",
                notes_permission="permission_granted",
                recording_policy="not_recorded",
                actor="founder",
            )
        input_path = self.directory / "requirements.json"
        input_path.write_text(
            json.dumps(requirements_document(status="READY_FOR_REVIEW", all_approved=True)), encoding="utf-8"
        )

        output = io.StringIO()
        with redirect_stdout(output):
            status = main(
                [
                    "--db",
                    str(database),
                    "requirements-capture",
                    str(meeting["id"]),
                    str(input_path),
                    "--actor",
                    "founder",
                    "--status",
                    "READY_FOR_REVIEW",
                ]
            )
        self.assertEqual(0, status)
        captured = json.loads(output.getvalue())
        document_id = int(captured["id"])

        list_output = io.StringIO()
        with redirect_stdout(list_output):
            status = main(["--db", str(database), "requirements-list", "--meeting-id", str(meeting["id"])])
        self.assertEqual(0, status)
        self.assertEqual(1, len(json.loads(list_output.getvalue())))

        show_output = io.StringIO()
        with redirect_stdout(show_output):
            status = main(["--db", str(database), "requirements-show", str(document_id)])
        self.assertEqual(0, status)
        self.assertEqual(document_id, json.loads(show_output.getvalue())["id"])

        export_path = self.directory / "exported-requirements.json"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            status = main(
                ["--db", str(database), "requirements-export", str(document_id), str(export_path)]
            )
        self.assertEqual(0, status)
        self.assertEqual("READY_FOR_REVIEW", json.loads(export_path.read_text(encoding="utf-8"))["status"])

        approve_output = io.StringIO()
        with redirect_stdout(approve_output):
            status = main(
                [
                    "--db",
                    str(database),
                    "requirements-approve",
                    str(document_id),
                    "--actor",
                    "founder",
                    "--approved-at",
                    "2026-10-20T12:00:00Z",
                ]
            )
        self.assertEqual(0, status)
        self.assertEqual("APPROVED", json.loads(approve_output.getvalue())["status"])


if __name__ == "__main__":
    unittest.main()
