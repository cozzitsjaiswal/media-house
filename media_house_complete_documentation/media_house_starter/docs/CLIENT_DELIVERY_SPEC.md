# Client Delivery Specification — Meeting to Working System

## Objective

The founder conducts the client discovery meeting. Media House then creates a structured, customer-approved requirements package. A separate AI solution-design/coding workflow may implement it, but only after scope and commercial approval.

## Pipeline

1. Pre-meeting research (verified public facts only).
2. Meeting agenda and questions.
3. Meeting notes with client permission and agreed recording policy.
4. Requirements extraction.
5. Uncertainty and conflict review.
6. Solution options and risk analysis.
7. Human review with client.
8. Approved requirements baseline.
9. Proposal/SOW, milestones and payment triggers.
10. Payment/authorization gate.
11. Build plan, tasks, acceptance tests and security plan.
12. Implementation branch/workspace.
13. Automated tests and AI review.
14. Human technical/security review.
15. Staging demo and client acceptance.
16. Production approval and deployment.
17. Documentation, training, backup/restore and handover.
18. Monthly support/reporting and renewal.

## Required requirements document

Use `templates/CLIENT_REQUIREMENTS_TEMPLATE.md` and, where practical, validate against `schemas/client_requirements.schema.json`.

The document must cover:
- Client/business and decision-maker.
- Problem statement and current workflow.
- Evidence and consequences.
- Goals and measurable outcomes.
- User types and permissions.
- User journeys and functional requirements.
- Non-functional requirements.
- Data categories, source of truth and hosting model.
- Integrations and credentials ownership.
- Explicit out-of-scope items.
- Dependencies and client responsibilities.
- Risks and unresolved questions.
- Price, taxes, payment milestones and dates.
- Support/maintenance and exit terms.
- Acceptance tests per requirement.

## Requirement status labels

- `CLIENT_STATED`: directly stated by client.
- `OBSERVED`: evidenced in current process/source.
- `PROPOSED`: suggested solution, not approved yet.
- `ASSUMPTION`: provisional assumption requiring validation.
- `OPEN_QUESTION`: unresolved question.
- `APPROVED`: client-approved baseline, with approver/date/version.
- `OUT_OF_SCOPE`: explicitly excluded.

AI must never silently promote an assumption or proposal into an approved requirement.

## Coding-agent handoff packet

The build agent receives only the approved, minimized data package it needs:
- requirements JSON/Markdown and version hash;
- architecture constraints and permitted repo/workspace;
- tech stack and dependency policy;
- approved data model and integration contracts;
- task list and acceptance tests;
- security/privacy/data retention requirements;
- environment variable names, not secret values;
- test/staging instructions;
- explicit permissions (read/write/deploy/communicate);
- deadline and budget limit;
- stop conditions and escalation contacts.

The agent returns:
- implementation plan before material changes;
- changed files and rationale;
- schema migrations and rollback if any;
- tests run and outputs;
- test gaps and residual risks;
- screenshots/demo steps where useful;
- deployment/recovery instructions;
- cost/usage implications;
- known limitations and remaining work.

## Change control

Any change to scope, budget, deadline, hosting, client data flow, integrations or acceptance criteria requires a written change request and client approval. Keep prior approved versions for traceability.

## Acceptance and release

A feature is not “done” because code exists. The reviewer must execute the acceptance test, check access permissions, confirm error behavior, verify the client-approved environment and record acceptance. Production releases require explicit human approval and a rollback plan.
