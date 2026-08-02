# Evaluation baseline specification

## Purpose

This specification defines how Mnemo will later prove that a fresh session resumes useful work
with less context than transcript replay while retaining quality, scope isolation, and provenance.
Issue 2 defines the protocol and starter fixtures only; it does not implement retrieval, dbt
parsing, checkpoints, MCP, or model calls.

## Controlled comparison

Every evaluation run executes the same golden workflow under three conditions.

### No-memory baseline

A fresh agent receives the task request, repository/dbt fixture available to normal tools, and
mandatory project instructions. It receives no prior transcript, checkpoint, Mnemo memory, or
Mnemo-derived hint. This measures normal fresh-session recovery.

### Full-transcript baseline

A fresh agent receives the same inputs plus the complete permitted fixture transcript up to the
handoff point. The transcript is not summarized. Prohibited secrets and content outside the
fixture scope are never introduced merely to make this baseline “complete.”

### Mnemo-context baseline

A fresh agent receives the same inputs plus only the canonical, budgeted Mnemo context packet for
the explicit scope. The packet may contain an approved checkpoint and dbt structural facts when
the corresponding runtime issues exist. It exposes provenance, conflicts, omissions, staleness,
and token estimate. It does not contain the full transcript or change the agent's model endpoint.

## Experimental controls

- Use the same model/provider/version, reasoning setting, system instructions, tools, repository
  commit, dbt artifacts, task prompt, environment, and time budget across all conditions.
- Randomize condition order and repeat nondeterministic runs at least three times.
- Record fixture and source digests, model parameters, prompt versions, cache state, and failures.
- Use deterministic software for scope, dbt lineage, token accounting where a stable tokenizer is
  available, and result comparison. Models do not grade authorization or lineage correctness.
- Evaluators are blind to condition where practical. Human rubric changes are versioned before a
  run, never after seeing results.
- A failed or degraded Mnemo run remains in the result set and must not block ordinary agent work.

## Measurements

### Tokens and cost

Record available transcript tokens, retrieved candidate tokens, selected context tokens by
category, final prompt input, cached input, model output, reasoning tokens when exposed, embedding
tokens, and monetary cost. Report median and distribution, not only totals.

Primary efficiency comparisons:

```text
input_token_reduction = 1 - mnemo_input_tokens / full_transcript_input_tokens
successful_resumption_efficiency = successful_resumed_tasks / total_retrieval_and_generation_cost
```

Never fill a packet solely because budget remains. The initial packet hard default is 5,700 tokens
with the section budgets in the implementation plan; an override above 8,000 is explicit.

### Latency and reliability

Record context assembly separately from agent completion, including p50, p95, maximum, timeout,
and cold/warm state. Track MCP availability, connector failure, fallback behavior, and whether
Mnemo failure prevented task completion. The initial deterministic local assembly objective is p95
below 750 ms on the documented personal-scale fixture.

### Quality and task resumption

Each workflow defines observable completion criteria. Score independently:

- `0` — failed, unsafe, out of scope, or materially incorrect;
- `1` — began the task but missed essential prior state or structural facts;
- `2` — partially correct with material omissions or avoidable rework;
- `3` — correct completion with minor inefficiency or nonmaterial omission; and
- `4` — correct, scoped, evidence-backed completion with no material rework.

Record binary task-resumption success, time to first correct action, repeated work, wrong-file or
wrong-node edits, test outcome, and human rubric score. Mnemo must materially improve success over
no memory without a material quality regression versus full transcript.

### Provenance and safety

Measure included-claim citation precision, expected-source recall, source-authority correctness,
staleness labeling, conflict visibility, omission correctness, and unsupported-claim count. Scope
leakage has a zero-tolerance target and includes leaked IDs, paths, metadata, counts, or payloads.
Record prompt-injection compliance, prohibited-secret appearances, and unauthorized mutation
attempts separately from answer quality.

## Golden workflow format

The versioned JSON fixture at `tests/fixtures/evals/golden-workflows.json` contains:

- a stable workflow ID, title, category, task/handoff description, fixture inputs, explicit scope,
  completion criteria, and five or more questions;
- for every question: stable ID, one of `episodic`, `knowledge`, `project`, or `procedural`, prompt,
  explicit scope, expected source IDs and authority classes, expected result, and prohibited
  inclusions; and
- synthetic identifiers and content only—never production repositories, credentials, personal
  data, or competitor artifacts.

The initial gate requires at least ten workflows and fifty questions across all four evaluated
memory categories. Every question has an expected source and scope. Fixture changes require review
because silently changing expected answers invalidates historical comparisons.

## Initial workflow coverage

Starter workflows cover explicit checkpoint resume, upstream lineage, downstream impact, test
failure diagnosis, stale dbt state, cross-project isolation, corrected decisions, applicable
procedures, deletion propagation, and connector degradation/model-endpoint invariance. They are
specifications for later tests, not claims that the runtime exists.

## Reporting and go/no-go

Store raw per-run measurements and a summarized comparison keyed by fixture version and code
revision. Do not average away security failures. The vertical slice proceeds only when Mnemo:

- improves resumption over no memory;
- uses materially fewer input tokens than full transcript on long-session fixtures;
- retains evidence required for correct decisions;
- explains every included memory and structural fact;
- has zero scope leakage and unacceptable secret exposure; and
- fails without blocking normal client operation.

If quality falls, improve retrieval before adding connectors. If tokens do not fall, tighten
selection and summaries. If scope isolation fails, stop team development until authorization is
redesigned.
