# Authentication Foundation and Tenant-Isolation Slice

**Base:** `origin/main` at `caa15c0f7d301c89102907de9c30aad7fb733a4c`
**Branch:** `feat/trusted-auth-provisioning-20261024`
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

- `media_house.auth.AuthenticationVerifier` is the explicit integration seam for a trusted
  identity/session adapter. The verifier must validate the external credential before it
  returns `VerifiedIdentity`; this project does not parse tokens or trust unsigned payloads.
- `AuthenticationBoundary.authenticate(...)` accepts only an opaque credential and an explicit
  tenant selection. It checks verifier output type, verified status, configured issuer/audience,
  expiry and not-before claims, then resolves membership before returning an immutable
  `AuthenticatedRequestContext`.
- `Principal` is immutable and can only be created inside the authentication boundary from
  `VerifiedIdentity`. Request-shaped actor IDs, roles and permission lists are not authority.
- `TenantContext.resolve(...)` requires a `Principal`, a requested tenant ID, a membership
  directory, and optional permission. It denies missing, unknown, inactive, mismatched, or
  insufficient membership instead of defaulting to a global tenant.
- Even a custom permission policy is capped by the intersection of verifier permissions and
  membership permissions; a policy cannot expand access.
- `MembershipDirectory` and `PermissionPolicy` are provider/storage-neutral interfaces.
  `SqliteMembershipDirectory` is a read-only adapter for the foundation tables; it is not an
  identity provider.

## Administrative provisioning

`media_house.provisioning.TenantProvisioningService` is the only application provisioning
interface in this slice. It requires an existing authenticated `TenantContext` with the
explicit `tenant.provision` or `membership.manage` permission. It:

- creates active tenants without accepting an administrator actor ID from the caller;
- creates memberships only for existing active tenants;
- permits membership creation in the administrator's own active tenant with
  `membership.manage`; cross-tenant membership provisioning additionally requires the
  explicit `tenant.provision` capability;
- rejects self-grant, unknown roles, duplicate memberships, empty permissions, and permissions
  outside the administrator's effective permission set; and
- records the authenticated administrator, administrator tenant, target tenant, affected
  identity, requested role/permissions, action, result, and status in the tenant audit log.

Tenant creation and its committed audit event share one transaction. Membership creation and
its committed audit event share one transaction. If the audit write fails, the provisioning
change rolls back. Unauthorized callers fail before any provisioning mutation; this slice does
not add public self-service signup or invitations.

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
restore, or production session security. The application must keep raw SQLite connections out
of untrusted request code; direct database access can bypass Python-level checks.

## Audit boundary

Tenant operations record the authenticated actor ID, tenant ID, action, object, UTC timestamp,
outcome, and minimized metadata in `tenant_audit_events`. Denied cross-tenant reads, updates,
deletes and attachment attempts are recorded without returning the other tenant's content.
A failed stream export is marked `delivery_failed`; it is never reported as delivered.

## Still required

Before production or a shared client deployment, implement and test:

1. a real trusted OIDC/session/token verification adapter implementing
   `AuthenticationVerifier` and validating signatures, key rotation, issuer, audience,
   nonce/state/session binding, revocation and clock skew according to the provider;
2. durable user lifecycle, invitations, membership administration and MFA;
3. a complete tenant-aware service/API boundary;
4. migration/backfill for all tenant-owned tables and same-tenant relationships;
5. authenticated file export/download authorization and object-storage policy;
6. MFA, secret management, encryption, retention, backup/restore and offboarding; and
7. full cross-tenant tests for every list/read/create/update/delete/export/file/job path.

The local Harvey integration is deliberately separate from this trust boundary. It consumes a
reviewed Harvey CSV export through `media-house harvey-import`; it does not import Harvey's
credentials, dashboard, sender, or network clients, and a Harvey export is not authentication
evidence or outreach permission.

The current implementation is a reviewable foundation for those next steps, not evidence that
production authentication or tenant isolation is complete.
