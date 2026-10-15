# PROJECT.md — Media House

## 1. Executive definition

**Media House** is a proposed AI-assisted business systems and automation company. It will initially serve businesses in Maharashtra, India, in Hindi, Marathi and English, then expand to other Indian states and international markets only after a repeatable offer, safe delivery process and viable economics are proven.

Media House will combine two systems:

1. **Internal Revenue Engine:** discovers appropriate business prospects, researches their public business context, drafts personalized demos and outreach, manages replies and meetings, creates proposals, tracks payments, and manages customer delivery, renewals and support.
2. **Client Business Systems:** configurable applications and workflow automations for a client’s actual operational needs—e.g., appointment handling, enquiry-to-quotation, restaurant order flow, stock alerts, attendance or reporting. Each project must be scoped from a client-approved requirements sheet.

The differentiator is not “software for every business.” It is a reliable process: understand a specific problem, show a relevant demo, scope a solution, obtain approval and payment, deliver a tested workflow, and maintain it.

## 2. Mission and commercial goals

### Mission

Help small and mid-sized businesses remove repetitive operational work through well-scoped software, integrations and AI-assisted automation.

### First-stage goal

Acquire and deliver one paid pilot with:
- a documented business problem;
- a customer-approved scope and acceptance criteria;
- a clear contract, price and payment milestones;
- a functioning and tested solution;
- documented hosting, data access, support and exit terms;
- measured delivery cost, support burden and contribution margin.

### Long-term goal

Work toward 100+ active paying clients by standardizing repeatable workflows, adding industry modules, and building acquisition and delivery automation. “100 clients” is a target to plan toward, not a guaranteed outcome or the first release criterion.

## 3. Initial market focus

- **Geography:** Maharashtra first (initial outreach may start locally); expand across India after learning, then international markets after country-specific legal and commercial review.
- **Languages:** Hindi, Marathi and English; code-switching should be supported when the underlying model/provider performs adequately.
- **Likely early buyer:** owner/operator of a 5–50-person service business or small operations-led company, or a smaller business with a specific costly repetitive workflow. This is a hypothesis to test, not a proven ideal customer profile.
- **Preferred early workflow:** lead-to-revenue operations—centralize enquiries, qualify interest, follow up, schedule meetings, generate proposals and track delivery.
- **Potential vertical modules later:** retail/trading, restaurants/cafés, clinics, education/training, local services, agencies and distributors.

## 4. Why the prior laptop-stand direction is paused

The earlier Work-from-Anywhere India initiative researched a physical-product business and built a local validation page. It is separate from the current core strategy and is paused. Preserve its files for reference; do not delete or continue developing it unless the owner explicitly requests that. The current business is service/software delivery, not dropshipping.

## 5. What Media House can sell

Offer productized services before building a broad SaaS platform:

1. Workflow audit and automation blueprint.
2. Lead/enquiry management and follow-up automation.
3. Appointment and booking workflows.
4. Customer-facing websites, forms and portals.
5. Client-specific dashboard and reporting.
6. Integrations with existing software.
7. Voice/chat assistants for suitable, approved workflows.
8. Monthly hosting/maintenance/support (subject to chosen deployment model).
9. Custom modules only when scope, price and ownership are explicit.

### Important limit

Do not promise a full POS, accounting system, clinic management system, ERP or regulated healthcare system to an early customer without evaluating all functions, integrations, security, reliability, legal and support requirements. Prefer to connect to the client’s existing system rather than replace critical software in the first release.

## 6. Product principle: client-specific solution, reusable platform

Every client receives a tailored solution based on an approved requirements sheet, but the engineering system should reuse common foundations where possible:

- tenant/workspace management;
- user authentication and role-based access;
- lead/CRM records;
- tasks and workflow state machines;
- notifications and integrations;
- documents, proposals and approvals;
- invoices/payment status records;
- project delivery and support tickets;
- audit logs, monitoring and reporting.

Industry modules should configure or extend this core without leaking data between clients.

## 7. Customer data control is a product requirement

The founder’s preference is for the client to retain control over business data. This needs a concrete hosting and data-flow choice—not a vague promise that “we do not take data.” Each proposal must specify one of:

- **Client-hosted:** app and database run in the client’s infrastructure/account.
- **Client-local/on-premises:** software runs on an approved local machine/server; backups and access are defined.
- **Tenant-isolated managed hosting:** Media House operates the service with strict isolation, contracts, encryption, access logs, backups and export/deletion support.
- **Hybrid:** sensitive records stay with the client while approved minimum metadata or workflow events are processed by a hosted component.

State where every data category lives, who can access it, which providers process it, retention, backup, export, deletion, and what happens after contract termination. Do not claim “data never leaves your environment” unless the architecture proves that for all app data, logs, backups and vendor calls.

## 8. Automated pipeline

`Discover → Verify → Qualify → Research → Prepare demo/audit → Human-approved outreach → Reply handling → Qualification → Meeting → Requirements capture → Human approval → Proposal/contract → Payment milestone → Project generation → Build → Automated tests → Human review → Client acceptance → Deployment → Training/handover → Monthly support/renewal`.

Each stage must have:
- defined entry and exit criteria;
- owner and timestamp;
- evidence/source;
- allowed next states;
- failure/retry route;
- human approval when consequential;
- auditable activity history.

## 9. AI roles

- **Research agent:** summarize permitted public business information and cite the source.
- **Lead scoring agent:** explain fit and confidence; never invent a need.
- **Personalization agent:** draft relevant outreach for human review.
- **Sales assistant:** answer approved product questions, qualify and schedule; hand off uncertain or sensitive questions.
- **Meeting analyst:** summarize authorized meeting notes and extract requirements, risks and unresolved questions.
- **Solution architect:** draft technical approaches from approved requirements.
- **Coding agent:** implement scoped tasks in a branch, add tests and documentation, never deploy to production independently.
- **QA agent:** run tests and compare delivered behavior to acceptance criteria.
- **Support agent:** triage issues and suggest fixes; human review for high-impact operations.
- **Finance/admin agent:** prepare invoice/progress summaries; never mark a payment confirmed without payment-provider/bank evidence.

## 10. Voice agent requirements

The intended voice experience should sound natural and support Hindi, Marathi and English, including code-mixed conversations, but must not deceive callers into believing it is a human. It should identify itself as an AI assistant at the start or when required, handle language preference, give concise answers, and transfer to a human on request or when uncertain.

For a clinic appointment workflow, collect only the minimum needed for scheduling (e.g., name, callback number, requested doctor/service, preferred date/time). Do not ask for detailed diagnosis by default, provide medical advice, or triage beyond a clinic-approved script. Verify available slots through the clinic’s source of truth. Do not fabricate availability. Record calls only after appropriate notice and lawful basis/consent.

## 11. Security and release principles

- Multi-tenant isolation must be tested, not merely documented.
- MFA for administrators; least-privilege access.
- Secrets server-side only; redact them from logs.
- Audit logs for access, changes, exports, outbound communications, payments and deployments.
- Encryption in transit and at rest according to hosting/provider capabilities.
- Data minimization, retention limits, backup/restore drills and incident response.
- Human approval for outbound campaigns, live calling, payments/refunds, production deployments and access to sensitive client data.
- Staging-first deployment with rollback.
- No production client information in dev/test; use synthetic or explicitly approved test data.

## 12. Commercial model

Potential revenue streams:
- paid discovery/workflow audit;
- one-time implementation/setup;
- monthly software/hosting/support/maintenance;
- usage-based voice/AI/messaging charges passed through transparently;
- custom integration and change requests;
- premium SLA only when we can actually meet it.

The exact price must be calculated from scope, delivery hours, provider costs, support burden, risk and customer willingness to pay. Previous figures such as ₹15,000–₹40,000 setup and ₹3,000–₹10,000/month are test hypotheses only—not market-verified prices.

## 13. Success metrics

Commercial: qualified leads, reply rate, meetings held, proposal rate, close rate, average contract value, time-to-close, cash collected, recurring revenue, churn, customer acquisition cost, contribution margin.

Delivery: scoped vs. actual hours, on-time delivery, first-pass acceptance, escaped defects, uptime (where monitored), incident count, support hours per account, automation execution success.

Compliance: opt-outs honored, unauthorized message/call count (target zero), consent records where required, data access reviews, deletion/export requests fulfilled, security incidents.

## 14. Current verified state (from user-provided handoff)

- A separate local product-validation project exists and is paused.
- User reports a separate OmniRush working directory.
- The current environment/repositories have not been independently inspected by this document author.
- No paying customer, approved live integration, production deployment, verified outbound channel, or recurring revenue is established here.
- Treat all proposed modules as planned unless the repository audit proves otherwise.

## 15. Definition of done for the first release

A first internal release is done only when:
1. a human can create/import a prospect with a traceable source;
2. the system can document fit, confidence and next action;
3. an outreach draft can be reviewed and approved without automatically sending;
4. replies/meetings/tasks can be tracked;
5. a client meeting can produce a structured requirements sheet;
6. approved requirements can generate a scoped delivery plan and acceptance tests;
7. project status, invoice/payment status and support tasks can be tracked;
8. permissions and tenant isolation are tested if client data is stored;
9. setup, test and recovery instructions exist;
10. a user can see what is manual, automated, unverified and blocked.
