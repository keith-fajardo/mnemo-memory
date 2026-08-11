# ADR 0046: Measure selective push and lazy pull before changing live memory attachment

## Status

Accepted for the explicitly approved shadow-policy issue.

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

Mnemo keeps the live ADR 0045 route unchanged and evaluates a separate content-free shadow action:

- `none` for deterministic no-memory and narrowly recognized current-output follow-ups;
- `push_structure`, `push_long_term`, or `push_both` when deterministic rules or explicitly taught
  project phrases establish one or both needs;
- `lazy_pull` when a need remains unresolved.

`lazy_pull` represents this fixed 29-token estimate: “Mnemo did not attach durable context. If prior
project decisions or structure could change the answer, call get_context.” The hint is not attached
live in this issue. It reuses the existing authorized, scoped `get_context` tool and does not add a
second tool description to every model context. A proposed push remains within the existing shared
1,300-token ceiling; the hint estimate must remain at most 40 tokens.

Automatic trace hooks use only deterministic routing and explicitly authorized learned phrases.
They do not load or invoke Potion. The pinned Potion adapter remains an explicit local evaluation
asset, outside the hook critical path. No result automatically teaches, promotes, forgets, or
changes a route.

New trace events add only a closed shadow action, proposed token count, and shadow duration. JSON
and table views compute combined routing time while retaining the old semantic/Potion fields for
historical records. A later `get_context` call is observed only as the closed `context_recall` tool
category. Prompts, paths, payloads, queries, results, embeddings, scores, and reasoning remain
prohibited.

## Consequences

Trace mode no longer incurs Potion model latency. The shadow record can compare current live token
cost with a concrete selective-push/lazy-pull counterfactual without withholding context. Historical
events remain readable, and users can still label the observed route as helpful, noise, or missing.

The shadow proposal does not prove answer quality or causation. Narrow current-output phrases can be
wrong after compaction, and an eventual agent pull can add latency or miss relevant memory. Live
promotion therefore requires a later approved issue with a user-authorized, secret-safe,
production-like evaluation set measuring answer quality, miss rate, tokens, and latency. A model,
cache, Redis service, or automatic learning loop is not justified unless that simpler policy fails
the agreed gates.

## Evidence

- [Adaptive-RAG](https://aclanthology.org/2024.naacl-long.389/) evaluates learned retrieval-complexity routing.
- [Corrective Retrieval Augmented Generation](https://arxiv.org/abs/2401.15884) evaluates retrieval correction and fallback decisions.
- [Self-RAG](https://arxiv.org/abs/2310.11511) evaluates on-demand retrieval and reflection signals.
- [Large Language Models Can Be Easily Distracted by Irrelevant Context](https://proceedings.mlr.press/v202/shi23a.html) measures degradation from irrelevant input.

These sources motivate evaluation dimensions; they do not validate Mnemo's thresholds or production
quality. Mnemo's promotion decision must come from its own controlled evidence.
