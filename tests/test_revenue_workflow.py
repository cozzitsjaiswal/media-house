from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from media_house.errors import NotFoundError, ValidationError
from media_house.cli import main
from media_house.store import CSV_COLUMNS, MediaHouseStore


def lead_row(**overrides: str) -> dict[str, str]:
    row = {column: "" for column in CSV_COLUMNS}
    row.update(
        {
            "company_name": "Revenue Workflow Co",
            "website": "https://revenue.example",
            "region": "Maharashtra, India",
            "industry": "retail",
            "source_url": "https://directory.example/revenue",
            "source_checked_at": "2026-10-13",
            "verified_status": "verified",
            "contact_name": "unknown",
            "contact_role": "owner",
            "contact_channel": "email",
            "channel_permission_status": "granted",
            "fit_reason": "Synthetic test row for order workflow.",
            "fit_confidence": "0.7",
            "lead_status": "qualified",
            "last_contact_at": "",
            "next_action": "Create order",
            "next_action_due": "2026-10-20",
            "opt_out": "false",
            "notes": "Synthetic revenue workflow row",
        }
    )
    row.update(overrides)
    return row


def write_csv(directory: Path, rows: list[dict[str, str]]) -> Path:
    path = directory / "leads.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


class RevenueWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MediaHouseStore(":memory:")
        self.tempdir = tempfile.TemporaryDirectory()
        self.directory = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def import_lead(self) -> dict[str, object]:
        self.store.import_csv(write_csv(self.directory, [lead_row()]), researcher="human-researcher")
        return self.store.list_prospects()[0]

    def test_default_service_packages_are_database_backed_and_editable(self) -> None:
        packages = self.store.list_service_packages()
        self.assertEqual(["Digital Identity Package", "Growth Suite"], [item["name"] for item in packages])
        self.assertEqual(4999, packages[0]["price_amount"])
        self.assertEqual("INR", packages[0]["currency"])

        updated = self.store.update_service_package(
            packages[0]["id"], actor="founder", price_amount=5999, active="false"
        )
        self.assertFalse(updated["active"])
        self.assertEqual(5999, updated["price_amount"])
        self.assertEqual(1, len(self.store.list_service_packages()))
        self.assertEqual(2, len(self.store.list_service_packages(include_inactive=True)))

        events = self.store.list_audit_events(action="service_package_updated")
        self.assertEqual(1, len(events))
        self.assertFalse(events[0]["send_performed"])

    def test_create_service_package_validates_price_and_actor(self) -> None:
        with self.assertRaises(ValidationError):
            self.store.create_service_package(
                name="Bad Package",
                description="Invalid price",
                price_amount=0,
                actor="founder",
            )
        with self.assertRaises(ValidationError):
            self.store.create_service_package(
                name="Automation Package",
                description="Invalid actor",
                price_amount=1000,
                actor="automation",
            )

        package = self.store.create_service_package(
            name="Local SEO Starter",
            description="Configurable local SEO setup package",
            price_amount=7999,
            currency="INR",
            actor="founder",
        )
        self.assertTrue(package["active"])
        self.assertEqual("Local SEO Starter", package["name"])

    def test_order_and_payment_request_are_safe_pending_intents(self) -> None:
        prospect = self.import_lead()
        package = self.store.list_service_packages()[0]

        order = self.store.create_order_request(
            prospect["id"], package["id"], requested_by="founder", notes="Customer requested quote"
        )
        self.assertEqual("REQUESTED", order["status"])
        self.assertEqual(package["name"], order["package_name"])
        self.assertEqual(package["price_amount"], order["amount"])

        payment = self.store.create_payment_request(
            order["id"], created_by="founder", upi_uri="upi://pay?pa=placeholder"
        )
        self.assertEqual("PENDING_PROVIDER", payment["status"])
        self.assertFalse(payment["provider_call_performed"])
        self.assertFalse(payment["payment_verified"])
        self.assertEqual(order["amount"], payment["amount"])

        events = self.store.list_audit_events()
        self.assertIn("order_request_created", {item["action"] for item in events})
        self.assertIn("payment_request_created", {item["action"] for item in events})

    def test_order_rejects_missing_prospect_and_inactive_package(self) -> None:
        package = self.store.list_service_packages()[0]
        with self.assertRaises(NotFoundError):
            self.store.create_order_request(999, package["id"], requested_by="founder")

        prospect = self.import_lead()
        self.store.update_service_package(package["id"], actor="founder", active=False)
        with self.assertRaises(ValidationError):
            self.store.create_order_request(prospect["id"], package["id"], requested_by="founder")

    def test_completed_meeting_is_audited_and_advances_only_early_lead_states(self) -> None:
        prospect = self.import_lead()
        meeting = self.store.record_meeting(
            prospect["id"],
            meeting_at="2026-10-20T10:00:00+05:30",
            notes="Synthetic customer described a manual enquiry follow-up workflow.",
            notes_permission="permission_granted",
            recording_policy="not_recorded",
            actor="founder",
        )

        self.assertEqual(prospect["id"], meeting["prospect_id"])
        self.assertEqual("2026-10-20T04:30:00Z", meeting["meeting_at"])
        self.assertEqual("permission_granted", meeting["notes_permission"])
        self.assertEqual("not_recorded", meeting["recording_policy"])
        self.assertEqual("founder", meeting["created_by"])
        self.assertEqual("discovery_complete", self.store.get_prospect(prospect["id"])["lead_status"])
        self.assertEqual([meeting["id"]], [item["id"] for item in self.store.list_meetings(prospect_id=prospect["id"])])

        events = self.store.list_audit_events(action="meeting_recorded")
        self.assertEqual(1, len(events))
        self.assertEqual(meeting["id"], events[0]["object_id"])
        self.assertEqual("permission_granted", events[0]["metadata"]["notes_permission"])
        self.assertEqual("discovery_complete", events[0]["metadata"]["lead_status_after"])
        self.assertFalse(events[0]["send_performed"])

        self.store.connection.execute(
            "UPDATE prospects SET lead_status = 'proposal_sent' WHERE id = ?", (prospect["id"],)
        )
        self.store.connection.commit()
        self.store.record_meeting(
            prospect["id"],
            meeting_at="2026-10-21",
            notes="Synthetic follow-up meeting; later proposal state must remain unchanged.",
            notes_permission="not_required_confirmed",
            recording_policy="permission_denied",
            actor="founder",
        )
        self.assertEqual("proposal_sent", self.store.get_prospect(prospect["id"])["lead_status"])

    def test_meeting_requires_human_actor_notes_and_explicit_recording_policy(self) -> None:
        prospect = self.import_lead()
        cases = (
            {
                "actor": "automation",
                "notes": "Notes",
                "notes_permission": "permission_granted",
                "recording_policy": "not_recorded",
            },
            {
                "actor": "founder",
                "notes": "",
                "notes_permission": "permission_granted",
                "recording_policy": "not_recorded",
            },
            {
                "actor": "founder",
                "notes": "Notes",
                "notes_permission": "permission_granted",
                "recording_policy": "unknown",
            },
            {
                "actor": "founder",
                "notes": "Notes",
                "notes_permission": "unknown",
                "recording_policy": "not_recorded",
            },
        )
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(ValidationError):
                    self.store.record_meeting(
                        prospect["id"],
                        meeting_at="2026-10-20",
                        **changes,
                    )
        self.assertEqual([], self.store.list_meetings())
        self.assertEqual([], self.store.list_audit_events(action="meeting_recorded"))

    def test_meeting_cli_records_and_lists_a_synthetic_meeting(self) -> None:
        database = self.directory / "meeting-cli.sqlite3"
        with MediaHouseStore(database) as store:
            store.import_csv(write_csv(self.directory, [lead_row()]), researcher="human-researcher")

        record_output = io.StringIO()
        with redirect_stdout(record_output):
            status = main(
                [
                    "--db",
                    str(database),
                    "meeting-record",
                    "--prospect-id",
                    "1",
                    "--meeting-at",
                    "2026-10-20T10:00:00+05:30",
                    "--notes",
                    "Synthetic CLI meeting notes",
                    "--notes-permission",
                    "permission_granted",
                    "--recording-policy",
                    "not_recorded",
                    "--actor",
                    "founder",
                ]
            )
        self.assertEqual(0, status)
        self.assertEqual("not_recorded", json.loads(record_output.getvalue())["recording_policy"])

        list_output = io.StringIO()
        with redirect_stdout(list_output):
            status = main(["--db", str(database), "meeting-list", "--prospect-id", "1"])
        self.assertEqual(0, status)
        listed = json.loads(list_output.getvalue())
        self.assertEqual(1, len(listed))
        self.assertEqual("Synthetic CLI meeting notes", listed[0]["notes"])


if __name__ == "__main__":
    unittest.main()
