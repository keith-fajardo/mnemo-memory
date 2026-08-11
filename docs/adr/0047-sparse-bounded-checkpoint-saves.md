# ADR 0047: Make checkpoint size and evidence preparation Mnemo-owned

## Status

Accepted for the explicitly approved checkpoint-hardening issue.

## Context

Captured production-like calls exposed two retry loops. A caller could label dense checkpoint text
with a low `token_estimate`, which Mnemo trusted on create; `record_lesson` later recomputed the
same content and rejected it at the 600-token ceiling. Separately, strict lowercase UUID parsing
rejected uppercase spelling even though it represented a valid UUID. Both failures consumed the
agent's initial tool-call tokens and the sanitized failed response, then encouraged another call.

Canonical checkpoint JSON also emitted every empty optional list, while evidence locations emitted
four null coordinates when no span existed. JSON `null` is a value rather than an absent property,
so those encodings spent context without conveying state. Asking an agent to stay below a target
cannot guarantee the canonical serialized size because the agent does not own Mnemo's estimator.

The normal evidence input required the agent to repeat full digests and UUIDs. Replacing a full
digest with a fixed six-hex-character value would weaken identity: six hexadecimal characters have
only 24 bits. Git instead defines `--short=<length>` as a unique prefix of at least that length and
may lengthen it when needed.

## Decision

Mnemo owns checkpoint sizing. Every create, revise, complete, abandon, and lesson revision ignores
the deprecated caller estimate, serializes the canonical content, and computes the same local
estimate used at retrieval. New revisions target 200 estimated tokens. If needed, deterministic
compaction keeps the objective and state, at most one next action, blocker, decision, verification,
and the newest evidence-backed lesson; it drops lower-priority repetitions and then progressively
truncates retained text. The response reports the original estimate plus omitted-item and
truncated-field counts. The existing 600-token ceiling remains a defense-in-depth maximum.

Older lessons remain immutable in predecessor revisions and are selected through the existing
bounded historical-lesson path. A lesson-only write stores the newest lesson in the active revision
instead of copying every older lesson. Duplicate and maximum-history checks still walk exact-scope
predecessors.

Canonical content writes only required objective, state, and estimate fields plus populated
optional collections. Evidence locations write only `uri` when no span exists. Decoders accept
both the new sparse shape and previous dense empty/null shapes; no storage migration is required.
Valid UUID input is normalized at the MCP boundary while internal identifiers remain lowercase
canonical values.

Local MCP callers may send mutually exclusive `evidence_files` instead of full references. A
registered-project resolver validates bounded relative regular files, rejects traversal and
symlinks, computes the full SHA-256 and deterministic IDs locally, and fills lesson evidence IDs.
Full hashes remain canonical integrity data. The Git label uses a unique abbreviation of at least
six characters for display only. Team composition receives no implicit filesystem resolver.

The existing diagnostic mode also governs a separate exact-scope checkpoint-save ledger. Summary
records failures; trace records all attempts; off records none. Events contain canonical scope for
filtering plus closed outcome metadata, and never checkpoint text, paths, hashes,
checkpoint/revision IDs, arbitrary invalid IDs, prompts, tool payloads, or reasoning. Observation is
fail-open and begins only after the application can resolve an exact task scope; transport/schema
failures before that boundary are not falsely attributed.

## Consequences

The observed undercount and UUID-spelling retries become successful first calls. Normal local saves
have shorter tool input because scope IDs, caller estimates, evidence UUIDs, and hashes can be
omitted. Sparse persisted content and a 200-token target reduce later automatic attachment cost.
Compaction can omit or shorten submitted detail, but it is deterministic and explicitly reported;
callers should use approved events or separate lesson revisions for facts that must survive outside
the compact active handoff.

The 200-token value is a product target under Mnemo's existing character heuristic, not provider
billing. It does not eliminate tokens already spent forming a tool call, transport validation
failures, storage failures, or a provider's tool-schema overhead. Diagnostics support measurement
without claiming access to hidden chain of thought.

## Evidence

- [Git `rev-parse --short`](https://git-scm.com/docs/git-rev-parse) specifies a unique object-name
  prefix with at least the requested length.
- [JSON Schema null reference](https://json-schema.org/understanding-json-schema/reference/null)
  states that a null value is not equivalent to a property being absent.
- Regressions reproduce caller undercount, lesson growth, uppercase UUID input, sparse legacy
  compatibility, local file evidence, and content-free failure observation in Mnemo's own tests.
