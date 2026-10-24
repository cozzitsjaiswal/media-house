# Optional Harvey integration

Harvey is an external, local sales-agent project. It has its own SQLite schema, dashboard,
provider credentials, browser/HTTP integrations, and outbound sending code. Media House does
**not** vendor Harvey or add its dependencies to the core `media-house-revenue-engine` package.
The core project remains dependency-free and its local-only safety boundary remains intact.

## Local installation

Install Harvey into a separate ignored directory and virtual environment when you explicitly
want to use it as a prospecting/export tool:

```bash
mkdir -p .media_house/harvey-source
git clone https://github.com/ethanplusai/harvey.git .media_house/harvey-source
uv venv .media_house/harvey-venv
uv pip install --python .media_house/harvey-venv/bin/python \
  -e .media_house/harvey-source
```

The attached Harvey source was inspected locally before adding this adapter. Harvey includes
mail providers, dashboard controls, browser collection, and an optional autonomous heartbeat;
none of those components are imported or started by Media House. Do not copy `.env`, provider
tokens, OAuth files, or `data/` from a Harvey installation into the repository.

## Reviewed CSV handoff

Harvey's `export` command produces a CSV. Review that file first, then preview the conversion
without opening SQLite:

```bash
uv run media-house harvey-import .media_house/harvey-prospects.csv
```

The conversion requires a company name, public HTTP(S) website, and valid Harvey `created_at`
timestamp. It maps missing region, fit, and permission facts to explicit `unknown` values. It
does not store the raw email address in Media House because the current local schema has no
tenant-scoped contact-address field; it records only that an email was present and preserves
Harvey's email status in the source notes. A verified/deliverable email is not consent.

After human review, explicitly import the converted rows:

```bash
uv run media-house --db .media_house/media-house.sqlite3 \
  harvey-import .media_house/harvey-prospects.csv \
  --confirm-import --researcher "reviewer"
```

The import uses the existing atomic CSV validation, deduplication, source-evidence, opt-out,
and no-send rules. It does not contact Harvey, fetch a website, send email, approve a draft,
or create an authenticated tenant. The `researcher` is a local attestation, not an identity
provider assertion.

## Trust and production boundary

Harvey export files are untrusted local evidence. They are not authentication assertions,
membership grants, role evidence, permission evidence, or proof of outreach permission. For
shared-client use, route the import through the trusted authentication boundary and a complete
tenant-scoped resource migration; do not expose the raw Harvey database or connection to a
request handler.
