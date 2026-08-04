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

### Source-structure memory — In progress

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

### Personal knowledge and Obsidian — In progress

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

### Procedural memory — In progress

The first procedural-memory slice reuses the immutable checked-in Markdown revision store rather
than creating a second procedure source of truth. A document is eligible only when strict scalar
frontmatter marks `mnemo_kind: procedure` and gives bounded literal `mnemo_tags`; callers must
request matching tags explicitly. Scoped matching returns a deterministic bounded set in the
existing `skills_and_procedures` packet section with immutable revision/digest provenance and
structured token-budget omissions. Optional `mnemo_mandatory: true` prioritizes a checked-in
project playbook over matching optional playbooks; it cannot override scope policy, user/system
instructions, or verified current dbt/source facts. Markdown remains untrusted evidence and is
never executed. Discovery of third-party skills, agent definitions, and automatic applicability
classification remain future work.

An enabled project may now designate one checked-in automatic profile for `codex`, `claude-code`,
or `any`. It supplies literal procedure tags for that client at fresh-session attachment time, so
matching procedures join the bounded automatic packet without an agent needing to remember a tag.
Exact-client profiles take precedence over `any`; multiple equally matching profiles fail closed.
The profile revision/digest remains cited alongside each selected procedure. Mnemo does not infer
an arbitrary named-agent role from hook events, inspect prompts, or execute project Markdown.

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
