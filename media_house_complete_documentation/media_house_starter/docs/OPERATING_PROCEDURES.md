# Operating Procedures

## 1. Daily sales workflow

1. Review tasks and warm/inbound leads first.
2. Research a small batch of companies from permitted sources.
3. Save source URLs and date; verify company and contact channel.
4. Record one evidenced business hypothesis and confidence.
5. Draft a relevant message or small demo; do not claim unverified facts.
6. Check consent/channel policy and suppression list.
7. Obtain approval before sending in MVP.
8. Record actual send/provider status, reply and next step.
9. Stop when declined or opted out.
10. Review outcomes and objections weekly.

## 2. Meeting workflow

Before: inspect prospect sources, build agenda and clarify who attends.  
During: ask about current process, failure cases, impact, constraints, decision authority, budget range, timeline, integrations and data control.  
After: label facts/assumptions/questions, produce requirements draft, review with client and seek written approval before scope/pricing is final.

## 3. Build workflow

1. Requirements approved and versioned.
2. SOW signed / payment gate met.
3. Delivery task list and tests created.
4. AI agent proposes plan; human approves material implementation.
5. Work in a branch or isolated environment.
6. Run lint, unit, integration, authorization, tenant-isolation, security and acceptance tests as applicable.
7. Deploy to staging with synthetic/minimized data.
8. Human QA; demonstrate to client.
9. Fix defects; client acceptance documented.
10. Human approves production release; backup and rollback ready.
11. Handover and support plan completed.

## 4. Payment/invoice workflow

- Draft invoice/quote from approved commercial terms.
- Payment system/bank receipt is the source of truth.
- Webhooks must be signature-verified and idempotent.
- A screenshot or LLM-generated summary is not payment confirmation.
- Record invoice due date, status, actual settled amount, transaction/provider ID, currency and reconciliation state.
- Refunds require documented human authorization and verified provider operation.

## 5. Support severity

- **P0 / critical:** data exposure, cross-tenant leak, security compromise, dangerous automation. Disable affected feature immediately and notify owner.
- **P1 / high:** booking/order/payment workflow incorrectly changes state or major production outage. Use manual fallback, investigate, communicate.
- **P2 / normal:** feature defect with workaround. Create ticket and target response based on contract.
- **P3 / request:** minor improvement/change request; estimate and price separately if out of scope.

Do not advertise an SLA until staffed and operationally tested.

## 6. Weekly review

Review:
- qualified leads and source quality;
- replies/meetings/proposals/wins/losses;
- reason for each loss;
- collected cash, invoices due and variable spend;
- implementation time and escaped defects;
- support hours and client satisfaction;
- channel opt-outs/complaints;
- AI/provider usage cost and failed jobs;
- one process improvement to ship next week.

## 7. KPI definitions

- **Qualified lead:** prospect meeting recorded ICP criteria with a plausible problem and permissible contact path.
- **Reply rate:** unique contacted prospects with a reply ÷ unique delivered contacts, with channel and time window noted.
- **Meeting show rate:** meetings held ÷ meetings booked that were due in the period.
- **Proposal win rate:** accepted proposals ÷ proposals with a resolved outcome.
- **Contribution margin:** revenue less directly attributable variable costs, with owner labour costed.
- **MRR:** recurring subscription value normalized monthly, excluding setup fees and uncollected/one-off revenue.
- **Churn:** lost recurring customers (define logo vs revenue churn separately) relative to opening cohort.
- **Automation success rate:** successful validated workflow runs ÷ eligible runs; retries and manual overrides logged.
- **False action rate:** incorrect booking/message/payment/status actions; target zero for financially/medically consequential action classes.
