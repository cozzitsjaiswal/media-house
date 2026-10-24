"""Minimal tenant-scoped repository boundary for the existing SQLite store.

The legacy store and CLI remain explicitly local-only. This module adds a separate,
tenant-aware ownership registry for one representative resource (prospects) without
pretending that the existing schema has already been migrated to multi-tenancy.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator, TextIO

from .auth import Membership, MembershipDirectory, TenantContext
from .errors import AuthorizationError, NotFoundError, ValidationError
from .timeutil import utc_now


TENANT_FOUNDATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_tenants (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended', 'disabled'))
);

CREATE TABLE IF NOT EXISTS auth_memberships (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES auth_tenants(id) ON DELETE CASCADE,
    principal_id TEXT NOT NULL,
    role TEXT NOT NULL,
    permissions_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'invited', 'suspended', 'revoked')),
    UNIQUE(tenant_id, principal_id)
);
CREATE INDEX IF NOT EXISTS auth_memberships_principal_index
    ON auth_memberships(principal_id, tenant_id);

CREATE TABLE IF NOT EXISTS tenant_prospect_ownership (
    tenant_id TEXT NOT NULL REFERENCES auth_tenants(id) ON DELETE CASCADE,
    prospect_id INTEGER NOT NULL UNIQUE REFERENCES prospects(id) ON DELETE CASCADE,
    assigned_by TEXT NOT NULL,
    assigned_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, prospect_id)
);
CREATE INDEX IF NOT EXISTS tenant_prospect_ownership_tenant_index
    ON tenant_prospect_ownership(tenant_id, prospect_id);

CREATE TABLE IF NOT EXISTS tenant_audit_events (
    id INTEGER PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES auth_tenants(id) ON DELETE CASCADE,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id INTEGER,
    occurred_at TEXT NOT NULL,
    outcome TEXT NOT NULL
        CHECK (outcome IN ('authorized', 'committed', 'delivered', 'delivery_failed', 'denied')),
    metadata_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tenant_audit_events_scope_index
    ON tenant_audit_events(tenant_id, occurred_at, id);
"""

_PROSPECT_FIELDS = (
    "id",
    "company_id",
    "verified_status",
    "fit_reason",
    "fit_confidence",
    "fit_score",
    "score_reasons_json",
    "contact_name",
    "contact_role",
    "contact_channel",
    "channel_permission_status",
    "opt_out",
    "lead_status",
    "last_contact_at",
    "next_action",
    "next_action_due",
    "notes",
    "created_at",
    "updated_at",
    "company_name",
    "website",
    "region",
    "industry",
    "tenant_id",
)


def install_tenant_foundation_schema(connection: sqlite3.Connection) -> None:
    """Install only the foundation tables; no existing business rows are migrated."""

    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(TENANT_FOUNDATION_SCHEMA)


class SqliteMembershipDirectory(MembershipDirectory):
    """Read active memberships without exposing tenant existence details."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        install_tenant_foundation_schema(connection)
        self.connection = connection

    def membership_for(self, *, actor_id: str, tenant_id: str) -> Membership | None:
        row = self.connection.execute(
            """
            SELECT m.id, m.tenant_id, m.principal_id, m.role, m.permissions_json, m.status
            FROM auth_memberships m
            JOIN auth_tenants t ON t.id = m.tenant_id
            WHERE m.principal_id = ?
              AND m.tenant_id = ?
              AND m.status = 'active'
              AND t.status = 'active'
            """,
            (actor_id, tenant_id),
        ).fetchone()
        if row is None:
            return None
        try:
            permissions = json.loads(row[4])
            if not isinstance(permissions, list):
                return None
            return Membership(
                membership_id=row[0],
                tenant_id=row[1],
                principal_id=row[2],
                role=row[3],
                permissions=permissions,
                status=row[5],
            )
        except (TypeError, ValueError, json.JSONDecodeError, ValidationError):
            return None


class TenantScopedProspectRepository:
    """Tenant-scoped access to existing prospects through an ownership registry.

    ``delete`` removes the tenant ownership association rather than hard-deleting the
    legacy prospect and its related local evidence. The legacy store remains a separate
    single-operator mode until a complete migration is designed.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        install_tenant_foundation_schema(connection)
        self.connection = connection

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
    def _require_context(context: TenantContext) -> TenantContext:
        if not isinstance(context, TenantContext):
            raise AuthorizationError("a validated TenantContext is required")
        return context

    @staticmethod
    def _prospect_id(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValidationError("prospect_id must be a positive integer")
        return value

    @staticmethod
    def _row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
        result = dict(zip(_PROSPECT_FIELDS, row))
        try:
            result["score_reasons"] = json.loads(result.pop("score_reasons_json"))
        except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
            raise ValidationError("prospect contains invalid score reasons") from exc
        result["opt_out"] = None if result["opt_out"] is None else bool(result["opt_out"])
        return result

    def _scoped_prospect(self, prospect_id: int, context: TenantContext) -> dict[str, Any]:
        context = self._require_context(context)
        context.require("prospect.read")
        row = self.connection.execute(
            """
            SELECT p.id, p.company_id, p.verified_status, p.fit_reason, p.fit_confidence,
                   p.fit_score, p.score_reasons_json, p.contact_name, p.contact_role,
                   p.contact_channel, p.channel_permission_status, p.opt_out, p.lead_status,
                   p.last_contact_at, p.next_action, p.next_action_due, p.notes,
                   p.created_at, p.updated_at, c.name, c.website, c.region, c.industry,
                   o.tenant_id
            FROM tenant_prospect_ownership o
            JOIN prospects p ON p.id = o.prospect_id
            JOIN companies c ON c.id = p.company_id
            WHERE o.tenant_id = ? AND p.id = ?
            """,
            (context.tenant_id, prospect_id),
        ).fetchone()
        if row is None:
            self._record_denial(
                context,
                action="tenant_prospect_read",
                object_id=prospect_id,
                reason="not_in_tenant",
            )
            raise NotFoundError(f"prospect {prospect_id} was not found")
        return self._row_to_dict(tuple(row))

    def list(self, context: TenantContext) -> list[dict[str, Any]]:
        context = self._require_context(context)
        context.require("prospect.read")
        rows = self.connection.execute(
            """
            SELECT p.id, p.company_id, p.verified_status, p.fit_reason, p.fit_confidence,
                   p.fit_score, p.score_reasons_json, p.contact_name, p.contact_role,
                   p.contact_channel, p.channel_permission_status, p.opt_out, p.lead_status,
                   p.last_contact_at, p.next_action, p.next_action_due, p.notes,
                   p.created_at, p.updated_at, c.name, c.website, c.region, c.industry,
                   o.tenant_id
            FROM tenant_prospect_ownership o
            JOIN prospects p ON p.id = o.prospect_id
            JOIN companies c ON c.id = p.company_id
            WHERE o.tenant_id = ?
            ORDER BY p.id
            """,
            (context.tenant_id,),
        ).fetchall()
        return [self._row_to_dict(tuple(row)) for row in rows]

    def get(self, prospect_id: int, context: TenantContext) -> dict[str, Any]:
        return self._scoped_prospect(self._prospect_id(prospect_id), context)

    def attach_existing_prospect(self, prospect_id: int, context: TenantContext) -> dict[str, Any]:
        """Assign one existing local prospect to the current tenant without trusting a tenant ID."""

        context = self._require_context(context)
        context.require("prospect.write")
        prospect_id = self._prospect_id(prospect_id)
        existing = self.connection.execute(
            "SELECT tenant_id FROM tenant_prospect_ownership WHERE prospect_id = ?",
            (prospect_id,),
        ).fetchone()
        if existing is not None:
            if existing[0] == context.tenant_id:
                return self._scoped_prospect(prospect_id, context)
            self._record_denial(
                context,
                action="tenant_prospect_attach",
                object_id=prospect_id,
                reason="owned_by_other_tenant",
            )
            raise NotFoundError(f"prospect {prospect_id} was not found")
        if self.connection.execute("SELECT 1 FROM prospects WHERE id = ?", (prospect_id,)).fetchone() is None:
            self._record_denial(
                context,
                action="tenant_prospect_attach",
                object_id=prospect_id,
                reason="unknown_prospect",
            )
            raise NotFoundError(f"prospect {prospect_id} was not found")

        with self._transaction():
            try:
                self.connection.execute(
                    """
                    INSERT INTO tenant_prospect_ownership
                        (tenant_id, prospect_id, assigned_by, assigned_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (context.tenant_id, prospect_id, context.actor_id, utc_now()),
                )
            except sqlite3.IntegrityError as exc:
                raise NotFoundError(f"prospect {prospect_id} was not found") from exc
            self._record_audit(
                context,
                action="tenant_prospect_attached",
                object_id=prospect_id,
                outcome="committed",
                metadata={"prospect_id": prospect_id},
            )
        return self._scoped_prospect(prospect_id, context)

    def update_notes(self, prospect_id: int, notes: str, context: TenantContext) -> dict[str, Any]:
        context = self._require_context(context)
        context.require("prospect.write")
        prospect_id = self._prospect_id(prospect_id)
        if not isinstance(notes, str):
            raise ValidationError("notes must be a string")
        denied = False
        with self._transaction():
            updated = self.connection.execute(
                """
                UPDATE prospects
                SET notes = ?, updated_at = ?
                WHERE id = ?
                  AND EXISTS (
                      SELECT 1 FROM tenant_prospect_ownership
                      WHERE tenant_id = ? AND prospect_id = prospects.id
                  )
                """,
                (notes.strip(), utc_now(), prospect_id, context.tenant_id),
            )
            if updated.rowcount != 1:
                self._record_audit(
                    context,
                    action="tenant_prospect_update",
                    object_id=prospect_id,
                    outcome="denied",
                    metadata={"reason": "not_in_tenant"},
                )
                denied = True
            else:
                self._record_audit(
                    context,
                    action="tenant_prospect_updated",
                    object_id=prospect_id,
                    outcome="committed",
                    metadata={"fields": ["notes"]},
                )
        if denied:
            raise NotFoundError(f"prospect {prospect_id} was not found")
        return self._scoped_prospect(prospect_id, context)

    def delete(self, prospect_id: int, context: TenantContext) -> None:
        """Remove only the current tenant's ownership association, never the legacy prospect row."""

        context = self._require_context(context)
        context.require("prospect.delete")
        prospect_id = self._prospect_id(prospect_id)
        denied = False
        with self._transaction():
            deleted = self.connection.execute(
                "DELETE FROM tenant_prospect_ownership WHERE tenant_id = ? AND prospect_id = ?",
                (context.tenant_id, prospect_id),
            )
            if deleted.rowcount != 1:
                self._record_audit(
                    context,
                    action="tenant_prospect_delete",
                    object_id=prospect_id,
                    outcome="denied",
                    metadata={"reason": "not_in_tenant"},
                )
                denied = True
            else:
                self._record_audit(
                    context,
                    action="tenant_prospect_detached",
                    object_id=prospect_id,
                    outcome="committed",
                    metadata={"hard_delete": False},
                )
        if denied:
            raise NotFoundError(f"prospect {prospect_id} was not found")

    def export_csv(self, destination: TextIO, context: TenantContext) -> int:
        """Export only scoped rows to a caller-owned stream and audit the delivery."""

        context = self._require_context(context)
        context.require("prospect.export")
        if not hasattr(destination, "write") or not hasattr(destination, "flush"):
            raise ValidationError("tenant-scoped export requires a writable text stream")
        rows = self.list(context)
        payload_stream = io.StringIO()
        writer = csv.DictWriter(payload_stream, fieldnames=("id", "company_name", "lead_status", "notes"))
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "id": row["id"],
                    "company_name": row["company_name"],
                    "lead_status": row["lead_status"],
                    "notes": row["notes"],
                }
            )
        payload = payload_stream.getvalue()
        with self._transaction():
            audit_id = self._record_audit(
                context,
                action="tenant_prospect_export",
                object_id=None,
                outcome="authorized",
                metadata={"row_count": len(rows), "delivery_confirmed": False},
            )
        try:
            if destination.write(payload) != len(payload):
                raise OSError("short write during tenant prospect export")
            destination.flush()
        except Exception as exc:
            self._update_audit(
                audit_id,
                outcome="delivery_failed",
                metadata={"row_count": len(rows), "delivery_confirmed": False, "error_type": type(exc).__name__},
            )
            raise
        self._update_audit(
            audit_id,
            outcome="delivered",
            metadata={"row_count": len(rows), "delivery_confirmed": True},
        )
        return len(rows)

    def list_audit_events(self, context: TenantContext) -> list[dict[str, Any]]:
        context = self._require_context(context)
        context.require("audit.read")
        rows = self.connection.execute(
            """
            SELECT id, tenant_id, actor_id, action, object_type, object_id, occurred_at, outcome, metadata_json
            FROM tenant_audit_events
            WHERE tenant_id = ?
            ORDER BY id
            """,
            (context.tenant_id,),
        ).fetchall()
        result = []
        for row in rows:
            try:
                metadata = json.loads(row[8])
            except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
                raise ValidationError("tenant audit event contains invalid metadata") from exc
            result.append(
                {
                    "id": row[0],
                    "tenant_id": row[1],
                    "actor_id": row[2],
                    "action": row[3],
                    "object_type": row[4],
                    "object_id": row[5],
                    "occurred_at": row[6],
                    "outcome": row[7],
                    "metadata": metadata,
                }
            )
        return result

    def _record_denial(
        self,
        context: TenantContext,
        *,
        action: str,
        object_id: int | None,
        reason: str,
    ) -> None:
        with self._transaction():
            self._record_audit(
                context,
                action=action,
                object_id=object_id,
                outcome="denied",
                metadata={"reason": reason},
            )

    def _record_audit(
        self,
        context: TenantContext,
        *,
        action: str,
        object_type: str = "prospect",
        object_id: int | None,
        outcome: str,
        metadata: dict[str, Any],
    ) -> int:
        try:
            serialized = json.dumps(metadata, ensure_ascii=False, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise ValidationError(f"tenant audit metadata is not valid JSON: {exc}") from exc
        cursor = self.connection.execute(
            """
            INSERT INTO tenant_audit_events
                (tenant_id, actor_id, action, object_type, object_id, occurred_at, outcome, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (context.tenant_id, context.actor_id, action, object_type, object_id, utc_now(), outcome, serialized),
        )
        return int(cursor.lastrowid)

    def _update_audit(self, audit_id: int, *, outcome: str, metadata: dict[str, Any]) -> None:
        serialized = json.dumps(metadata, ensure_ascii=False, sort_keys=True, allow_nan=False)
        with self._transaction():
            self.connection.execute(
                "UPDATE tenant_audit_events SET outcome = ?, metadata_json = ? WHERE id = ?",
                (outcome, serialized, audit_id),
            )
