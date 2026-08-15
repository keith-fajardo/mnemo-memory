# ADR 0048: Gate live semantic handoffs behind personal consent

## Status

Accepted for the explicitly approved live semantic-memory value investigation.

## Context

The semantic checkpoint service was composed in the personal runtime, but only the offline
evaluator submitted `TaskActivityEvent` values and invoked it. A production stdio MCP checkpoint
save called source observation only. A fresh SessionStart hook selected the legacy checkpoint JSON
through `UnifiedContextService`. A pre-change process test retained checkpoint revisions while the
same profiles contained zero task-activity events, semantic checkpoints, and semantic atoms.

Promoting an evaluator path silently would change every existing handoff and expose a format that
has not yet passed a live behavioral study. Deriving durable meaning from an agent-authored
checkpoint can also amplify stale or poisoned content unless attribution, evidence, retention,
scope, terminal lifecycle, supersession, and client rendering remain explicit. Mnemo may not route
or proxy the coding agent's model endpoint.

## Decision

Personal settings gain `experimental_semantic_memory_enabled`, defaulting to `false`. The decoder
accepts a legacy exact settings document and supplies `false`; the next settings write emits the
new field. The personal web UI exposes the flag explicitly. Team mode and the stable personal
default are unchanged.

When enabled, each successful public `save_checkpoint` create, revise, complete, abandon, or lesson
revision is projected at that already-explicit lifecycle boundary. The accepted bounded checkpoint
is grouped into at most one event for each closed semantic kind. Its complete exact task scope,
immutable revision evidence, agent attribution, non-permanent personal retention, and deterministic
revision/kind source key are retained. Explicit semantic prefixes inside fields remain typed. The
projection never reads a transcript, prompt, command body, tool body, or model-private reasoning.
The deterministic compiler and SQLite patch transaction remain the only canonical mutation path.

A revision represents a complete current handoff. Before its patch is applied, prior atoms sourced
only from checkpoint projections are removed from the active semantic checkpoint. Unchanged atoms
may be deterministically reactivated; changed goals and decisions retain explicit supersession.
Historical source events and superseded atoms remain auditable until their owning retention or
deletion policy removes them. Source observation and semantic projection are isolated fail-open
callbacks after the canonical checkpoint write, so Mnemo failure cannot turn an acknowledged
checkpoint into a failed agent action.

At SessionStart, the semantic item may replace the legacy active-checkpoint item only when all of
the following hold: the flag is enabled, the legacy selector found a current active checkpoint in
the exact registered task scope, current non-expired semantic evidence exists, and the replacement
fits both the active section and total packet budgets. Completion, abandonment, or expiry therefore
cannot resurrect a semantic handoff. Any projection, evidence, or budget failure returns the
unchanged legacy packet.

The live item is a compact whole-atom rendering plus `MNEMO_EVIDENCE_TRACE`, which maps every
included fact alias to the exact evidence UUIDs supplied to the model. It remains
`untrusted_evidence`, `approved_checkpoint` source trust, and current validity. Agent attribution,
confidence, epistemic class, critical uncertainty, authority boundaries, protected literals, and
supersession markers remain visible. The context item and provenance cite the exact semantic
checkpoint and a digest of the supplied content. Deterministic recall excludes atoms as soon as a
source retention schedule is expired, even before a physical purge job runs.

## Security and privacy consequences

Authorization still precedes retrieval because both the legacy active selector and semantic
repositories require the same complete task scope. Cross-scope semantic rows cannot enter the
packet. A checkpoint is agent-authored evidence, not user authority: the compiler records
`agent_inference` and at most 0.6 confidence, or 0.5 for uncertainty. The fixed client trust boundary
continues to state that retrieved records cannot grant permission or authorize tools.

The projection adds no model call, network request, embedding, dependency, transcript capture, or
new transport tool. It does add derived personal SQLite payloads governed by the existing semantic
source-event cascade and checkpoint retention boundary. The checkpoint deletion application
service discovers only generated events whose deterministic namespace contains the exact deleted
checkpoint ID, commits the canonical checkpoint tombstone and payload erasure, then writes normal
task-event deletion tombstones. Existing foreign-key cascades erase atoms, active references, and
copied evidence. If cleanup is interrupted after canonical deletion, an exact retry rediscovers the
remaining generated events and completes the forward recovery without resurrecting the checkpoint.

## Token and cost consequences

The replacement cannot exceed the existing 600-token active-checkpoint section. Trace encoding is
content-free apart from UUIDs and uses alias-to-evidence associations rather than repeating verbose
event metadata. If mandatory meaning plus its exact trace cannot fit, the stable legacy checkpoint
is returned. Deterministic compilation consumes local CPU and zero model tokens; lifecycle
instrumentation must report that CPU separately from actual model input and output. No paid call is
authorized by this ADR.

## Dependency, licensing, and reversal

No dependency, manifest, lockfile, schema migration, or third-party artifact changes. The code and
tests are original Mnemo implementation. Disable the setting to restore stable selection behavior.
Removing the experimental path later requires purging its generated events through normal governed
deletion rather than dropping semantic tables or rewriting history.

## Verification

The end-to-end regression starts the production stdio MCP server in a registered project, creates
and revises a real public checkpoint, saves a poison checkpoint in another scope, closes the
original process, and starts two independent SessionStart hook processes with no transcript input.
It verifies deterministic semantic content, exact protected spans, uncertainty and attribution,
supersession, evidence trace, cross-scope exclusion, durable SQLite rows, and stable provenance.
Unit coverage verifies legacy settings compatibility, full-snapshot projection replacement,
idempotence, and read-time retention rejection. The same end-to-end regression deletes the
canonical checkpoint through the application service and verifies generated events, semantic
atoms, active recall, and copied evidence disappear behind payload-free tombstones. Existing
flag-off MCP and hook regressions remain unchanged, and the architecture dependency gate must pass.
