# Initial Mnemo threat model

## Scope

This threat model covers the planned personal, local-first path through native Codex and Claude
Code MCP integration, explicit checkpoints, SQLite, and dbt structural projections. It specifies
required controls before those features exist; it does not claim they are implemented.

Team tenancy, remote MCP, hosted sync, UI, automatic task-event capture, automatic extraction-job
invocation, candidate approval/activation, and backup infrastructure are deferred and require
threat-model revisions.

## Security objectives

- Zero unauthorized cross-owner, workspace, or project disclosure.
- No retrieved content can become instructions, permissions, or canonical mutations by itself.
- Prohibited secrets are not persisted, embedded, logged, exported, or returned.
- Every included claim is scoped, evidence-bearing, current enough for its use, and explainable.
- Correction, expiry, consent withdrawal, and deletion prevent continued use and resurrection.
- Mnemo and connector failures degrade to normal coding-agent operation.
- Mnemo never proxies or changes the coding agent's model endpoint.

## Assets

- User source files, repository metadata, dbt artifacts, notes, and task events.
- Checkpoints, durable memories, evidence references, corrections, and tombstones.
- Owner/workspace/project authorization and consent state.
- Canonical storage, indexes, embeddings, caches, jobs, exports, and future backups.
- Connector credentials and client configuration.
- Context packets and provenance explanations.
- Local service control endpoints, logs, diagnostics, and dependency supply chain.

## Trust boundaries

1. User and coding-agent client to the local MCP/API process.
2. Local process to registered repositories, dbt artifacts, and other connector sources.
3. Untrusted retrieved content to policy and context assembly.
4. Deterministic policy to optional model providers.
5. Canonical storage to rebuildable projections, caches, exports, and future backups.
6. Local loopback service to the host network and other local users/processes.
7. Mnemo-owned code to third-party packages, CI Actions, installers, and release artifacts.

## Adversaries and failure assumptions

Threats may come from malicious document or repository content, a compromised connector or
dependency, another local process, a mis-scoped client, stale artifacts, mistaken user actions,
model output, or ordinary implementation bugs. The personal profile does not assume every local
process is trusted. External content, model output, and remembered agent output are untrusted.

## Threat analysis

### Cross-project disclosure

**Scenario:** A request for project A retrieves memories, paths, or structural facts from project B
because scope is absent, inferred, cached incorrectly, or filtered only after ranking.

**Required controls:** Explicit owner/workspace/project scope on every item and operation;
authorization-constrained database and index queries; no null-as-wildcard behavior; scoped cache
keys; deny-by-default multi-project requests; provenance showing included scope. Local inspection
must resolve one explicitly enabled canonical project directory to its stored internal task scope
before querying and must not accept an absent binding as a wildcard.

The context engine passes the complete task scope to the active-episodic repository before it
normalizes query terms or computes lexical, temporal, confidence, and type-priority scores. The
repository excludes inactive, retracted, expired, purged, and deleted identities inside that
scoped query. No broad candidate pool, post-ranking scope filter, or query-derived scope exists.
The transient query is bounded and is neither persisted nor included as a packet field. Candidate
reads stop at 50 items; a further page is disclosed only as a payload-free lower-rank omission.
Every included memory carries the original scope and evidence plus rank, score, retrieval method,
source trust, and matching provenance. Section and total token limits are enforced before packet
construction. Expected storage failure returns a sanitized omission and cannot widen the read.

**Verification:** Adversarial unit, repository-contract, integration, cache, export, and local CLI
tests with identical text across projects, including enabled and unregistered directories. Required
result is zero leaked IDs, metadata, counts, or payloads. Context-engine tests additionally record
the exact scope passed before scoring, compare same-text isolation, verify stable ranking and
provenance, exercise zero-token and 50-candidate bounds, and ensure storage errors do not expose
their payload.

**Residual risk:** Team authorization is not designed; team mode must not reuse personal-mode
nullability.

### Prompt injection through retrieved content

**Scenario:** A note, source comment, checkpoint, dbt description, or tool output instructs an
agent to ignore policy, reveal another scope, invoke tools, or mutate memory.

**Required controls:** Retrieved text is delimited and labeled as untrusted evidence; instructions
come only from the client/system and checked-in applicable procedures; no retrieved text changes
permissions, tools, scope, or write intent; write tools require deterministic validation and
explicit authorization.

**Verification:** Injection corpus across every content category; assertions that packets retain
data labels and that tool/mutation decisions are unchanged.

**Residual risk:** A downstream model may still follow malicious prose; minimize excerpts, surface
source trust, and test each renderer/client.

### Poisoned memories

**Scenario:** Incorrect assistant claims, malicious source content, repeated low-trust assertions,
or compromised extraction creates an apparently authoritative durable memory.

**Required controls:** Evidence and source-trust requirements; assistant output cannot become a
user fact without verified evidence; candidate/approval states; revision chains; source-authority
order; conflict reporting; extractor and prompt versioning; deterministic mutation policy. An
explicit approved fact has at most one immutable correction or retraction action; corrected and
retracted targets are excluded before context ranking, and correction retains exact evidence.
Optional task-event extraction sends only the minimized event identity, category, actor, summary,
and a four-candidate limit through a provider-neutral port. Mnemo accepts a closed proposal schema,
retries malformed structured output once, pins configured provider/model metadata, and constructs
scope, evidence, retention, identity, provenance versions, and inactive status itself. Candidates
require verified non-inference evidence, pass deterministic content safety before an atomic write,
and cannot enter context until a later explicit approval workflow exists. Deterministic identity
makes duplicate delivery idempotent and changed retry output a conflict rather than an overwrite.
Every extracted candidate remains inactive regardless of confidence or sensitivity until one
exact-scope user review with verified user-correction evidence approves it. Approval atomically
adds an active marker and preserves both source and review provenance; rejection creates no active
payload. Duplicate review is idempotent, while competing decisions, action-key reuse, unsafe
content, and cross-scope targets fail closed. Storage re-runs candidate and review safety so a
caller cannot bypass the review boundary by writing directly to an adapter.
Approved episodic-memory correction and retraction use one append-only optimistic chain whose first
revision is the approval action. Each user action names the exact current revision, carries verified
user-correction evidence, and passes deterministic safety checks. Corrections preserve immutable
scope, source, retention, and extraction provenance; stale revisions and action-key reuse cannot
fork the chain. Retraction is terminal, creates a payload-free final revision, and immediately
removes the memory from active reads, including idempotent review retries. Reference and SQLite
replay are required to produce identical history and active state.

**Verification:** Conflicting-source, repeated-claim, low-confidence, source-deletion, correction,
and model-output tests. Frequency must not increase authority.

**Residual risk:** A user can approve bad evidence; the UI and CLI must make source and consequence
clear and keep correction simple.

### Secret ingestion

**Scenario:** Credentials appear in source files, environment output, transcripts, tool results,
diagnostics, or model responses and enter storage, embeddings, logs, or exports.

**Required controls:** Bounded registered sources; denylisted files and patterns; deterministic
secret detection before persistence or embedding; redaction in telemetry; no environment-wide
capture; model classification only as supplemental defense; visible rejection events without the
secret value. Supplemental dbt parsing uses a fixed data-minimizing projection: catalog comments,
owners, and statistics plus run-result environment values, adapter responses, messages, compiled
code, relation SQL, arbitrary arguments, and thread identifiers are never retained in its domain
artifacts.

The personal-profile content-safety boundary always runs Mnemo's deterministic classifier first.
At most eight explicitly supplied classifiers may strengthen sensitivity or reject content; they
cannot override a deterministic rejection, and invalid results or classifier failures reject with
stable content-free codes. Knowledge passages are checked again immediately before the local
embedding provider is invoked, so rejection produces neither a provider call nor a vector row.
Explicit task-activity capture persists only a bounded summary plus closed category/actor,
sensitivity, retention, scope, time, and evidence fields. Its schema has no raw transcript,
prompt, command, tool argument/body/result, source-content, or opaque-model-trace field; the same
content-safety decision and declared-sensitivity floor run before the event and outbox transaction.

**Verification:** Synthetic secret corpus across ingestion, job retry, logging, retrieval, export,
and deletion paths. Confirm no raw value or reversible encoding appears.

**Residual risk:** Pattern detection cannot identify every secret; minimize capture and allow
pluggable classifiers and source exclusions.

### Stale structural information

**Scenario:** A checkpoint or dbt projection describes an old commit, target, environment, or
manifest and is presented as current, causing an incorrect edit or impact assessment.

**Required controls:** Content digest, Git commit and working-tree fingerprint, dbt target,
environment, invocation, and observed time; compare current fingerprints before retrieval; label
stale facts; current artifacts outrank memories; never use an LLM for authoritative lineage.

**Verification:** Changed-manifest, changed-branch, dirty-tree, target-switch, deleted-node, and
clock-skew fixtures. Stale facts must be excluded or explicitly labeled.

**Residual risk:** Files can change after validation; context packets need an observed-at value and
consumers should revalidate before consequential writes.

### Expired episodic payload use

**Scenario:** An extracted candidate or approved memory remains readable after its retention
schedule is due, is resurrected by extraction retry, or extends its lifetime through approval,
correction, retraction, confidence, or access.

**Required controls:** Authorize one exact task scope before discovering due candidates; use only
the canonical schedule copied from the source event; append a deterministic payload-free
expiration record atomically; make identical delivery idempotent and conflicting policy, source,
scope, schedule, or time fail closed. Every candidate, review, active-memory, governance, and
revision payload query must exclude an expired identity before reconstruction or ranking. Storage
retry cannot restore an expired candidate. A later exact-scope purge marker is deterministic and
payload-free; its transaction deletes the candidate claim, dependent review/governance payloads,
links, and newly orphaned evidence while preserving the expiration tombstone and permitted source
event. The minimized source event has its own payload-free expiration and purge markers. Source
purge must fail while any candidate payload still depends on it, cancel its task-activity outbox
job, remove only newly orphaned evidence, and leave candidate tombstones valid after source removal.
Permanent and not-yet-due schedules remain unchanged. The reference and SQLite adapters must
discover identical expiration and purge sets and retain both states across restart.

**Verification:** Before/due boundary tests, exact-scope isolation, approval/correction/retraction
fixtures, replay and conflicting-delivery tests, transaction failure injection, restart tests,
payload-free schema inspection, and forward-only migration rollback with candidate preservation.

**Residual risk:** Retention now covers the deliberately minimized task events, not arbitrary raw
conversations or tool bodies (which Mnemo does not capture here). Explicit user/source deletion is
implemented for this production episodic slice; export cleanup and backup cleanup remain required
before the full retention and deletion promise is complete.

### Unauthorized memory mutation

**Scenario:** A read request, retrieved instruction, model proposal, compromised client, or replayed
request creates, pins, corrects, or deletes memory without authority or consent.

**Required controls:** Separate read/write tools and permissions; explicit scoped actor; request IDs
and idempotency; deterministic schema, policy, consent, and evidence validation; confirmation for
destructive or authority-changing writes; audit metadata without sensitive payloads. Personal CLI
governance resolves only an enabled canonical project binding, requires confirmation, and uses a
deterministic action key so retry cannot create a second replacement or tombstone.

**Verification:** Read-only annotation tests, confused-deputy cases, replay tests, malformed scope,
stale consent, injection-triggered writes, and mutation authorization matrices.

**Residual risk:** A compromised authorized client can act as the user; minimize capabilities and
make writes inspectable and reversible where possible.

### Cross-scope or tampered episodic export

**Scenario:** An export retrieves payloads before authorization, mixes another task's data, includes
expired or deleted content, omits anti-resurrection tombstones, or is modified without detection
before a future import.

**Required controls:** Require one exact task scope before any storage query; derive dependent
review/governance payloads only from already-authorized candidate identities; exclude every memory
or source identity carrying an expiration or deletion tombstone; include all matching payload-free
expiration, purge, and deletion records; validate candidate/source, review, governance, revision,
purge, and dependent-deletion relationships; reject duplicates and cross-scope objects. Use a
versioned schema, stable identity ordering, canonical UTF-8 JSON, and a SHA-256 digest over the full
content. Treat any future import as untrusted input and reapply policy rather than granting
authority from the export itself.

**Verification:** Reference/SQLite byte parity at one timestamp, unrelated-scope fixtures,
review/correction replay, expired/purged/deleted fixtures, JSON round trip, restart, duplicate and
relationship corruption, non-canonical ordering, and content/digest tampering.

**Residual risk:** This slice returns an in-memory bundle and cannot revoke or repair a copy after
the user delivers it elsewhere. Import, stored-export cleanup, encryption, and backup handling are
separate work.

### Deletion propagation

**Scenario:** Deleted or consent-withdrawn content remains in indexes, embeddings, caches, jobs,
summaries, exports, or backups, or is resurrected during re-ingestion.

**Required controls:** Immediate retrieval exclusion; tombstones; idempotent durable deletion jobs;
projection/cache invalidation; job cancellation; source re-ingestion checks; export disclosure;
backup policy defined before backups ship.

For the current approved-fact-only retraction slice, the canonical event payload and its direct
evidence links are removed atomically after the scoped tombstone is inserted. Approved facts have
no FTS, vector, cache, export, or backup projection in this profile. This narrow control does not
claim general checkpoint, knowledge, export, or backup deletion.

For extracted production episodic state, one user-authored exact-task-scope action writes a
deterministic payload-free memory or source tombstone before removing content. Individual memory
deletion removes candidate, review, active, governance, link, and newly orphaned evidence payloads
without changing its source. Source deletion creates deterministic dependent memory tombstones,
removes every dependent payload, then removes the minimized event, its evidence links, newly
orphaned evidence, and task-activity outbox job in the same transaction. Existing retention
tombstones survive. Reads and re-ingestion reject deleted identities; exact replay is idempotent;
competing actions, reused action keys, cross-scope targets, and source mismatches fail closed.
Reference/SQLite parity, restart, schema inspection, and injected transaction failure are tested.
No checkpoint, knowledge, embedding, export, backup, or external-copy deletion is implied.

**Verification:** Failure-injected deletion tests across every materialized copy, retries, export,
reindex, restore, and source rename/recreation. Counts and digests must reconcile.

**Residual risk:** User-controlled exports cannot be recalled; warn and document their boundary.

### Local service exposure

**Scenario:** The local API or MCP service binds beyond loopback, trusts filesystem permissions
alone, accepts requests from another local user/process, or leaks data through diagnostics.

**Required controls:** stdio where possible; otherwise loopback-only binding, unpredictable local
credentials, origin protections where relevant, restrictive file permissions, bounded payloads,
timeouts, rate limits, redacted logs, and no automatic firewall exposure. Non-loopback requires a
security ADR, authentication, authorization, and encryption.

**Verification:** Bind-address, unauthenticated-client, other-user, oversized-payload, slow-client,
port-conflict, and diagnostic-redaction tests.

**Residual risk:** Malware running as the same OS user may access local data; document this personal
profile boundary and avoid ambient credentials.

### Compromised connectors

**Scenario:** A connector returns forged scope/source metadata, reads beyond registration, supplies
malicious content, exfiltrates data, hangs the worker, or modifies client model configuration.

**Required controls:** Least-privilege, read-only defaults; Mnemo assigns canonical scope and source
identity rather than trusting connector claims; path allowlists and traversal protection;
timeouts, size limits, isolation, checksums, and failure visibility; native MCP configuration only;
never proxy or rewrite model endpoints.

**Verification:** Forged metadata, traversal, symlink escape, malformed artifacts, oversized input,
timeout, partial failure, and model-endpoint invariance tests for every connector.

The supplemental dbt adapters accept only explicit caller scope and source identity, current
reviewed schema versions, finite timings, unique resource identities, and configured byte/resource/
string limits. Their tests include unsupported versions, mismatched and duplicate identities,
malformed timing/status data, non-standard numeric constants, absolute source identities, and
hostile size limits. Lifecycle attachment additionally requires an exact same-scope manifest
snapshot and accepts supplemental failure without replacing that manifest. Context selection maps
only exact node identities, caps rendered columns, preserves artifact evidence, and applies the
existing structural token budget before returning any supplemental fact.

Manifest exposures, metrics, and the semantic-model nodes joining metrics to models are admitted
to lineage only through the reviewed v12 collections, exact matching map keys and contained
identities, typed resource values, bounded counts, and same-artifact `depends_on.nodes` references.
Unknown endpoints, collection/type disagreement, map disagreement, and cycles fail closed;
descriptive content, dimensions, and measures never create an edge.

Macro impact uses only exact same-artifact identities from `depends_on.macros`, a distinct typed
edge, and the shared graph count/cycle limits. Macro SQL is never normalized, persisted, or
returned. A macro identity in `depends_on.nodes`, a non-macro identity in `depends_on.macros`, an
unknown endpoint, or a macro cycle rejects the artifact.

Directed dbt paths resolve both endpoints inside one already-authorized snapshot before traversal.
The search is breadth-first, deterministically ordered, and bounded by explicit node, edge, and
depth limits. A missing, cross-scope, or unreachable endpoint cannot trigger a wider snapshot read
or disclose whether a matching identity exists elsewhere.

Direct dbt test-coverage queries resolve the subject inside one authorized snapshot, filter only
persisted enabled test nodes that directly depend on it, and cap returned tests before context
rendering. Latest status is joined only from the run-results projection attached to that exact
snapshot. Missing execution evidence remains absent and cannot be promoted to a passing result.

Structured dbt selectors accept only bounded exact resource-type, package, and tag strings and
require at least one filter. They scan only the already-authorized immutable snapshot, exclude
disabled nodes, cap matches before context rendering, and never interpret selector expressions,
Jinja, SQL, or shell syntax.

dbt changed-state comparison reads exactly two already-authorized immutable snapshots. An
append-only scoped activation ledger supplies latest-transition order; UUID and timestamp ordering
are never treated as activation evidence. Node classification uses only minimized manifest fields,
and affected-node traversal uses only stored typed edges with strict change, node, and packet
limits. Cross-scope snapshot IDs, missing history, stale required state, and storage failure cannot
widen the read or trigger source, SQL, Jinja, warehouse, or dbt execution.

Optional dbt code excerpts are requested only after exact scope and manifest-node resolution. The
local reader accepts only canonical registered-project `.sql`, `.yml`, and `.yaml` paths, follows
no escaping symlink, requires a bounded regular UTF-8 file, and caps both selected lines and bytes.
Deterministic prohibited-secret checks run before return. The excerpt is never persisted, parsed,
executed, or used as lineage authority; it is separately cited as untrusted current repository
evidence. Any binding, path, decoding, size, secret, read, or budget failure yields only a bounded
omission and cannot broaden retrieval.

`sources.json` freshness ingestion accepts only bounded official v3 fields and attaches results to
source identities in one exact authorized manifest snapshot. Database error text, adapter
responses, filters, timing details, environment values, and arbitrary payloads are validated only
as needed and discarded before persistence. Retrieval returns the immutable observed status and
timestamp; it never contacts the warehouse, recomputes freshness, or upgrades a missing result to
pass.

dbt Git-state observation executes only fixed read-only Git argument vectors without a shell and
uses a short timeout. Changed, deleted, and untracked paths are bounded, canonicalized under the
project root, and rejected on traversal, escaping symlinks, unsupported file types, or file/total
byte limits. Mnemo hashes status/path bytes and current content locally but persists only the final
SHA-256 fingerprint plus full HEAD, dirty state, and explicit target; paths, bodies, diffs, commit
messages, remotes, credentials, stderr, and environment values are never stored or logged.
Retrieval-time comparison occurs only after strict scope parsing and only for an unambiguous local
dbt binding with the same owner, workspace, project, and visibility identity. MCP callers cannot
submit a commit or fingerprint as current-state evidence. Missing, corrupt, ambiguous, mismatched,
or unregistered bindings and observation failures return unknown currentness without exposing the
configured path or Git diagnostics.

**Residual risk:** A connector with legitimate filesystem access can observe allowed content;
minimize its permission and dependency surface.

## Security gates and ownership

Changes affecting a threat above must update its required controls and verification. Security tests
are required before implementation can claim the control. Critical/high findings block release.
The package owner implements controls; `packages/policy` owns deterministic authorization,
consent, retention, and mutation decisions; composition roots own exposure and connector
capabilities. No control may depend solely on a model.
