from __future__ import annotations

import json
import sqlite3
import unittest

import media_house.auth as auth
from media_house.auth import Principal, TenantContext, VerifiedIdentity
from media_house.errors import AuthorizationError, ValidationError
from media_house.provisioning import (
    MEMBERSHIP_MANAGE_PERMISSION,
    TENANT_PROVISION_PERMISSION,
    TenantProvisioningService,
)
from media_house.store import MediaHouseStore
from media_house.tenant_repository import SqliteMembershipDirectory


ADMIN_PERMISSIONS = {
    TENANT_PROVISION_PERMISSION,
    MEMBERSHIP_MANAGE_PERMISSION,
    "prospect.read",
    "prospect.write",
    "prospect.delete",
    "prospect.export",
    "audit.read",
}


class TenantProvisioningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MediaHouseStore(":memory:")
        self.directory = SqliteMembershipDirectory(self.store.connection)
        self.provisioning = TenantProvisioningService(self.store.connection)
        self._insert_tenant("admin-tenant")
        self._insert_membership("admin-membership", "admin-tenant", "admin", ADMIN_PERMISSIONS)
        self.admin = Principal._from_verified_identity(
            self._identity("admin", ADMIN_PERMISSIONS),
            _proof=auth._TRUSTED_AUTHENTICATION_PROOF,
        )
        self.admin_context = TenantContext.resolve(
            self.admin,
            "admin-tenant",
            self.directory,
            required_permission=MEMBERSHIP_MANAGE_PERMISSION,
        )

    def tearDown(self) -> None:
        self.store.close()

    @staticmethod
    def _identity(actor_id: str, permissions: set[str]) -> VerifiedIdentity:
        return VerifiedIdentity._from_trusted_verifier(
            actor_id=actor_id,
            roles={"admin"},
            permissions=permissions,
            authentication_source="synthetic-verifier",
            issuer="https://issuer.synthetic.example",
            audience="media-house-test",
            expires_at="2099-01-01T00:00:00Z",
            _proof=auth._TRUSTED_AUTHENTICATION_PROOF,
        )

    def _insert_tenant(self, tenant_id: str) -> None:
        self.store.connection.execute(
            "INSERT INTO auth_tenants (id, status) VALUES (?, 'active')",
            (tenant_id,),
        )
        self.store.connection.commit()

    def _insert_membership(
        self,
        membership_id: str,
        tenant_id: str,
        principal_id: str,
        permissions: set[str],
    ) -> None:
        self.store.connection.execute(
            """
            INSERT INTO auth_memberships
                (id, tenant_id, principal_id, role, permissions_json, status)
            VALUES (?, ?, ?, 'admin', ?, 'active')
            """,
            (membership_id, tenant_id, principal_id, json.dumps(sorted(permissions))),
        )
        self.store.connection.commit()

    def test_ordinary_member_cannot_create_tenant_or_membership(self) -> None:
        self._insert_membership("limited-membership", "admin-tenant", "limited", {"prospect.read"})
        limited = Principal._from_verified_identity(
            self._identity("limited", {"prospect.read"}),
            _proof=auth._TRUSTED_AUTHENTICATION_PROOF,
        )
        limited_context = TenantContext.resolve(limited, "admin-tenant", self.directory)

        with self.assertRaises(AuthorizationError):
            self.provisioning.create_tenant("tenant-forged", administrator=limited_context)
        with self.assertRaises(AuthorizationError):
            self.provisioning.create_membership(
                "admin-tenant",
                "attacker",
                role="admin",
                permissions={"prospect.read"},
                administrator=limited_context,
            )
        self.assertIsNone(
            self.store.connection.execute("SELECT 1 FROM auth_tenants WHERE id = 'tenant-forged'").fetchone()
        )
        self.assertIsNone(
            self.store.connection.execute(
                "SELECT 1 FROM auth_memberships WHERE principal_id = 'attacker'"
            ).fetchone()
        )

    def test_admin_provisioning_is_atomic_and_audited_with_authenticated_identity(self) -> None:
        tenant = self.provisioning.create_tenant("tenant-new", administrator=self.admin_context)
        self.assertEqual({"id": "tenant-new", "status": "active"}, tenant)

        membership = self.provisioning.create_membership(
            "tenant-new",
            "bob",
            role="viewer",
            permissions={"prospect.read"},
            administrator=self.admin_context,
            membership_id="membership-bob",
        )
        self.assertEqual("membership-bob", membership["id"])
        self.assertEqual("bob", membership["principal_id"])

        events = self.store.connection.execute(
            """
            SELECT actor_id, tenant_id, action, outcome, metadata_json
            FROM tenant_audit_events
            WHERE action IN ('tenant_provisioned', 'membership_provisioned')
            ORDER BY id
            """
        ).fetchall()
        self.assertEqual(2, len(events))
        for event in events:
            self.assertEqual("admin", event[0])
            self.assertEqual("admin-tenant", event[1])
            self.assertEqual("committed", event[3])
            metadata = json.loads(event[4])
            self.assertEqual("admin", metadata["administrator_actor_id"])
            self.assertEqual("admin-tenant", metadata["administrator_tenant_id"])
        tenant_metadata = json.loads(events[0][4])
        membership_metadata = json.loads(events[1][4])
        self.assertEqual("tenant-new", tenant_metadata["target_tenant_id"])
        self.assertEqual("tenant-new", membership_metadata["target_tenant_id"])
        self.assertEqual("bob", membership_metadata["affected_identity"])

    def test_self_grant_and_permission_expansion_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.provisioning.create_membership(
                "admin-tenant",
                "admin",
                role="owner",
                permissions={"prospect.read"},
                administrator=self.admin_context,
            )
        with self.assertRaises(ValidationError):
            self.provisioning.create_membership(
                "admin-tenant",
                "carol",
                role="owner",
                permissions={"membership.manage", "tenant.provision", "system.root"},
                administrator=self.admin_context,
            )
        self.assertIsNone(
            self.store.connection.execute(
                "SELECT 1 FROM auth_memberships WHERE principal_id = 'carol'"
            ).fetchone()
        )
        denied = self.store.connection.execute(
            """
            SELECT COUNT(*) FROM tenant_audit_events
            WHERE action = 'membership_provisioned' AND outcome = 'denied'
            """
        ).fetchone()[0]
        self.assertEqual(2, denied)

    def test_audit_failure_rolls_back_membership_insert(self) -> None:
        self._insert_tenant("tenant-atomic")
        self.store.connection.executescript(
            """
            CREATE TRIGGER fail_committed_membership_audit
            BEFORE INSERT ON tenant_audit_events
            WHEN NEW.action = 'membership_provisioned' AND NEW.outcome = 'committed'
            BEGIN
                SELECT RAISE(ABORT, 'synthetic audit failure');
            END;
            """
        )
        self.store.connection.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            self.provisioning.create_membership(
                "tenant-atomic",
                "dana",
                role="operator",
                permissions={"prospect.read"},
                administrator=self.admin_context,
            )

        self.assertIsNone(
            self.store.connection.execute(
                "SELECT 1 FROM auth_memberships WHERE tenant_id = 'tenant-atomic' AND principal_id = 'dana'"
            ).fetchone()
        )


if __name__ == "__main__":
    unittest.main()
