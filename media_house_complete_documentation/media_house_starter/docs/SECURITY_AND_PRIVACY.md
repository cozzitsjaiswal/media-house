# Security & Privacy Requirements

## 1. Classification

Minimum data classes:
- Public business information.
- Internal prospecting/operational information.
- Client confidential information (workflows, prices, contracts, credentials references).
- Personal data (names, phone numbers, email, recordings/transcripts).
- Sensitive/high-risk data, including health-related details or financial account information.

Assign access, retention and controls by class. Health data needs separate risk review and must not enter the generic MVP by accident.

## 2. Security baseline

- Use established authentication, password hashing/provider-managed identities, session expiry and MFA for privileged accounts.
- Enforce server-side authorization on every API. Apply least privilege.
- Separate tenants using verified tenant membership and database controls.
- Use TLS for data in transit and appropriate encryption at rest/backup.
- Keep API secrets in a secret manager/environment configuration, not code, frontend variables, CSVs or logs.
- Redact auth headers, tokens, passwords, call content and health data from logs.
- Validate all input schemas; use parameterized database access; defend against injection, XSS, CSRF where applicable, SSRF and insecure file uploads.
- Verify signed provider webhooks and reject replay/duplicate events.
- Rate-limit login, appointment mutations, outreach and expensive AI/voice endpoints.
- Add request IDs, structured audit logs and retention limits.
- Pin/review dependencies, scan vulnerabilities and keep a patch process.
- Use synthetic data in development and tests.

## 3. Backups and recovery

Before a production client:
- choose backup frequency and retention based on contract/risk;
- encrypt backups and restrict access;
- perform a restore test;
- document recovery time/data loss targets agreed with the client;
- plan for client offboarding and data export/deletion;
- document failure mode if internet, API, calendar or payment provider is down.

## 4. Data-flow record per client

Document for every field:
- purpose;
- source of truth;
- which component reads/writes it;
- whether sent to an AI/telephony/messaging/payment vendor;
- hosting region and access role;
- retention and deletion date/rule;
- backup/export path;
- legal/contract basis and customer disclosure where required.

If call audio/transcripts or patient details are sent to an external model provider, do not claim that the data stays only on the client’s device.

## 5. Client-controlled data options

- Client-hosted/local hosting can improve control but means the client and Media House must agree on patching, backup, remote support and incident responsibilities.
- Managed SaaS simplifies operations but requires tenant isolation, data processing terms, access controls and vendor due diligence.
- Hybrid can reduce data sent to cloud, but every external API payload must be documented and minimized.

No deployment option is automatically “most secure”; verify the actual threat model and operational competence.

## 6. AI security

- Treat prospect websites, documents, emails and client-provided data as untrusted input; they can contain prompt injection or malicious instructions.
- Separate system instructions from untrusted retrieved text.
- AI cannot independently grant permissions, exfiltrate records, modify auth, approve payments, suppress audits or deploy to production.
- Use typed allowlisted tools; validate all outputs; constrain SQL/shell/code execution.
- Require approval for outbound messages, access to sensitive records, contracts, payment/refund actions and production deployment.
- Limit context to fields needed for the task; do not upload entire customer databases to an LLM by default.
- Log model/provider, prompt version, data references, usage cost and reviewer while minimizing content retention.

## 7. Healthcare-specific caution

For doctor/clinic systems:
- Do not store clinical notes or symptom descriptions unless necessary, contracted and reviewed.
- The default appointment bot captures administrative details only.
- No diagnosis or treatment advice.
- Define clinic vs. Media House data responsibilities and incident response.
- Review applicable Indian law and the ABDM Health Data Management Policy where relevant.
- Conduct security review before any patient data is used in a live pilot.

## 8. Incident response

Runbook: detect → contain/disable affected integration → preserve logs → notify owner and required parties as assessed → investigate root cause → remediate → test → document lessons → resume only with approval.

## 9. Release gate

A client release is blocked if critical security tests fail, tenant isolation is not verified, secrets are exposed, backup restore is untested for a data-critical system, required consent/disclosure is missing, or production rollout has no accountable approver and rollback plan.
