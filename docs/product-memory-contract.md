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
visible in the result. Team authorization design is deferred; personal-mode nullability must not
be treated as a team-mode authorization rule.

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
action-key reuse, cross-scope targets, and target/source mismatches. This does not yet claim the
same operation for checkpoints, knowledge documents, exports, backups, or external copies.

## Structural projections versus durable memories

Repository state, dbt nodes and edges, tests, columns, catalog metadata, source freshness, and Git
fingerprints are rebuildable structural projections. They retain source IDs, digests, observed
times, target/environment, and staleness state, but are not user facts and do not gain durable
authority through retrieval frequency.

Durable memories represent approved evidence-bearing claims, decisions, preferences, outcomes,
and checkpoints with revision and retention lifecycles. They may cite a structural projection but
must not duplicate it as an allegedly timeless fact. When the authoritative artifact changes,
the projection becomes stale or is rebuilt; Mnemo does not “correct” the artifact using memory.
