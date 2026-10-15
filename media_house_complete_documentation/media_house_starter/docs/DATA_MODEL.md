# Proposed Data Model

This is a logical starting point, not a migration file. Adapt after the baseline code audit. All tenant-owned business tables need tenant scope unless documented otherwise.

## Core entities

| Entity | Purpose | Key relationships/fields |
|---|---|---|
| `users` | Platform identities | id, email/phone, auth-provider subject, status, MFA state |
| `tenants` | Client workspaces | id, name, status, hosting mode, locale, retention profile |
| `tenant_memberships` | Role access | tenant_id, user_id, role, permissions, granted_by |
| `companies` | Prospect/client organizations | id, tenant_id (when customer-owned), name, domain, industry, region |
| `prospects` | Potential client leads | company_id, source_url, observed_at, fit reasons, confidence, qualification state |
| `contacts` | Business contacts where lawful/necessary | company_id, name, business role, channel, source, permission/consent status, verified_at |
| `communication_preferences` | Suppression/permissions | contact_id, channel, permitted purpose, source, timestamp, status, opt_out_at |
| `outreach_drafts` | Proposed communication | prospect_id, channel, content/version, reviewer, approval status |
| `communication_events` | Sent/received message/call metadata | provider id, status, direction, timestamp, channel, minimized metadata |
| `meetings` | Meeting logistics | prospect/client, time zone, attendees, status, notes visibility |
| `requirements_documents` | Versioned client scope | client_id, version, status, content hash, approver, approved_at |
| `requirements` | Individual requirements | document_id, key, status label, priority, acceptance criteria, traceability |
| `proposals` | Commercial scope | version, amount, currency, tax details, expiry, approval/status |
| `contracts` | Agreements/SOW | document ref, signed status, effective/end dates, scope version |
| `projects` | Delivery projects | client_id, approved requirements version, state, owner, dates |
| `tasks` | Implementation work | project_id, requirement key, assigned_to/agent, status, estimates, dependencies |
| `test_cases` | Acceptance and regression tests | requirement key, setup, steps, expected result, result/evidence |
| `invoices` | Billing records | client_id, amount, currency, due date, status, provider reference |
| `payment_events` | Authoritative payment status log | invoice_id, provider reference, verified state, amount, currency, webhook signature status |
| `support_tickets` | Ongoing operations | client_id, priority, status, SLA class, owner, related release |
| `integration_connections` | Provider config reference | tenant_id, provider, account reference, secret reference (never raw secret) |
| `ai_jobs` | AI work audit | task type, prompt version, input refs, cost, output schema status, reviewer, status |
| `audit_events` | Security/business audit | actor, tenant, action, object, timestamp, source/request ID, redacted metadata |
| `files` | Documents/deliverables | tenant/client, storage reference, classification, retention date, hash |

## Required design rules

- Tenant ownership is enforced in backend/database, never only in UI.
- Do not put sensitive personal/health data in generic `prospects`, `ai_jobs` or application logs.
- Avoid saving raw call recordings by default; store minimal outcomes/metadata where possible.
- Separate contact permission/suppression records from prospect score.
- Payment state comes from verified provider/bank records or explicit manual reconciliation—not from an LLM interpretation of a screenshot.
- Use idempotency keys on booking/order/payment mutation endpoints where supported.
- Use immutable event/audit records for significant state changes.
- Set retention categories and deletion/export rules before production.
- Do not store credentials directly. Store secret-manager references and access controls.

## State enums (illustrative)

- `prospect_status`: new, researched, qualified, draft_ready, approved_to_contact, contacted, replied, meeting_booked, discovery_complete, proposal_sent, negotiation, won, lost, nurture, suppressed.
- `requirements_status`: draft, needs_clarification, ready_for_review, approved, superseded.
- `project_status`: scoped, payment_gate_pending, planned, implementing, QA, staging_review, acceptance_pending, deployment_approved, deployed, handover, support, paused, cancelled.
- `payment_status`: unknown, pending, authorized, paid_verified, failed, refunded_verified, disputed, manually_reconciled.
- `consent_status`: unknown, granted, withdrawn, not_required_confirmed (requires basis/evidence), blocked.

## Tenant-isolation test matrix

For user A in tenant A and user B in tenant B, test every operation (list/read/create/update/delete/export/file access) against records from both tenants. Unauthorized access must return a safe denial and must not leak existence/content through response details. Run tests at API and database policy layers where applicable.
