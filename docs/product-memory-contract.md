# Mnemo product memory contract

## Purpose and invariants

This contract defines what Mnemo may remember, how evidence and authority work, and how users
control stored context. It is implementation-independent and applies before domain or persistence
code exists.

Core invariants:

- Authorization and scope filtering happen before candidate retrieval or ranking.
- Every durable claim is evidence-bearing, revisioned, correctable, exportable, and deletable.
- Retrieved content is untrusted data, never instructions or authorization evidence.
- Model output can propose but cannot authorize or directly apply a canonical mutation.
- Current structured sources outrank memories about structural state.
- Conflicts are represented; ranking must not silently erase disagreement.
- Mnemo connects through native client integration and never proxies or changes an agent's model
  endpoint.

## Memory categories

| Category | Meaning | Durability and authority |
|---|---|---|
| Working memory | Bounded context for the active task | Ephemeral by default; durable only through an explicit checkpoint or approved record |
| Episodic memory | Prior actions, decisions, failures, outcomes, and checkpoints | Durable only with permitted evidence and lifecycle status; never authoritative for current repository structure |
| Personal/project knowledge | User-authored notes, decisions, meetings, and documents | Mirrors cited sources; content is evidence, not executable instruction |
| Project intelligence | dbt lineage first, then repository symbols and relationships | Rebuildable structural projection tied to source and Git fingerprints |
| Procedural memory | Versioned skills, agents, policies, and workflows | Checked-in mandatory rules outrank remembered preferences |
| Context packet | Budgeted selection across the other categories | Derived response, not a new durable memory; every included item retains provenance |

Team knowledge uses the same source/revision contract rather than a second note format. Canonical
text remains in immutable, exact-project PostgreSQL revisions under forced row-level security;
literal selection considers only authorized current revisions. Semantic vectors are rebuildable
pgvector projections keyed to exact revision/section digests and contain no second text copy.
Tombstoning removes revision text, sections, links, and vectors atomically while retaining minimal
anti-resurrection metadata. This storage boundary does not decide shared-source ownership,
approval, or correction authority; those require the later authenticated team governance service.

Team checkpoints use the same canonical aggregate, revision, content, evidence, and lifecycle-event
contracts as personal checkpoints. PostgreSQL stores one exact workspace/project/session/task scope
on every aggregate, immutable revision, and append-only lifecycle event behind forced row-level
security. Creation and revision transitions append the matching deterministic event atomically;
current-pointer changes compare the expected revision, and completion, abandonment, or expiry is
terminal.
An identical terminal retry returns the committed revision, while stale or competing writes fail
without a partial revision or event. PostgreSQL may also attach one immutable exact-scope source
observation to a revision only when the matching authorized project snapshot already exists. That
identity-and-time record means co-observation, not causation, and stores no checkpoint or source
payload. Expiry appends an evidence-preserving terminal revision and removes that handoff from
current selection. Scope-first due discovery selects at most 100 active checkpoints by oldest
canonical `updated_at`; the application rereads their current identities and uses the normal
compare-and-swap expiry transition so a concurrent save wins. Automatic-memory session start uses
the configured 1–3650 day personal episodic-retention period for one exact task and fails open.
Retrieval never updates the schedule. This storage boundary does not authenticate a remote
principal or provide physical deletion propagation or a remote team service. Complete
aggregate, revision, and lifecycle-event history is portable through a strict exact-task bundle;
source observations are excluded because their source snapshots are rebuildable projections.
An explicit user deletion is distinct from expiry: it writes one deterministic payload-free
exact-task tombstone before atomically erasing the aggregate, revisions and evidence payload,
lifecycle events, source observations, and checkpoint-lifecycle jobs under Mnemo's control. Exact
retry is idempotent, competing or cross-scope actions fail closed, and the tombstone prevents
canonical resurrection. New live-history exports omit the deleted checkpoint. Portable tombstone
transfer, prior user-controlled exports, backups, scheduled retention, and remote deletion remain
separate lifecycle boundaries.

Team dbt manifests use the same minimized authoritative project-index contract as personal mode.
PostgreSQL stores immutable exact-project snapshots, typed nodes and lineage edges, explicit
activation history, and last-sync state behind forced row-level security. A project-keyed
transaction lock and expected-active comparison serialize activation; exact content replay reuses
the retained snapshot, while composite foreign keys bind every edge endpoint to the same scoped
graph. Runtime access cannot mutate metadata or delete projections. The projection contains no raw
manifest, SQL, compiled content, macro body, warehouse response, credential, or environment value.
Minimized catalog, run-results, and source-freshness versions attach to that exact graph through
same-snapshot resource links. Their independently active immutable versions retain only reviewed
typed fields and evidence; raw comments, statistics, messages, adapter/database errors, filters,
arbitrary arguments, and environment values are discarded before persistence.

Team task activity retains only the existing explicitly minimized event contract, never raw
interaction bodies. PostgreSQL applies deterministic secret/sensitivity policy before persistence
and atomically inserts one deterministic delivery job with an accepted event. Queue claims require
exact task scope, increment attempts under a worker lease, and use row locks that prevent concurrent
claim duplication. Completion and retry require the live lease owner. Explicit project-level
failed-job requeue is capped at 100, preserves attempt counts, and cannot record successful
handling. Both canonical event rows and delivery metadata remain behind forced RLS; content-free
project status returns counts only. Pre-v4 checkpoint history is not silently replayed or backfilled.

Team approved episodic facts use the same explicit task-scoped event, correction, retraction, and
pin contracts as the personal profile. Deterministic safety rejection happens before PostgreSQL
persistence. Facts and immutable evidence-bearing actions remain behind forced RLS, and every
accepted mutation commits with one deterministic delivery job. A correction preserves fact kind,
supersedes the target, and transfers an active pin through immutable actions. A retraction records
bounded governance provenance, releases the pin, and erases the target summary, source key, and
fact evidence in the same transaction. Exact retries are idempotent; competing target, identity,
source-key, or action-key reuse fails. This episodic storage boundary remains separate from team
knowledge governance.

The authenticated team knowledge composition records the verified creating principal as immutable
source owner and the verified contributor on every immutable revision. New sources are excluded
from content retrieval until a project maintainer, workspace administrator, or workspace owner
approves the stable source against its exact current revision. Approval is immutable and idempotent
for one caller key, also requires the `mnemo:knowledge:approve` OAuth scope, remains valid across
later predecessor-checked revisions, and cannot bypass deterministic secret controls. Competing
corrections that name the same predecessor cannot both
become current; Mnemo performs no automatic merge or semantic contradiction inference. Bounded
source-status output contains no note content.
Pre-v21 rows retain the prior scope owner only as an explicitly unauthenticated legacy attribution;
Mnemo does not manufacture historical creator or author evidence.

Team extracted episodic candidates remain inactive PostgreSQL records until one explicit verified
user review approves them. Each bounded batch contains at most four contiguous proposals from one
authorized task event and extractor version. Scope, retention, and evidence are copied from and
database-bound to that canonical source; provider/model/prompt provenance and deterministic
candidate identities are retained. Candidate and review safety is rerun before persistence. A
rejection creates no active memory; an approval atomically creates one matching active marker and
merges review evidence. Exact retries are idempotent, while changed extraction output, competing
review, action-key reuse, or cross-task linkage fails. No model confidence value authorizes
activation, and this storage boundary invokes no extractor.

Team active episodic memories use the same optimistic immutable governance contract as personal
memory. The approval action roots revision one; every correction or terminal retraction must name
the current expected revision. PostgreSQL stores only immutable exact-task actions and replays the
chain rather than maintaining a duplicate mutable claim. A correction carries verified-user
evidence, bounded replacement content, and sensitivity. A retraction carries no replacement claim
or sensitivity and removes the memory from active reads. Exact retries are idempotent; stale
writers, changed key reuse, unsafe content, revision forks, and post-retraction mutation fail
closed. Retention and deletion remain separate lifecycle boundaries.

Team extracted-memory retention uses immutable forced-RLS expiration and purge tombstones. The
source-bound non-permanent schedule is the only expiration authority. Expiration immediately hides
candidate, review, active, governance, and revision payloads; purge later removes those dependent
rows while retaining the tombstones and permitted source event. Exact replay is idempotent, direct
pre-purge deletion is database-denied, and an expiration tombstone prevents candidate resurrection.
Task-event retention and explicit user deletion remain separate operations.

Team task-activity retention applies the same two-phase contract to the minimized source. Event
expiration immediately hides its summary and evidence. Source purge is rejected while any
dependent candidate payload remains; after candidate purge it atomically removes the event and its
task-activity outbox job while retaining source and candidate tombstones. Direct pre-purge deletion,
non-task outbox deletion, and source resurrection fail closed. Scheduling and explicit deletion
remain separate operations.

Team explicit episodic deletion uses the same verified-user exact-task contract as personal mode.
An individual deletion atomically writes one payload-free deterministic tombstone and removes its
candidate, review, active, and governance payloads. A source deletion writes its source tombstone,
retains any earlier individual tombstones, creates tombstones for every remaining dependent memory,
then removes all dependent payloads plus the minimized source event and task-activity outbox job in
one transaction. Existing expiration and purge tombstones survive. Exact replay is idempotent,
changed action or target reuse fails closed, and retained tombstones prevent event or candidate
resurrection. Export, backup propagation, and external-consumer cleanup remain separate operations.

Team episodic export uses the same `mnemo.episodic-export.v1` bundle as personal mode. PostgreSQL
opens one authorized exact-task repeatable read-only snapshot before reconstructing live minimized
events and candidates, reviews, governance streams, replayed revisions, and matching retention,
purge, and deletion tombstones. Identity ordering, canonical UTF-8 JSON, and SHA-256 integrity
semantics are backend-independent. Forced RLS and exact scope filtering precede payload parsing;
an unauthorized or foreign task yields the same valid empty bundle shape without disclosing counts
or identities. The operation returns data only and does not persist an export file or authorize a
later import.

Team source structure uses the same rebuildable projection contract as personal mode. PostgreSQL
stores immutable exact-project snapshot digests and counts, privacy-safe relative file paths and
content digests, symbol identities, static edges, explicit activation history, and last-sync time
behind forced RLS. It stores no source body, comment, docstring, absolute path, environment value,
embedding, or inferred relationship. Exact digest replay reuses the existing snapshot, activation
order—not UUID order—defines transitions, and bounded authorized literal symbol selection reuses the
backend-independent deterministic rank. The adapter accepts an already parsed artifact; filesystem
discovery, refresh scheduling, and checkpoint co-observation remain separate operations.

## Source-authority order

The default order, from highest to lowest authority, is:

1. current repository content, current dbt artifacts, and verified tool results for current
   structural state;
2. user corrections and explicitly pinned facts;
3. user-authored documents and user statements;
4. approved agent checkpoints and evidence-bearing extracted episodes;
5. approved external documents; and
6. assistant inference.

Authority is claim-specific. A dbt manifest can authoritatively describe lineage but cannot prove
a user's preference. Recency does not elevate a lower-trust source above a higher-trust source.
When applicable sources disagree, the response includes the conflict, source dates, and evidence
instead of silently choosing a vector or lexical winner.

## Scope model

Every stored, projected, retrieved, corrected, exported, or deleted item must carry:

- `owner_id` — required and never inferred from memory content;
- `workspace_id` — nullable only in personal mode; null means the owner's personal workspace,
  never “all workspaces”;
- `project_id` — nullable only for genuinely owner/workspace-wide material; null never means “all
  projects” during a project-scoped query;
- `source_id` — stable identifier for the evidence-producing source;
- `visibility` — explicit access classification;
- `sensitivity` — explicit handling classification;
- `retention_policy` — policy identifier, not an informal duration; and
- `created_at`, `observed_at`, `valid_from`, and `valid_to` as applicable.

Requests require an authenticated owner and an explicit workspace/project selection. Missing,
malformed, unauthorized, or ambiguous scope fails closed. Candidate generation must execute
inside the authorized scope; post-retrieval filtering is prohibited. Cross-project context is
allowed only through an explicit authorized multi-project request whose omissions and scope are
visible in the result. Personal-mode nullability must not be treated as a team-mode authorization
rule.

A team request requires a non-null workspace ID and an authenticated principal identity mapped to
Mnemo's owner-identity type at the authentication boundary. The deterministic team policy accepts
only one exact active workspace membership. Workspace owners may perform the closed team operation
set; admins may perform every operation except workspace ownership management; editors may read and
contribute; viewers may read. Workspace-visible projects use that role matrix. Private projects
also require the principal to be the project owner, a workspace owner/admin, or have one exact
active project membership: maintainer, contributor, or viewer. Maintainers may read, contribute,
manage the project, and approve sources; contributors may read and contribute; viewers may read.
An owner-visible item remains accessible only to its item owner, regardless of an administrator's
role. Missing, suspended, mismatched, cross-workspace, and cross-project claims are explicit denials
and cannot trigger a broad lookup. This application policy is the canonical authorization contract;
the later PostgreSQL adapter must enforce an equivalent restrictive row-level policy as a second
boundary.

Team authority state changes are compare-and-set operations over exact composite identities. A
workspace is created with exactly one active owner membership. Ordinary membership updates cannot
create or change the owner role; ownership transfer atomically updates the workspace, promotes one
active successor, demotes the former owner to admin, and records the change. Projects require an
existing workspace and active workspace-member owner. A new project membership must start active,
and an active project membership requires an active workspace membership. Project visibility may
change without changing identity or ownership. Suspended membership remains stored authority state
but never authorizes access.

Every committed authority mutation carries one immutable, payload-free audit event in the same
atomic operation. It records only request/event, workspace/project/principal, actor, action, and
time identities. The same request and canonical mutation may be replayed idempotently; a reused
request with different state is a conflict. Audit reads require the exact workspace and are capped
at 100 records per page. The PostgreSQL team control-plane adapter now implements these requirements
in a dedicated schema with forced row-level security. Every runtime transaction is bound to one
principal, workspace, and closed operation through transaction-local settings; absent or malformed
values deny access. Runtime uses a non-owner, non-superuser, non-`BYPASSRLS` role and cannot update
audit rows. This durable storage boundary does not authenticate a principal by itself; the
authenticated application service, remote transport controls, personal-data migration, and
operations requirements remain required before team mode exists.

The team MCP application boundary is an OAuth resource server over stateless Streamable HTTP. A
pinned asymmetric verifier requires an exact HTTPS issuer and audience, expiration and issuance
times, canonical UUID subject, bounded client identity, and configured scopes before MCP dispatch.
The verified subject—not any tool field—becomes the principal for one explicit request workspace.
Only then does the application construct the existing PostgreSQL checkpoint, episodic, knowledge,
source, dbt, procedure, and skill stack; repository authorization and forced RLS still precede data
access. The server binds to loopback and stores no bearer token or credential. Deployment
configuration, TLS proxying, key-file controls and rotation, and the remaining team governance and
operations gates are separate requirements before team availability.

The installed `mnemo-memory-team` runtime accepts only non-secret deployment metadata through its
environment. Its PostgreSQL password must come from an absolute owner-only regular file; its OAuth
verification key must come from an absolute owner-owned, non-writable regular file. Descriptor-
verified no-follow reads enforce type, ownership, permissions, byte limits, and UTF-8 before server
construction. Every PostgreSQL connection uses certificate and hostname verification with TLS 1.2
or newer and has no plaintext fallback. The MCP upstream remains fixed to `127.0.0.1`; the public
HTTPS proxy, certificate, and network policy are a separate operator-controlled boundary. Rotation
is atomic file replacement plus restart, and startup failures expose only stable content-free codes.

The installed `mnemo-memory-team-admin` entry point creates operator-invoked PostgreSQL custom
archives from one exported snapshot using a dedicated non-superuser `BYPASSRLS` backup role. The
MCP runtime credential remains non-`BYPASSRLS` and cannot create a whole-team backup. Each private,
non-overwriting archive has a canonical digest-bound manifest containing only schema version and
per-table counts. Restore drills accept only a matching private archive, reject the live database
and any target already containing `mnemo_team`, restore transactionally into an explicitly
provisioned database, and require exact migration-ledger and table-count parity before success.
The target must already contain the approved `vector` extension. Backup at-rest encryption,
custody, retention, scheduling, and external-copy deletion propagation remain explicit
operator/release gates.

Team backup manifests version 2 also bind the snapshot count of every monotonic canonical erasure
ledger, including filtered retraction records. An explicit reconciliation compares those counts
with one current whole-team inventory. Any managed backup predating an erasure is deleted
archive-first and manifest-second with durable directory synchronization; current backups remain
unchanged, retries are safe, and a regressed ledger or unverified candidate fails closed before
valid deletion. Version-1 backups are conservatively obsolete after any erasure. This guarantee
applies only to directories explicitly submitted to Mnemo; external copies remain the operator's
responsibility and must be disclosed by retention policy.

The authenticated team MCP boundary applies a strict fixed-window request limit to each exact
OAuth principal/workspace pair after identity and scope validation and before repository
composition. Invalid authentication does not consume a bucket. Denied calls expose only
`MNEMO_RATE_LIMITED` and cannot reach PostgreSQL. The monotonic, concurrency-safe in-memory state is
bounded and reclaims expired identities. This guarantee covers a single service process; a future
multi-process deployment requires an explicitly reviewed shared counter.

Every team workspace must also have an explicit schema-administrator checkpoint quota before its
agents can create or revise checkpoints. PostgreSQL serializes aggregate and revision admission on
that exact workspace's quota row and enforces positive maxima for aggregate count, retained revision
count, and UTF-8 bytes in canonical revision content and evidence JSONB text. An unprovisioned or
over-limit write rolls back atomically and returns only `MNEMO_QUOTA_EXCEEDED`; scoped reads remain
available. The runtime role cannot inspect or change quota records. Lowering a limit below existing
usage preserves canonical data and denies further affected writes until an administrator changes
the limit or usage is removed through an authorized lifecycle operation.

Team operations inspection uses the dedicated backup/operations credential, never the MCP runtime
credential. The installed administrator command returns one whole-team point-in-time snapshot with
only schema version, aggregate workspace/project/active-membership counts, aggregate checkpoint
quota coverage and utilization, and durable outbox backlog/lease/failure counters. It returns no
tenant identity, memory or job payload, source path, credential, or exception. Strict operator
thresholds produce closed `MNEMO_TEAM_*` alert codes; the machine check exits 0 for healthy, 1 for
active alerts, and 2 for an unavailable or invalid check. Alert notification and historical metric
storage remain external operator responsibilities.

## Evidence requirements

Every durable memory includes:

- stable `memory_id` and typed `memory_type`;
- a claim or structured value;
- status: `candidate`, `active`, `superseded`, `retracted`, or `expired`;
- confidence and source trust class;
- one or more evidence references with source ID, location, observed time, and content digest;
- extractor version; model, provider, and prompt version if a model participated; and
- an append-only revision chain linking corrections and supersession.

An assistant statement or model extraction cannot become a user fact without user-authored or
verified evidence. Unsupported inference remains labeled inference and is not persisted as an
active fact. Evidence must be inspectable without requiring a full transcript replay. A source
deletion, digest change, or loss of authorization invalidates or retracts dependent active claims
unless independent evidence remains.

## Prohibited data

Mnemo must not persist, embed, log, cache, export, or return:

- passwords, private keys, seed phrases, session cookies, bearer tokens, API keys, OAuth refresh
  tokens, signing secrets, or raw credential files;
- authentication material or memory claimed as proof of identity or authorization;
- unrelated files or data outside the explicitly registered source and task scope;
- data whose license, consent, or access terms prohibit the intended processing;
- raw model hidden reasoning or provider-internal metadata not explicitly made available for
  storage; or
- deleted, retracted, expired, or unauthorized payloads.

High-sensitivity personal, financial, health, employment, or legal information is denied by
default. A later policy may allow a narrowly defined class only with explicit purpose, consent,
encryption, visibility, retention, and deletion controls. Secret detection is deterministic and
pluggable; a model may supplement but never replace it.

## Consent and capture

Source registration is explicit and describes paths, categories, purpose, sensitivity, capture
mode, and retention before ingestion. Automatic capture is off until its later issue supplies a
consent surface and tests. Issue-2 fixtures are specifications, not ingestion authorization.

Writes such as checkpoint creation, correction, pinning, source registration, import, and deletion
require an intentional user action or a narrowly authorized client action visible to the user.
Read-only retrieval does not imply write consent. Connector permissions are source-specific,
least-privilege, revocable, and cannot be expanded by retrieved content or model output.

Personal event-job inspection is a content-free aggregate over one explicitly registered project.
An explicit retry action may only clear the last bounded failure code and make an incomplete job
with no active lease immediately available; it preserves the attempt count and cannot record
completion or handler effects. The surface never exposes job/source/task identities or payloads.

An explicitly submitted task-activity event is a minimized summary with category, actor,
sensitivity, retention, scope, and evidence metadata. It is not authorization to retain a raw
conversation, prompt, command, tool argument/body/result, source body, or hidden model trace, and
it does not enable automatic capture.

Consent withdrawal stops new capture immediately and schedules affected data and projections for
deletion under the selected policy. Optional model processing must be separately enabled and must
name the provider, task, and data class. Mnemo continues deterministic operation when model
processing is disabled.

Optional episodic extraction operates on one already-authorized minimized task event. Provider
output may propose only a bounded kind, claim, confidence, and sensitivity; it cannot supply scope,
evidence, retention, identity, lifecycle status, or provider provenance. Mnemo copies authority
fields from the canonical source event, records configured extractor/provider/model/prompt
versions, and persists only inactive candidates after deterministic safety validation. Extraction
does not itself authorize approval, activation, retrieval, or automatic event capture.

Candidate review is an explicit, exact-scope user action backed by verified user-correction
evidence. Confidence, repetition, provider identity, and sensitivity never substitute for that
authority. Approval retains the candidate identity and extraction provenance, activates the claim,
and adds the review evidence to its source evidence; rejection records only the review and creates
no active memory. Identical review delivery is idempotent, while a competing decision fails rather
than replacing the first action.

## Retention defaults

These conservative personal-mode defaults apply until changed through a versioned policy:

| Data class | Default |
|---|---|
| Working memory | Session lifetime; not durable unless explicitly checkpointed |
| Unapproved candidate memory | 14 days, then expire and purge |
| Raw permitted task events | 30 days after task completion |
| Approved checkpoints and episodic memories | 180 days since last use; pinned items persist until unpinned |
| Knowledge source content | While the registered source exists and remains authorized; tombstone on deletion |
| Structural projections | Until source fingerprint changes, source is removed, or project is unregistered; stale data is not presented as current |
| Payload-free audit metadata | 365 days, subject to future security and legal review |

Retention is enforceable deterministic policy. Access does not silently extend retention unless
the policy explicitly defines “last use.” Expired content is excluded immediately and purged from
canonical storage and rebuildable projections by a durable job. Future backup retention must be
documented before backups are implemented.

For an extracted episodic candidate and any memory approved from it, the canonical schedule is the
schedule copied from its permitted source event. A deterministic exact-task-scope sweep may append
one payload-free expiration record only when that non-permanent schedule is due. The record binds
the memory, source event, policy, scheduled time, actual sweep time, and scope; exact replay is
idempotent and conflicting metadata fails closed. Once recorded, candidate, review, active-memory,
governance, and revision payload reads exclude the identity immediately, including after restart.
Approval, correction, retraction, confidence, or access cannot extend this schedule. The current
purge operation then removes the candidate claim and its dependent review/governance payloads and
newly orphaned evidence while retaining the payload-free expiration record as an anti-resurrection
tombstone. It does not remove the permitted source task event or evidence still referenced by that
event. A separate explicit user action can now delete that individual memory or its minimized
source event in the production episodic slice. Individual deletion writes a deterministic,
payload-free exact-scope tombstone and removes candidate, review, active, governance, link, and
newly orphaned evidence payloads. Source deletion writes its own tombstone plus dependent memory
tombstones, removes all dependent payloads, and then removes the source event, evidence links,
newly orphaned evidence, and task-activity outbox job. Existing expiration and purge metadata is
retained, exact replay is idempotent, and either tombstone prevents re-ingestion from restoring the
deleted identity. Export cleanup and backup cleanup remain separate lifecycle operations.

An explicitly minimized task-activity event follows the same deterministic pattern using its own
canonical schedule. Expiration hides its summary and evidence immediately. Physical purge waits
until every dependent episodic candidate payload has been purged, then removes the event, evidence
links, newly orphaned evidence, and task-activity outbox job while preserving payload-free event
and candidate tombstones. Event retry cannot restore expired or purged content. This lifecycle does
not apply to arbitrary conversations, source documents, checkpoints, exports, or backups.

## Context selection

Context planning is deterministic and inspectable. A bounded transient query may select memory
categories through literal intent rules, but it cannot change scope, authority, consent, or
mutation policy and is never persisted as memory. Exact task scope is supplied to the episodic
repository before any lexical, temporal, confidence, or type-priority score is computed. Only
active, non-expired, non-deleted memories returned by that authorized read become candidates.

Episodic ranking uses a documented stable formula and deterministic tie breakers. Every selected
item remains untrusted evidence, retains its complete evidence references, source trust,
sensitivity, observed time, rank, score, and retrieval method, and receives a matching provenance
notice. The episodic section limit and packet total limit are both hard bounds; a candidate that
does not fit produces a bounded omission rather than truncation or budget overflow. Candidate
retrieval itself is bounded, and storage failure yields only a payload-free omission. Retrieval
does not update access time, retention, confidence, or any canonical record.

A content-free explanation may be derived from a canonical packet after strict packet and budget
validation. It reports item identity/type/scope, source and evidence metadata, ranks, retrieval
methods, omissions, conflicts, validity, and token accounting, but never repeats retrieved content,
query text, or evidence locations. Explanation performs no retrieval or mutation and cannot prove
that a caller-supplied packet originated from Mnemo; its output labels that basis and is never
authentication, authorization, or mutation evidence.

The deterministic plan may route a transient query into already-registered lexical knowledge and
retained source-identity indexes. General queries search both bounded categories; a specialized
literal intent searches only its selected categories. A closed list of question and category words
may be removed from the source query so the remaining identifiers still use the existing exact,
prefix, and all-term match contract. Explicit category and structured requests are never
overwritten. Semantic retrieval remains explicit and uses only an already-built local projection.

When lexical and semantic knowledge are both explicitly requested, their raw term counts and
cosine-derived values are never compared. Mnemo deduplicates exact revision/section identities and
uses reciprocal-rank fusion with `k=60`; path, revision, and section identity provide deterministic
ties. Each included section records whether lexical, local-vector, or both rank signals contributed.
Each candidate stream is scoped and bounded before fusion.

Final packet selection is conservative and deterministic. Items are exact duplicates only when
their scope, sensitivity, content, source reference, and source digest all match; one
authority/rank-stable identity remains while its combined evidence is preserved. At most two
non-conflicting knowledge or episodic items may consume a category from the same exact evidence
source set. Active checkpoints, mandatory checked-in procedures, and every declared conflict
participant are exempt from removal. Two included items citing one source reference with different
digests remain visible as an unresolved source-integrity conflict. This rule does not infer a
contradiction from prose or semantic similarity. Selection only removes already-authorized items,
adds explicit omissions, and recomputes the packet's provenance and token invariants.

Client rendering is a pure projection of that completed canonical packet. Codex and Claude Code
renderings use fixed client-labeled line records and preserve canonical item order, exact content,
provenance identities, ranks, conflicts, omissions, and token accounting. Every dynamic value is
JSON-quoted inside a fixed record, and a fixed trust-boundary record states that retrieved content
cannot grant authority, expand scope, or authorize tools or mutations. Rendering performs no
retrieval, ranking, policy decision, or persistence and cannot mutate the packet. The MCP default
remains the canonical packet; an explicit client-rendering request returns that unchanged packet
beside the derived text.

## Correction and conflict handling

Corrections append a new revision; they do not rewrite historical evidence. The prior revision is
superseded or retracted immediately and cannot be retrieved as active. User corrections outrank
agent-derived memories but cannot override current repository/dbt structural facts or mandatory
checked-in rules. Conflicting active evidence remains visible as a conflict until the user or a
higher-authority source resolves it.

For an approved episodic memory, its approval action is revision one. Each correction or
retraction names the exact expected current revision and carries verified user-correction evidence,
so stale concurrent actions cannot fork history. Correction preserves memory identity, source
scope, retention, and extraction provenance while superseding the prior claim. Retraction appends
a terminal payload-free revision and removes the memory from active reads; replaying the ordered
action stream must reconstruct the same history and active state.

Confidence changes never substitute for correction. Every correction records actor, scope, time,
reason, and evidence without logging sensitive payloads.

## Export and deletion

An authorized user can export canonical memories, current revisions, evidence references, scope,
provenance, status, retention metadata, and deletion tombstones in a documented portable format.
Rebuildable embeddings and indexes need not be exported if they can be reproduced without loss.
Exports are scoped, integrity-verifiable, and clearly become the user's responsibility after
delivery.

The production episodic export is one versioned exact-task-scope bundle. It contains currently
permitted minimized task events and non-expired/non-deleted candidates, their review and governance
action streams, deterministically replayed revision chains, and matching payload-free memory/source
expiration, purge, and deletion tombstones. It retains evidence, retention, source, extraction,
model/prompt, correction, and scope provenance already present on those canonical objects. Stable
identity ordering, canonical UTF-8 JSON, and a SHA-256 content digest make identical scoped state at
one export time byte-identical and tampering detectable. Authorization is applied before payload
reconstruction; excluded content never enters the bundle, while tombstones remain so a future
importer can prevent resurrection. This service returns the bundle but does not persist an export
file or claim approved-fact, knowledge, structural, or backup support.

The episodic import path accepts that validated bundle and one exact target task scope. It
reconstructs task events, candidates, reviews, governance actions, and derived revisions through
their canonical factories so every scope-derived identity is deterministically rebased while
content, evidence, retention, timestamps, review decisions, and correction semantics remain
unchanged. The target may be empty or an exact resumable subset; unrelated state fails before a
write. Completion requires exact typed target state, category counts, the validated source digest,
and the target repository's canonical digest. A partial failure is not success and an exact retry
must converge.

Payload-free expiration, purge, and deletion records are deterministically mapped from their
retained source identity and exact target scope, then rebuilt through normal expiration/purge or
scope-aware deletion factories. PostgreSQL stores the strict target projection, retained source
identity, and source digest atomically in one forced-RLS canonical table; it never manufactures a
deleted event or candidate payload and cannot retain a summary or claim. Exact replay is
idempotent, competing source/target mapping fails closed, ordinary export includes the imported
tombstones, and ordinary event/candidate writes consult them before mutation. Complete episodic
personal-to-team state now has verified counts and source/target hashes.

Checkpoint history uses a separate `mnemo.checkpoint-export.v2` exact-task bundle containing every
live aggregate, immutable revision, deterministic lifecycle event, and payload-free checkpoint
deletion tombstone. It validates canonical order, contiguous predecessor chains, current pointers,
lifecycle state, evidence, timestamps, live/deleted disjointness, deletion uniqueness, and one
SHA-256 digest. The parser remains compatible with the exact version-1 live-history format, whose
absent tombstone state is necessarily empty. Personal-to-team import preserves every checkpoint,
revision, event, deletion action key, and timestamp while rebasing only scope-derived deletion
identity into the explicit target. PostgreSQL accepts only an empty or already-identical target,
inserts live history, normal outbox jobs, and deletion tombstones atomically behind forced RLS,
records source deletion/digest provenance, and verifies the target bundle before commit. No erased
payload is manufactured. The application returns verified live/deletion counts plus strict source
and target digests; exact replay is idempotent. Source-structure observations remain excluded
rebuildable projection links.

Approved episodic facts use a separate `mnemo.approved-event-export.v1` exact-task bundle containing
every retained event payload, immutable correction or retraction, and ordered pin action. Import
rebuilds scope-derived identities in the exact target scope and retains source identity plus source
digest provenance. PostgreSQL writes the native event, governance, pin, and outbox rows atomically
behind forced RLS and verifies the target bundle before commit. An already-retracted target receives
only a deterministic payload-free target identity: its deleted summary, source key, and direct
event evidence are never manufactured or restored. Exact replay is idempotent, ordinary governance
inspection and anti-resurrection remain effective, and the application returns verified counts and
source/target hashes.

Knowledge history uses a separate `mnemo.knowledge-export.v1` exact-project bundle containing every
active source pointer, all retained immutable revisions, declared Markdown/Obsidian links, minimal
payload-free deletions, and last successful sync time. Import deterministically rebases document
identity to the target project while preserving revision IDs, predecessor chains, paths, digests,
source kinds, titles, frontmatter, sections, links, and timestamps. PostgreSQL stores active history
in the native forced-RLS tables with source identity and digest provenance. An already-deleted note
uses a dedicated forced-RLS projection containing only source/target identity, path, digest,
deletion time, and import provenance; it never recreates a source creation time, revision, title,
frontmatter, section, or link. Exact retry is idempotent and target embeddings/search data remain
rebuildable projections. Structural export/import remains a separate requirement.

Deletion fails closed and is idempotent. It immediately removes the item from retrieval, writes a
minimal non-sensitive tombstone when needed to prevent resurrection, and propagates to canonical
data, indexes, embeddings, caches, jobs, derived summaries, and controlled exports. Source
re-ingestion must honor tombstones and current consent. Local deletion should finish within 24
hours once persistence exists; failures remain visible and retryable. Backup deletion semantics
must be defined before backup support ships.

For the current production episodic slice, only a user-authored exact-task-scope action can delete
an extracted memory or its explicitly minimized task-event source. The stored deletion metadata
contains identity, scope, actor, action key, cause/dependency identity, and time only; it contains
no event summary, claim, reason, or evidence payload. The operation atomically removes every
content-bearing canonical row and task-activity job controlled by this slice, deletes only newly
orphaned evidence, preserves unrelated sources and memories, and rejects competing actions,
action-key reuse, cross-scope targets, and target/source mismatches. Checkpoint deletion follows
the same explicit pattern for one exact task-scoped aggregate. It removes all canonical revision
and evidence content, lifecycle events, source observations, and checkpoint outbox jobs after
storing a payload-free anti-resurrection tombstone. Knowledge-document governance, structural
projections, prior exports, backups, and external copies remain separate boundaries. Checkpoint
tombstones are included in new version-2 checkpoint transfers.

## Structural projections versus durable memories

Repository state, dbt nodes and edges, tests, columns, catalog metadata, source freshness, and Git
fingerprints are rebuildable structural projections. They retain source IDs, digests, observed
times, target/environment, and staleness state, but are not user facts and do not gain durable
authority through retrieval frequency.

A local MCP process bound to one registered project refreshes that project's syntax-only source
projection once before serving requests. Refresh is fail-open: an unreadable, unsafe, or oversized
checkout leaves the prior projection available but cannot prevent the coding agent from operating.

Durable memories represent approved evidence-bearing claims, decisions, preferences, outcomes,
and checkpoints with revision and retention lifecycles. They may cite a structural projection but
must not duplicate it as an allegedly timeless fact. When the authoritative artifact changes,
the projection becomes stale or is rebuilt; Mnemo does not “correct” the artifact using memory.
