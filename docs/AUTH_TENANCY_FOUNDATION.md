# Authentication Foundation and Tenant-Isolation Slice

**Base:** `origin/main` at `caa15c0f7d301c89102907de9c30aad7fb733a4c`
**Branch:** `feat/auth-tenant-foundation-20261023`
**Status:** local implementation only; not pushed, merged, deployed, or production-ready

## Scope

This slice adds the smallest explicit authorization boundary around one representative
resource: existing prospect rows. It does **not** claim that the current CLI/store is an
authenticated multi-tenant application.

The legacy `MediaHouseStore` and CLI remain an explicitly local-only path. They continue to
accept local actor strings and unscoped database operations for synthetic/local workflows.
They must not receive real shared-client data until a complete migration and authenticated
service boundary replace that mode.

## New contracts

- `media_house.auth.VerifiedIdentity` is evidence that a trusted authentication adapter has
  already validated an external credential. No login, token parsing, password handling, or
  provider integration is implemented here.
- `Principal` is immutable and can only be created from `VerifiedIdentity`. Direct construction
  from request-shaped dictionaries or actor strings fails.
- `TenantContext.resolve(...)` requires a `Principal`, a requested tenant ID, a membership
  directory, and optional permission. It denies missing, unknown, inactive, mismatched, or
  insufficient membership instead of defaulting to a global tenant.
- `MembershipDirectory` and `PermissionPolicy` are provider/storage-neutral interfaces.
  `SqliteMembershipDirectory` is a read-only adapter for the foundation tables; it is not an
  identity provider.

## Tenant-scoped prospect path

`TenantScopedProspectRepository` is the first repository boundary that requires a validated
`TenantContext`. It supports:

- attaching an existing local prospect to the current tenant without accepting a tenant ID from
  the operation as authority;
- tenant-filtered list/read;
- tenant-checked notes update;
- tenant ownership detach instead of destructive hard delete; and
- tenant-filtered stream export with audit-before-delivery and delivery outcome.

`tenant_prospect_ownership.prospect_id` is unique, so one legacy prospect cannot silently be
shared between tenants in this slice. Cross-tenant object lookups return the same not-found
shape and record a denied tenant audit event.

## Schema and migration boundary

The slice adds only foundation tables:

- `auth_tenants`;
- `auth_memberships`;
- `tenant_prospect_ownership`; and
- `tenant_audit_events`.

No existing business row is backfilled and no broad `tenant_id` migration is attempted. A
future migration must explicitly assign existing rows to a tenant, reconcile relationships,
create a rollback/backup copy, and verify counts and ownership before enabling real data.

The current SQLite file remains a local storage boundary, not a substitute for shared-database
row-level security. The tenant repository uses explicit ownership joins and application checks,
but it does not provide PostgreSQL RLS, filesystem encryption, remote identity, MFA, backup
restore, or production session security.

## Audit boundary

Tenant operations record the authenticated actor ID, tenant ID, action, object, UTC timestamp,
outcome, and minimized metadata in `tenant_audit_events`. Denied cross-tenant reads, updates,
deletes and attachment attempts are recorded without returning the other tenant's content.
A failed stream export is marked `delivery_failed`; it is never reported as delivered.

## Still required

Before production or a shared client deployment, implement and test:

1. a real trusted OIDC/session/token verification adapter;
2. tenant/user lifecycle and authenticated membership administration;
3. a complete tenant-aware service/API boundary;
4. migration/backfill for all tenant-owned tables and same-tenant relationships;
5. authenticated file export/download authorization and object-storage policy;
6. MFA, secret management, encryption, retention, backup/restore and offboarding; and
7. full cross-tenant tests for every list/read/create/update/delete/export/file/job path.

The current implementation is a reviewable foundation for those next steps, not evidence that
production authentication or tenant isolation is complete.
