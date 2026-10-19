# Media House revenue engine and lead discovery

This workspace contains a small Python 3.11 + SQLite lead pipeline and an
optional OpenStreetMap/Overpass discovery adapter. Both use only the standard
library. The supplied documentation under `media_house_complete_documentation/`
is retained; `docs/BASELINE_AUDIT.md` records the earlier, local-only baseline.

## Setup

The supported rebuild uses Python **3.11.13**, pinned in `.python-version`, and
[uv](https://docs.astral.sh/uv/) **0.13.0**. The application has no runtime
dependencies outside the Python standard library; `uv.lock` records the
resolved project metadata and empty runtime dependency set. Docker supplies
both pinned tools, so the container workflow is the simplest clean-machine
rebuild.

On Ubuntu or Debian, install `pipx` for the pinned `uv` command-line tool:

```bash
sudo apt update
sudo apt install -y pipx
```

From the repository root:

```bash
pipx install 'uv==0.13.0'  # only if uv 0.13.0 is absent
export PATH="$HOME/.local/bin:$PATH"  # needed if ~/.local/bin is not already on PATH
uv python install 3.11.13
uv sync --locked
```

Finder file-target operations additionally require a Python `sqlite3` build
exposing `Connection.serialize` and `Connection.deserialize`; the application
checks these APIs and fails closed before opening a file target. The pinned
Python 3.11.13 environment is the supported reproducible build.

`uv sync --locked` fails instead of changing the lockfile when the project
metadata and `uv.lock` disagree. Copy `.env.example` to `.env` only when a
shell or process manager will load it; the application itself does not parse
`.env` files. The only supported setting is `MEDIA_HOUSE_DB`, which supplies
the default SQLite path when `--db` is omitted. The command-line `--db` value
continues to take precedence because it is parsed by the existing CLI.

## Run locally

The installed `media-house` entry point and the existing module invocation are
equivalent. Examples below use `uv run` so the locked environment is selected:

```bash
uv run media-house --help
uv run media-house --db .media_house/demo.sqlite3 package-list
uv run python -m media_house.cli discover import tests/fixtures/discovery_synthetic.json
```

The CLI defaults to `.media_house/media_house.sqlite3` and creates local state
only when a database-backed command is run. Discovery planning, saved-result
preview, and the synthetic finder path remain offline; network access still
requires the explicit `discover run --confirm-network` flag documented below.

For a container rebuild, Docker (or a compatible builder) uses the pinned
Python base image and `uv.lock`:

```bash
docker build -t media-house:local .
docker run --rm media-house:local --help
```

The image stores its default database under `/data`; mount a host directory at
`/data` when a container run needs persistence. No credentials are baked into
the image or read during the build. The build runs the offline test suite
before it produces the runnable image.

## Safety boundary

- CSV rows are validated before any database write; a file with an invalid row
  is rejected atomically.
- A company is deduplicated by normalized website domain when one is supplied,
  otherwise by normalized company name plus region. Each distinct accepted row
  adds source evidence with URL, observed timestamp and researcher.
  Discovery additionally reuses the stable OSM object URL already in source
  evidence, even when the object name, website or query geography label changes.
  Conflicting source/domain identities reject the entire import for review.
- Unknown contact, permission and opt-out values remain explicit. An existing
  opt-out is sticky and cannot be cleared by a later import.
- Fit scores are deterministic and explainable. They are an initial heuristic,
  not a market-validated probability or a claim that a prospect has a need.
- Outreach drafts stay `PENDING_APPROVAL`. Approval requires a supplied human
  actor and a `granted` or `not_required_confirmed` channel permission. An
  approved draft still does not call a provider; its audit metadata explicitly
  records `send_performed: false`.
- Completed customer meetings can be recorded with human-authored notes and an
  explicit recording policy. The local app stores no audio or calendar data;
  early lead states advance to `discovery_complete`, while later proposal or
  delivery states are preserved.

Only `discover run --confirm-network` can request data from an Overpass
endpoint. Planning, saved-result preview, cache-only reuse and importing are
offline. Discovery never creates/approves outreach drafts or sends email,
WhatsApp, calls, or any other messages. It does not fetch discovered websites.
There are no paid services, credentials, scheduled requests or background jobs.

## CLI

Run from the workspace root:

```bash
python3 -m media_house.cli --help
# Use a populated CSV that follows the supplied template header.
python3 -m media_house.cli --db .media_house/demo.sqlite3 import-csv \
  path/to/populated-leads.csv \
  --researcher "founder"
# Inspect the JSON list and use a returned prospect id.
python3 -m media_house.cli --db .media_house/demo.sqlite3 list
python3 -m media_house.cli --db .media_house/demo.sqlite3 draft 1 \
  --actor "founder" --channel email --content "Human-reviewed draft text"
python3 -m media_house.cli --db .media_house/demo.sqlite3 approve-draft 1 \
  --actor "founder"
python3 -m media_house.cli --db .media_house/demo.sqlite3 meeting-record \
  --prospect-id 1 --meeting-at "2026-10-20T10:00:00+05:30" \
  --notes-permission permission_granted \
  --recording-policy not_recorded --actor "founder" \
  --notes "Human-authored meeting notes"
python3 -m media_house.cli --db .media_house/demo.sqlite3 meeting-list \
  --prospect-id 1
python3 -m media_house.cli --db .media_house/demo.sqlite3 export-csv export.csv
```

The header-only supplied template is useful as a schema reference; an import
row must contain the required values, and must use explicit `unknown` markers
where contact, permission, or opt-out information is unresolved.
Meeting recording policies are `not_recorded`, `permission_granted`, or
`permission_denied`; the field records operator metadata only and does not
enable recording or contact a provider. Notes require either
`permission_granted` or `not_required_confirmed` metadata.

## Checks

```bash
uv lock --check
uv run python -m unittest discover -s tests -v
uv run python -m compileall -q media_house tests
# An entirely synthetic saved-result preview, with no database/network access:
uv run python -m media_house.cli discover import tests/fixtures/discovery_synthetic.json
```

The discovery tests use synthetic JSON and injected HTTP openers. An explicit
guard rejects accidental real urllib requests. No live-source availability,
coverage, data quality or endpoint compatibility has been verified by these
checks.
Both files in `tests/fixtures/` are fabricated offline examples, including their
source IDs/timestamps; they do not report records retrieved from OpenStreetMap.

## Optional local agent tooling

The project-scoped OmniRush/OpenCode state and the separately managed Agency
Agents checkout are excluded from version control. They are not required to
set up, run, test, or containerize Media House. The retained
[`docs/AGENCY_AGENTS_GUIDE.md`](docs/AGENCY_AGENTS_GUIDE.md) records their
local safety boundaries for a workspace where those optional tools are present.

## Lead discovery: plan → fetch for review → approve import

The operator supplies a **bbox in south,west,north,east order**, a geography
label, and one or more category filters. Repeated filters are combined with OR;
`shop=*` means the shop tag is present. Values are quoted as literal QL strings,
not interpreted as regular expressions or query fragments. Geography is query
configuration, not a verified business address.

### 1. Inspect the plan (offline)

This **Nagpur pilot bbox is an example configuration only**, roughly a small
central-city study area, not an administrative boundary or a business-logic
default. Choose a suitable small area and category before any request:

```bash
python3 -m media_house.cli discover plan \
  --bbox '21.10,79.02,21.20,79.15' \
  --geography 'Nagpur pilot, Maharashtra, India' \
  --filter 'amenity=restaurant' --filter 'shop=*' \
  --max-results 25
```

The JSON plan shows the endpoint, geography, bbox, filters, query, request cap,
SHA-256 cache key, attribution and license. It makes no HTTP request, creates no
cache directory, and does not initialize SQLite. For negative coordinates use
the equals form, e.g. `--bbox='-1.1,-2.1,-1.0,-2.0'`.

### 2. Explicitly permit a source request and save a review file

**The following command permits network access when the operator runs it.**
Omitting `--confirm-network` prints the plan and exits with code 2, even if a
cache might exist. `--cache-only` is the separate offline path.

```bash
python3 -m media_house.cli discover run \
  --bbox '21.10,79.02,21.20,79.15' \
  --geography 'Nagpur pilot, Maharashtra, India' \
  --filter 'amenity=restaurant' --filter 'shop=*' \
  --max-results 25 --timeout 25 \
  --cache-dir .media_house/discovery-cache --cache-ttl 86400 \
  --confirm-network --output .media_house/nagpur-review.json
```

A successful run writes a versioned, reviewable JSON file atomically and prints
counts plus up to five preview records. It does **not** import anything. The
default `--output -` writes the full result to stdout and the short preview to
stderr, so stdout can be redirected to a file. On failure, the exit code is
nonzero, no successful result is printed, and an existing output file is retained.

For a separately triggered, one-request live smoke test, use one narrow filter
and the smallest result cap first. This is the point at which the operator
chooses to permit network access; it is not run by the test suite:

```bash
python3 -m media_house.cli discover run \
  --bbox '21.16,79.08,21.18,79.10' \
  --geography 'Nagpur smoke-test area, Maharashtra, India' \
  --filter 'amenity=restaurant' --max-results 5 --timeout 15 \
  --cache-dir .media_house/discovery-cache \
  --confirm-network --output .media_house/nagpur-smoke-review.json
```

Review the returned `counts`, `preview`, source timestamp, attribution, and
skipped reasons before using the larger example above. A successful smoke run
still only creates a local review JSON file; it does not write SQLite or send
outreach. If the source request fails, inspect the typed error and follow the
manual recovery steps below rather than repeatedly retrying.

The adapter sends one form-encoded HTTP POST (`data=<urlencoded Overpass QL>`)
with an identifying `MediaHouse-LeadDiscovery/1.0` User-Agent. It requests `nwr`
(nodes, ways and relations) with tags and center coordinates, a server timeout,
a 32 MiB server memory budget, and an `out ... N` output cap. It also applies
the result cap locally and limits the response/file size to 8 MiB. Redirects
are reported rather than followed; no retries or pagination run automatically.
`--endpoint` overrides the default `https://overpass-api.de/api/interpreter`
(for example, an operator's local synthetic test endpoint).

### 3. Review offline; approve only the selected records

```bash
# Full converted-row preview: no SQLite connection and no network access.
python3 -m media_house.cli discover import .media_house/nagpur-review.json

# After review, approve the full file for the existing CSV import pipeline.
python3 -m media_house.cli --db .media_house/pilot.sqlite3 discover import \
  .media_house/nagpur-review.json --confirm-import --researcher 'founder'

# Or preview/import a subset; replace the sample id with one actually returned.
python3 -m media_house.cli discover import .media_house/nagpur-review.json \
  --source-id node/123
```

Repeat `--source-id` for multiple reviewed objects, adding `--confirm-import`
and `--researcher` only when ready. Without a selection, all valid normalized
records in the file are selected. Merely supplying `--researcher` does not
approve import. A confirmed import without a nonempty researcher fails before
opening SQLite. The local researcher is an attestation, not authenticated identity.

The integration converts records to a temporary lead-pipeline CSV and calls
`MediaHouseStore.import_csv`. The store's row validation, normalization,
deduplication, transaction, scoring and opt-out/permission merges remain
authoritative. Preview uses that same pure row validator. If any selected row
fails CSV validation (for example a malformed supplied website), the selected
batch is rejected atomically. Preview the errors and select valid IDs rather
than silently replacing source facts. A source-request failure never opens or
writes the database.

New discovery leads have `verified_status=discovered`, `lead_status=new`,
`fit_confidence=unknown`, `channel_permission_status=unknown`, and
`opt_out=unknown`. The exact fit rationale is:

> Public source category/location match only; customer need is unknown.

Names, matched tags, stable `node/123` / `way/456` / `relation/789` identifiers,
OSM object URLs, coordinates or centers, retrieval time, source name, public
website/contact tags, attribution and license are retained. Structured JSON in
CSV notes and source evidence preserves these facts and explicit
`source_returned_unverified`, unqualified, no-confirmed-pain flags. Only the
allowlisted business website/contact tags are extracted; `contact:name`, staff
identities and other personal-detail tags are not used. Contact name/role stay
unknown. A returned email or phone indicates a possible channel, **not consent**.

Missing website/contact/software fields imply nothing about automation or
customer pain. Scores remain the existing research-prioritization heuristic,
not qualification. Existing opt-outs/blocks remain protected. Existing known
channels, explicit permission decisions, verified status, human fit rationale,
and later-stage lead states are preserved by discovery merge rules; the new
source row still retains its own unknown/unverified evidence values. A discovery
row cannot promote a lead to verified or qualified.

### Counts and result handling

- `source_returned`: raw element count in the response.
- `discovered`: valid, named normalized source records retained within the cap.
- `selected`: records selected for import review.
- `accepted`: selected rows that pass CSV validation; on confirmed success,
  equals `imported`. A preview reports eligibility, not database writes.
- `imported`: rows accepted by `import_csv`, including matches to existing leads.
- `new_leads`: prospects newly created by the store.
- `duplicate_records`: accepted rows matched to existing or earlier-in-batch
  businesses. A repeated import can have `imported > 0` and `new_leads = 0`.
- `invalid`: malformed/missing-name/missing-location source records plus selected
  rows failing CSV validation. Source-invalid records also count as skipped.
- `skipped`: source records skipped for reported reasons, plus unselected IDs.
  Repeated source objects, unmatched categories, nodes outside the bbox and cap truncation have separate
  reasons. CSV-invalid selected rows block the batch, rather than silently importing
  the remainder.

New/duplicate/imported counts are zero during preview and
`database_deduplication_checked=false`; no database estimates are made. The run
preview shows source counts and zero import counts. The complete JSON retains
all records and reason counts. Missing names and malformed individual elements
are skipped with counts. Invalid envelopes, invalid JSON and Overpass error
remarks (even with HTTP 200 and partial elements) are typed failures. Empty
successful results are distinct from a failed fetch.

### Bounds, cache, rate limits and recovery

| Setting | Default / enforced limit |
|---|---|
| Bbox | Required; increasing coordinates, no antimeridian crossing; each span ≤ 0.5°, spherical area ≤ 500 km² |
| Filters | Required; 1–5 distinct filters; simple OSM keys ≤ 64 characters, values ≤ 100 characters |
| Results | Default 25; 1–200; no auto-pagination |
| Timeout | Default 25 seconds; 1–60 server seconds; HTTP timeout is 5 seconds longer |
| Cache | `.media_house/discovery-cache`; TTL default 86,400 seconds (one day), range 0–604,800 (seven days) |

The cap bounds output, not all server-side search work. Public Overpass services
have usage policies, limited capacity and no guaranteed availability. Use narrow
pilot requests, respect the chosen endpoint's policy, and do not loop requests,
rotate endpoints to evade limits, or treat the results as a complete business
directory. OSM may be missing, outdated or incorrectly tagged; way/relation
centers can lie outside a query bbox even when the geometry intersects it.

Successful normalized result JSON is cached under a hash of the endpoint,
geography, bbox and query/version. Cache policy/path do not change identity.
Writes use a same-directory temporary file, flush/fsync and atomic replacement;
failed writes retain the previous entry. Failed requests, malformed envelopes,
service-error responses, and results containing malformed elements/locations
are not cached. Missing-name skips alone can be cached. Cache reuse retains the
original retrieval timestamp rather than pretending it was rechecked.

For offline reuse, repeat the **same query options** with `--cache-only` instead
of `--confirm-network`. Expired/missing caches fail without accessing the source.
`--force-refresh --confirm-network` bypasses a cache; it cannot be combined with
`--cache-only`. A zero TTL disables fresh-cache reuse. `--cache-dir` and
`--cache-ttl` are available on plan/run. Saved import files do not expire under
the cache TTL: review their retrieval date before approving an old file.

Recovery is deliberate and manual:

1. **429 / Retry-After:** wait for the indicated seconds or HTTP date, follow the
   endpoint policy, and reuse a fresh cache where possible. No automatic retry.
2. **403 / redirect:** check endpoint access policy or the intended interpreter
   URL before another explicitly confirmed request.
3. **5xx / timeout / network / Overpass remark:** reduce the bbox/filter scope,
   check service availability separately, then retry later only if appropriate.
   An expired cache is not silently presented as fresh.
4. **Malformed/corrupt cache:** inspect/remove the affected hash file or explicitly
   confirm a force refresh. Corruption never silently triggers a request.
5. **Disk-write failure:** fix space/permissions and retry saving; the previous
   complete cache/output file remains intact. Reuse a successful cache to avoid
   another source request.
6. **Import validation/identity conflict:** inspect preview errors and saved facts,
   review existing evidence, select valid IDs, then approve a new atomic batch.
   Saved JSON consistency checks reject altered safety flags/counts/metadata;
   files are local evidence artifacts, not authenticated proof of source truth.

Caches, result files and SQLite are local operator-managed data. Remove stale
artifacts when no longer needed; this milestone supplies no retention job,
tenant access controls or production data-handling system.

### OpenStreetMap attribution and license

Data source: **© OpenStreetMap contributors**, made available under the
**Open Database License (ODbL) 1.0**. Overpass is the query service, not a business
verification service. Retain the attribution and license metadata when using or
sharing data and review ODbL attribution/share-alike requirements for public
database redistribution or derived databases:

- https://www.openstreetmap.org/copyright
- https://opendatacommons.org/licenses/odbl/1-0/

Public availability does not establish accuracy, outreach permission, confirmed
customer need, or an exemption from the selected service's usage policy. One
operator-triggered live smoke request was executed on 2026-10-13 against
`overpass-api.de` for the documented narrow Nagpur bbox and
`amenity=restaurant`, with `max-results=5`: the endpoint returned successfully
with `source_returned=0` and `discovered=0`. This verifies one successful empty
response path, not endpoint-wide coverage or business-data quality. No SQLite
import or outreach followed that request.
