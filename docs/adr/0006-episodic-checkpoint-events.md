# ADR 0006: Evidence-bearing checkpoint lifecycle events

- **Status:** Accepted
- **Date:** 2026-08-03
- **Scope:** Episodic-memory foundation

## Context

Checkpoint revisions preserve a durable handoff, but a revision alone does not provide an
independently queryable chronological record that says *which lifecycle fact happened*.  A later
context service needs to distinguish creation, ordinary progress, completion, abandonment, and a
recorded reasoning correction without copying the checkpoint body into another mutable store.

The product must not address this by collecting a conversation transcript, command output, source
text, environments, SQL, or a model's private reasoning.  Those sources have different privacy,
evidence, and retention requirements.

## Decision

Mnemo introduces an append-only `CheckpointLifecycleEvent` domain fact.  The first event family is
limited to checkpoint transitions:

- `checkpoint_created`
- `checkpoint_revised`
- `checkpoint_completed`
- `checkpoint_abandoned`
- `checkpoint_lesson_recorded`

Every event is scoped to the same complete task scope as the checkpoint revision it identifies.  It
contains only the event kind, checkpoint and revision identities, revision number, occurrence time,
a deterministic idempotency key, and the exact revision evidence references.  It contains no
checkpoint content or arbitrary payload.

An event identity and idempotency key are deterministically derived from the event kind and the
immutable checkpoint/revision identities.  A retry therefore cannot create a second historical
event for the same transition.  The revision remains the canonical source of handoff content;
events are chronological, evidence-bearing transition facts rather than a second checkpoint
representation.

Persistence and automatic projection make an event and its checkpoint transition atomic in both
the SQLite and reference repositories. A successful checkpoint mutation cannot lose its event,
and a failed mutation cannot leave an event or revision behind. Cross-scope reads remain
indistinguishable from not found.

## Consequences

This enables bounded, provenance-preserving episodic retrieval and later replayable projections
without relaxing the current no-transcript guarantee.  It does not claim to record every agent
action or infer a reasoning mistake.  New event families—permitted tool outcomes, user-approved
decisions, or imported documents—require their own domain contract, evidence source, privacy
review, retention/deletion behavior, and explicit ingestion boundary.

Migration 0006 stores the event ledger separately from immutable revision content. The SQLite
adapter projects creation, revision, completion, abandonment, and lesson-recording in the same
transaction as their revision mutation; the reference adapter mirrors that all-or-nothing behavior
for the shared contract. The ledger is now available for bounded future episodic retrieval, but is
not a transcript or a second mutable checkpoint store.
