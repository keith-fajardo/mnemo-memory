# ADR 0016: Team approved facts retain immutable governance and erase retracted payloads

- **Status:** accepted
- **Date:** 2026-08-06
- **Deciders:** Mnemo maintainers
- **Issue:** 21G
- **Supersedes:** none
- **Superseded by:** none

## Context

PostgreSQL already stores team knowledge, checkpoints, minimized task events, and delivery state,
but the existing explicit approved-fact contract is available only in the personal profile. Team
storage needs the same deterministic correction, retraction, pinning, evidence, and idempotency
behavior before an authenticated service can compose approved episodic context. It must not invent
a second lifecycle or preserve a retracted payload merely for audit convenience.

## Decision

Migration 0005 adds exact-task `approved_episodic_events`,
`approved_episodic_event_governance`, and `approved_episodic_event_pin_actions` tables. Every row
repeats workspace, project, owner, visibility, session, and task identity and uses forced RLS.
Facts, governance actions, pin actions, and their evidence are immutable. The runtime role may
delete a fact row only after an exact-scope retraction action exists; a fixed-search-path trigger
rejects every other deletion. Retraction therefore removes summary, source key, and fact evidence
while retaining bounded evidence-bearing governance metadata as the anti-resurrection record.

`PostgreSQLApprovedEpisodicEventRepository` runs deterministic safety policy before a transaction.
An append accepts only one complete source-key/identity intent. Correction inserts the replacement
fact and one governance action atomically, preserves the fact kind, and transfers an active pin
through immutable release/acquire actions. Retraction appends one governance action, releases an
active pin, and erases the target payload in the same transaction. Exact retries return the
already-committed record; competing target, action-key, identity, or replacement reuse fails.

Every accepted fact, correction/retraction, and pin action creates one deterministic metadata-only
outbox job in the same transaction. Migration 0005 extends the existing source guard to verify the
complete source scope, kind, and occurrence time for both approved-event topics. Historical
personal or PostgreSQL data is not inferred, imported, or backfilled by this migration.

## Alternatives considered

- **Keep retracted text for audit.** Rejected because user retraction requires payload erasure;
  bounded governance identity, reason, evidence, scope, and time are sufficient lifecycle proof.
- **Update one mutable fact row for correction and pinning.** Rejected because retries and
  competing actions would erase provenance and could not reconstruct why retrieval priority
  changed.
- **Use a separate queue write after commit.** Rejected because a committed mutation could become
  permanently invisible to downstream processing.
- **Add shared-source approval resolution now.** Rejected because authenticated actor/source
  ownership and conflicting team-correction policy are a separate service-level boundary.

## Consequences

PostgreSQL now implements the existing approved episodic-event repository contract, including
payload-erasing retraction and pin transfer. It still does not expose team mode, authenticate a
principal, resolve shared-source ownership, store extraction candidates, enforce retention,
import personal data, or provide remote operations.

The runtime role has select/insert/delete access to fact rows, select/insert access to immutable
governance and pin rows, and no update privilege on any of them. The fact deletion trigger narrows
delete to an already-recorded retraction. A future service must keep the database credential
infrastructure-only and expose only policy-validated application operations.

## Security and privacy implications

Authorization and exact task scope are applied before every read or mutation. Fixed-search-path
triggers bind governance, pin, erasure, and outbox rows to canonical source scope. Secret rejection
precedes connection acquisition, so rejected content reaches neither PostgreSQL nor the outbox.
Repository failures expose bounded outcomes without payloads or database details.

## Token and cost implications

The adapter stores only the existing bounded fact summary and evidence metadata. Retraction removes
that summary. No retrieval change, embedding, provider call, or new token budget is introduced.

## Dependency and licensing implications

No dependency is added. The implementation uses the existing PostgreSQL driver and Mnemo-owned
domain, policy, storage, and outbox contracts.

## Reversal or migration strategy

Migration 0005 is atomic and forward-only. Failure from valid v4 leaves ledger `(1, 2, 3, 4)` and
no approved-event table. Recovery before team release is restore of the verified pre-upgrade
backup or correction followed by idempotent migration. Any future schema replacement must preserve
immutable action order, payload-free retraction records, evidence, scope, and outbox delivery state.

## Verification

- An injected v4-to-v5 failure retains v4 and a clean retry reaches v5.
- Real PostgreSQL tests cover append/retry/conflict/secret rejection, ordering, pin priority,
  correction, pin transfer, retraction, payload erasure, terminal retries, and restart reads.
- Private-project viewers and different tasks cannot observe or mutate the records.
- Deterministic jobs are readable for accepted fact and governance mutations.
- Runtime privilege inspection denies updates and governance/pin deletion; a direct deletion of an
  active fact is rejected by the database trigger.

## References

- `docs/implementation-plan.md`, Milestone 9
- `docs/adr/0010-team-authorization-kernel.md`
- `docs/adr/0012-postgresql-team-control-plane.md`
- `docs/adr/0015-postgresql-team-task-events-outbox.md`
