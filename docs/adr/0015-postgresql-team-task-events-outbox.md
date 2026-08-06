# ADR 0015: Team task events and delivery state commit together

- **Status:** accepted
- **Date:** 2026-08-06
- **Deciders:** Mnemo maintainers
- **Issue:** 21F
- **Supersedes:** none
- **Superseded by:** none

## Context

Team checkpoint and knowledge storage exists, but production episodic processing cannot begin
without a minimized canonical source event and durable at-least-once delivery metadata. The
existing storage-neutral `TaskActivityEventRepository` and `EventOutboxRepository` contracts
already define safety rejection, event idempotency, lease ownership, retry, project status, and
explicit failed-job requeue. PostgreSQL should implement that behavior before candidate extraction,
approved-fact governance, retention, or remote composition.

## Decision

PostgreSQL migration 0004 adds append-only task-activity events and one mutable transactional
outbox inside `mnemo_team`. Every row repeats exact workspace, project, owner, visibility, session,
and task identity. Both tables enable and force row-level security through the established
principal/workspace/operation function. Event payloads use the existing strict retention and
evidence JSON serialization; identity, source key, kind, actor, sensitivity, time, and scope remain
relational and constrained. Raw prompts, transcripts, commands, tool bodies, and tool results are
not part of the event contract.

`PostgreSQLTaskActivityEventRepository` runs deterministic task-event safety policy before opening
a write transaction. An accepted new event and its deterministic `task_activity` outbox job commit
together. The same complete event/source-key retry is idempotent; changed content under the same
key or identity fails. Events are immutable to the runtime role.

`PostgreSQLEventOutboxRepository` selects only one exact task scope before claiming jobs. Claims use
`FOR UPDATE SKIP LOCKED`, increment the attempt count, and attach a bounded worker lease. Completion
or retry requires the exact live lease owner. Retry records a bounded failure code and next
availability; explicit project-level requeue clears only the failure and expired lease for at most
100 selected jobs, preserving attempts and never claiming handler success. Project status returns
only pending, processing, and failed counts.

The same insert helper now creates an outbox job for each new PostgreSQL checkpoint lifecycle
event. Migration deliberately does not synthesize jobs for checkpoint history committed before
v4; replay/backfill requires a separate operational contract.

## Alternatives considered

- **Use an external queue as canonical delivery state.** Rejected because event and delivery
  intent could not commit atomically and tenant authorization would depend on another product.
- **Store raw interaction payloads for later extraction.** Rejected because the product contract
  permits only explicitly minimized, evidence-bearing summaries.
- **Claim jobs with an unlocked select followed by update.** Rejected because concurrent workers
  could both observe and process one delivery attempt.
- **Reset attempts during manual requeue.** Rejected because it would erase operational evidence
  and misrepresent retry history.
- **Backfill all existing checkpoint events automatically.** Rejected because historical delivery
  may duplicate prior effects and no operator intent or handler version is available.

## Consequences

PostgreSQL now provides the canonical source and delivery primitive for later team episodic
processing. It still does not supply an extractor, approved-memory governance, retention,
deletion, import, scheduler, worker daemon, remote service, or usable team mode.

The runtime role can select/insert task events and select/insert/update outbox jobs. It cannot
update or delete task events or delete outbox rows. Future retention/deletion work must add an
explicit constrained deletion path rather than broadening ordinary worker privileges.

## Security and privacy implications

Authorization occurs before event or queue selection, including row locks and project status.
Source-validation triggers use a fixed `pg_catalog` search path and require an outbox source to
match the event's complete scope, kind, and occurrence time. Unsupported future topics fail closed.
Database and handler failure details are reduced to bounded repository outcomes and failure codes.

The database credential is not authentication. A future service must derive its principal and
workspace from verified authentication, and workers must receive only the role and scope needed
for their queue partition.

## Token and cost implications

Events retain at most 1,200 summary characters and 64 evidence references under the existing
domain contract. The outbox stores metadata only. No model call, embedding, or context retrieval is
added.

## Dependency and licensing implications

No dependency is added. The implementation uses PostgreSQL locking and the existing approved
driver.

## Reversal or migration strategy

Migration 0004 is atomic and forward-only. A failure from valid v3 leaves ledger `(1, 2, 3)` and
neither v4 table. Recovery before team release is restore of the verified pre-upgrade backup or
correction followed by idempotent migration. Canonical event and attempt history must be exported
or migrated before any future table removal.

## Verification

- A real injected v3-to-v4 failure retains v3 and a clean retry reaches v4.
- Event creation, exact retry, conflicting retry, secret rejection, restart reads, ordering, and
  private-project denial run against a non-owner/non-`BYPASSRLS` runtime role.
- Event creation exposes exactly one deterministic job; rejected writes expose neither event nor
  job.
- Claim, active-lease exclusion, wrong-worker denial, retry, failed status, bounded requeue,
  second claim, completion, and attempt preservation run against real PostgreSQL.
- Runtime privilege inspection proves task events cannot be updated and outbox jobs cannot be
  deleted.

## References

- `docs/implementation-plan.md`, Milestones 4 and 9
- `docs/adr/0010-team-authorization-kernel.md`
- `docs/adr/0012-postgresql-team-control-plane.md`
- `docs/adr/0014-postgresql-team-checkpoints.md`
