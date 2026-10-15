# Media House — AI Business Systems & Automation

**Documentation pack version:** 1.0  
**Prepared:** 13 October 2026  
**Initial operating region:** Maharashtra, India  
**Languages:** Hindi, Marathi, English  
**Capital constraint:** ₹0 available upfront; paid tools and services require explicit approval.

## Start here

Media House is intended to become an AI-assisted business automation company. It will identify a specific business problem, prepare a relevant demonstration, sell an approved implementation, deliver a tested system, and retain clients through monthly support/automation services. It is not intended to start as a generic e-commerce store or as a giant all-industries app.

The first objective is **one paid pilot with repeatable scope and positive delivery economics**. The long-term target is a reusable platform serving 100+ active customers, but that target is not yet evidence of demand or a forecast.

## Documentation map

1. [`PROJECT.md`](PROJECT.md) — authoritative vision, boundaries and current status.
2. [`docs/MARKET_AND_COMPETITORS.md`](docs/MARKET_AND_COMPETITORS.md) — competitor landscape, research hypotheses and positioning.
3. [`docs/PRODUCT_REQUIREMENTS.md`](docs/PRODUCT_REQUIREMENTS.md) — product scope and acceptance criteria.
4. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — internal revenue engine and client-systems architecture.
5. [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) — phases and build order.
6. [`docs/SALES_AND_COMPLIANCE.md`](docs/SALES_AND_COMPLIANCE.md) — lead research, outbound policy and messaging guardrails.
7. [`docs/VOICE_AGENT_SPEC.md`](docs/VOICE_AGENT_SPEC.md) — Hindi/Marathi/English voice agent, appointment workflows and safety.
8. [`docs/CLIENT_DELIVERY_SPEC.md`](docs/CLIENT_DELIVERY_SPEC.md) — meeting-to-requirements-to-build-agent handoff.
9. [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) — proposed CRM and multi-tenant schema.
10. [`docs/SECURITY_AND_PRIVACY.md`](docs/SECURITY_AND_PRIVACY.md) — data control, security, privacy and healthcare cautions.
11. [`docs/BUSINESS_MODEL.md`](docs/BUSINESS_MODEL.md) — offer, pricing hypotheses and unit economics.
12. [`docs/OPERATING_PROCEDURES.md`](docs/OPERATING_PROCEDURES.md) — standard workflow, release gates and KPI definitions.
13. [`docs/DECISIONS.md`](docs/DECISIONS.md) — decisions made and open questions.
14. [`docs/RESEARCH_SOURCES.md`](docs/RESEARCH_SOURCES.md) — dated official and primary sources.
15. [`docs/COMPLETE_BUILD_PROMPT.md`](docs/COMPLETE_BUILD_PROMPT.md) — master prompt for GPT-6 Astra/OmniRush.
16. [`templates/CLIENT_REQUIREMENTS_TEMPLATE.md`](templates/CLIENT_REQUIREMENTS_TEMPLATE.md) — meeting output to be completed per client.
17. [`schemas/client_requirements.schema.json`](schemas/client_requirements.schema.json) — machine-readable requirements contract.
18. [`templates/LEAD_PIPELINE.csv`](templates/LEAD_PIPELINE.csv) — header-only CRM import template.

## Important status note

This pack is documentation and a proposed architecture. It does **not** mean that a CRM, voice agent, integrations, website, billing system, deployment, lead source, supplier, customer or revenue stream already exists. An AI coding environment must first inspect the actual codebase and report the verified state before making changes.

The previously created product-validation project is a separate paused experiment. Preserve it; do not delete it, mix it into the core Media House platform, or keep developing it unless explicitly requested.

## How to use this pack

1. Extract this folder into the intended Media House repository or workspace.
2. Review existing files before copying or overwriting anything.
3. Give `docs/COMPLETE_BUILD_PROMPT.md` to GPT-6 Astra in your coding environment.
4. Require it to inspect the real project first, create a baseline inventory, and work in small tested stages.
5. Do not connect live calling, WhatsApp, payment, email sending, or client data until the related provider accounts, permissions, compliance checks and cost approvals are in place.

## Suggested project root layout

```text
media-house/
├── README.md
├── PROJECT.md
├── docs/
├── schemas/
├── templates/
├── apps/                  # only after actual code structure is agreed
├── services/              # only after actual code structure is agreed
├── infra/                 # only after deployment design is approved
├── tests/
├── scripts/
├── .env.example           # variable names only, never secrets
├── .gitignore
└── CHANGELOG.md
```

## Operating rules

- Preserve existing source code and user data.
- Never print, commit or place credentials in the frontend.
- No paid spend, account connection, public deployment, DNS changes, external messages, calls or real client data without human approval.
- Do not make unauthorized bulk calls, messages, scraping or data enrichment requests.
- Use natural Hindi, Marathi and English, but do not impersonate a person or hide that a voice agent is AI when asked or where disclosure is required.
- Prefer integration to replacement where a client already owns a reliable POS, billing, clinic or accounting system.
- Every client feature needs scope, acceptance criteria, tests, security review and an owner.
- First prove a single client workflow; only then generalize it into a reusable module.
