# Security Architecture and Tenant-Isolation Review

**Evidence date:** 2026-10-23
**Repository state reviewed:** `fix/release-readiness-20261023` at `7066e8667b8944c8657be6c3280cadeb54c221ca`
**Scope:** current local implementation, retained Media House architecture/security documents, and the design required before accepting real client data

## Executive decision

The current repository is a **single-operator, local SQLite milestone**. It is not an
authenticated multi-tenant application and must remain limited to synthetic or otherwise
explicitly approved local test data until identity, authorization, tenant isolation,
authenticated audit identity, backup/recovery, and export/deletion controls are implemented
and tested.

The next production-capable direction should be a **modular monolith** with:

1. an established identity provider or equivalent verified OIDC implementation;
2. a server-created request principal and tenant context;
3. tenant-scoped repositories and server-side authorization on every operation;
4. a relational hosted database with database-level tenant defense in depth where supported;
5. immutable, authenticated audit records; and
6. explicit handling for client-hosted, client-local, managed, and hybrid data-flow modes.

No authentication vendor, hosting provider, or database migration is selected by this
document. Those choices require an implementation decision after the current security
contract and test matrix are accepted.

## 1. Evidence and current boundary

### 1.1 What was inspected

- `media_house/store.py`: SQLite schema, persistence methods, lifecycle checks, exports and audit writes.
- `media_house/cli.py`: all current command entry points and identity inputs.
- `media_house/requirements.py`: requirements validation, hashing and approval metadata.
- `README.md`, `docs/BASELINE_AUDIT.md`, and `docs/COMPLETION_CHECKLIST.md`.
- Retained source documents under `media_house_complete_documentation/media_house_starter/`, especially `ARCHITECTURE.md`, `DATA_MODEL.md`, `SECURITY_AND_PRIVACY.md`, `PROJECT.md`, and `PRODUCT_REQUIREMENTS.md`.

### 1.2 What exists now

The current store creates these SQLite tables:

| Table | Current purpose | Current ownership boundary |
|---|---|---|
| `companies` | normalized business/company records | none; global within the selected SQLite file |
| `prospects` | lead qualification and pipeline state | none; linked to `companies` only |
| `source_evidence` | source URL, observation, researcher and fit evidence | none; linked to `prospects` only |
| `meetings` | human-authored meeting notes and recording metadata | none; linked to `prospects` only |
| `requirements_documents` | versioned, hashed requirements records | none; linked to meetings/prospects |
| `outreach_drafts` | pending outreach drafts and local approval metadata | none; linked to prospects |
| `service_packages` | local package catalog | global; `name` is globally unique in the database |
| `order_requests` | local order intents | none; linked to prospects/packages |
| `payment_requests` | pending provider/payment intents | none; linked to orders |
| `audit_events` | local activity records | `actor` is free text; no authenticated subject or tenant |

The schema has no `tenant_id`, `user_id`, membership table, permission table, request ID,
provider identity, or authenticated principal. Foreign keys protect record relationships,
but they do not enforce tenant boundaries.

### 1.3 Current trust boundary

The current trust boundary is the local process and the filesystem containing the SQLite
database. The caller controls:

- the database path through `MEDIA_HOUSE_DB` or `--db`;
- the CLI actor/researcher strings;
- the selected prospect, meeting, document, package, order and payment identifiers;
- the output path for exports; and
- whether the process is run against a real or synthetic file.

The store validates formats, state transitions, consent-like metadata and some filesystem
hazards, but it does not authenticate the caller. A non-empty human-looking actor is a local
attestation, not proof of identity, membership, role or authority.

### 1.4 Explicitly absent

The current milestone does not provide:

- login, sessions, MFA, API tokens, OIDC verification or password handling;
- tenant creation, membership, invitation, suspension or role assignment;
- server-derived tenant context;
- API or web authorization middleware;
- row-level security or equivalent database policy;
- authenticated export/delete authorization;
- durable production audit identity;
- encryption/key management, backup/restore drills or hosted secret management;
- provider webhook verification or an outbound provider adapter; or
- cross-tenant negative tests.

These are release blockers for real client data, not optional polish.

## 2. Security objectives

### 2.1 Primary objectives

1. A caller can act only as an authenticated principal whose identity and status were verified.
2. A principal can access only tenants to which it has an active membership.
3. Every tenant-owned read, write, export, file access and workflow transition is scoped to the
   server-derived tenant context and checked against the principal's role/permission.
4. Cross-tenant access fails safely without revealing whether the target object exists.
5. Significant access and state changes are attributable to an authenticated actor, tenant,
   request/correlation ID and outcome.
6. Client data flow, retention, backup, export and deletion are documented for the selected
   hosting mode before production use.
7. AI, integrations, payment state and deployment actions remain typed, bounded and human-
   approved; model output is never an authorization decision.

### 2.2 Non-goals for the first auth slice

- Building a full public client portal.
- Selecting or integrating a voice, WhatsApp, email or payment provider.
- Moving the local CLI directly into a multi-user production interface.
- Treating an actor string, database filename, frontend field or hidden UI control as identity.
- Adding a broad workflow engine or microservices before the authorization boundary is sound.

## 3. Proposed target architecture

### 3.1 Request flow

```text
HTTP request / approved CLI boundary
        │
        ▼
Identity verification
(OIDC/session/token; provider choice pending)
        │
        ▼
Principal resolution
(subject, status, MFA assurance, auth time)
        │
        ▼
Membership lookup
(tenant, role, permission version, membership status)
        │
        ▼
TenantContext
(tenant_id, principal_id, role, request_id, auth assurance)
        │
        ├── policy/permission check
        ├── tenant-scoped repository query
        ├── state/business-rule validation
        ├── transaction and audit event
        └── redacted response
```

The frontend may display a selected tenant, but it cannot establish tenant authority. The
server must derive the effective tenant from the verified principal and membership record.
If a user may act in more than one tenant, tenant selection is an input to membership
resolution, not a direct database filter trusted from the request.

### 3.2 Deployment modes

| Mode | Recommended isolation baseline | Important limitation |
|---|---|---|
| Client-hosted | tenant may map to a client-controlled deployment/database; verified identity still required for shared operator access | patching, backup, support and incident ownership must be contracted |
| Client-local | one approved local deployment/file can be a single-tenant boundary | filesystem access is still powerful; local operator identity remains a separate question |
| Media House managed | shared service with PostgreSQL tenant scoping, database policy defense in depth, verified memberships and encrypted backups | requires the strongest operational controls and data-processing terms |
| Hybrid | keep sensitive source records in the client system; send only approved minimum fields | every external payload and returned event needs a documented source of truth |

SQLite remains suitable for the current local single-operator milestone and possibly an
explicitly single-tenant client-local mode. It is not, by itself, a sufficient shared-hosting
tenant-isolation mechanism.

### 3.3 Identity and principal contract

The application boundary should consume a typed principal rather than a free-text actor:

```text
Principal {
  principal_id: stable internal user id
  subject: verified identity-provider subject
  status: active | suspended | disabled
  auth_assurance: session/MFA assurance level
  authenticated_at: timestamp
}
```

The resolved request context should add:

```text
TenantContext {
  tenant_id: stable tenant id
  principal_id: stable internal user id
  membership_id: membership id
  role: tenant role
  permissions: effective permission set
  request_id: correlation id
  auth_assurance: copied from the principal
}
```

No business method should accept an arbitrary `tenant_id` or actor string as the source of
authority. Business methods may accept a `TenantContext` or a narrower capability object that
has already passed authorization.

## 4. Tenant ownership model

### 4.1 Identity and platform records

These records are platform-level and should not be confused with tenant-owned business data:

- `users`: internal identity linked to a verified provider subject;
- `tenants`: client workspace and lifecycle state;
- `tenant_memberships`: user-to-tenant role and status;
- provider/integration catalog records that contain no tenant secret or customer payload;
- security events that describe platform-level authentication events.

### 4.2 Tenant-owned records

The following records must be tenant-scoped in a shared deployment:

- companies, prospects and source evidence;
- contacts and communication preferences when added;
- meetings, meeting notes and requirements documents;
- outreach drafts, approval records and suppression state;
- tenant-specific service packages, orders and payment intents;
- files, exports, AI jobs and provider event metadata;
- projects, tasks, proposals, contracts, invoices, support tickets and renewals when added.

`audit_events` should contain `tenant_id` for tenant events and may allow a null tenant only
for explicitly platform-scoped security events. A null tenant must never be used as a shortcut
for a tenant-owned event.

### 4.3 Relationship invariants

Every foreign-key path between tenant-owned records must preserve the same tenant. For example:

- a prospect's company belongs to the same tenant as the prospect;
- a meeting, requirements document, draft, order and payment request resolve through a prospect
  in the same tenant;
- a package used by an order is either a tenant package in that tenant or an explicitly immutable
  platform catalog item;
- a file, export or AI job references only objects in the current tenant;
- an audit event's object and actor membership resolve to the same tenant unless the event is
  explicitly platform-scoped.

Application checks and database constraints/policies should both enforce these invariants where
the selected database supports them.

## 5. Authorization model

### 5.1 Initial roles

These are proposed role names, not implemented permissions:

| Role | Intended authority | Must not imply |
|---|---|---|
| `platform_admin` | platform support, tenant lifecycle, emergency security operations with explicit audit | routine access to all client content without a reason and elevated audit |
| `tenant_admin` | manage tenant members/configuration and all ordinary tenant workflows | platform-wide access or irreversible production actions by default |
| `operator` | research, prospect, meeting and task operations allowed by tenant policy | membership changes, payment verification or production deployment |
| `reviewer` | approve drafts, requirements and scoped workflow gates | identity administration or payment confirmation |
| `finance` | prepare/reconcile commercial and payment records within the tenant | editing requirements or sending communications unless separately granted |
| `client_member` | read and update explicitly permitted client-facing records | internal research, other tenants or administrative controls |
| `service_agent` | no direct human login; only typed delegated actions with a human/system authority chain | self-granting permissions or bypassing approvals |

Use permissions for action-level decisions where roles become too broad. A role is not a
substitute for object ownership, state-machine checks or approval requirements.

### 5.2 Operation policy

For every operation, evaluate all of:

1. authentication and principal status;
2. active membership in the target tenant;
3. required permission for the operation;
4. object tenant ownership and relationship invariants;
5. workflow/state transition rules;
6. sensitive-data or step-up-authentication requirements; and
7. auditability and idempotency requirements.

The same policy must apply to list, read, create, update, delete, export, file download,
bulk operations, background jobs and provider callbacks. A route is not secure because its
button is hidden in the frontend.

### 5.3 Safe denial behavior

- Unauthenticated: `401` or equivalent session challenge; do not perform the operation.
- Authenticated without membership/permission: `403` where the caller is already authorized
  to know the tenant context; otherwise use a non-enumerating `404` for object lookups.
- Object in another tenant: behave as not found at the resource boundary and emit no object
  content or existence detail.
- Invalid state transition: `409`/domain error with no partial mutation.
- Missing required step-up/MFA for a high-impact action: deny and require the configured
  stronger assurance.

The exact HTTP status mapping can be chosen with the API framework, but the information
disclosure rule is architectural.

## 6. Audit and accountability

### 6.1 Required audit fields

The current `audit_events.actor` text field is insufficient for production. The target event
contract should include at least:

| Field | Requirement |
|---|---|
| `event_id` | immutable unique identifier |
| `tenant_id` | tenant scope, or null only for explicit platform security events |
| `actor_principal_id` | authenticated user/service identity; never only display text |
| `actor_type` | human, service, delegated-agent, platform |
| `action` | stable allowlisted action name |
| `object_type` / `object_id` | target resource, if any |
| `request_id` | correlation ID across API, jobs and provider callbacks |
| `occurred_at` | server-generated UTC timestamp |
| `outcome` | allowed, denied, failed, committed, delivered/unconfirmed where relevant |
| `reason_code` | structured decision/failure reason |
| `metadata` | minimized, redacted, schema-validated context |

Audit records should be append-only to ordinary application roles. Administrative access,
exports, deletes, membership changes, role changes, authentication events, outbound actions,
payment reconciliation and deployment approvals require explicit events.

### 6.2 Existing audit limitations

The current store does record many workflow actions and deliberately records flags such as
`send_performed: false` and `provider_call_performed: false`. That is useful local evidence,
but the record is not an authenticated security audit because `actor` is caller-supplied,
there is no tenant, request ID or authenticated subject, and some reads/exports are not
audited. Do not present current audit rows as proof of identity or complete access history.

## 7. Data protection and lifecycle

### 7.1 Data classes

Apply the retained project classification:

1. public business information;
2. internal prospecting and operational information;
3. client confidential information such as workflow, pricing, contracts and credential references;
4. personal data such as names, phone numbers, email, recordings and transcripts; and
5. high-risk data such as health or financial account information.

The current local milestone may contain public business data, internal prospecting information,
business contact fields and meeting/requirements notes. It must not be assumed safe for health
data, payment credentials, raw recordings or production client data.

### 7.2 Storage and transfer rules

- Store only the minimum fields needed for the workflow.
- Keep credentials in a secret manager or provider account reference, never in tables, CSVs or logs.
- Redact tokens, auth headers, passwords, raw call content and high-risk data from logs.
- Treat prospect websites, documents, emails and client text as untrusted input, including prompt
  injection attempts.
- Document every external provider field, purpose, region, retention and deletion path.
- Use encrypted transport and appropriate encryption at rest/backups in hosted modes.
- Define export, deletion, backup and offboarding behavior before production use.

### 7.3 File/export boundary

The current requirements export protects the active SQLite database and stages file output before
audit commit. That is a useful delivery-integrity control, not an authorization control. A hosted
export must additionally check tenant membership, export permission, sensitive-data policy,
destination approval, request identity and audit outcome. Object storage keys and download URLs
must be tenant-scoped and short-lived where appropriate.

## 8. Threat model and required mitigations

| Threat | Current exposure | Required mitigation before production |
|---|---|---|
| IDOR / guessed numeric ID | every store lookup is ID-based without tenant context | scope queries by tenant and return non-enumerating denial |
| caller-supplied actor spoofing | CLI/API callers provide `--actor`, `--researcher` or equivalent strings | derive actor from verified principal; retain display name only as metadata |
| cross-tenant list leakage | list methods query the whole database | require tenant context and scope every list/count/export |
| cross-tenant write/linkage | foreign keys do not compare tenant ownership | same-tenant composite relationships plus service-level checks |
| role escalation | no memberships or roles exist | centralized permission policy, guarded membership/role changes, audit and step-up auth |
| export exfiltration | CSV export has no actor; requirements export has only local attestation | explicit export permission, destination policy, authenticated audit and download controls |
| local DB theft | SQLite file is an ordinary local file | client-local threat model, filesystem permissions, encrypted storage/backups where required |
| stale membership / TOCTOU | no membership lifecycle | resolve active membership per request/transaction and version sensitive changes |
| malicious untrusted content | source/requirements text can influence AI or operators | isolate untrusted content, schema-validate outputs, allowlist tools and require review |
| webhook replay/forgery | no provider callbacks currently exist | signature verification, timestamp/replay window, idempotency and event audit |
| secret leakage | no provider integration currently exists | secret-manager references, redacted logs and no frontend secret exposure |
| destructive workflow bypass | local state protections exist but are not identity/role gates | explicit transition policy, optimistic locking/idempotency and authenticated approval |
| backup/offboarding failure | no backup/restore or deletion job | documented retention, encrypted backups, restore drill and export/delete evidence |

## 9. Release gates

Real client data remains blocked until all applicable gates pass:

- identity verification and privileged-account MFA are implemented;
- tenant membership and role checks are enforced server-side;
- every tenant-owned table/query/export/file access has a tenant scope;
- cross-tenant negative tests cover list/read/create/update/delete/export/file access;
- audit events contain authenticated actor, tenant and request identity;
- sensitive data classification, retention, export, deletion and offboarding are documented;
- secrets, encryption, backups and restore testing match the chosen hosting mode;
- provider callbacks are verified before any live integration is enabled;
- production deployment has an accountable approver, rollback plan and monitoring; and
- the owner approves the hosting/data-flow and first production pilot.

Until then, the current local workflow is a development/synthetic-data tool, not a tenant-safe
SaaS platform.
