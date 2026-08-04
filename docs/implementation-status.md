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
are both unambiguous. Exact C++ namespace calls, C# `using Namespace.Type` calls, and PHP
`use Namespace\\Type` static calls now join when their saved target is unique; namespace-only
imports, aliases, and duplicate candidates remain unresolved. Broad multi-language semantic resolution, a complete call graph, and
automatic transcript capture remain separate follow-up work.

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
