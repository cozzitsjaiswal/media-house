# Next Production-Security Plan

**Scope:** planning only; this document does not add a real identity provider, broad schema
migration, hosted database, or production deployment.

**Reviewed base:** `origin/main` at `caa15c0f7d301c89102907de9c30aad7fb733a4c`

**Current branch:** `feat/trusted-auth-provisioning-20261024`

The current code is a local integration seam. The existing CLI and `MediaHouseStore` are still
local-only, and the tenant repository covers only a representative prospect-ownership path.
The stages below are the smallest order that can turn that seam into a defensible shared-client
service without silently treating SQLite or caller-supplied fields as authentication.

## Current data model and boundary

The legacy SQLite schema currently contains:

- `companies`, `prospects`, and `source_evidence` for lead identity and research facts;
- `meetings` and `requirements_documents` for customer discovery and versioned scope;
- `outreach_drafts` for approval-gated local drafts;
- `service_packages`, `order_requests`, and `payment_requests` for pending commercial intents;
- `audit_events` for the older local-only audit path; and
- discovery/finder review JSON and cache artifacts outside the database.

The foundation adds `auth_tenants`, `auth_memberships`, `tenant_prospect_ownership`, and
`tenant_audit_events`. It does not backfill legacy rows, protect the raw SQLite file, or make
the existing CLI safe for shared-client data.

## Phase 1 — Real identity verification and API request context

**Goal:** replace the synthetic verifier seam with one named, configured provider without
allowing request fields to become identity evidence.

Deliverables:

1. Select OIDC/OAuth or another reviewed provider and document issuer, audience, subject claim,
   scopes/claims, tenant claim policy, key discovery, rotation, clock-skew, revocation, and
   logout/session behavior.
2. Implement an adapter that validates signatures and claims before producing the existing
   `VerifiedIdentity` contract. Reject unsigned tokens, wrong algorithms, wrong issuer/audience,
   expired/not-yet-valid tokens, malformed subjects, replayed authorization responses, and
   revoked sessions where the provider supports revocation.
3. Add API/request middleware that receives only the credential and an explicit tenant selector,
   calls `AuthenticationBoundary`, and passes an immutable request context to services.
4. Remove actor/role/permission fields from authorization inputs and audit construction. Keep
   them only as values derived from verified identity and membership services.
5. Add negative tests for bad signatures, key rotation, issuer/audience confusion, clock skew,
   revocation, session expiry, missing credentials, tenant switching, and permission expansion.

Exit evidence: a provider-backed request can reach a tenant-scoped service with an authenticated
actor and tenant, while forged request JSON cannot change either value.

## Phase 2 — Identity, tenant, membership, and offboarding lifecycle

**Goal:** make provisioning durable and reviewable instead of relying on synthetic rows.

Deliverables:

1. Add a durable principal/user registry keyed by provider subject plus issuer; do not key users
   only by email, display name, or caller-provided IDs.
2. Define tenant lifecycle states, membership states, invitations, accepted identity binding,
   suspension, revocation, tenant closure, and offboarding semantics.
3. Separate tenant-scoped administrators from platform-wide provisioning capabilities. Keep
   `tenant.provision` as an explicitly privileged capability, require step-up/MFA for it, and
   record target tenant and affected identity in the audit trail.
4. Add authenticated membership update/revoke flows. Make privilege reduction and revocation
   take effect on the next request; do not rely on a long-lived cached context.
5. Add audit identity fields for provider issuer/subject, internal principal ID, tenant ID,
   membership ID, request/correlation ID, result, and policy decision.

Exit evidence: no self-service or admin action can create an active membership for an unbound
identity, silently retain access after revocation, or change another tenant without a documented
platform capability.

## Phase 3 — Tenant-aware migration of legacy resources

**Goal:** move from the representative ownership registry to complete tenant ownership.

Migration controls before writing shared data:

- take a restorable backup and test the restore;
- create a staging copy and a deterministic tenant assignment map;
- reconcile row counts, foreign keys, duplicate identities, orphan rows, and ambiguous owners;
- write a migration journal with checksums and a rollback procedure; and
- require an explicit cutover decision after dry-run evidence.

Resource plan:

| Resource | Tenant decision and required controls |
|---|---|
| `companies`, `prospects`, `source_evidence` | Tenant-owned by default. Add tenant keys and same-tenant constraints; replace the temporary ownership registry after reconciliation. Decide explicitly whether a company may be shared or must be copied per tenant. |
| `meetings` | Tenant-owned through its prospect. Enforce same-tenant prospect links and tenant-scoped list/read/write/delete paths. |
| `requirements_documents` | Tenant-owned through meeting and prospect. Preserve version history, approval hashes, and supersession links within one tenant. |
| `outreach_drafts` | Tenant-owned. Require tenant context for create/read/edit/approve/reject and preserve the no-send approval gate. |
| `service_packages` | Decide whether packages are platform catalog rows or tenant-owned custom rows. Never infer this from a caller-supplied package ID. Orders must bind to the chosen scope and immutable price/currency snapshot. |
| `order_requests` | Tenant-owned through prospect and package. Enforce same-tenant relationships and tenant-scoped cancellation/list/read. |
| `payment_requests` | Tenant-owned through order. Keep provider references and links scoped; never treat a request row as proof of payment. |
| `audit_events`, `tenant_audit_events` | Define one canonical event model, bind actor/tenant/request identity server-side, and preserve denied attempts without leaking other tenants. |
| review JSON, discovery caches, exports, files | Treat as untrusted artifacts. Bind every import/export/download to a tenant and authenticated actor; do not use a file's contents as identity or authorization proof. |

Exit evidence: every resource path has an ownership rule, every foreign-key traversal proves
same-tenant scope, and no legacy unscoped service remains reachable from a shared-client API.

## Phase 4 — Database, files, jobs, and delivery isolation

**Goal:** prevent a correct application check from being bypassed by another execution path.

Deliverables:

1. Move shared deployment to a database with row-level security or an equivalent enforced
   tenant boundary. Set tenant context on each transaction and test connection-pool reset and
   failure behavior.
2. Remove raw database connections from request handlers and background tasks. Services receive
   the immutable request/job context, not arbitrary tenant strings.
3. Add tenant-aware file/object-storage keys, signed download authorization, content-type/size
   limits, path traversal protection, retention, and deletion/offboarding behavior.
4. Add tenant/correlation context to background jobs, scheduled work, webhook handlers, and
   retry/dead-letter records. A missing job tenant must fail closed rather than default global.
5. Make exports audit-before-delivery and record delivery outcome without claiming success
   from an unobserved filesystem/network operation.

Exit evidence: API, worker, file, export, webhook, and retry paths all enforce the same tenant
policy and have cross-tenant negative tests.

## Phase 5 — Adversarial test matrix and release gates

Add tests for each resource and path covering:

- forged actor, tenant, role, permission, membership, and provider claims;
- wrong issuer/audience, bad signature, expired/not-yet-valid/revoked session, replay, and
  clock-skew cases;
- tenant A reading, updating, deleting, exporting, downloading, or enqueueing tenant B data;
- admin provisioning another tenant without the explicit platform capability;
- revoked/suspended membership during a long-running request or retry;
- duplicate/replayed invitation or provisioning requests;
- rollback on audit, database, file, queue, and provider failures;
- malformed, oversized, symlinked, stale, or tampered import/export artifacts; and
- direct raw-SQL/file access as an explicitly documented deployment threat, with a database/RLS
  control required before shared-client release.

Release gates should require all tests on a disposable database, migration reconciliation,
restore evidence, secret scanning, dependency review, and a human security review. Passing unit
tests alone is not a production authorization certification.

## Phase 6 — Secrets, backup, retention, and incident operations

Before shared-client use:

- store provider secrets in a managed secret store or equivalent, never in tracked files,
  exports, logs, or audit metadata;
- rotate signing/JWKS/client/database credentials and document emergency revocation;
- encrypt backups and sensitive storage, restrict operator access, and run restore drills;
- define retention and deletion for users, tenants, leads, meeting notes, requirements, exports,
  files, jobs, and audit records, including legal holds where required;
- implement tenant offboarding with access revocation, export/deletion decisions, and evidence;
- monitor denied cross-tenant attempts, provisioning changes, token failures, export failures,
  and unusual data access; and
- prepare incident response for credential compromise, tenant-boundary failure, accidental
  export, and provider outage.

## Explicit non-goals of this branch

This plan does not implement a provider, session middleware, broad tenant migration, RLS,
production file storage, MFA, real credentials, deployment, or production-data changes. The
current branch remains a reviewable local foundation plus a reviewed Harvey CSV handoff.
