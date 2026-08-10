# ADR 0045: Route ambiguous memory intent with a compact local classifier

## Status

Accepted for the explicitly approved compact-router issue.

## Context

Mnemo's automatic prompt route uses authoritative literal rules for recognizable task-memory,
knowledge, structure, skill, diagnostic, and direct-inspection intent. All other non-trivial
prompts currently perform the same bounded knowledge probe. That conservative fallback protects
recall but can do unnecessary local retrieval, while paraphrases such as “pick up where we left
off” may not select prior task memory.

Making a hosted or generative model answer first would add prompt tokens, latency, provider
disclosure, and a second answer authority. Automatically activating the optional FastEmbed runtime
would also violate ADR 0008's explicit model-weight-download boundary. Mnemo must remain a context
platform and never proxy or replace the coding agent's configured model.

## Decision

Mnemo adds an original, standard-library text classifier trained from a small checked-in set of
Mnemo-owned synthetic routing examples. It uses presence-only normalized word unigrams and adjacent
bigrams and returns only one closed route proposal: no attachment, prior task memory, project
knowledge, or the existing source-structure projection. Repeated features contribute once. It
never generates text, computes source relationships, or answers the task.

Every nonblank prompt is reduced transiently to at most 512 characters before routing or retrieval.
Short prompts remain unchanged. Longer prompts use one deterministic head/tail view so instructions
around pasted code or logs remain visible without handing an unbounded prompt to skill discovery,
lexical retrieval, or the optional local embedding adapter. The full prompt and the bounded view are
never persisted or included in telemetry. The mandatory deterministic high-confidence secret gate
disables semantic query embedding when the bounded view or selected query matches a prohibited
credential pattern; lexical retrieval remains local and transient.

Authoritative literal rules execute first. The classifier runs only at the former general-memory
fallback. Prior-memory routing uses a recall-oriented threshold. No-memory routing requires both a
high score and a separating margin. Knowledge predictions and every uncertain result retain the
existing bounded knowledge probe. A structural proposal selects only the authorization-first,
rebuildable source projection with its existing structural budget; dbt artifacts remain
authoritative, and the classifier never creates lineage or source facts. Classifier output cannot
choose scope, authorization, visibility, evidence, budget, retention, or mutation behavior.

The model is reconstructed deterministically in process, performs no file or network I/O, and does
not persist prompts, features, scores, or predictions. Existing content-free route reason and
outcome telemetry may identify the closed classifier branch but never includes classifier input.

## Consequences

Self-contained prompts can skip local memory retrieval with zero attachment tokens, previously
unrecognized continuation paraphrases can retrieve prior task memory, and ambiguous source-graph
questions can select structural context. Repeated padding is idempotent, while conflicting or weak
evidence continues to probe rather than risking a false no-memory decision. The classifier adds a
small fixed amount of local CPU work and checked-in synthetic examples but no dependency, model
weights, provider call, model token use, or storage.

Held-out tests measure prior-memory and structural recall, conservative no-memory precision,
long-prompt boundary selection, padding invariance, and fallback behavior.
This is not a calibrated universal probability model; changing examples or thresholds requires the
same routing evaluation and privacy review. A later learned embedding or provider-backed router
would be a separate issue with explicit consent, budget, dependency, and threat-model approval.
