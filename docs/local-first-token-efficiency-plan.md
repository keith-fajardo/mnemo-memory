# Local-first token-efficiency implementation plan

## Objective and metric

Reduce paid coding-model tokens across a complete work-and-resume lifecycle while retaining the
evidence, constraints, decisions, and next actions needed for correct continuation. Count persistent
tool schemas, automatic attachments, checkpoint-generation output, retrieval, validation failures,
retries, and repair. Local work has zero provider-token charge but still records latency and compute.

Keep deterministic dbt lineage, source parsing, Git observation, hashing, secret controls,
authorization, retention, lifecycle transitions, and token-budget enforcement model-free. Use a
local model only when semantic interpretation or compression beats deterministic behavior under the
evaluation gates.

## Sequenced bounded issues

1. **Compact personal MCP profile.** Preserve the complete full profile, including its current and
   conditionally enabled tools, and add an opt-in personal profile exposing only reduced
   `get_context` and `save_checkpoint` contracts. Remove
   explicit scope identifiers, advanced selectors, raw evidence objects, lessons, and approved-event
   mutation from the compact schemas. Codex and Claude registrations must record the selected
   profile exactly, detect profile mismatches, and remain safely removable. Gate promotion on a
   measured schema reduction of at least 50% without changing durable checkpoint behavior.
2. **Bounded local task evidence.** With explicit project consent, record only a minimized event
   envelope needed for checkpointing: the stated objective, previous checkpoint, verified file/Git
   transitions, test status, explicit decisions, blockers, and evidence references. Never make raw
   transcript, prompt, command body, tool body, source body, or hidden reasoning capture implicit.
3. **Local semantic compiler.** Add a provider-neutral local-generation port and an explicitly
   installed adapter capable of constrained `SemanticCheckpointPatch` output. Literal and
   deterministic extraction runs first; local generation handles only unresolved semantic meaning.
   Model output is an untrusted proposal and cannot choose scope, evidence, authorization,
   retention, mutation, or token policy. Pin and digest-verify model assets, remain usable when the
   runtime is absent, and make no hosted-provider call. Consume the existing strict personal
   settings fields `optional_model_enabled`, `model_provider`, and `model_id`; do not hardcode a
   model ID. Resolve one immutable settings snapshot per operation, require an MCP restart after a
   settings replacement, and record provider, model, prompt version, latency, usage, and result
   status for each attempted call. Changing the configured model must require no persistence
   migration, must not rewrite earlier provenance, and must fail safely to the deterministic path.
4. **Live semantic checkpoint composition.** Route the personal checkpoint lifecycle through the
   existing semantic ledger and whole-atom adaptive renderer. Compile only events since the prior
   head, retain protected meanings and evidence associations, target 200 local tokens, and expand
   toward 600 only when mandatory state requires it. Preserve the existing checkpoint API during a
   compatibility period.
5. **Selective attachment and rollover.** Promote selective push/lazy pull only after shadow
   evaluation meets recall and miss-rate gates. Start fresh sessions with the compact handoff and
   critical constraints; retrieve structure and knowledge on demand. Provide an explicit
   checkpoint-and-rollover workflow because Mnemo cannot erase a provider's active context window.
6. **Production-like promotion evaluation.** Compare the complete profile, compact profile, native
   compaction, and local-semantic path using exact client-reported cached/uncached input, output,
   reasoning, tool-schema, retrieval, retry, and repair usage. Require zero critical constraint or
   authority loss, zero unsupported promoted facts, secret-policy success, acceptable warm/cold
   latency, and positive total lifecycle token savings before changing defaults.

## Promotion rules

- Implement one issue at a time and preserve the complete profile until compatibility evidence
  supports a default change.
- Prefer literal rules and deterministic observations before loading a local model.
- Treat every local-model result as a proposal against closed schemas and canonical evidence.
- Fall back safely when a model or model runtime is absent, slow, or invalid.
- Keep local generation-provider selection separate from embedding, route-classifier, and coding-
  client model configuration; each may evolve independently.
- Do not claim provider savings from character estimates; use client-reported usage in the final
  production-like comparison.
