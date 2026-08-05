# ADR 0007: Explicit approved episodic facts

- **Status:** Accepted
- **Date:** 2026-08-03
- **Scope:** Episodic-memory foundation

## Context

Checkpoint lifecycle events say that a checkpoint transition occurred. They do not represent a
separate, user- or agent-approved decision, failed approach, or bounded tool outcome. Retaining
arbitrary transcripts, prompts, command output, source text, or a model's private reasoning to
fill that gap would violate Mnemo's privacy and evidence boundaries.

## Decision

Mnemo stores a distinct immutable `ApprovedEpisodicEvent` only when a connected agent or user
explicitly supplies it. Its permitted kinds are `decision`, `failure`, and `tool_outcome`. Every
event has complete task scope, a bounded factual summary, occurrence time, one or more exact
evidence references, and a caller-provided source-event key.

The source-event key is unique within complete task scope. Repeating the exact same event is
idempotent; attempting a different event with the same key is a typed conflict. Facts are stored
separately from checkpoints and never mutate checkpoint content or lifecycle history. Reads are
scope-first; another task's event is indistinguishable from absent.

SQLite migration 0007 persists only the bounded event fields and evidence relationships. It never
stores transcript bodies, prompts, arbitrary tool output, SQL, environment data, credentials, or
absolute machine paths. The reference and SQLite adapters have the same append, retrieval,
pagination, evidence, idempotency, and cross-scope behavior.

Correction and retraction use a separate append-only governance record introduced by migration
0013. One fact may have at most one outgoing governance action. A correction atomically appends a
same-kind replacement fact and an evidence-bearing link from the superseded fact. A retraction
atomically appends an evidence-bearing tombstone and removes the target event row plus its original
evidence links, so its summary and source key are no longer reviewable or retrievable. The
deterministic target ID remains in the tombstone to prevent resurrection with the same source key.
Repeating the same action key and intent is idempotent; a competing or stale action is a conflict.

Ordinary approved-event listing is an authorization-constrained active query: it excludes any
event that has an outgoing governance action in SQL or in the reference adapter before context
selection. Review listing is a separate bounded scoped contract that may return active events,
superseded events, and payload-free retraction tombstones. Migration 0013 is forward-only and
transactional; failure rolls back its schema objects and ledger entry, and retry re-applies the
whole step. Recovery from a successfully committed migration requires restoring the pre-upgrade
personal-profile database backup rather than attempting a lossy down-migration.

Pinning is a separate immutable user-action ledger introduced by migration 0027. A pin does not
change fact content, evidence, source authority, or authorization; it only places an active fact
before unpinned recency inside the already bounded approved-fact selection. Pin and unpin actions
require verified user-correction evidence and complete task scope. Correcting a pinned fact
atomically releases the superseded identity and transfers the pin to its replacement; retracting a
pinned fact atomically records an unpin before payload erasure. Historical pin actions remain
audit metadata, while corrected and retracted review records are never reported as currently
pinned.

The personal dashboard can also create an on-demand `mnemo.approved-memory-export.v1` JSON
snapshot. It resolves one registered task scope before reading, traverses the existing bounded
application query, retains full event/governance evidence plus payload-free tombstones and current
pin state, and digests canonical content. The explicit same-origin response uses a fixed filename
and is not persisted by Mnemo. It is a user-held inspection export, not an import or backup format;
historical pin-action rows remain governed audit metadata rather than being duplicated into it.

## Consequences

This provides a durable, privacy-bounded event substrate plus explicit personal governance. It does
not infer decisions or mistakes from a diff, failed command, or model reasoning. The retraction
contract applies only to the canonical approved-fact payload and its direct evidence links; it
cannot recall a user-controlled export and is not a claim about future backups. General retention,
backup deletion, automatic capture,
and background event processing remain separate, explicitly designed milestones.
