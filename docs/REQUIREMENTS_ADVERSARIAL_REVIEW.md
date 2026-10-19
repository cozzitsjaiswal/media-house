# Requirements capture: verification history and final fixes

Date: 2026-10-19. Scope: local working tree on `feature/requirements-capture`.

**Result: the approval/lifecycle fixes, both original residual export findings,
and export symlink-loop handling are addressed locally. The README now shows
explicit review-ready approval using the returned document ID. Export uses
audit-before-delivery ordering, NOT a joint
SQLite/filesystem transaction; the documented delivery/crash risks remain.
This is not an unconditional approval for publishing or production use.**
No commit, push, PR, merge or deployment was
performed. No package was installed and no dependency or runtime pin was changed.

Verification results below are separated by working-tree snapshot. The original
**194/79** results preceded bounded-input hardening; the later **201/86** results
include seven input regressions and the protected Docker-context exclusions.
Neither historical total includes the new symlink-loop CLI regression. Current
final-fix verification is recorded separately, not substituted into those earlier
receipts.

## Revision and preservation

- HEAD: `dbe5dc4a80d39a04d02cc81a4a02da63cf25b934` (unchanged).
- Retained `origin/main`: `ab5ec71feda2be64b5385ead14b2c47f1d8a122f`.
- Branch is ahead of that ref by two commits. No fetch was needed or performed;
  this comparison does not claim that the remote server has not moved.
- Committed diff against that base: six files, 1,458 insertions, one deletion.
- Existing uncommitted requirements/store/test fixes were preserved and extended.
  No files were staged.
- Finder branch refs remained unchanged: local
  `4522bf0add205cd9ccb58b4dd49bf2692b77c519`, retained remote
  `a6c8f86df65f5b67bd30d68b55f27e303c961dca`.
- `.claude/`, `opencode.jsonc`, `.venv-root-python-backup` and unrelated user data
  were not read or edited. All mutable test data was synthetic and disposable.
- The Python 3.11.13 environment was reused. Before/after checks confirmed
  unchanged `.python-version`, `pyproject.toml`, `uv.lock`, `.venv/pyvenv.cfg`
  and installed distribution names/versions. Locked sync reported only a check,
  not installation/removal.

## Ranked confirmed findings

### High: multiple unlinked approved baselines (fixed)

Location: `media_house/store.py`, `approve_requirements`.
Two review drafts captured before either was approved could both be approved
without a superseding link. Approval now reads, validates and changes state
inside `BEGIN IMMEDIATE`, rechecks the current baseline, and requires an explicit
link to an older baseline in the same meeting/prospect. Unlinked, stale sibling,
backwards and repeated approvals fail without changing the current baseline.
Real two-connection races verify one winner and one clean rejection.

### High: show/list bypassed integrity validation (fixed)

Location: `media_house/store.py`, `get_requirements_document` and
`_validate_requirements_record_integrity` (around lines 1254 and 1277).
Show/list previously returned tampered approval data even when export rejected
it. Reads now check linkage, document version, embedded lifecycle state, content
hash, embedded approval metadata and DB approval columns. Corruption is rejected
across read/list/export/approve and CLI paths for every lifecycle state.

### Medium: inconsistent standalone lifecycle validation (fixed)

Location: `media_house/requirements.py`, `validate_document` (around line 252).
Previously the default validator accepted empty review-ready documents and
approved/superseded documents with arbitrary well-shaped approval digests.
Lifecycle invariants are now intrinsic: review/approved states require content;
approved/superseded states require matching approval hashes and explicit
`APPROVED`/`OUT_OF_SCOPE` requirement labels. Capture states forbid non-null
approval metadata. Drafts may still contain unresolved requirements.

### Medium: invalid metadata normalized into valid capture values (fixed)

Location: `media_house/store.py`, `capture_requirements` (around line 1127).
Supplied null/bool/noninteger linkage/version values could be overwritten by
allocated metadata, and a null status could be masked by a CLI flag. Supplied
values are now checked before allocation. Boolean/noninteger superseding IDs
are rejected. Omitted status/version continue to default/allocate normally.

### Medium: expected input errors leaked exceptions (fixed)

Locations: `media_house/cli.py:29,439`; `media_house/requirements.py:39,59`;
`media_house/store.py`, stored JSON parsing.
Deep JSON raised `RecursionError`; huge identifiers raised SQLite binding
`OverflowError`; malformed Unicode could escape hashing. These now produce
controlled validation/CLI errors. RFC3339-style approval timestamps require
an explicit timezone and reject compact dates, space separators, whitespace,
invalid offsets and conversion overflow. Existing enum/null fixes were retested
through both the validator and CLI. Real module subprocesses return exit 2
with empty stdout and no traceback for the tested expected input errors.

### Medium: commit failure left transactions open (fixed)

Location: `media_house/store.py:560`, `_transaction`.
Commit was outside the protected exception block. A denied SQLite COMMIT now
rolls back both baseline updates and all audit inserts. Fault-injection tests
also fail after each approval/superseding audit insertion, compare complete
before/after requirements/audit rows, and confirm the connection remains usable.

### Medium: audit failure overwrote an existing export (fixed; tradeoff explicit)

Location: `media_house/store.py`, `export_requirements`.
Reproduced before the closure fix: an injected audit-insert failure rolled back
DB rows but overwrote the existing synthetic destination. File export now fully
validates/serializes, stages in the destination directory, writes/flushes/fsyncs/
closes, commits its audit, and only then calls `os.replace`. Audit insertion and
COMMIT failures preserve existing bytes (or an absent destination), roll back
audit rows, and remove the owned temporary file. Staging write/short-write,
flush/fsync/close failures likewise occur before audit and preserve the target.

This closes the overwrite-on-audit-failure finding by choosing an explicit
ordering, **not** by making SQLite and file delivery jointly atomic. Committing
the audit first allows a committed event even when replacement later fails or
the process dies before delivery. Such an event is an audited export attempt,
not proof of delivery. Metadata records `delivery_order="audit_before_delivery"`,
`delivery_confirmed=false` and `destination_type="file"` or `"stream"`. These
fields remain unchanged even after success; callers inspect the return/exit
status and output instead of assuming the audit confirms delivery.

### Low: export audit used the creator identity (fixed; API/CLI change)

Location: `media_house/store.py`, `export_requirements`; `media_house/cli.py`,
`requirements-export` parser/dispatch.
Export now requires the keyword-only `actor` argument and required CLI `--actor`.
The supplied exporter is validated with the existing human-actor rules and
recorded as audit `actor`; the document creator is retained only as separate
`metadata.created_by`. Empty, unknown, machine, nonstring and invalid UTF-8 actors fail before
any staging/audit/output effects. Omission is an intentional API/CLI change:
Python reports the missing argument, while CLI parsing exits 2. Existing
positional CLI arguments, return document, JSON formatting and event action
`requirements_exported` are retained; all known callers/examples were updated.

### Medium: export destination symlink loops leaked a traceback (fixed)

Location: `media_house/store.py`, `export_requirements` destination guard.
On Python 3.11, `Path.resolve()` raises `RuntimeError` for a self-referential
symlink. The old guard allowed that exception to escape the CLI as exit 1 with
a traceback. A local catch now converts it to `ValidationError` with the prefix
`requirements export destination could not be resolved:` before staging or audit.
The existing active-database guard and audit-before-delivery order are retained.

The real subprocess regression
`test_cli_self_referential_symlink_is_exit_two_without_side_effects` uses a
synthetic on-disk DB and checks exit 2, empty stdout, no traceback/success summary,
no export audit, an unchanged complete SQLite dump and fixture bytes, the
unchanged self-referential symlink, and no leftover staging file. It skips only
when symlink creation raises `NotImplementedError` or an unsupported-operation
OS error (`ENOSYS`, `ENOTSUP`, `EOPNOTSUPP`); permission and other errors fail.
The focused Linux run passed with **0 skips**.

### Low: README attempted approval directly after DRAFT capture (fixed)

The initial-baseline example now requires a human-reviewed JSON document with
`status=READY_FOR_REVIEW` and every requirement explicitly `APPROVED` or
`OUT_OF_SCOPE`. It extracts the actual document ID from capture's JSON response,
approves that ID, and exports it with the required actual exporter actor.
The `&&` chain stops on failure; no label is automatically promoted.
An offline smoke check executed the README's actual Bash block with disposable
synthetic paths. It successfully approved/exported document **2** for meeting
**1**, with export actor `reviewer`, verifying that document and meeting IDs
need not match.

### Export boundary guarantees and residual risks

| Boundary | Destination/stream | Database and cleanup |
|---|---|---|
| Actor, integrity or serialization error | Untouched; no output parents created | No audit/temp file |
| Parent/temp creation, write, flush, fsync or close error | Existing target retained; absent target remains absent | No audit; owned temp removed if created |
| Audit insertion or COMMIT error | Existing target retained; stream untouched | DB rollback; owned temp removed |
| Replacement fails after COMMIT | Existing destination retained for ordinary failed replacement | Committed audit remains unconfirmed; owned temp removed; explicit delivery error |
| Stream write/flush fails after COMMIT | Partial output may already exist and cannot be retracted | Committed audit remains unconfirmed; caller stream is not closed |
| Successful replacement/write | Requested payload returned/delivered; CLI success only after delivery | Attributed audit remains a non-confirming attempt record |

Handled failures clean exactly the owned same-directory temporary path, never
the existing destination or unrelated temporary files. If removal itself is
denied, the error explicitly reports the owned path and whether auditing already
committed; automatic cleanup is impossible under such a filesystem failure.
The injected cleanup-denial test removes that fixture's file manually after
restoring normal filesystem behavior. Abrupt process termination can leave a
temporary file or an audited-but-undelivered document. A crash after replacement
can leave delivery whose success the caller did not observe. Parent directories
are not fsynced, so rename durability over power loss is not guaranteed. No
automatic retry, compensating audit deletion or crash-recovery daemon is added.

Missing parent directories are created and may remain empty after failure;
removing them broadly could harm concurrent/user data. Replacement uses the
temporary file's restrictive mode (normally 0600), not the old file's mode.
Destination symlinks are replaced rather than following their targets; resolved
aliases of the configured active SQLite DB are rejected. This guard is not
comprehensive protection against hostile directories, races or SQLite sidecars.
Stdout/already-open streams are caller-owned; shell redirection or opening a
stream may itself truncate a file before this API runs. Use a path destination
for the file-preservation guarantee.

## Canonical schema: MANUAL PARITY REVIEW, not standards validation

Source: `media_house_complete_documentation/media_house_starter/schemas/client_requirements.schema.json`.
The actual retained JSON file was parsed and read, not a hand-copied replacement.
The project and system Python had no `jsonschema` or `fastjsonschema`; the project
also had no `referencing` or `rfc3339_validator`. No `check-jsonschema`, `ajv`, or
`jsonschema` executable was available; Node could not resolve `ajv/dist/2020`
or `jsonschema`. No package installation, network fetch or lockfile change was
used to overcome this. **Draft 2020-12 schema/metaschema validation was NOT run.**

The new tests enumerate declarations to generate parity inputs; that enumerator
is explicitly not an independent JSON Schema validator. Manual findings:

| Section | Compared behavior |
|---|---|
| Root | All eight schema-required fields; object shape; retained optional sections. Capture prepares omitted version/status before validating stored documents. |
| Client | Required business name/industry; region, languages (`hi/mr/en`), decision-maker role; string/list types and null rejection. |
| Version/status | Positive Python integer version (not bool); exact five lifecycle enums; lifecycle rules are stronger than the schema. |
| Approval | Object/null; approver, date-time and scope hash. Runtime conditionally requires complete metadata and a matching lowercase SHA-256 digest; the schema itself does not require these properties or tie them to status. |
| Business problem | Required statement/current workflow; evidence/existing-system string arrays; absent versus explicit null. |
| Goals | Metric/method strings; baseline/target allow strings, finite numbers or null. |
| Requirements | All five required fields; `REQ-[0-9]{3,}` identifier format; exact source/status and priority enums; description, test, notes types. Runtime additionally rejects duplicates and blank required text. |
| Scope | Included/excluded required string arrays; dependencies array; change-control string. All included in hash coverage. |
| Data handling | Required hosting, categories, processors, retention and export/deletion; exact hosting enum; strict health-data boolean. |
| Integrations | Provider/purpose/fallback strings; strict access-confirmed boolean. |
| AI constraints | Allowed/human-approval string arrays; handoff/disclosure strings. |
| Commercial | Currency/support strings; numeric-or-null fees; payment string array. No invented currency/default value is inserted. |
| Acceptance | Required ID/steps/expected result; optional evidence; runtime also rejects duplicate IDs. |
| Open questions | Optional string array; explicit null rejected. |
| Extensions | Schema has no `additionalProperties: false`. Linkage and unknown JSON fields are retained and hashed, not silently discarded. |

The human template is broader than the JSON schema. It requests per-requirement
actor/trigger/expected behavior/exception behavior, users/permissions,
non-functional requirements, more detailed commercial terms and distinct scope,
hosting and SOW acknowledgements. The schema does not define/require all those
fields. Such extension content is preserved/hashed when supplied, but runtime
validation does **not** prove full template completeness or actual client signoff.
No schema expansion or new business-approval policy was invented in this pass.

### Lifecycle matrix verified

| State | Content requirement | Approval metadata |
|---|---|---|
| DRAFT | Empty requirement/acceptance arrays allowed | Absent/null only; DB approval columns null |
| NEEDS_CLARIFICATION | Empty arrays allowed | Absent/null only; DB approval columns null |
| READY_FOR_REVIEW | At least one requirement and acceptance criterion | Absent/null only; unresolved requirement labels remain unapproved |
| APPROVED | Nonempty; all labels APPROVED/OUT_OF_SCOPE | Complete timestamp/actor/hash; embedded and DB fields must agree |
| SUPERSEDED | Same content rules as approved; original hash preserved | Original complete approval retained; embedded and DB fields must agree |

Every declared material schema field is mutation-tested for hash inclusion,
including requirement status/priority/test, scope exclusions/dependencies/change
control, data handling, integrations, goals, commercial terms, AI constraints,
acceptance and risks. Tests also cover linkage IDs, version, and supplied
actor/trigger/expected/exception extensions. Only top-level lifecycle status
and approval metadata are excluded. Key ordering is immaterial.

## Commands and actual results by snapshot

All recorded commands ran at repository root. Offline/no-sync flags deliberately
reuse the existing environment. Test totals are method counts; matrix subcases
are not inflated into extra tests.

### Snapshot A: original approval/export closure — historical 194/79

This receipt predates the bounded JSON input fix, the seven input tests, and the
three protected-path Docker exclusions. These are historical results, not the
current full-suite totals.

| Command | Result |
|---|---|
| `uv run --offline --locked --no-sync python -m unittest discover -s tests -v` | PASS: 194 tests, no skips/expected failures |
| `uv run --offline --locked --no-sync python -m unittest tests.test_requirements_export tests.test_requirements tests.test_requirements_adversarial tests.test_revenue_workflow -v` | PASS: 79 tests (29 export, 10 existing requirements, 33 adversarial, 7 workflow) |
| `uv run --offline --locked --no-sync python -m unittest tests.test_requirements_export -v` | PASS: 29 focused export tests |
| `uv run --offline --locked --no-sync python -m unittest tests.test_requirements_adversarial -v` | PASS: 33 tests |
| `UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv sync --locked` | PASS: resolved/checked existing project; no installs/removals reported |
| `UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv lock --check` | PASS |
| `uv run --offline --locked --no-sync python -m compileall -q media_house tests` | PASS |
| `uv run --offline --locked --no-sync media-house --help` | PASS, exit 0 |
| `uv run --offline --locked --no-sync media-house requirements-export --help` | PASS; required `--actor` documented |
| `git diff --check` | PASS |

At snapshot A, five concurrency methods were also repeated ten times each in an inline
`unittest.TestSuite`: **50 additional runs passed**. This is a bounded concurrency
check, not exhaustive stress or crash testing. Exact runner:

```bash
uv run --offline --locked --no-sync python - <<'PY'
import unittest
from tests.test_requirements_adversarial import RequirementsStoreAdversarialTests
names = (
    'test_concurrent_version_allocation_is_unique_and_atomic',
    'test_concurrent_duplicate_supplied_version_is_rejected_cleanly',
    'test_concurrent_unlinked_baseline_approvals_have_one_winner',
    'test_concurrent_superseding_siblings_have_one_winner',
    'test_concurrent_double_approval_never_replaces_winner_metadata',
)
suite = unittest.TestSuite(RequirementsStoreAdversarialTests(name) for _ in range(10) for name in names)
result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(not result.wasSuccessful())
PY
```

### Snapshot B: bounded input and Docker-context hardening — historical 201/86

The later verification added `tests/test_requirements_input.py` (seven tests),
bounded reads of at most `MAX_REQUIREMENTS_INPUT_BYTES + 1` bytes, and exclusions
for `.claude/`, `opencode.jsonc`, and `.venv-root-python-backup/` in `.dockerignore`.
Protected contents were not inspected. This snapshot still predates the new
symlink-loop test and the final README correction.

| Command | Result at snapshot B |
|---|---|
| `uv run --offline --locked --no-sync python -m unittest discover -s tests -v` | PASS: 201 tests, no skips/expected failures |
| `uv run --offline --locked --no-sync python -m unittest tests.test_requirements_input tests.test_requirements_export tests.test_requirements tests.test_requirements_adversarial tests.test_revenue_workflow -v` | PASS: 86 tests (7 input, 29 export, 10 requirements, 33 adversarial, 7 workflow) |
| `uv run --offline --locked --no-sync python -m unittest tests.test_requirements_input -v` | PASS: 7 input tests |

Locked offline sync/lock checks, compilation, CLI help and tracked/untracked
whitespace checks also passed at snapshot B. The 201/86 receipts superseded
194/79 for that working tree; neither table claims a Docker build/run or
standards-based JSON Schema validation.

### Snapshot C: final finding fixes — current 202/87

This working tree adds the symlink-loop regression and controlled resolution
error, corrects the README workflow, and labels snapshots A/B as historical.
The changes in this final-fix step are confined to `media_house/store.py`,
`tests/test_requirements_export.py`, `README.md` and this report; previous bounded
input, Docker-context and approval/export changes are retained.

| Command/check | Current actual result |
|---|---|
| `uv run --offline --locked --no-sync python -m unittest discover -s tests -v` | PASS: 202 tests, 0 skips/expected failures |
| `uv run --offline --locked --no-sync python -m unittest tests.test_requirements_input tests.test_requirements_export tests.test_requirements tests.test_requirements_adversarial tests.test_revenue_workflow -v` | PASS: 87 tests (7 input, 30 export, 10 requirements, 33 adversarial, 7 workflow), 0 skips/expected failures |
| `uv run --offline --locked --no-sync python -m unittest tests.test_requirements_export.RequirementsExportTests.test_cli_self_referential_symlink_is_exit_two_without_side_effects -v` | PASS: 1 test; symlink creation supported; 0 skips |
| README Bash-block smoke with only synthetic DB/input/output paths substituted and offline environment | PASS: exit 0; returned document ID 2 for meeting ID 1; approved JSON exported by `reviewer` |
| `UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv sync --locked` | PASS: existing project checked, no installs/removals reported |
| `UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv lock --check` | PASS |
| `uv run --offline --locked --no-sync python -m compileall -q media_house tests` | PASS |
| `uv run --offline --locked --no-sync media-house --help` | PASS: exit 0 |
| `uv run --offline --locked --no-sync media-house requirements-capture --help` | PASS: exit 0 |
| `uv run --offline --locked --no-sync media-house requirements-approve --help` | PASS: exit 0 |
| `uv run --offline --locked --no-sync media-house requirements-export --help` | PASS: exit 0 |
| `uv run --offline --locked --no-sync media-house meeting-record --help` | PASS: exit 0 |
| `git diff --check`, `git diff --check origin/main`, and whitespace checks of intended untracked files | PASS |

Only the extra symlink-loop method changes the suite counts from 201/86 to
202/87. The README smoke is an additional disposable check, not another counted
test method. The 50 repeated concurrency runs above belong to snapshot A and
are not represented as a new stress run for snapshot C.

Docker/Podman remain unavailable, and no standards-based JSON Schema validator
was installed or run. **Docker build/run and Draft 2020-12 schema/metaschema
validation remain unverified.** The local results do not imply joint filesystem/
SQLite atomicity, authenticated human identity, client signoff or production
certification.

## Delivery and outstanding limits

Working-tree review files (all uncommitted):

- `media_house/requirements.py`
- `media_house/store.py`
- `media_house/cli.py`
- `tests/test_requirements.py` (prior review edits retained; export callers migrated)
- `tests/test_requirements_adversarial.py` (prior adversarial work retained; export callers migrated)
- `tests/test_requirements_export.py` (original export-boundary tests plus the real symlink-loop CLI regression)
- `tests/test_requirements_input.py` (seven bounded-read and input-validation regressions)
- `README.md` (returned-ID review-ready workflow, required actor/API migration, ordering and residual delivery risks)
- `Dockerfile` and `.dockerignore` (canonical schema inclusion and protected-path context exclusions)
- `docs/REQUIREMENTS_ADVERSARIAL_REVIEW.md` (this report)

Docker is unavailable, so image build/run remains unverified despite the focused
COPY/ignore changes. No dependency installation or deployment was attempted.
The runtime pins/lockfile were not modified. Tests demonstrate consistency checks
and local actor attestation, not authenticated human identity, a cryptographic
signature or protection against someone rewriting the entire DB and its hashes.
No real customer data or historical database was inspected, migrated or repaired.
The original two export findings are closed by explicit attribution and staged
audit-before-delivery ordering. This does not remove the documented crash,
post-commit delivery/cleanup risks or the template/schema coverage limit;
passing regressions must not be presented as joint atomicity or certification.
