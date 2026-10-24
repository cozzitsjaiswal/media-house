"""Explicit administrative provisioning for tenants and memberships.

This module is intentionally narrower than a user-management system. It exposes only
validated tenant and membership creation through an already-authenticated administrative
context. It does not implement signup, invitations, password handling, OIDC, MFA, or
unrestricted SQL access.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any

from .auth import TenantContext
from .errors import AuthorizationError, ValidationError
from .normalization import normalize_token
from .tenant_repository import TenantScopedProspectRepository, install_tenant_foundation_schema


TENANT_PROVISION_PERMISSION = "tenant.provision"
MEMBERSHIP_MANAGE_PERMISSION = "membership.manage"
ALLOWED_MEMBERSHIP_ROLES = frozenset({"owner", "admin", "operator", "client_member", "viewer"})


def _identifier(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a non-empty string")
    result = value.strip()
    if not result or any(ord(character) < 32 for character in result):
        raise ValidationError(f"{field_name} must be a non-empty printable string")
    return result


def _permission_values(values: Iterable[str]) -> frozenset[str]:
    if isinstance(values, (str, bytes)):
        raise ValidationError("permissions must be a collection of strings")
    try:
        result = frozenset(_identifier(value, field_name="permission") for value in values)
    except TypeError as exc:
        raise ValidationError("permissions must be a collection of strings") from exc
    if not result:
        raise ValidationError("permissions must contain at least one permission")
    return result


def _safe_metadata_value(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or any(ord(character) < 32 for character in text):
        return None
    return text


class TenantProvisioningService:
    """Small, transaction-safe administrative provisioning boundary."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        install_tenant_foundation_schema(connection)
        self.connection = connection
        # The repository owns the canonical tenant-audit serialization shape. This
        # service is deliberately composed with it rather than exposing SQL callers.
        self._audit = TenantScopedProspectRepository(connection)

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    @staticmethod
    def _require_administrator(context: TenantContext, permission: str) -> TenantContext:
        if not isinstance(context, TenantContext):
            raise AuthorizationError("a validated administrative TenantContext is required")
        context.require(permission)
        return context

    def _record_provisioning_audit(
        self,
        administrator: TenantContext,
        *,
        action: str,
        outcome: str,
        metadata: dict[str, Any],
    ) -> int:
        return self._audit._record_audit(  # noqa: SLF001 - shared internal audit seam
            administrator,
            action=action,
            object_type="provisioning",
            object_id=None,
            outcome=outcome,
            metadata=metadata,
        )

    def create_tenant(self, tenant_id: str, *, administrator: TenantContext) -> dict[str, str]:
        """Create one active tenant through an existing tenant administrator.

        The administrator's actor and tenant come only from the authenticated context;
        neither is accepted as a method argument. Validation failures and duplicate
        attempts get a denied audit event in the administrator's tenant, while all
        business changes remain atomic with the successful audit.
        """

        administrator = self._require_administrator(administrator, TENANT_PROVISION_PERMISSION)
        metadata_base = {
            "administrator_actor_id": administrator.actor_id,
            "administrator_tenant_id": administrator.tenant_id,
            "target_tenant_id": _safe_metadata_value(tenant_id),
            "affected_identity": None,
        }
        failure: ValidationError | None = None
        target_id = ""
        with self._transaction():
            try:
                target_id = _identifier(tenant_id, field_name="tenant_id")
                self.connection.execute(
                    "INSERT INTO auth_tenants (id, status) VALUES (?, 'active')",
                    (target_id,),
                )
            except sqlite3.IntegrityError as exc:
                failure = ValidationError("tenant already exists")
                self._record_provisioning_audit(
                    administrator,
                    action="tenant_provisioned",
                    outcome="denied",
                    metadata={**metadata_base, "result": "rejected", "error": "duplicate_tenant"},
                )
            except ValidationError as exc:
                failure = exc
                self._record_provisioning_audit(
                    administrator,
                    action="tenant_provisioned",
                    outcome="denied",
                    metadata={**metadata_base, "result": "rejected", "error": "invalid_tenant_id"},
                )
            else:
                self._record_provisioning_audit(
                    administrator,
                    action="tenant_provisioned",
                    outcome="committed",
                    metadata={
                        **metadata_base,
                        "target_tenant_id": target_id,
                        "result": "created",
                        "status": "active",
                    },
                )
        if failure is not None:
            raise failure
        return {"id": target_id, "status": "active"}

    def create_membership(
        self,
        tenant_id: str,
        principal_id: str,
        *,
        role: str,
        permissions: Iterable[str],
        administrator: TenantContext,
        membership_id: str | None = None,
    ) -> dict[str, object]:
        """Create one membership without allowing self-grant or privilege expansion."""

        administrator = self._require_administrator(administrator, MEMBERSHIP_MANAGE_PERMISSION)
        metadata_base = {
            "administrator_actor_id": administrator.actor_id,
            "administrator_tenant_id": administrator.tenant_id,
            "target_tenant_id": _safe_metadata_value(tenant_id),
            "affected_identity": _safe_metadata_value(principal_id),
        }
        failure: ValidationError | None = None
        target_id = ""
        target_principal = ""
        target_role = ""
        target_permissions = frozenset[str]()
        target_membership_id = ""

        with self._transaction():
            try:
                target_id = _identifier(tenant_id, field_name="tenant_id")
                target_principal = _identifier(principal_id, field_name="principal_id")
                target_role = normalize_token(_identifier(role, field_name="role"))
                if target_role not in ALLOWED_MEMBERSHIP_ROLES:
                    raise ValidationError("role is not an allowed membership role")
                target_permissions = _permission_values(permissions)
                if target_principal == administrator.actor_id:
                    raise ValidationError("an administrator cannot grant a new membership to themself")
                if not target_permissions.issubset(administrator.permissions):
                    raise ValidationError("granted permissions exceed the administrator's effective permissions")
                target_membership_id = (
                    _identifier(membership_id, field_name="membership_id")
                    if membership_id is not None
                    else uuid.uuid4().hex
                )
                tenant = self.connection.execute(
                    "SELECT status FROM auth_tenants WHERE id = ?",
                    (target_id,),
                ).fetchone()
                if tenant is None or tenant[0] != "active":
                    raise ValidationError("target tenant is unknown or inactive")
                self.connection.execute(
                    """
                    INSERT INTO auth_memberships
                        (id, tenant_id, principal_id, role, permissions_json, status)
                    VALUES (?, ?, ?, ?, ?, 'active')
                    """,
                    (
                        target_membership_id,
                        target_id,
                        target_principal,
                        target_role,
                        json.dumps(sorted(target_permissions)),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                failure = ValidationError("membership already exists or violates a provisioning constraint")
                self._record_provisioning_audit(
                    administrator,
                    action="membership_provisioned",
                    outcome="denied",
                    metadata={**metadata_base, "result": "rejected", "error": "constraint"},
                )
            except ValidationError as exc:
                failure = exc
                self._record_provisioning_audit(
                    administrator,
                    action="membership_provisioned",
                    outcome="denied",
                    metadata={
                        **metadata_base,
                        "result": "rejected",
                        "error": "validation_or_privilege_policy",
                    },
                )
            else:
                self._record_provisioning_audit(
                    administrator,
                    action="membership_provisioned",
                    outcome="committed",
                    metadata={
                        **metadata_base,
                        "target_tenant_id": target_id,
                        "affected_identity": target_principal,
                        "membership_id": target_membership_id,
                        "role": target_role,
                        "permissions": sorted(target_permissions),
                        "result": "created",
                        "status": "active",
                    },
                )
        if failure is not None:
            raise failure
        return {
            "id": target_membership_id,
            "tenant_id": target_id,
            "principal_id": target_principal,
            "role": target_role,
            "permissions": sorted(target_permissions),
            "status": "active",
        }
