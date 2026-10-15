# Proposed Architecture

## 1. Design approach

Use a modular monolith for the first production-capable version unless a verified repository or traffic requirement justifies separate services. This reduces deployment complexity and preserves module boundaries. Do not introduce microservices, agent swarms or a custom workflow engine before a real workflow requires them.

## 2. Two products, one operating company

### A. Internal Media House OS

- Prospect and company CRM.
- Research/source evidence store.
- Outreach draft/approval and suppression lists.
- Replies, meetings and opportunity pipeline.
- Meeting notes and requirements generation.
- Proposal/SOW and approval records.
- Client/projects/tasks/issues.
- Invoice/payment state records and reconciliation.
- Support, renewals and service-cost analytics.
- AI job queue, usage/cost trace, approval inbox and audit history.

### B. Client Business Systems

A client-specific workspace with approved modules and configuration. Potential modules include appointment booking, lead routing, staff tasks, restaurant order integration, inventory alerts and reporting. The first client module is determined by the first signed pilot, not by a prebuilt giant feature list.

## 3. Logical components

1. Web frontend: admin console and client portal.
2. API: authentication, validation, authorization and business rules.
3. Relational database: clients, users, pipeline, requirements, projects and audit records.
4. Background workers: scheduled reminders, approved outbound events, integrations, reports and retries.
5. Integration adapters: email provider, calendar, voice/telephony, WhatsApp Business Platform, payment provider, accounting/POS systems.
6. AI orchestration layer: approved tools, prompt/version management, cost limits and deterministic validation around model output.
7. File/document storage: requirements, proposals, reports and client-owned deliverables with signed/expiring access where appropriate.
8. Observability: application logs, provider events, health checks, cost and error dashboards.

## 4. Candidate technology stack—not yet committed

- Frontend: React + TypeScript; styling system chosen after repository audit.
- Backend: Python FastAPI or existing backend if already sound.
- Database: PostgreSQL.
- Authentication: established auth provider or secure first-party auth with proven libraries.
- Jobs: database-backed task queue initially; choose managed queue later if needed.
- Voice: provider evaluated for India reachability, compliant caller ID, recording/consent, streaming audio, webhooks, Marathi/Hindi speech quality, pricing and data location.
- AI speech: evaluate Sarvam APIs for Indian-language STT/TTS, and compare alternatives through a scripted benchmark. Sarvam documents speech-to-text, TTS, translation and code-mixed language support at https://docs.sarvam.ai/; capabilities and prices must be rechecked before use.
- Deployment: choose a supported low-cost host after security/region/provider review; free tiers are not assumed to be production-ready or unlimited.

Do not select technology by preference alone. Inspect existing project code and use the simplest maintainable path.

## 5. Multi-tenant model

Every client-owned record must include a `tenant_id` (or equivalent). Tenant context is derived server-side from the authenticated identity and membership record—not trusted from an arbitrary frontend field.

Use:
- tenant membership table and role mapping;
- server-side permission checks for each API operation;
- row-level security if supported by the selected database architecture;
- cross-tenant negative tests for list/read/create/update/delete/export;
- tenant-scoped object storage policies;
- audit logs for elevated/admin access;
- automated security tests before any real client data is accepted.

Supabase’s security documentation explicitly recommends enabling RLS and granting least privilege for exposed tables, and testing both allow/deny behavior: https://supabase.com/docs/guides/database/secure-data and https://supabase.com/docs/guides/database/postgres/row-level-security. This is an example, not a mandate to use Supabase.

## 6. Client hosting options

Every client proposal chooses and documents one mode:

1. Client-hosted: deploy into a client-controlled cloud account.
2. Client-local: local machine/server; document network access, backups, patching and recovery limitations.
3. Media House managed: tenant-isolated managed platform with explicit data processing terms.
4. Hybrid: sensitive business data remains in customer systems; the automation reads/writes only approved fields and sends minimum necessary payloads to external APIs.

The product should not promise zero external data flow if cloud AI/voice/email/payment providers process inputs. If an AI provider receives call audio or transcript, it is part of the data flow and must be disclosed/contracted appropriately.

## 7. AI control plane

Every AI task should have:
- task type and versioned prompt;
- authorized context/data fields;
- allowed tools and their schemas;
- tenant and user scope;
- cost/time limits;
- output schema validation;
- confidence/uncertainty handling;
- human approval requirement;
- audit record of input references, output, reviewer and final action.

AI output is untrusted until validated. Do not let model-generated strings become arbitrary SQL, shell commands, public messages, payment actions or production deployments without validation and authorization.

## 8. State machines

Lead lifecycle:
`new → researched → qualified → outreach_draft → approved_to_contact → contacted → replied → meeting_booked → discovery_complete → proposal_draft → proposal_sent → negotiation → won/lost/nurture/suppressed`.

Delivery lifecycle:
`intake → scope_draft → requirements_approved → proposal_accepted → payment_gate_met → implementation → QA → staging_review → client_acceptance → production_approved → deployed → handover → support/maintenance → offboarding`.

Transitions must be explicit, logged and authorized; do not let the AI skip approval gates.

## 9. Integration contract pattern

Each provider adapter should implement:
- validated request schema;
- idempotency where available;
- timeouts and retry policy;
- signature verification for inbound webhooks;
- provider message/call/payment ID storage;
- normalized status mapping;
- rate-limit handling;
- error categorization and dead-letter handling;
- per-tenant credential reference (never raw secret in a table/log);
- test/sandbox mode and mock adapter.

## 10. Deployment pipeline

1. Local development with synthetic data.
2. Lint/type-check/unit tests.
3. Integration and authorization tests.
4. Security/dependency scan.
5. Staging deployment with synthetic or approved test data.
6. Human QA and owner approval.
7. Backup/release note and rollback plan.
8. Production deployment by explicitly authorized operator.
9. Smoke checks and monitored rollout.

Coding agents must work on a branch or isolated workspace and present diff/test summary. Never deploy production automatically because an AI says tests passed.
