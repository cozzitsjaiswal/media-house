# Media House completion checklist

**Evidence date:** 2026-10-23

**Local branch:** `fix/release-readiness-20261023`

**Starting/current base:** `caa15c0f7d301c89102907de9c30aad7fb733a4c` (`origin/main`)
**Scope:** synthetic/local verification only; no customer data, outreach, deployment, merge, push, or PR closure.

## Feature status

Each item has exactly one status.

| Feature | Status | Evidence / limitation |
|---|---|---|
| Reproducible Python/uv setup | IMPLEMENTED AND VERIFIED | `.python-version` is `3.11.13`; `uv.lock` requires `==3.11.*`; locked sync passed. |
| Finder fail-closed novelty classification | IMPLEMENTED AND VERIFIED | `media_house/finder/core.py`; empty/unresolved snapshots remain ambiguous unless the validated novelty contract is present. |
| Finder descriptor-pinned Linux storage | IMPLEMENTED AND VERIFIED | `media_house/finder/linux_storage.py`; `openat2`, no-follow descriptors, capability checks, anonymous staging and `linkat` are covered by injected boundary tests. |
| Finder safe database installation and failure receipts | IMPLEMENTED AND VERIFIED | Pre/post-install failure, link, fsync, close, cleanup and namespace-substitution tests pass. |
| Finder no-accidental-database-creation guard | IMPLEMENTED AND VERIFIED | Concurrent disappearance and target-race tests pass; SQLite uses `:memory:` image operations only. |
| Finder CLI isolation and offline boundary | IMPLEMENTED AND VERIFIED | Finder requires an explicit synthetic database; tests assert no network, provider, outreach, or regular store access. |
| Evidence CSV import and atomic validation | IMPLEMENTED AND VERIFIED | `MediaHouseStore.import_csv`; invalid batches do not partially write. |
| Deterministic fit scoring | IMPLEMENTED AND VERIFIED | Stored score, reasons and disclaimer are deterministic and bounded. |
| Human-reviewed outreach draft | IMPLEMENTED AND VERIFIED | Drafts remain `PENDING_APPROVAL`; approval records `send_performed=false` and makes no provider call. |
| Monotonic lead-stage protection | IMPLEMENTED AND VERIFIED | Stale imports and draft approval cannot downgrade later or protected statuses, and CSV imports cannot jump over later workflow gates; regression tests cover `discovery_complete`, `proposal_sent`, `won`, `blocked`, and `opted_out`. |
| Manual completed-meeting tracking | IMPLEMENTED AND VERIFIED | Human notes and recording policy are stored; future timestamps are rejected before persistence. |
| Requirements capture and versioning | IMPLEMENTED AND VERIFIED | Documents are linked to a meeting/prospect, validated, hashed and versioned. |
| Requirements approval and supersession | IMPLEMENTED AND VERIFIED | Human approval requires `READY_FOR_REVIEW`, explicit requirement labels, matching hash and audit metadata. |
| Requirements export | IMPLEMENTED AND VERIFIED | File/stdout export, exporter identity, audit-before-delivery ordering and active-DB/sidecar protection are covered. |
| Local package/order/payment intents | PARTIALLY IMPLEMENTED | Package and pending order/payment-request records exist; no provider call, payment verification or reconciliation exists. |
| Proposal/SOW generation and approval | NOT IMPLEMENTED | No proposal table, command, export, or approved-requirements snapshot linkage exists. |
| Contract, invoice and verified payment lifecycle | NOT IMPLEMENTED | No commercial document or verified payment state exists. |
| Authenticated users, roles and tenant isolation | NOT IMPLEMENTED | Human actors are local attestations, not authenticated identities; design and implementation gates are recorded in `docs/SECURITY_ARCHITECTURE.md` and `docs/AUTH_TENANCY_IMPLEMENTATION_PLAN.md`. |
| API, frontend and client portal | NOT IMPLEMENTED | The supported interface is the local CLI and Python store. |
| Project delivery, support and renewals | NOT IMPLEMENTED | No delivery/task/support lifecycle exists. |
| Backup, restore, retention automation and monitoring | PARTIALLY IMPLEMENTED | Requirements-history deletion guards and local audit events exist; no backup/restore drill, scheduler, health checks or alerting exists. |
| Docker image build/run/persistence verification | IMPLEMENTED BUT NOT VERIFIED | `Dockerfile` is present and pins Python/uv, but Docker is unavailable in this environment. |
| Remote CI workflow | IMPLEMENTED AND VERIFIED | Current `main` SHA `caa15c0` had a successful `Python 3.11 checks` run on 2026-10-19; this unpushed branch has no remote CI receipt. |

## Checks and receipts

| Command / check | Exit status | Result |
|---|---:|---|
| `uv lock --check` | 0 | Passed. |
| `uv sync --locked` | 0 | Passed; one locked editable package resolved/checked. |
| `uv run python -m unittest discover -s tests -v` | 0 | **226 tests passed** in 24.797s. |
| `uv run python -m compileall -q media_house tests` | 0 | Passed. |
| `uv run media-house --db <temporary-db> package-list` | 0 | Passed twice against a fresh synthetic database; both reads returned two packages and the database remained non-empty. |
| Synthetic CLI workflow: import → score/list → draft → approval → meeting → requirements capture → approval → export | 0 | Passed with synthetic records; exported status was `APPROVED`, fit score was `95`, and `send_performed` was `false`. |
| `uv run python -m pytest -q` | 1 | **NOT VERIFIED / blocked by missing optional tool**: `.venv/bin/python: No module named pytest`. The repository CI and documented checks use `unittest`; no pytest dependency was added. |
| Docker availability | — | **NOT VERIFIED**: `docker` is not installed in the environment, so build/run/mounted persistence could not be exercised. |

## Repository and remote state

- Starting checkout was `fix/requirements-export-symlink` at `79c62851`; it was not edited in place.
- Work was moved to the isolated branch `fix/release-readiness-20261023` from live `origin/main` at `caa15c0`.
- The PR #4 symlink-resolution guard remains present in `media_house/store.py`.
- The reported local commit `5e78cc2` exists in the local checkpoint history, but GitHub returned 422 for that SHA; it is not present as a reachable GitHub commit. Its runtime pinning content is present in the current tree.
- PR #3 remains open and untouched as requested.
- Pre-existing untracked `.claude/`, `FETCH_HEAD`, and `opencode.jsonc` were preserved.

## Release blockers

1. **High:** no authenticated API, tenant isolation, or durable production audit identity; do not use real customer data yet. The required design and staged implementation plan are documented, but no control is implemented by those documents alone.
2. **High:** proposal/SOW, contract, invoice, verified payment, delivery and support lifecycles are absent.
3. **Medium:** Docker build/run and mounted-database persistence remain unverified until Docker is available.
4. **Medium:** the unpushed feature branch has no remote CI receipt; run CI after an approved push/PR.
5. **Low:** the optional `pytest` self-check is unavailable; the project’s locked standard-library `unittest` suite is the verified test path.
