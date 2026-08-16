# ADR 0046: Selective push and lazy pull routing for automatic memory attachment

## Status

Accepted. Initially shadow-only; live promotion is limited to the separately approved experimental
semantic-memory Phase 2 gate described below.

## Context

ADR 0045 deliberately sends uncertain live prompts to a bounded project-knowledge probe to protect
recall. Content-free production diagnostics then showed that this branch accounted for most
estimated automatic-attachment tokens in the reviewed sample. Those estimates measure rendered
characters rather than provider billing, and correlated later tool reads do not establish whether
the attachment helped. The sample therefore demonstrates a cost concentration, not permission to
reduce live recall.

Retrieval research supports making retrieval conditional on task need and retrieval quality rather
than always adding context. It does not supply a universally calibrated threshold for Mnemo's local
distribution. Irrelevant context can also distract a downstream model. Mnemo needs production-like
counterfactual evidence before it changes what an agent receives.

Running Potion synchronously in trace mode added local cold-load and inference latency even though
its proposal could not change the live route. A cache would reduce repeated computation but would
not remove irrelevant attachment text from the downstream model context.

## Decision

Mnemo computes a separate content-free deterministic action:

- `none` for deterministic no-memory and narrowly recognized current-output follow-ups;
- `push_structure`, `push_long_term`, or `push_both` when deterministic rules or explicitly taught
  project phrases establish one or both needs;
- `lazy_pull` when a need remains unresolved.

`lazy_pull` represents this fixed 30-token estimate: “Mnemo did not attach durable context. If prior
project decisions or structure could change the answer, call get_context.” It reuses the existing
authorized, scoped `get_context` tool and does not add a second tool description to every model
context. A proposed push remains within the existing shared 1,300-token ceiling; the hint estimate
must remain at most 40 tokens.

The stable path remains unchanged when `experimental_semantic_memory_enabled=false`. When the flag
is true, a `UserPromptSubmit` hook promotes the deterministic action: `none` does not retrieve or
attach a slice, `lazy_pull` attaches only the fixed hint, and a push retrieves and renders one
existing route-selected slice within the action and route ceilings. The gate is a hook decision; it
does not proxy, invoke, wrap, or rerun the agent model. The SessionStart path is not suppressed by
this prompt gate and retains the compact semantic index as its minimum attachment whenever current
semantic evidence is eligible, so the experiment never replaces that index with silent nothing.

Automatic trace hooks use only deterministic routing and explicitly authorized learned phrases.
They do not load or invoke Potion. The pinned Potion adapter remains an explicit local evaluation
asset, outside the hook critical path. No result automatically teaches, promotes, forgets, or
changes a route.

New trace events add only a closed action, proposed token count, and routing duration. A promoted
event additionally records a boolean gate marker and the deterministically measured injected
context tokens. JSON diagnostics report those tokens separately from Mnemo model tokens, which are
zero because the gate is deterministic. Actual downstream agent token deltas and break-even reuse
remain explicitly unevaluated until an authorized live model comparison supplies them. A later
`get_context` call is observed only as the closed `context_recall` tool category. Prompts, paths,
payloads, queries, results, embeddings, scores, and reasoning remain prohibited.

## Consequences

Trace mode no longer incurs Potion model latency. Historical events remain readable, and users can
still label the observed route as helpful, noise, or missing. The experimental live gate avoids
retrieval work for deterministic `none` and `lazy_pull` outcomes and supplies measured injection
costs for a later authorized paired comparison.

The promoted proposal does not prove answer quality or causation. Narrow current-output phrases can
be wrong after compaction, and an eventual agent pull can add latency or miss relevant memory. Live
model-token savings, answer quality, miss rate, and break-even therefore require the separately
authorized, secret-safe evaluation ladder. A model, cache, Redis service, or automatic learning
loop is not justified by this deterministic gate.

## Evidence

- [Adaptive-RAG](https://aclanthology.org/2024.naacl-long.389/) evaluates learned retrieval-complexity routing.
- [Corrective Retrieval Augmented Generation](https://arxiv.org/abs/2401.15884) evaluates retrieval correction and fallback decisions.
- [Self-RAG](https://arxiv.org/abs/2310.11511) evaluates on-demand retrieval and reflection signals.
- [Large Language Models Can Be Easily Distracted by Irrelevant Context](https://proceedings.mlr.press/v202/shi23a.html) measures degradation from irrelevant input.

These sources motivate evaluation dimensions; they do not validate Mnemo's thresholds or production
quality. Any behavioral or economic claim must come from Mnemo's own controlled evidence.
