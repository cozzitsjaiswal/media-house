# Decisions and Open Questions

## Decisions (current)

| ID | Decision | Reason | Status |
|---|---|---|---|
| D-001 | Media House is the main business initiative | User’s stated vision is a broad AI business-systems company | Accepted direction |
| D-002 | Work-from-Anywhere India product experiment is separate and paused | The physical-product direction did not match the core vision | Paused, preserve files |
| D-003 | Maharashtra is the initial geographic focus; global expansion later | Local language, discovery and support are easier to validate in a focused market | Working assumption |
| D-004 | Hindi, Marathi and English are primary language requirements | Explicit user requirement | Required |
| D-005 | First product should be an internal lead-to-delivery Revenue Engine, then a narrow client workflow | This supports selling, scoping and delivering the business | Recommended, validate against existing repo |
| D-006 | Begin with service/automation implementation rather than full multi-industry SaaS | Reduces time to first evidence/revenue and avoids building unvalidated modules | Recommended |
| D-007 | Voice agent must be natural but not deceptive; disclose AI identity and obey channel/law/provider rules | Trust, safety, compliance and platform policy | Required |
| D-008 | Human approval required for live outreach, external calls/messages, spending, refunds, contracts, access to sensitive data and production deployment | Prevents uncontrolled consequential action | Required |
| D-009 | Client data residency/ownership must be selected and documented per client | “Data stays with customer” is not one architecture unless verified | Required |
| D-010 | Use existing software/integrations where possible before rebuilding POS/ERP/clinical systems | Competitive maturity and lower risk | Recommended |

## Open questions for the baseline audit

1. Which directory/repository is the canonical Media House codebase?
2. Does any Autonomous Revenue Engine/CRM/automation code already exist? Which components run?
3. Are there existing Git remotes, branches, tests, environment variables, databases or deployed services?
4. Which first ICP is most accessible: B2B service businesses, retail/distributors, or appointment businesses?
5. What actual problem did the first interviewed businesses report?
6. Is there a design-partner prospect willing to trial/pay for a narrow workflow?
7. Which hosting model is required for the first client?
8. What provider accounts are already lawfully available? Do not expose credentials.
9. What is the available weekly time and who will handle customer support?
10. What is the maximum acceptable monthly recurring tool spend after revenue? Default now: no spend without approval.

## Change log policy

Update this file only when a decision is explicitly made or when evidence overturns a prior decision. Record date, evidence, alternatives and impact. Do not silently change core strategy in code-generation steps.
