# Product Requirements — Media House Revenue Engine and Client Systems

## 1. Purpose

Create an internal operations system that turns verified, relevant business prospects into qualified opportunities, meetings, scoped projects, delivered software and recurring support. It should also generate reusable client-system modules from approved requirements.

## 2. Personas

- **Founder/admin:** approves strategy, outreach, prices, payments, production releases and client access.
- **Sales operator:** researches prospects, drafts communication, logs activity and books meetings.
- **Delivery lead:** converts meeting records into scope, dependencies, timelines and acceptance tests.
- **AI/coding agent:** assists with research, proposal drafting, development and tests within explicit permissions.
- **Client owner:** reviews a demo, approves scope, sees project status, manages authorized users and accepts delivery.
- **Client staff:** access only assigned client workflows and records.
- **Support operator:** handles incidents, requests and renewal tasks.

## 3. MVP boundary

### Included in first internal MVP

1. Prospect/company CRM with source URL and verification status.
2. Manual CSV import and deduplication (not uncontrolled scraping).
3. Fit scoring with explainable reasons and confidence.
4. Personalized outreach drafts requiring human approval.
5. Communication and opt-out history.
6. Pipeline stages, tasks, meeting scheduling and notes.
7. Client requirements document generation.
8. Proposal/statement-of-work draft and acceptance criteria.
9. Project/task generation from approved scope.
10. Payment/invoice status records, with manual verification at first.
11. Delivery checklist, issues, handover and support/renewal tracking.
12. Audit log and basic dashboard.
13. Export of business-owned data.

### Not in MVP

- Auto-send cold email to large scraped lists.
- Unauthorized automated cold calling.
- Unsolicited WhatsApp bulk campaigns.
- An “undetectable AI” voice agent that impersonates a human.
- Automatic price negotiation, contract signing, refunds or bank/payment confirmation.
- A full POS, accounting, EMR/EHR, ERP, school management or hotel system.
- Automatic production deployment without human review.
- Storing detailed patient symptoms or health records unless separately approved, legally reviewed and secured.
- Global operations before country-specific compliance review.

## 4. MVP user stories and acceptance criteria

### Lead creation
As a sales operator, I can add a company with its public source, industry, geography, contact channel, reason for fit and verification status.

Acceptance:
- Required fields validated.
- Source URL, observed date and researcher captured.
- Duplicate detection by normalized company/domain/contact.
- Unknown fields remain unknown; system does not fabricate email/phone.

### Lead qualification
As a sales operator, I can record need, role, fit, likely urgency, budget range if shared, preferred language and next action.

Acceptance:
- Score displays reason and confidence.
- “Unknown” differs from “No.”
- The AI cannot claim an unverified pain point as fact.

### Outreach draft and approval
As the founder, I can review a message before it sends.

Acceptance:
- No provider call occurs until approved.
- Approved channel and lawful basis/consent status are recorded as required.
- Opt-out/suppression is checked at send time.
- Every send is logged with provider message ID and status.

### Meeting-to-requirements handoff
As the founder, I can turn meeting notes into a structured requirements document.

Acceptance:
- Facts, assumptions, open questions and client-stated needs are separated.
- Scope, exclusions, data handling, integrations, cost/timeline, risks and acceptance tests are present.
- No code-generation job can move to “approved” until the human marks the requirements approved.

### Project generation
As a delivery lead, I can turn approved requirements into milestones, tasks, test cases and deployment gates.

Acceptance:
- Every requirement maps to tasks and one or more acceptance tests.
- Security/privacy tasks are generated for sensitive data and external integrations.
- Production deployment is blocked without human release approval.

### Client data isolation
As a client user, I can access only my tenant’s records and authorized functions.

Acceptance:
- Cross-tenant read/write tests fail safely.
- Server checks tenant and role on every request; frontend hiding alone is not authorization.
- Audit logs record administrative access and export/delete events.

## 5. Client module requirements

### General-purpose service workflow (first candidate)
- Enquiry capture/import.
- Lead stage, assignee and follow-up due date.
- Conversation/meeting notes with source and visibility rules.
- Quote/proposal status.
- Won/lost reason.
- Reminder/task queue.
- Pipeline metrics and CSV export.

### Appointment workflows (later, clinic-sensitive)
- Slot availability from a trusted calendar/source.
- Booking, rescheduling and cancellation states.
- Minimal appointment details.
- Approved notification preferences.
- Human handoff and exception queue.
- No diagnosis or medical advice from the assistant.

### Restaurant workflows (later)
- Prefer read/write integration with client-approved POS API, if available.
- Orders/tables/menu must have a clear authoritative source of truth.
- Prevent duplicate ticket/order creation; use idempotency keys.
- Offline behavior and device sync must be specified before production.
- Do not replace tax/billing functions without local legal and vendor compatibility review.

## 6. Non-functional requirements

- Reliability: retry transient calls with limits; dead-letter failures; do not silently drop orders or messages.
- Performance: set targets after workflow/traffic discovery rather than inventing SLAs.
- Security: least privilege, MFA for admins, secrets server-side, encrypted transport, backups and audit.
- Privacy: minimal data, clear retention and deletion/export process.
- Localization: resource files for Hindi/Marathi/English; store canonical values and locale separately.
- Accessibility: keyboard navigation, clear errors and readable UI.
- Observability: structured logs, correlation ID, health checks and alerting once hosted.
- Portability: export client-owned data and document offboarding.

## 7. Definition of an accepted deliverable

A requirement is accepted only when its acceptance test passes, a human reviewer approves the result, and the client confirms acceptance according to the statement of work. “Generated code exists” is not the same as “feature is delivered.”
