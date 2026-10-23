# Authentication and Tenant-Isolation Implementation Plan

**Plan date:** 2026-10-23
**Starting code state:** local SQLite store and CLI on `fix/release-readiness-20261023`
**Production status:** blocked until the gates in this plan are implemented and verified

## 1. Outcome and acceptance criteria

The first authenticated tenancy slice is complete only when an authenticated user can select
an authorized tenant, perform an allowed operation on that tenant's records, and receive a safe
denial for every record and operation outside that tenant or role.

Observable acceptance criteria:

1. A verified identity resolves to an active internal user and membership; a caller cannot
   choose an arbitrary `tenant_id` or actor name to gain authority.
2. Every tenant-owned list, read, create, update, delete, export and file operation is scoped
   by server-derived tenant context.
3. User A in tenant A cannot read, modify, export or infer the existence of tenant B records;
   user B receives the same protection in the opposite direction.
4. Role and workflow gates are enforced server-side, including requirements approval, outreach
   approval, membership changes, exports, payment reconciliation and production deployment.
5. Audit events identify the authenticated actor, tenant, request, action, target and outcome.
6. Missing authentication, membership, permission, tenant context or required step-up assurance
   fails closed without a partial write.
7. Migration, backup, export, deletion and offboarding behavior is documented and exercised
   before real client data is accepted.

The current local CLI/store does not meet these criteria. This plan deliberately describes the
smallest safe sequence rather than adding a partial `tenant_id` argument to existing methods.

## 2. Decisions and constraints

### 2.1 Decisions for this implementation sequence

- Keep the current SQLite/CLI milestone single-operator and synthetic/local-only until the
  authenticated service boundary exists.
- Build a modular monolith first; do not introduce microservices or a custom workflow engine.
- Use a verified OIDC/session identity boundary or another established provider-backed identity
  system. The provider is not selected in this plan.
- Use a typed `Principal` and `TenantContext`; do not use free-text `actor`, `researcher`,
  frontend tenant fields or database filenames as authorization.
- For shared managed hosting, prefer a relational database with database-level tenant defense in
  depth, such as PostgreSQL row-level policies where the final architecture supports them.
  SQLite remains an option for an explicitly single-tenant local deployment, not shared SaaS.
- Do not accept real client data until the negative test matrix and release gates pass.
- Do not add outbound messaging, payment providers, voice, production deployment or AI actions
  as part of the auth slice.

### 2.2 Existing behavior to preserve

The implementation must preserve these already-verified local controls while adding identity:

- atomic CSV validation/import;
- source evidence and explainable scoring;
- opt-out and permission safety;
- monotonic/protected lead states;
- human meeting and requirements approval gates;
- requirements content/version hashes and supersession rules;
- export staging and active-database path protection; and
- no provider call or outbound send from the local workflow.

Authorization must be an additional boundary, not a reason to weaken these state and data-integrity
checks.

## 3. Target contracts

### 3.1 Principal

Introduce an internal identity contract similar to:

```text
Principal
  principal_id       stable internal user id
  provider_subject   verified identity-provider subject
  status             active | suspended | disabled
  auth_assurance     session/MFA assurance level
  authenticated_at   server-observed UTC timestamp
```

The provider subject is an identity link, not a tenant permission. A disabled/suspended user
must fail before membership or business lookups.

### 3.2 Membership

```text
TenantMembership
  membership_id
  tenant_id
  principal_id
  role
  permission_version
  status             active | invited | suspended | revoked
  granted_by
  granted_at
  revoked_at
```

Membership changes require a privileged existing member or platform operation, a reason, and an
audit event. Invitations must not create active access before acceptance and identity verification.

### 3.3 Tenant context

```text
TenantContext
  tenant_id
  principal_id
  membership_id
  role
  effective_permissions
  request_id
  auth_assurance
```

Only the authentication/membership boundary creates this context. Downstream functions receive
the context or a narrower authorized capability; they do not accept an untrusted tenant ID as
authority.

### 3.4 Authorization result

Use a centralized policy decision with a stable reason code:

```text
AuthorizationDecision
  allowed: bool
  permission: stable action name
  reason_code: authenticated | no_membership | forbidden_role |
               wrong_tenant | invalid_state | step_up_required | disabled
```

Callers should not duplicate role logic in individual CLI commands, routes or templates.

## 4. Ordered implementation slices

Each slice should be a separately reviewable change with its own tests and rollback. Do not
combine identity-provider wiring, all table migrations and new business features in one change.

### Slice 0 — Freeze the security contract

**Deliverables**

- Adopt this document and `docs/SECURITY_ARCHITECTURE.md` as the design baseline.
- Enumerate the first supported roles and permissions.
- Decide the first supported hosting mode for the pilot.
- Create a data-flow/retention record for every field that will enter the pilot.

**Acceptance tests**

- The owner can identify the source of truth, tenant owner and allowed roles for each current
  table.
- No implementation task labels the current actor string as authenticated identity.
- The pilot is explicitly blocked if hosting, retention or data-flow ownership is unresolved.

### Slice 1 — Identity and membership adapter

**Scope**

- Add a provider-neutral identity adapter interface.
- Validate provider subject, token/session expiry, issuer/audience and authentication time.
- Map the verified subject to an internal `users` record.
- Add active/suspended/disabled handling and privileged MFA assurance metadata.

**Acceptance tests**

- Missing, expired, wrong-issuer and wrong-audience credentials are rejected.
- Disabled users cannot resolve a tenant context.
- A provider subject cannot be replaced by a caller-supplied display name.
- No raw token is written to logs, database rows or audit metadata.

**Not in scope**

- Choosing a vendor, building password storage, or adding a public signup flow before the
  provider/tradeoff decision is approved.

### Slice 2 — Tenant and membership schema

**Scope**

Add migration-managed records equivalent to:

- `users`;
- `tenants`;
- `tenant_memberships`;
- role/permission definitions or a versioned policy registry; and
- tenant status/hosting/retention metadata.

For shared hosting, add `tenant_id` to every tenant-owned table. Keep platform catalog rows
explicitly separate from tenant-owned service packages.

**Required constraints**

- active membership uniqueness per `(tenant_id, principal_id)`;
- tenant-owned primary/foreign-key relationships cannot cross tenants;
- tenant identifiers are not nullable on tenant-owned rows after backfill;
- suspended/revoked tenants and memberships deny new access; and
- audit records cannot point to a tenant-owned object in another tenant.

**Acceptance tests**

- A row cannot be inserted with a missing tenant where tenant scope is required.
- A relationship from tenant A to a tenant B object is rejected.
- Membership revocation takes effect before the next protected operation.

### Slice 3 — Request context and repository boundary

**Scope**

- Resolve `Principal` and `TenantContext` once at the application boundary.
- Make repository methods require context for tenant-owned operations.
- Remove unscoped list/read/export paths from the authenticated service boundary.
- Keep a clearly named single-tenant/local adapter only if it cannot be confused with hosted mode.

**Acceptance tests**

- Calling a tenant-owned repository method without context fails closed.
- A supplied `tenant_id` that differs from the context is rejected.
- List and count queries return only current-tenant records.
- A direct numeric ID from another tenant returns the safe not-found/denial response.

### Slice 4 — Centralized permission and state policy

**Scope**

Define stable permissions for at least:

| Permission | Example allowed roles | Extra gate |
|---|---|---|
| `prospect.read/write` | operator, tenant_admin | same tenant |
| `research.import` | operator, tenant_admin | source/evidence validation |
| `meeting.record` | operator, reviewer, tenant_admin | completed meeting and notes policy |
| `requirements.capture` | operator, reviewer, tenant_admin | linked completed meeting |
| `requirements.approve` | reviewer, tenant_admin | ready state, explicit labels, step-up if configured |
| `outreach.draft` | operator, tenant_admin | no external send |
| `outreach.approve` | reviewer, tenant_admin | permission/opt-out/state checks |
| `export.read` | tenant_admin, approved reviewer/finance | destination and audit policy |
| `membership.manage` | tenant_admin/platform_admin | step-up auth and audit |
| `payment.reconcile` | finance, tenant_admin | verified provider/manual evidence |
| `deployment.approve` | explicitly assigned release approver | step-up auth and release evidence |

**Acceptance tests**

- Every protected operation has an allow and deny case per relevant role.
- State-machine rules still reject invalid transitions after role authorization succeeds.
- AI/service-agent callers cannot grant themselves permissions or bypass human gates.

### Slice 5 — Authenticated audit identity

**Scope**

Extend the local audit concept to include:

- event ID;
- tenant ID where applicable;
- authenticated principal ID and actor type;
- action, object type/id, request ID and server timestamp;
- outcome and reason code; and
- minimized, schema-validated metadata.

Do not delete existing safety metadata such as `send_performed` or
`provider_call_performed`; reinterpret it as one part of a larger authenticated event.

**Acceptance tests**

- Denied cross-tenant reads and writes are audited without disclosing target content to the
  caller.
- Exports, deletes, role/membership changes and approvals have authenticated events.
- Ordinary tenant roles cannot edit or delete audit history.
- A request ID ties related authorization, mutation and provider events together.

### Slice 6 — Export, file and background-job isolation

**Scope**

- Require tenant context and `export.read` for CSV/requirements/file exports.
- Scope object-storage keys, temporary paths, download links and cleanup to the tenant and job.
- Require short-lived signed download authorization where remote storage is used.
- Propagate tenant context into jobs and reject jobs without a valid, non-revoked context.
- Store provider/integration secret references, never raw secrets or tenant credentials.

**Acceptance tests**

- Tenant A cannot export tenant B data by changing an ID, filename, storage key or job ID.
- An export audit exists before delivery is reported.
- Failed delivery is reported as unconfirmed and never as successful merely because an audit row exists.
- A revoked membership cannot download a previously created tenant file unless policy explicitly
  permits it and the new access is audited.

### Slice 7 — Database defense in depth

**Scope**

- For shared PostgreSQL hosting, enable least-privilege database roles and tenant policies/RLS
  where supported by the final design.
- Set a transaction-local tenant context from trusted server code, not a client header.
- Test policy behavior with both allowed and denied tenant contexts.
- Keep application checks even when database policies exist.

**Acceptance tests**

- A direct query through the application role cannot see another tenant when the context is A.
- Missing tenant context returns no tenant-owned rows and cannot write a row.
- Admin/support access requires an explicit elevated path and audit event.

SQLite local mode should instead use a clear single-tenant deployment contract and filesystem
protection; do not describe it as equivalent to shared database RLS.

### Slice 8 — Migration and pilot cutover

**Scope**

- Treat the current local database as an untrusted single-tenant source until an owner assigns
  its records to a named tenant.
- Back up and hash the source before migration.
- Create one explicit bootstrap tenant and one bootstrap membership only through an approved,
  auditable operation.
- Backfill tenant IDs through every relationship and verify counts/hash/invariants.
- Keep a read-only rollback copy and a tested restore path.

**Acceptance tests**

- Migration is repeatable or fails before partial commit.
- All backfilled rows resolve to exactly one tenant unless intentionally classified as platform data.
- Source/target record counts and relationship checks are reconciled.
- Export and delete/offboarding behavior is demonstrated on synthetic data before pilot use.

## 5. Migration map from the current SQLite schema

| Current table | Target classification | Required change |
|---|---|---|
| `companies` | tenant-owned business data | add `tenant_id`; tenant-scoped uniqueness for domain/name-region |
| `prospects` | tenant-owned | inherit/enforce company tenant; scope status/indexes/queries |
| `source_evidence` | tenant-owned | enforce prospect tenant; authenticated researcher/principal reference |
| `meetings` | tenant-owned | enforce prospect tenant; authenticated creator and visibility policy |
| `requirements_documents` | tenant-owned | enforce meeting/prospect same tenant; authenticated creator/approver/exporter |
| `outreach_drafts` | tenant-owned | enforce prospect tenant; separate draft/approval permissions |
| `service_packages` | tenant catalog or tenant-owned | choose explicitly; avoid global mutable package leakage |
| `order_requests` | tenant-owned | enforce prospect/package tenant; finance policy |
| `payment_requests` | tenant-owned | enforce order tenant; provider/manual evidence and reconciliation policy |
| `audit_events` | platform or tenant-scoped | add tenant, principal, request, outcome and immutable event fields |

The `tenant_id` of a child row must not be accepted independently of its parent. Use composite
foreign keys or an equivalent same-tenant constraint where the database supports it, and retain
service-level checks for clear errors and business rules.

## 6. Required test matrix

Use at least two tenants, two users and a role set containing an operator, reviewer, tenant admin
and an unauthorized/removed member. Use synthetic records with distinct IDs and content.

### 6.1 Boundary matrix

For each tenant-owned resource, test user A against tenant A and tenant B for:

- list and filtered list;
- count/summary/search;
- read by numeric/public ID;
- create with current tenant context;
- create attempting a different tenant ID;
- update own-tenant object;
- update other-tenant object;
- delete where supported;
- CSV/requirements/file export;
- background job enqueue/read/download; and
- audit/event lookup.

Expected result for tenant B access: no content, no existence leak, no partial write, and an
appropriate denied/not-found audit event.

### 6.2 Identity and role cases

- absent, malformed, expired, wrong-issuer and wrong-audience credentials;
- disabled/suspended user;
- active user with no membership;
- invited/revoked/suspended membership;
- active membership with insufficient role;
- tenant admin changing membership with and without step-up assurance;
- reviewer approving a draft/requirements document with invalid state or missing permission;
- operator attempting approval, payment reconciliation or deployment approval;
- delegated service/AI action with missing human authorization chain.

### 6.3 Regression cases from the current milestone

Keep the existing 226-test `unittest` suite green and add authenticated variants for:

- opt-out and withdrawn/blocked lead protection;
- monotonic lead-state transitions;
- meeting/requirements linkage and approval hash checks;
- requirements export audit-before-delivery behavior;
- active SQLite/sidecar export protection;
- no outbound provider call; and
- no real network/data dependency in local tests.

## 7. Operational and release checklist

Before a managed pilot:

- choose identity provider, hosting region and data-processing terms;
- require MFA for platform and tenant administrators;
- configure secret storage, key rotation and log redaction;
- define retention by data class and tenant contract;
- encrypt and restrict backups; perform a restore drill;
- test export, deletion and offboarding with evidence;
- configure rate limits, request IDs, structured logs and alerts;
- verify provider webhooks and idempotency before enabling integrations;
- run the full cross-tenant negative matrix in CI/staging;
- complete dependency/security scanning and review findings;
- document incident response, rollback and accountable approvers; and
- obtain owner approval for the selected data flow and first real-data pilot.

## 8. Stop conditions

Stop and do not accept real client data if any of the following occurs:

- tenant context is missing or can be supplied by the client without verification;
- any list/read/export path is unscoped;
- a cross-tenant negative test returns content or mutates data;
- audit records cannot identify the authenticated actor and tenant;
- a privileged action lacks the required role/step-up gate;
- secrets appear in code, logs, frontend payloads or ordinary database fields;
- backups cannot be restored or offboarding cannot be demonstrated;
- the chosen provider/hosting data flow is undocumented; or
- the only evidence of authorization is a caller-supplied name such as `founder` or `reviewer`.

## 9. First implementation PR boundary

The first implementation PR after this design should be intentionally small:

1. provider-neutral `Principal`/`TenantContext` contracts;
2. membership/policy interfaces with deterministic in-memory tests;
3. one tenant-scoped repository path around a read-only resource; and
4. cross-tenant deny tests proving the boundary before migrating every table.

Do not add the full portal, provider integrations, payments, voice, or production deployment
to that PR. Expand only after the first boundary is reviewed and its negative tests are stable.
