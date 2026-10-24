from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import media_house.auth as auth
from media_house.auth import (
    AuthenticatedRequestContext,
    AuthenticationBoundary,
    AuthenticationBoundaryError,
    Principal,
    TenantContext,
    VerifiedIdentity,
)
from media_house.errors import AuthenticationError, AuthorizationError, NotFoundError
from media_house.store import CSV_COLUMNS, MediaHouseStore
from media_house.tenant_repository import (
    PROSPECT_ATTACH_PERMISSION,
    SqliteMembershipDirectory,
    TenantScopedProspectRepository,
)


PERMISSIONS = {
    "prospect.read",
    "prospect.write",
    PROSPECT_ATTACH_PERMISSION,
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
        self._seed_member(
            "membership-writer",
            "tenant-a",
            "writer",
            {"prospect.read", "prospect.write", "audit.read"},
        )
        self._seed_member("membership-limited", "tenant-a", "limited", {"prospect.read", "audit.read"})
        self.alice = Principal._from_verified_identity(
            self._verified_identity("alice", {"operator"}, PERMISSIONS),
            _proof=auth._TRUSTED_AUTHENTICATION_PROOF,
        )
        self.bob = Principal._from_verified_identity(
            self._verified_identity("bob", {"operator"}, PERMISSIONS),
            _proof=auth._TRUSTED_AUTHENTICATION_PROOF,
        )
        self.writer = Principal._from_verified_identity(
            self._verified_identity("writer", {"operator"}, {"prospect.read", "prospect.write", "audit.read"}),
            _proof=auth._TRUSTED_AUTHENTICATION_PROOF,
        )
        self.limited = Principal._from_verified_identity(
            self._verified_identity("limited", {"client_member"}, {"prospect.read", "audit.read"}),
            _proof=auth._TRUSTED_AUTHENTICATION_PROOF,
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
    def _verified_identity(
        actor_id: str,
        roles: set[str],
        permissions: set[str],
        *,
        issuer: str = "https://issuer.synthetic.example",
        audience: str = "media-house-test",
        expires_at: str = "2099-01-01T00:00:00Z",
        not_before: str | None = None,
    ) -> VerifiedIdentity:
        return auth.VerifiedIdentity._from_trusted_verifier(
            actor_id=actor_id,
            roles=roles,
            permissions=permissions,
            authentication_source="test-verifier",
            issuer=issuer,
            audience=audience,
            expires_at=expires_at,
            not_before=not_before,
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
            Principal._from_verified_identity({"actor_id": "alice"}, _proof=auth._TRUSTED_AUTHENTICATION_PROOF)  # type: ignore[arg-type]
        with self.assertRaises(AuthenticationBoundaryError):
            VerifiedIdentity(actor_id="alice")  # type: ignore[call-arg]
        with self.assertRaises(AuthorizationError):
            TenantContext("tenant-a", self.alice)  # type: ignore[call-arg]

    def test_authentication_boundary_uses_opaque_credential_and_explicit_tenant(self) -> None:
        class SyntheticVerifier:
            def verify(self, credential: object) -> VerifiedIdentity:
                if not isinstance(credential, str) or credential != "alice-token":
                    raise ValueError("invalid synthetic credential")
                return AuthTenantFoundationTests._verified_identity("alice", {"operator"}, PERMISSIONS)

        boundary = AuthenticationBoundary(
            SyntheticVerifier(),
            self.directory,
            expected_issuer="https://issuer.synthetic.example",
            expected_audience="media-house-test",
            clock=lambda: datetime(2026, 10, 24, tzinfo=timezone.utc),
        )
        request_context = boundary.authenticate(
            "alice-token",
            tenant_id="tenant-a",
            required_permission="prospect.read",
        )
        self.assertIsInstance(request_context, AuthenticatedRequestContext)
        self.assertEqual("alice", request_context.actor_id)
        self.assertEqual("tenant-a", request_context.tenant_id)
        self.assertEqual(PERMISSIONS, request_context.permissions)

        with self.assertRaises(AuthenticationError):
            boundary.authenticate(
                {
                    "actor_id": "alice",
                    "tenant_id": "tenant-a",
                    "role": "owner",
                    "permissions": sorted(PERMISSIONS),
                },
                tenant_id="tenant-a",
                required_permission="prospect.read",
            )

    def test_authentication_boundary_rejects_expired_future_and_wrong_scope_evidence(self) -> None:
        class SyntheticVerifier:
            def __init__(self, identity: VerifiedIdentity) -> None:
                self.identity = identity

            def verify(self, credential: object) -> VerifiedIdentity:
                if credential != "token":
                    raise ValueError("invalid synthetic credential")
                return self.identity

        for identity in (
            self._verified_identity("alice", {"operator"}, PERMISSIONS, expires_at="2026-10-23T23:59:59Z"),
            self._verified_identity("alice", {"operator"}, PERMISSIONS, issuer="https://evil.example"),
            self._verified_identity("alice", {"operator"}, PERMISSIONS, audience="other-app"),
            self._verified_identity("alice", {"operator"}, PERMISSIONS, not_before="2099-01-01T00:00:00Z"),
        ):
            boundary = AuthenticationBoundary(
                SyntheticVerifier(identity),
                self.directory,
                expected_issuer="https://issuer.synthetic.example",
                expected_audience="media-house-test",
                clock=lambda: datetime(2026, 10, 24, tzinfo=timezone.utc),
            )
            with self.assertRaises(AuthenticationError):
                boundary.authenticate("token", tenant_id="tenant-a")

    def test_custom_permission_policy_cannot_expand_verified_or_membership_permissions(self) -> None:
        class ExpandingPolicy:
            def effective_permissions(self, *, principal: Principal, membership: auth.Membership) -> frozenset[str]:
                return frozenset({"system.root"})

        class SyntheticVerifier:
            def verify(self, credential: object) -> VerifiedIdentity:
                if credential != "alice-token":
                    raise ValueError("invalid synthetic credential")
                return AuthTenantFoundationTests._verified_identity(
                    "alice",
                    {"operator"},
                    {"prospect.read"},
                )

        boundary = AuthenticationBoundary(
            SyntheticVerifier(),
            self.directory,
            expected_issuer="https://issuer.synthetic.example",
            expected_audience="media-house-test",
            permission_policy=ExpandingPolicy(),
        )
        context = boundary.authenticate("alice-token", tenant_id="tenant-a")
        self.assertEqual(frozenset(), context.permissions)
        with self.assertRaises(AuthorizationError):
            context.require("system.root")

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

    def test_attach_requires_a_dedicated_permission(self) -> None:
        context = self._context(self.writer, "tenant-a")
        with self.assertRaises(AuthorizationError):
            self.repository.attach_existing_prospect(1, context)
        self.assertEqual([], self.repository.list(context))

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

    def test_detach_blocks_cross_tenant_reattach_but_allows_same_tenant_recovery(self) -> None:
        context_a = self._context(self.alice, "tenant-a")
        context_b = self._context(self.bob, "tenant-b")
        self.repository.attach_existing_prospect(1, context_a)
        self.repository.delete(1, context_a)

        with self.assertRaises(NotFoundError):
            self.repository.attach_existing_prospect(1, context_b)
        self.assertEqual([], self.repository.list(context_b))

        self.repository.attach_existing_prospect(1, context_a)
        self.assertEqual([1], [row["id"] for row in self.repository.list(context_a)])

    def test_attach_denials_use_the_same_non_enumerating_reason(self) -> None:
        context_a = self._context(self.alice, "tenant-a")
        context_b = self._context(self.bob, "tenant-b")
        self.repository.attach_existing_prospect(1, context_a)

        with self.assertRaises(NotFoundError):
            self.repository.attach_existing_prospect(1, context_b)
        with self.assertRaises(NotFoundError):
            self.repository.attach_existing_prospect(999, context_b)

        events = self.repository.list_audit_events(context_b)
        self.assertEqual(
            ["not_available", "not_available"],
            [event["metadata"]["reason"] for event in events[-2:]],
        )

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
