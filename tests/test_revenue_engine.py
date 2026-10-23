from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from media_house.errors import ApprovalError, ValidationError
from media_house.scoring import SCORING_DISCLAIMER, calculate_fit_score
from media_house.store import CSV_COLUMNS, MediaHouseStore


def lead_row(**overrides: str) -> dict[str, str]:
    row = {column: "" for column in CSV_COLUMNS}
    row.update(
        {
            "company_name": "Acme Automation Services",
            "website": "https://www.example.com/about",
            "region": "Maharashtra, India",
            "industry": "professional services",
            "source_url": "https://directory.example/acme",
            "source_checked_at": "2026-10-13",
            "verified_status": "verified",
            "contact_name": "unknown",
            "contact_role": "owner",
            "contact_channel": "email",
            "channel_permission_status": "granted",
            "fit_reason": "Public workflow evidence suggests a service-led operation.",
            "fit_confidence": "0.75",
            "lead_status": "researched",
            "last_contact_at": "",
            "next_action": "Review manually",
            "next_action_due": "2026-10-20",
            "opt_out": "false",
            "notes": "Synthetic test row",
        }
    )
    row.update(overrides)
    return row


def write_csv(directory: Path, rows: list[dict[str, str]], *, fieldnames: tuple[str, ...] = CSV_COLUMNS) -> Path:
    path = directory / "leads.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


class RevenueEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MediaHouseStore(":memory:")
        self.tempdir = tempfile.TemporaryDirectory()
        self.directory = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def import_rows(self, *rows: dict[str, str]):
        path = write_csv(self.directory, list(rows))
        return self.store.import_csv(path, researcher="human-researcher")

    def test_invalid_row_is_atomic_and_requires_explicit_unknowns(self) -> None:
        valid = lead_row(company_name="Valid Co")
        invalid_source = lead_row(company_name="Invalid Co", source_url="")
        invalid_contact = lead_row(company_name="Invalid Contact Co", contact_channel="")
        path = write_csv(self.directory, [valid, invalid_source, invalid_contact])

        with self.assertRaises(ValidationError) as context:
            self.store.import_csv(path, researcher="researcher-1")

        self.assertIn("source_url is required", "\n".join(context.exception.errors))
        self.assertIn("contact_channel must be explicit", "\n".join(context.exception.errors))
        self.assertEqual([], self.store.list_prospects())

    def test_missing_required_header_is_rejected_before_writes(self) -> None:
        row = lead_row()
        fields = tuple(column for column in CSV_COLUMNS if column != "fit_reason")
        path = write_csv(self.directory, [row], fieldnames=fields)

        with self.assertRaises(ValidationError) as context:
            self.store.import_csv(path, researcher="researcher-1")

        self.assertIn("fit_reason", str(context.exception))
        self.assertEqual([], self.store.list_prospects())

    def test_blank_contact_identity_must_use_explicit_unknown(self) -> None:
        with self.assertRaises(ValidationError) as context:
            self.import_rows(lead_row(contact_name=""))
        self.assertIn("contact_name must be explicit", "\n".join(context.exception.errors))
        self.assertEqual([], self.store.list_prospects())

    def test_domain_dedupe_retains_source_evidence_and_opt_out(self) -> None:
        first = lead_row(
            company_name="Acme One",
            website="https://www.Example.com/first",
            source_url="https://source.example/one",
            source_checked_at="2026-10-01",
            opt_out="true",
        )
        second = lead_row(
            company_name="Acme Different Display Name",
            website="http://example.com/second",
            source_url="https://source.example/two",
            source_checked_at="2026-10-02",
            opt_out="false",
            notes="Second independent source",
        )

        result = self.import_rows(first, second)
        self.assertEqual(2, result.source_evidence_added)
        self.assertEqual(1, len(self.store.list_prospects()))
        prospect = self.store.list_prospects()[0]
        self.assertTrue(prospect["opt_out"])
        self.assertEqual(2, prospect["source_evidence_count"])
        evidence = self.store.list_source_evidence(prospect["id"])
        self.assertEqual({"https://source.example/one", "https://source.example/two"}, {item["source_url"] for item in evidence})
        self.assertEqual("human-researcher", evidence[0]["researcher"])

    def test_name_and_region_fallback_dedupe_without_domain(self) -> None:
        first = lead_row(company_name="  Café & Co. ", website="", region="Maharashtra")
        second = lead_row(
            company_name="café co",
            website="unknown",
            region=" Maharashtra ",
            source_url="https://source.example/second",
        )
        self.import_rows(first, second)
        self.assertEqual(1, len(self.store.list_prospects()))
        self.assertEqual(2, len(self.store.list_source_evidence(1)))

    def test_name_and_region_fallback_can_match_existing_domain_record(self) -> None:
        first = lead_row(company_name="Same Company", website="https://same.example")
        second = lead_row(
            company_name="same company",
            website="",
            source_url="https://source.example/no-domain",
        )
        self.import_rows(first, second)
        self.assertEqual(1, len(self.store.list_prospects()))
        self.assertEqual(2, len(self.store.list_source_evidence(1)))

    def test_reimport_of_same_evidence_is_idempotent(self) -> None:
        row = lead_row()
        first = self.import_rows(row)
        second = self.import_rows(row)
        self.assertEqual(1, first.source_evidence_added)
        self.assertEqual(0, second.source_evidence_added)
        self.assertEqual(1, len(self.store.list_source_evidence(1)))

    def test_fit_score_is_deterministic_explainable_and_bounded(self) -> None:
        first = calculate_fit_score(
            region="Maharashtra",
            industry="Restaurant",
            verified_status="verified",
            fit_reason="Observed manual enquiry workflow",
            source_evidence_count=2,
            confidence=0.4,
        )
        second = calculate_fit_score(
            region="Maharashtra",
            industry="Restaurant",
            verified_status="verified",
            fit_reason="Observed manual enquiry workflow",
            source_evidence_count=2,
            confidence=0.4,
        )
        self.assertEqual(first, second)
        self.assertGreater(first.score, 0)
        self.assertLessEqual(first.score, 100)
        self.assertEqual(0.4, first.confidence)
        self.assertTrue(any("region" in reason for reason in first.reasons))
        self.assertEqual(SCORING_DISCLAIMER, first.disclaimer)

    def test_invalid_fit_confidence_is_rejected(self) -> None:
        row = lead_row(fit_confidence="1.1")
        with self.assertRaises(ValidationError):
            self.import_rows(row)
        self.assertEqual([], self.store.list_prospects())

    def test_explicit_unknown_values_are_preserved(self) -> None:
        self.import_rows(
            lead_row(
                contact_name="unknown",
                contact_role="unknown",
                contact_channel="unknown",
                channel_permission_status="unknown",
                fit_confidence="unknown",
                opt_out="unknown",
            )
        )
        prospect = self.store.list_prospects()[0]
        self.assertEqual("unknown", prospect["contact_name"])
        self.assertEqual("unknown", prospect["contact_role"])
        self.assertEqual("unknown", prospect["contact_channel"])
        self.assertEqual("unknown", prospect["channel_permission_status"])
        self.assertIsNone(prospect["fit_confidence"])
        self.assertIsNone(prospect["opt_out"])

    def test_approval_gate_rejects_opt_out_unknown_blocked_and_withdrawn(self) -> None:
        cases = (
            {"opt_out": "true", "channel_permission_status": "granted"},
            {"opt_out": "unknown", "channel_permission_status": "granted"},
            {"opt_out": "false", "channel_permission_status": "blocked"},
            {"opt_out": "false", "channel_permission_status": "withdrawn"},
            {"opt_out": "false", "channel_permission_status": "unknown"},
        )
        for index, changes in enumerate(cases):
            with self.subTest(index=index):
                row = lead_row(company_name=f"Blocked {index}", **changes)
                self.import_rows(row)
                prospect = self.store.list_prospects()[-1]
                draft = self.store.create_outreach_draft(
                    prospect["id"],
                    channel="email",
                    content="A reviewed draft",
                    created_by="human-drafter",
                )
                with self.assertRaises(ApprovalError):
                    self.store.approve_draft(draft["id"], human_actor="human-approver")
                self.assertIsNone(self.store.get_draft(draft["id"])["approved_at"])

    def test_approval_requires_human_actor_and_updates_lead_without_sending(self) -> None:
        self.import_rows(lead_row(channel_permission_status="not_required_confirmed"))
        prospect = self.store.list_prospects()[0]
        draft = self.store.create_outreach_draft(
            prospect["id"],
            content="Hello from a human-reviewed draft.",
            created_by="human-drafter",
        )
        self.assertEqual("PENDING_APPROVAL", draft["status"])
        self.assertFalse(draft["send_performed"])
        with self.assertRaises(ApprovalError):
            self.store.approve_draft(draft["id"], human_actor="automation")
        with self.assertRaises(ApprovalError):
            self.store.approve_draft(draft["id"], human_actor="")

        approved = self.store.approve_draft(draft["id"], human_actor="founder")
        self.assertEqual("PENDING_APPROVAL", approved["status"])
        self.assertFalse(approved["send_performed"])
        self.assertEqual("approved_to_contact", self.store.get_prospect(prospect["id"])["lead_status"])
        events = self.store.list_audit_events(action="outreach_draft_approved")
        self.assertEqual(1, len(events))
        self.assertIs(False, events[0]["send_performed"])
        self.assertIs(False, events[0]["metadata"]["send_performed"])
        self.assertIs(False, events[0]["metadata"]["provider_call_performed"])

    def test_approval_is_atomic_when_gate_fails(self) -> None:
        self.import_rows(lead_row(channel_permission_status="unknown"))
        prospect = self.store.list_prospects()[0]
        draft = self.store.create_outreach_draft(
            prospect["id"],
            content="Draft only",
            created_by="human-drafter",
        )
        with self.assertRaises(ApprovalError):
            self.store.approve_draft(draft["id"], human_actor="founder")
        self.assertEqual("researched", self.store.get_prospect(prospect["id"])["lead_status"])
        self.assertEqual([], self.store.list_audit_events(action="outreach_draft_approved"))

    def test_stale_import_does_not_downgrade_later_or_protected_statuses(self) -> None:
        protected_statuses = ("discovery_complete", "proposal_sent", "won", "blocked", "opted_out")
        for index, status in enumerate(protected_statuses):
            with self.subTest(status=status):
                row = lead_row(
                    company_name=f"Status Protection {index}",
                    website=f"https://status-protection-{index}.example",
                    source_url=f"https://directory.example/status-protection-{index}",
                    lead_status="qualified",
                    opt_out="true" if status == "opted_out" else "false",
                )
                self.import_rows(row)
                prospect = self.store.list_prospects()[-1]
                self.store.connection.execute(
                    "UPDATE prospects SET lead_status = ? WHERE id = ?",
                    (status, prospect["id"]),
                )
                self.store.connection.commit()

                stale = dict(row)
                stale["lead_status"] = "researched"
                stale["opt_out"] = "false"
                self.import_rows(stale)

                current = self.store.get_prospect(prospect["id"])
                self.assertEqual(status, current["lead_status"])
                if status == "opted_out":
                    self.assertTrue(current["opt_out"])

    def test_csv_import_rejects_a_later_stage_bypass(self) -> None:
        with self.assertRaises(ValidationError) as initial:
            self.import_rows(lead_row(lead_status="won"))
        self.assertIn("cannot advance lead_status", str(initial.exception))
        self.assertEqual([], self.store.list_prospects())

        self.import_rows(lead_row(lead_status="qualified"))
        stale_forward = lead_row(lead_status="proposal_sent")
        with self.assertRaises(ValidationError) as existing:
            self.import_rows(stale_forward)
        self.assertIn("cannot advance lead_status", str(existing.exception))
        self.assertEqual("qualified", self.store.list_prospects()[0]["lead_status"])

    def test_draft_approval_does_not_downgrade_later_lead_status(self) -> None:
        self.import_rows(lead_row(lead_status="qualified"))
        prospect = self.store.list_prospects()[0]
        self.store.connection.execute(
            "UPDATE prospects SET lead_status = 'proposal_sent' WHERE id = ?",
            (prospect["id"],),
        )
        self.store.connection.commit()
        draft = self.store.create_outreach_draft(
            prospect["id"],
            channel="email",
            content="A reviewed synthetic draft.",
            created_by="human-drafter",
        )

        approved = self.store.approve_draft(draft["id"], human_actor="founder")

        self.assertEqual("proposal_sent", self.store.get_prospect(prospect["id"])["lead_status"])
        self.assertEqual("proposal_sent", approved["audit"]["lead_status_after"])
        self.assertEqual("proposal_sent", approved["audit"]["lead_status_before"])

    def test_export_uses_template_fields_and_explainability_extensions(self) -> None:
        self.import_rows(lead_row())
        output = io.StringIO()
        count = self.store.export_csv(output)
        self.assertEqual(1, count)
        output.seek(0)
        exported = list(csv.DictReader(output))
        self.assertEqual(1, len(exported))
        self.assertEqual("Acme Automation Services", exported[0]["company_name"])
        self.assertEqual("1", exported[0]["source_evidence_count"])
        self.assertTrue(json.loads(exported[0]["score_reasons"]))


if __name__ == "__main__":
    unittest.main()
