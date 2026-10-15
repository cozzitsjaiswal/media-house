# Media House Baseline Audit

**Audit date:** 2026-10-13  
**Workspace:** project root  
**Audit scope:** supplied documentation, current workspace, installed local tooling, and the first safe implementation slice

## Executive result

Before implementation, this workspace contained the supplied documentation pack
and OmniRush attachment metadata, but no application repository or runnable
Media House software. The reported external OmniRush workspace and separate
paused project were not present in this environment. No Git
repository, package manifest, database migration, deployment configuration,
provider integration, test suite, or environment-variable file was found at
the workspace root.

The first implementation slice is now a deliberately local-only Python 3.12
and SQLite prospect workflow. It validates and imports manually prepared CSV
leads, preserves source evidence, deduplicates records, calculates an
explainable initial fit heuristic, creates human-reviewable outreach drafts,
and records a human approval without sending anything externally.

This is not the complete Media House product or the complete first-release
definition in `PROJECT.md`. It is a safe Phase 2 foundation that can be tried
without paid services, credentials, network access, or client data.

## Documentation read and governing decisions

The archive contains 23 files under
`media_house_complete_documentation/media_house_starter/`, including the
project README, project definition, changelog, architecture, product
requirements, implementation plan, data model, delivery handoff, security and
privacy, sales/compliance, business model, operating procedures, voice-agent
specification, decisions, templates, and JSON schema.

The documentation establishes these constraints that affected the slice:

- Start with one narrow, revenue-relevant workflow rather than a broad
  multi-industry platform.
- Prefer a modular monolith and existing tools; do not introduce services or
  provider integrations before a real need is verified.
- Keep facts, hypotheses, unknowns, and client-approved requirements separate.
- Preserve source URL, observation time, researcher, verification status, and
  fit rationale for prospect research.
- Require human approval before outbound communication and never fabricate
  contact, permission, need, or payment evidence.
- Do not connect calling, WhatsApp, email sending, payments, production data,
  or public deployment without separate account, compliance, cost, and owner
  approval.

No direct contradiction between the supplied documents was found. The
candidate technology stack is explicitly not committed; because no existing
application stack was present, this milestone uses the lowest-dependency local
choice (Python standard library plus SQLite) rather than selecting FastAPI,
React, PostgreSQL, or a provider by preference.

## Verified environment before implementation

Commands run from the project root:

| Check | Verified result |
|---|---|
| `pwd` | Project root |
| `git status --short --branch` | Not a Git repository; no branch or status available |
| Workspace tree | `.opencode/` and `media_house_complete_documentation/` only before implementation |
| Reported external OmniRush workspace | Missing |
| Reported paused project locations | Missing in the available user/root paths |
| Python | 3.12.3 |
| Node/npm | Node 22.23.3, npm 10.9.9; no Node project manifest found |
| Git | 2.39.5 |
| `uv` | 0.13.0 |
| PostgreSQL client | Not installed |
| Docker | Not installed |
| Common Python packages checked | FastAPI, Uvicorn, Pydantic, pytest, HTTPX, SQLAlchemy, and jsonschema were not installed |
| Secrets/config inspection | No secret values were read or printed; no `.env` or deployment config was found |

The supplied ZIP was listed and extracted for tool access. It contains 23
documentation/template/schema files and the extracted files were byte
identical to the archive. The separate paused Work-from-Anywhere project was
not found and was not modified.

## Implementation inventory after the kickoff slice

| Area | Current state |
|---|---|
| Local package | `media_house/` Python package using only the standard library |
| Persistence | Local SQLite schema in `media_house/store.py`; no hosted database |
| Import | Atomic CSV validation and upsert using the supplied lead-pipeline columns |
| Identity | Website-domain deduplication, with normalized company-plus-region fallback |
| Evidence | Source URL, UTC observed time, researcher, verification, rationale, notes |
| Qualification | Deterministic score with stored reasons, confidence, and non-market-validated disclaimer |
| Outreach | Draft records remain `PENDING_APPROVAL`; approval requires a non-machine actor and permitted channel status |
| External action | None; approval records `send_performed: false` and invokes no provider |
| CLI | `import-csv`, `list`, `draft`, `approve-draft`, and `export-csv` |
| Tests | Standard-library `unittest` coverage for validation, atomicity, dedupe, evidence, scoring, opt-out, approval, and export |
| Documentation | Root `README.md` explains the safe boundary and local CLI; supplied pack is unchanged |

## Checks actually run

From the workspace root:

```text
python3 -m unittest discover -s tests -v
Ran 14 tests in 0.061s — OK

python3 -m compileall -q media_house tests
Passed

python3 -m media_house.cli --help
Passed; showed the five local commands and the no-network description
```

A local CLI smoke flow was also executed with synthetic data for import, list,
draft, human approval, and CSV export. It produced no provider call and no
external message. The exact test suite is the authoritative regression check;
there is no lint, type-check, integration, database-service, deployment, or
browser check configured yet.

## Security and privacy status

Safe for synthetic/local test data only. The slice has no authentication,
tenant membership, role enforcement, encryption/key management, remote
storage, webhook verification, retention job, export/delete authorization, or
production audit identity. The local approval actor is an explicit attestation,
not an authenticated user identity. The database must not receive real client
records until those controls and the hosting/data-flow decision are designed
and tested.

The implementation intentionally has no network client, scraper, sender,
telephony adapter, payment adapter, AI provider call, secret lookup, or
deployment command. No paid spend or external side effect occurred.

## Remaining gaps against the supplied first-release definition

The following are not implemented and must not be inferred from the local
milestone:

1. Authentication, tenant isolation, role permissions, and cross-tenant
   negative tests.
2. A browser/API interface and durable authenticated audit identity.
3. Meeting scheduling, meeting notes, requirements-document generation and
   validation against the supplied requirements schema.
4. Proposal/SOW, contract, invoice, verified payment-state, and approval
   records.
5. Project/task/test-case generation and coding-agent handoff gates.
6. Client portal, support, renewals, analytics, backups, restore drills, and
   offboarding controls.
7. Provider adapters, live integrations, source verification services, or any
   outbound communication.
8. Maharashtra customer interviews, selected ICP, paid pilot evidence, vendor
   quotations, and validated pricing/unit economics.

## Recommended next gate

Use the local workflow with synthetic/publicly permitted sample rows and choose
one initial ICP and measurable lead-to-revenue pain from customer discovery.
Then approve one of these next implementation slices: (a) authenticated
meeting-to-requirements handoff with schema validation, or (b) an authenticated
CRM/API layer around the current local store. Do not enable live outreach or
accept real client data before the owner approves the channel, hosting/data
flow, identity model, retention, and security test plan.

## Subsequent lead-discovery milestone update — 2026-10-13

After this baseline was read, the workspace gained a separate, standard-library
Overpass adapter and an explicit review/import workflow. It now supports:

- bounded, configurable bbox/category queries with a 1–200 result cap;
- query planning without network or SQLite access;
- opt-in network access via `discover run --confirm-network` only;
- typed handling for rate limits, HTTP/network/timeout failures, malformed
  responses, service remarks, response-size limits, and cache failures;
- atomic success-only cache/result writes with offline cache reuse;
- synthetic parsing of OSM object IDs, names, categories, coordinates, public
  website/contact tags, retrieval time, source attribution, and ODbL metadata;
- preview-only saved-result review and explicit `--confirm-import --researcher`
  import through the existing `MediaHouseStore.import_csv` transaction;
- stable OSM-object deduplication, retained source evidence, and protection
  against a discovery row downgrading an existing verified or qualified lead;
- no automatic website fetches, scraping, retries, pagination, messaging,
  calls, drafts, approvals, scheduled jobs, paid services, or deployment.

The offline suite now runs 60 tests. Synthetic fixtures and injected HTTP test
boundaries were exercised. A separately operator-confirmed live smoke request
was then made on 2026-10-13 to `https://overpass-api.de/api/interpreter` for a
narrow Nagpur bbox, `amenity=restaurant`, and a five-record cap. It returned a
successful empty response (`source_returned=0`, `discovered=0`) and produced
the local review artifact `.media_house/nagpur-smoke-review.json` plus one
successful cache entry. No SQLite import or outreach followed. This verifies
one live empty-response path, but not broad endpoint coverage, populated
results, or business-data quality. Further live runs must still be reviewed
and explicitly confirmed by an operator before any source request or database
import.
