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

### Repository hygiene — Completed (2026-08-02)

The standalone Mnemo root now has its own Git repository on `main`. The baseline tracks only
project files; local runtime data, SQLite files, caches, secrets, and editor artifacts are ignored.
No parent or sibling path was staged, committed, or modified.

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

#### Issue 10A — Complete

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
The unreleased v2 migration also stores scope visibility structurally, correcting the concrete
scope-round-trip defect that would otherwise prevent exact canonical aggregate reconstruction.

#### Issue 10A.3 — In progress

Repository lifecycle contracts and compare-and-swap mutations.

##### Issue 10A.3a — Complete

Defined the storage-independent aggregate/revision lifecycle contract and typed expected outcomes.
The reference adapter now creates active aggregate/revision pairs, appends immutable canonical
revisions with expected-revision comparison, applies completion and abandonment transitions, keeps
terminal retries idempotent, and lists only scoped active checkpoints with deterministic pagination.
Its shared behavioral contract passed against the reference adapter as part of the complete
100-test verification gate. The legacy compatibility cleanup was completed in 10A.3c.

##### Issue 10A.3b — Complete

Implemented the canonical checkpoint lifecycle port in the existing SQLite adapter. Scoped SQL
creates aggregates and initial revisions transactionally, retrieves current and historical
revisions, performs guarded current-pointer compare-and-swap updates, and supports terminal
transitions, deterministic listing, pagination, restart persistence, and reference-contract parity.
The complete 115-test verification gate, injected rollback coverage, and bounded two-connection
concurrency coverage passed.

##### Issue 10A.3c — Complete

Removed legacy replacement-chain operations from the canonical repository port and deleted the
obsolete legacy repository-behavior contract. The only retained legacy helper is explicitly named
and limited to seeding real v1 migration fixtures. Final compatibility, identity, immutability,
scope, CAS, migration, integrity, and two-adapter contract review passed in the complete 114-test
verification gate.

#### Issue 10A.3 — Complete

Canonical checkpoint repository contracts, reference behavior, SQLite CAS parity, and final
compatibility cleanup are complete.

#### Issue 10A — Complete

ADR, canonical aggregate/revision representation, forward migration, repository lifecycle
contracts, SQLite CAS, and final architecture review are complete.

#### Issue 10B — In progress

##### Issue 10B.1 — Complete

Added the storage-independent checkpoint application service. Its typed commands coordinate
canonical aggregate/revision creation, revision, completion, abandonment, and scoped retrieval
through the repository port while translating expected storage outcomes into safe application
errors. Context queries select only scoped active checkpoints, construct a versioned packet with
exact revision provenance, enforce the 600-token active-checkpoint budget and the packet total,
and report deterministic token-budget omissions. The fixture MCP adapter remains unchanged.

The focused application suite and complete verification gate passed with 123 tests: formatting,
linting, strict type checking, context-schema validation, dependency/provenance validation, and
architecture validation all passed. No SQLite runtime composition, MCP wiring, connector, or
Issue 11 work was started.

##### Issue 10B.2 — In progress

###### Issue 10B.2a — Complete

Consolidated local personal-profile resolution with the precedence explicit path, `MNEMO_DATA_DIR`,
persisted platform-default configuration, then a platform default that never depends on the working
directory. The closeable runtime composition opens the configured SQLite profile, migrates it to
schema v2, and provides the canonical checkpoint application service without a global connection.
Focused resolver, corruption/newer-schema, lifecycle, close/reopen, and same-directory persistence
tests passed; a different directory has no access to the stored checkpoint. The complete 128-test
verification gate passed. MCP remains fixture-backed; 10B.2b and 10C are not started.

###### Issue 10B.2b — Complete

The production stdio server now composes the canonical local runtime for its full lifetime and
translates its two fixed MCP tools through `CheckpointApplicationService`. `save_checkpoint`
supports explicit create, revise, complete, and abandon operations; `get_context` returns only the
scoped active durable revision in the canonical packet. Test-only fixture injection remains explicit
and the production SQLite adapter no longer offers a legacy-chain write helper. Durable lifecycle,
terminal selection, sanitized errors, real stdio, Codex, and Claude launcher regressions passed in
the complete 125-test verification gate.

#### Issue 10B — Complete

Checkpoint application services, deterministic local runtime composition, and durable MCP wiring
are complete. Issue 10 remains in progress for 10C hardening only.

#### Issue 10C — Complete

Reconciled the 128-to-125 test change: the old seven fixture MCP tests became four durable MCP
tests after four fixture-invalid-input parameter cases were consolidated into strict durable schema
and sanitized-error coverage, for a net decrease of three without loss of runtime, migration,
fixture-isolation, or connector behavior. Added three independent-process integration tests, bringing
the suite back to 128 meaningful tests. They prove restart after create/revise/complete, abandonment
history and reason preservation, abrupt-stop durability after an acknowledged save, bounded
two-process revision conflict handling, integrity/foreign-key checks, scope/provenance continuity,
and 600-token packet behavior. The final verification gate passed with no fixture or legacy
replacement-chain writer on the production path.

### Issue 10 — Complete

Issue 10 now provides canonical immutable checkpoint revisions, scoped SQLite persistence,
storage-independent lifecycle services, durable stdio MCP tools, and restart/failure-isolation
coverage.

### Issue 11 — Complete

#### Issue 11A — Complete

Added an original, model-free coding-task handoff fixture with a 2,917-token deterministic prior
transcript, golden required/current/stale/inference facts, exact synthetic evidence anchors, and an
explicit 311-token canonical checkpoint. The `npm run eval:resumption -- --json` command builds a
357-token canonical packet, compares no-memory, full-transcript, and Mnemo conditions, and scores
fact availability/provenance without invoking a model. It passed every fixture gate: 100% required
fact recall and provenance coverage, accepted current decision and next action, no stale decision
presented as current, and 87.8% contextual-token savings over full transcript replay. The full
verification gate passed with 132 tests. Issue 11B was deferred to the next approved sub-issue;
Issue 12 was not started.

#### Issue 11B — Complete

Added a real, isolated cross-client transport evaluator using Codex CLI 0.145.0 and Claude Code
2.1.220. It registers each temporary `mnemo-memory` launcher through Mnemo's connection commands,
reads back the exact client-owned command/argument arrays, and runs fresh stdio MCP processes over
one temporary SQLite profile. Codex-to-Claude and Claude-to-Codex both retrieve the Issue 11A
checkpoint with 100% required-fact and provenance coverage; an alternating Claude revision retains
the stable checkpoint identity and advances to revision 2. The evaluator records no-memory 0%
availability, scope non-disclosure, isolated configuration preservation, stale-tool recovery,
corrupt-profile failure without fallback, and missing-launcher failure. It invokes no model or
authentication flow. `npm run eval:cross-client -- --json` and the complete 135-test gate passed.
Issue 12 remains not started.

### Issue 12 — In progress

#### Issue 12A — Complete

Added an offline, storage-independent dbt manifest v12 adapter and deterministic lineage graph.
It validates `metadata.dbt_schema_version`, explicit scope, bounded JSON input, node/source
identity, dependency references, authoritative `depends_on.nodes`, optional parent/child map
consistency, cycles, and personal-mode resource limits. The parser records SHA-256 artifact and
normalized-graph fingerprints, marks currentness unknown without external repository evidence,
and keeps descriptions and metadata inert. The original synthetic fixture covers source, staging,
intermediate, mart, test, package-boundary, branching, and converging lineage. Direct/transitive
queries are iterative, bounded, deduplicated, depth-aware, stable, and evidenced.

Results: focused parser and graph tests, existing resumption and cross-client evaluations, and the
complete repository verification gate passed. No dbt executable, warehouse, SQL/Jinja rendering,
SQLite persistence, MCP/context retrieval, model call, or Issue 12B/C work was added.

#### Issue 12B — In progress

##### Issue 12B.1 — Complete

Added migration v3 and storage-independent project-index repository contracts for immutable,
project-scoped dbt manifest snapshots. The SQLite and reference adapters atomically persist
metadata, bounded node/edge projections, and evidence; preserve historical snapshots; provide
digest idempotency; and switch one active snapshot per project using expected-active conflict
protection. Raw manifests, SQL, macro bodies, arbitrary metadata, credentials, absolute source
paths, and descriptions are excluded. The shared contract covers retrieval, adjacency, ordering,
scope non-disclosure, replacement, and rollback/integrity behavior.

Results: SQLite migration, reference/SQLite contract, integrity, reopen, stale-writer, and private
read-only temporary-database validation passed. The query service and context integration remain
deferred.

##### Issue 12B.2 — Complete

Added the storage-independent manifest ingestion and lineage-query application service. It parses
only through the supported offline adapter, persists immutable snapshots via expected-active
activation, and serves direct or transitive scoped upstream/downstream traversal with batched
frontiers, shortest depth, deterministic ordering, evidence, limits, and structured truncation.
Currentness is deliberately distinct from active selection: exact comparable manifest/source-state
evidence yields current or stale; missing or incomparable evidence yields unknown. No MCP or
context-packet integration was added.

#### Issue 12B — Complete

Immutable snapshots, atomic activation, and deterministic persisted lineage queries are complete.
Context-packet/MCP integration remains deferred to 12C.

#### Issue 12C — In progress

##### Issue 12C.1 — Complete

Added local `mnemo dbt ingest` and `mnemo dbt status` commands plus an application-level unified
context assembler. Optional structured `dbt_lineage` input on the existing `get_context` tool
returns bounded, provenance-bearing structural facts alongside the durable checkpoint without
adding a third tool. Active dbt snapshots are explicitly labeled current, stale, or unknown from
comparable evidence; `require_current` omits non-current structural facts. Structural output uses
nearest-depth deterministic ordering and structured token/traversal omissions. No dbt execution,
warehouse access, raw SQL storage, or model call was introduced.

##### Issue 12C.2 — Not started

##### Issue 12C.2 — Complete

Added the deterministic, model-free unified checkpoint-plus-lineage benchmark. It compares
no-memory, transcript, raw-manifest, and bounded Mnemo structural conditions using the existing
synthetic task and manifest. The gate validates factual/lineage/provenance availability, section
and total budgets, currentness labeling, and combined context reduction without claiming provider
billing or model-answer quality. The initial local vertical slice is complete; later work remains
limited to automatic capture, catalog/run-results artifacts, execution/warehouse access, general
code indexing, embeddings, UI, team mode, and live model-quality evaluation.

#### Issue 12 — Complete

Issues 1–12 complete the initial local vertical slice: explicit durable checkpoints, dbt manifest
lineage, scoped unified context, and native local client launchers. No model/provider, credential,
or network configuration is changed by Mnemo.

## Issue queue

Issues 1–12 are complete. Later milestones listed in the implementation plan remain unimplemented.

### Issue 13 — In progress

#### Issue 13A — Complete

The source tree is an installable `mnemo_memory` package with the separate `mnemo-memory` console
command. Runtime migrations and schemas are wheel-safe resources loaded with
`importlib.resources`; the production launcher uses the installed command rather than a source
checkout module. The permanent distribution name and public project URL remain intentionally
pending maintainer approval; no package has been uploaded.

#### Issue 13B — Complete

Built source-independent wheel and source-distribution artifacts with the public repository
metadata, and verified their contents include package code, typed-marker, migrations, and schema.
Both artifacts install outside the checkout and load resources from `site-packages`; the isolated
wheel verification has covered CLI lifecycle, durable stdio MCP restart behavior, synthetic dbt
ingest/status, `uv tool install`, and isolated real Codex/Claude registration read-back/removal.
Final wheel and sdist were rebuilt source-independently, inspected, and installed into fresh
temporary environments outside the checkout. The installed command, package-resource resolution,
SQLite migrations, synthetic dbt path, durable MCP restart smoke, and isolated connector
registration paths pass without a source checkout dependency. CI now covers the verified Python
3.12 configuration on Linux and macOS. No package registry was queried or modified, no private
manifest was used, and no external upload occurred.

#### Issue 13C — Complete

##### Issue 13C.1 — Complete

Read-only PyPI/TestPyPI name research selected the approved permanent distribution name
`mnemo-unified-context`; the Python namespace and installed executable remain unchanged.

##### Issue 13C.2 — Complete

Published the approved `mnemo-unified-context` 0.1.0a1 wheel and source distribution through
TestPyPI Trusted Publishing. The exact uploaded artifacts were independently downloaded,
hash-verified, installed into a fresh environment, and smoke-tested. No production PyPI upload
occurred during this stage.

#### Issue 13D — Complete

Published the exact checksum-bound TestPyPI-verified artifacts to production PyPI through the
manual `pypi` Trusted Publishing workflow. Production metadata, both artifact hashes, fresh-wheel
installation, CLI initialization, and packaged migration/schema resources were independently
verified after upload. No long-lived API token was used.

### Issue 14 — In progress

#### Issue 14A — In progress

##### Issue 14A.1 — Complete

Implemented the storage-independent generic command-wrapper kernel and its local argv-only
subprocess adapter. The wrapper uses injected resolver, process, clock, and invocation-ID ports;
provides distinct sanitized resolution, launch, recursion, working-directory, and strict-hook
outcomes; and retains bounded structured hook results. Before hooks execute in registration order,
after hooks unwind in reverse order, and strict mode changes only an otherwise successful child
result. The local adapter inherits terminal streams, avoids shells, and performs bounded
interrupt cleanup. Entry-point discovery, dbt hooks/bindings, CLI and shell integration remain
out of scope.

##### Issue 14A.2 — Not started

#### Issue 14B — Complete

Added local, explicit dbt-project bindings and the dbt pre/post-hook functions. The pre-hook
resolves a configured project and captures the prior manifest/active snapshot; the post-hook
activates only a changed, valid manifest after a successful non-interrupted command. Failed,
missing, invalid, or stale competing updates retain the prior snapshot.

#### Issue 14C — Complete

Added `mnemo-memory dbt exec -- <dbt arguments>` and opt-in zsh/bash/fish shell-hook generation.
The wrapper preserves dbt argument arrays and exit codes, supports default fail-open and explicit
strict-memory behavior, and keeps manual `dbt ingest`/`dbt status` plus the two-tool MCP contract.
No dbt-core dependency, warehouse call, automatic shell-profile modification, or publication was
introduced.
