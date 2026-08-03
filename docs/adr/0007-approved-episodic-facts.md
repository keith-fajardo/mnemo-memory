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

## Consequences

This provides a durable, privacy-bounded event substrate for later application and context
retrieval work. It does not infer decisions or mistakes from a diff, failed command, or model
reasoning. Retention, correction/supersession, deletion propagation, automatic capture, and
background event processing remain separate, explicitly designed milestones.
