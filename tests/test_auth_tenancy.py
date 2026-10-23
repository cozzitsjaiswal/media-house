from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

import media_house.auth as auth
from media_house.auth import AuthenticationBoundaryError, Principal, TenantContext, VerifiedIdentity
from media_house.errors import AuthorizationError, NotFoundError
from media_house.store import CSV_COLUMNS, MediaHouseStore
from media_house.tenant_repository import SqliteMembershipDirectory, TenantScopedProspectRepository


PERMISSIONS = {
    "prospect.read",
    "prospect.write",
    "prospect.delete",
    "prospect.export",
    "audit.read",
}


def lead_row(index: int) -> dict[str, str]:
    return {
        "company_name": f"Tenant Company {index}",
        "website": f"https://tenant-{index}.example",
        "region": "Maharashtra, India",
        "industry": "retail",
        "source_url": f"https://directory.example/tenant-{index}",
        "source_checked_at": "2026-10-20",
        "verified_status": "verified",
        "contact_name": "unknown",
        "contact_role": "owner",
        "contact_channel": "email",
        "channel_permission_status": "granted",
        "fit_reason": "Synthetic tenant-isolation test row.",
        "fit_confidence": "0.7",
        "lead_status": "qualified",
        "last_contact_at": "",
        "next_action": "Review",
        "next_action_due": "2026-10-21",
        "opt_out": "false",
        "notes": f"Tenant {index} seed notes",
    }


class AuthTenantFoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MediaHouseStore(":memory:")
        self.directory = SqliteMembershipDirectory(self.store.connection)
        self.repository = TenantScopedProspectRepository(self.store.connection)
        self.tempdir = tempfile.TemporaryDirectory()
        csv_path = Path(self.tempdir.name) / "leads.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows([lead_row(1), lead_row(2)])
        self.store.import_csv(csv_path, researcher="local-seed")
        self._seed_tenant("tenant-a")
        self._seed_tenant("tenant-b")
        self._seed_member("membership-a", "tenant-a", "alice", PERMISSIONS)
        self._seed_member("membership-b", "tenant-b", "bob", PERMISSIONS)
        self._seed_member("membership-limited", "tenant-a", "limited", {"prospect.read", "audit.read"})
        self.alice = Principal.from_verified_identity(self._verified_identity("alice", {"operator"}, PERMISSIONS))
        self.bob = Principal.from_verified_identity(self._verified_identity("bob", {"operator"}, PERMISSIONS))
        self.limited = Principal.from_verified_identity(
            self._verified_identity("limited", {"client_member"}, {"prospect.read", "audit.read"})
        )

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def _seed_tenant(self, tenant_id: str) -> None:
        self.store.connection.execute(
            "INSERT INTO auth_tenants (id, status) VALUES (?, 'active')",
            (tenant_id,),
        )
        self.store.connection.commit()

    def _seed_member(
        self, membership_id: str, tenant_id: str, principal_id: str, permissions: set[str]
    ) -> None:
        self.store.connection.execute(
            """
            INSERT INTO auth_memberships
                (id, tenant_id, principal_id, role, permissions_json, status)
            VALUES (?, ?, ?, ?, ?, 'active')
            """,
            (membership_id, tenant_id, principal_id, "operator", json.dumps(sorted(permissions))),
        )
        self.store.connection.commit()

    @staticmethod
    def _verified_identity(actor_id: str, roles: set[str], permissions: set[str]) -> VerifiedIdentity:
        return auth.VerifiedIdentity._from_trusted_verifier(
            actor_id=actor_id,
            roles=roles,
            permissions=permissions,
            authentication_source="test-verifier",
            _proof=auth._TRUSTED_AUTHENTICATION_PROOF,
        )

    def _context(self, principal: Principal, tenant_id: str, permission: str | None = None) -> TenantContext:
        return TenantContext.resolve(
            principal,
            tenant_id,
            self.directory,
            required_permission=permission,
        )

    def test_principal_and_context_require_trusted_boundaries(self) -> None:
        with self.assertRaises(AuthenticationBoundaryError):
            Principal(actor_id="alice", roles={"operator"}, permissions=PERMISSIONS)
        with self.assertRaises(AuthenticationBoundaryError):
            Principal.from_verified_identity({"actor_id": "alice"})  # type: ignore[arg-type]
        with self.assertRaises(AuthenticationBoundaryError):
            VerifiedIdentity(actor_id="alice")  # type: ignore[call-arg]
        with self.assertRaises(AuthorizationError):
            TenantContext("tenant-a", self.alice)  # type: ignore[call-arg]

    def test_missing_unknown_and_unrelated_membership_fail_closed(self) -> None:
        with self.assertRaises(AuthorizationError):
            TenantContext.resolve(self.alice, "tenant-unknown", self.directory)
        with self.assertRaises(AuthorizationError):
            TenantContext.resolve(self.bob, "tenant-a", self.directory)
        with self.assertRaises(AuthorizationError):
            self.repository.get(1, None)  # type: ignore[arg-type]

    def test_insufficient_permission_is_denied_without_a_write(self) -> None:
        context = self._context(self.limited, "tenant-a", "prospect.read")
        self.assertEqual([], self.repository.list(context))
        with self.assertRaises(AuthorizationError):
            TenantContext.resolve(self.limited, "tenant-a", self.directory, required_permission="prospect.write")
        before = self.store.get_prospect(1)["notes"]
        with self.assertRaises(AuthorizationError):
            self.repository.update_notes(1, "must not write", context)
        self.assertEqual(before, self.store.get_prospect(1)["notes"])

    def test_tenant_scope_protects_read_update_delete_and_export(self) -> None:
        context_a = self._context(self.alice, "tenant-a")
        context_b = self._context(self.bob, "tenant-b")
        self.repository.attach_existing_prospect(1, context_a)
        self.repository.attach_existing_prospect(2, context_b)

        self.assertEqual([1], [row["id"] for row in self.repository.list(context_a)])
        self.assertEqual([2], [row["id"] for row in self.repository.list(context_b)])

        with self.assertRaises(NotFoundError):
            self.repository.get(2, context_a)
        before = self.store.get_prospect(2)["notes"]
        with self.assertRaises(NotFoundError):
            self.repository.update_notes(2, "cross-tenant write", context_a)
        self.assertEqual(before, self.store.get_prospect(2)["notes"])
        with self.assertRaises(NotFoundError):
            self.repository.delete(2, context_a)
        self.assertEqual([2], [row["id"] for row in self.repository.list(context_b)])

        output = io.StringIO()
        self.assertEqual(1, self.repository.export_csv(output, context_a))
        exported = output.getvalue()
        self.assertIn("Tenant Company 1", exported)
        self.assertNotIn("Tenant Company 2", exported)

    def test_untrusted_tenant_change_cannot_claim_an_owned_prospect(self) -> None:
        context_a = self._context(self.alice, "tenant-a")
        context_b = self._context(self.bob, "tenant-b")
        self.repository.attach_existing_prospect(1, context_a)
        with self.assertRaises(NotFoundError):
            self.repository.attach_existing_prospect(1, context_b)
        self.assertEqual([], self.repository.list(context_b))
        self.assertEqual([1], [row["id"] for row in self.repository.list(context_a)])

    def test_failed_cross_tenant_operations_are_audited_without_business_mutation(self) -> None:
        context_a = self._context(self.alice, "tenant-a")
        context_b = self._context(self.bob, "tenant-b")
        self.repository.attach_existing_prospect(1, context_a)
        with self.assertRaises(NotFoundError):
            self.repository.get(1, context_b)
        before = self.store.get_prospect(1)["notes"]
        with self.assertRaises(NotFoundError):
            self.repository.update_notes(1, "must not write", context_b)
        with self.assertRaises(NotFoundError):
            self.repository.delete(1, context_b)
        self.assertEqual(before, self.store.get_prospect(1)["notes"])
        events = self.repository.list_audit_events(context_b)
        self.assertTrue(events)
        self.assertTrue(all(event["tenant_id"] == "tenant-b" for event in events))
        self.assertTrue(all(event["actor_id"] == "bob" for event in events))
        self.assertTrue(all(event["outcome"] == "denied" for event in events))
        self.assertEqual([1], [row["id"] for row in self.repository.list(context_a)])

    def test_audit_records_authenticated_actor_and_tenant_for_successful_operations(self) -> None:
        context_a = self._context(self.alice, "tenant-a")
        self.repository.attach_existing_prospect(1, context_a)
        self.repository.update_notes(1, "tenant A update", context_a)
        output = io.StringIO()
        self.repository.export_csv(output, context_a)
        events = self.repository.list_audit_events(context_a)
        self.assertTrue(events)
        self.assertTrue(all(event["tenant_id"] == "tenant-a" for event in events))
        self.assertTrue(all(event["actor_id"] == "alice" for event in events))
        self.assertEqual("delivered", events[-1]["outcome"])
        self.assertTrue(events[-1]["metadata"]["delivery_confirmed"])

    def test_failed_export_is_not_reported_as_delivered(self) -> None:
        context_a = self._context(self.alice, "tenant-a")
        self.repository.attach_existing_prospect(1, context_a)

        class FailingStream:
            def write(self, payload: str) -> int:
                raise OSError("synthetic stream failure")

            def flush(self) -> None:
                return None

        with self.assertRaises(OSError):
            self.repository.export_csv(FailingStream(), context_a)  # type: ignore[arg-type]
        events = self.repository.list_audit_events(context_a)
        self.assertEqual("delivery_failed", events[-1]["outcome"])
        self.assertFalse(events[-1]["metadata"]["delivery_confirmed"])


if __name__ == "__main__":
    unittest.main()
