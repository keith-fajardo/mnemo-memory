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

Added an original, model-free coding-task handoff fixture with a 2,948-token deterministic prior
transcript, golden required/current/stale/inference facts, an evidence-backed correction lesson,
and exact synthetic evidence anchors. The `npm run eval:resumption -- --json` command builds a
499-token canonical packet from a 453-token checkpoint, compares no-memory, full-transcript, and
Mnemo conditions, and scores fact availability/provenance without invoking a model. It passed every
fixture gate: 100% required fact recall and provenance coverage, accepted current decision, next
action, and correction lesson, no stale decision presented as current, and 83.1% contextual-token
savings over full transcript replay. The full
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

### Issue 14 — Complete

#### Issue 14A — Complete

##### Issue 14A.1 — Complete

Implemented the storage-independent generic command-wrapper kernel and its local argv-only
subprocess adapter. The wrapper uses injected resolver, process, clock, and invocation-ID ports;
provides distinct sanitized resolution, launch, recursion, working-directory, and strict-hook
outcomes; and retains bounded structured hook results. Before hooks execute in registration order,
after hooks unwind in reverse order, and strict mode changes only an otherwise successful child
result. The local adapter inherits terminal streams, avoids shells, and performs bounded
interrupt cleanup. Entry-point discovery, dbt hooks/bindings, CLI and shell integration remain
out of scope.

##### Issue 14A.2 — Complete

Added deterministic installed-package hook discovery under `mnemo.command_hooks`. Only Python
distribution entry points are considered; project files and current-directory Python are never
loaded. Registrations are validated, sorted, filtered by their integration, and malformed,
unloadable, or duplicate entries are skipped with bounded sanitized warnings. `dbt exec` now
merges accepted installed dbt hooks after Mnemo's built-in manifest hook, so the contract is live
rather than merely a library helper; an extension cannot shadow Mnemo or another accepted hook.

#### Issue 14B — Complete

Added local, explicit dbt-project bindings and the dbt pre/post-hook functions. The pre-hook
resolves a configured project and captures the prior manifest/active snapshot; the post-hook
activates only a changed, valid manifest after a successful non-interrupted command. Failed,
missing, invalid, or stale competing updates retain the prior snapshot.

The personal-mode path is now `mnemo-memory dbt enable` once per dbt repository: Mnemo initializes
the local profile if needed, creates private stable owner/workspace/project identities, binds the
canonical directory, and optionally ingests an existing valid manifest. `dbt status` and
`dbt disable` use that local binding without asking a normal user for UUIDs. An unenabled project
still runs dbt in fail-open mode and receives one concise enable reminder.

#### Issue 14C — Complete

Added `mnemo-memory dbt exec -- <dbt arguments>` and opt-in zsh/bash/fish shell-hook generation.
The wrapper preserves dbt argument arrays and exit codes, supports default fail-open and explicit
strict-memory behavior, and keeps manual `dbt ingest`/`dbt status` plus the two-tool MCP contract.
The release workflows also install each source-independent wheel and source distribution outside
the checkout, then prove the installed command can enable a synthetic project, generate the safe
shell wrapper, activate a manifest through `dbt exec`, retain it after reopen, and idempotently
recognize an unchanged manifest.

#### Issue 14D — Complete

Automatic task-memory onboarding is available as an explicit opt-in for Codex and Claude Code.
It creates a private machine-local project binding, installs only Mnemo-owned lifecycle-hook entries,
refreshes a bounded static source-structure projection at session start, a checkpoint save, and a
changed unsaved work-stop boundary, and asks the
connected agent to retrieve bounded context at session start and write a typed checkpoint at a
work-stop or compaction boundary. It does not ingest transcripts, source text, or credentials.
No dbt-core dependency, warehouse call, or automatic shell-profile modification was introduced.
The lifecycle boundary now accepts a completed handoff only after the scoped SQLite repository
shows a changed checkpoint revision (or a verified terminal transition); a reported tool name or
repository-read failure cannot clear pending work. With the same explicit consent, a bounded user
prompt is used transiently to select already-persisted same-project checkpoint and Markdown
context. The prompt is never stored, and the automatic prompt packet is capped at 1,300 estimated
tokens with scope-first retrieval. Fresh-process and token-reduction tests cover this path.
The two MCP tools now resolve an omitted scope only from the current directory's explicit local
auto-memory binding. This removes UUID handling from normal personal-mode agent use while retaining
complete explicit scope for advanced callers; partial or unregistered implicit scopes fail closed.
Version `0.1.0a2` was published to production PyPI through the checksum-bound, OIDC Trusted
Publishing workflow after source-independent artifact verification. Machine-local project bindings
and lifecycle session markers now use symlink-safe, process-serialized atomic updates so concurrent
client events cannot discard another session's marker. A deterministic synthetic wrapper-phase
evaluation now separately accounts for pre-hook, child dbt, and post-hook parse/activation work
without making a machine-performance claim.

Automatic task memory and dbt enablement now reuse the same local project scope when they are
enabled for the same canonical repository. Unified context deliberately translates a task request
to that project scope only for structural lookups, so a checkpoint handoff and authoritative dbt
lineage can appear together without weakening cross-project isolation.

Lifecycle source refresh now compares immutable structural snapshots and provides a bounded,
metadata-only added/removed/modified relative-file, declaration, and relationship summary from the
most recent persisted transition to the connected agent at session start and in its checkpoint
reminder. Immutable per-file SHA-256 fingerprints make a body-only change visible without storing
source bodies or transcript; intent remains explicit checkpoint evidence rather than an inference
from a diff.

Go source structure now treats a root `go.mod` as file-only local-module evidence: an exact declared
local-module import targets an explicit package-directory symbol, never an arbitrary source file.
External or malformed/absent-module imports remain unresolved, and member calls still require one
exact saved declaration in that package.

TypeScript source structure now also recognizes one strict-JSON, root `tsconfig.json` local path
alias mapping. It resolves only an exact local `baseUrl`/single-`paths` target; inherited, commented,
multi-target, escaping, malformed, and package mappings remain deliberately unresolved.

Python source structure now recognizes only one explicit setuptools `pyproject.toml` source-root
declaration: `package-dir = {"" = "src"}`. That mapping may resolve exact import and call targets
below `src/`; Mnemo does not infer a source root from a directory name or interpret other packaging
systems in this static slice.

JavaScript/TypeScript source structure now resolves a workspace-package import only when root
`package.json` contains a strict literal `workspaces` array and one matching local package declares
an exact string `exports` (or fallback `main`) entry to a saved source file. Conditional exports,
duplicate names, nested globs, and external packages remain unresolved rather than guessed.

The same strict JavaScript/TypeScript workspace proof now records a local package dependency only
for a runtime `dependencies` entry with a literal `workspace:` specifier. This lets source-impact
queries identify a proven dependent workspace package without executing a package manager or
claiming lockfile, build, development, peer, optional, or runtime-resolution behavior.

Rust source structure now applies an equally narrow rule to local Cargo library crates: a literal
runtime `[dependencies] name = { path = "..." }` declaration becomes an edge only when its
normalized local path and unrenamed package name identify another parsed local library crate.
Cargo is never executed; version-only, workspace-inherited, build, development, optional, feature,
renamed, and escaping dependencies remain unresolved.

Scoped source discovery now reuses the existing `source_query` route with deterministic lexical
ranking: exact saved identities, then prefixes, then all-literal-token matches. Reference and
SQLite adapters share the same bounded ranking contract, while SQLite retrieves only a bounded
scoped candidate set per query. The search uses retained symbol/path identities only—not source
bodies, comments, embeddings, model calls, or cross-project data—and its results still carry the
immutable snapshot provenance needed before an impact traversal.

Each successful checkpoint save for an enabled local project can also record one immutable,
scope-checked co-observation with the exact source snapshot parsed immediately afterward. The
association is idempotent, survives restart, and appears in bounded context with checkpoint and
snapshot provenance. It is deliberately not causal: only explicit checkpoint evidence and lessons
can explain why work changed.

Supported Codex and Claude Code integrations also now use their prompt-boundary lifecycle event
without inspecting submitted prompt text. After a tracked project mutation, the next user turn
receives one bounded reminder to consult Mnemo before making historical or impact claims; saving a
checkpoint clears that reminder. This is a client-integrated cue, not a replacement for model
reasoning or transcript capture.

Checkpoint content now has a bounded, immutable, evidence-backed lesson type for corrected
reasoning or analysis mistakes. A lesson records the trigger, mistaken assumption, correction,
prevention, and exact revision evidence IDs. It is returned with the checkpoint in a later bounded
context packet. If a newer active handoff omits a prior lesson, the context service walks a bounded
revision history and returns the lesson as historical episodic evidence with its original revision
and citations; token-budget pressure produces a structured omission. Mnemo does not infer private
reasoning from edits, test failures, or transcripts: the agent must explicitly record the lesson,
which keeps a later prevention cue honest and scoped.

The active follow-up adds `save_checkpoint` operation `record_lesson`: it appends exactly one
evidence-backed correction to the current active handoff without requiring an agent to resend all
checkpoint fields. It preserves the ordinary immutable revision chain, 600-token write budget,
and expected-revision conflict behavior; an identical retry at the current revision is idempotent.

Checkpoint creation, ordinary revision, completion, abandonment, and lesson recording now also
write one scoped, append-only lifecycle event tied to the exact immutable revision and its evidence.
The event ledger contains only transition metadata—not checkpoint bodies, transcripts, source text,
SQL, environments, or model reasoning—and its SQLite projection is atomic with the checkpoint
write. This is a durable foundation for later bounded episodic retrieval; the existing checkpoint
and lesson context remains the user-facing task handoff path.

The next episodic-memory foundation slice adds immutable, explicit approved facts for a decision,
failure, or bounded tool outcome. Each fact is task-scoped, evidence-bearing, idempotent by a
caller-supplied source key, and stored separately from checkpoint revisions. It deliberately does
not capture transcripts, prompts, source bodies, raw tool output, SQL, environments, or private
model reasoning. Migration 0007 and matching reference/SQLite repository contracts provide the
durable scoped store. The existing two-tool MCP surface exposes it as `save_checkpoint`
`record_event`, while `get_context` returns bounded, cited facts only when
`include_approved_events` is requested. The opt-in automatic-memory session cue now explicitly
attaches those approved facts and the bounded current task handoff before a supported client starts
work. It does not mistake a small `record_event` for a complete task handoff; the agent remains
prompted to save the full checkpoint before it stops.

The automatic fresh-session attachment now composes those existing immutable records into a small
recent-work ledger: bounded checkpoint lifecycle chronology, lessons, explicit approved
decision/failure/tool-outcome facts, and a separate cited source transition when present. It reuses
the existing scoped stores rather than creating another mutable event history. The ledger states
what was saved or verified and where its evidence lives; the checkpoint or lesson remains the
evidence for why the work happened. It stores no transcript, terminal output, source body, SQL,
environment, or inferred reasoning.

When tracked project work reaches a stop/compaction boundary without a complete checkpoint, Mnemo
also retains one bounded local handoff-needed marker per enabled project. It survives a client
restart and causes the next automatic session reminder to request a real handoff; an ordinary
checkpoint lifecycle save clears it, while incremental fact/lesson recording does not. The marker
is a hashed local scope plus a boolean, not a transcript, source copy, terminal log, command
payload, or inferred explanation.

### Source-structure memory — Complete

The current local slice makes Mnemo useful for ordinary Python, JavaScript/JSX, TypeScript/TSX, Go,
Rust, C, C++, C#, Java, and PHP repositories as well as dbt projects. It has deterministic,
no-execution parser adapters for modules, imports, declarations, and direct syntactically explicit
calls; immutable scoped SQLite snapshots; unambiguous in-snapshot import and limited static-call
links; deterministic direct/transitive dependency and dependent impact candidates; immutable
snapshot diffs; bounded
provenance-bearing `get_context` facts; and
opt-in lifecycle refresh at session start and after changed work stops. Parsing is offline and
stores no source text. Safe direct Java/Rust imported-call resolution now joins the existing
Python/ES-module support. Exact Go imported-package member calls now also join when one matching
declaration exists in the same unique local package directory; imports themselves stay unresolved
because they name directories while the projection names files. Explicit class-owned `self`/`this`
sibling calls now resolve across supported adapters when the enclosing type and target declaration
are both unambiguous. Direct local Python `from .module import member` and parent-package relative
imports now resolve when their path remains inside the registered project. A literal Python
package-initializer re-export such as `from .core import member as public_name` also preserves that
public name when a later caller imports it; wildcard package imports remain unresolved. Direct top-level literal
CommonJS `require("./local")` bindings in JavaScript/TypeScript now also resolve exact local members;
direct top-level JavaScript/TypeScript `const name = function/arrow` bindings also become saved
function symbols, while mutable, conditional, and nested variable bindings remain unresolved;
named explicit local default function/class exports resolve through `import local from "./module"`, and
an exact named local barrel export such as `export { member as alias } from "./module"` resolves through
that literal link. A local wildcard barrel resolves one non-default member only when it proves exactly
one saved local declaration; anonymous, ambiguous/indirect re-exports, and value-flow defaults remain deliberately
unresolved, and default-class
member calls resolve only when the method is syntactically static; computed/dynamic or nested requires remain deliberately unresolved. Explicit Rust `use crate::path::member as local_name` aliases and flat `use crate::path::{member as local_name, member}` lists now resolve only to unique local members; wildcards and nested groups remain unresolved. Exact C++ namespace calls, C#
`using Namespace.Type`, explicit `using Local = Namespace.Type`, and `using static Namespace.Type` calls, and PHP
`use Namespace\\Type`, explicit `use Namespace\\Type as Alias`, explicit `use function Namespace\\member`, and flat grouped PHP imports now join when their saved target is unique; `use const` aliases never become callable links. Direct Java
`import static package.Type.member` calls now join when their local class and method target are
unique; namespace-only imports, aliases that do not name a unique target, and duplicate candidates
remain unresolved. Broad multi-language semantic resolution, a complete call graph, and
automatic transcript capture remain separate follow-up work.

Common unparsed source extensions, including dbt `.sql` models, JSON/XML/INI configuration,
dependency lockfiles, Dockerfile/Makefile-style build files, CSS/HTML/GraphQL/TOML project files,
and Swift/Kotlin/Ruby/Scala/Elixir/Lua/Dart files, now participate as immutable file-only
fingerprints. They make a path/body change visible in a source transition without persisting source
text or claiming parsed declarations, dependencies, or calls. dbt dependency authority remains the
manifest projection.

An exact safe `source_query` can now retrieve one of those file-only projections (for example
`package.json`, `uv.lock`, or `Dockerfile`) with snapshot provenance. The result contains only its
relative identity and digest-backed citation, never its contents or invented structural links.

Source snapshots also retain a scoped, append-only activation ledger. This establishes a truthful
"previous structural state → current structural state" sequence without treating random snapshot
UUIDs as timestamps.

The bounded context path surfaces that latest recorded transition through the existing
`get_context` tool's optional `source_changes` request. An enabled client now also receives that
small transition alongside its automatic fresh-session checkpoint/lesson/fact attachment, so it
does not need to remember a second query merely to see recent changed files. It returns only
provenance-bearing file, declaration, and relationship identities, preserves current/stale/unknown
labeling from an exact supplied source digest, and emits structured omissions for missing history
or budget pressure. It does not replay source text or infer why a change was made.

Fresh enabled sessions also receive a bounded `source_overview` when an active scoped source
snapshot exists, even if no latest transition is available. The overview contains only cited
snapshot identity, file/symbol/edge counts, and a deterministic bounded sample of relative-file,
module, and declaration identities. This keeps the map useful even for intentionally file-only
inputs without claiming an unparsed file has declarations or dependencies. It is subject to the same current/stale/unknown policy and records a
structured budget omission rather than dropping identifiers silently; the summary also records the
counts outside its bounded file/module/declaration sample. It does not add a source
replay, automatic reasoning, or a runtime call-graph claim.

The automatic session-start path passes the exact freshly activated source digest into its bounded
overview and latest-transition request, so those facts are labeled current for that captured
snapshot. It does not weaken the normal rule that a later manual request needs comparable evidence
before an active snapshot can be called current.

The opt-in automatic-memory lifecycle now treats compaction as a context boundary rather than a
command-stop decision. When changed work still needs a handoff, `PreCompact` attaches the last
bounded durable packet and asks the agent to save the current checkpoint; the existing private
pending marker survives immediate compaction. After the real checkpoint save, a fresh
`SessionStart` directly attaches that exact new revision and no longer reports an incomplete
handoff. The Stop boundary continues to block completion until a full checkpoint save is observed.
No prompt, transcript, tool body, or inferred explanation is captured by the hook.

For each bounded exact changed file that has saved parsed declarations, the lifecycle cue also
includes up to six static dependent candidates through resolved in-snapshot relationships, with
the exact immutable source snapshot ID as provenance. It is explicitly a syntax-derived impact
candidate—not a runtime call-graph claim—and omits unparsed, ambiguous, or unresolved files rather
than guessing.

When a checkpoint itself names supported relative files as relevant, the automatic handoff now
uses up to two matching scoped paths in checkpoint order as bounded static-dependent starting
points. The checkpoint only selects the task topic: returned relationships remain source-snapshot
facts with their own provenance and currentness. This bridges a saved “why these files changed”
handoff to the small syntax-proven “what may depend on them?” context without broad source replay.

After a trusted client mutation, source refresh is batched to the next `UserPromptSubmit` lifecycle
boundary rather than executed for every editor operation. The resulting cue contains the same
bounded change and static-impact facts without inspecting or retaining submitted prompt text.

For a changed `.sql` path, the same lifecycle cue also attempts an exact lookup in the active,
scoped dbt manifest. When one node owns the path, it includes up to six downstream dbt unique IDs
from authoritative manifest edges, explicitly with unknown currentness until matching state evidence
is supplied. Missing or ambiguous manifests produce no such cue and never fall back to SQL parsing.

`source_changes` can now also request up to sixteen recent immutable transitions for one canonical
relative path. Results are newest-first, scoped, bounded, and cited to each before/after snapshot;
unrelated project changes and source bodies remain excluded. This lets a later agent investigate
what was recorded for a particular model or file while relying on the checkpoint, lesson, or
approved fact—not a diff—as evidence for why it changed.

An immutable source transition now labels a file rename only when exactly one removed path and one
added path share the same saved SHA-256 body fingerprint. The bounded CLI, automatic session cue,
and `source_changes` packet preserve both relative identities with snapshot evidence. Duplicate or
copied bodies deliberately remain separate add/remove records rather than a guessed move.
For a proven renamed dbt `.sql` model, the automatic cue uses only its new exact relative path for
the existing scoped manifest lookup; it does not derive lineage from SQL or from the rename itself.

The same personal audit is available without MCP through `mnemo-memory memory changes --path
RELATIVE_PATH --history-limit N`; unrelated transitions are omitted from a path-filtered history.

For an enabled local Git work tree, source refresh now records a small local observation keyed to
the immutable Mnemo source digest: full commit ID, parent ID when available, and clean/dirty state.
The automatic session cue can render that state and a proven before/after commit relationship next
to the existing source transition. Git status output, diffs, commit messages, remote URLs,
branches, absolute paths, source text, and intent are neither stored nor returned; checkpoints,
lessons, and approved facts remain the evidence for why work changed.

For an enabled dbt project, unified context now accepts an exact manifest `original_file_path` as
the lineage start identity. It resolves only one scoped active-snapshot node and then uses
authoritative manifest edges for impact; no match or an ambiguous shared file is rejected rather
than inferred from SQL.

### Personal knowledge and Obsidian — Complete

The first completed foundation is a storage-independent, deterministic Markdown/Obsidian document
parser. It requires explicit scope and a safe relative source identity; records a stable document
identity, SHA-256 digest, bounded scalar frontmatter, headings, literal sections, and declared
links; and marks every parsed document as untrusted data. It reads no filesystem itself, follows
no links, executes no Markdown/frontmatter, and makes no model or network call. Vault discovery,
consent, secret policy, persistent incremental sync, rename/deletion handling, lexical retrieval,
and context integration remain in progress.

The next completed boundary is an explicit local Markdown discovery connector. A caller supplies
an absolute approved root and exact scope; the connector reads only bounded `.md` files, skips
symlinked files and known metadata/cache directories (including `.obsidian`), preserves safe
relative identities, and returns deterministic untrusted candidates. It does not persist text,
follow document links, discover a vault automatically, or add documents to agent context.

A storage-independent incremental-sync planner now compares one scoped discovery result with
known active document metadata. It deterministically produces unchanged, revised, added,
uniquely digest-proven renamed, and payload-free tombstone actions. A duplicate-content copy is
never guessed to be a rename.

Durable synchronization is now implemented for the local SQLite profile and a matching reference
adapter. It creates immutable, scoped document revisions; preserves the current revision pointer;
atomically rejects invalid batches; and, after an explicit deletion, removes all content-bearing
revision/section/link rows while retaining only a minimal scoped tombstone. A deterministic
high-confidence secret policy rejects clear credential-like values before persistence; it does not
claim to detect every secret. The application service composes planning, policy, revision identity,
and atomic storage without filesystem access, and the local runtime now exposes that service.

Deterministic lexical retrieval over current revisions is also available as a bounded,
scope-first package service. SQLite uses a rebuildable FTS5 projection over only the current scoped
sections; the reference adapter shares its normalized literal-token scoring and ordering contract.
Every sync atomically rebuilds that scope’s projection, so historical revisions and explicit
deletions are not searchable. It returns exact document/revision identities, executes no document,
runs no model, and is deliberately capped for personal mode rather than performing broad ambient
search. Equal literal scores use checked-in repository Markdown before an optional Obsidian note,
but that is only a deterministic ordering rule: both remain separately cited, untrusted evidence
and no note silently overrides current structural facts.

An explicit `get_context` `knowledge_query` can now include matching current document sections in
the same bounded packet as task and structural facts. Every included section is cited to its exact
immutable document revision and remains labelled `unknown` for filesystem currentness until a
future source-state comparison can prove otherwise. This is an explicit, scope-first request—not
ambient retrieval. For a repository already enrolled through the explicit automatic-memory opt-in,
Mnemo now synchronizes bounded in-project Markdown at lifecycle refresh boundaries; the hook emits
no document text, but reports only a bounded document count and retrieval guidance to a fresh
enabled agent. When the current durable checkpoint names relevant files, its bounded automatic
session attachment also performs same-scope literal note selection from at most four file stems,
with a 250-token note allowance; it never reads the new prompt, replays a notes directory, or
treats that selection as semantic authority. An optional explicit local Obsidian vault binding can add one external vault to
that same project scope with a generated source prefix, and disabling it first performs an atomic
sync that removes its retained content-bearing revisions before removing the binding. Current scoped document navigation
now resolves only declared direct Markdown/Obsidian links and backlinks; ambiguous, external, and
unresolvable links are omitted rather than guessed, with both endpoint revision IDs retained as
evidence.

The optional local semantic retrieval slice now adds a rebuildable vector projection for current
scoped note sections. It is activated only by `mnemo-memory memory semantic index` after the
project's ordinary automatic memory is enabled. The FastEmbed ONNX adapter may download its public
model weights at that first explicit action, but document and query text remain local. SQLite stores
only model identifiers, section digests, and finite vectors tied to immutable revisions; no second
note body, raw SQL, credential, or environment payload is stored. The projection is idempotent,
scope-first, hidden when a revision is superseded, deleted with a tombstoned note, and returned as
the same bounded cited untrusted evidence through an explicit `semantic_knowledge_query`. Hosted
embedding providers, broad ambient semantic retrieval, and semantic authority over current dbt or
source facts remain out of scope.

Knowledge corrections remain revision-based: editing a note creates its next immutable revision,
and only that current revision is searchable. An author may also declare one exact same-project
`mnemo_conflicts_with` relative path in frontmatter. When the declaring note is selected, Mnemo
retrieves both scoped current revisions and preserves an unresolved cited conflict; it does not
infer disagreement from prose, leak another scope, or choose a winner over authoritative structural
evidence.

#### Issue 18A — Complete

The current bounded issue supplies the two missing Milestone 6 exit assertions over existing
behavior. Re-indexing unchanged current sections must perform zero additional local embedding
provider calls. A production unified packet must combine a checkpoint, relevant Markdown section,
and authoritative dbt lineage within all section and total budgets; the note must retain its local
path, heading, citation, and untrusted-evidence label even when its body contains an attempted
instruction override.

This issue adds no connector, parser, persistence, retrieval strategy, model/provider, dependency,
MCP/CLI surface, UI, packaging, procedural memory, or team behavior.

The Milestone 6 audit confirms the explicit filesystem and Obsidian connectors, stable scoped
document identity, content hashes, immutable revisions, unique-digest rename detection, destructive
payload tombstones, bounded frontmatter/headings/sections, declared links/backlinks, and exact
revision citations. Current scoped FTS retrieval is primary; the local vector index is a rebuildable
projection. Editing a note is its correction revision, project Markdown wins only an exact lexical
tie with an Obsidian result, and explicit same-project conflict frontmatter preserves both cited
revisions. All document bodies remain untrusted evidence.

The semantic idempotency test now counts provider invocations: the initial two-section index uses
one batch call, while an unchanged repeat reuses both sections with no additional call. A new
production unified-context test combines an 80-token active checkpoint, a relevant Markdown
section containing an attempted instruction/scope override, and authoritative upstream dbt facts.
The checkpoint objective is unchanged, the note retains `docs/reconciliation.md`, its
`Reconciliation policy` heading, citation, and `untrusted_evidence` representation, no procedure is
created from its prose, and every section plus total token invariant passes.

All 68 focused parser/discovery/sync/storage/retrieval/navigation/semantic/context/policy tests and
the three Obsidian binding/disable tests pass. Create, modify, digest-proven rename, delete,
scope-isolated search, current-revision-only retrieval, malicious-note labeling, explicit conflict,
and embedding cleanup are covered for reference and SQLite behavior where applicable. The complete
repository gate passes with 730 tests, strict typing for 184 source files,
dependency/provenance validation for 86 entries, and architecture validation for 97 product Python
files. Milestone 6 is complete; no new connector, parser, persistence, retrieval strategy,
model/provider, dependency, MCP/CLI surface, UI, packaging, procedural memory, or team behavior was
added by the exit audit.

### Procedural memory — Complete

#### Issue 19A — Versioned checked-in skill and agent registry — Complete

This bounded issue defines strict scalar frontmatter contracts for Mnemo `skill` and `agent`
documents and exposes their current immutable revisions through one scope-first registry. Skills
declare a bounded name, version, applicability tags, compatible clients, checked-in trust, and
source digest; agents declare a bounded name, version, compatible client, and the exact skill tags
they request. Only synchronized checked-in Markdown is eligible. The registry reads the repository's
current revision on every request, retains history in the existing immutable document store, and
does not add a second cache, database, source scanner, model call, context attachment, or MCP tool.
Existing source files are parsed without rewriting them.

Implemented pure domain contracts and one live `KnowledgeDocumentSkillRegistry` over the existing
immutable knowledge repository. Strict `skill` frontmatter now records name, semantic version,
applicability tags, concrete Codex/Claude Code compatibility, checked-in trust, revision, and exact
SHA-256 source digest. Strict `agent` frontmatter records name, semantic version, concrete/any
client compatibility, and requested skill tags. Registry calls require project scope, select only
current synchronized Markdown, reject malformed or non-checked-in entries, fail closed on duplicate
names, prefer exact-client agents over `any`, and perform no prompt inference or source execution.
Because the registry has no second cache, a synchronized revision is visible on the next call while
the repository retains its predecessor revision. Checked-in fixture imports preserve the parsed
frontmatter and Markdown sections without source rewrites or semantic loss.

All 12 focused procedural registry tests pass. The complete repository gate passes with 735 tests,
strict typing for 187 source files, dependency/provenance validation for 86 entries, and architecture
validation for 99 product Python files. No database migration, dependency, scanner, context behavior,
MCP surface, model call, UI, packaging, or team behavior was added.

#### Issue 19B — Applicable skills in unified context — Complete

This bounded issue allows one context request to select checked-in skills by exact applicability
tags or by one exact checked-in agent name plus a concrete client. Procedures are assembled first,
so mandatory checked-in project rules retain priority within the shared section and total budgets.
Selected skills retain immutable skill and optional agent revision/digest provenance and remain
untrusted evidence. This issue adds no prompt inference, generated skill, MCP tool, source scanner,
database migration, dependency, model call, UI, packaging, or team behavior.

Implemented explicit `skill_tags`/`skill_client` selection and exact `skill_agent_name` resolution
on the unified context request. A request cannot combine tag and agent discovery, cannot use the
wildcard client, and cannot discover a skill without a concrete supported client. The service
resolves current project-scoped registry entries only, renders each selected skill as bounded
`untrusted_evidence`, and cites its immutable document revision and digest; agent-selected skills
also cite the exact agent revision and digest. The production MCP composition now supplies the live
registry, but no transport parameter or new tool is exposed in this issue.

Procedures are assembled before skills. A focused budget test proves a mandatory checked-in
procedure remains present while a matching preference skill is omitted when both cannot fit, and
the final selector independently protects mandatory procedures. All 16 focused procedural tests
pass. The complete repository gate passes with 739 tests, strict typing for 187 source files,
dependency/provenance validation for 86 entries, and architecture validation for 99 product Python
files. No database migration, dependency, prompt inference, generated skill, source scanner, MCP
surface, model call, UI, packaging, or team behavior was added.

#### Issue 19C — Scoped MCP skill discovery — Complete

This bounded issue passes explicit skill tags/client or an exact agent/client through `get_context`
and adds two read-only stdio tools: metadata-only `list_skills` and exact-name `get_skill`. Both use
the existing registered-project default or require all five task-scope UUIDs, accept only Codex or
Claude Code as concrete clients, query the current scope before returning anything, and expose
immutable revision/digest provenance. `get_skill` returns one checked-in Markdown skill as untrusted
evidence; neither tool executes content, scans source files, infers from prompts, calls a model, or
mutates state. This issue updates the declared MCP inventory but adds no dependency, migration, UI,
packaging, or team behavior.

Implemented `skill_tags`, `skill_client`, and `skill_agent_name` on the MCP `get_context` schema
and translation path. Added read-only `list_skills` and `get_skill` tools to the Codex/Claude Code
stdio surface. Both resolve the same registered-project binding when UUIDs are omitted, otherwise
require the complete five-ID task scope, and authorize the project before reading the live current
registry. Listing returns at most 32 compatible metadata records without content; exact retrieval
returns one compatible current skill or `null`, with source path, immutable revision ID, SHA-256
digest, checked-in trust, and sections explicitly labeled `untrusted_evidence`. Tool schemas reject
unknown fields and accept only concrete supported clients.

All affected tool inventories, client connection tests, durability/restart tests, cross-client
benchmark checks, and user documentation now declare the five-tool surface. The 70 focused
registry/MCP/Codex/Claude/durability tests pass. The live cross-client benchmark passes on Codex CLI
0.146.0 and Claude Code 2.1.221 with preserved client configuration, zero cross-scope disclosure,
no model call, failure degradation intact, 84.63% context savings, and 81.58% total-input savings
on its fixture.

Milestone 7 exit evidence is complete: exact applicability and compatibility gate every selected
skill; current repository revisions replace stale registry results immediately while immutable
predecessors remain retrievable; a mandatory checked-in procedure retains budget priority over a
matching preference skill; and checked-in skill/agent fixtures preserve their scalar frontmatter
and Markdown sections without rewrite or semantic loss. The complete repository gate passes with
740 tests, strict typing for 187 source files, dependency/provenance validation for 86 entries, and
architecture validation for 99 product Python files. No dependency, migration, generated skill,
prompt inference, source execution, model call, UI, packaging, or team behavior was added.

The first procedural-memory slice reuses the immutable checked-in Markdown revision store rather
than creating a second procedure source of truth. A document is eligible only when strict scalar
frontmatter marks `mnemo_kind: procedure` and gives bounded literal `mnemo_tags`; callers must
request matching tags explicitly. Scoped matching returns a deterministic bounded set in the
existing `skills_and_procedures` packet section with immutable revision/digest provenance and
structured token-budget omissions. Optional `mnemo_mandatory: true` prioritizes a checked-in
project playbook over matching optional playbooks; it cannot override scope policy, user/system
instructions, or verified current dbt/source facts. Markdown remains untrusted evidence and is
never executed. At that first slice boundary, skill/agent registry discovery and explicit
applicability remained for the bounded issues completed above; automatic prompt-based applicability
remains deliberately out of scope.

An enabled project may now designate one checked-in automatic profile for `codex`, `claude-code`,
or `any`. It supplies literal procedure tags for that client at fresh-session attachment time, so
matching procedures join the bounded automatic packet without an agent needing to remember a tag.
Exact-client profiles take precedence over `any`; multiple equally matching profiles fail closed.
The profile revision/digest remains cited alongside each selected procedure. Mnemo does not infer
an arbitrary named-agent role from hook events, inspect prompts, or execute project Markdown.

### Settings, inspection, and packaging — Complete

#### Issue 20A — Loopback onboarding and health dashboard — Complete

This bounded issue serves one dependency-free local dashboard from the existing loopback-only API.
It provides a non-mutating onboarding checklist, lifecycle readiness, sanitized Codex/Claude Code
connection health, current-project registration state, bounded source/dbt/knowledge index counts,
and fixed privacy guarantees. It returns no owner/scope IDs, absolute paths, document or memory
content, process diagnostics, credentials, or command output. The HTML uses packaged local assets
only and a restrictive content-security policy. This issue does not add settings mutation, memory
browsing/mutation, job retry, backup/upgrade/uninstall behavior, network exposure, dependency,
model call, or team behavior.

Implemented a packaged, responsive HTML/CSS/JavaScript dashboard served by the existing FastAPI
process at `/` and a sanitized `/api/dashboard` status endpoint. The dashboard shows a three-step
setup checklist, local store/schema readiness, boolean Codex and Claude Code availability and owned
registration health, current-project registration, and bounded current source/dbt/knowledge counts.
Index staleness remains truthfully `unknown` unless a separate exact currentness observation exists.
The production launcher now starts the packaged `mnemo_memory.apps.api.server` module correctly.

All assets are local package resources. Responses set no-store, no-referrer, no-sniff, frame denial,
and a CSP limited to same-origin scripts/styles/connect calls with all other sources denied. The
status contract excludes data-directory/project paths, scope and snapshot IDs, process metadata,
document/memory payloads, command output, credentials, and exception text; failure yields bounded
status labels and does not affect agent operation. Thirty focused lifecycle/resource tests pass,
including packaged-asset loading, CSP, sanitized API composition, registered-project counts, and
path/ID non-disclosure. The complete repository gate passes with 742 tests, strict typing for 188
source files, dependency/provenance validation for 86 entries, and architecture validation for 100
product Python files. No dependency, model call, settings mutation, memory mutation, job retry,
backup, installer change, non-loopback exposure, or team behavior was added.

#### Issue 20B — Durable personal settings — Complete

This bounded issue adds an atomically replaced, mode-0600 local settings document and loopback
GET/PUT settings endpoints protected against cross-origin writes. It configures repository Markdown
auto-sync consent, explicit approved-event capture consent, optional model provider/model metadata,
the default episodic-retention duration for future compatible jobs, and all context-packet section
and total defaults. Settings must be strict, bounded, secret-free, and recover safely to documented
defaults when absent; malformed state fails closed. The MCP process reads packet/capture defaults at
startup and automatic project hooks honor the knowledge-sync switch. No API key, model call,
retroactive retention mutation, arbitrary source registration, memory browser, job retry, backup,
installer change, non-loopback exposure, dependency, or team behavior is added.

Implemented strict `PersonalSettings` and a symlink-rejecting `PersonalSettingsStore` that returns
documented defaults when absent and atomically replaces a canonical JSON document under the data
directory with mode 0600. Exact fields cover repository Markdown auto-sync, explicit approved-event
capture, optional provider/model identifiers (never credentials), future-job episodic-retention
days, and the six section plus total context budgets. Unknown fields, malformed/oversized files,
invalid model state, totals above 8,000, and unsafe filesystem state fail closed; an interrupted
replacement preserves the prior document.

The dashboard now provides GET/PUT settings. Writes require a same-loopback origin and explicit
`X-Mnemo-Intent`, reject cross-origin requests and undeclared secret fields, and return sanitized
errors. New MCP processes apply all configured packet defaults while preserving per-request bounded
overrides; disabling approved-event capture rejects new `record_event` writes. Automatic hooks honor
repository-knowledge sync consent and cap their already-smaller budgets by configured values, so a
setting can reduce but never expand automatic attachment limits. Optional model metadata makes no
call by itself, and retention changes do not rewrite existing schedules.

All 118 focused settings/API/MCP/automatic-hook/lifecycle tests pass, including default and custom
round trips, permissions, symlink/malformed input, cross-origin denial, unknown secret rejection,
write-failure preservation, budget/capture enforcement, and knowledge-sync enable/disable. The
complete repository gate passes with 753 tests, strict typing for 190 source files,
dependency/provenance validation for 86 entries, and architecture validation for 101 product Python
files. No dependency, API key, model call, retroactive retention mutation, memory browser, job
retry, backup, installer change, non-loopback exposure, or team behavior was added.

#### Issue 20C — Read-only approved-memory browser — Complete

This bounded issue adds a read-only browser for the current registered project's approved task
facts. It must resolve the exact task scope from the canonical local project binding before any
repository read, return bounded pages containing active, corrected, and payload-free retracted
records, and expose each retained event's immutable evidence plus correction/retraction lineage.
The packaged loopback UI may display those records and their evidence, but it must not infer
missing history or retrieve another project. Storage failures and invalid pagination must produce
bounded safe errors. This issue adds no mutation endpoint, candidate review, pin, expiry, export,
deletion, model call, dependency, index/job control, packaging change, non-loopback exposure, or
team behavior.

Implemented a bounded `/api/memories` read model and packaged dashboard section over the existing
approved-event application service. Each request resolves the current canonical project binding
before selecting its exact task scope. The response omits owner/workspace/project/session/task and
source-key metadata while retaining the user-relevant event identity, lifecycle status, bounded
summary, immutable evidence, and correction/retraction action. Corrected records link to their
replacement identity, and retracted records expose only their payload-free tombstone and retained
governance evidence. An unregistered project returns an empty explicit state; another registered
project receives only its own records.

All 42 focused memory-browser/lifecycle/settings/resource tests pass, including active, corrected,
and retracted rendering, cross-project isolation, missing binding behavior, bounded pagination,
and sanitized storage failures. The complete repository gate passes with 755 tests, strict typing
for 192 source files, dependency/provenance validation for 86 entries, and architecture validation
for 102 product Python files. No mutation endpoint, pin, expiry, export, deletion, model call,
dependency, job control, packaging change, non-loopback exposure, or team behavior was added.

#### Issue 20D — Approved-memory correction and payload erasure — Complete

This bounded issue adds explicit correction and payload-erasing retraction controls to the current
project's approved-memory browser. Every write must resolve the canonical local project binding,
require exact same-loopback-origin and explicit intent headers, validate strict bounded input, and
use the existing immutable approved-event governance service with deterministic verified
user-correction evidence. A correction appends one replacement and lineage action; a retraction
removes the retained event/evidence payload and leaves only its scoped tombstone. Retried identical
actions must remain idempotent, stale or competing actions must fail safely, and another project
must not discover or mutate the target. This issue adds no general episodic-candidate UI, pin,
expiry, export, new deletion model, migration, model call, dependency, job control, packaging
change, non-loopback exposure, or team behavior.

Implemented same-origin `POST /api/memories/{event_id}/correct` and
`DELETE /api/memories/{event_id}` controls plus explicit confirmation flows in the packaged UI.
Both routes require their exact `X-Mnemo-Intent`, reject absent or foreign origins before invoking
the action, resolve the current canonical task scope, and accept only exact bounded fields. Mnemo
derives stable action and replacement keys plus verified user-correction evidence from the action
content, so identical retries are idempotent without trusting a client-supplied authority field.
Corrections use the existing append-only replacement lineage; erasure uses the existing retraction
transaction that removes the event and evidence payload and retains only the minimal tombstone.

All 65 focused browser/governance/repository/lifecycle/settings tests pass, including same-origin
denial, cross-project not-found behavior, strict secret-field rejection, retry idempotency,
competing-action safety, persisted payload removal, and sanitized API failures. The complete
repository gate passes with 757 tests, strict typing for 192 source files,
dependency/provenance validation for 86 entries, and architecture validation for 102 product
Python files. No general episodic-candidate UI, pin, expiry, export, new deletion model, migration,
model call, dependency, job control, packaging change, non-loopback exposure, or team behavior was
added.

#### Issue 20E — Durable approved-memory pinning — Complete

This bounded issue adds explicit pin/unpin state for active approved facts. Pin state must be an
evidence-backed scoped user action, remain idempotent for an identical action, rank pinned active
facts before unpinned recency inside the existing bounded approved-event retrieval, transfer to an
immutable correction replacement, and be removed when its fact is retracted. The browser must show
the current pin state and require same-loopback-origin intent plus explicit confirmation for writes.
Migration 0027 must be additive and transactional with documented forward-only recovery. Another
project must not observe or change a pin. This issue adds no general candidate browser, expiry,
export, new deletion behavior, model call, dependency, job control, packaging change, non-loopback
exposure, or team behavior.

Implemented immutable `ApprovedEpisodicEventPinAction` records with verified user-correction
evidence, matching reference/SQLite repository behavior, and an application command used by the
loopback dashboard. Migration `0027_approved_episodic_event_pins.sql` stores append-only scoped
actions and evidence behind a live-target scope trigger without retaining a foreign key that would
block later payload erasure. Identical actions are idempotent; conflicting keys and cross-scope
targets fail closed. Active record views expose only the latest pin state.

The existing bounded approved-event query now orders pinned facts before unpinned recency, so a
one-item context request selects a pinned fact without increasing its count or token budget.
Correction atomically appends an unpin for the superseded identity and transfers the pin to the
replacement; retraction atomically unpins before deleting the event/evidence payload. The browser
shows the current state and uses an explicit same-origin `pin-memory` intent and confirmation for
both pin and unpin.

All 117 focused pin/browser/repository/application/outbox/lifecycle/resource tests pass across
reference and SQLite adapters, including context priority, unpin, retry, cross-scope isolation,
correction transfer, retraction removal, restart-durable rows, and migration rollback to schema 26.
The complete repository gate passes with 760 tests, strict typing for 193 source files,
dependency/provenance validation for 86 entries, and architecture validation for 102 product
Python files. No general candidate browser, expiry, export, new deletion behavior, model call,
dependency, job control, packaging change, non-loopback exposure, or team behavior was added.

#### Issue 20F — Exact-scope approved-memory export — Complete

This bounded issue adds an explicit JSON download for every approved-memory record in the current
registered task scope. The export must include full immutable event and governance serialization,
payload-free retraction tombstones, current pin state, exact scope, a UTC export timestamp, and a
digest over canonical content. It must page through the existing application query rather than
introducing a second storage path, require same-loopback-origin plus explicit export intent, and
use a fixed non-identifying filename. An unregistered or different project must not receive the
target export, and storage failures must remain sanitized. This issue adds no import, expiry,
general production-episodic browser, job control, backup/installer change, dependency, model call,
non-loopback exposure, or team behavior.

Implemented a canonical `mnemo.approved-memory-export.v1` JSON response assembled through the
existing 100-record application pages. Each record carries exact scope, lifecycle status, current
pin state, and the full immutable event/governance serialization; retracted records retain only
their existing payload-free tombstone. The envelope records a normalized UTC export timestamp and
SHA-256 digest over canonical content. The dashboard requests the download only after confirmation
and sends the explicit `export-memories` intent from the same loopback origin; the response uses a
fixed `mnemo-approved-memories.json` filename and Mnemo persists no duplicate export copy.

All 34 focused browser/lifecycle tests pass, including 101-record multi-page completeness, content
digest verification, exact-scope serialization, correction/retraction provenance, empty-project
behavior, unregistered-project rejection, cross-project isolation, same-origin denial, fixed
filename, and sanitized failure handling. The complete repository gate passes with 761 tests,
strict typing for 193 source files, dependency/provenance validation for 86 entries, and
architecture validation for 102 product Python files. No import, expiry, general
production-episodic browser, job control, backup/installer change, dependency, model call,
non-loopback exposure, or team behavior was added.

#### Issue 20G — Verified personal SQLite backup — Complete

This bounded issue adds the recovery primitive required before automated upgrades. One explicit
CLI command must create a coherent SQLite backup under a private directory in the configured data
root, validate integrity, foreign keys, and the exact schema version, include the schema, UTC
timestamp, and full SHA-256 digest in a non-overwritten filename, and report the recovery artifact
without exposing memory payloads. Unsafe symlinks, an absent/corrupt store, partial-copy failures,
and destination collisions must fail closed without damaging the live database or publishing a
partial backup. A restore proof must read canonical data from the backup after the live database
changes. This issue adds no upgrade execution, restore mutation, retention/expiry, index/job
control, diagnostic bundle, installer/uninstaller change, dependency, model call, non-loopback
exposure, or team behavior.

Implemented `mnemo-memory backup` over a dedicated personal-profile application service. It opens
the configured source read-only, uses SQLite's backup API, consolidates destination WAL state,
validates `integrity_check`, `foreign_key_check`, and the migration-ledger maximum, hashes the
validated file, fsyncs it, and atomically publishes it under a mode-0700 `backups` directory with
mode 0600. The non-overwritten filename contains the exact schema version, normalized UTC creation
time, and full SHA-256 digest; the JSON result exposes only recovery metadata. Identical timestamp
and content reuse the already verified artifact without rewriting it.

All 33 focused backup/lifecycle tests pass, including committed-state recovery after live payload
erasure, exact digest/schema naming, permissions, repeat idempotency, missing/corrupt input, unsafe
directory symlink, injected partial-copy cleanup, SQLite WAL/SHM sidecar cleanup, conflicting
destination preservation, live-database non-mutation, and sanitized CLI failure. The complete
repository gate passes with 765 tests, strict typing for 195 source files,
dependency/provenance validation for 86 entries, and architecture validation for 103 product
Python files. No upgrade execution, restore mutation, retention/expiry, index/job control,
diagnostic bundle, installer/uninstaller change, dependency, model call, non-loopback exposure, or
team behavior was added.

#### Issue 20H — Backup-gated one-command upgrade — Complete

This bounded issue adds `mnemo-memory upgrade` for installations actually owned by uv or pipx.
The command must identify ownership from the running isolated environment before taking action,
resolve only the matching manager executable, create and verify an Issue 20G backup before any
installer invocation, stop and await the local daemon when necessary, run the manager's documented
package-specific upgrade command without exposing its output, run the upgraded CLI's initialization
to apply/validate migrations, and restore the prior running/stopped state. Unsupported or ambiguous
environments, missing managers, backup failure, stop timeout, installer failure, post-upgrade
validation failure, and restart failure must return bounded codes; a post-backup failure must report
the recovery artifact. This issue adds no automatic package downgrade or database restore,
installation/uninstall command, expiry, index/job control, diagnostic bundle, dependency, release
workflow change, model call, non-loopback exposure, or team behavior.

Implemented `mnemo-memory upgrade` with isolated-environment ownership detection for one regular
uv receipt or pipx metadata marker. It resolves only that manager's executable and uses the
documented `uv tool upgrade mnemo-unified-context` or `pipx upgrade mnemo-unified-context`
argument vector without a shell. The command requires an initialized schema, publishes a verified
Issue 20G backup, stops and waits up to five seconds for a running service, suppresses manager
stdin/stdout/stderr, runs the upgraded CLI's `init` validation, and restarts only when the service
was previously running. Installer failure attempts to restore that prior service; later validation
or restart failure leaves the recovery artifact and a bounded code without silently replacing
data or downgrading the package.

All 41 focused upgrade/backup/lifecycle tests pass, including uv and pipx ownership, exact command
vectors, backup-before-installer ordering, initialized-store gating, missing/ambiguous ownership,
missing manager, malformed process state, stop timeout, installer/validation/restart failure,
installer-output suppression, prior running/stopped preservation, recovery metadata, and sanitized
CLI configuration errors. The complete repository gate passes with 773 tests, strict typing for
197 source files, dependency/provenance validation for 86 entries, and architecture validation for
104 product Python files. No automatic package downgrade or database restore,
installation/uninstall command, expiry, index/job control, diagnostic bundle, dependency, release
workflow change, model call, non-loopback exposure, or team behavior was added.

#### Issue 20I — Safe one-command uninstall — Complete

This bounded issue adds `mnemo-memory uninstall` for installations actually owned by uv or pipx.
The command must stop and await the local daemon, remove only Mnemo-owned Codex and Claude Code
MCP registrations and automatic-memory hook entries, invoke the owning manager's documented
package-specific uninstall command without exposing its output, and preserve the complete personal
data directory by default. Optional data deletion must require both `--delete-data` and `--yes`,
reject unsafe or ambiguous targets before any change, and occur only after package removal succeeds.
Unsupported environments, client cleanup failures, stop timeouts, installer failures, and data
deletion failures must return bounded codes. This issue adds no backup deletion policy, restore,
diagnostic bundle, signed release workflow, index/job control, dependency, model call, non-loopback
exposure, or team behavior.

Implemented `mnemo-memory uninstall` with exact uv/pipx isolated-environment ownership detection,
fixed shell-free manager commands, bounded process output, daemon stop/await behavior, and cleanup
of only exact Mnemo-owned Codex/Claude Code MCP registrations and automatic-memory hook commands.
Unavailable client executables and foreign registrations are reported and preserved. Failures
before package removal retain the application and attempt to restore a previously running daemon.

The default result explicitly preserves and reports the configured personal data directory.
Permanent removal requires the distinct `--delete-data --yes` form, validates a regular matching
configuration and database before any lifecycle change and again after successful package removal,
and rejects broad, symlinked, or unrecognized targets. A later deletion failure truthfully reports
that application removal already succeeded; user-controlled copies remain outside Mnemo's reach.

All 128 focused uninstall/upgrade/Codex/Claude Code/automatic-hook/lifecycle tests pass. The
complete repository gate passes with 787 tests, strict typing for 199 source files,
dependency/provenance validation for 86 entries, and architecture validation for 105 product
Python files. No backup deletion policy, restore, diagnostic bundle, signed release workflow,
index/job control, dependency, model call, non-loopback exposure, or team behavior was added.

#### Issue 20J — Private diagnostic bundle — Complete

This bounded issue adds `mnemo-memory diagnostics`, which must create one private, integrity-
verifiable ZIP archive of closed diagnostic metadata even when the personal database is absent or
corrupt. The manifest may report the Mnemo/Python/platform versions, initialized/running/schema and
content-free SQLite health, settings availability, current-project registration, and bounded client
availability/ownership status. It must never include memory, checkpoint, note, source, query, job,
or evidence content; owner/scope/item IDs; filesystem or executable paths; environment/configuration
values; credentials; subprocess or exception output; or durable logs. Archive creation must reject
unsafe symlink/collision state, publish atomically with restrictive permissions, include a canonical
manifest digest, and clean partial candidates. This issue adds no log persistence, index/job
control, backup/restore, installer/release workflow change, dependency, model call, non-loopback
exposure, or team behavior.

Implemented `mnemo-memory diagnostics` over a closed typed context and a read-only SQLite health
probe. It publishes a deterministic ZIP containing exactly one canonical
`mnemo.personal-diagnostics.v1` manifest. The manifest reports only bounded runtime versions,
initialized/running/schema state, integrity and foreign-key booleans, settings availability,
current-project registration, client availability/ownership, and explicit exclusion flags. An
absent or corrupt database still yields a useful content-free result without creating a database
or embedding failure details.

The archive and manifest each have a SHA-256 digest. The mode-0700 diagnostics directory and
mode-0600 archive use symlink rejection, exclusive temporary creation, atomic non-overwriting
publication, deterministic reuse, collision preservation, fsync, and partial-candidate cleanup.
The archive contains no durable log or user content, identity, path, environment/configuration
value, credential, subprocess output, or exception text.

All 64 focused diagnostics/lifecycle/Codex/Claude Code tests pass. The complete repository gate
passes with 794 tests, strict typing for 201 source files, dependency/provenance validation for 86
entries, and architecture validation for 106 product Python files. No log persistence, index/job
control, backup/restore, installer/release workflow change, dependency, model call, non-loopback
exposure, or team behavior was added.

#### Issue 20K — Index freshness and last-sync inspection — Complete

This bounded issue adds exact-project last-successful-sync timestamps for the existing knowledge,
source-structure, and dbt indexes and reports their honest currentness through the loopback
dashboard. Successful idempotent and empty syncs count as sync attempts; failed transactions must
not advance status. Source currentness may use only the existing bounded content-free Git
observation, while unavailable or insufficient evidence remains `unknown`. This issue adds no
source scan on dashboard reads, scheduler, durable job queue, retry control, release workflow,
dependency, model call, non-loopback exposure, or team behavior.

Implemented schema-28 `project_index_sync_status` as a content-free exact-project operational
projection. Knowledge, source-structure, and dbt repositories now expose one storage-neutral
last-sync query, and both Reference and SQLite adapters advance it for successful empty,
idempotent, and changed syncs. SQLite updates share the index transaction, remain isolated by the
complete owner/workspace/project scope, survive restart, and do not advance after a rejected write.
Existing index history is deliberately not backfilled because it cannot prove the last complete
sync time. Migration failure rolls back both schema and ledger and has a documented verified-backup
recovery boundary.

The loopback dashboard reports counts, last successful sync, and currentness for all three indexes
without returning paths or identities. dbt uses its authoritative artifact currentness; source
status compares only the existing bounded Git commit/dirty observation and reports `unknown` when
that evidence cannot prove current or stale; knowledge remains `unknown`. Dashboard reads never
scan source files. The web UI displays the same status and `never synced` explicitly.

All 64 focused storage, migration, dashboard, source, dbt, knowledge, and packaged-resource tests
pass. The complete repository gate passes with 798 tests, strict typing for 202 source files,
dependency/provenance validation for 86 entries, and architecture validation for 106 product
Python files. No scheduler, durable job queue, retry control, release workflow, dependency, model
call, non-loopback exposure, or team behavior was added.

#### Issue 20L — Failed-job visibility and explicit retry — Complete

This bounded issue exposes content-free pending, processing, and failed counts for the existing
durable event outbox in the exact registered project and adds one explicit bounded retry action for
failed, unleased jobs. Retry may only make existing jobs immediately available; it must preserve
attempt counts, never synthesize handler success, never reveal job/source/task identities or
payloads, and never cross project scope. This issue adds no new job type, scheduler, handler,
background daemon, payload browser, migration, dependency, model call, non-loopback exposure, or
team behavior.

Implemented one content-free `EventOutboxProjectStatus` contract and exact-project Reference/SQLite
queries over the existing durable event outbox. Pending, active-lease, and failed counts are
mutually exclusive; completed jobs and every other owner/workspace/project are excluded before
aggregation. The dashboard returns counts only, with no job, source, session, task, owner, failure,
or payload data.

The same application service supports an explicit bounded requeue of at most 100 failed jobs. It
selects only incomplete jobs with absent or expired leases, makes them immediately available,
clears only their last bounded failure code, and preserves attempt counts. Active leases are never
broken and requeue never records completion or handler effects. The loopback POST endpoint requires
the registered current project, same-origin request, explicit intent header, and user confirmation;
failures expose stable codes only. No second queue, scheduler, worker, handler, or job type was
introduced.

All 53 focused outbox, dashboard, API, memory-browser, exact-project, active-lease, and sanitized-
failure tests pass across Reference and SQLite. The complete repository gate passes with 804 tests,
strict typing for 203 source files, dependency/provenance validation for 86 entries, and
architecture validation for 107 product Python files. No new job type, scheduler, handler,
background daemon, payload browser, migration, dependency, model call, non-loopback exposure, or
team behavior was added.

#### Issue 20M — Signed PyPI release artifacts — Complete

This bounded issue replaces the existing unsigned uv upload steps in the already manual,
environment-gated TestPyPI and PyPI workflows with the pinned official PyPA publishing action.
The action must publish only the already built, inspected, checksum-bound wheel and source
distribution through the existing trusted-publisher identity and generate a Sigstore-backed PyPI
publish attestation for each artifact. Post-upload verification must require registry-accepted
provenance bound to the expected filename, SHA-256 digest, repository, and workflow. This issue does
not trigger a workflow, publish a release, change a version, add a runtime dependency, create a new
distribution format, or change install/upgrade behavior.

The manual TestPyPI and PyPI workflows now transfer the existing checksum-bound three-file release
bundle into a publish job whose only elevated permission is GitHub OIDC. That job revalidates the
flat bundle, copies only the exact wheel and source distribution into a two-file publish directory,
and invokes the official PyPA publishing action pinned to reviewed commit
`cef221092ed1bacb1cc03d23a2d87d1d172e277b`. Trusted Publishing generates a Sigstore-backed PyPI
publish attestation for each distribution without a long-lived package-index credential.

The existing standard-library registry verifier now polls the PyPI Integrity API for both expected
artifacts after upload. It requires a registry-accepted publish attestation with a nonempty
signature, certificate, and transparency entry, then binds the decoded statement to the exact
filename and SHA-256 digest from the original release bundle and to the expected GitHub repository
and workflow. The post-upload TestPyPI job now checks out the exact triggering commit before running
that verifier. Archive verification also requires every migration through `0028`, preventing a
signed but operationally incomplete distribution from passing the release gate.

Focused workflow, YAML, verifier, archive, and dependency-register validation passes with 17 tests.
The complete repository gate passes with 806 tests, strict typing for 203 source files,
dependency/provenance validation for 87 registered entries, and architecture validation for 107
product Python files. No release workflow was triggered, no artifact was published, and no version,
runtime dependency, distribution format, install behavior, or upgrade behavior changed.

#### Issue 20N — Milestone 8 install and recovery exit audit — Complete

This bounded audit must execute the written personal workflow from a source-independent built
distribution: install it once into an isolated uv tool environment, initialize private storage,
connect an isolated Codex-compatible registration with automatic memory for a sample project, and
start a fresh registered MCP process whose `get_context` call omits every UUID. The result must
truthfully report no active checkpoint before one is saved, then preserve the exact checkpoint and
source snapshot across another fresh process. The audit must also compose the real verified backup
service with a simulated failed upgrade and prove both unchanged live canonical data and readable
recovery data. Existing uninstall separation and diagnostic redaction evidence must be mapped to
the remaining exit gates. This issue adds no installer format, restore command, package publication,
runtime dependency, model call, non-loopback exposure, team behavior, or incremental feature.

The aggregate repository gate now includes a source-independent personal installation check. It
builds and inspects the exact wheel and source distribution, installs the wheel once with isolated
`uv tool install --offline --reinstall`, initializes a private store, and follows the written
automatic-memory connection command against an isolated Codex-compatible registration. The
read-back launcher exposes the current five-tool inventory. Its first fresh `get_context` call
supplies no UUID and truthfully returns no active checkpoint while citing the exact source snapshot;
`save_checkpoint` also omits UUIDs, and a second independently launched MCP process returns that
exact immutable checkpoint revision and the same source-snapshot provenance.

The upgrade audit composes `PersonalUpgradeService` with the real lifecycle, backup, SQLite, and
approved-event services. A simulated owning-manager installation failure occurs only after a
verified backup. The exact canonical event remains readable and identical in both the unchanged
live store and recovery database. Existing upgrade tests cover successful manager/validation
ordering and prior service-state restoration. The safe-uninstall suite proves application removal
preserves data by default and requires the separate `--delete-data --yes` choice for irreversible
deletion. The private-diagnostics suite proves the bundle remains useful for absent/corrupt stores
while excluding content, identities, paths, environment/configuration values, credentials, command
output, exception details, and durable logs.

The focused real backup/upgrade suite passes with 13 tests, and the installed personal workflow
check passes from built artifacts outside the source environment. The complete repository gate
passes with 807 tests, strict typing for 203 source files, dependency/provenance validation for 87
registered entries, architecture validation for 107 product Python files, and the installed-package
gate. Every Milestone 8 build item and exit condition now has executable evidence, so Milestone 8 is
complete. No new installer format, restore command, package publication, runtime dependency, model
call, non-loopback exposure, team behavior, or incremental feature was added.

### Team workspace and production hardening — Complete

#### Issue 21A — Team authorization kernel — Complete

This first Milestone 9 issue defines the storage-independent authorization contract required before
adding PostgreSQL or any remote surface. It must model exact active workspace membership, workspace
roles, project visibility, explicit private-project membership, and a closed operation set. A pure
deterministic policy must deny absent, inactive, mismatched, cross-workspace, and cross-project
claims before storage or ranking; owner-only item visibility must remain owner-only regardless of
workspace role. The role/operation matrix and every denial reason must be explicit, serializable,
and covered by a cross-tenant test matrix. This issue adds no database, RLS policy, API/MCP route,
OAuth, network listener, migration, dependency, audit log, team import, or mutable membership
service.

Implemented strict immutable workspace membership, project membership, project visibility, and
role contracts plus one pure deny-by-default authorization policy over a closed operation set.
Every team request now requires an exact active workspace membership before project evaluation;
private-project grants, workspace-visible projects, project ownership, owner-only item visibility,
and workspace-only administration follow explicit role matrices with typed payload-free denial
reasons. Cross-principal, cross-workspace, cross-project, missing, suspended, owner-only, and
complete role/operation cases are covered by the adversarial matrix. ADR 0010, the product memory
contract, and threat model establish this policy as the application contract that later PostgreSQL
RLS must reproduce. The complete repository gate passes with 818 tests, strict typing for 206
source files, dependency/provenance validation for 87 registered entries, architecture validation
for 109 product Python files, and the isolated installed-package MCP workflow. No database, RLS
policy, authentication, network surface, migration, dependency, audit log, team import, or mutable
membership service was added; team mode remains unavailable until the remaining Milestone 9 issues
are complete.

#### Issue 21B — Team control-plane storage contract — Complete

The current bounded issue defines the durable, storage-neutral state transitions that PostgreSQL
must later implement: workspace creation with exactly one active owner, explicit non-owner
workspace membership changes, atomic ownership transfer, project creation and visibility changes,
and exact project membership changes. Every successful authority mutation must atomically append a
strict payload-free audit event and identical request retries must be idempotent; stale writes,
cross-workspace/project records, orphan projects or project members, implicit owner changes, and a
second workspace owner must fail closed. Exact-key reads and bounded audit pagination are required.
A thread-safe reference adapter and repository contract tests establish semantics without claiming
team durability. This issue adds no database, migration, RLS, application/API service, invitation,
email, OAuth, remote listener, dependency, personal import, knowledge sharing, or usable team mode.

Implemented strict workspace and payload-free audit domain contracts plus a storage-neutral team
control-plane repository and thread-safe reference adapter. Workspace creation atomically establishes
one active owner; ordinary writes cannot create or mutate that owner; ownership transfer promotes
one active successor, demotes the former owner, updates the workspace, and appends one audit event.
Workspace/project memberships and project visibility use exact compare-and-set state, reject stale,
orphaned, inactive-parent, cross-scope, implicit-owner, and no-op mutations, and append audit only on
success. An exact request ledger makes identical canonical retries idempotent and rejects a changed
payload under the same request. Audit records contain only typed identities, action, and time, and
exact-workspace pagination is capped at 100. ADR 0011, the product contract, and threat model define
the transaction and residual-risk boundary. The complete repository gate passes with 826 tests,
strict typing for 208 source files, dependency/provenance validation for 87 registered entries,
architecture validation for 110 product Python files, and the isolated installed-package MCP
workflow. No database, migration, RLS, authenticated service, network surface, dependency, import,
shared knowledge behavior, or usable team mode was added.

#### Issue 21C — PostgreSQL team control plane and RLS parity — Complete

The current bounded issue implements Issue 21B's repository contract in one dedicated PostgreSQL
schema and transaction adapter. The initial forward-only migration must enforce exact foreign keys,
one active workspace owner, active parent membership for active project grants, request
idempotency, atomic payload-free audit append, and restrictive forced row-level security. Every
runtime transaction must set an exact authenticated principal, workspace, and closed operation in
transaction-local database settings; missing or malformed settings default-deny. The database
authorization function and RLS policies must match Issue 21A's complete role matrix, including
private projects and owner-only item visibility, and a non-owner/non-`BYPASSRLS` runtime role must
pass cross-tenant tests. Migration-owner access is limited to schema maintenance and never used by
the runtime adapter. The optional pure-Python PostgreSQL driver and its complete permissively
licensed dependency graph must be pinned and registered. This issue adds no pgvector data,
personal-data parity/import, application/API/MCP service, OAuth, listener, secret store, backup,
deletion workflow, quotas, dashboards, shared knowledge, or usable team mode.

Implemented the Issue 21B repository in a dedicated `mnemo_team` PostgreSQL schema with an atomic
forward-only migration, exact foreign keys, deferrable owner/project-authority constraints,
workspace/request idempotency, and append-only payload-free audit records. All five authority/audit
tables enable and force row-level security. Every adapter transaction sets one exact principal,
workspace, closed operation, and statement timeout with transaction-local settings; missing,
malformed, unknown, and cross-workspace settings deny access. The runtime role is separately
provisioned and rejected if it is the schema owner, a superuser, or `BYPASSRLS`; it receives only
explicit table, sequence, schema, and policy-function privileges and cannot update audit rows.

The PostgreSQL authorization function matches the complete Issue 21A workspace and private-project
role matrices, including owner-only visibility with no administrator bypass. The durable adapter
implements atomic creation, compare-and-set membership/project changes, ownership transfer,
idempotent retry, exact-key reads, and bounded audit pagination while translating database failures
to payload-free storage outcomes. Migration failure rolls the schema back completely; ADR 0012
documents the forward-recovery boundary, runtime credential trust, connection-pool requirements,
dependency choice, and remaining risks. The optional pure-Python `pg8000==1.31.5` transport and its
complete permissively licensed dependency graph are pinned and registered; personal installs do
not acquire the team extra or import the driver.

`npm run check` now starts an isolated real PostgreSQL server and requires migration, runtime-role,
durability, RLS parity, cross-tenant, private-project, and missing/malformed-context tests to pass.
The complete gate passes with 826 default tests plus 3 mandatory real-PostgreSQL tests, strict
typing for 211 source files, dependency/provenance validation for 92 entries, architecture
validation for 111 product Python files, schema validation, and the isolated installed-package MCP
workflow. The clean default install also explicitly marks the delayed optional FastEmbed import for
typing without changing semantic runtime behavior. No pgvector data, personal-data parity/import,
authenticated application/API/MCP service, OAuth, listener, secret store, backup, deletion
workflow, quotas, dashboards, shared knowledge, or usable team mode was added.

#### Issue 21D — PostgreSQL team knowledge and pgvector parity — Complete

The current bounded issue adds the first canonical team-memory data path on top of Issue 21C: one
PostgreSQL implementation of the existing `KnowledgeDocumentRepository` contract. A forward-only
migration must preserve immutable current revisions, exact team project scope, atomic incremental
sync, destructive payload tombstones, deterministic literal retrieval, and rebuildable semantic
vectors using the pgvector data type. Every knowledge table and query must remain protected by the
same forced RLS principal/workspace/operation boundary, and real-database contract tests must prove
current-only retrieval, cross-tenant denial, atomic rollback, secret rejection, vector round trips,
and deletion of text/vector payloads. This issue adds no checkpoint, episodic, dbt/source-structure,
personal import, source-approval, remote service, OAuth, backup, deletion orchestration, quota,
dashboard, or usable team mode.

Implemented PostgreSQL schema version 2 and `PostgreSQLKnowledgeDocumentRepository` against the
existing storage-neutral knowledge contract. Exact workspace/project/owner/visibility scope is
present on every source, immutable revision, section, link, tombstone, sync-status, and embedding
row. Composite foreign keys plus fixed-search-path trigger checks prevent child-scope substitution;
all seven tables enable and force RLS before reads, ranking, row locks, upserts, or deletion.
Literal retrieval reuses the reference/SQLite deterministic ranking contract after a bounded
database selection of authorized current documents.

Semantic projections now use pgvector's native variable-dimension `vector` type without duplicating
source text. The migration requires pgvector 0.8.5, recorded under its PostgreSQL License and pinned
to upstream commit `159b79aaad5983fb7459c1e3df2897fbb2d11788`; CI verifies that immutable commit
before compiling the extension. Vectors retain exact current revision/section/model/digest scope,
and the existing semantic service continues deterministic cosine ranking without an unnecessary
approximate index or new retrieval contract.

Knowledge synchronization is atomic, applies deterministic secret policy before persistence,
maintains current and historical immutable revision reads, and records even an empty successful
sync. Tombstoning first commits minimal anti-resurrection metadata and clears the current pointer,
then removes the revision chain newest-first; sections, links, and pgvector rows cascade in the same
transaction. A private-project viewer can neither read nor tombstone the source, foreign scopes
return no rows, and rejected secret/stale batches leave prior state unchanged. ADR 0013, the product
contract, and threat model document source-governance, extension, recovery, and residual-risk
boundaries.

The real PostgreSQL gate now includes five tests: fresh migration/rollback, atomic v1-to-v2 upgrade,
control-plane durability, knowledge/pgvector parity and deletion, and the complete authorization
matrix. The complete repository gate passes with 826 default tests plus 5 mandatory real-PostgreSQL
tests, strict typing for 212 source files, dependency/provenance validation for 93 entries,
architecture validation for 112 product Python files, schema validation, and the isolated
installed-package MCP workflow. No checkpoint, episodic, dbt/source-structure, personal import,
source-approval, remote service, OAuth, backup, deletion orchestration, quota, dashboard, or usable
team mode was added.

#### Issue 21E — PostgreSQL team checkpoint parity — Complete

The current bounded issue adds PostgreSQL implementations of the existing checkpoint aggregate and
checkpoint lifecycle-event repository contracts. A forward-only migration must preserve exact task
scope, immutable revision history, compare-and-set current revisions, terminal completion and
abandonment, evidence provenance, atomic lifecycle-event append, and bounded active-checkpoint and
event queries. Every checkpoint table and query must use the existing forced-RLS authenticated
principal/workspace/operation boundary, and real-database tests must prove lifecycle parity,
idempotency, stale-writer rollback, terminal-state enforcement, and cross-tenant denial. This issue
adds no checkpoint source-observation projection, episodic memory, dbt/source-structure parity,
personal import, remote service, OAuth, backup, quota, dashboard, or usable team mode.

Implemented PostgreSQL schema version 3 and `PostgreSQLCheckpointRepository` against the existing
checkpoint aggregate and lifecycle-event contracts. Aggregate, immutable revision, and append-only
event rows repeat exact workspace/project/owner/visibility/session/task scope and all three tables
force RLS. Runtime transactions set the bound principal, workspace, closed operation, and statement
timeout before any read, insert, update, or row lock. The runtime role may update only aggregate
current pointers; immutable revisions and events have no update or delete grant.

Canonical checkpoint content and evidence use their existing strict JSON serialization, while
scope, identity, predecessor, revision number, lifecycle status, and time remain constrained
columns. A deferred current-pointer constraint and fixed-search-path triggers require aggregates,
predecessors, and events to match the same scoped revision. Creation, revision, completion, and
abandonment append their deterministic lifecycle event in the same transaction. Aggregate row
locking plus compare-and-set rejects stale writers without a partial revision or event; identical
terminal retries return the committed revision, while competing terminal actions fail closed.

ADR 0014, the product contract, and threat model document the authorization, immutability,
recovery, and remaining service boundary. The real PostgreSQL gate proves atomic v2-to-v3 rollback
and retry, current/historical revision reads, active selection, lifecycle event ordering and
idempotency, completion, abandonment, stale-writer rollback, different-task isolation, and
private-project viewer denial. The complete repository gate passes with 826 default tests plus 6
mandatory real-PostgreSQL tests, strict typing for 213 source files, dependency/provenance
validation for 93 entries, architecture validation for 113 product Python files, schema
validation, and the isolated installed-package MCP workflow. No checkpoint source-observation
projection, event outbox, episodic memory, dbt/source-structure parity, personal import, remote
service, OAuth, backup, quota, dashboard, or usable team mode was added.

#### Issue 21F — PostgreSQL team task events and transactional outbox — Complete

The current bounded issue implements the existing minimized task-activity event and event-outbox
repository contracts in PostgreSQL. A forward-only migration must preserve exact task scope,
deterministic safety rejection, event identity/source-key idempotency, immutable evidence and
retention provenance, and atomic creation of one delivery job with every accepted event. The
outbox must support exact-scope claim, completion, retry, bounded project status, and explicit
failed-job requeue without duplicate effects. All rows and operations must use forced RLS and the
existing authenticated principal/workspace/operation boundary, with real-database tests for lease
races, restart durability, rollback, and cross-tenant denial. This issue adds no approved-fact
governance, extraction candidates, retention expiry/purge, checkpoint outbox backfill,
dbt/source-structure parity, import, remote service, OAuth, backup, quota, dashboard, or usable team
mode.

Implemented PostgreSQL schema version 4 with append-only exact-task `task_activity_events` and a
mutable metadata-only `event_outbox`, plus `PostgreSQLTaskActivityEventRepository` and
`PostgreSQLEventOutboxRepository` implementations of the existing storage-neutral contracts. Both
tables repeat workspace/project/owner/visibility/session/task identity and force RLS. Runtime task
event grants are insert/read only; outbox workers can read/insert/update delivery metadata but
cannot delete it.

Task-event secret and sensitivity policy runs before a transaction. One accepted minimized event
and its deterministic delivery job commit atomically; exact retries are idempotent, while changed
source-key or identity reuse fails without a second row. Retention and evidence retain their strict
canonical serialization, and raw prompts, transcripts, commands, tool bodies, and tool results are
not admitted. New PostgreSQL checkpoint lifecycle events now use the same atomic outbox insertion;
pre-v4 history is deliberately not replayed or backfilled.

Outbox claims select only an authorized exact task and use `FOR UPDATE SKIP LOCKED`, incrementing
attempts under a bounded worker lease. Completion and retry require the exact live lease owner.
Retry records only a bounded failure code and next availability; explicit project requeue selects
at most 100 failed jobs with absent/expired leases, preserves attempt counts, clears neither
completion nor handler effects, and exposes only content-free status counts. A fixed-search-path
trigger requires every inserted task/checkpoint job to match its canonical source scope, kind, and
time; unsupported future topics fail closed.

ADR 0015, the product contract, and threat model document minimization, authorization, replay,
lease, and recovery boundaries. The real PostgreSQL suite proves atomic v3-to-v4 rollback/retry,
accepted/idempotent/conflicting/secret events, event/job restart durability, private-project and
different-task denial, active-lease exclusion, wrong-worker rejection, retry/status/requeue,
attempt preservation, second claim, completion, and immutable-table runtime privileges. The
complete repository gate passes with 826 default tests plus 7 mandatory real-PostgreSQL tests,
strict typing for 214 source files, dependency/provenance validation for 93 entries, architecture
validation for 114 product Python files, schema validation, and the isolated installed-package MCP
workflow. No approved-fact governance, extraction candidates, retention expiry/purge, checkpoint
outbox backfill, dbt/source-structure parity, import, remote service, OAuth, backup, quota,
dashboard, or usable team mode was added.

#### Issue 21G — PostgreSQL team approved episodic-event governance — Complete

The current bounded issue implements the existing `ApprovedEpisodicEventRepository` contract in
PostgreSQL. A forward-only migration and adapter must preserve exact task scope, deterministic
secret rejection before persistence, immutable event/evidence/action history, source-key and
identity idempotency, active-only correction/retraction/pinning, payload erasure for retracted
records, stable pagination, and atomic delivery-job creation for accepted mutations. Every table
and operation must use forced RLS and the existing authenticated principal/workspace/operation
boundary, with real-database tests for migration rollback, restart durability, runtime privilege
limits, and cross-tenant/cross-task denial.

This issue adds no extraction-candidate storage, retention expiry/purge, source approval workflow,
conflicting team-correction resolution beyond the existing deterministic repository contract,
dbt/source-structure parity, personal import, remote service, OAuth, backup, quota, dashboard, or
usable team mode.

Implemented PostgreSQL schema version 5 and
`PostgreSQLApprovedEpisodicEventRepository` for the existing storage-neutral approved-fact
contract. Exact-task fact, governance, and pin tables repeat workspace/project/owner/visibility/
session/task identity and force RLS. Runtime facts are insert/read plus retraction-gated delete;
governance and pin history are insert/read only. No table grants mutable payload updates.

Deterministic event, governance, and pin safety runs before a connection is opened. Accepted facts
commit with one deterministic delivery job; source-key and identity retries are exact and changed
reuse fails. Corrections preserve fact kind and atomically insert the replacement, governance,
delivery jobs, and immutable release/acquire pin transfer when needed. Retractions atomically
append their governance/job, release an active pin, and delete the target summary, source key, and
fact evidence. The payload-free record remains inspectable, exact retries are idempotent, and a
fixed-search-path trigger rejects any fact deletion without its exact retraction.

Migration 0005 extends the outbox source guard for approved fact, correction/retraction, and pin
topics; every job must match canonical scope, kind, and occurrence time. Governance and pin scope
triggers reject cross-task sources. ADR 0016, the product contract, and threat model document the
authorization, payload-erasure, delivery, recovery, and residual authenticated-service boundaries.

The real PostgreSQL suite proves atomic v4-to-v5 rollback/retry, accepted/idempotent/conflicting/
secret facts, pin priority and retry, correction, pin transfer, retraction, payload erasure,
corrected/retracted retry behavior, restart durability, different-task and private-project denial,
deterministic outbox jobs, active-fact delete protection, and least-privilege runtime grants. The
complete repository gate passes with 826 default tests plus 8 mandatory real-PostgreSQL tests,
strict typing for 215 source files, dependency/provenance validation for 93 entries, architecture
validation for 115 product Python files, schema validation, and the isolated installed-package MCP
workflow. No dependency was added. No extraction-candidate storage, retention expiry/purge, shared
source approval or correction resolution, dbt/source-structure parity, personal import, remote
service, OAuth, backup, quota, dashboard, or usable team mode was added.

#### Issue 21H — PostgreSQL team episodic candidates and explicit review — Complete

The current bounded issue implements the existing `EpisodicMemoryCandidateRepository` and
`EpisodicMemoryReviewRepository` contracts in PostgreSQL. A forward-only migration and adapter
must preserve exact task scope; task-event source binding; deterministic candidate/review safety;
bounded one-source batches; candidate identity and extraction provenance; immutable evidence,
retention, and review actions; active state only after explicit approval; stable pagination; and
atomic rollback/idempotency. Every table and operation must use forced RLS and the existing
authenticated principal/workspace/operation boundary, with real-database tests for migration
rollback, restart durability, runtime privilege limits, and cross-tenant/cross-task denial.

This issue adds no extractor or model call, worker/scheduler, episodic correction/retraction,
retention expiry/purge, deletion/export, team source approval workflow, dbt/source-structure parity,
personal import, remote service, OAuth, backup, quota, dashboard, or usable team mode.

Implemented PostgreSQL schema version 6 and `PostgreSQLEpisodicMemoryRepository` for the existing
inactive-candidate and explicit-review contracts. Candidate, review, and active-marker rows repeat
exact workspace/project/owner/visibility/session/task identity and force RLS. Composite foreign
keys plus fixed-search-path triggers bind candidates to canonical task events, reviews to exact
candidates, and active markers to matching approvals. Runtime access is insert/read only for all
three immutable tables.

Candidate storage accepts one contiguous batch of at most four proposals from one source event and
extractor version. Deterministic identity, scope, source, retention, evidence, extraction/provider/
model/prompt provenance, sensitivity, and safety are validated before insertion; retention and
evidence must equal the canonical source row. Exact batch retries are idempotent and changed output
or identity reuse rolls back atomically. Candidates remain inactive regardless of confidence.

Review reloads one authorized candidate and reruns candidate/review safety. One verified user
approval atomically stores the immutable action and matching active marker, preserving source and
extraction provenance and merging review evidence through the existing domain contract. Rejection
stores the action without an active marker. Exact retry is idempotent; competing review,
action-key reuse, unsafe review, and forged rejected activation fail closed.

ADR 0017, the product contract, and threat model document the inactivity, authorization,
provenance, activation, and recovery boundaries. The real PostgreSQL suite proves atomic v5-to-v6
rollback/retry, exact/changed/secret/source-mismatched batches, ordering and source filtering,
approval, rejection, active reads, competing review, action-key reuse, unsafe review, restart
durability, different-task and private-project denial, immutable runtime grants, and database
rejection of an active marker backed by rejection. The complete repository gate passes with 826
default tests plus 9 mandatory real-PostgreSQL tests, strict typing for 216 source files,
dependency/provenance validation for 93 entries, architecture validation for 116 product Python
files, schema validation, and the isolated installed-package MCP workflow. No dependency was added.
No extractor/model call, worker/scheduler, episodic correction/retraction, retention expiry/purge,
deletion/export, source approval workflow, dbt/source-structure parity, personal import, remote
service, OAuth, backup, quota, dashboard, or usable team mode was added.

#### Issue 21I — PostgreSQL team active episodic-memory governance — Complete

The current bounded issue implements the existing `EpisodicMemoryGovernanceRepository` contract
for PostgreSQL active memories. A forward-only migration and adapter extension must preserve exact
task scope, deterministic governance safety, one optimistic immutable revision chain rooted at the
approval action, expected-revision compare-and-set, correction evidence and sensitivity, terminal
payload-free retraction, stable replay, action identity/source-key idempotency, and atomic rollback.
Every table and operation must use forced RLS and the existing authenticated principal/workspace/
operation boundary, with real-database tests for migration rollback, restart durability, runtime
privilege limits, stale writers, and cross-tenant/cross-task denial.

This issue adds no retention expiry/purge, deletion/export, extractor/model call, worker/scheduler,
team source approval workflow, dbt/source-structure parity, personal import, remote service, OAuth,
backup, quota, dashboard, or usable team mode.

Implemented PostgreSQL schema version 7 and extended `PostgreSQLEpisodicMemoryRepository` with the
existing active-memory governance contract. One immutable exact-task action table uses forced RLS,
a fixed-search-path active-memory scope guard, and select/insert-only runtime privileges. Approval
is revision one; corrections and retraction extend that chain through expected-revision
compare-and-set. A database uniqueness constraint prevents two successors from forking one
revision, and the adapter replays approval plus ordered actions instead of storing duplicate
mutable current-claim state.

Corrections preserve bounded verified-user evidence, replacement claim, sensitivity, reason, and
source action identity. Retraction is terminal, stores no replacement claim or sensitivity, and
removes the memory from active reads. Deterministic governance safety, exact task scope, action
identity, and source-key checks precede mutation. Exact retries are idempotent; stale writers,
changed retry payloads, unsafe corrections, revision forks, and post-retraction actions fail
closed. Restart replay reconstructs the same complete revision chain.

ADR 0018, the product contract, and threat model document the optimistic lifecycle, payload and
authorization boundaries, and forward-only recovery. Real PostgreSQL tests prove atomic v6-to-v7
rollback/retry, two corrections, exact retries, stale-writer rejection, secret rejection, changed
identity conflict, terminal payload-free retraction, active-read exclusion, post-retraction denial,
restart replay, different-task and private-project denial, and immutable runtime privileges. The
complete repository gate passes with 826 default tests plus 10 mandatory real-PostgreSQL tests,
strict typing for 216 source files, dependency/provenance validation for 93 entries, architecture
validation for 116 product Python files, schema validation, and the isolated installed-package MCP
workflow. No dependency was added. No retention expiry/purge, deletion/export, extractor/model
call, worker/scheduler, source approval workflow, dbt/source-structure parity, personal import,
remote service, OAuth, backup, quota, dashboard, or usable team mode was added.

#### Issue 21J — PostgreSQL team episodic-memory retention and purge — Complete

The current bounded issue implements the existing `EpisodicMemoryRetentionRepository` contract for
PostgreSQL extracted candidates and approved memories. A forward-only migration and adapter
extension must preserve exact task scope, canonical source retention, deterministic payload-free
expiration identity, due-only selection, immediate exclusion from candidate/review/active/
governance/revision reads, atomic dependent-payload purge, anti-resurrection tombstones, stable
replay, and batch rollback/idempotency. Every row and operation must use forced RLS and the existing
principal/workspace/operation boundary, with real-database tests for migration rollback, restart
durability, runtime privilege limits, conflicting batches, and cross-tenant/cross-task denial.

This issue does not add task-activity-event retention/purge, explicit deletion/export, scheduler or
worker, checkpoint/dbt/source-structure parity, personal import, authenticated remote service,
OAuth, backup, quota, dashboard, source governance, or usable team mode.

Implemented PostgreSQL schema version 8 and the existing `EpisodicMemoryRetentionRepository`
contract on `PostgreSQLEpisodicMemoryRepository`. Immutable expiration and purge tombstone tables
repeat complete task scope, force RLS, and expose read/insert-only runtime privileges. Fixed-search-
path triggers bind expiration to the exact candidate source, policy, non-permanent canonical
schedule, and scope, then bind purge to that expiration and its chronological boundary. Canonical
ISO timestamp text is preserved exactly for deterministic identity provenance while PostgreSQL
casts it only for time comparisons.

One expiration immediately excludes candidate, review, active-memory, governance, and revision
payload reads, including after restart. A later exact purge permits trigger-gated deletion of the
matching governance, active, review, and candidate rows while retaining both tombstones and the
permitted minimized source event. Candidate storage checks retained expiration tombstones before
insertion, preventing extraction retry from resurrecting purged content. Complete batches validate
before mutation; exact expiration/purge replay is idempotent, and non-due, changed, stale,
cross-scope, or concurrently conflicting batches roll back atomically.

ADR 0019, the product contract, and threat model document the two-phase lifecycle, deterministic
timestamp, authorization, anti-resurrection, and recovery boundaries. Real PostgreSQL tests prove
atomic v7-to-v8 rollback/retry, not-due selection, conflicting-batch rollback, exact expiration
replay, immediate exclusion of every dependent read, direct pre-purge deletion denial, restart
durability, different-task and private-project denial, physical dependent-payload purge, source
survival, exact purge replay, immutable tombstone privileges, and anti-resurrection. The complete
repository gate passes with 826 default tests plus 11 mandatory real-PostgreSQL tests, strict typing
for 216 source files, dependency/provenance validation for 93 entries, architecture validation for
116 product Python files, schema validation, and the isolated installed-package MCP workflow. No
dependency was added. No task-event retention/purge, deletion/export, scheduler/worker,
checkpoint/dbt/source-structure parity, personal import, remote service, OAuth, backup, quota,
dashboard, source governance, or usable team mode was added.

#### Issue 21K — PostgreSQL team task-activity retention and purge — Complete

The current bounded issue implements the existing `TaskActivityRetentionRepository` contract for
PostgreSQL minimized task events. A forward-only migration and adapter extension must preserve
exact task scope, canonical event retention, deterministic payload-free expiration, immediate
event-payload exclusion, dependent-candidate purge ordering, atomic source/outbox physical purge,
anti-resurrection tombstones, stable replay, and batch rollback/idempotency. Every row and
operation must use forced RLS and the existing principal/workspace/operation boundary, with real-
database tests for migration rollback, restart durability, guarded runtime deletion, dependency
conflicts, and cross-tenant/cross-task denial.

This issue does not add explicit user deletion/export, a scheduler or worker, checkpoint/dbt/source-
structure parity, personal import, authenticated remote service, OAuth, backup, quota, dashboard,
source governance, or usable team mode.

Implemented PostgreSQL schema version 9 and the existing `TaskActivityRetentionRepository`
contract on `PostgreSQLTaskActivityEventRepository`. Immutable exact-task expiration and purge
tombstones force RLS and are read/insert-only. Fixed-search-path triggers bind expiration to the
event's exact non-permanent policy and canonical ISO schedule text, bind purge to its expiration,
and reject purge while any dependent candidate payload remains. Canonical timestamp text is
preserved for deterministic identity and cast only for chronological comparison.

Expiration immediately removes the minimized event from ordinary reads. Once all dependent
candidate payloads are purged, source purge atomically deletes the matching task-activity outbox
job and event behind trigger-gated DELETE privileges, while source and candidate expiration/purge
tombstones remain. Migration 0009 removes the candidate-expiration foreign key to the live source
row so anti-resurrection metadata can survive required source cleanup; its insertion trigger still
requires the canonical source. Event append rejects the retained source tombstone. Complete
batches validate before mutation; exact replay is idempotent and conflicts roll back atomically.

ADR 0020, the product contract, and threat model document dependency ordering, authorization,
timestamp, anti-resurrection, outbox cleanup, and recovery. Real PostgreSQL tests prove atomic
v8-to-v9 rollback/retry, not-due selection, conflicting-batch rollback, exact expiration replay,
immediate event exclusion, direct-delete denial, restart durability, different-task/private-
project denial, dependent-candidate blocking, candidate-first purge, atomic event/outbox removal,
retained tombstones, exact purge replay, immutable tombstone privileges, and anti-resurrection.
The complete repository gate passes with 826 default tests plus 12 mandatory real-PostgreSQL
tests, strict typing for 216 source files, dependency/provenance validation for 93 entries,
architecture validation for 116 product Python files, schema validation, and the isolated
installed-package MCP workflow. No dependency was added. No explicit deletion/export,
scheduler/worker, checkpoint/dbt/source-structure parity, personal import, remote service, OAuth,
backup, quota, dashboard, source governance, or usable team mode was added.

#### Issue 21L — PostgreSQL team explicit episodic deletion — Complete

The current bounded issue implements the existing `EpisodicDeletionRepository` contract for
PostgreSQL. One explicit verified-user exact-task action must create immutable deterministic,
payload-free tombstones and atomically erase either one memory's candidate/review/active/governance
payloads or one source event plus every dependent memory payload and task-activity outbox job.
Existing retention tombstones must survive, exact replay must be idempotent, changed action keys or
targets must fail closed, and every deletion must use forced RLS, guarded runtime privileges, and
atomic rollback. Real-database tests must cover restart durability, source/dependent ordering,
post-purge deletion, anti-resurrection, cross-tenant/cross-task denial, and migration rollback.

This issue does not add export, backup propagation, scheduler/worker behavior, checkpoint/dbt/
source-structure parity, personal import, authenticated remote service, OAuth, quota, dashboard,
source governance, or usable team mode.

Implemented PostgreSQL schema version 10 and the existing `EpisodicDeletionRepository` contract on
`PostgreSQLEpisodicMemoryRepository`. Immutable payload-free source and memory deletion tombstones
repeat complete task scope, force RLS, and expose read/insert-only runtime privileges. Fixed-search-
path triggers bind each tombstone to an exact live target or retained expiration tombstone, bind
source-caused memory deletion to its source action, and permit physical payload deletion only after
a matching purge or explicit deletion lifecycle record exists.

Individual deletion atomically writes its verified-user deterministic tombstone and erases
candidate, review, active, and governance payload rows. Source deletion first writes its source
tombstone, retains any earlier individual tombstones, creates missing tombstones for every dependent
memory, removes every dependent payload, and then removes the minimized source event and its task-
activity outbox job in one transaction. Existing expiration and purge tombstones survive. Event and
candidate insertion reject retained deletion tombstones. Exact replay is idempotent; changed target,
action-key, identity, scope, or source linkage fails closed and rolls back atomically.

ADR 0021, the product contract, and threat model document erasure ordering, payload-free lifecycle
state, authorization, anti-resurrection, and forward-only recovery. Real PostgreSQL tests prove
atomic v9-to-v10 rollback/retry, individual and source deletion, earlier-dependent ordering, exact
and conflicting replay, restart durability, post-retention-purge deletion, retention-tombstone
survival, different-task/private-project denial, physical payload/outbox removal, immutable
tombstone privileges, and anti-resurrection. The complete repository gate passes with 826 default
tests plus 13 mandatory real-PostgreSQL tests, strict typing for 216 source files, dependency/
provenance validation for 93 entries, architecture validation for 116 product Python files, schema
validation, and the isolated installed-package MCP workflow. No dependency was added. No export,
backup propagation, scheduler/worker, checkpoint/dbt/source-structure parity, personal import,
remote service, OAuth, quota, dashboard, source governance, or usable team mode was added.

#### Issue 21M — PostgreSQL team episodic export parity — Complete

The current bounded issue implements the existing `EpisodicExportRepository` contract for
PostgreSQL. One authorized exact-task read-only repeatable snapshot must produce the existing
versioned canonical bundle with live minimized events/candidates, review and governance streams,
deterministically replayed revisions, and every matching retention, purge, and deletion tombstone.
Scope filtering and forced RLS must precede payload reconstruction; stable identity ordering,
canonical JSON, the SHA-256 content digest, restart stability, and empty non-disclosing denied-scope
results must match the personal contract. Real-database tests must cover complete mixed lifecycle
state, byte stability, tamper-verifiable round trip, private-project/cross-task isolation, and
storage failure translation.

This issue does not add an export file writer, personal-to-team import, checkpoint/approved-fact/
knowledge/dbt/source-structure export, backup propagation, remote service, OAuth, quota, dashboard,
source governance, or usable team mode.

Implemented the existing `EpisodicExportRepository` contract on
`PostgreSQLEpisodicMemoryRepository` without a schema change. Each export starts one repeatable
read-only transaction configured with the exact authenticated principal, bound workspace, and read
operation. Forced RLS and complete task scope filter every canonical query before PostgreSQL JSON
payloads are parsed or revision state is reconstructed.

The adapter returns the existing `mnemo.episodic-export.v1` bundle with permitted live minimized
events and candidates, candidate reviews, governance action streams, deterministically replayed
revision chains, and all matching memory/source expiration, purge, and deletion tombstones. The
existing domain bundle enforces canonical identity ordering, source/dependent relationships,
backend-independent UTF-8 JSON, and its SHA-256 content digest. Exact exports are byte-stable for
the same snapshot and export time. Foreign-task and unauthorized private-project reads yield the
same valid empty bundle shape without leaking identifiers or counts; connection and reconstruction
failures become payload-free export storage outcomes.

The product contract and threat model document snapshot consistency, authorization-before-
reconstruction, integrity, portability, and residual file/import boundaries. Real PostgreSQL tests
cover approved/corrected and rejected live candidates, fully purged memory/source retention,
source deletion, individual deletion, complete tombstone export, canonical JSON round trip, digest
stability and time sensitivity, restart parity, foreign-task/private-project non-disclosure,
invalid-scope rejection, and storage failure translation. The complete repository gate passes with
826 default tests plus 14 mandatory real-PostgreSQL tests, strict typing for 216 source files,
dependency/provenance validation for 93 entries, architecture validation for 116 product Python
files, schema validation, and the isolated installed-package MCP workflow. No migration or
dependency was added. No export file writer, import, broader category export, backup propagation,
remote service, OAuth, quota, dashboard, source governance, or usable team mode was added.

#### Issue 21N — PostgreSQL team source-structure projection parity — Complete

The current bounded issue implements the existing `SourceStructureRepository` contract for
PostgreSQL. Immutable project-scoped source snapshots, privacy-safe relative file paths/digests,
symbols, resolved/unresolved edges, activation history, and last-sync status must commit atomically
behind forced RLS. Exact digest replay reactivates the existing snapshot idempotently; changed
identity or invalid graph state fails closed. Reads and bounded deterministic symbol selection must
authorize before ranking, preserve explicit activation order, survive restart, and reveal no source
text, comments, docstrings, absolute paths, or environment values. Real-database tests must cover
contract parity, atomic rollback, migration rollback, runtime privileges, restart durability, and
cross-tenant/private-project denial.

This issue does not add checkpoint source observations, dbt projections, filesystem scanning in a
service, a worker/scheduler, personal import, remote service, OAuth, backup, quota, dashboard,
source approval governance, or usable team mode.

Implemented PostgreSQL schema version 11 and `PostgreSQLSourceStructureRepository` for the existing
rebuildable source projection contract. Snapshot, safe relative-file fingerprint, symbol, edge,
activation, and sync-status rows repeat exact project scope and force RLS. Composite foreign keys
bind every child to its snapshot and every resolved edge to same-snapshot symbols. The projection
stores no source body, comment, docstring, absolute path, environment value, embedding, or model
output.

One project-keyed advisory transaction lock serializes complete projection storage and activation.
New artifacts insert atomically; exact scoped digest replay reuses the immutable snapshot and
reactivates it only when needed. Explicit append-only activations—not UUID ordering—define history
and transitions. A fixed-search-path trigger prevents immutable-field updates or active-state
changes that do not follow the newest activation. Runtime update access is column-limited to active
state and last-sync time. Exact reads, bounded literal symbol matching, module/path selection,
symbol lookup, and forward/reverse adjacency authorize in PostgreSQL before deterministic ranking.

ADR 0022, the product contract, and threat model document the rebuildable/no-source-text boundary,
authorization, activation serialization, and forward-only recovery. Real PostgreSQL tests prove
atomic v10-to-v11 rollback/retry, exact replay, two activations and reactivation, transition/history
order, sync state, file/symbol/edge parity, bounded search and graph frontiers, conflicting identity
rollback, restart durability, foreign-project/private-viewer denial, immutable-column privileges,
and trigger rejection of an unrecorded active change. The complete repository gate passes with 826
default tests plus 15 mandatory real-PostgreSQL tests, strict typing for 217 source files,
dependency/provenance validation for 93 entries, architecture validation for 117 product Python
files, schema validation, and the isolated installed-package MCP workflow. No dependency was added.
No checkpoint source observation, dbt projection, filesystem service, worker, import, remote
service, OAuth, backup, quota, dashboard, source governance, or usable team mode was added.

#### Issue 21O — PostgreSQL team checkpoint source-observation parity — Complete

The current bounded issue implements the existing `CheckpointSourceObservationRepository` contract
on PostgreSQL. One immutable exact-task observation must bind an existing exact checkpoint revision
to an existing authorized exact-project source snapshot, remain explicitly non-causal, and store
only identities, scope, and observation time. Exact replay is idempotent; a second snapshot for the
revision, mismatched scope, missing side, cross-tenant access, or concurrent conflict fails closed.
The table must force RLS, expose read/insert-only runtime privileges, survive restart, and roll back
atomically on migration or mutation failure.

This issue does not add automatic filesystem parsing, causal inference, checkpoint retention/
deletion/export, dbt parity, worker scheduling, personal import, remote service, OAuth, backup,
quota, dashboard, source governance, or usable team mode.

Implemented PostgreSQL schema version 12 and the existing checkpoint source-observation contract on
`PostgreSQLCheckpointRepository`. The immutable exact-task row forces RLS, exposes read/insert-only
runtime access, and contains checkpoint/revision/source-snapshot identities plus observation time
only. A composite source foreign key and fixed-search-path revision trigger bind both authorized
sides to exact workspace/project/owner/visibility and task scope.

The adapter verifies the checkpoint revision and source snapshot before mutation. Exact replay is
idempotent; a second valid snapshot conflicts, a missing snapshot or revision is not found, and
cross-task/private-project reads disclose nothing. ADR 0023, the product contract, and threat model
make the co-observation/non-causality boundary explicit. Real PostgreSQL tests prove atomic v11-to-
v12 rollback/retry, exact replay, competing and missing targets, restart durability, cross-task and
private-project denial, immutable privileges, and one-row persistence. The complete repository gate
passes with 826 default tests plus 16 mandatory real-PostgreSQL tests, strict typing for 217 source
files, dependency/provenance validation for 93 entries, architecture validation for 117 product
Python files, schema validation, and the isolated installed-package MCP workflow. No dependency was
added. No automatic parsing, causal inference, checkpoint retention/deletion/export, dbt parity,
worker, import, remote service, OAuth, backup, quota, dashboard, source governance, or usable team
mode was added.

#### Issue 21P — PostgreSQL team dbt manifest projection parity — Complete

The current bounded issue implements the core manifest portion of the existing
`ProjectIndexRepository` contract for PostgreSQL. Immutable project-scoped snapshots, minimized
manifest metadata, typed nodes and lineage edges, explicit activation history, and last-sync state
must commit atomically behind forced row-level security. Exact content-digest replay reuses and
reactivates the retained snapshot; compare-and-swap activation, conflicting snapshot identities,
invalid graph endpoints, and cross-tenant access must fail closed. Reads and bounded graph batches
must authorize before reconstruction and survive restart without retaining raw dbt artifacts,
compiled SQL, source SQL, environment values, or adapter responses.

This issue does not add catalog, run-results, or source-freshness projection parity; personal
import; a remote service; OAuth; backup; quota; dashboard; source governance; or usable team mode.

Implemented PostgreSQL schema version 13 and the core manifest portion of the existing
`ProjectIndexRepository` contract. Snapshot, minimized node, typed edge, activation, and sync rows
repeat exact project scope and force RLS. Composite foreign keys bind child rows and both edge
endpoints to the exact scoped snapshot. No raw manifest, SQL, compiled content, macro body, adapter
response, warehouse payload, credential, or environment value is retained.

One project-keyed advisory transaction lock serializes expected-active comparison, complete graph
storage, activation, and sync update. Exact content-digest replay reuses and can reactivate the
retained immutable snapshot; explicit activation rows define transitions independently of UUIDs or
timestamps. A fixed-search-path trigger rejects immutable-field changes and active-state changes
that do not match the latest activation. Runtime updates are column-limited. Exact snapshot,
node/file, adjacency, batch, transition, sync, and deterministic pagination reads authorize in the
database before reconstruction.

ADR 0024, the product contract, and threat model document minimization, authorization, activation,
and forward-only recovery. Real PostgreSQL tests prove atomic v12-to-v13 rollback/retry, exact
replay, CAS conflicts, two activations and reactivation, graph and batch parity, invalid endpoint
rejection, conflicting identity rollback, deterministic pagination, restart durability,
foreign-project/private-viewer denial, immutable-column privileges, and trigger enforcement. The
complete repository gate passes with 826 default tests plus 17 mandatory real-PostgreSQL tests,
strict typing for 218 source files, dependency/provenance validation for 93 entries, architecture
validation for 118 product Python files, schema validation, and the isolated installed-package MCP
workflow. No dependency was added. Supplemental dbt projection parity remains the next bounded
team-backend issue.

#### Issue 21Q — PostgreSQL team dbt supplemental projection parity — Complete

The current bounded issue completes the existing `ProjectIndexRepository` contract on PostgreSQL
with minimized catalog, run-results, and source-freshness projections attached to one exact
authorized manifest snapshot. Each immutable artifact must retain typed domain fields and evidence,
validate every referenced resource against that snapshot, activate idempotently by kind and digest,
force RLS, and survive restart. Raw artifacts, SQL, compiled content, comments, statistics, adapter
responses, database errors, filters, credentials, and environment values must not be retained.

This issue does not add dbt execution, a warehouse connection, scheduled ingestion, personal
import, remote service, OAuth, backup, quota, dashboard, source governance, or usable team mode.

Implemented PostgreSQL schema version 14 and completed the existing `ProjectIndexRepository`
contract with catalog, run-results, and source-freshness projections. One immutable supplemental
artifact table retains versioned typed metadata and a Mnemo-owned minimized projection; one
resource-link table uses composite foreign keys to bind every referenced identity to the exact
scoped manifest snapshot. Both tables force RLS. Raw artifact bytes and unreviewed comments,
statistics, messages, adapter/database payloads, filters, credentials, and environment values are
not stored.

A project/snapshot/kind advisory transaction lock serializes insertion and activation. Exact
content-digest replay is idempotent, a different retained version can become active, and conflicting
schema, normalized digest, source identity, scope, or manifest resource fails closed. A
fixed-search-path trigger makes every artifact field except active state immutable; runtime updates
are column-limited. Retrieval verifies the authorized manifest before selecting and reconstructing
one active strict domain projection.

ADR 0025, the product contract, and threat model document minimization, relational binding, and
forward-only recovery. Real PostgreSQL tests cover all three artifact kinds, exact replay, catalog
version switching, absent-resource rejection, restart round trips, foreign-project/private-viewer
denial, immutable privileges, trigger enforcement, and absence of prohibited fixture payloads. The
complete repository gate passes with 826 default tests plus 17 mandatory real-PostgreSQL tests,
strict typing for 218 source files, dependency/provenance validation for 93 entries, architecture
validation for 118 product Python files, schema validation, and the isolated installed-package MCP
workflow. No dependency was added. PostgreSQL dbt project-index parity is complete.

#### Issue 21R — Verified live episodic personal-to-team import — Complete

The current bounded issue imports the live portion of one canonical personal episodic export into
one exact authorized team task scope. Scope-derived identities must be deterministically rebased,
all minimized payload and evidence fields must remain semantically identical, existing target
state must be either an exact resumable subset or rejected before mutation, and completion must be
proved by target counts and the canonical target digest. Retrying an interrupted import must
converge without duplicates.

This issue does not claim the Milestone 9 import exit gate: payload-free expiry, purge, and deletion
tombstones require a subsequent storage-level import path that preserves anti-resurrection
controls. A bundle containing any such tombstone must fail explicitly rather than silently omit
lifecycle state. This issue adds no remote service, OAuth, backup, quota, dashboard, source
governance, or new dependency.

Implemented a storage-neutral live episodic import service over the existing canonical export and
repository contracts. It rebuilds task events, candidate batches, reviews, governance actions, and
derived revisions through their domain factories in dependency order. Scope-derived identities
are deterministically rebased into the target task while minimized claims, evidence, retention,
timestamps, decisions, extraction provenance, and correction semantics remain intact.

The service exports the authorized target before mutation and accepts only an empty target or an
exact typed subset of the expected projection. Existing unrelated state fails before a write.
Repository idempotency makes an interrupted replay resumable; a second export must match every
typed object and exact category count before success. The result returns the validated source
digest and target repository's canonical digest. Equivalent instants with backend-normalized time
zone offsets compare as typed datetimes rather than weakening either bundle's own canonical hash.
All adapter failures become one payload-free outcome. Lifecycle-bearing bundles fail before target
access and remain the next bounded issue.

ADR 0026, the product contract, and threat model document identity rebasing, resumable subset
preconditions, digest verification, and the tombstone boundary. Unit tests prove count/hash parity,
idempotency, conflicting-target rejection, lifecycle rejection, sanitized interruption, and retry
convergence. A real PostgreSQL test imports live SQLite state, verifies restart-stable target
counts/hash and exact retry, and denies a private-project viewer. The complete repository gate
passes with 829 default tests plus 18 mandatory real-PostgreSQL tests, strict typing for 219 source
files, dependency/provenance validation for 93 entries, architecture validation for 119 product
Python files, schema validation, and the isolated installed-package MCP workflow. No dependency or
schema migration was added.

#### Issue 21S — Verified episodic lifecycle-tombstone import — Complete

The current bounded issue completes episodic personal-to-team import for payload-free memory and
task expiration, purge, and deletion state. Imported tombstones must be deterministically rebased,
stored atomically behind forced RLS without manufacturing deleted payloads or bypassing native
lifecycle triggers, participate in ordinary anti-resurrection checks and exact export, and retain
source/target identity plus source-digest provenance. Exact retry must be idempotent and conflicting
mapping must fail closed.

This issue does not add other export categories, a remote service, OAuth, backup propagation,
quota, dashboard, source governance, or a new dependency.

Implemented PostgreSQL schema version 15 with one canonical imported-lifecycle table for exact
memory/task expiration, purge, and deletion projections. Each row retains full target task scope,
kind, deterministic target identity, retained source identity, validated source-bundle digest,
strict payload-free JSON, and import time. The table forces RLS, grants runtime `SELECT`/`INSERT`
only, and cannot retain a summary, claim, review, evidence, or manufactured source payload.

Orphan source event and memory identities are mapped with fixed Mnemo UUIDv5 namespaces over the
canonical target scope and retained source identity. Expiration/purge identities and scope-derived
deletion actions are then rebuilt through normal domain factories, including dependent source
deletions, so complete bundle validation proves every relationship. Live identities continue
through their canonical factories.

The lifecycle repository validates exact scope, source/target counts, native-target absence, and
source/target mapping uniqueness before inserting the complete tombstone set in one transaction.
Exact replay is idempotent and competing mapping rolls back. Ordinary repeatable-read export merges
native and imported tombstones behind RLS, and ordinary event/candidate writes consult both stores
before mutation. The resumable application import reports success only after the complete target
export matches every typed object, count, and target digest.

ADR 0027, the product contract, and threat model document the dedicated-table decision,
minimization, mapping, and recovery. Unit tests prove complete lifecycle rebasing, count/hash
parity, and replay. Real PostgreSQL tests prove atomic v14-to-v15 rollback/retry, complete
SQLite-to-team transfer, restart export parity, exact replay, private-viewer denial, payload
minimization, immutable privileges, and imported deletion anti-resurrection. The complete
repository gate passes with 830 default tests plus 18 mandatory real-PostgreSQL tests, strict
typing for 219 source files, dependency/provenance validation for 93 entries, architecture
validation for 119 product Python files, schema validation, and the isolated installed-package MCP
workflow. No dependency was added. Episodic personal-to-team import now preserves verified live
and lifecycle counts and hashes; other export categories remain separate requirements.

#### Issue 21T — Verified checkpoint-history personal-to-team import — Complete

The current bounded issue adds one portable, integrity-verified bundle for every checkpoint
aggregate, immutable revision, and lifecycle event in an exact personal task scope, then imports
that state atomically into one authorized team task scope. Checkpoint, revision, lifecycle-event,
content, evidence, status, and timestamp identity must be preserved while only the enclosing scope
is rebased. Canonical ordering and one SHA-256 digest must make tampering and incomplete history
fail closed. A target must be empty or exactly identical; conflicting state must be rejected before
mutation, and exact retry must be idempotent. Completion requires verified SQLite-to-PostgreSQL
counts and canonical target hash behind forced RLS.

Checkpoint source observations remain links to rebuildable source-structure projections and are
not portable durable checkpoint payload. This issue adds no approved-event or knowledge export,
remote service, OAuth, backup propagation, quota, dashboard, source governance, or dependency.

Implemented the strict `mnemo.checkpoint-export.v1` domain bundle for every aggregate, immutable
revision, and deterministic lifecycle event in one exact task scope. Canonical identity ordering,
contiguous predecessor validation, aggregate-current-state binding, exact event/revision matching,
canonical UTF-8 JSON, and one SHA-256 digest reject incomplete, duplicate, cross-scope,
non-canonical, or tampered history. SQLite and Reference exports are scope-first and restart stable.

The transfer service preserves checkpoint, revision, event, content, evidence, lifecycle status,
and timestamp identities while rebasing only the explicit target scope. Reference and PostgreSQL
targets accept only an empty or already-identical target. PostgreSQL validates the exact
source/target rebase, inserts all aggregates, revisions, lifecycle events, and normal deterministic
outbox jobs in one forced-RLS transaction, reconstructs the target bundle before commit, and makes
exact retry idempotent. The application independently exports before and after import and returns
verified category counts plus strict source and target digests. Checkpoint source observations are
excluded because their referenced source snapshots are rebuildable target-specific projections.

ADR 0028, the product contract, and threat model record the portable-format, identity, RLS,
conflict, and residual deletion boundaries. Domain/Reference tests cover tamper and duplicate
rejection, scope-only rebasing, identity preservation, conflicting target rejection, and replay.
SQLite tests prove full history, scope isolation, and restart durability. A real PostgreSQL test
proves SQLite-to-team transfer, exact identities/counts/hashes after restart, idempotent replay,
normal outbox insertion, and private-project viewer denial. The complete repository gate passes
with 833 default tests plus 19 mandatory real-PostgreSQL tests, strict typing for 222 source files,
dependency/provenance validation for 93 entries, architecture validation for 121 product Python
files, schema validation, and the isolated installed-package MCP workflow. No schema migration or
dependency was added. Approved-event and knowledge transfer remain separate requirements.

#### Issue 21U — Verified approved-event personal-to-team import — Complete

The current bounded issue adds one portable, integrity-verified bundle for approved episodic
events, immutable correction/retraction governance, and complete pin-action history in one exact
task scope, then imports that state atomically into one authorized team task scope. The import must
preserve live payload and evidence, preserve erased retraction state without reconstructing deleted
payload, rebase deterministic identities to the target scope, reject partial or conflicting state,
and make exact retry idempotent. Completion requires verified SQLite-to-PostgreSQL counts and
canonical hashes behind forced RLS.

This issue adds no knowledge export, remote service, OAuth, backup propagation, quota, dashboard,
source governance, or dependency.

Implemented the strict `mnemo.approved-event-export.v1` domain bundle for every retained approved
event, immutable correction/retraction action, and ordered pin action in one exact task scope. The
bundle validates deterministic identities, canonical event/governance order, contiguous pin order,
unique keys, correction relationships and kind preservation, acyclic correction chains, final
governed pin state, erased retraction targets, canonical UTF-8 JSON, and one SHA-256 digest. SQLite
and Reference exports are scope-first and restart stable.

The transfer service rebuilds retained event, governance, and pin identities in the explicit target
scope. Erased source targets receive deterministic payload-free target identities derived only from
target scope and retained source identity. PostgreSQL schema version 16 adds insert-only import
provenance to the native event, governance, and pin tables and narrowly permits already-erased
retraction and pin history without recreating a summary, source key, or direct event evidence. The
repository validates the complete source-to-target projection, requires an empty or identical
target, inserts all canonical rows and normal outbox jobs in one forced-RLS transaction, and
reconstructs the target bundle before commit. Exact retry is idempotent and ordinary governance
inspection and anti-resurrection include imported retractions.

ADR 0029, the product contract, and threat model document the portable format, payload-erasure,
scope, RLS, provenance, and recovery boundaries. Unit tests prove complete correction/retraction
and pin-history export, tamper and reorder rejection, scope rebasing, conflict rejection, replay,
and SQLite restart durability. Real PostgreSQL tests prove atomic v15-to-v16 rollback/retry,
SQLite-to-team counts and hashes after restart, normal outbox insertion, source/target provenance,
payload absence, anti-resurrection, exact replay, and private-project viewer denial. The complete
repository gate passes with 836 default tests plus 20 mandatory real-PostgreSQL tests, strict typing
for 225 source files, dependency/provenance validation for 93 entries, architecture validation for
123 product Python files, schema validation, and the isolated installed-package MCP workflow. No
dependency was added. Knowledge transfer remains a separate requirement.

#### Issue 21V — Verified knowledge-history personal-to-team import — Complete

The current bounded issue adds one portable, integrity-verified bundle for active knowledge source
state, complete retained document revision/link history, and payload-free deletion records in one
exact project scope, then imports that state atomically into one authorized team project scope.
Scope-derived document identities must be deterministically rebased while revision, content,
frontmatter, section, link, timestamp, source-kind, and deletion metadata remain exact. Deleted note
payload must not be reconstructed. Rebuildable search and embedding projections are excluded.

This issue adds no remote service, OAuth, backup propagation, quota, dashboard, team correction
governance, source approval, or dependency.

Implemented the strict `mnemo.knowledge-export.v1` domain bundle for active source pointers, every
retained immutable document revision, and minimal payload-free deletion state in one exact project
scope. The bundle retains source kind, path, content digest, title, frontmatter, ordered sections,
declared Markdown/Obsidian links, revision identity, predecessor, number, timestamp, and last sync
time. It validates canonical order, unique source/path/revision identities, complete contiguous
predecessor chains, current-pointer binding, active/deleted separation, canonical UTF-8 JSON, and
one SHA-256 digest. SQLite and Reference exports are scope-first and restart stable.

The transfer service deterministically rebases source document identity while preserving revision
IDs and all retained content and provenance. PostgreSQL schema version 17 records source identity,
source bundle digest, and import time on native active knowledge sources. Already-deleted notes use
one forced-RLS payload-free projection containing only source/target identity, path, digest,
deletion time, and import provenance; no placeholder source creation time, revision, title,
frontmatter, section, or link is created. A database trigger blocks resurrection. PostgreSQL
requires an unsynchronized empty or already-identical target, writes active history, deletion
state, and last-sync state atomically, and reconstructs the target bundle before commit. Search and
embedding data remain excluded rebuildable projections.

ADR 0030, the product contract, and threat model document retained-history, erasure, scope, RLS,
provenance, untrusted-content, and recovery boundaries. Unit tests prove revision/link history,
renamed paths, tamper rejection, payload minimization, rebasing, conflict rejection, replay, and
SQLite restart durability. Real PostgreSQL tests prove atomic v16-to-v17 rollback/retry,
SQLite-to-team counts and hashes after restart, native retrieval/search, exact revision identity,
source/target provenance, absent deleted payload and embeddings, anti-resurrection, replay, and
private-project viewer denial. The complete repository gate passes with 839 default tests plus 21
mandatory real-PostgreSQL tests, strict typing for 228 source files, dependency/provenance
validation for 93 entries, architecture validation for 125 product Python files, schema validation,
and the isolated installed-package MCP workflow. No dependency was added. Structural projection
transfer remains separate from durable personal state.

#### Issue 21W — Checkpoint expiry parity — Complete

The current bounded issue makes the existing canonical `expired` checkpoint status operational
across the application service, Reference, SQLite, and PostgreSQL repositories, lifecycle events,
and strict checkpoint transfer bundle. Expiry creates one immutable terminal revision that reuses
the current bounded content and evidence, removes the checkpoint from current selection, remains
idempotent for the same expected revision, and preserves complete portable audit provenance.

This issue adds no physical checkpoint erasure, scheduled retention policy, remote service, OAuth,
backup propagation, quota, dashboard, team correction governance, source approval, or dependency.

Implemented `ExpireCheckpoint` as an application command that reuses the current immutable
handoff content and evidence. Reference, SQLite, and PostgreSQL repositories append a terminal
`expired` revision and deterministic `checkpoint_expired` lifecycle event atomically, compare the
expected predecessor, return the committed result for an identical retry, reject competing or
post-terminal writes, and exclude the expired aggregate from current selection. The strict
checkpoint export/import contract now validates and preserves expired histories.

SQLite migration 0029 transactionally rebuilds only the checkpoint lifecycle ledger constraint,
preserves existing sequence identities, and recreates its scoped indexes and guards. PostgreSQL
migration 0018 extends the three closed checkpoint constraints and fixed-search-path event guard
while retaining forced RLS and immutable revision/event privileges. ADR 0031, the product contract,
SQLite profile, and threat model distinguish logical expiry from scheduled retention and physical
erasure. Backend-neutral, application, transfer, SQLite migration, and real PostgreSQL tests cover
content/evidence preservation, idempotency, selection removal, exact portability, runtime scope,
and injected rollback/retry. The complete repository gate passes with 843 default tests plus 21
mandatory real-PostgreSQL tests, strict typing for 228 source files, dependency/provenance
validation for 93 entries, architecture validation for 125 product Python files, schema validation,
and the isolated installed-package MCP workflow. No dependency was added.

#### Issue 21X — Physical checkpoint erasure — Complete

The current bounded issue adds one explicit user-authored exact-task deletion action across the
Reference, SQLite, and PostgreSQL checkpoint repositories. It writes a deterministic payload-free
tombstone before atomically erasing the aggregate, every revision and retained evidence payload,
lifecycle events, source observations, and checkpoint-lifecycle outbox jobs under Mnemo's control.
Exact retry must be idempotent; competing actions, reused action keys, missing or cross-scope
targets, direct deletion without a tombstone, and resurrection must fail closed.

This issue adds no portable tombstone transfer, scheduled retention, remote service, OAuth,
backup propagation, quota, dashboard, team knowledge governance, source approval, or dependency.

Implemented the strict `CheckpointDeletion` domain action and application service with one
deterministic identity derived from exact task scope, checkpoint identity, and caller action key.
Reference, SQLite, and PostgreSQL repositories insert its payload-free user tombstone before
deleting the aggregate, every immutable revision and evidence payload, lifecycle events, source
observations, and checkpoint-lifecycle outbox jobs atomically. SQLite additionally removes legacy
checkpoint rows and newly orphaned normalized evidence. Exact retry is idempotent; different or
reused actions, missing and cross-scope targets, and resurrection fail closed.

SQLite migration 0030 adds scoped tombstones plus direct-delete and resurrection guards.
PostgreSQL migration 0019 adds the forced-RLS tombstone, fixed-search-path guards, and narrowly
controlled delete privileges; the checkpoint outbox guard permits deletion only after a matching
tombstone. ADR 0032, the product contract, SQLite profile, and threat model distinguish physical
erasure from expiry, scheduled retention, portable deletion transfer, backups, and external
copies. Focused tests cover serialization tampering, payload/job/evidence/observation removal,
scope and action conflicts, exact replay, direct-delete rejection, anti-resurrection, and injected
migration rollback/retry. The complete repository gate passes with 846 default tests plus 21
mandatory real-PostgreSQL tests, strict typing for 232 source files, dependency/provenance
validation for 93 entries, architecture validation for 127 product Python files, schema
validation, and the isolated installed-package MCP workflow. No dependency was added.

#### Issue 21Y — Verified checkpoint-deletion transfer — Complete

The current bounded issue extends portable checkpoint history with payload-free deletion
tombstones while preserving compatibility with existing version-1 bundles. A personal deletion
must rebase deterministically into one authorized team task scope without reconstructing erased
checkpoint content, and the target must export the same typed tombstone state with verified counts
and canonical hashes. Exact retry must be idempotent; tampering, partial or conflicting target
state, cross-scope access, and resurrection must fail closed.

This issue adds no scheduled checkpoint retention, remote service, OAuth, backup propagation,
quota, dashboard, team knowledge governance, source approval, or dependency.

Implemented `mnemo.checkpoint-export.v2` with one canonical payload-free deletion collection beside
live checkpoint history. The bundle requires live/deleted identity disjointness, unique deletion
identities, checkpoint identities and action keys, canonical ordering, exact task scope, and one
digest over all state. The parser still accepts the exact version-1 field set and digest, with no
invented deletion history. New Reference, SQLite, and PostgreSQL exports include every scoped
tombstone and no erased checkpoint payload.

The transfer service preserves checkpoint identity, actor, action key, and deletion time while
deterministically deriving the target deletion identity from the authorized target scope.
Reference and PostgreSQL targets accept only empty or already-identical complete state. PostgreSQL
schema version 20 adds complete source deletion/digest/import-time provenance to native forced-RLS
tombstones and permits a validated payload-free import without manufacturing an aggregate,
revision, evidence, event, observation, or job. Live rows, normal outbox jobs, and tombstones are
written and re-exported in one transaction; ordinary anti-resurrection remains effective.

ADR 0033, the product contract, and threat model record format compatibility, minimization,
identity rebasing, RLS, provenance, and residual backup/remote boundaries. Domain/Reference/SQLite
tests cover version-1 compatibility, version-2 round trips, tampering, duplicate and overlapping
state, rebasing, conflict rejection, restart durability, and exact replay. Real PostgreSQL tests
cover atomic v16-to-v20 rollback/retry, verified SQLite transfer, exact counts and hashes,
source/target provenance, absent deleted payload, anti-resurrection, replay, and private-project
viewer denial. The complete repository gate passes with 847 default tests plus 21 mandatory real-
PostgreSQL tests, strict typing for 232 source files, dependency/provenance validation for 93
entries, architecture validation for 127 product Python files, schema validation, and the isolated
installed-package MCP workflow. No dependency was added.

#### Issue 21Z — Scheduled checkpoint retention expiry — Complete

The current bounded issue makes the existing checkpoint expiry lifecycle operational from the
configured personal episodic-retention period. An automatic-memory session start may run one
bounded, exact-task, oldest-first sweep over active checkpoints whose last canonical write is due;
the sweep must use the existing compare-and-swap expiry transition, preserve immutable history and
evidence, remain idempotent after restart, and fail open without blocking the coding agent.

Authorization and exact scope filtering must happen before time ordering. Retrieval must not
silently extend retention, concurrent checkpoint changes must not be expired from a stale
selection, and no physical purge, remote listener, OAuth, backup propagation, quota, dashboard,
team knowledge governance, source approval, or dependency belongs to this issue.

Implemented one storage-independent retention service and oldest-first, exact-task due discovery
for Reference, SQLite, and PostgreSQL repositories. A pass uses the configured 1–3650 day period,
is capped at 100 active checkpoints, rereads the selected aggregate and current revision, and uses
the existing expected-revision expiry transition. Concurrent saves or terminal changes are skipped;
the committed expiry preserves immutable content, evidence, and lifecycle history, while repeated
or restarted passes are idempotent. SQLite compares timestamp values chronologically rather than by
offset-bearing text.

Automatic-memory `SessionStart` now runs one personal retention pass before current-checkpoint
selection. The callback loads the current secret-free setting only when invoked and fails open on
invalid settings, storage failure, or any retention error, so Codex or Claude Code remains usable.
No retrieval path updates the canonical write time. PostgreSQL exposes the same scope-first due
query behind existing forced RLS without adding a listener or unauthenticated scheduler.

ADR 0034, the product contract, user guide, README, and threat model record the last-write clock,
bounded execution, stale-selection defense, logical-expiry boundary, and residual backup/team
scheduling risk. Focused tests cover exact-scope cutoff selection, evidence preservation, restart
idempotence, concurrent revision, installed-hook configuration, and fail-open behavior. The complete
repository gate passes with 856 default tests plus 21 mandatory real-PostgreSQL tests, strict typing
for 234 source files, dependency/provenance validation for 93 entries, architecture validation for
128 product Python files, schema validation, and the isolated installed-package MCP workflow. No
dependency or schema migration was added.

#### Issue 21AA — Authenticated team MCP request boundary — Complete

The current bounded issue makes the existing PostgreSQL parity usable through one authenticated,
loopback-hosted Streamable HTTP MCP composition intended for deployment behind an HTTPS reverse
proxy. A pinned asymmetric JWT verifier must validate signature, exact issuer, resource audience,
expiry, subject UUID, client identity, and required scopes before MCP dispatch. The verified subject
becomes the PostgreSQL principal; the exact request workspace selects one principal/workspace-bound
repository composition, so existing authorization policy and forced RLS still execute before every
storage read or mutation.

The team port must compose existing checkpoint, episodic, knowledge, source-structure, dbt, skill,
and procedure retrieval rather than redesigning them. It must require explicit UUID scope, retain no
token or database secret, expose no unauthenticated tool route, bind loopback by default, and reject
invalid issuer/audience/signature/scope/subject before creating a repository. This issue adds no
authorization server, dynamic client registration, JWKS network fetch, non-loopback listener, team
knowledge correction/source approval, backup, quota, dashboard, or new memory behavior.

Implemented a pinned asymmetric JWT verifier for the optional team profile. It accepts only
configured `RS256`, `PS256`, or `ES256`, exact HTTPS issuer and resource audience, valid `exp` and
`iat`, a canonical UUID subject, bounded client identity, and all configured scopes. Signature,
claim, or scope failure returns no access token and creates no repository. The verifier retains only
minimized issuer metadata in request context and never stores or logs the bearer token.

The authenticated MCP port derives its database principal exclusively from the verified subject and
requires one explicit canonical request workspace before constructing a port. Its PostgreSQL
factory composes the already-complete checkpoint, approved-event, episodic, knowledge, source,
dbt, procedure, and skill repositories into the existing unified context and checkpoint service.
Every repository remains bound to that principal/workspace and executes existing deterministic
authorization plus forced RLS; private viewers and foreign workspaces receive no checkpoint.

The Streamable HTTP server is stateless, bearer-protected across the route, and fixed to
`127.0.0.1`; advertised issuer and resource URLs require HTTPS for a later controlled proxy. ADR
0035, the product contract, dependency register, architecture rules, and threat model record the
identity binding, no-token-retention rule, dependency/reversal boundary, and residual TLS,
key-rotation, audit, and operations work. The optional `team` extra now declares the already locked
and reviewed `pyjwt==2.13.0` dependency directly; no new transitive package or schema migration was
added.

Security tests cover missing authentication, alternate keys, issuer/audience/scope/subject/client
tampering, exact principal/workspace binding, unauthenticated HTTP, and loopback/stateless settings.
The real PostgreSQL service test covers owner success plus private-viewer and foreign-workspace
non-disclosure. The complete repository gate passes with 868 default tests plus 22 mandatory real-
PostgreSQL tests, strict typing for 238 source files, dependency/provenance validation for 93
entries, architecture validation for 131 product Python files, schema validation, and the isolated
installed-package personal MCP workflow.

#### Issue 21AB — Secret-safe team service deployment — Complete

The current bounded issue makes the authenticated loopback team MCP server operable without placing
database passwords or bearer material in command arguments, repository files, logs, or diagnostic
output. A dedicated installed entry point must load strict non-secret settings from environment,
read the database password from one owner-only regular file, read the OAuth public key from one
bounded regular file, require certificate-verified PostgreSQL TLS, and bind only to loopback. It
must fail closed with stable content-free errors and support restart-based public-key rotation.

Deployment documentation must define the HTTPS reverse-proxy trust boundary, authorization-header
forwarding, key/password file permissions, health/start/stop/rotation checks, and the prohibition on
direct non-loopback Mnemo binding. This issue adds no bundled proxy, authorization server, client
secret storage, automatic certificate issuance, team knowledge governance, backup, quota,
dashboard, or new memory behavior.

Implemented the dedicated `mnemo-memory-team` installed entry point. Strict environment parsing
accepts only bounded non-secret host, port, database, OAuth, scope, and public-resource metadata.
The database password must come from an absolute owner-owned mode-0600 regular file; the OAuth key
must come from an absolute owner-owned regular file with no group/other write bit. Both use
descriptor-verified no-follow opens, byte bounds, and UTF-8 validation, rejecting symlinks,
relative paths, unsafe modes, and oversized or malformed content without including values or paths
in errors.

Every optional-team PostgreSQL connection now uses the system trust store, certificate and hostname
verification, TLS 1.2 or newer, a five-second connection timeout, and no plaintext fallback. The
authenticated stateless MCP upstream remains fixed to `127.0.0.1`; there is no host configuration.
The packaged deployment runbook and non-secret environment example define an HTTPS-only reverse
proxy, bearer-header forwarding, direct-port isolation, content-free logging, start/stop checks,
PostgreSQL TLS failure, and atomic-file/restart password and public-key rotation with rollback.

ADR 0036, the product contract, threat model, release archive verifier, installed-package verifier,
and build source include record and enforce the deployment boundary. Release verification now
requires both console entry points, both team runtime modules, the OAuth verifier, and team
deployment documentation. Security tests cover valid inert startup, unsafe permissions, symlinks,
invalid configuration, content-free failures, and the exact certificate-verifying database TLS
context. The complete repository gate passes with 882 default tests plus 22 mandatory real-
PostgreSQL tests, strict typing for 240 source files, dependency/provenance validation for 93
entries, architecture validation for 132 product Python files, schema validation, and installed-
package workflow verification. No new dependency or schema migration was added.

#### Issue 21AC — Team knowledge source governance — Complete

The current bounded issue makes shared PostgreSQL knowledge explicitly attributable and reviewable.
Every team source must retain its creating principal as owner and every immutable revision must
retain its author. Existing predecessor compare-and-swap remains the conflict boundary: two
corrections based on the same current revision cannot both commit. Team retrieval must exclude a
source until a project maintainer, workspace administrator, or workspace owner records one explicit
approval against its exact current revision; approval remains attached to the stable source identity
across later conflict-checked revisions. Reused action keys, stale expected revisions, insufficient
roles, foreign scopes, and deleted sources must fail closed, while an exact retry is idempotent.

The issue will expose bounded, content-free source status and approval operations only on the
authenticated team MCP surface. It adds no automatic merge, prose contradiction inference, source
rejection workflow, general knowledge editor, personal-mode approval, backup, quota, dashboard,
model call, or dependency.

Implemented immutable team source ownership and revision authorship backed by the authenticated
PostgreSQL principal. Pre-v21 rows are marked as unauthenticated legacy scope-owner attribution
rather than receiving invented actor provenance. New sources are pending and all content-bearing
document, procedure, skill, lexical, and vector retrieval paths require one immutable source
approval. Project maintainers, workspace administrators, and workspace owners can approve an exact
live revision through forced RLS; the MCP mutation additionally requires the dedicated
`mnemo:knowledge:approve` OAuth scope. Approval follows the stable source identity across later
revisions, while the existing predecessor compare-and-swap rejects competing corrections.

The authenticated team MCP surface now exposes bounded `list_knowledge_sources` metadata and
`approve_knowledge_source`; personal MCP retains its existing five tools. Approval identity and
caller keys are deterministic, exact retry is idempotent, and changed/reused actions, stale
revisions, contributors/viewers, foreign scopes, and deleted sources fail closed. PostgreSQL
migration 0021 is additive, forced-RLS, fixed-search-path, and rollback-atomic. ADR 0037, the product
contract, deployment guide, and threat model record stable-source approval, explicit provenance,
OAuth scope, deletion behavior, and residual semantic-conflict/backup boundaries.

Focused unit and security tests cover serialization, metadata minimization, transport scope,
dedicated OAuth permission, and retries. The mandatory real PostgreSQL suite covers migration
rollback, pending-source exclusion, maintainer approval, contributor/viewer denial, stale approval,
stable ownership, authenticated authorship, competing corrections, approved retrieval, vector
handling, and payload deletion. The complete repository gate passes with 886 default tests plus 22
mandatory real-PostgreSQL tests, strict typing for 243 source files, dependency/provenance
validation for 93 entries, architecture validation for 134 product Python files, schema validation,
and installed-package workflow verification. No dependency was added.

#### Issue 21AD — Verified team database backup and restore drill — Complete

The current bounded issue adds an operator-invoked, non-overwriting PostgreSQL custom-format backup
and restore-drill workflow for the optional team profile. Backup creation must use the existing
owner-only password-file boundary, certificate-verified database transport, a private operator-
selected directory, one atomic mode-0600 artifact, and a digest-bound content-free manifest. The
native backup must include all current `mnemo_team` canonical, projection, governance, audit,
outbox, tombstone, and migration state without logging credentials or payloads.

A restore drill must accept only a verified Mnemo backup, reject the live database and a nonempty
target, restore into one explicitly provisioned target database in a single transaction, then
compare the schema ledger and per-table row counts with the digest-bound manifest before reporting a
bounded recovery duration. Partial backup/restore artifacts, command output, and target contents
must not be returned on failure. This issue adds no scheduled backups, remote object storage,
encryption-key manager, deletion-triggered backup rotation, retention pruning, quota, dashboard,
or dependency.

Implemented the installed `mnemo-memory-team-admin` entry point over an original versioned manifest,
storage-neutral backup service, and native PostgreSQL adapter. Backup uses a dedicated
non-superuser `BYPASSRLS` role, one exported repeatable-read snapshot, PostgreSQL 17.10 custom
format, private passfiles, certificate-verified application connections, an owner mode-`0700`
directory, and atomic non-overwriting mode-`0600` archive and manifest publication. The canonical
manifest binds archive identity, SHA-256, size, schema version, and a sorted count for every table
in `mnemo_team`; command vectors, results, errors, and logs disclose no credential or payload.

Restore requires an explicitly provisioned non-live database with the approved `vector` extension
and no `mnemo_team` schema. It verifies file ownership and permissions, archive digest and native
structure, restores in one transaction, and succeeds only after exact migration-ledger and
per-table count parity. ADR 0038, the product contract, deployment runbook, dependency register,
release-package verifier, and threat model record the dedicated authority, extension, encrypted-
storage, deletion-propagation, and recovery boundaries.

The complete repository gate passes with 906 default tests plus 23 mandatory real-PostgreSQL tests,
including a native dump/restore drill, strict typing for 253 source files, dependency/provenance
validation for 94 entries, architecture validation for 140 product Python files, schema validation,
and installed-package workflow verification. No Python dependency, schedule, remote storage,
encryption-key manager, retention pruning, deletion-triggered backup rotation, quota, or dashboard
was added.

#### Issue 21AE — Team backup deletion propagation — Complete

The current bounded issue adds an explicit operator reconciliation command for backups retained in
one Mnemo-managed private directory. The PostgreSQL adapter must compare each verified manifest's
monotonic erasure-ledger table counts with one current authorized database inventory. A backup that
predates any knowledge, checkpoint, approved-event, task-event, episodic-memory, or retention purge
must have its archive deleted before its manifest so an interruption cannot leave an apparently
valid payload-bearing backup. Current backups remain byte-identical.

The operation must use the dedicated backup role and verified database transport, scan only a
bounded set of strict Mnemo manifest names, reject symlinks and unsafe permissions, never follow an
artifact path outside the selected directory, and return only removed file/byte counts. Exact
retry must be safe, and malformed or unverified candidates must fail closed without deleting a
valid archive. This issue adds no automatic scheduler, arbitrary retention-age policy, remote
object-store deletion, external-copy recall, encryption-key manager, quota, or dashboard.

Implemented `mnemo-memory-team-admin prune-deleted` over version-2 backup manifests. Each new
manifest binds a sorted snapshot count for all eleven monotonic erasure ledgers; the two mixed
governance tables count retractions only, so corrections do not invalidate a recovery point. The
explicit command validates a bounded private directory and every strict-name manifest/archive,
loads one current whole-team inventory through the dedicated backup role, rejects ledger
regression, and removes only backups whose erasure watermark is stale. Version-1 manifests remain
readable and are conservatively stale after any erasure.

Deletion is archive-first with directory fsync before manifest deletion and a second fsync. Exact
retry safely removes an orphaned stale manifest, current backups remain byte-identical, and a
malformed candidate, substituted digest, symlink, unsafe permission, missing current archive, or
regressed database blocks valid deletion. Results contain only removed backup, file, and byte
counts. ADR 0039, ADR 0038, the product contract, deployment runbook, and threat model record the
managed-directory guarantee and the unresolved external-copy boundary.

Focused tests cover exact stale/current selection, filtered erasure inventory, v1 compatibility,
interruption retry, invalid candidates, symlinks, unsafe permissions, ledger regression, and
unchanged current bytes. The mandatory real-PostgreSQL drill backs up live knowledge payload,
tombstones and physically erases it, creates a post-erasure recovery point, removes only the old
payload-bearing archive, and retains the current archive. The complete repository gate passes with
910 default tests plus 23 mandatory real-PostgreSQL tests, strict typing for 253 source files,
dependency/provenance validation for 94 entries, architecture validation for 140 product Python
files, schema validation, and installed-package workflow verification. No dependency, scheduler,
age-based retention policy, remote store, key manager, quota, or dashboard was added.

#### Issue 21AF — Authenticated team request rate limits — Complete

The current bounded issue adds one process-local fixed-window request limiter after OAuth subject
and explicit workspace validation but before repository composition. Limits must be strict positive
deployment settings, isolate exact principal/workspace pairs, use a monotonic clock, reset after the
window, remain safe under concurrent calls, and cap tracked identities to prevent limiter-state
exhaustion. Missing/invalid authentication and scope must not consume a bucket; denied calls must
not reach PostgreSQL and must return one payload-free stable code.

This issue adds no distributed counter, proxy configuration, storage quota, model budget,
dashboard, alert, dependency, or unauthenticated network surface.

Implemented a concurrency-safe fixed-window limiter keyed by exact typed OAuth principal and
workspace. Authentication, dedicated tool scope, and canonical workspace parsing run first; the
limiter then denies before repository composition with only `MNEMO_RATE_LIMITED`. Strict deployment
settings control requests, seconds, and tracked identities. A monotonic clock resets windows,
expired keys are reclaimed before capacity admission, and the map cannot exceed its configured
bound. The documented supported profile is exactly one Mnemo process; restart resets state and a
multi-process service requires a later shared-counter design.

ADR 0040, the product contract, deployment environment/runbook, and threat model record ordering,
single-process scope, proxy responsibilities, and residual distributed-limit risk. Focused tests
cover scope isolation, reset, capacity reclaim, invalid settings, concurrent contention, missing
authentication, and factory non-invocation on denial. The complete repository gate passes with 918
default tests plus 23 mandatory real-PostgreSQL tests, strict typing for 255 source files,
dependency/provenance validation for 94 entries, architecture validation for 141 product Python
files, schema validation, and installed-package workflow verification. No dependency, distributed
counter, storage quota, model budget, dashboard, or alert was added.

#### Issue 21AG — Durable team checkpoint quotas — Complete

The current bounded issue adds a PostgreSQL-enforced workspace quota for the only agent-facing
content mutation, `save_checkpoint`. Each workspace must have an explicit administrator-provisioned
maximum checkpoint aggregate count, revision count, and retained canonical payload bytes. The
database must serialize competing admissions, count only that exact workspace before inserting a
new aggregate or revision, preserve idempotent retries that add no row, and fail before checkpoint,
evidence, lifecycle, or outbox partial state commits.

The authenticated service must translate quota denial to one payload-free stable code without
weakening forced RLS. Existing workspaces without a quota fail closed for remote checkpoint writes;
reads remain available. This issue adds no quota for an unexposed content-ingestion surface,
billing, model budget, dashboard, alert, dependency, or general quota framework.

Implemented PostgreSQL schema version 22 with administrator-owned per-workspace aggregate-count,
retained-revision-count, and canonical-payload-byte limits. Fixed-search-path security-definer
triggers verify the transaction workspace, serialize admissions on the exact quota row, and reject
an absent or exceeded quota before mutation with private SQLSTATE `MZQ01`. The runtime role and
`PUBLIC` have no quota-table access. Storage and application boundaries translate denial into the
payload-free `MNEMO_QUOTA_EXCEEDED` service code, while reads remain available.

The migration runner exposes strict positive-bigint administrative provisioning, and the team
deployment runbook documents schema-administrator setup, usage inspection, limit reduction, and
recovery. ADR 0041 and the product and threat contracts define the atomicity, concurrency, byte
accounting, privilege, and residual load-risk boundaries. The mandatory real-PostgreSQL test covers
unprovisioned fail-closed behavior with zero partial aggregate/revision/lifecycle/outbox state,
runtime privilege denial, two simultaneous aggregate admissions with one winner, aggregate,
revision, and byte limits, recovery after quota adjustment, and idempotent terminal replay.

#### Issue 21AH — Fresh registered-project structure on MCP startup — Complete

The current bounded issue fixes a demonstrated automatic-memory workflow failure: a freshly
started MCP process can resolve the registered project scope but may serve an old active source
snapshot when the client lifecycle hook did not run. The MCP composition root must perform one
fail-open syntax-only refresh for its exact registered project before serving requests, without
making MCP availability depend on parsing success. A fresh process must then retrieve a file and
symbol added after the previously active snapshot without UUID arguments.

This issue changes no source parser, ranking rule, stored source contract, dependency, background
watcher, team quota, or general indexing schedule. Issue 21AG remained paused until this
prerequisite passed the full repository gate.

Implemented one fail-open registered-project source refresh in the local MCP composition root by
reusing the existing bounded offline parser and scoped projection repository. Checkpoint
co-observation now reuses the same refresh boundary. Parser, filesystem, or projection failure
returns no snapshot and never prevents MCP startup.

A real stdio regression starts from a deliberately stale snapshot, adds a new source class, launches
a fresh MCP process, and retrieves its exact current path and symbol without UUID arguments. Focused
failure-isolation coverage rejects an oversized file without a snapshot or raised startup error. The
complete repository gate passes with 919 default tests plus 24 mandatory real-PostgreSQL tests,
strict typing for 255 source files, dependency/provenance validation for 94 entries, architecture
validation for 141 product Python files, schema validation, and installed-package workflow
verification. The verified checkout was reinstalled and reconnected; explicit refresh produced
snapshot `23efbcbf-110f-5568-83c4-1fffe0528eba` with 326 files, 4,787 symbols, and 28,793 edges, and
Mnemo then retrieved the previously missing `PostgreSQLCheckpointRepository` with exact path, line,
methods, and source provenance.

#### Issue 21AI — Content-free team operations dashboard and alerts — Complete

The current bounded issue adds one operator-invoked, whole-team operational snapshot through the
installed administration entry point. It must report only aggregate service state needed to operate
the existing team runtime: schema version, workspace/project/membership counts, checkpoint quota
coverage and utilization, and durable outbox backlog/lease age. Strict operator thresholds must
produce stable content-free alert codes and a nonzero check result suitable for a supervisor.

The snapshot must use the existing dedicated backup/operations credential, never the forced-RLS MCP
runtime credential, and must not return tenant identities, memory payloads, paths, job bodies,
credentials, exception details, or request data. This issue adds no public route, metrics server,
background scheduler, notification transport, model budget, distributed counter, dependency, or
general analytics framework.

Implemented `mnemo-memory-team-admin status` and `check` over one read-only whole-team PostgreSQL
snapshot using the existing dedicated backup/operations credential. Output is bounded canonical
JSON containing only schema support, aggregate workspace/project/active-membership totals,
checkpoint quota coverage and maximum utilization, and durable outbox backlog, lease, failure, and
oldest-age counters. No tenant identity, path, job body, canonical payload, credential, request, or
database exception is returned.

Strict invocation thresholds generate an ordered closed alert set for schema mismatch, missing,
high, or exceeded quota, high backlog or age, expired leases, and excessive failures. `status`
returns every valid snapshot; `check` exits 0 when healthy, 1 for active alerts, and 2 for invalid or
unavailable checks. ADR 0042, the product contract, deployment runbook, and threat model define the
credential, scheduling, notification, privacy, and residual-risk boundaries. Focused unit/security
tests cover validation, serialization, alert ordering, CLI exit behavior, TLS/secret reuse, and
content-free failure. The mandatory real-PostgreSQL suite passes 25 tests, including actual quota
and outbox alerts, identity-free output, and denial through the MCP runtime credential. The complete
repository gate passes with 927 default tests plus those 25 mandatory real-PostgreSQL tests, strict
typing for 257 source files, dependency/provenance validation for 94 entries, architecture
validation for 142 product Python files, schema validation, and installed-package verification.

#### Issue 21AJ — Durable per-workspace model budgets — Complete

The current bounded issue protects Mnemo's only optional model task, episodic-candidate extraction,
with an explicit daily workspace budget before every provider attempt. Each workspace/task budget
must independently cap call count, reserved input tokens, reserved output tokens, and reserved
monetary micro-USD. PostgreSQL must serialize competing reservations, use a database-defined UTC
day, fail closed when unprovisioned or exhausted, and reveal no usage or tenant detail to the model
provider or MCP caller.

The existing schema-bound gateway must reserve a trusted configured worst-case charge before each
provider call, including its one malformed-output retry. Budget denial or storage failure must
prevent the provider call and return a stable payload-free model-gateway code. This issue adds no
provider SDK, model endpoint, automatic extraction worker, new MCP tool, billing system, price
discovery, notification transport, second model task, dependency, or general cost framework.

Implemented a strict model-budget domain port and schema-bound gateway reservation before each
episodic-extraction provider attempt. PostgreSQL migration 0023 adds administrator-provisioned
workspace/task limits plus UTC-day usage, an atomic fixed-search-path reservation function, and
execute-only runtime authority. The gateway maps exhaustion and storage failure to stable
payload-free codes and charges its one malformed-output retry separately. Team operations status
now reports only aggregate model-budget coverage and maximum current-day utilization with closed
missing, warning, and exhausted alerts. ADR 0043, the product contract, deployment runbook, and
threat model record provisioning, conservative worst-case charging, retry, privilege, residual
pricing, and retention boundaries.

Focused gateway, operations, and backup-security coverage passes with 45 tests. The mandatory real
PostgreSQL suite passes with 26 tests, including absent-budget denial, runtime table isolation,
three concurrent reservations with exactly two winners, exact UTC usage, foreign-scope denial, and
content-free operations alerts. The complete repository gate passes with formatting, linting,
strict typing, schema, dependency/provenance, architecture, package, and installed-package checks.

#### Issue 21AK — Declared PostgreSQL team load objectives — Complete

The current bounded issue adds one reproducible, opt-in load gate for the existing authenticated
team `get_context` path against a real PostgreSQL server. It must exercise concurrent exact-scope
requests through the production team composition, report zero-error throughput and nearest-rank
p95 latency in canonical content-free JSON, and fail when either declared objective is missed. The
runbook must state the workload, reference environment, limitations, and capacity-planning rule.

The first measured run exposed fresh physical connection creation as the bottleneck: 2,458.889 ms
p95 and 3.711 requests per second. Reusing one connection per tool call improved the result to
590.646 ms p95 and 15.455 requests per second but still missed the objectives. The smallest complete
production fix now composes all repositories in one tool call over one request-owned connection and
returns it to a lazy bounded process pool. The pool defaults to 16, caps configuration at 64, waits
at most five seconds, rolls back before reuse, discards broken connections, and adds no dependency,
cache, background queue, retry, autoscaling, multi-process profile, or endpoint.

The explicit `npm run team-load:check` gate provisions an isolated PostgreSQL 17.10 server and runs
160 authenticated production-composition context requests after eight warm-ups at concurrency
eight. It requires zero errors, nearest-rank p95 no greater than 250 ms, and throughput of at least
30 requests per second. Four consecutive accepted runs had zero errors; the worst p95 was 23.322
ms and the lowest throughput was 266.097 requests per second. ADR 0044, the product contract,
deployment runbook, threat model, and load-objective document record the measured decision,
capacity rule, exclusions, and deployment-specific HTTPS/OAuth rerun requirement. Pool and config
unit/security coverage passes with 24 tests; the complete 26-test real PostgreSQL suite reuses one
physical connection across owner, viewer, and foreign-workspace requests without scope leakage.

#### Issue 21AL — Independent team security review readiness — Complete

The current bounded issue prepares one reproducible, revision-pinned review package for an
independent reviewer and a deterministic acceptance check for the resulting attestation. The review
must cover the complete team threat model, OAuth/HTTP boundary, forced RLS and pooled-connection
scope, authority mutations, retention/deletion/export/import, backup/restore, quotas, model budgets,
operations metadata, dependencies, and deployment runbook. The accepted artifact must identify an
independent reviewer and exact candidate revision and must contain no unresolved critical or high
finding.

Repository-owned security tests and an internal audit are supporting evidence, not independence.
This issue must not mark the Milestone 9 gate complete, manufacture a reviewer identity, weaken
severity, auto-accept risk, publish a release, add a scanner dependency, or treat a model-generated
self-review as the required independent review.

The independent reviewer attested the exact frozen candidate
`e95696c41602d65b42e1c733e8e0e37696dd3ce3` in `docs/security-reviews/team-v1.toml`. The accepted
artifact records two open informational observations and no critical or high finding; the
deterministic review checker reports zero unresolved critical/high findings. Supporting static and
adversarial analysis covered the mandatory contracts, ADRs 0021–0044, deployment boundary, all 23
PostgreSQL migrations, OAuth/JWT validation, forced RLS and pooled transaction identity, retention,
deletion and anti-resurrection, outbox integrity, quotas, model budgets, backup/restore, secret-file
handling, and dependency provenance. Locked bootstrap, the complete gate, the 46-test security
suite, the real-PostgreSQL suite, and the separate load gate pass; the accepted load run measured
24.923 ms p95 and 255.043 requests per second. No security-relevant implementation changed after the
reviewed candidate; final closure adds only the review artifact and status evidence. Milestone 9
and all 98 implementation-plan requirements are complete.

### Personal checkpoint inspection — Complete

The current approved slice adds one read-only local CLI inspection path for the active durable
handoff. It must resolve scope only from an explicitly enabled canonical project directory, return
the existing bounded canonical checkpoint packet with exact immutable revision and evidence
provenance, report truthfully when no active checkpoint exists, and fail closed for an unregistered
project. It must not add a model call, MCP tool, mutation, source refresh, note read, broad history
browser, export, deletion, settings UI, team behavior, or new dependency.

Implemented `mnemo-memory memory inspect` as a read-only personal-mode command over the existing
canonical checkpoint application service and context-packet contract. It resolves only an explicit
local project binding, returns exact checkpoint revision and evidence provenance, reports no active
handoff truthfully, and rejects an unregistered project without disclosing another project’s IDs or
payload. The user guide and cross-project threat-model control now cover the command. Focused
formatting, lint, strict typing, and all 27 lifecycle/CLI tests passed; the complete verification
gate passed with 526 tests, schema validation, dependency/provenance validation for 86 registered
entries, and architecture validation for 69 product Python files. No dependency or MCP tool was
added.

### Approved episodic fact governance — Complete

The current approved issue adds personal-mode review, immutable correction, and explicit retraction
for the existing task-scoped approved decision/failure/tool-outcome facts. A correction must append
one evidence-bearing replacement and one immutable link from the superseded fact; a retraction must
remove the original fact payload and retain only a bounded scoped tombstone. Both operations must be
idempotent for the same caller action key, reject stale or competing actions, survive restart, and
make ordinary `get_context` return active facts only. Local CLI review and mutation commands must
resolve scope only from an explicitly enabled canonical project and require confirmation for writes.
Cross-project reads and mutations must fail without disclosing IDs, counts, or payload. This issue
does not add automatic extraction, transcript capture, a model call, a new MCP tool, broad checkpoint
history, export, backup deletion, settings UI, team behavior, or a dependency.

Implemented scope-bound `memory events`, `memory event inspect`, `memory event correct`, and
`memory event retract` commands over new application and storage contracts. Corrections append an
immutable same-kind replacement plus an evidence-bearing governance action; retractions erase the
target event and its evidence payload while retaining a minimal scoped tombstone. Reference and
SQLite repositories enforce active-only retrieval, deterministic action-key retries, stale and
competing action rejection, exact-scope isolation, secret rejection, transactional rollback, and
restart durability. SQLite migration 0013 is append-only for governance actions and has a documented
forward-only recovery boundary. The user guide, ADR, personal storage profile, and threat model now
cover the lifecycle and its limits. Focused governance, CLI, application, and MCP tests passed; the
complete verification gate passed with 541 tests, schema validation, dependency/provenance validation
for 86 registered entries, and architecture validation for 70 product Python files. No dependency or
MCP tool was added.

### Issue 15 — dbt artifact completeness — Complete

#### Issue 15A — Complete

The current bounded issue adds original, storage-independent parsers and domain contracts for the
current public dbt `catalog.json` v1 and `run_results.json` v6 schemas. Parsing must remain offline,
bounded, explicitly scoped, and evidence-bearing. Catalog output is limited to relation identity,
relation type, and ordered column names/types; run-result output is limited to exact manifest node
identity, normalized status, bounded timing, failure count, and invocation metadata. Warehouse
comments, owners, statistics, adapter responses, messages, compiled code, relation SQL, arbitrary
arguments, environment values, and thread identifiers must not be retained. Invalid schemas,
non-finite timings, duplicate identities, malformed columns, and hostile-size inputs must fail
closed. This issue does not add persistence, context retrieval, dbt execution, warehouse access, a
model call, a dependency, or support for a later dbt artifact schema.

Implemented pure domain contracts plus offline adapters for catalog v1 and run-results v6. Both
adapters reject unsupported schemas, malformed identities, invalid or non-finite timing values,
duplicate structural identities, non-standard JSON numeric constants, absolute source identities,
and configured size limits. Digest-addressed evidence is retained for every relation or result,
while the explicitly excluded warehouse and execution payloads are absent from normalized output.
The dbt ADR and connector threat controls now record the public-schema provenance, retained-field
boundary, and hostile-input verification. Focused parser/manifest tests passed; the complete gate
passed with 548 tests, strict typing for 146 source files, schema validation, dependency/provenance
validation for 86 entries, and architecture validation for 72 product Python files. No dependency
was added.

#### Issue 15B — Complete

The current bounded issue persists the minimized catalog and run-results projections against one
exact authorized manifest snapshot. Reference and SQLite repositories must reject any supplemental
resource identity absent from that manifest snapshot, reject cross-scope attachment, store
immutable digest-addressed artifact versions, select one current projection per manifest and kind,
and make identical retries idempotent. SQLite migration 0014 must preserve transactional rollback
and use foreign keys to prevent detached catalog relations, columns, results, or timing rows. No raw
artifact, warehouse/comment/statistics payload, compiled SQL, message, adapter response, model call,
new MCP tool, or dependency may be stored or introduced.

Implemented matching reference and SQLite repository contracts plus migration 0014. Catalog and
run-results versions are immutable and content-digest addressed; identical retries are idempotent,
new versions atomically replace only the current pointer for their manifest/kind, and reads require
the exact project scope and manifest snapshot. Supplemental identities must already exist in that
manifest. Composite foreign keys bind every retained child row, and failure injection proves a
rejected child insert leaves neither a header nor partial projection. SQLite reopen tests verify
durability and confirm excluded warehouse comments, owners, adapter payloads, and compiled SQL are
absent from the database dump. The complete gate passed with 554 tests, strict typing for 147 source
files, schema validation, dependency/provenance validation for 86 entries, and architecture
validation for 72 product Python files. No dependency was added.

#### Issue 15C — Complete

The current bounded issue integrates the minimized supplemental dbt projections with the existing
manifest lifecycle and context path. Successful wrapped dbt commands and explicit local manifest
ingestion must attach sibling `catalog.json` and `run_results.json` only to the exact authorized
manifest snapshot. A missing, malformed, unsupported, or mismatched supplemental artifact must be
reported with a bounded safe status and must not invalidate an otherwise valid manifest snapshot.
The existing `get_context` dbt-lineage response may include only bounded matching relation columns
and latest node execution status, each with exact immutable supplemental evidence and subject to the
existing structural token budget. This issue does not add a new MCP tool, dbt execution behavior,
warehouse access, model call, dependency, broad artifact replay, or support for another artifact
schema.

Implemented application ports and local composition for catalog/run-results ingestion, plus
fail-soft sibling discovery in explicit ingestion, project enablement, and successful wrapped dbt
commands. An unchanged manifest can receive a newly generated supplemental projection only when
its digest still matches the exact active snapshot. Missing, invalid, unsupported, or mismatched
supplemental artifacts return bounded statuses without displacing a valid manifest. `dbt status`
reports projection availability, and the existing dbt-lineage context path now adds at most twelve
ordered columns plus matching run status, execution time, and failure count, each with its exact
artifact evidence and under the existing structural token budget. Cross-scope reads and
attachments fail closed; excluded secret-bearing comments, adapter messages, compiled code, and
environment payloads are neither persisted nor returned. The complete gate passed with 559 tests,
strict typing for 147 source files, schema validation, dependency/provenance validation for 86
entries, and architecture validation for 72 product Python files. No dependency or MCP tool was
added.

#### Issue 15D — Complete

The current bounded issue completes the manifest lineage model for dbt exposures and metrics in
the supported v12 adapter, including semantic-model nodes required to preserve their explicit
dependency path. These resources must be parsed as ordinary immutable manifest nodes only when
their exact `depends_on.nodes` identities exist in the same artifact, and their typed
resource identity and dependency edges must survive reference and SQLite storage. Downstream
queries may then surface the exposure or metric affected by a model change with the same bounded
scope, currentness, and evidence rules as existing model/test lineage. Macro dependencies,
semantic dimensions/measures, selector syntax, freshness, test-coverage aggregation, changed-state
indexing, and new MCP tools remain outside this issue.

Implemented exposure, metric, and semantic-model parsing as typed v12 manifest graph nodes with
exact digest-addressed evidence and ordinary explicit dependency edges. The shared node limit now
counts all three collections, collection/resource-type mismatches and unknown dependencies fail
closed, and manifest parent/child maps must agree with every parsed relationship. Reference and
SQLite round-trip coverage proves the resource types and downstream edges survive persistence, while
application and context tests show a model impact query returning its exact dashboard and metric
through the manifest-declared semantic-model bridge without descriptive payload interpretation.
The complete gate passed with 561 tests, strict typing for 147 source files, schema validation,
dependency/provenance validation for 86 entries, and
architecture validation for 72 product Python files. No migration, dependency, model call, or MCP
tool was added.

#### Issue 15E — Complete

The current bounded issue models dbt v12 macros and exact `depends_on.macros` relationships without
retaining macro SQL. Macro resources must carry typed manifest identity and evidence, while macro
dependencies use a distinct edge type from ordinary `depends_on.nodes` lineage. Both edge types
must survive reference and SQLite storage and remain scope-first and traversal-bounded. Migration
0015 may widen only the existing rebuildable edge-type constraint, must retain foreign-key and
transactional rollback guarantees, and requires the documented pre-upgrade backup recovery path.
Macro SQL, execution, Jinja rendering, selector syntax, freshness, test-coverage aggregation,
changed-state indexing, model calls, dependencies, and new MCP tools remain outside this issue.

Implemented typed macro nodes and `dbt_macro_dependency` edges from exact v12
`depends_on.macros` identities. The parser rejects unknown or wrong-type endpoints, duplicate
dependencies, mixed node/macro fields, cycles, and combined per-resource limit overflow; macro SQL
is excluded from normalized projections, SQLite, and context. Existing lineage traversal remains
bounded and now exposes supporting edge types and edge evidence, so a macro impact result is not
misrepresented as ordinary data lineage. Migration 0015 transactionally rebuilds only the
rebuildable edge table, preserves existing rows and endpoint foreign keys, and an injected schema-14
upgrade failure proves rollback leaves its table and ledger unchanged. The complete gate passed
with 563 tests, strict typing for 147 source files, schema validation, dependency/provenance
validation for 86 entries, and architecture validation for 72 product Python
files. No model call, external dependency, or MCP tool was added.

#### Issue 15F — Complete

The current bounded issue adds a deterministic directed shortest-path query over one exact scoped
dbt snapshot. A caller supplies the existing start node or exact manifest file plus a destination
node through the existing `get_context` `dbt_lineage` request. The application must choose one
stable shortest path using only persisted typed manifest edges, respect node/edge/depth limits,
preserve exact edge/node evidence and currentness, and distinguish no path from an unauthorized or
unknown node without leaking another scope. This issue does not add selector syntax, freshness,
test-coverage aggregation, changed-state indexing, code excerpts, a migration, dependency, model
call, or MCP tool.

Implemented one stable breadth-first shortest path over the persisted, typed dbt graph. The
existing `get_context` `dbt_lineage` request now accepts `path_to_unique_id`, resolves both
endpoints inside the same authorized snapshot, applies the existing node, edge, and depth limits,
and returns ordered path nodes with exact manifest evidence, typed edge evidence, and snapshot
currentness. A reachable path, a bounded no-path result, and an unknown or cross-scope endpoint
remain distinct without disclosing another scope. Documentation and security guidance describe
the bounded request and provenance contract. The complete repository gate passes with 566 tests,
strict typing for 147 source files, schema validation, dependency/provenance validation for 86
entries, and architecture validation for 72 product Python files. No model call, external
dependency, migration, or MCP tool was added.

#### Issue 15G — Complete

The current bounded issue adds deterministic direct dbt test-coverage context for one exact node
or unambiguous manifest file in one authorized snapshot. The existing `get_context` tool must
return a stable, bounded list of enabled manifest test nodes that directly depend on the selected
node, exact node and edge evidence, snapshot currentness, and the latest persisted run-result
status when available. No attached tests must remain distinct from an unknown or cross-scope
node without leaking another scope. This issue does not add transitive coverage inference, column
coverage, selector syntax, freshness, changed-state indexing, code excerpts, a migration,
dependency, model call, or MCP tool.

Implemented `dbt_test_coverage` on the existing `get_context` tool for one exact dbt unique ID or
unambiguous manifest file. The application resolves the subject inside one authorized immutable
snapshot, selects a stable bounded list of directly attached enabled manifest tests, and returns
their node/dependency evidence and snapshot currentness. When the same snapshot has a persisted
`run_results.json` projection, each matching test also carries its latest status, execution time,
failure count, and run-result evidence; absence is never promoted to success. Empty coverage,
unknown/cross-scope subjects, truncation, staleness, and token-budget omissions remain explicit.
The complete repository gate passes with 569 tests, strict typing for 147 source files, schema
validation, dependency/provenance validation for 86 entries, and architecture validation for 72
product Python files. No external dependency, migration, model call, or MCP tool was added.

#### Issue 15H — Complete

The current bounded issue adds a deterministic structured dbt selector query to the existing
`get_context` tool. A caller may intersect exact `resource_type`, `package_name`, and `tag`
filters against enabled nodes in one authorized immutable manifest snapshot; at least one filter
is required. Results must be stably ordered, capped before packet rendering, carry exact node
evidence and snapshot currentness, and report no match without widening scope. This is not an
implementation of dbt selector-string syntax and does not add graph expansion, freshness,
changed-state indexing, code excerpts, a migration, dependency, model call, or MCP tool.

Implemented `dbt_selector` on the existing `get_context` tool as a small structured intersection
over exact manifest `resource_type`, `package_name`, and `tag` values. The application requires at
least one filter, reads only the selected authorized immutable snapshot, excludes disabled nodes,
sorts matches by dbt unique ID, and caps them at 100 before context rendering. Each returned fact
carries node evidence and snapshot currentness; no match, stale required state, result truncation,
and packet-budget truncation remain explicit. No dbt selector expression, graph traversal, SQL,
Jinja, or shell input is interpreted. The complete repository gate passes with 571 tests, strict
typing for 147 source files, schema validation, dependency/provenance validation for 86 entries,
and architecture validation for 72 product Python files. No external dependency, migration,
model call, or MCP tool was added.

#### Issue 15I — Complete

The current bounded issue adds authoritative dbt source-freshness context from the official
`sources.json` v3 artifact produced by `dbt source freshness`. Mnemo must parse, persist, and
activate only bounded source identity, observed timestamps/age, threshold counts/periods, status,
execution time, artifact metadata, and exact evidence for an existing authorized manifest
snapshot. Adapter responses, database error text, freshness filters, environment values, timing
details, compiled content, and arbitrary arguments must be discarded. The existing command hook
may ingest an adjacent `sources.json`, and the existing `get_context` tool may query one exact
source unique ID or unambiguous manifest file with currentness and scope safety. This issue does
not execute dbt or contact a warehouse, infer freshness from configuration, add selector syntax,
changed-state indexing, code excerpts, a dependency, model call, or MCP tool. The SQLite schema
change requires upgrade atomicity and forward-only recovery coverage.

Implemented bounded parsing, immutable reference/SQLite persistence, automatic sibling-artifact
ingestion, status reporting, and exact `get_context` retrieval for official dbt `sources.json` v3.
Only source identity, dbt-reported status, observed timestamps/age, threshold counts/periods,
execution time, safe artifact metadata, and evidence survive parsing. Environment values, adapter
responses, database error text, freshness filters, timing details, and arbitrary payloads are
validated only as needed and discarded. Each result must reference a source node in the exact
authorized manifest snapshot; missing or cross-scope nodes cannot widen retrieval. Migration 0016
is additive, forward-only, foreign-keyed, and transactionally rolls back with its ledger entry on
failure. The complete repository gate passes with 577 tests, strict typing for 147 source files,
schema validation, dependency/provenance validation for 86 entries, and architecture validation
for 72 product Python files. No external dependency, model call, warehouse access, dbt execution,
or MCP tool was added.

#### Issue 15J — Complete

The current bounded issue activates dbt manifest source-state fingerprints during successful local
command-wrapper and explicit existing-manifest ingestion. Mnemo must observe only a full Git HEAD
object ID, dirty boolean, a deterministic SHA-256 fingerprint over a bounded set of changed,
deleted, and untracked repository paths plus their current content digests, and an explicit dbt
`--target` value when supplied. Git calls must be shell-free, read-only, timed out, and failure
isolated; unsafe paths, symlinks escaping the project, too many files, too many bytes, missing Git,
or observation failure must yield unknown state rather than fail dbt. No path, source body, diff,
commit message, remote, credential, or environment value may be stored or logged. This issue does
not add retrieval-time probing, changed-state indexing, code excerpts, a migration, dependency,
model call, or MCP tool.

Implemented bounded, shell-free Git source-state observation for successful dbt command-wrapper
ingestion and explicit existing-manifest ingestion. Stored provenance is limited to the full Git
HEAD object ID, dirty state, an irreversible SHA-256 working-tree fingerprint, and a safe explicit
dbt target. Fingerprinting covers bounded changed, deleted, and untracked paths and their current
content digests without retaining or logging paths, bodies, diffs, commit messages, remotes,
credentials, or environment values. Traversal, escaping symlinks, non-regular files, oversized
status or content, missing Git, malformed output, timeouts, and observation failures yield unknown
state without failing dbt. Target differences now produce deterministic stale or unknown
currentness. The complete repository gate passes with 580 tests, strict typing for 149 source
files, schema validation, dependency/provenance validation for 86 entries, and architecture
validation for 73 product Python files. No migration, dependency, model call, or MCP tool was
added.

#### Issue 15K — Complete

The current bounded issue activates retrieval-time dbt source-state comparison for existing
structural queries. After exact scope resolution, a fresh local MCP process may resolve one
unambiguous registered dbt project for that project identity, perform the existing bounded Git
observation, and pass the resulting fingerprint to lineage, path, test-coverage, selector, and
freshness queries. Missing, corrupt, ambiguous, or mismatched bindings and any observation failure
must leave currentness unknown without failing `get_context`; a caller cannot submit or override
raw source-state evidence. Authorization remains ahead of structural retrieval, paths remain local
configuration rather than durable identity, and no repository content, path, diff, environment
value, or Git diagnostic may be stored, logged, or returned. This issue does not add changed-state
indexing, code excerpts, a migration, dependency, model call, MCP tool, or retrieval for an
unregistered scope.

Implemented retrieval-time comparison for every existing dbt structural query through an
application callback supplied by the MCP composition root. The callback resolves exactly one
machine-local dbt binding by owner, workspace, project, and visibility identity only after strict
scope parsing, then uses the existing bounded Git observer; ambiguity, corruption, missing
registration, and callback or Git failure yield unknown currentness without failing context
retrieval. Raw commits and fingerprints are not accepted from MCP callers. Unit coverage proves
matching evidence is current and observer failures are unknown, while an independent fresh-process
integration test starts MCP inside a registered dbt project, omits all scope UUIDs, and receives
current structural context. The complete repository gate passes with 583 tests, strict typing for
149 source files, schema validation, dependency/provenance validation for 86 entries, and
architecture validation for 73 product Python files. No migration, dependency, model call, or MCP
tool was added.

#### Issue 15L — Complete

The current bounded issue adds deterministic dbt changed-state and affected-node context between
two authorized immutable manifest activations. The existing `get_context` tool may compare an
explicit before/after snapshot pair or the latest recorded activation transition, classify bounded
added, modified, and removed nodes from manifest-owned structural fields, and return bounded
downstream refresh candidates derived only from authoritative edges in the relevant snapshots.
Activation order must be recorded explicitly rather than inferred from UUIDs or timestamps;
idempotent re-ingestion of an already active snapshot must not create a transition. The after
snapshot carries the existing retrieval-time currentness comparison, and `require_current` must
fail closed. Cross-scope IDs, missing history, ambiguous files, result and token bounds, and
storage failure remain explicit without widening retrieval. This issue does not execute dbt,
interpret selector syntax or SQL/Jinja, retain source bodies, add code excerpts, a dependency,
model call, or MCP tool. The SQLite activation-history migration requires upgrade atomicity and a
documented forward-only recovery path.

Implemented `dbt_changes` on the existing `get_context` tool for an explicit before/after pair or
the latest recorded manifest activation transition. An append-only, scope-constrained activation
ledger establishes order independently of UUIDs and timestamps, ignores idempotent activation of
the already-active snapshot, and records reactivation of an older snapshot. Comparison classifies
bounded added, modified, and removed nodes from minimized manifest fields; bounded refresh
candidates are the surviving changed nodes and their authoritative downstream dependents across
the before and after graphs. Results carry exact node evidence, after-snapshot currentness,
structured truncation, and `require_current` enforcement. Cross-scope IDs and missing history fail
closed. SQLite migration 0017 is additive, forward-only, transactionally rollback-tested, seeds
only the known active snapshot on upgrade, and supports recovery from an unreleased table-without-
ledger state. The complete repository gate passes with 586 tests, strict typing for 149 source
files, schema validation, dependency/provenance validation for 86 entries, and architecture
validation for 73 product Python files. No dependency, model call, dbt/warehouse execution, source
body retention, or MCP tool was added.

#### Issue 15M — Complete

The current bounded issue adds optional minimal current-file excerpts to an exact dbt lineage
request through the existing `get_context` tool. Only after scope authorization and unambiguous
manifest node resolution may local composition read that node's registered-project `.sql`, `.yml`,
or `.yaml` file. The caller must opt in and may select a positive start line and at most 40 lines;
the reader must also cap file and returned bytes, reject traversal, absolute paths, unsupported
types, non-regular files, escaping symlinks, prohibited secrets, invalid text, missing or ambiguous
bindings, and observation failures. Excerpts carry an immutable digest/line evidence reference,
are rendered as untrusted evidence, remain separately currentness-labeled, and never become
authoritative lineage. Failure or token pressure yields a bounded omission without failing or
widening structural retrieval. This issue does not persist source content, read arbitrary files,
add changed-state behavior, a migration, dependency, model call, MCP tool, SQL/Jinja parsing, or
warehouse/dbt execution.

Implemented opt-in starting-node excerpts on existing `dbt_lineage` requests. After exact scope
and manifest-file resolution, local composition may read only the registered dbt project's
canonical `.sql`, `.yml`, or `.yaml` path, starting at a caller-selected positive line for at most
40 lines. Files and returned text are byte-capped; traversal, absolute paths, unsupported types,
non-regular files, escaping symlinks, invalid UTF-8/NUL content, oversized input, prohibited
secrets, ambiguous or missing bindings, invalid bounds, and read/observation failures yield a safe
omission while lineage remains available. Returned text is never persisted or interpreted. It is
rendered as separately current, untrusted repository evidence with deterministic file/excerpt
digests and exact line provenance, while manifest currentness remains explicit and authoritative
edges remain unchanged. The complete repository gate passes with 589 tests, strict typing for 151
source files, schema validation, dependency/provenance validation for 86 entries, and architecture
validation for 74 product Python files. No migration, dependency, model call, MCP tool, SQL/Jinja
parser, warehouse/dbt execution, or durable source-content storage was added.

### Issue 16 — Production episodic memory — Complete

#### Issue 16A — Complete

The current bounded issue adds a durable transactional outbox and retryable local job boundary for
the canonical checkpoint-lifecycle and explicitly approved episodic/governance events that already
exist. Each first event write must atomically enqueue one deterministic scoped job containing only
event identity, kind/topic, scope IDs, and timestamps—never checkpoint content, summaries,
evidence payloads, prompts, transcripts, tool bodies, source text, or exception text. Idempotent
event retries and already-active no-op operations must not enqueue duplicates. Reference and SQLite
repositories must support bounded oldest-first claims with expiring leases, owner-checked
completion, and safe retry scheduling; a failed transaction must persist neither event nor job.
The application runner invokes one explicitly supplied handler, treats delivery as at-least-once,
requires handler idempotency by job ID, and records only bounded stable failure codes. Concurrency,
lease expiry, restart, duplicate delivery, cross-scope isolation, and migration rollback require
tests. This issue does not add model extraction, conversation capture, a daemon, network service,
new MCP tool, retention/export/deletion behavior, dependency, or team mode. SQLite migration 0018
is additive and forward-only with documented backup recovery.

Implemented as an original Mnemo-owned event-delivery boundary. Checkpoint lifecycle events and
explicitly approved episodic corrections/retractions now atomically enqueue deterministic,
task-scoped jobs containing only event identity, topic/kind, scope metadata, delivery state, and
timestamps. Idempotent event retries do not duplicate jobs. Reference and SQLite adapters provide
bounded oldest-first claims, exclusive expiring leases, owner-checked completion, safe scheduled
retry, restart persistence, and cross-scope non-disclosure; SQLite stores comparable timestamps in
UTC and migration `0018_event_outbox.sql` is additive with transactional rollback and interrupted
local-migration recovery. The bounded application runner accepts one explicit handler, documents
job-ID idempotency, preserves at-least-once delivery, records only validated stable failure codes,
and never persists exception text. Focused tests cover concurrent claimers, lease expiry,
idempotent retry, restart, governance/checkpoint enqueue, reference and SQLite rollback, schema
contents, cross-scope access, migration rollback, and duplicate handler delivery. The complete
repository gate passes with 602 tests, strict typing for 154 source files, schema validation,
dependency/provenance validation for 86 entries, and architecture validation for 76 product Python
files. No model call, extraction, conversation capture, daemon, network/MCP surface, retention,
export/deletion feature, dependency, team mode, or source-content storage was added.

#### Issue 16B — Complete

The current bounded issue adds one composed, content-free classification boundary before the
existing approved-event and knowledge-document persistence paths and immediately before local
knowledge embedding. Mnemo's deterministic high-confidence secret classifier is mandatory and
always runs first; callers may supply a bounded ordered set of additional classifiers, but no
classifier may weaken or bypass a deterministic rejection. Decisions expose only acceptance,
sensitivity, and a bounded stable code, never matched content. Rejection must persist and embed
nothing, must not invoke later classifiers or the embedding provider, and must surface only a safe
code. Existing default behavior remains deterministic and dependency-free. Focused tests require
secret-corpus coverage across both ingestion paths and embedding, ordered classifier composition,
fail-closed invalid classifier results, and proof that optional classifiers cannot override the
mandatory gate. This issue does not add event kinds, conversation/tool capture, model calls,
candidate extraction, an API/CLI/MCP surface, a worker/daemon, migration, retention/export/deletion
behavior, dependency, or team mode.

Implemented one dependency-free `ContentSafetyPolicy` with a mandatory deterministic
high-confidence secret classifier followed by at most eight explicitly supplied classifiers.
Decisions contain only acceptance, sensitivity, and a validated stable code. Deterministic
rejection stops the chain before optional classifiers; later classifiers can only strengthen
sensitivity or reject, and exceptions, malformed output, oversized input, or invalid configuration
fail closed without retaining payloads. The existing approved-event/governance policies now check
summaries, source keys, and persisted evidence references, while knowledge policy checks paths,
titles, frontmatter keys/values, and section headings/content. Both Reference and SQLite adapters
accept the same composed policy without changing safe defaults. Local semantic indexing rechecks
every pending passage immediately before the provider and vector write. Focused tests cover the
secret corpus, content-free results, ordered composition, non-bypass, sensitivity monotonicity,
invalid/failing classifiers, bounds, both persistence adapters, evidence metadata, and zero
provider/vector effects after rejection. The threat model records the implemented boundary. The
complete repository gate passes with 622 tests, strict typing for 156 source files, schema
validation, dependency/provenance validation for 86 entries, and architecture validation for 77
product Python files. No event kind, conversation/tool capture, model call, candidate extraction,
transport surface, daemon, migration, retention/export/deletion behavior, dependency, or team mode
was added.

#### Issue 16C — Complete

The current bounded issue adds the missing canonical activity categories without changing the
completed checkpoint or approved decision/failure/tool-outcome ledgers. One explicitly submitted,
minimized task event may describe a conversation handoff, task activity, tool invocation, or task
outcome using only a bounded summary, actor/category metadata, a caller-owned idempotency key,
explicit evidence, sensitivity, retention schedule, task scope, and timestamps. Raw transcripts,
prompts, tool arguments/bodies/results, commands, source content, and opaque model traces are not
accepted fields. Prohibited content must fail the Issue 16B classification boundary before any
canonical or outbox write. Reference and SQLite repositories must be append-only, scoped,
idempotent, deterministically ordered, and transactionally enqueue exactly one minimal Issue 16A
job per first event write; failed and duplicate writes persist no partial or duplicate state.
Migration 0019 is forward-only with a scope-preserving outbox-topic constraint rebuild, additive
event tables, and rollback coverage. This issue adds no automatic
capture, agent hook, model call, extraction candidate, context retrieval/ranking, API/CLI/MCP
surface, daemon, retention execution, export/deletion behavior, dependency, or team mode.

Implemented the original `TaskActivityEvent` contract for explicit conversation handoffs, task
activity, tool invocations, and task outcomes. The closed event schema contains only a bounded
summary, actor/category, idempotency key, task scope, declared non-prohibited sensitivity,
retention schedule, occurred time, and up to 64 evidence references; raw interaction and tool/source
payload fields are absent and strict deserialization rejects unknown fields. The composed safety
policy rejects secrets and sensitivity labels weaker than classifier output before persistence.
Reference and SQLite repositories provide scoped append-only writes, exact idempotency, ordered
pagination, restart durability, and atomic minimal outbox enqueue under the new `task_activity`
topic. Forward-only migration `0019_task_activity_events.sql` preserves existing outbox rows while
rebuilding its closed topic constraint and adds the scoped event/evidence tables; injected failure
rolls back the whole migration. Focused tests cover all four categories, strict/minimized schema,
scope isolation, conflicts, duplicate writes, classification, Reference and SQLite atomicity,
restart, outbox minimality, table contents, row preservation, and migration rollback. Product and
threat contracts record explicit-only capture and the excluded payloads. The complete repository
gate passes with 635 tests, strict typing for 159 source files, schema validation,
dependency/provenance validation for 86 entries, and architecture validation for 79 product Python
files. No automatic capture, client hook, model call, extraction, context retrieval, transport
surface, daemon, retention execution, export/deletion behavior, dependency, or team mode was added.

#### Issue 16D — Complete

The current bounded issue adds optional provider-neutral extraction from one authorized
`TaskActivityEvent` into durable typed episodic-memory candidates. A closed proposal schema may
return at most four kind/claim/confidence/sensitivity proposals; scope, evidence, retention,
identity, source-event linkage, extractor/provider/model/prompt versions, and candidate status are
constructed by Mnemo and cannot be supplied by model output. Unknown fields, invalid types,
non-finite confidence, prohibited content, under-evidenced sources, and mismatched provider metadata
fail closed before persistence. Invalid structured output receives at most one retry; provider
failures and the final invalid result surface stable payload-free codes. Candidate IDs are
deterministic by source event, extractor version, and proposal position, so at-least-once handling
is idempotent and changed retry output conflicts rather than overwrites. Reference and SQLite batch
storage must be scoped, atomic, durable, and candidate-only. Migration 0020 is additive and
forward-only with rollback coverage. Tests use an explicitly configured fake Luna provider; this
issue adds no model SDK or network call, automatic job invocation, approval/activation, correction,
context retrieval, API/CLI/MCP surface, daemon, retention execution, export/deletion behavior,
dependency, or team mode.

Implemented a provider-neutral, explicitly configured extraction boundary over one exact-scope
minimized task event. The raw provider sees only event identity, category, actor, summary, and a
four-candidate limit; a closed output schema accepts only kind, claim, finite confidence, and
non-prohibited sensitivity, retries malformed output once, pins provider/model metadata, and
returns stable payload-free failures. Mnemo constructs deterministic candidate identity, inactive
status, exact source scope/evidence/retention, source-event linkage, and extractor/provider/model/
prompt provenance. Verified non-inference evidence and mandatory content safety are required before
the Reference or SQLite adapters atomically store a batch; changed at-least-once output conflicts
instead of overwriting. SQLite migration `0020_episodic_memory_candidates.sql` is additive,
scope-guarded, restart-durable, forward-only, and transactionally rollback-tested. Nineteen focused
tests cover strict schema and authority-field rejection, non-finite and invalid types, one retry,
provider failure and metadata mismatch, evidence sufficiency, sensitivity strengthening, secret
rejection at service and repository boundaries, scope isolation, deterministic idempotency,
changed-output conflict, SQLite restart durability, atomic batch failure, and migration rollback.
The product and threat contracts record the candidate-only trust boundary. The complete repository
gate passes with 654 tests, strict typing for 166 source files, schema validation,
dependency/provenance validation for 86 entries, and architecture validation for 85 product Python
files. No model SDK, network call, automatic job invocation, approval/activation, correction,
context retrieval, transport surface, daemon, retention execution, export/deletion behavior,
dependency, or team mode was added.

#### Issue 16E — Complete

The current bounded issue adds explicit user review for one exact-scope episodic candidate. One
strict append-only action may approve or reject a still-pending candidate using a caller-owned
idempotency key, bounded reason, user actor, action time, and verified user-authored evidence.
Every extracted candidate requires this action: confidence never grants authority, sensitive
candidates cannot bypass review, and model/provider output cannot construct or authorize it.
Approval deterministically creates one active evidence-bearing episodic-memory record that retains
the candidate kind, source-event link, source scope/evidence/retention, confidence, and extraction
provenance plus review provenance; rejection creates no active payload. Repeated identical review
is idempotent while competing, cross-scope, non-user, under-evidenced, prohibited, or secret-bearing
actions fail closed without partial state. Reference and SQLite review must be atomic, scoped,
durable, and independently revalidate candidate safety. Migration 0021 is additive and
forward-only with rollback coverage. This issue adds no candidate correction or revision chain,
automatic approval/job invocation, context retrieval/ranking, API/CLI/MCP surface, daemon,
retention execution, export/deletion behavior, dependency, or team mode.

Implemented strict deterministic `EpisodicCandidateReviewAction` and
`ActiveEpisodicMemory` contracts. A review is always a user action with exact task scope, a
caller-owned idempotency key, bounded reason, timestamp, and verified user-correction evidence;
non-user or under-evidenced actions cannot be constructed. Candidate confidence and sensitivity
never activate memory. Reference and SQLite repositories re-run candidate and review content
safety, accept exactly one append-only approval or rejection, make identical delivery idempotent,
and reject competing decisions, reused scoped action keys, secret-bearing reasons, evidence
identity conflicts, and cross-scope access. Approval atomically retains candidate identity,
source-event scope/evidence/retention, confidence, extraction provenance, and review evidence in an
active claim; rejection stores no active marker. Additive forward-only migration
`0021_episodic_candidate_reviews.sql` persists scoped review/evidence rows and a minimal active
marker guarded by approval/candidate triggers. Nine focused tests cover strict serialization,
user/evidence authority, no confidence-based activation, approval, rejection, idempotency,
competing decisions and keys, secret rejection, scope isolation, SQLite restart durability,
transactional activation failure, and migration rollback with candidate preservation. The product
and threat contracts record the explicit review boundary. The complete repository gate passes with
663 tests, strict typing for 167 source files, schema validation, dependency/provenance validation
for 86 entries, and architecture validation for 85 product Python files. No correction or revision
chain, automatic approval/job invocation, context retrieval, transport surface, daemon, retention
execution, export/deletion behavior, dependency, or team mode was added.

#### Issue 16F — Complete

The current bounded issue adds explicit user correction and retraction for an approved episodic
memory as a deterministic append-only revision chain. The approval action is revision one; every
later action carries the exact expected revision identity, caller-owned idempotency key, bounded
reason, user actor, action time, and verified user-correction evidence. Correction supplies one
bounded safe replacement claim and non-weaker sensitivity, marks the prior revision superseded,
and makes the replacement active without changing memory identity, source event, extraction
provenance, scope, retention, or original evidence. Retraction appends a payload-free terminal
revision and immediately excludes the memory from active reads. Identical delivery is idempotent;
stale expected revisions, forks, action-key reuse, cross-scope targets, unsafe content, non-user or
under-evidenced actions, and governance after retraction fail closed. Reference and SQLite replay
of the same action stream must produce identical revisions and active state, atomically and across
restart. Migration 0022 is additive and forward-only with rollback coverage. This issue adds no
expiry or retention execution, physical purge, export/deletion propagation, automatic governance,
context retrieval/ranking, API/CLI/MCP surface, daemon, dependency, or team mode.

Implemented strict `EpisodicMemoryGovernanceAction` and replayed
`EpisodicMemoryRevision` contracts. Approval deterministically forms revision one; every correction
or retraction names the exact active revision, uses a user-only caller key and verified
user-correction evidence, and becomes the next revision identity. Correction preserves memory ID,
source event, extraction metadata, scope, retention, and accumulated evidence while replacing only
the safe claim and non-weaker sensitivity; replay marks every prior revision superseded. Retraction
adds a terminal revision with no claim or sensitivity and immediately disappears from active get,
list, and idempotent review results. Reference and SQLite adapters enforce action-key and expected-
revision uniqueness, monotonic time, content safety, exact evidence identity, cross-scope isolation,
atomic writes, and deterministic ordered replay. Additive forward-only migration
`0022_episodic_memory_governance.sql` stores the scoped optimistic action stream and evidence with
scope and payload-shape guards. Ten focused tests cover strict actions, deterministic replay,
correction and retraction, immutable provenance, idempotency, stale forks, sensitivity weakening,
secret rejection, terminal behavior, cross-scope reads, Reference/SQLite replay equality, SQLite
restart durability, transactional failure, and migration rollback preserving active memory. The
product and threat contracts record the revision boundary. The complete repository gate passes
with 673 tests, strict typing for 167 source files, schema validation, dependency/provenance
validation for 86 entries, and architecture validation for 85 product Python files. No expiry or
retention execution, physical purge, export/deletion propagation, automatic governance, context
retrieval, transport surface, daemon, dependency, or team mode was added.

#### Issue 16G — Complete

The current bounded issue enforces the canonical retention schedule copied onto extracted episodic
candidates and approved memories. One deterministic exact-scope sweep at an explicit aware time
discovers non-permanent candidates whose scheduled expiry is due and atomically appends one
payload-free expiration record per memory identity. Expiration identity is deterministic by memory,
policy, and scheduled time; repeated discovery or delivery is idempotent while conflicting policy,
scope, schedule, or time fails closed. Once recorded, the candidate or memory payload is immediately
excluded from candidate get/list, review, active get/list, governance, and revision reads, including
after restart; approval, correction, retraction, confidence, or access cannot extend retention.
Permanent and not-yet-due schedules remain untouched. Reference and SQLite must expose only scoped
payload-free expiration metadata and produce identical due sets. Migration 0023 is additive and
forward-only with rollback coverage. This issue adds no raw task-event expiry, physical purge,
source/dependent deletion propagation, export, backup behavior, automatic scheduler/daemon,
context retrieval, API/CLI/MCP surface, dependency, or team mode.

Implemented strict payload-free `EpisodicMemoryExpiration` records and one exact-task-scope
retention service. Both adapters discover only due non-permanent schedules copied from canonical
source events, sort the same target set deterministically, validate the entire batch before an
atomic append, make exact delivery idempotent, and reject mismatched source, scope, policy,
schedule, or time. Expiration immediately excludes candidate, review, active-memory, governance,
and revision payloads and prevents extraction retry from restoring content; approval, correction,
retraction, confidence, and access do not change the schedule. SQLite persists exclusion across
restart through additive forward-only migration `0023_episodic_memory_expirations.sql`, whose
table contains only identity, scope, policy, and time metadata guarded against noncanonical
targets. Twelve focused tests cover strict serialization, deterministic identity, before/due and
permanent boundaries, complete payload exclusion, correction and retraction, exact-scope reads,
adapter parity, replay idempotency, conflicting-batch atomicity, injected SQLite rollback, restart
durability, payload-free schema inspection, and migration rollback with candidate preservation.
The product and threat contracts record logical expiry and its remaining physical-purge boundary.
The complete repository gate passes with 685 tests, strict typing for 170 source files,
dependency/provenance validation for 86 entries, and architecture validation for 87 product
Python files. No raw task-event expiry, physical purge, source/dependent deletion propagation,
export, backup behavior, automatic scheduler/daemon, retrieval or transport surface, dependency,
or team mode was added.

#### Issue 16H — Complete

The current bounded issue physically purges candidate-dependent episodic payloads after the
Issue 16G expiration record exists. One deterministic exact-task-scope operation discovers only
unpurged payload-free expiration records, appends one payload-free purge marker per memory, and
atomically removes the candidate claim, candidate evidence links, review reason/evidence links,
active marker, governance claims/reasons/evidence links, and any newly orphaned evidence rows.
The expiration record remains as the scoped anti-resurrection tombstone, identical delivery is
idempotent, conflicting scope, identity, expiration, or time fails closed, and extraction retry
cannot restore purged content. The permitted source task event and evidence it still references
remain intact. Reference and SQLite must produce identical purge targets and results, including
after restart. Migration 0024 is additive/forward-only in behavior and rebuilds only the expiration
table to detach its candidate foreign key while preserving every expiration row; rollback must
preserve the pre-purge candidate and expiration. This issue adds no raw task-event expiry, source
or user-requested deletion, export, backup cleanup, automatic scheduler/daemon, context retrieval,
transport surface, dependency, or team mode.

Implemented strict deterministic `EpisodicMemoryPurge` markers and an exact-task-scope purge sweep
over payload-free expiration metadata. Both adapters validate the full batch before mutation,
retain the expiration tombstone, make exact replay idempotent, reject conflicting expiration,
scope, identity, or time, and permanently prevent candidate restoration. Reference removes every
candidate-dependent object from its in-memory projections. SQLite atomically marks the tombstone,
deletes candidate, review, active, governance, and evidence-link rows in dependency order, and
deletes only evidence rows no longer referenced by any Mnemo evidence-bearing record; source-event
and shared evidence remain intact. Migration `0024_episodic_memory_purges.sql` rebuilds only the
payload-free expiration table, preserves every expiration row, detaches its candidate foreign key,
adds immutable purge metadata, and has rollback and foreign-key-integrity coverage. Seven added
focused tests cover strict payload-free serialization, end-to-end purge and anti-resurrection on
both adapters, physical SQLite row/evidence removal, source preservation, adapter parity,
cross-scope and conflicting replay, batch atomicity, injected transaction rollback, restart
durability, and migration rollback with candidate/expiration preservation. The product and threat
contracts now distinguish candidate-dependent physical purge from remaining raw-event and
source/user deletion work. The complete repository gate passes with 692 tests, strict typing for
170 source files, dependency/provenance validation for 86 entries, and architecture validation for
87 product Python files. No raw task-event expiry, source or user-requested deletion, export,
backup cleanup, automatic scheduler/daemon, retrieval or transport surface, dependency, or team
mode was added.

#### Issue 16I — Complete

The current bounded issue enforces expiry and physical purge for the explicitly minimized task
activity source events introduced in Issue 16C. One deterministic exact-task-scope service uses
each event's canonical non-permanent schedule to append a payload-free expiration tombstone, hide
the summary/evidence immediately, and later append a payload-free purge marker that atomically
removes the event, its evidence links, newly orphaned evidence, and its task-activity outbox job.
Purge fails closed while any candidate payload still depends on the event; Issue 16G/16H expiry and
purge must remove that dependent payload first. Exact replay is idempotent, mismatched policy,
scope, schedule, identity, or time conflicts, permanent and not-yet-due events remain available,
and event retry cannot resurrect expired content. Existing candidate expiration/purge tombstones
remain valid after their source event is purged. Reference and SQLite must expose identical scoped
targets and behavior, including restart. Migration 0025 is additive/forward-only in behavior,
adds payload-free source-event lifecycle metadata, and rebuilds only the existing episodic
expiration table to detach the source-event foreign key required for source purge; rollback must
preserve every event, candidate tombstone, and expiration. This issue adds no user-requested or
source-deletion propagation, export, backup cleanup, automatic scheduler/daemon, context retrieval,
transport surface, dependency, or team mode.

Implemented strict payload-free `TaskActivityEventExpiration` and `TaskActivityEventPurge`
contracts plus one authorization-first task-activity retention service. Reference and SQLite
discover the same due/non-permanent events, validate complete batches before mutation, make exact
expiration and purge delivery idempotent, reject conflicting policy, schedule, scope, identity, or
time, hide expired event payloads immediately, and prevent retry resurrection. Source purge checks
that candidate payloads are gone, preserves candidate expiration/purge tombstones, removes the
event summary and evidence links, deletes only newly orphaned evidence, and cancels its
task-activity outbox job atomically. Permanent and not-yet-due events remain readable. Migration
`0025_task_activity_retention.sql` preserves existing candidate tombstones while detaching their
source-event foreign key, adds only payload-free source lifecycle metadata, and retains foreign-key
integrity across rollback/retry. Twelve focused tests cover strict serialization, before/due and
permanent boundaries, logical exclusion, physical purge, outbox cancellation, evidence removal,
anti-resurrection, exact scope, adapter parity, dependent candidate ordering, tombstone survival,
conflicting-batch atomicity, injected SQLite rollback, restart durability, payload-free schema,
and migration rollback. The product and threat contracts now record minimized source-event
retention without implying capture or deletion of arbitrary raw conversations/tool bodies. The
complete repository gate passes with 704 tests, strict typing for 172 source files,
dependency/provenance validation for 86 entries, and architecture validation for 88 product Python
files. No user-requested or source-deletion propagation, export, backup cleanup, automatic
scheduler/daemon, retrieval or transport surface, dependency, or team mode was added.

#### Issue 16J — Complete

The current bounded issue adds explicit user/source deletion propagation for the production
episodic slice. One strict user-authored exact-task-scope action may delete an individual episodic
memory or a minimized source task event. Individual deletion writes one deterministic payload-free
memory tombstone and atomically removes any candidate, review, active, governance, evidence-link,
and newly orphaned evidence payload while preserving unrelated source data. Source deletion writes
one payload-free source tombstone plus deterministic dependent memory tombstones, removes every
candidate-dependent payload, then removes the source event, its evidence links, newly orphaned
evidence, and task-activity outbox job. Existing expiry/purge tombstones remain payload-free and
valid. Exact replay is idempotent; competing actions, action-key reuse, cross-scope targets, and
target/source mismatches fail closed. Re-ingestion cannot resurrect a deleted event or memory.
Reference and SQLite must produce identical tombstones and deletion results, including restart and
transaction failure. Migration 0026 is additive and forward-only with rollback coverage. This
issue adds no export, backup cleanup, filesystem/knowledge/checkpoint deletion, automatic
scheduler/daemon, API/CLI/MCP surface, dependency, or team mode.

Implemented strict deterministic `TaskActivityEventDeletion` and `EpisodicMemoryDeletion`
contracts plus one exact-task-scope user deletion service. Reference and SQLite now make
individual memory deletion and source-event cascade produce identical payload-free tombstones,
make exact replay idempotent, reject competing actions, action-key reuse, cross-scope targets, and
source mismatches, and permanently prevent event or candidate resurrection. Individual deletion
removes candidate, review, active, governance, evidence-link, and newly orphaned evidence payloads
while preserving the source. Source deletion creates dependent memory tombstones, removes every
dependent payload, then removes the minimized source event, its evidence links, newly orphaned
evidence, and task-activity outbox job atomically while preserving unrelated data and all existing
retention tombstones. Migration `0026_episodic_deletions.sql` adds only scoped action/dependency
metadata, contains no claim, summary, reason, or evidence payload, survives restart, preserves
retention completion after explicit deletion, and has forward-only rollback and foreign-key
coverage. Nine focused tests cover strict serialization, individual reviewed/corrected deletion,
source cascade, physical row/job/evidence removal, anti-resurrection, cross-scope and competing
replay, adapter parity, retention-purge compatibility, restart durability, payload-free schema,
and injected transaction rollback. Product and threat contracts now delimit this production
episodic deletion slice from remaining export, backup, checkpoint, and knowledge deletion work.
The complete repository gate passes with 713 tests, strict typing for 175 source files,
dependency/provenance validation for 86 entries, and architecture validation for 90 product Python
files. No export, backup cleanup, filesystem/knowledge/checkpoint deletion, automatic
scheduler/daemon, API/CLI/MCP surface, dependency, or team mode was added.

#### Issue 16K — Complete

The current bounded issue adds one portable, integrity-verifiable export for the production
episodic slice. One exact task scope returns a versioned canonical bundle containing every
currently permitted minimized task event and non-expired/non-deleted candidate payload, its review
and governance action stream, the deterministically replayed revision chain, and all matching
payload-free memory/source expiration, purge, and deletion tombstones. Every nested object retains
its exact scope, evidence, retention, source, and extraction/governance provenance. Arrays have
stable identity ordering; canonical UTF-8 JSON and a SHA-256 content digest make identical state at
one export time byte-identical and tampering detectable. Authorization is applied in every scoped
storage query before payload reconstruction. Expired, purged, and deleted payloads never enter the
bundle, while their tombstones remain exportable so an eventual importer cannot resurrect them.
Reference and SQLite must produce the same bundle, including after restart, and malformed,
cross-scope, duplicate, inconsistent, or digest-mismatched bundles fail closed. This issue adds no
filesystem output, import, CLI/API/MCP surface, checkpoint/approved-fact/knowledge/structural export,
backup behavior, migration, dependency, model call, or team mode.

Implemented the versioned `EpisodicExportBundle` domain contract, strict revision serialization,
one authorization-first export service, and matching Reference/SQLite projections. The bundle
canonically orders live minimized events, live candidates, reviews, governance streams, and replayed
revision chains, includes every matching memory/source expiration, purge, and deletion tombstone,
and computes one SHA-256 digest over canonical UTF-8 JSON. It rejects unsupported versions, naive
times, cross-scope state, duplicates, non-canonical ordering, unavailable sources, inconsistent
review/governance/revision/purge/deletion relationships, malformed JSON, and digest mismatch.
SQLite discovers payloads only through the exact authorized scope or already-authorized candidate
identities; both adapters exclude expired/deleted source and memory payloads before reconstruction
and produce byte-identical output at the same export time. Three focused tests cover strict revision
round-trip, complete mixed-state Reference/SQLite parity, scope isolation, current/rejected/
corrected state, retention and deletion tombstones, SQLite restart, deterministic bytes and digest,
changed export time, tampering, duplicates, relationship omission, non-canonical order, and invalid
scope. Product and threat contracts now define export integrity and its user-controlled-copy
boundary. The complete repository gate passes with 716 tests, strict typing for 178 source files,
dependency/provenance validation for 86 entries, and architecture validation for 92 product Python
files. No filesystem output, import, CLI/API/MCP surface, checkpoint/approved-fact/knowledge/
structural export, stored-export cleanup, backup behavior, migration, dependency, model call, or
team mode was added.

### Issue 17 — Unified context engine — Complete

#### Issue 17A — Complete

The current bounded issue adds the first production context-engine slice around the already
implemented unified packet assembler. A bounded transient query is classified deterministically
into an explainable retrieval plan, and the plan always includes exact-task resumption context.
The engine retrieves only active episodic memories through the caller's complete task scope before
performing lexical, temporal, confidence, or type-priority scoring. It emits stable ranked context
items with complete evidence provenance, respects both the episodic section and total hard token
budgets, and records budget omissions without persisting the query. The production MCP and
automatic-memory composition paths use this engine, while existing explicit knowledge, source,
dbt, and procedure requests continue through their current authoritative services.

This issue adds no vector fusion, cross-category reranking, conflict detection, deduplication,
client-specific renderer, `explain_context` tool, new persistence, model call, dependency, API/UI,
or team mode.

Implemented the first production `packages/context_engine` component with a deterministic,
inspectable query planner and an authorization-first episodic packet enricher. A bounded transient
query selects literal intent categories without a model call or persistence; complete task scope is
sent to the active-memory repository before lexical, temporal, confidence, or kind-priority
scoring. Stable score and identity tie breakers produce ranked untrusted context items that retain
scope, sensitivity, evidence, source trust, observation time, retrieval method, and one matching
provenance notice. Both the episodic section and total packet budget are enforced before inclusion;
non-fitting and beyond-50 candidates receive bounded omissions, and expected storage failure
degrades to one sanitized omission. The stdio MCP schema now accepts the transient query and routes
even a no-option `get_context` through the engine when configured. Installed MCP composition plus
automatic session and prompt attachments use the new engine, while the existing canonical packet
assembler remains unchanged for explicit checkpoint, knowledge, procedure, source, and dbt facts.

Three focused tests cover deterministic multi-intent planning, project-scope exclusion, query
validation/non-persistence, exact scope-before-score behavior, stable lexical/temporal ranking,
cross-scope isolation, evidence/provenance completeness, hard zero-token and candidate bounds, and
sanitized storage failure; MCP schema coverage verifies the new query input. The product contract
and threat model record the retrieval and privacy controls. The complete repository gate passes
with 719 tests, strict typing for 181 source files, dependency/provenance validation for 86 entries,
and architecture validation for 94 product Python files. No vector fusion, cross-category
reranking, conflict detection, deduplication, client renderer, `explain_context`, persistence,
model call, dependency, API/UI, or team mode was added.

#### Issue 17B — Complete

The current bounded issue adds a content-free explanation for an already assembled canonical
context packet. One read-only `explain_context` MCP tool accepts only that strict packet, validates
its existing provenance and budget invariants, and returns included item identities/types/scopes,
source and evidence metadata, ranks and retrieval methods, token accounting, exclusions,
conflicts, and staleness without repeating claims, note text, code, checkpoint content, query text,
or evidence locations. The input is byte-bounded before parsing and cannot retrieve, persist, rank,
authorize, or mutate anything. This issue updates the public MCP inventory from two tools to three
without changing `get_context` or `save_checkpoint` behavior.

This issue adds no new retrieval, reranking, conflict inference, renderer, persistence, model call,
dependency, API/UI, or team mode.

Implemented a deterministic content-free `ContextExplanation` projection and the third local MCP
tool, `explain_context`. The read-only tool accepts one packet returned by `get_context`, rejects
inputs above 128 KiB before parsing, revalidates the packet's strict provenance and hard-budget
invariants, and reports item identity/type/scope, source trust, sensitivity, validity, observed
time, rank/score/retrieval method, source reference/digest, payload-free evidence metadata,
omissions, conflicts, explicit non-current items, and complete token accounting. It never returns
the packet's checkpoint, memory, note, code, or procedure content, query text, or evidence location;
it performs no retrieval, authorization decision, ranking, persistence, or mutation. The output
labels its basis as a caller-supplied canonical packet so structural validation cannot be mistaken
for proof of origin. Malformed, inconsistent, and oversized input produces one sanitized error.

The production server, installed-launcher benchmark, Codex/Claude connection smoke tests, local MCP
guide, client guide, README, dbt wrapper guide, and context benchmark now use the three-tool
inventory. Four context-engine tests cover selection plus full explanation metadata and content
absence; stdio tests cover successful explanation, read-only safety annotations, malformed input,
oversize rejection, and error redaction. The product contract and threat model record the
authenticity and denial-of-service boundary. The complete repository gate passes with 720 tests,
strict typing for 182 source files, dependency/provenance validation for 86 entries, and
architecture validation for 95 product Python files. No new retrieval, reranking, conflict
inference, renderer, persistence, model call, dependency, API/UI, or team mode was added.

#### Issue 17C — Complete

The current bounded issue makes the deterministic query plan execute against existing authorized
category indexes. A transient general query selects both lexical knowledge and retained source
identity search; a specialized query selects only its literal categories. Source routing removes
only a closed list of question/intent words so identifiers remain exact, bounded, and explainable.
Explicit knowledge, semantic, source, dbt, and procedure requests remain authoritative and are
never overwritten. No semantic search is enabled implicitly.

When a caller explicitly requests both lexical and already-local semantic knowledge, the assembler
must deduplicate sections and fuse their independent ranks with reciprocal-rank fusion (`k=60`)
instead of comparing incomparable term counts and cosine-derived integers. Each result records the
signals that contributed to its fused score and retains deterministic path/revision tie breakers.
Authorization stays inside each repository query before fusion.

This issue adds no model/provider call beyond an already-explicit semantic request, dbt intent
inference, procedure-tag inference, cross-category budget changes, conflict inference, persistence,
dependency, renderer, API/UI, or team mode.

Implemented deterministic execution of the transient query plan against the existing scoped
knowledge and retained source-identity indexes. A specialized literal query routes only to the
selected categories; a general query routes to both bounded lexical categories. A fixed closed
question/category-word list reduces source queries while leaving exact identifiers for the
existing exact/prefix/all-term matcher. Explicit lexical, semantic, source, source-impact/change/
overview, dbt, and procedure requests are preserved unchanged. The query is not persisted or
returned, and semantic retrieval remains explicit.

The knowledge assembler now keeps lexical and explicit local-vector result streams separate,
deduplicates exact revision/section identities, and uses reciprocal-rank fusion with `k=60` when
both were requested. Raw term counts and cosine-derived integers are never compared. Fused scores
and contributing signal names are returned in ranking metadata, with deterministic path/section
ties; declared conflict evidence remains retained after fusion. Each repository query still
receives the authorized project scope before candidate generation.

Five context-engine tests and ten knowledge-context tests cover deterministic general/specialized
routing, explicit-query preservation, absence of implicit semantic search, actual scoped candidate
generation, query non-persistence, independent lexical/vector ranks, exact-section deduplication,
RRF ordering/score/method provenance, and existing source/knowledge behavior. The product contract,
threat model, README, and local MCP guide record the rules. The complete repository gate passes
with 723 tests, strict typing for 182 source files, dependency/provenance validation for 86 entries,
and architecture validation for 95 product Python files. No model/provider call beyond an
already-explicit semantic request, dbt intent inference, procedure-tag inference, cross-category
budget change, conflict inference, persistence, dependency, renderer, API/UI, or team mode was
added.

#### Issue 17D — Complete

The current bounded issue finalizes an assembled packet with three conservative deterministic
controls. Items with the same scope, sensitivity, content, source reference, and source digest are
exact duplicates; one authority/rank-stable survivor remains and every removed identity receives a
`duplicate` omission. Non-conflicting knowledge and episodic results are limited to two items per
exact evidence-source set so one source cannot consume a category; lower-ranked removals receive a
diversity omission. Checkpoints, mandatory procedures, and all conflict participants are protected.

Two included items citing the same source reference with different digests become an unresolved
source-integrity conflict. Both remain, their context items are marked unresolved, and their exact
evidence is retained. Existing declared conflicts are also reflected on their item state. No prose,
claim, or semantic contradiction inference is performed. Selection only removes items from an
already authorized packet and recomputes token/provenance invariants; it performs no repository
read or mutation.

This issue adds no learned reranking, semantic conflict inference, configurable budget change,
renderer, persistence, model call, dependency, API/UI, or team mode.

Implemented one deterministic final-selection pass over an already-authorized canonical packet.
It collapses only items whose scope, sensitivity, content, source reference, and source digest all
match, chooses a stable source-authority/validity/rank survivor, merges the exact evidence set, and
records each removed identity as a duplicate omission. Non-conflicting episodic and knowledge
items are limited to the two highest-ranked results for one exact evidence-source set. Active
checkpoints, mandatory procedures, and every declared conflict participant are protected from
both controls. Removed-item provenance is excluded and declared token totals are recomputed from
the retained packet.

The same pass creates one deterministic unresolved conflict when included items cite one exact
source reference with different digests, retains every participant and its evidence, and reflects
declared unresolved/resolved conflict state on each context item. It performs no semantic or prose
contradiction inference. Checkpoint-lesson and source-edge provenance references are now
item-specific so ordinary multi-lesson revisions and multi-edge symbols cannot be misclassified as
digest conflicts.

Five focused final-selection tests cover exact duplicate evidence merging, authority/rank survival,
two-per-source diversity, deterministic source-integrity conflicts, existing declared conflict
state, mandatory-procedure protection, omissions, and token reconciliation. Existing checkpoint
and multi-language source tests verify item-specific producer provenance. The product contract,
threat model, and README record the behavior and its limits. The complete repository gate passes
with 728 tests, strict typing for 183 source files, dependency/provenance validation for 86 entries,
and architecture validation for 96 product Python files. No learned reranking, semantic conflict
inference, configurable budget change, renderer, persistence, model call, dependency, API/UI, or
team mode was added.

#### Issue 17E — Complete

The current bounded issue adds deterministic agent-readable rendering for the existing canonical
packet. Codex and Claude Code receive an explicit client-labeled line-record rendering with a fixed
trust boundary, stable section order, exact selected content, ranks, provenance identities,
conflicts, omissions, and canonical token accounting. Dynamic content stays JSON-quoted on one
record and cannot change the renderer's structural labels. Rendering is a pure projection and
cannot retrieve, authorize, rank, mutate, or change the canonical packet.

The existing `get_context` tool keeps returning the canonical packet by default. An explicit
`render_for` value may additionally return that unchanged packet beside its rendering; no new MCP
tool is added. Opt-in automatic-memory hooks render their already-bounded canonical attachment for
the configured client before adding it to context. Invalid rendering input fails open and cannot
prevent either client from operating.

This issue adds no new retrieval, ranking, conflict inference, persistence, model call, dependency,
API/UI, settings, packaging, or team mode.

Implemented a pure `render_context_packet` projection for `codex` and `claude-code`. The stable
line-record format names the client and canonical request/token metadata, emits selected items in
canonical order with exact content, rank, validity, trust, evidence identities, source reference
and digest, then emits conflicts and omissions. A fixed trust record precedes all dynamic data.
Every dynamic value is compact JSON on one record, so embedded newlines or record sentinels remain
quoted data. Rendering leaves the frozen canonical packet unchanged and performs no repository or
model operation.

The existing `get_context` default response remains the canonical packet. Its optional
`render_for` input returns `context_packet`, `rendered_context`, and `rendered_for`; the nested
packet round-trips through the same strict domain contract and can be passed to `explain_context`.
Automatic-memory session and prompt attachments validate their bounded canonical packet and use
the configured client's renderer before hook output. Invalid input returns no attachment, and the
hook remains fail open. No tool was added in that issue and the then-current three-tool inventory
was unchanged.

Renderer tests prove deterministic Codex/Claude labels, exact content and provenance preservation,
JSON quoting of embedded newlines/sentinels, omissions, canonical immutability, and automatic
invalid-input failure. Real stdio MCP coverage proves default response compatibility, optional
wrapping, canonical packet validation, and schema rejection of an unsupported client. The focused
engine, MCP, and automatic-memory suites pass with 89 tests. The product contract, threat model,
README, local MCP guide, and client guide record the behavior. The complete repository gate passes
with 729 tests, strict typing for 184 source files, dependency/provenance validation for 86 entries,
and architecture validation for 97 product Python files. No new retrieval, ranking, conflict
inference, persistence, model call, dependency, API/UI, settings, packaging, or team mode was
added.

#### Issue 17F — Complete

The Milestone 5 completion audit maps every durable build requirement to production evidence.
`DeterministicContextPlanner` supplies literal inspectable classification and retrieval plans;
scoped repositories generate candidates before scoring. Episodic selection combines lexical,
temporal, confidence, and kind-importance signals. Knowledge uses scoped lexical FTS and explicit
local-vector scoring, then reciprocal-rank fusion. Source identities use deterministic
exact/prefix/all-term structural rank, while dbt queries use authoritative structured traversal.
Procedure tags are exact and checked-in. Final selection supplies conflicts, exact deduplication,
source diversity, and hard budgets. Codex/Claude rendering preserves the canonical packet, and
`explain_context` reports ranks, sources, exclusions, conflicts, and staleness without content.

The current deterministic resumption evaluation passes with no-memory required-fact recall at 0%,
Mnemo required-fact recall and provenance coverage at 100%, no stale decision presented as current,
and a 499-token packet versus a 2,948-token transcript: 83.07% context reduction and 81.58% total
fresh-input reduction. The unified checkpoint-plus-dbt evaluation passes exact upstream/downstream
precision and recall, provenance, staleness, scope, every hard budget, 72.98% structural reduction,
and 77.65% combined reduction. Both invoke no model.

The real isolated cross-client evaluation passes on Codex CLI 0.146.0 and Claude Code 2.1.221.
Codex-to-Claude, Claude-to-Codex, and alternating revision paths each retain 100% required-fact and
provenance coverage with a 453-token runtime packet versus the 2,948-token transcript (84.63%
context reduction). It also proves zero cross-project disclosure, unchanged client configuration,
stale-writer recovery, prompt failure for a missing launcher, and sanitized corrupt-store failure.
The complete repository gate remains green with 729 tests, strict typing for 184 source files,
dependency/provenance validation for 86 entries, and architecture validation for 97 product Python
files. Milestone 5 is complete; no incremental ranking, new dependency, model call, API/UI,
packaging, or team feature was added during the audit.

### User onboarding and memory-lifecycle documentation — Complete

The current bounded documentation-only issue makes the GitHub and PyPI landing page useful to a
first-time user, adds plain-language memory-management and Team-mode guides, and corrects stale
benchmark and release-boundary statements. It changes no runtime behavior, schema, dependency,
policy, storage, MCP tool, packaging version, or release artifact.

The GitHub/PyPI README now leads with the problem, user benefits, supported use cases, corrected
deterministic token-reduction evidence, a five-minute install path, everyday automatic-memory
behavior, and a plain-language context lifecycle. The same README remains the package's PyPI long
description. New focused guides explain review/correction/forgetting choices and the Team trust and
operator boundary. The practical and client guides link to those paths, the accepted Team security
review replaces the stale release-gate statement, and both benchmark documents match the current
fixtures and product boundary.

The deterministic evaluations pass with a 499-token handoff packet versus 2,948 transcript tokens
(83.07% reduction) and a 1,426-token unified packet versus 6,379 transcript-plus-manifest tokens
(77.65% reduction); the structural slice is 927 versus 3,431 tokens (72.98% reduction). The first
complete repository gate after the content changes passed with 942 tests, 27 expected skips, the
26-test real PostgreSQL suite with one expected load-test skip, schema validation, dependency and
provenance validation for 94 entries, architecture validation for 144 product Python files, and
installed-package verification. Relative Markdown link targets and `git diff --check` also pass.

### PyPI 0.1.0a4 documentation release candidate — Complete

This bounded release issue packages the completed user-onboarding documentation for the next
immutable PyPI prerelease. It updates only active `0.1.0a4` package, workflow, verifier, test, and
installation references and regenerates the committed lockfile through uv. It adds no runtime
behavior, schema, dependency, policy, MCP tool, or deployment-boundary change.

The release candidate passed the focused release suite (`10 passed`), workflow YAML parsing,
relative Markdown link validation, `git diff --check`, and the complete `npm run check` gate
(`942 passed`, `27 skipped`; PostgreSQL integration `26 passed`, `1 skipped`). The independently
built artifacts passed the release verifier with these SHA-256 digests:

- wheel: `005667efdafbd4149950cb77866efbe3e434a0b9c159aaf2501b57a1ce26471a`
- source distribution: `a95919a9e2372526648f773fb50a4711239904e3f64168a38c2a47b9287821d9`

Publication remains an operational action performed from the committed revision by the existing
protected PyPI workflow; the workflow performs its own clean build, exact-bundle hash checks,
trusted publication, provenance verification, and installed-package smoke test.

### Installed CLI version reporting — Complete

This bounded usability issue adds a standard eager `mnemo-memory --version` root option backed by
the installed `mnemo-unified-context` distribution metadata. It adds focused CLI coverage and an
installed-wheel smoke assertion. It does not change package dependencies, storage, schema, policy,
MCP tools, client registration, or runtime services. User installation documentation remains
pinned to the immutable production `0.1.0a4` release and must add this option only with the next
versioned publication that contains it.

The option exits successfully with `mnemo-memory 0.1.0a4`, is listed in root help, and is exercised
through both focused Typer tests and the isolated installed-wheel verification. The focused
lifecycle suite passes with 33 tests, focused formatting and lint pass, `git diff --check` passes,
and the first complete repository gate passes with 944 tests, 27 expected skips, the 26-test real
PostgreSQL suite with one expected load-test skip, schema validation, dependency/provenance
validation for 94 entries, architecture validation for 144 product Python files, and installed
package verification.

### PyPI 0.1.0a5 CLI version release candidate — Complete

This bounded release issue packages the completed root `mnemo-memory --version` option in the next
immutable PyPI prerelease. It updates active package, lockfile, workflow, verifier, test, and user
installation references to `0.1.0a5`, then publishes only through the existing protected PyPI OIDC
workflow. It adds no further runtime behavior, dependency, schema, policy, MCP tool, client
registration, or deployment-boundary change.

The release candidate passes the focused CLI and release suite (`43 passed`), direct
`mnemo-memory --version` verification, workflow YAML parsing, relative Markdown link validation,
`git diff --check`, and the complete `npm run check` gate (`944 passed`, `27 skipped`; PostgreSQL
integration `26 passed`, `1 skipped`). The independently built artifacts pass the release verifier
with these SHA-256 digests:

- wheel: `146c4583f2b4e343d0b718f5b09e9e8177bdf3aaacb909b6219525df2bbc7d16`
- source distribution: `5e08f904e8db13a8aab56f55d3e597f66333a81aa3538861545e04043390fe77`

Publication remains an operational action performed from the committed revision by the existing
protected workflow, which rebuilds and verifies its own exact artifact bundle before upload.

The first production workflow attempt correctly stopped before artifact construction or upload
because GitHub's forced ANSI coloring split the styled `--version` token in the raw help-test
output. The CLI behavior was unaffected. The release test now removes terminal styling before
asserting the user-visible help text. The exact forced-color regression and the complete repository
gate both pass after the correction (`944 passed`, `27 skipped`; PostgreSQL integration `26 passed`,
`1 skipped`).

### Generated dbt target source-refresh isolation — Complete

This bounded defect fix prevents the general source-structure projection from scanning conventional
generated `target/` directories. A real dbt repository exposed the failure with `catalog.json`,
`index.html`, and `manifest.json` artifacts above the existing one-megabyte per-file safety limit;
the first oversized artifact caused `mnemo-memory memory refresh` to stop with
`MNEMO_SOURCE_REFRESH_UNAVAILABLE` even though authoritative dbt ingestion was already healthy.

The dedicated dbt artifact path is unchanged: `dbt enable` and wrapped dbt commands still ingest
the manifest, catalog, run results, and freshness evidence into the authoritative dbt projection.
Only the independent general source scanner now treats `target/` like the existing generated
`build`, `dist`, and `node_modules` exclusions. Parser and public-CLI regressions prove that
oversized generated dbt artifacts are excluded before file-size enforcement while real source
files remain indexed and repeated refreshes remain idempotent.

The focused regression suite passes with 13 tests. The complete `npm run check` gate passes with
945 tests and 27 expected skips, the 26-test real PostgreSQL suite with one expected load-test
skip, schema validation, dependency/provenance validation for 94 entries, architecture validation
for 144 product Python files, and installed-package verification. No dependency, schema, policy,
storage contract, dbt parser, MCP tool, or client-registration behavior changed.

### PyPI 0.1.0a6 generated-target refresh release candidate — Complete

This bounded release issue packages the completed generated `target/` source-refresh isolation fix
in the next immutable PyPI prerelease. It updates active package, lockfile, workflow, verifier,
test, and installation references to `0.1.0a6`. It adds no runtime behavior beyond the completed
fix and no dependency, schema, policy, storage contract, MCP tool, client registration, or dbt
artifact-ingestion change.

The release candidate passes the focused source-refresh, CLI-version, workflow, and artifact suite
(`31 passed`), direct `mnemo-memory --version` verification, both workflow YAML parsers,
`git diff --check`, and the complete `npm run check` gate (`945 passed`, `27 skipped`; PostgreSQL
integration `26 passed`, `1 skipped`). The independently built artifacts pass the release verifier
with these SHA-256 digests:

- wheel: `51edbb8823f891e306b2077d2d82486b02e1059594cab51ac3dcac9f833c8cce`
- source distribution: `75cbfa28e4ccc1fc86131dc2f29efe34279c0bb659d0b10f3a8da2a4c71f299b`

Publication remains an operational action performed from the committed revision by the existing
protected PyPI OIDC workflow, which rebuilds and verifies its own exact artifact bundle before
upload and independently verifies the live PyPI files afterward.

### Automatic dbt structural awareness and shorthand onboarding — Complete

The demonstrated Claude Code VS Code workflow had a connected Mnemo MCP server and an active dbt
projection, but prompt-time automatic retrieval selected knowledge and generic source identity
only; the client therefore fell back to scanning SQL files. Automatic retrieval now routes a short,
explicit request about dbt models to the authoritative saved dbt projection. It attaches at most 32
model summaries within a 400-token structural allocation inside the unchanged 1,300-token automatic
context budget, with snapshot provenance and truncation metadata. Selection remains deterministic,
scope-first, prompt-transient, and fail-open: it adds no model call, stores no prompt text, never
enumerates an unbounded manifest, and does not change explicit MCP query behavior.

The primary executable is now the shorter `mnemo`, while `mnemo-memory` remains a compatible alias.
`mnemo connect codex` and `mnemo connect claude-code` now act on the current directory and enable
automatic project memory by default. `--dry-run`, optional `--confirm`, `--check`, advanced
`--project-dir`, and the inverse `--auto-memory-disable` retain explicit control; the former `--yes`
spelling remains accepted but hidden for script compatibility. The new `mnemo scan` command refreshes
bounded repository structure, detects only the canonical `dbt_project.yml` marker, reuses or creates
the matching registered scope, and ingests an existing `target/manifest.json` idempotently. If the
manifest is absent it reports that state and never executes dbt or other project code. `mnemo dbt
enable` remains available for explicit dbt setup.

README/PyPI copy, user and MCP guides, client connection guides, dbt wrapper documentation, threat
model, package verification, and installed-command smoke checks now describe and enforce the short
default workflow. The real cross-client evaluator also invokes the default connection commands
from its isolated project, preventing an unrelated checkout scan. The focused regression suites
pass (`148 passed`), including automatic structural retrieval, repeated scan idempotence,
default/disabled connection behavior, both aliases, and release artifact entry points. The complete
gate passes (`954 passed`, `27 skipped`; PostgreSQL integration
`26 passed`, `1 skipped`) with formatting, lint, typing, dependency provenance, architecture, schema,
and installed-package verification. This change adds no schema, dependency, model call, network
request, or new MCP tool.

### URI-only checkpoint evidence locations — Complete

The demonstrated `save_checkpoint` call supplied a valid immutable evidence URI but omitted four
unknown source-span coordinates. The MCP schema represented each evidence reference as a generic
object, while the downstream canonical parser required those coordinate keys to be present with
explicit `null` values. The sanitized adapter error therefore reported only `MNEMO_INVALID_INPUT`.
This bounded follow-up must make URI-only locations valid, continue rejecting unknown fields and
partially supplied spans, publish the nested evidence shape in the MCP input schema, retain
sanitized errors, and add no persistence, authorization, scope, or dependency change.

The checkpoint tool now publishes strict nested evidence-reference and location schemas: `uri` is
required, the four source-span coordinates are optional as a group, unknown nested properties are
disallowed, IDs are bounded, and content hashes advertise their lowercase SHA-256 shape. Runtime
nested validation remains behind Mnemo's canonical durable boundary because FastMCP/Pydantic's
pre-dispatch validation error can echo rejected input values. That boundary canonicalizes only an
exact URI-only location to four explicit `null` coordinates; partial spans, unknown fields,
malformed values, and conflicting evidence still fail before persistence with a payload-free code.

The focused MCP suite passes (`30 passed`), including schema inspection, direct durable parsing,
real stdio URI-only persistence, partial-span and unknown-field rejection, and proof that a rejected
private marker is not returned. The complete gate passes (`955 passed`, `27 skipped`; PostgreSQL
integration `26 passed`, `1 skipped`) with formatting, lint, typing, schema, dependency provenance,
architecture, and installed-package verification. Documentation now shows the concise URI-only
input. This fix adds no storage migration, dependency, authorization or scope change, model call,
network request, or new MCP tool.

### PyPI 0.1.0a7 structural-awareness and evidence-input release candidate — Complete

The approved release packages the completed automatic dbt structural retrieval, `mnemo` shorthand
and default automatic onboarding, `mnemo scan`, generated-target isolation, and URI-only checkpoint
evidence fix as `0.1.0a7`. The package metadata, lockfile, signed production and TestPyPI workflow
filenames, release verifiers, tests, and installation documentation must agree on that exact
version. Publication remains restricted to the protected production PyPI OIDC workflow, which must
rebuild and verify the committed revision and confirm the live PyPI files after upload.

The coordinated version, workflow-YAML, lockfile, CLI-version, and focused release suite pass
(`141 passed`). Independently built source-independent artifacts pass the release verifier with
these SHA-256 digests:

- wheel: `3e07a767561b44d1852bff043f68d0d6984afc825bf72d6e5d79cb5ac706f1e3`
- source distribution: `9b60431a289cc659a4b79ead7c13d1f2de40f190b0161a9c9ae0a7845dda8cb1`

The complete gate passes (`955 passed`, `27 skipped`; PostgreSQL integration `26 passed`,
`1 skipped`) with formatting, lint, typing, schema, dependency provenance, architecture, and
installed-package verification. The release remains unpublished until the committed revision is
pushed and the protected production workflow succeeds.

### Compact dbt inventory awareness — Complete

A live Claude Code workflow demonstrated that the broad question “can you see the dbt models?”
could expand into an 8,000-token `get_context` request, duplicate canonical and rendered packets,
persist oversized results to files, retry ignored selector fields, invoke shell parsing, and then
trigger a checkpoint reminder for an otherwise read-only question. This bounded defect issue must
make one saved-manifest retrieval sufficient for dbt inventory awareness.

Acceptance requires an evidence-backed aggregate containing the exact matched model count,
snapshot identity, currentness, and bounded sample metadata; strict MCP selector input that rejects
unknown fields instead of silently ignoring agent-invented `select`, `limit`, or `path` keys; and a
small broad-selector node cap so a visibility/count request cannot expand into dozens of full node
records. Existing exact package/tag selection, lineage, authorization-first scope, provenance, and
explicit larger structural workflows must remain intact. The focused regression must cover the
demonstrated request and real stdio schema/size behavior, followed by the complete repository gate.

Broad resource-type-only selection now returns one manifest-level aggregate with the exact enabled
match count, project name, snapshot, currentness, and immutable artifact evidence. Automatic dbt
model-overview routing requests only that aggregate. Exact package/tag queries retain bounded node
records, while an explicit MCP node sample is capped at eight. The MCP schema publishes every
accepted nested field and forbids extras; the durable boundary independently rejects unknown or
malformed values with a payload-free error. Tool guidance identifies the normal 1,300-token budget
and warns that `render_for` duplicates the canonical representation.

The focused dbt/application/context/automatic-memory/MCP suites pass (`117 passed`). A real stdio
regression reproduces the demonstrated 8,000-token Claude request: the invented `select` and
`limit` fields fail without echo, while the valid broad inventory returns one item below 150
declared tokens and below 12,000 serialized characters even with duplicate rendering. The complete
`npm run check` gate passes (`956 passed`, `27 skipped`; PostgreSQL integration `26 passed`,
`1 skipped`) with formatting, lint, strict typing, schema, dependency provenance, architecture,
and installed-package verification. No schema migration, dependency, authorization, scope,
retention, model call, network request, or new MCP tool was added.

An isolated scan of the real `he-dbt-bi` checkout activated its existing manifest without changing
the user's normal Mnemo profile. The exact demonstrated prompt injected one 230-character aggregate
reporting 103 models from dbt snapshot `e540e7d7-b5f2-4974-92cd-456f36d71c3b`. A fresh Claude
process with repository and file tools disabled answered from that aggregate in one turn, made no
tool call, and emitted 107 output tokens.

### Previous-session and bounded recent recap — Complete

The approved user-facing issue adds `mnemo recap` for the latest saved project handoff plus
`mnemo recap --days N` and the requested `mnemo recap --3days` shorthand for a recent window.
The same deterministic request must work conversationally through the existing `get_context` tool
and automatic prompt hook. Recap input is limited to 90 days; selection is authorization-first,
project/task scoped, bounded to 50 lifecycle events, eight newest checkpoint handoffs, and the
existing context token budget. Each item must cite its immutable checkpoint revision. Mnemo must
not replay transcripts, commands, source bodies, tool payloads, or model reasoning, and must not
make a model call.

Acceptance requires exact previous-session semantics (the newest saved handoff), an inclusive
recent-day cutoff with newest-per-checkpoint deduplication, readable CLI output, MCP schema and
sanitized validation, natural-language routing without unrelated source or knowledge search,
automatic-hook retrieval within 1,300 tokens, active-checkpoint duplicate suppression, focused
tests, documentation, and the complete repository gate. This issue adds no schema migration,
dependency, new MCP tool, model route, network surface, capture source, retention change, or team
behavior.

Implemented one storage-neutral recap query over the existing immutable checkpoint lifecycle
ledger. It authorizes the exact task scope first, reads no more than 50 descending events, applies
an optional 1-90 day cutoff, keeps the newest event for at most eight checkpoints, retrieves those
exact revisions, and spends only the supplied recap token budget. Every recap item includes the
saved objective, state, completed/remaining work, decisions, failures, blockers, timestamp, event
kind, checkpoint/revision IDs, and original evidence. Unified context suppresses a recap revision
already present as the active checkpoint.

The primary CLI renders `mnemo recap`, `mnemo recap --days 3`, and `mnemo recap --3days` as readable
project activity with exact revision provenance. Existing MCP `get_context` now accepts
`recap_days: 0..90`; zero means the latest saved handoff. Literal natural-language recap prompts
route only to checkpoint history. The automatic hook shifts category allocation—not the existing
1,300-token total—toward episodic evidence for recap prompts and performs no knowledge, semantic,
or source search.

Focused application, CLI, planner, MCP, and automatic-hook verification passes (`125 passed`). The
real `mnemo recap --3days` command returned this repository's two saved handoffs. A fresh Claude
process with repository tools disabled answered the same natural-language request from the inline
attachment in one turn, with zero tool calls or permission denials. The complete `npm run check`
gate passes (`962 passed`, `27 skipped`; PostgreSQL integration `26 passed`, `1 skipped`) with
formatting, lint, strict typing for 264 files, schema, dependency provenance, architecture, and
installed-package verification. No schema migration, dependency, new MCP tool, model call, network
surface, capture source, retention change, or team behavior was added.

### Compact generic source-graph awareness — Complete

A fresh Claude process in this non-dbt repository demonstrated that a broad architecture question
ignored the saved structural graph and scanned files. An explicit `source_overview` then returned
87,763 client-visible characters, after which the agent retried multiple query budgets and
incorrectly concluded that no saved structure existed. The active snapshot was present; the
failure is in generic routing and projection, not ingestion.

This bounded defect issue must route literal repository/codebase architecture and component
questions to one compact, provenance-bearing graph projection. That projection must contain exact
snapshot counts plus bounded component, file, module, declaration, and resolved or explicitly
unresolved relationship
samples in one token-accounted structural item. Automatic prompt retrieval must not perform
knowledge or semantic search for that intent. Focused and real-client-shaped regressions must keep
the packet below 1,300 declared tokens and 12,000 serialized characters, with no repository scan,
model call, schema migration, dependency, new MCP tool, or dbt behavior change.

Broad architecture intent now routes deterministically to `source_overview` rather than lexical
symbol search. Automatic prompt retrieval allocates structure-only space inside the unchanged
1,300-token total and performs no checkpoint, episodic, knowledge, semantic, or procedure search
for that intent. Exact implementation questions retain the existing symbol/path lookup and bounded
static-edge navigation.

The overview is now one snapshot-cited structural item rather than separate file and declaration
items. It carries exact file/symbol/edge counts, deterministic component aggregates, bounded
relative files/modules/declarations, and bounded component relationships derived from the saved
graph. Unresolved static imports remain explicitly unresolved rather than disappearing or becoming
guessed links. Its content is independently capped at 800 estimated tokens even when the caller
requests an 8,000-token outer packet; every sample has explicit selected/omitted counts. The strict
MCP overview schema rejects unknown fields, and the durable boundary repeats validation with a
payload-free error.

The focused source/context/MCP/automatic suites pass (`140 passed`). The real stdio regression uses
an empty overview selector with an 8,000-token outer allowance and proves one positive-token graph
item below the 800-token content cap and both MCP representations below 12,000 characters. The
complete repository gate passes (`968 passed`, `27 skipped`; PostgreSQL integration `26 passed`,
`1 skipped`) with formatting, lint, strict typing for 264 files, schema, dependency provenance,
architecture, and installed-package verification. No source body, schema migration, dependency,
new tool, model call, network request, dbt behavior, authorization rule, or persistence contract was
added.

The installed-checkout acceptance test refreshed snapshot
`54c8fbdc-9ad6-5f5d-8e78-9381513780f0` with 339 files, 4,948 symbols, and 29,573 static
relationships. A fresh Claude process with every tool disabled answered the generic architecture
question from that snapshot in one turn and 420 output tokens. It performed no repository scan,
MCP retrieval retry, or shell/file call.

### Read-only shell checkpoint false positives — Complete

A demonstrated dbt inventory question used a shell-backed read, after which the automatic Stop
hook treated the session as mutated, required a full checkpoint, and left the agent unable to end
a trivial read-only task cleanly. This bounded defect issue must suppress that reminder only when
Mnemo has a prior clean Git observation and a post-tool observation proves the project remains
clean at the same commit. Missing Git evidence, a dirty working tree, a changed commit, and
explicit edit/write tools must remain checkpoint-required. The hook must not inspect or retain
command text, tool output, source bodies, paths, diffs, or status payloads. This issue adds no
schema migration, dependency, MCP tool, authorization change, model call, or network behavior.

The hook now stores the clean, full-commit Git observation and active source digest in the current
session's private content-free lifecycle marker. A second fixed, shell-free Git probe must prove the
same commit and a still-clean working tree before Mnemo leaves the session unchanged. Every
unavailable, dirty, or changed observation falls back to the existing checkpoint-required path;
explicit edit/write tools remain unchanged. Command text and output are neither inspected nor
persisted.

Focused automatic-memory and Git-observation verification passes (`66 passed`), covering a clean
same-commit shell read, clean-to-dirty work, missing Git evidence, initially dirty state, command
privacy, Stop behavior, and explicit edits. A real clean temporary Git checkout reproduced the
client lifecycle—SessionStart, shell-backed read, Stop—and returned no checkpoint block. The
complete gate passes (`972 passed`, `27 skipped`; PostgreSQL `26 passed`, `1 skipped`) with
formatting, lint, strict typing for 264 files, schema, dependency provenance, architecture, and
installed-package verification. The threat model records that same-commit cleanliness proves only
the versioned project state, not ignored-file or external-system side effects.

### PyPI 0.1.0a9 read-only shell checkpoint release candidate — Complete

This approved release packages only the completed read-only shell checkpoint false-positive fix
as the next immutable prerelease. Package metadata, lockfile, installation guidance, workflow
artifact names, release verifiers, and release tests must agree on `0.1.0a9`. Publication remains
restricted to the protected production PyPI OIDC workflow, which must rebuild and verify the
committed revision, publish only its checksum-bound wheel and source distribution, and verify the
live PyPI artifacts after upload.

The focused automatic-memory and release suite passes (`115 passed`). Independently built
source-independent artifacts pass the release verifier with these SHA-256 digests:

- wheel: `d999e24211ca568505fbcd26983f2df25e954a5fba7c0e519d5c248d8f52105e`
- source distribution: `2f911960ba3797f966eebf93d185b6debc9a422314f4aafed2f33c495d9f781a`

The complete gate passes (`972 passed`, `27 skipped`; PostgreSQL `26 passed`, `1 skipped`) with
formatting, lint, strict typing for 264 files, schema, dependency provenance, architecture, and
installed-package verification. Workflow YAML parsing and the final diff whitespace check pass.
The separately observed invalid PreCompact response and proposed token analytics remain outside
this bounded release. Publication still requires the committed revision to be pushed and the
protected production workflow to succeed.

### Cost-aware automatic context routing and meta telemetry — Complete

A demonstrated investigation asked Mnemo for structural awareness, received no matching source
items, and then succeeded with a narrow exact search. The client had no prior signal for choosing
the lower-cost route and Mnemo retained no content-free outcome showing that the structural route
missed. Separately, checked-in skills are retrieved lazily only after exact tags are supplied, so
their applicability metadata cannot yet help an agent decide that a workflow applies.

This bounded issue must make automatic context selection choose the smallest applicable route
among no attachment, prior task memory, project knowledge, saved structure, and lazy checked-in
skill discovery. Selection must remain deterministic, authorization-first, client-compatible,
budgeted, and fail-open. It must not call a model, proxy or alter the agent's model endpoint, store
the prompt, source text, command text, tool input/output, or secrets, or treat model output as
authority. Mandatory checked-in procedures remain protected and deterministic.

Acceptance requires privacy-safe scenario telemetry for the chosen route, reason, hit or miss,
fallback signal when observable, final rendered byte and estimated-token cost, canonical item
tokens, duplicate-render status, and bounded latency. The user must be able to inspect aggregate
route outcomes through an existing local CLI or dashboard surface. Focused tests must distinguish
an exact low-cost lookup from architecture/impact, prior-memory, and skill-trigger scenarios;
prove lazy skill bodies remain unattached until selected; prove telemetry contains no prompt or
retrieved payload; and cover bounded retention and corrupted-state recovery. The threat model,
user guidance, and complete repository gate must be updated. No new dependency, schema migration,
MCP tool, network request, or team behavior is approved by this issue.

Automatic prompt retrieval now makes one deterministic route decision before building context. A
trivial prompt attaches nothing; an exact file, symbol, or simple file-count question recommends
direct inspection with a zero-token Mnemo attachment ceiling; recap language selects prior task
memory; architecture, impact, dependency, and dbt language selects saved structure; and remaining
domain questions probe scoped knowledge. Existing dbt inventory and recap semantics remain exact.
Route budgets are fixed inputs to the decision rather than learned from noisy telemetry, and every
failure remains fail-open.

Checked-in skills may now declare a normalized `mnemo_when` discovery description. Mnemo combines
that metadata with existing applicability tags and client compatibility using bounded local term
overlap. It returns at most three metadata-only candidates inside a 256-estimated-token catalog;
the body remains outside model context until the agent explicitly requests the exact skill through
`get_skill`. Candidate metadata is labelled untrusted and includes immutable revision/digest
identity plus an estimated body size.

The standard-library-only telemetry component stores at most 256 private content-free events with
explicit task scope, fixed route/reason/outcome values, planned attachment ceiling, canonical
tokens, final rendered characters/bytes/estimated tokens, latency, duplicate status, fallback,
and closed downstream tool categories. The hook never reads command text, tool input, or tool
output; downstream calls without a trusted metric remain explicitly unmeasured. Writes use a
private process lock, atomic replacement, mode `0600`, symlink rejection, bounded reads, and safe
corrupt-state recovery. The hook replaces preliminary render counts with the complete emitted
`additionalContext` size, including Mnemo's wrapper, and resets rejected attachments to zero.
`mnemo memory routes` exposes only exact-scope aggregates.

Focused routing, automatic-memory, and registry verification passes (`82 passed`), including
direct-versus-dbt count selection, structural miss/fallback, lazy-body privacy,
prompt/tool-payload exclusion, bounded retention, permissions, corruption recovery, final-hook
render accounting, recap compatibility, and CLI inspection. The complete `npm run check` gate
passes (`986 passed`, `27 skipped`; PostgreSQL `26 passed`, `1 skipped`) with
formatting and strict typing for 269 files, schema validation, dependency/provenance validation for
94 registered entries, architecture validation for 147 product Python files, and installed-package
verification. No dependency, migration, MCP tool, model call, endpoint, network, authorization,
retention-policy, or team behavior changed.

### PyPI 0.1.0a10 cost-aware routing release — Complete

This explicitly approved release packages only the completed cost-aware automatic context routing,
lazy skill-discovery metadata, and private route-cost telemetry issue as the next immutable
prerelease. Package metadata, lockfile, installation guidance, workflow artifact names, release
verifiers, and release tests must agree on `0.1.0a10`. Publication remains restricted to the
protected production PyPI OIDC workflow, which must rebuild and verify the committed revision,
publish only its checksum-bound wheel and source distribution, and verify the live PyPI metadata,
provenance, hashes, and installed wheel after upload.

No additional product behavior, dependency, migration, MCP tool, model call, endpoint, network
integration, authorization rule, retention policy, or team behavior is approved by this release
issue. Completion requires the focused release suite, independently built artifact verification,
the complete repository gate, workflow YAML validation, a clean committed and pushed revision,
successful protected publication, and live production verification.

Pre-publication verification passes: the focused routing, automatic-memory, skill-registry,
workflow, packaging, and release-verifier suite reports `93 passed`; both workflow YAML files parse;
source-root and installed-package verification pass; and the complete `npm run check` gate reports
`986 passed`, `27 skipped` with PostgreSQL `26 passed`, `1 skipped`. Independently built
source-independent artifacts pass the release verifier with these SHA-256 digests:

- wheel: `027e356382a38233a3bd2042135822988cd209cc7b19421ac3ca86543652a330`
- source distribution: `ad9eeab45b2a82e7a643c0a81e271e3c6160990511230d8943911de1444fa27f`

Release commit `cb175c27208c70d6eba890b4309c875555ad0459` was pushed to `main` and protected
workflow run `31242976877` completed successfully. Its independent build-and-verify job reran the
complete repository gate and deterministic evaluations, inspected and smoke-tested both artifacts,
and transferred one checksum-bound release bundle. The isolated OIDC job published only that wheel
and source distribution, and the final unprivileged job verified live PyPI metadata, provenance,
hashes, downloaded wheel installation, CLI initialization, and packaged resources.

The downloaded workflow bundle, its `SHA256SUMS`, and live PyPI metadata agree on these canonical
published SHA-256 digests:

- wheel: `6dc3fc0bf4d243892d60ae60fa576875679f6d2e06514c9fb587170eb022775a`
- source distribution: `83ab39602b38bfc40a3abaf7c196b427c29c2e74a9b9c4d49d17fb4009767e15`

Production publication and live verification are complete. No long-lived registry credential was
used.

### Local Mnemo operational guidance and verified self-upgrade — Complete

Two demonstrated failures define this bounded issue. First, a local Mnemo version, memory-status,
or hook-failure question was misrouted to OpenAI documentation and web search after session context
failed. Second, `mnemo upgrade` invoked a receipt-constrained, prerelease-unaware package-manager
command, discarded its output, and reported `upgraded` without proving the installed version
changed.

Mnemo must recognize only bounded local operational intent and emit a compact deterministic
recommendation to inspect `mnemo --version`, `mnemo status`, `mnemo recap`, and the configured hook
launcher before using external documentation. The recommendation may suggest a scoped
`AGENTS.md` fallback when equivalent guidance is absent or hooks are failing, but it must never
edit another repository automatically. The prompt remains transient and must not enter telemetry,
memory, logs, or the recommendation.

Mnemo-owned Codex and Claude Code lifecycle handlers must use a 300-second client timeout, and
reconnecting must upgrade exact owned legacy timeout values without duplicating handlers or
changing unrelated hooks. The prompt-time local diagnostics route must still return without
opening the normal retrieval runtime.

The self-upgrader must replace an exact pinned receipt with an open first-party package request
through the owning manager, refresh stale package metadata without allowing prerelease transitive
dependencies,
read the installed distribution version before and after the package-manager operation, and report
`upgraded` only when the version actually changes; an unchanged successful resolution reports
`already_current`. Version validation must expose no installer output or environment data and all
existing backup, stop, validation, restart, and fail-open boundaries remain intact.

Acceptance requires focused route, hook, telemetry, upgrade, privacy, and CLI tests; updated user
and threat-model guidance; the complete repository gate; an immutable corrective `0.1.0a13`
release; a clean commit and push; protected production PyPI publication; and live artifact and installed-version
verification. This issue adds no dependency, schema migration, MCP tool, model call, network
surface, automatic repository mutation, authorization change, or retained prompt/tool payload.

Implemented a closed `local_diagnostics` route and reason that recognize bounded Mnemo runtime and
lifecycle-failure language before opening the normal retrieval runtime. The fixed recommendation
is below 256 estimated tokens, names local version/status/recap and exact hook inspection, rejects
unrequested OpenAI documentation or web search, and includes the user-approved scoped `AGENTS.md`
suggestion with an explicit no-auto-edit policy. Explicit recap and previous-session language keeps
its existing checkpoint-history precedence. Route telemetry records only the closed route, reason,
delivery cost, and hit state; the prompt and recommendation content are not retained.

Exact Mnemo-owned Codex and Claude Code hook entries now use 300 seconds and existing owned values
are upgraded in place. The backup-gated self-upgrader replaces exact pinned receipts through an
open first-party force-install owned by uv or pipx and reads a bounded version
from the exact managed Python before and after installation, and distinguishes `upgraded` from
`already_current`. Installer output remains discarded and existing service recovery behavior is
preserved.

Focused route, automatic-memory, upgrade, privacy, telemetry, and CLI verification passes (`81
passed`). The complete `npm run check` gate passes (`990 passed`, `27 skipped`; PostgreSQL `26
passed`, `1 skipped`) with formatting, lint, strict typing for 269 files, schema validation,
dependency/provenance validation for 94 registered entries, architecture validation for 147
product Python files, and installed-package verification. Both workflow YAML files parse.
Release commit `80ce5403bb0284e86a021437c7e21e48cc7d3b49` and protected workflow run
`31245247290` successfully published `0.1.0a11`, but immediate local acceptance exposed two defects
in its uv command. Removing the exact receipt pin without `--upgrade` reused cached index metadata
and reinstalled `0.1.0a10`; adding `--upgrade` reached `0.1.0a11` but the global `allow` prerelease
policy also selected beta transitive dependencies. Therefore `0.1.0a11` does not complete this
issue and must not be represented as the verified self-upgrade release.

The corrective `0.1.0a12` command combines uv `--force --upgrade` with the explicit first-party
requirement `mnemo-unified-context>=0.1.0a0` and `--prerelease explicit`. This refreshes Mnemo's
index data and admits Mnemo prereleases without opting transitive dependencies into prereleases.
Pipx receives the same open first-party requirement with `--force --upgrade` and no global
`--pre`. The corrective focused route, automatic-memory, upgrade, privacy, telemetry, workflow,
and release suite passes (`91 passed`). The complete `npm run check` gate passes (`990 passed`, `27
skipped`; PostgreSQL `26 passed`, `1 skipped`) with formatting, lint, strict typing, schema,
dependency provenance, architecture, and installed-package verification. Source-independent
`0.1.0a12` artifacts pass the release verifier with these pre-publication SHA-256 digests:

- wheel: `395383f97047a6d4deb2c8155b21159324c0b9f23da71b94e6e94c6ccfec5b3e`
- source distribution: `f0cb87784ee2325093a6c87afe26ca6d5d15f3e96d337700d5d2ea088bb26576`

Corrective release commit `1027e39225c1516ca5ecb278bd22bb96899058af` and protected workflow
run `31245740149` successfully published and independently verified `0.1.0a12`. A clean install
proved that its open first-party requirement selects `0.1.0a12` with stable `pydantic==2.13.4`,
`pydantic-core==2.46.4`, and `starlette==1.5.0`. The end-to-end `mnemo upgrade` acceptance then
failed with `MNEMO_UPGRADE_VERSION_UNAVAILABLE`: resolving the managed environment's Python
symlink selected uv's base interpreter, which cannot see the tool environment's installed Mnemo
metadata or CLI.

The final `0.1.0a13` correction preserves the absolute managed-environment Python entry point
without dereferencing its symlink for before/after version reads, migration validation, and service
restart. A focused regression creates an actual symlink and proves both version reads and the CLI
command keep that managed path. The final focused release suite passes (`92 passed`), and the
complete `npm run check` gate passes (`991 passed`, `27 skipped`; PostgreSQL `26 passed`, `1
skipped`) with formatting, lint, strict typing, schema, dependency provenance, architecture, and
installed-package verification. Source-independent `0.1.0a13` artifacts pass the release verifier
with these pre-publication SHA-256 digests:

- wheel: `72b2f232040970a75a1c1b1803069126546489d16876eb090c19ea1508119ef6`
- source distribution: `03523720b079235a95df2df494a4414cae129a0117ef99180ddd6288d4ca8c28`

Final release commit `c63fdfe4bc229c13248f0c9ae4582d868f50f986` was pushed to `main`, and
protected production workflow run `31246662186` completed successfully. Its independent verifier
confirmed the published metadata, artifact hashes, downloaded wheel, installed CLI, and packaged
resources. Live PyPI metadata and the resolver-facing simple index agree on these canonical
published SHA-256 digests:

- wheel: `4680b6e6d6b03ddfdd377987e5e342c85a2f8ed6c0825120041840b5720dd3f7`
- source distribution: `d6c08f1412a19acaa232afedcf3924966d121f8fa4c2b8e8a123ae2cc205a1ef`

After normal PyPI simple-index propagation, the exact documented uv command installed
`0.1.0a13` while retaining stable `pydantic==2.13.4`, `pydantic-core==2.46.4`, and
`starlette==1.5.0`. The live installed `mnemo upgrade` completed with
`before_version==after_version==0.1.0a13`, `changed=false`, and `status=already_current`. The exact
installed hook launcher reports `0.1.0a13`, and all five Mnemo-owned Codex lifecycle handlers have
the single timeout value `300`. Production publication and live verification are complete.

### Local MCP cold-start handshake — Complete

Codex `0.147.0` reported `MCP startup interrupted` for `mnemo-memory` when started from the
registered ATK path. The per-process client timeline proves this is latency rather than a server
crash: Mnemo was pending when Codex ended its initial startup phase and completed initialization
roughly 27 seconds later. A subsequent warm handshake completed below one second. ATK resolves to
the parent `~/Desktop/local/personal` Git root, and the local MCP composition root currently opens
and migrates SQLite and synchronously refreshes that registered source projection before starting
the stdio transport.

This bounded correction must make server construction, MCP initialization, and `tools/list`
independent of local storage and source parsing. The durable runtime must initialize exactly once
on the first real Mnemo tool call. A source projection refresh must occur only when a context
request explicitly asks for source structure, or through the existing post-checkpoint observer;
normal checkpoint, recap, and skill calls must not pay repository-parse latency. Process shutdown
must close an initialized runtime without constructing an unused one. Existing five-tool schemas,
scope resolution, fail-closed authorization, fail-open source observation, and sanitized storage
errors must remain unchanged.

Acceptance requires focused deferred-composition and real-stdio regressions, the complete
repository gate, and an installed-style ATK handshake measurement. This issue adds no dependency,
schema migration, MCP tool, network surface, authorization change, background daemon, retained
payload, or automatic canonical memory mutation.

Implemented a process-local deferred MCP port that constructs the existing durable runtime exactly
once on the first storage-backed tool call and closes it only when it was initialized. FastMCP
construction, protocol initialization, and tool listing no longer resolve local configuration,
open or migrate SQLite, load personal settings, or parse registered source files. Known lazy
configuration and storage failures return the fixed `MNEMO_STORAGE_UNAVAILABLE` boundary through
the tool response instead of terminating the MCP handshake.

The former unconditional startup source refresh is now a one-time, fail-open refresh immediately
before the first explicit `source_query`, `source_impact`, `source_changes`, or `source_overview`
request. Normal checkpoint, recap, skill, and knowledge retrieval do not parse the repository.
Post-checkpoint source observation remains unchanged. Enabled-project real-stdio coverage proves
an explicit source request still sees source changes made before process launch.

Focused deferred-composition and real-stdio verification passes (`34 passed`). From the real ATK
working directory, the source-built `mnemo-memory mcp serve --stdio` launcher completes protocol
initialization in `1.472` seconds and lists all five tools in `0.002` seconds, for `1.474` seconds
total. A first normal scoped context request initializes SQLite lazily and completes in `0.019`
seconds without repository parsing. The complete `npm run check` gate passes (`993 passed`, `27
skipped`; PostgreSQL `26 passed`, `1 skipped`) with formatting, lint, strict typing for 269 files,
schema validation, dependency provenance for 94 registered entries, architecture validation for
147 product Python files, and installed-package verification.

### Checkpoint token-estimate omission — Complete

Three valid `save_checkpoint` create requests were rejected with the sanitized
`MNEMO_INVALID_INPUT` response because the MCP schema advertised `token_estimate` as optional
while the durable request translator required a non-negative integer. Optional checkpoint list
fields correctly defaulted to empty values and did not cause the rejection.

This bounded correction must deterministically estimate checkpoint tokens when the caller omits
the estimate, while retaining an explicitly supplied estimate as required by ADR 0002. The
existing local, tokenizer-free checkpoint heuristic and 600-token write budget remain
authoritative. `record_lesson` and `record_event` must not require an irrelevant token field,
sanitized errors must remain payload-free, and no schema, dependency, storage, authorization,
retention, or network change is permitted.

Acceptance requires focused durable-MCP and tool-schema regressions plus the complete repository
gate.

The durable MCP translator now constructs canonical checkpoint content with a zero placeholder
only when the caller omits `token_estimate`, then replaces it with the existing deterministic
checkpoint estimate before application-policy validation or persistence. Explicit caller
estimates remain unchanged. The MCP schema documents the optional fallback and keeps
`record_lesson` and `record_event` free of an irrelevant requirement.

Focused MCP verification passes (`35 passed`), including a regression that reproduces the
previously rejected minimal create payload and proves the persisted estimate matches the exact
deterministic heuristic within the 600-token budget. The complete `npm run check` gate passes
(`994 passed`, `27 skipped`; PostgreSQL `26 passed`, `1 skipped`) with formatting, lint, strict
typing for 269 files, schema validation, dependency provenance for 94 registered entries,
architecture validation for 147 product Python files, and installed-package verification.

### Release 0.1.0a14 — Complete

The user approved deployment of the completed local MCP cold-start and checkpoint
token-estimation corrections. PyPI returned 404 for the exact `0.1.0a14` metadata endpoint before
the bump, confirming the immutable version was unused. Current package metadata, lockfile,
install documentation, artifact verifiers, workflow contracts, and workflow tests target
`0.1.0a14`; historical `0.1.0a13` publication evidence remains unchanged.

Production publication must use the manual protected `publish-pypi.yml` workflow, transfer the
single verified wheel/sdist/checksum bundle without rebuilding it, publish through environment
OIDC, and complete the independent PyPI metadata, provenance, hash, download, install, and smoke
verification job. No TestPyPI publication, dependency addition, migration, or configuration
mutation is part of this release.

Focused MCP and release verification passes (`46 passed`), both release workflow YAML files
parse, and the complete `npm run check` gate passes (`994 passed`, `27 skipped`; PostgreSQL `26
passed`, `1 skipped`) with formatting, lint, strict typing, schema validation, dependency
provenance for 94 registered entries, architecture validation for 147 product Python files, and
installed-package verification. The source-independent `0.1.0a14` artifacts pass the release
verifier with these local pre-publication SHA-256 digests:

- wheel: `6800a2473dcce35fa6d3ac99aaefd74fafaae8f08f0a2f835b562fb1bb9e8dbb`
- source distribution: `c7c810583ae63a1de6fe82970fb3a5e61b4349d32c5fdb9debbf00e6beceb38b`

GitHub secret scanning reports no open alerts. Dependabot alerts are disabled and code scanning
has no analysis for this repository; the locked dependency/provenance, architecture, full-test,
artifact, and protected-publication gates above remain the available release controls.

Release commit `7f1cc18bb6478a3f0da853d2113c33b61cb302fe` was pushed to `main`, and
protected production workflow run `31250232892` completed successfully. Its isolated build,
repository/evaluation, artifact inspection, wheel/sdist smoke, OIDC publication, and independent
PyPI metadata, provenance, hash, download, install, and initialization jobs all passed. Live PyPI
metadata and the immutable workflow bundle agree on these canonical SHA-256 digests:

- wheel: `9a5de7f5e540184906112fd5ed24224e54ff0a4230b10db9e1c59f393839470a`
- source distribution: `6746bef91c593528ebae075bee14afe1c7f2f4085430c3bfce648b3dc1f96482`

The managed local uv installation upgraded from `0.1.0a13` to `0.1.0a14` after creating a verified
schema-v30 backup. From the real ATK working directory, the installed launcher initialized MCP in
`1.589` seconds, listed all five tools in `0.004` seconds, and returned a normal bounded context in
`0.058` seconds. An isolated installed-package regression then submitted the previously failing
minimal checkpoint without optional list fields or `token_estimate`; the save succeeded, persisted
an active checkpoint, and recorded Mnemo's deterministic estimate of `104` tokens. Production
deployment and local acceptance are complete.

### Token-bounded single-delivery automatic context — Complete

The demonstrated automatic-memory path can advertise a bounded canonical packet while delivering a
materially larger client rendering, because repeated scope, evidence, digest, timestamp, ranking,
and renderer metadata are outside the canonical item estimate. At `SessionStart`, Mnemo also
attaches that retrieved packet while instructing the agent to call `get_context` again, causing an
avoidable second retrieval of overlapping durable memory.

This bounded issue must add a compact deterministic rendering used only by Mnemo-owned automatic
hooks. It must preserve the explicit trust boundary, packet-level scope, item content, item/source
identity, trust, validity, conflict state, and bounded evidence identity while avoiding repeated
per-item envelope fields. The complete automatic attachment must fit its route or session token
ceiling using Mnemo's existing character estimate; lower-priority records that do not fit must be
omitted deterministically with a content-free token-budget notice, while selected mandatory
procedures retain priority. The canonical packet and the existing explicit MCP `render_for`
projection remain unchanged.

When a valid session-start attachment is present, the lifecycle instruction must tell the agent to
use that already-checked bounded context and must not require a redundant `get_context` call. If no
attachment is available, the existing fail-open retrieval instruction remains. Existing dirty-stop
and pre-compaction checkpoint behavior remains unchanged.

Acceptance requires focused renderer, automatic-memory, client-hook, privacy, and telemetry
regressions plus the complete `npm run check` gate. The issue adds no dependency, migration, MCP
tool, model call, provider endpoint change, Redis service, authorization change, retention or
episodic-promotion policy, raw prompt/tool payload telemetry, or `explain_context` redesign. Route
policy, lifecycle-wide analytics, write-path cost measurement, and memory maturation remain later
explicit issues.

Implementation is complete. Mnemo-owned session and prompt attachments now use a deterministic
compact projection capped by the existing session or route ceiling with the existing character
estimate. It keeps the trust boundary, packet scope, content, compact source/evidence provenance,
trust, validity, and conflict state; selected mandatory procedures are considered first, and
records that do not fit produce one content-free token-budget omission. The canonical packet and
explicit MCP `render_for` projection are unchanged. SessionStart loads a valid attachment once and
uses it without requesting an overlapping `get_context`; the prior scoped retrieval instruction is
retained only when no valid attachment is available. Prompt-route telemetry continues to persist
only content-free sizes and now demonstrates that rendered estimates remain within the selected
route ceiling.

Focused context-engine and automatic-memory verification passed `81` tests. The complete
`npm run check` gate then passed: formatting, lint, type checking, `996` regular tests with `27`
expected environment-gated skips, `26` PostgreSQL migration/RLS tests with one expected load-test
skip, schema validation, dependency/provenance checks, architecture checks, and installed personal
workflow verification. No dependency, schema, migration, storage, retention, promotion, Redis, or
provider change was made. This issue is complete; any cache tier or memory-maturation policy needs
a separately approved issue.

### `0.1.0a15` production deployment — Complete

The user explicitly requested deployment of the completed token-bounded single-delivery automatic
context change. Release metadata, pinned artifact names, verification scripts, user-facing install
examples, release-contract tests, and the committed lockfile are being advanced together from
`0.1.0a14` to the next unused immutable version, `0.1.0a15`. The release must use the protected
manual production PyPI workflow from a clean pushed `main` commit. Completion requires the complete
local gate, protected workflow build and evaluation gates, checksum-bound OIDC publication, live
PyPI metadata and provenance verification, exact artifact hashes, and an isolated installed-package
smoke test. No TestPyPI or direct local upload is authorized or needed for this production release.

Production deployment is complete. The exact PyPI version endpoint returned `404` before the bump,
confirming that `0.1.0a15` was unused. The release tree passed the complete local `npm run check`
gate, the source verifier, workflow contract tests and YAML validation, and source-independent local
wheel/sdist inspection. Clean release commit `990ec18446708e978fff1b9393f4508ac254660e`
was pushed to `main` and protected production workflow run `31349469982` completed successfully.
Its clean-runner repository and deterministic evaluation gates, artifact build and smoke tests,
checksum bundle, OIDC publication, and separate live PyPI verification jobs all passed.

The downloaded immutable workflow bundle and live PyPI metadata/provenance agree on these canonical
SHA-256 digests:

- wheel: `3cb593c68e36acb05ee5c426933310eca74160c9e0766b14a285211a68f7a97d`
- source distribution: `4de0d62f042eec2d6b7f86bf69dd32a790ca81522f798e9dd5072501ac3978e1`

An independent post-workflow verification downloaded both production artifacts, rechecked the
workflow checksum bundle, validated PyPI metadata and integrity provenance, and installed the exact
published wheel into a fresh Python 3.12 environment. The isolated command reported
`mnemo 0.1.0a15`, imported from `site-packages`, exposed the compatibility command, and initialized
a fresh personal database at schema version `30`. Production deployment and acceptance are
complete.

### Compact local memory router — Complete

The user explicitly approved a small memory router after reviewing evidence for adaptive
retrieval and model cascades. This bounded issue adds one Mnemo-owned, standard-library text
classifier only at the ambiguous automatic-context boundary. Existing literal rules remain
authoritative for trivial prompts, exact source lookup, local diagnostics, explicit prior-memory
language, checked-in skill discovery, dbt, architecture, impact, and explicit knowledge intent.

The classifier may select no attachment, prior task memory, or the existing bounded knowledge
probe. Low-confidence output must retain the current knowledge-probe behavior, and every route must
remain authorization-first, token-bounded, inspectable, content-free in telemetry, and fail-open.
The classifier must not answer the user's task, choose scope or authority, persist a prompt, call a
provider, download model weights, modify the configured coding model, or authorize memory writes.

Acceptance requires held-out routing regressions with complete prior-memory recall and conservative
no-memory precision, prompt/privacy and telemetry coverage, documented threat-model boundaries,
focused tests, and the complete `npm run check` gate. No dependency, model endpoint, schema,
migration, Redis service, retention rule, or memory-promotion policy is part of this issue.

Implementation is complete. Authoritative literal routes still run first. At only the former
general-memory fallback, an embedded multinomial classifier built from Mnemo-owned synthetic
examples now proposes no attachment, prior task memory, or project knowledge. Prior-memory
selection is recall-oriented; no-memory selection requires both a high posterior and a separating
margin; all uncertainty retains the existing bounded knowledge probe. The classifier generates no
answer, performs no I/O, persists no prompt or score, and cannot select scope, authority, evidence,
budget, retention, or mutation behavior.

Focused routing and automatic-memory verification passed `75` tests. The held-out classifier
fixture correctly routed all `16` examples, including `6/6` continuation paraphrases and `6/6`
self-contained prompts; hook telemetry retained the closed route reason and zero delivered tokens
without retaining the private prompt marker. The complete `npm run check` gate passed: formatting,
lint, strict typing for `269` files, `1,000` regular tests with `27` expected environment-gated
skips, `26` PostgreSQL migration/RLS tests with one expected load-test skip, schema validation,
dependency/provenance checks for `94` entries, architecture checks for `147` product Python files,
and installed personal workflow verification. No dependency, weight download, provider call,
schema, migration, storage, Redis, retention, promotion, or configured-agent model change was made.

### Codex PreCompact output compatibility — Complete

The installed Codex `0.147.0` command schema accepts only the common `continue`, `stopReason`,
`suppressOutput`, and `systemMessage` fields from a `PreCompact` hook. Mnemo currently emits the
`hookSpecificOutput.additionalContext` shape that Codex accepts at `SessionStart`, so Codex rejects
the otherwise valid JSON before compaction.

This bounded fix must keep compaction fail-open, preserve the project-scoped pending-handoff
marker, emit no unsupported Codex `PreCompact` fields, and attach the existing bounded context and
checkpoint reminder at the following compact-origin `SessionStart`. Claude Code behavior remains
unchanged. Acceptance requires a focused lifecycle regression, the complete `npm run check` gate,
and a real installed-hook invocation whose Codex `PreCompact` output is valid empty JSON. No schema,
migration, dependency, storage, model, routing, retention, or authorization change is included.

Implementation is complete. A dirty Codex `PreCompact` still refreshes bounded project state and
sets the scoped pending-handoff marker, but now returns `{}` instead of the unsupported
`hookSpecificOutput.additionalContext` object. The next compact-origin `SessionStart` uses its
supported `hookSpecificOutput` schema to attach the existing bounded context and pending-checkpoint
reminder. Claude Code retains the prior PreCompact attachment behavior.

The focused automatic-memory and routing suite passed `75` tests. The complete `npm run check`
gate passed: formatting, lint, strict typing for `269` files, `1,000` regular tests with `27`
expected environment-gated skips, `26` PostgreSQL migration/RLS tests with one expected load-test
skip, schema validation, dependency/provenance checks for `94` entries, architecture checks for
`147` product Python files, and installed personal workflow verification. The verified working
tree was installed into the uv-managed `0.1.0a15` command. A full-schema installed smoke marked a
synthetic session dirty, observed valid empty JSON with no keys from PreCompact, then observed a
compact-origin SessionStart with bounded context and the pending-checkpoint reminder. No dependency,
schema, migration, storage, model, routing, retention, authorization, or published-release change
was made.

### Long-prompt, structural-route, and reminder token safety — Complete

The user approved the follow-up router-safety issue and asked that ambiguous structural-knowledge
intent also be classified. This issue must route every nonblank prompt through one transient,
bounded head/tail view; prevent repeated prompt features from amplifying classifier evidence; keep
no-memory selection conservative; allow the classifier to select only the existing deterministic
structural projection; and stop repeating the dirty-session reminder on every prompt. Exact dbt and
source rules remain authoritative, authorization still precedes retrieval, and no prompt or bounded
view may be persisted or emitted through telemetry.

Acceptance requires focused long-prompt, padding, structural-routing, privacy, and one-shot lifecycle
regressions; a balanced original synthetic routing fixture with cost-weighted recall and no-memory
precision gates; threat-model and user-documentation updates; the complete `npm run check` gate; and
a local installed-hook smoke. No dependency, model weight, provider call, Redis service, schema,
migration, retention, promotion, or configured-agent model change is included.

Implementation is complete. Every nonblank prompt now uses one transient head/tail view of at most
512 characters consistently across literal routing, the presence-only classifier, skill discovery,
and retrieval-query construction. Route-aligned boundary lines avoid letting pasted log/code noise
swamp a short request. Duplicate unigrams and bigrams contribute once, so repeating known padding
does not multiply evidence. The classifier can now propose the existing `structure` route, but only
deterministic authorized projections supply source and dbt facts; uncertainty still falls back to
bounded knowledge, and the strict no-memory score plus margin remains the only way to suppress an
attachment.

The dirty-session prompt cue is now `MNEMO_DIRTY_V1`, 250 characters rather than 497 (about 63
rather than 125 tokens under Mnemo's documented character estimate). A private session-state
boolean emits that base cue once per dirty checkpoint cycle. Repeated prompts and further edits do
not resend it; Stop and PreCompact still enforce the handoff, and a verified new checkpoint resets
the next cycle. Prompt text and the bounded view remain absent from state and telemetry.

Focused routing, lifecycle, privacy, long-prompt, and evaluation verification passed `84` tests,
including proof that a secret-bearing bounded view cannot construct the optional embedding provider.
The balanced 60-case original synthetic fixture passed `56/60` (`93.3%`) overall with `15/15`
prior-memory recall, `14/15` structural recall, and `100%` precision whenever no-memory was chosen.
The two earlier 45-prompt screens improved from `84.4%` to `97.8%` and from `64.4%` to `88.9%`,
both with complete prior-memory recall and no false no-memory selection. The complete
`npm run check` gate passed: formatting and linting, strict typing for `270` files, `1,009` regular
tests with `27` expected environment-gated skips, `26` PostgreSQL migration/RLS tests with one
expected load-test skip, schema validation, dependency/provenance checks for `94` entries,
architecture checks for `147` product Python files, and installed personal workflow verification.
No dependency, weight, provider, schema, migration, Redis, retention, promotion, or published
release change was made.

The verified working tree was then installed into the existing local `0.1.0a15` uv tool. The first
same-version `uv tool install --force .` reused a cached build; the required exact-launcher smoke
caught the old repeated reminder. Reinstallation with `--reinstall` rebuilt the local artifact, and
the installed `hook.py` SHA-256 then matched the working tree at
`34e2ad394d50a0ab5f2fc0579bd667af2407acbae196bc60917e764b9f9cfefa`. In an isolated enabled
project, the configured launcher emitted `MNEMO_DIRTY_V1` at the first dirty prompt and `{}` at the
second. Its installed router selected `structure` from a long prompt and produced identical
decisions and scores after two versus twenty repetitions of the same padding phrase.

### `0.1.0a16` production deployment — Complete

The user explicitly approved production deployment of the completed PreCompact compatibility,
long-prompt routing, structural classification, secret-gating, and one-shot reminder changes.
The exact production PyPI version endpoint returned `404` before the bump, confirming that
`0.1.0a16` was unused. Release metadata and pinned artifact references now target `0.1.0a16`.

Acceptance requires the complete local gate, source and artifact verification, a clean release
commit pushed to `main`, successful protected `publish-pypi.yml` execution, live PyPI metadata and
integrity-provenance verification, and an isolated install of the exact published wheel. No
TestPyPI or direct local upload is authorized or needed for this production release.

Production deployment is complete. Release commit
`0c29929e8c72bd1251859ba445a88a197a8306f1` was pushed to `main`, and protected workflow run
`31392699824` completed successfully. Its clean-runner repository and deterministic evaluation
gates, source-independent artifact build, wheel/sdist smoke tests, checksum-bound OIDC publication,
and separate live PyPI verification jobs all passed.

The immutable workflow bundle and production PyPI metadata/provenance agree on these SHA-256
digests:

- wheel: `979016ca3e5b8f5d38bf18da2f5c5c79f34fd1ee59be995d6178e8ad79649199`
- source distribution: `71acc237b3d35c062a657b648cb808353bc8215740eb37317741208feeb18264`

An independent post-workflow audit downloaded that bundle, verified its checksum manifest and
PyPI integrity provenance, and installed the exact published wheel into a fresh Python 3.12
environment. The isolated command reported `mnemo-memory 0.1.0a16`, imported from `site-packages`,
loaded packaged resources, and initialized a fresh personal database at schema version `30`.

### Shadow structural/long-term planner, diagnostics, and optional Potion router — Complete

The user explicitly approved the first staged slice of a two-axis structural/long-term planner after
reviewing local routing benchmarks and an independent architecture review. This issue also includes
project-scoped phrase teaching through the user-facing `mnemo learn --phrase "..." --as ROUTE` and
`mnemo forget --phrase "..."` commands, an opt-in content-free decision footprint, and a checkpoint
hook instruction that asks the agent to stay below 450 Mnemo-estimated tokens. Complete command help
and user documentation are required. Mnemo must never silently persist prompts seen by automatic
hooks; learning is an explicit user-authorized mutation only.

Live automatic attachment remains unchanged and mutually exclusive during this issue. In shadow
mode, structural need and long-term-memory need are independently classified as yes, no, or unknown
under one 1,300-token planning ceiling, so a prompt may require both. Authoritative literal and
deterministic routes remain first. The pinned local Potion embedding adapter may propose only when
the live router returns its existing uncertain knowledge fallback. Its proposal cannot select scope,
authority, source truth, token budgets, retention, mutation, dbt lineage, direct lookup, or live
attachment, and a learned/model `none` proposal cannot suppress memory. Missing dependencies, absent
or invalid weights, loading failure, and inference failure preserve the current bounded knowledge
fallback without blocking the coding agent. Ordinary hooks perform no network access; model setup is
explicit, revision-pinned, and digest-verified.

Learned phrases must be bounded, secret-scanned, normalized for exact duplicate/forget behavior,
stored in an owner/workspace/project-scoped private local document with source, sensitivity, and
creation provenance, and capped against unbounded growth. Forgetting must be exact, scoped, and
idempotent, and must leave no derived cache under Mnemo's control. The model receives only the same
transient 512-character head/tail routing view already approved; prompts, embeddings, scores, and
predictions remain absent from telemetry and durable state.

Trace diagnostics are explicit and fail-open. They may retain only closed decision fields, route
costs, latency, subsequent closed tool categories, and an explicit helpful/noise/missing label for a
bounded TTL. They never retain prompts, paths, payloads, embeddings, scores, hidden reasoning, or
chain of thought, and they report correlation rather than causation. Turning diagnostics off stops
new events; exact-scope purge removes existing events. No diagnostic label directly changes routing.

Acceptance requires combined-axis, shared-budget, unchanged-live-route, checkpoint-size-instruction,
pinned dependency/model provenance, original route examples, routing and cost-weighted evaluation,
secret and cross-project isolation, corrupt-state, backward-compatible telemetry, model-failure
fallback, CLI help/documentation, warm/cold latency evidence, the complete `npm run check` gate, and
a local installed workflow smoke. No live combined attachment, PreToolUse injection, hosted provider,
configured agent model change, Redis service, automatic phrase promotion, automatic memory-age
promotion, team-mode learning, Glimmer runtime, or deployment is part of this issue.

Implemented the bounded shadow planner, private scoped phrase store, explicit teach/forget commands,
summary/trace/off diagnostics with TTL and feedback labels, digest-pinned optional Potion adapter, and
compact checkpoint instruction. The current live attachment route remains unchanged. On the held-out
60-prompt evaluation, deterministic routing classified 56 prompts correctly and the uncertainty-only
hybrid classified 57; Potion ran for four uncertain prompts, improved one, and introduced no
regression. On the validation machine, first classification took 329.32 ms; the 100-sample warm
median was 0.15 ms and p95 was 0.20 ms.

The complete `npm run check` gate passed: 1,024 tests passed with 27 expected skips, the isolated
PostgreSQL suite passed 26 tests with one opt-in load test skipped, and schema, dependency,
architecture, and installed-package checks passed. A separately built wheel with the `router` extra
installed into a fresh Python 3.12 environment, exposed all new CLI help surfaces, and returned a
valid local router status without downloading model files.

### `0.1.0a17` production deployment — Complete

The user explicitly approved production deployment and local installation of the completed shadow
planner, compact checkpoint guidance, explicit phrase teaching, content-free diagnostics, and
optional Potion router. Release preparation advances the pinned prerelease version to `0.1.0a17`
without changing the approved runtime boundary: live attachment remains unchanged and the new
two-axis planner remains shadow-only.

Acceptance requires a clean complete repository gate, one reviewed release commit pushed to `main`,
successful protected `publish-pypi.yml` execution, live PyPI metadata and integrity-provenance
verification, and installation of the exact published `0.1.0a17` package with the `router` extra on
this Mac. No TestPyPI publication, direct local upload, or later live-routing issue is authorized.

Production deployment is complete. Release commit
`74d851842428dc7d959daca0ce9a65080635fa2e` was pushed to `main`, and protected workflow run
`31416676176` completed successfully. Its clean-runner repository and deterministic evaluation
gates, source-independent build, wheel/sdist smoke tests, checksum-bound OIDC publication, and live
PyPI verification all passed.

The immutable workflow bundle, downloaded production artifacts, and PyPI integrity provenance agree
on these SHA-256 digests:

- wheel: `984a1efc2856e03df2603006525d37db69d92d5c7d2e21158f563ebcbfc6e7f5`
- source distribution: `542387c240c025a063c166a2cf4097f6e78a295cbc6546ec9098d4225a3f766d`

The first name-based local install attempt occurred before the PyPI simple index exposed the new
version and made no installation change. The exact checksum- and provenance-verified production
wheel was then installed with the `router` extra. The installed command reports `mnemo 0.1.0a17`,
imports from the uv tool's `site-packages`, retains the existing Codex connection, and reports the
pinned Potion model revision as installed and enabled in uncertainty-only shadow mode. Ordinary
hooks remain network-free.

### Human-readable route diagnostics table — Complete

The user explicitly approved a native human-readable view for the existing content-free route
footprints, followed by testing and production deployment. This bounded issue adds
`mnemo memory diagnostics show --format table`; `--format json` remains the default and preserves
the existing machine-readable object for scripts.

The table must be deterministic plain text produced with the standard library, require no terminal
or formatting dependency, and include time, live route/reason, shadow structure/history decisions,
estimated attachment tokens, routing latency, Potion result, feedback, and the full event ID needed
by `diagnostics mark`. Missing pre-trace values render as `-`; empty results still render a header and
the existing correlation notice. Invalid formats must fail through the CLI without reading or
mutating telemetry. No stored schema, retention, routing, model, prompt handling, or live attachment
behavior changes in this issue.

Acceptance requires backward-compatible JSON coverage, traced and legacy table rows, empty output,
invalid-format help, user documentation, an installed-wheel smoke, the complete `npm run check`
gate, and production deployment plus installation of the exact verified next prerelease.

Implemented the standard-library table renderer with deterministic column widths, numeric
alignment, UTC timestamps, explicit missing-value markers, Potion results, feedback, and full event
IDs. The JSON default and object shape are unchanged; unsupported formats are rejected by the CLI
before the command reads telemetry. User documentation and command help describe both formats.

The focused telemetry/CLI suite passed seven tests. The complete `npm run check` gate passed with
1,024 tests and 27 expected skips; the isolated PostgreSQL suite passed 26 tests with one opt-in load
test skipped. Formatting, linting, strict typing, schema, dependency, architecture, and the enhanced
installed-wheel table smoke all passed.

### `0.1.0a18` production deployment — Complete

The user explicitly approved production deployment and local installation of the completed native
diagnostics table. Release preparation advances the pinned prerelease version to `0.1.0a18` without
changing telemetry storage, retention, routing, model, prompt, or live attachment behavior.

Acceptance requires a reviewed release commit pushed to `main`, successful protected
`publish-pypi.yml` execution, live PyPI metadata/hash/integrity-provenance verification, installation
of the exact published package with the existing `router` extra on this Mac, retained Potion model
readiness, and a successful native `--format table` command from the installed distribution. No
TestPyPI publication or direct local upload is authorized.

Release commit `e9daa69a0ef33cd68ae6d4cad86d24dbdaa299f3` was pushed to `main` and
protected production workflow run `31419614776` passed repository verification, artifact smoke
tests, OIDC publication, and independent PyPI verification. The immutable artifact hashes are
`4ac5373d0a50f050510dd27b94a9a97a34cf0f2cc1288854ac4bef43ccf168ee` for the wheel and
`651d04e0b42f993f1e617a8898851216de4e6651a0e4eee8549b7fcc7117d7fb` for the source
distribution; a separate live PyPI metadata, hash, provenance, and wheel comparison also passed.

The exact verified wheel was installed locally with the `router` extra. The installed command
reports `mnemo 0.1.0a18`, the existing Codex connection remains active, and pinned Potion revision
`bf8b056651a2c21b8d2565580b8569da283cab23` remains installed and enabled in uncertainty-only
shadow mode. The installed `mnemo memory diagnostics show --format table --limit 3` command rendered
the local diagnostic events and correlation notice successfully.

### Selective-push/lazy-pull shadow policy and complete route diagnostics — Complete

The user explicitly approved the first evidence-gathering slice of the researched token-efficiency
recommendation. This issue changes only the opt-in shadow planner and content-free diagnostics. Live
automatic attachment remains unchanged: no prompt receives less or different context because of
this issue.

The shadow policy must preserve explicit deterministic structure and long-term routes, propose no
attachment for clear self-contained/current-output prompts, and convert unresolved prompts from an
automatic 1,300-token knowledge proposal to a bounded `lazy_pull` action representing a static hint
of no more than 40 estimated tokens. The hint tells an agent to use the existing scoped
`get_context` tool only when prior project decisions or repository structure could materially change
the answer; this issue does not add a second recall tool or inject the hint live. Project-scoped
learned phrases remain deterministic inputs. Potion remains available for explicit evaluation but
must not load or run synchronously in automatic trace hooks.

Diagnostics must remain prompt-free and backward compatible while exposing the shadow action,
proposed attachment-token cost, live outcome, route latency, shadow-planning latency, and their
combined routing time in JSON and the native table. Existing semantic/Potion fields remain readable
for historical events. Telemetry must describe correlation, not causation, and must not store
prompts, paths, payloads, embeddings, scores, or hidden reasoning.

Acceptance requires focused deterministic routing, current-output, uncertain lazy-pull, explicit
combined-axis, learned-phrase, no-synchronous-Potion, closed `get_context` correlation, legacy
telemetry, JSON/table, privacy, and
failure-isolation tests; updated ADR, threat model, user documentation, and implementation status;
the complete `npm run check` gate; and an installed-wheel smoke before release. Live selective
attachment, automatic learning, a new retrieval tool, Redis, a hosted provider, or model endpoint
changes are outside this issue. Production deployment and local installation are explicitly
authorized only after this issue is complete and will be tracked as the next issue.

Implemented the shadow-only selective policy with closed `none`, `push_structure`,
`push_long_term`, `push_both`, and `lazy_pull` actions. Unresolved prompts now propose a fixed
30-token `get_context` hint instead of a 1,300-token knowledge attachment, while narrowly recognized
current-output prompts propose zero tokens. These are counterfactual measurements only: live
attachment remains byte-for-byte governed by the prior route. Trace hooks no longer load or invoke
Potion; its installed model is reported as explicit-evaluation-only.

New trace events add a validated action, proposed token estimate, and shadow-planning duration.
JSON and table views expose live outcome, live/shadow/combined routing latency, proposed cost, and
historical Potion latency. A later `get_context` call is retained only as `context_recall`, never as
its query or result. Both aggregate-only and original shadow event shapes replay successfully.

The focused routing, telemetry, hook, Potion-boundary, and evaluation suite passed 101 tests. The
complete `npm run check` gate passed with 1,026 tests and 27 expected skips; the isolated PostgreSQL
suite passed 26 tests with one opt-in load test skipped. Formatting, linting, strict typing, schema,
dependency, architecture, and installed-package checks passed. No dependency, model artifact,
storage migration, live attachment, or configured agent endpoint changed.

### `0.1.0a19` production deployment — Complete

The user explicitly approved production deployment and local installation after the completed
selective-push/lazy-pull shadow and diagnostics issue. Release preparation advances the pinned
prerelease version to `0.1.0a19` without promoting the shadow policy to live attachment. Potion
remains installed only for explicit local evaluation and automatic hooks remain deterministic.

Acceptance requires a reviewed version-only release delta on top of the completed implementation,
a clean complete repository gate, one release commit pushed to `main`, successful protected
`publish-pypi.yml` execution, live PyPI metadata/hash/integrity-provenance verification, installation
of the exact published wheel with the existing `router` extra on this Mac, retained Codex connection
and diagnostics mode, and an installed table/router-status smoke. No TestPyPI publication, direct
local upload, live selective attachment, or later routing issue is authorized.

Production deployment is complete. Release commit
`7634ecc23caa56356a5344ca825bd28bdc634f32` was pushed to `main`, and protected workflow run
`31450848976` passed clean-runner repository verification, deterministic evaluations,
source-independent artifact build and smoke tests, checksum-bound OIDC publication, and independent
live PyPI metadata, hash, provenance, and wheel verification.

The immutable workflow bundle and separately downloaded PyPI artifacts agree on these SHA-256
digests:

- wheel: `e73cfc533c226b2abaddeb5cf34d4af20c8ba9700019edb3084dfbfb47bee0f6`
- source distribution: `926d03df692c0cbcd237b7b421d8802a0e483156bdf4a2275507786c71931bfd`

The exact verified wheel was installed locally with the `router` extra. The installed command
reports `mnemo 0.1.0a19`; `mnemo connect codex --check` reports connected; diagnostics remains in
trace mode with seven-day retention; and the pinned Potion revision remains installed and enabled
but now reports `explicit_evaluation_only` and `used_by_automatic_hooks: false`. Installed synthetic
hook smokes produced `none` with zero proposed tokens for a current-output prompt and `lazy_pull`
with 30 proposed tokens for an unresolved prompt, while preserving the unchanged live knowledge
route. The native table rendered the new outcome, action, live/proposed cost, and latency columns.

The installed smoke corrected one documentation-only arithmetic assumption: the fixed hint is 120
characters and therefore 30, not 29, Mnemo-estimated tokens. The runtime calculation and enforced
40-token ceiling were already correct; documentation now derives the value from the actual rendered
string rather than an informal estimate.

### Sparse 200-token checkpoint saves and failure diagnostics — Complete

The user explicitly approved hardening the existing checkpoint save path after captured failures
showed two avoidable retry causes: a caller-supplied token estimate could undercount dense content,
and strict canonical UUID parsing rejected otherwise valid uppercase input. New checkpoint revisions
must target about 200 Mnemo-estimated tokens through deterministic application-owned compaction,
rather than relying on an agent to count or retry. The existing 600-token packet ceiling remains a
defense-in-depth maximum.

Canonical checkpoint JSON must omit empty optional collections and absent evidence coordinates;
older dense rows with empty arrays or explicit null coordinates remain readable. The save boundary
must ignore the deprecated caller token estimate, compute the canonical value locally, normalize
valid UUID spelling, and preserve the newest correction lesson without copying every older lesson
into the active revision. Compaction must retain a useful objective, state, next action, decisions,
verification, and newest lesson when present, report that it occurred, and never silently claim the
original text was stored in full.

Mnemo may expose a Git object abbreviation of at least six hexadecimal characters for human display,
but integrity and evidence identity continue to use the full digest internally; a fixed six-character
digest is not an integrity key. Content-free, scope-bound checkpoint diagnostics must record every
application-reached save failure when diagnostics are enabled and all attempts in trace mode, without
recording checkpoint text, paths, hashes, checkpoint/revision or arbitrary invalid identifiers, or
tool payloads. Canonical exact-task scope remains solely for filtering. Schema/transport failures
that occur before the application boundary are explicitly outside that observer.

Acceptance requires focused compatibility, sparse serialization, deterministic compaction,
under-count, record-lesson, uppercase-UUID, diagnostic privacy/retention/failure-isolation, and MCP
contract tests; updated ADR, threat model, user documentation, and help; the complete
`npm run check` gate; and an installed-wheel smoke. No dependency, database migration, routing,
automatic learning, model endpoint, release, deployment, or installation is authorized in this
issue.

Implemented application-owned canonical sizing and deterministic compaction. Every write now
ignores the deprecated caller estimate, recomputes sparse serialized size, and stores at most 200
estimated tokens for the tested inputs while preserving the objective/state, one current next
action, blocker, decision, verification, and newest lesson when present. Middle truncation keeps
both the beginning and ending of retained facts, and successful responses expose stored size plus a
populated compaction report only when items or text changed. Lesson-only revisions retain one newest
lesson while exact-scope immutable predecessor history supplies duplicate detection, the existing
16-lesson bound, and later historical recall.

New checkpoint JSON omits empty optional collections, URI-only evidence omits absent coordinates,
and decoders retain backward compatibility with dense empty/null rows. Valid uppercase UUID input
is normalized before canonical domain parsing. Local callers can use bounded project-relative
`evidence_files`; Mnemo rejects traversal, symlinks, duplicates, and oversized files, then derives
full SHA-256 evidence and lesson IDs internally. Git uses a unique display prefix of at least six
characters while full hashes remain integrity data. No dependency or migration was added.

The automatic checkpoint instruction no longer injects five scope UUIDs or asks the agent to guess
a token estimate. It asks for only populated handoff fields and `evidence_files`. The existing
diagnostic mode now controls a private exact-scope checkpoint save ledger: summary records failures,
trace records every application-reached attempt, and off records none. JSON and
`mnemo memory diagnostics saves --format table` omit absent metrics and expose only closed outcome,
cost, latency, and compaction metadata; storage failure is fail-open and exact-scope purge removes
route and save records together.

Focused compatibility, compaction, undercount, lesson-history, uppercase-UUID, resolver-security,
telemetry-privacy/mode, CLI JSON/table, real MCP-process, and cross-client transport tests passed.
The deterministic cross-client evaluation resumes through Codex and Claude with a 197-token
checkpoint while requiring paired task-specific fragments for the core objective, decision, next
action, verification, and prevention lesson plus exact provenance. The complete `npm run check`
gate passed 1,039 tests with 27 expected skips, the isolated PostgreSQL gate passed 26 tests with
one opt-in load test skipped, and formatting, linting, strict typing, schema, dependency,
architecture, and installed-package checks passed. The built-wheel smoke independently resolved
`evidence_files`, compacted an oversized save to no more than 200 tokens, produced a sanitized
failure, and rendered that failure in the installed diagnostics table.

### `0.1.0a20` production deployment — Complete

The user explicitly approved production deployment after completion of the sparse bounded
checkpoint-save and failure-diagnostics issue. Release preparation advances the pinned prerelease
version to `0.1.0a20` without changing the approved checkpoint, evidence, telemetry, routing,
model, prompt, storage, or retention behavior.

Acceptance requires a reviewed release delta on top of the completed implementation, a clean
complete repository gate, one release commit pushed to `main`, successful protected
`publish-pypi.yml` execution, live PyPI metadata/hash/integrity-provenance verification,
installation of the exact published wheel with the existing `router` extra on this Mac, retained
Codex connection and diagnostics mode, and installed compaction, sanitized-failure, and checkpoint
diagnostics-table smokes. No TestPyPI publication, direct local upload, later routing work, or new
feature issue is authorized.

Production deployment is complete. Release commit
`f5b019a326938cfad170730a446d8e6a148faacd` was pushed to `main`, and protected workflow run
`31462258124` passed clean-runner repository verification, deterministic evaluations,
source-independent artifact build and wheel/source-distribution smokes, checksum-bound OIDC
publication, and independent live PyPI metadata, hash, provenance, and wheel verification.

The immutable workflow bundle and separately downloaded PyPI artifacts agree on these SHA-256
digests:

- wheel: `4e2b4b4b7039d07c9a1d1ad048bb6d96ffcc80fa46ad1340635e84940c06937a`
- source distribution: `b9c3b6a8b4ffd6262811f7c8cb95e790a200d35f4d92a7f0708d81c1c9c76793`

The exact verified wheel was installed locally with the existing `router` extra. The installed
command reports `mnemo 0.1.0a20`; the Codex connection remains active; diagnostics remains in trace
mode with seven-day retention; and pinned Potion revision
`bf8b056651a2c21b8d2565580b8569da283cab23` remains installed and enabled only for explicit
evaluation, with automatic-hook use disabled. An isolated installed-runtime smoke compacted an
oversized checkpoint to 154 estimated tokens, returned a content-free error for an invalid save,
and rendered both the compacted success and `MNEMO_INVALID_INPUT` failure in the native checkpoint
diagnostics table.

### Semantic checkpoint ledger and adaptive rendering — Complete

The user explicitly approved one bounded vertical slice of Mnemo's long-term checkpoint system:
reuse exact-scope immutable task-activity events and their immutable evidence references as the
evidence archive; compile those events through a pluggable, deterministic baseline compiler into a
typed semantic ledger; persist validated idempotent patches and periodic materialized checkpoints
in personal SQLite; and render whole semantic units in compact, portable, and audit modes with
measured 200-token preferred and 600-token ceiling policies.

This issue must preserve attribution, uncertainty, negation, modality, conditions, identifiers,
commands, and evidence associations; never recursively summarize prose or insert ellipses inside a
unit; retain all mandatory active units even when they exceed the ceiling; and emit structured
whole-unit omission notices with retrieval handles. Phrase reduction is limited to a versioned,
idempotent safe table outside detected protected spans. Existing checkpoint revisions, reviewed
episodic memory, public MCP tools, model endpoints, and automatic attachment behavior remain
unchanged.

Acceptance requires pure-domain patch and supersession tests, immutable-evidence and exact-scope
SQLite tests, delta/snapshot equivalence, protected-span and phrase-table regression tests,
deterministic compact/portable/audit rendering, adaptive-budget and mandatory-overrun tests, an
always-local evaluation comparing history and renderings, documentation of evidence/ledger/view
boundaries and limitations, focused tests, and the complete `npm run check` gate. The additive
personal SQLite migration must have a documented forward-only recovery path. PostgreSQL/team
persistence, MCP surface expansion, live automatic-context replacement, external-model calls,
release, deployment, and installation are outside this issue.

Implemented a storage-neutral semantic checkpoint domain with all eleven required atom kinds,
attribution and epistemic qualifiers, confidence, priority, validity, evidence lineage, lifecycle,
and explicit supersession. A closed patch model supports add, metadata update, supersede, resolve,
expire, checkpoint activation, and checkpoint removal. Pure patch application validates scope,
identity, evidence, immutable meaning, and targets before producing state; exact replay is
idempotent. The deterministic baseline compiler consumes only new immutable task-activity events,
recognizes explicit type labels, preserves unlabeled ambiguity as attributed inference, and emits
patches rather than rewriting a prior rendering.

Personal schema 31 additively persists atoms, event associations, payload-free compiled-event
markers, patch operations, checkpoint metadata, and checkpoint atom materializations. Routine saves
are deltas; generation one and every configured eighth generation are snapshots. Historical
materializations retain their then-active atom representation after later supersession. Authorized
source deletion cascades associations, orphan atoms, and checkpoint references without retaining
copied text; the payload-free compilation marker prevents replay after a deleted head event, and a
new save can continue. Migration failure rolls back both schema objects and its ledger entry; the
documented reversal is restoration of a verified schema-30 backup.

Compact, portable, and audit renderings select indivisible atoms under a 200-token preferred target
and 600-token ceiling. Mandatory goals, constraints, decisions, questions, actions, commitments,
authority, and critical uncertainty survive regardless of age or ceiling. Omitted optional units
produce typed counts, kinds, reasons, and retrieval handles. A two-rule `safe-phrases-v1` table is
idempotent and runs only outside detected logical, quoted, code, command, path, identifier, hash,
date, version, IP, number, unit, and authority spans. Injectable provider tokenizer adapters are
used when supplied; the named lexical fallback counts token pieces, not visible characters.

The focused semantic suite passed 28 tests. A separate 12-case held-out corpus measured 100%
required-group continuation fidelity, 100% protected-span fidelity, 100% provenance coverage, zero
meaning inversions, zero critical omissions, zero false-memory probes, deterministic repeated
rendering, and no drift. Eleven compact cases fit 0-200 fallback tokens and one used the 201-600
band; none exceeded 600. Synthetic tests separately prove mandatory state survives a greater-than-
600 overrun while optional state is partitioned.

Against canonical immutable event JSON, aggregate history/compact/portable counts were
20,779/1,481/3,560 with the conservative lexical fallback, 9,766/887/2,108 with the deterministic
OpenAI-shaped byte proxy, and 11,160/1,011/2,408 with the deterministic Anthropic-shaped byte proxy.
The resulting history-to-compact ratios were 14.03x, 11.01x, and 11.04x. The provider-shaped values
are comparison fixtures, not exact provider tokenizer claims.

The complete `npm run check` gate passed with 1,067 tests and 27 expected skips. The isolated
PostgreSQL/RLS gate passed 26 tests with one opt-in load test skipped; formatting, linting, strict
typing, schema, dependency/provenance, architecture, and installed-package checks passed. No
dependency, PostgreSQL schema, model endpoint, MCP tool, automatic attachment, release, deployment,
or installation changed.

This compact format is not production-validated: the held-out corpus has 12 rather than at least 50
cases, exact provider tokenizers were unavailable, and no fresh-session Codex/OpenAI or
Claude/Anthropic continuation runs were performed. PostgreSQL/team persistence, a separately
versioned append-only atom-metadata history, semantic export/deletion APIs, general contradiction
resolution, runtime composition into the public MCP save path, and live automatic-context adoption
remain explicit follow-on work requiring a new approved issue.

### Lifecycle viability evaluation — Complete

The user explicitly approved a bounded, dependency-free evaluation issue to determine whether the
current semantic checkpoint slice improves long-horizon continuation while reducing total
lifecycle token consumption. The evaluated worktree is based on commit
`bef8bb312aab386bb17ab0698af399e0b69584b1` plus the uncommitted semantic-checkpoint issue above.

Acceptance requires versioned synthetic scenario and ground-truth contracts spanning short,
medium, and long horizons; paired full-history, sliding-window, rolling-summary, Mnemo compact,
Mnemo adaptive, and Mnemo adaptive-plus-retrieval adapters; an explicitly unavailable
provider-native condition unless a real provider capability is configured; lifecycle token,
latency, retry, break-even, statistical, memory-integrity, task-impact-proxy, economic-sensitivity,
Pareto, and viability calculations; exact deterministic graders plus a blind semantic-grader
boundary; external-call budget denial by default; append-only raw results, aggregate data,
reproducibility metadata, SVG charts, and a decision report; real-trace import and pilot/market
evidence schemas; focused tests and the complete `npm run check` gate.

No live provider call is authorized merely because a CLI or credential exists. Live evaluation
requires an explicit nonzero repository evaluation budget and a configured adapter. Offline
information-availability scores must not be described as generated-agent task success, provider
token usage, market validation, or production portability. This issue does not alter model
endpoints, MCP behavior, persistence schemas, release state, installation, or deployment.

Implemented a dependency-free, versioned evaluation package with typed scenario and ground-truth
contracts, seven explicit condition adapters, fail-closed external-call budgets, lifecycle token and
cost accounting, deterministic integrity and blind semantic-grader boundaries, paired
counterbalancing, bootstrap statistics, non-inferiority and Pareto analysis, market and trace-import
schemas, ten SVG charts, and a 25-section decision report. The corpus expands six original realistic
workflow templates to 18 short, medium, and long scenarios, including all required controlled
challenges. Saved runs are append-only and include raw JSONL, flat per-run metrics, aggregates,
configuration, environment metadata, artifact hashes, and separate blind-review packets and
condition keys.

Final offline run `offline-20260812-bef8bb3-004` executed 378 observations: 324 available paired
observations, 54 per available condition, across reuse counts one, three, and ten. B3 remained
explicitly unavailable because no real provider-native adapter was configured. The checked-in
budget authorized zero external calls and zero spend, so no model completion, provider usage,
provider billing, or model grader result was fabricated; external cost was $0.00.

M3 achieved 0.993 mean gated LME, 100% required-knowledge, hard-constraint, protected-span,
temporal/supersession, and evidence-association fidelity, zero critical violations and false-memory
probes, and exact eight-cycle drift resistance in the synthetic deterministic evaluation. At the
same 200-token memory policy, the M1 information-availability proxy exceeded B2 rolling summary by
0.701. These are context-availability measurements, not live agent task success or continuation
quality.

The primary token hypothesis failed under the measured offline lifecycle accounting. Median M3
lifecycle tokens were 42,681 versus 13,615.5 for B0, a median TES of -209.6% after save,
validation, recall, retrieval, and repair estimates. Median calculated break-even was 10.272 reuses
and P90 was 31.541. Only long-horizon reuse ten had positive median TES, at 6.6%, still below the
30% target; medium reuse ten was -2.4%, and all shorter or lower-reuse strata were worse. Economic
sensitivity had negative per-run token savings in every case; its optimistic positive annual value
was driven solely by unvalidated human-time and failure-avoidance assumptions. Market pull and
portability remain unmeasured.

The final verdict is **INSUFFICIENT EVIDENCE**, with four of eight provisional thresholds passed.
The harness supports continued validation, but the executed evidence does not justify production
integration or continued product investment by itself. The highest-value next experiment is a
preregistered, explicitly funded fresh-session study with at least 30 paired B0/B2/M3 runs on one
OpenAI and one Anthropic model, exact provider usage, hidden held-out traces, and blinded human
review.

The focused viability suite passed 14 tests. The complete `npm run check` gate
passed 1,080 tests with 27 expected skips; isolated PostgreSQL/RLS passed 26 tests with one opt-in
load test skipped; formatting, linting, strict typing, schema, dependency/provenance, architecture,
and installed-package checks passed. The final saved log reaggregates exactly, all 19 recorded
artifact hashes match, all ten charts are valid XML, and the result scan found no credentials or
private keys. No dependency, database migration, model endpoint, MCP behavior, release,
deployment, or installation changed in this issue.

### `0.1.0a21` production deployment — In progress

The user explicitly approved production deployment and local installation of the completed semantic
checkpoint ledger, adaptive renderer, and lifecycle viability evaluation. Release preparation
advances the coordinated prerelease metadata from `0.1.0a20` to the next unused immutable version,
`0.1.0a21`. PyPI returned 404 for that exact version before preparation.

Acceptance requires a reviewed release delta, the complete local repository gate, source-independent
wheel and source-distribution verification, one release commit pushed to `main`, successful protected
`publish-pypi.yml` execution, independent live PyPI metadata/hash/provenance verification,
installation of the exact published wheel with the existing `router` extra, and installed-runtime
version, connection, diagnostics, migration, and semantic-checkpoint smokes. Deployment packages the
implemented code but does not promote semantic M3 into automatic context: the public MCP and
automatic-attachment path remains intentionally unchanged. No TestPyPI publication or direct local
upload is authorized.

Release-candidate verification passed before commit: the focused semantic, viability, workflow,
and artifact-verifier suite passed 52 tests; both workflow YAML files parsed; and the complete
`npm run check` gate passed 1,081 tests with 27 expected skips, plus the isolated PostgreSQL/RLS
gate with 26 tests passed and one opt-in load test skipped. Formatting, linting, strict typing,
schema, dependency/provenance, architecture, and installed-package checks passed. The
source-independent artifacts passed the release verifier with pre-publication SHA-256 digests:

- wheel: `64a4238c3a71c64ce2e22d4a4bde230626ab710d9768fea641ad924399c0a586`
- source distribution: `ec56f6692e29452531f1c54af2a7b19b08a908a9b83898f9ed01d70c886ffd1d`
