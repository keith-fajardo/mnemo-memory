# Mnemo implementation status

Authoritative plan: `docs/implementation-plan.md` (revision 2)

## Completed issues

### Issue 1 — Completed (2026-08-02)

Initialize the monorepo, CI, linters, type checks, and test commands; copy revision 2 of the
plan into the repository and create this status record.

Completed verification:

- repository structure and revision-2 plan checks;
- clean installs from `uv.lock` and `package-lock.json`;
- Python formatting, linting, strict type checking, and tests;
- dependency license, provenance, and clean-room manifest checks;
- the aggregate `npm run check` command.

Results: the revision-2 digest matched; both clean installs succeeded; Ruff formatting and
linting passed; mypy reported no issues in four source files; all four pytest tests passed;
and all 20 registered Issue 1 dependencies, toolchain components, and CI Actions passed the
license, provenance, lockfile, and prohibited-dependency checks.

No product feature was started in Issue 1.

## Completed issues

### Issue 2 — Completed (2026-08-02)

Create the repository instructions, ownership policy, complete dependency register,
architecture decision template, product memory contract, initial threat model, evaluation
baseline and fixtures, and automated architecture dependency checks.

Completed artifacts include repository instructions, clean-room product ownership and dependency
approval policy, the complete current dependency register, ADR process and template, product
memory contract, initial threat model, three-condition evaluation baseline, ten synthetic golden
workflows with fifty scoped and evidenced questions, and automated package-boundary enforcement.

Results: clean installs from both lockfiles passed; Ruff formatting and linting passed; mypy
reported no issues in eight source files; all seventeen pytest tests passed; all twenty registered
dependencies, toolchain components, and CI Actions passed license and provenance checks; and the
architecture checker passed against the repository while its negative tests rejected forbidden
domain, reverse-package, and connector-peer imports.

No domain or runtime implementation was started in Issue 2.

## Current issue

### Issue 3 — Completed (2026-08-02)

Implement immutable, storage-independent domain identifiers, scopes, sensitivity classification,
retention schedules, evidence references, durable claims, and checkpoint lifecycle types. Add
strict serialization and focused domain tests without adding persistence, transport, model, or
connector behavior.

Implemented nominal UUID identifiers; immutable scope, classification, retention, evidence,
durable-claim, and checkpoint value objects; strict serialization; temporal and lifecycle
invariants; and focused domain tests. Domain code has no storage, transport, MCP, model SDK, or
other adapter dependency.

Results: `npm ci` completed with no vulnerabilities; `uv sync --frozen` audited eleven locked
packages; Ruff formatting and linting passed; mypy reported no issues in twelve source files; all
twenty-five pytest tests passed; dependency and provenance checks passed for twenty registered
entries with no competing memory product dependency; and the architecture checker passed for all
three product Python files.

## Current issue

### Issue 4 — Completed (2026-08-02)

Implemented a versioned, strict context-packet contract and canonical JSON Schema using the Issue
3 domain types. The validator enforces all section and total hard limits, explicit overrides above
8,000 tokens, evidence-bearing items and provenance, conflict preservation, deterministic
omission codes, timezone-aware times, and strict serialization. It validates only; no retrieval,
storage, ranking decision, rendering, or transport behavior was added.

Results: Ruff formatting and linting passed; mypy reported no issues in fifteen source files; all
thirty-eight pytest tests passed; the context-packet JSON Schema and representative fixture check
passed; dependency and provenance checks passed for twenty registered entries with no competing
memory-product dependency; and the architecture checker passed for all four product Python files.

## Current issue

### Issue 5 — Completed (2026-08-02)

Added a storage-neutral checkpoint repository protocol; a local SQLite personal-profile adapter;
the deterministic, forward-only `0001_initial.sql` migration; and backend-neutral repository
contract tests. The adapter uses foreign keys, WAL, a busy timeout, explicit transactions,
restrictive local permissions, scoped queries, strict domain round trips, and restrictive deletion.
No API, CLI, MCP, connector, retrieval, extraction, dbt, embedding, UI, or team behavior was added.

Results: migrations passed from empty databases and remained idempotent; injected migration failure
rolled back; Ruff formatting and linting passed; mypy reported no issues in twenty source files;
all forty-three pytest tests passed; schema, dependency/provenance, and architecture checks passed.

## Current issue

### Issue 6 — Completed (2026-08-02)

Built the minimal application-service layer, strict personal-profile configuration, loopback-only
FastAPI lifecycle API, and source-checkout CLI commands for initialization, start, status, and
stop. The composition module is the only place that constructs the SQLite adapter. No MCP,
checkpoint, retrieval, connector, UI, or team feature is included.

Results: lifecycle configuration, initialization, API, and CLI tests passed; Ruff formatting and
linting passed; mypy reported no issues in twenty-eight source files; all forty-seven pytest tests
passed; schema, dependency/provenance, and architecture checks passed for thirty-seven registered
dependencies and fourteen product Python files.

## Current issue

### Issue 7 — Completed (2026-08-02)

Built the local stdio MCP adapter with fixture-backed `get_context` and `save_checkpoint` tools.
The adapter has a narrow application port, uses the official MCP SDK's stdio transport, keeps MCP
protocol output on stdout, and documents closed, bounded input contracts and fixture-only results.
It does not access SQLite, configure coding agents, proxy model traffic, or implement durable
checkpoint lifecycle, retrieval, connector, UI, or team work.

Results: `uv sync --locked` resolved 49 and audited 46 locked packages; the full `npm run check`
gate passed with 32 formatted and type-checked Python files, 54 pytest tests (including the real
stdio subprocess initialization, tool-listing, both-tool-call, unknown-field, and clean-exit test),
context-packet schema validation, dependency/provenance validation for 56 registered entries, and
architecture validation for 17 product Python files. The dependency register records the direct
`mcp==1.28.1` package and all MCP-attributable transitive packages with locked versions,
provenance, licenses, maintainers, purposes, and replacement boundaries. No competing memory
product dependency was introduced.

Issue 8 has not been started.

## Current issue

### Issue 8 — Completed (2026-08-02)

Implemented the explicit, reversible native Codex MCP registration workflow. It uses only Codex's
supported `mcp add`, `get --json`, `list --json`, and `remove` commands with an absolute,
argument-array Mnemo launcher. The manager never edits TOML directly and refuses conflicting or
unrecognized entries.

Results: an integration test used a temporary `CODEX_HOME`, registered `mnemo-memory` through the
real installed Codex CLI, read the entry back through both `get --json` and `list --json`, launched
the exact returned command and arguments, initialized MCP, discovered exactly `get_context` and
`save_checkpoint`, invoked both fixture tools, removed the registration, and confirmed unrelated
configuration survived. Controlled CLI tests cover accepted, declined, EOF, `--yes`, `--dry-run`,
and `--check` flows without hanging. `npm run check` passed with 66 tests, formatting, linting,
type checking, schema, dependency/provenance, and architecture checks all passing.

No Codex model, provider, authentication, sandbox, approval, network setting, or real user Codex
configuration was modified.

### Issue 9 — Completed (2026-08-02)

Implemented reversible user-scope Claude Code MCP registration using only the supported Claude
CLI. Real integration tests isolate `HOME`, remove Anthropic environment variables, register and
read back the exact Mnemo launcher, validate isolated configuration structurally, start that exact
launcher through the MCP client, call both fixture tools, and remove only Mnemo's entry. No model
request, login, proxy, hook, or project `.mcp.json` change occurs.

Results: confirmation, failure, conflict, timeout-bounded, real-Claude, and MCP smoke tests pass;
the complete verification gate passed with 79 tests. Issue 10 has not been started.

### Issue 10 — In progress

#### Issue 10A — In progress

#### Issue 10A.1 — Complete

ADR 0002 records stable logical checkpoint identity and immutable revision semantics.

#### Issue 10A.2 — Complete

Added distinct checkpoint revision identities and a forward-only SQLite v2 migration. The ADR 0002
corrective amendment separates identity-free `CheckpointContent` from the legacy replacement-
checkpoint DTO: v2 revision payload JSON now contains only canonical content, while aggregate and
revision identities, scope, lifecycle, timestamps, predecessor links, evidence, and provenance
remain structural metadata. Legacy checkpoint chains migrate transactionally to aggregate headers
and ordered immutable revision records while preserving payload, scope, timestamps, lifecycle
state, and evidence links. Invalid ambiguous, forked, cyclic, cross-scope, broken, or
provenance-less chains fail the migration and roll back. The 10A.2 focused migration, canonical
serialization, raw-payload, and reopening coverage passes as part of the complete 92-test gate.

#### Issue 10A.3 — In progress

Repository lifecycle contracts and compare-and-swap mutations. The 10A.3a repository-port and
reference-adapter work remains in progress and is unblocked by the canonical content correction;
10A.3b SQLite compare-and-swap and 10A.3c compatibility cleanup/final gate have not started.

#### Issue 10B and 10C — Not started

## Issue queue

Issues 10–12 are not started. Work must stop after Issue 9 until explicit approval is given.
