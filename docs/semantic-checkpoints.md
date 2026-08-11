# Semantic checkpoints

Mnemo's semantic checkpoint slice follows one rule: store evidence without rewriting it, compile
typed meaning from that evidence, save incremental changes, and retrieve only the whole meaning
units needed for continuation. It is an additive personal-mode capability; the existing checkpoint
MCP tools and automatic context attachment are unchanged.

## Three distinct layers

1. **Evidence archive.** Existing `TaskActivityEvent` rows are immutable, exact-task-scope event
   envelopes. They store a bounded explicit summary plus immutable evidence references, not raw
   transcripts, prompts, command bodies, or tool bodies. The referenced source remains canonical.
   Reusing a source key with changed content is rejected.
2. **Semantic ledger.** `SemanticMemoryAtom` is the canonical structured meaning layer. Its closed
   kinds are `goal`, `fact`, `state`, `decision`, `constraint`, `preference`, `open_question`,
   `next_action`, `result`, `failure`, and `inference`. Subject, predicate, object, qualifiers,
   confidence, priority, lifecycle status, validity, source event IDs, and supersession are stored
   separately. Attribution and the epistemic qualifier distinguish a user claim, tool observation,
   and agent inference. Uncertainty lowers confidence rather than becoming a fact.
3. **Checkpoint view and rendering.** `SemanticCheckpoint` records its parent, generation, schema,
   head evidence event, delta/snapshot type, renderer profile, tokenizer identity, measured tokens,
   compression ratio, and patch digest. Checkpoint-to-atom rows select active atoms. `compact`,
   `portable`, and `audit` are model-facing views, not storage formats; audit expands evidence IDs.

The archive and ledger outlive a rendering. No checkpoint is compiled from an earlier rendered
summary, so repeated saves cannot accumulate prose-summary drift.

## Compiler and patch rules

`MemoryCompiler` is a provider-neutral protocol. The included `DeterministicMemoryCompiler` accepts
explicit prefixes such as `goal:` and `decision:`. Unlabelled task text remains an attributed,
lower-confidence inference unless the closed event type proves a tool/result observation. This
baseline does not guess hidden intent. A future model compiler must return the same closed patch
schema; its output remains an untrusted proposal until deterministic validation succeeds.

Patch operations are `add`, `update_metadata`, `supersede`, `resolve`, `expire`,
`activate_in_checkpoint`, and `remove_from_active_checkpoint`. Application is deterministic and
validates exact scope, source-event existence, atom identity, lifecycle targets, and immutable
meaning fields before persistence. SQLite applies the validated ledger and checkpoint in one
transaction. Exact retries are idempotent. A newer explicit goal or decision with the same
attributed subject supersedes the older active atom; the older atom remains inspectable but is not
active.

The first generation is a snapshot. Routine generations are deltas, and every eighth generation
is marked as a full semantic snapshot by default. Every checkpoint retains its bounded active atom
references, so restoration does not require replaying an unbounded prose chain.

## Protected meaning and phrase reduction

Before compact phrase reduction, Mnemo detects quoted/code spans, commands, paths, identifiers,
UUIDs, hashes, dates, versions, IP addresses, numbers with units, negation, modality, quantifiers,
conditions, exceptions, uncertainty, and approval/authority terms. Exact literals remain unchanged;
logical relationships remain in the indivisible atom meaning.

Phrase table `safe-phrases-v1` has only two reviewed rules:

```text
in order to -> to
utilize -> use
```

Rules apply only outside protected spans and are idempotent. Phrase reduction is a final rendering
optimization, not the semantic compiler. Mnemo does not use a private Caveman-style language or
model-generated lexical rewrite.

## Atomic selection and adaptive tokens

Recall accepts exact scope, query/task text, preferred token target, maximum ceiling, render mode,
and a tokenizer adapter. The default preferred target is 200 tokens and the default ceiling is 600.
These are cost goals, never correctness limits.

Selection orders active goals and success state, constraints and authority, decisions, current
state/facts, blockers/questions, next actions, failures/results, preferences, then inferences.
Query overlap ranks within those safety bands. Active goals, constraints, decisions, open questions,
next actions, commitments, authority boundaries, and critical uncertainty are mandatory regardless
of age. Optional atoms are admitted only as whole units within the preferred target. No renderer
uses an ellipsis inside an atom.

When optional units do not fit, the rendering reports their count, kinds, reason, and checkpoint-
local retrieval handles. If mandatory state exceeds 600, Mnemo emits all mandatory units, marks
`mandatory_overrun=true`, and omits only whole optional units. Compact renderings use local `A#` and
`E#` aliases; the result object retains the event-ID mapping for `inspect_evidence`. Portable mode
is self-describing; audit mode expands provenance.

`CheckpointTokenCounter` is the provider/model tokenizer extension point. Mnemo uses an injected
provider counter when available. The dependency-free fallback tokenizes lexical pieces and
conservatively charges long UTF-8 pieces; it is identified as `mnemo/conservative-lexical-v1` and
is not represented as an exact OpenAI or Anthropic count. Identical input and configuration produce
identical output.

## APIs

`SemanticMemoryService` exposes storage-neutral equivalents of `appendEvent`, `saveCheckpoint`,
`applyCheckpointPatch`, `getCheckpoint`, `recallMemory`, `inspectEvidence`,
`materializeSnapshot`, `renderCheckpoint`, and `measureCheckpointTokens` through Python's existing
snake-case convention. The personal checkpoint runtime composes this service against schema 31.
This issue intentionally does not add MCP tools, route existing MCP saves through it, or replace
live automatic context behavior.

## Evaluation and production threshold

The deterministic harness compares full history, compact rendering, and portable rendering. It
reports per-tokenizer counts and compression, continuation and required-group fidelity, protected
span fidelity, inversions, critical omissions, false-memory probes, constraint and decision
rationale retention, supersession, temporal accuracy, provenance coverage, determinism, drift, and
fresh-session criteria.

Run the focused suite and held-out corpus with:

```bash
uv run pytest -q tests/unit/test_semantic_checkpoints.py \
  tests/unit/test_sqlite_semantic_checkpoints.py \
  tests/evals/test_semantic_checkpoint_evaluation.py
```

The checked-in held-out corpus has 12 cases and local deterministic tokenizer adapters. It is useful
for regression detection, but it is deliberately below the production threshold of at least 50
held-out cases plus successful fresh-session continuation on two model families. External Codex and
Claude evaluation is optional and was not run by this slice. Therefore the compact format is not
production-validated yet even when the local suite passes.

## Persistence, deletion, and recovery

Migration `0031_semantic_checkpoints.sql` is additive, transactional, and personal-SQLite-only.
It adds the atom ledger, atom-to-source-event links, checkpoint metadata, patch-operation audit, and
checkpoint atom references. Source-event links cascade on an authorized source deletion, and an
orphan trigger removes an atom after its last source event is erased; checkpoint references then
cascade. A payload-free compiled-event marker retains only the erased event ID and checkpoint ID so
the remaining archive is not recompiled after a deleted head event. The patch audit contains IDs
and operation kinds, not copied evidence or atom text.

The migration is forward-only. Create a verified personal-profile backup before upgrading; restore
that pre-schema-31 backup to reverse a committed migration. A failed migration transaction leaves
the previous schema and migration ledger unchanged and can be retried.

Known limitations: the evidence envelope intentionally stores no raw transcript bodies; the
deterministic compiler supports explicit typing but not general natural-language contradiction;
metadata revisions do not yet have a separate append-only version table; semantic persistence is
not wired to PostgreSQL/team mode; no new deletion/export API is exposed for semantic checkpoints;
and this rendering is not yet used by the live automatic hook.
