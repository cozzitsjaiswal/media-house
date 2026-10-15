# AI Agent Handoff Contract

The coding/implementation agent receives only the approved requirements package and permitted repository context. It must never infer permission from access to a file or API key.

## Input contract

- `task_id`
- `client_id` (internal reference; avoid unnecessary personal details)
- `requirements_version`
- `requirements_hash`
- `approval_status` (must be `APPROVED`)
- `repo_or_workspace` (explicit allowed path)
- `allowed_operations` (read, write branch, run tests, stage deploy, etc.)
- `prohibited_operations` (send communications, access production data, deploy production, spend, modify auth, etc.)
- `tech_constraints`
- `acceptance_tests`
- `data_classification`
- `integrations_and_test_doubles`
- `budget/time limit`
- `stop_conditions`

## Required output contract

- implementation plan and affected files;
- assumptions and unresolved questions;
- tasks mapped to requirement IDs;
- schema/migration plan and rollback;
- code changes in an isolated branch/workspace;
- commands/tests executed and exact results;
- acceptance-test status;
- security/privacy review findings;
- dependency and license notes;
- estimated operational costs and external provider calls;
- known limitations and risks;
- staging/demo instructions;
- deployment checklist and rollback instructions;
- human approval request before external/consequential action.

## Mandatory stop conditions

Stop and request a human decision if requirements conflict, client approval is missing, the proposed feature processes health/sensitive data not covered by the approved data flow, credentials are unavailable, vendor costs exceed limits, tests fail without a safe workaround, a destructive migration is required, a new paid service is required, a message/call must be sent, or production deployment is requested.
