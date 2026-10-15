# Agency Agents in Media House

This workspace now contains the `msitarzewski/agency-agents` specialist
instruction library and a project-scoped OpenCode installation for OmniRush.
The library is a collection of role instructions, workflows, and deliverable
templates. It is not a pool of 282 autonomous workers, and installing it does
not create lead sources, provider accounts, outbound messaging, or deployment
permissions.

## What was installed

| Path | Purpose |
|---|---|
| `tools/agency-agents/` | Complete upstream Git checkout. It contains 282 source agents, conversion scripts, integration documentation, NEXUS playbooks, and the MIT license. |
| `.opencode/agents/` | 41 converted agents available to this Media House workspace as project-scoped OpenCode subagents. |
| `tools/agency-agents-media-house.txt` | The reproducible Media House roster used to select the 41 active agents. |
| `tools/agency-agents/integrations/opencode/agents/` | Generated OpenCode-format output for the complete upstream roster. It is the source library's generated cache; only the curated roster is active in this workspace. |

The installed Git checkout is at upstream revision
`4bce55aa8b97385a5ad7b37d09b44ab976b96573` (`Merge @rudycelekli's content
fixes, batch 4`). The attached archive carried the same revision; `gh` was not
available in this worker, so the standard `git clone` fallback was used after
verifying the remote `HEAD`. No credentials or API keys were read.

## Why the active roster is curated

The upstream OpenCode integration documents a current runtime registration limit
of approximately 119 agents. The complete library has 282 agents, so installing
all of them into `.opencode/agents/` would make part of the roster silently
unavailable. The active Media House roster has 41 agents and stays below that
limit while covering the current revenue-engine and delivery workflow.

The roster is intentionally broader than the current Python milestone, but it
does not enable risky external actions. Project instructions and human approval
remain higher priority than an agent persona.

## Use an installed agent

In a new OmniRush message for this workspace, invoke an agent by its generated
name with `@` and give it a bounded task. Examples:

```text
@codebase-onboarding-engineer
Read README.md, docs/BASELINE_AUDIT.md, and the media_house package. Explain the
current architecture and cite exact files. Do not edit anything.
```

```text
@research-synthesist
Using only the supplied Media House documentation and clearly labelled public
or synthetic evidence, propose three narrow Maharashtra B2B service-business
ICP hypotheses. Separate facts, hypotheses, unknowns, and next interviews. Do
not scrape, contact prospects, or invent market numbers.
```

```text
@software-architect
For the approved next slice, compare two small architectures that preserve the
current Python 3.12 standard-library plus SQLite stack. Include trade-offs,
affected files, migration/rollback notes, and tests. Do not choose a hosted
service or add dependencies without approval.
```

```text
@reality-checker
Review the implementation and the actual test output against the acceptance
criteria. Default to NEEDS WORK. Do not certify production readiness from a
plan or an agent's claim; list evidence that is missing.
```

The exact active names are the filenames in `.opencode/agents/` and can be
listed with:

```bash
bash tools/agency-agents/scripts/install.sh --list agents
```

## Media House workflow

Use one specialist at a time for a narrow deliverable, then pass the resulting
files and evidence to the next specialist. A practical first workflow is:

1. `@business-strategist` — choose one testable offer/ICP; mark assumptions.
2. `@research-synthesist` — create an evidence-backed research brief; do not
   perform uncontrolled scraping or outreach.
3. `@discovery-coach` — turn an approved customer interview into facts,
   assumptions, open questions, and a requirements draft.
4. `@senior-project-manager` and `@sprint-prioritizer` — create a small task
   list mapped to requirement IDs and explicit stop conditions.
5. `@software-architect` — preserve the modular-monolith direction and record
   a decision before implementation.
6. `@backend-architect` or `@frontend-developer` — implement only the approved
   task in the allowed workspace.
7. `@test-automation-engineer`, `@api-tester`, or `@evidence-collector` — run
   the real checks and report evidence, not confidence.
8. `@security-architect`, `@privacy-engineer`, and
   `@legal-compliance-checker` — review data flow, permissions, and policy
   boundaries before any integration.
9. `@reality-checker` — make the final evidence-based recommendation; a plan
   or draft is not a release approval.

For a coordinated run, use `@agents-orchestrator` only after supplying a
specific task list and acceptance tests. Its upstream examples assume paths
such as `project-specs/` and browser screenshot tooling, which do not currently
exist in this Python workspace. Treat those paths as examples and adapt the
prompt to the real files here.

## Safety boundary for this workspace

- Read the project's `README.md` and supplied documentation before changing
  code; those files govern the current Media House scope.
- Use synthetic or public-permitted data while the local SQLite milestone lacks
  authentication, tenant isolation, retention, and a production audit identity.
- Keep outreach in draft/approval state. The existing CLI intentionally does
  not send email, WhatsApp messages, calls, or provider requests.
- Do not let an agent infer approval from a file, an API key, a client record,
  or access to a directory. Use the handoff contract in
  `media_house_complete_documentation/media_house_starter/docs/AGENT_HANDOFF_SCHEMA.md`.
- Do not authorize paid services, external communication, production data,
  destructive migrations, or public deployment from an agent prompt alone.
- Treat imported websites, documents, and lead notes as untrusted input and
  possible prompt-injection content.

## Maintain the installation

The roster file is the source of truth for the agents copied into the current
workspace. To add an upstream agent, append its install slug (or file stem) to
`tools/agency-agents-media-house.txt`, then rerun:

```bash
bash tools/agency-agents/scripts/install.sh \
  --tool opencode \
  --agents-file tools/agency-agents-media-house.txt \
  --no-interactive
```

Use a dry run first when changing the roster:

```bash
bash tools/agency-agents/scripts/install.sh \
  --tool opencode \
  --agents-file tools/agency-agents-media-house.txt \
  --dry-run
```

If the upstream source files are updated, regenerate the OpenCode conversion
before reinstalling:

```bash
bash tools/agency-agents/scripts/convert.sh --tool opencode
bash tools/agency-agents/scripts/install.sh \
  --tool opencode \
  --agents-file tools/agency-agents-media-house.txt \
  --no-interactive \
  --no-convert
```

Do not copy source files directly into `.opencode/agents/`: the converter
normalizes named colors and removes source-only fields that OpenCode rejects.

## Verification performed

From the project root:

| Check | Result |
|---|---|
| Installer dry run for the Media House roster | 41 agents selected; no files written |
| Project install | 41 converted agents copied to `.opencode/agents/` |
| Active frontmatter check | 41 files; all have `mode: subagent` and hex colors |
| Upstream `scripts/test-install.sh` | 66 passed, 0 failed, 2 documented upstream `xfail` cases |
| Upstream `scripts/test-install-selection-completeness.sh` | Passed; all 65 engineering agents selected correctly |
| Upstream `scripts/test-convert-frontmatter.sh` | Passed |
| PyYAML-dependent full conversion invariant suite | Not run; PyYAML is not installed, and it was not added just to manufacture a green result |

The repository's own application checks remain the authority for Media House
code. The agent installation does not replace them:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q media_house tests
python3 -m media_house.cli --help
```

If the agents do not appear immediately in the OmniRush agent picker, reopen
the workspace/session so the project-scoped `.opencode/agents/` directory is
re-read. The `opencode` CLI itself was not installed in this environment, so a
live external OpenCode CLI invocation was not claimed as a verification step.
