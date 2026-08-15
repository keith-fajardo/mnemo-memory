# Small-Model Long-Horizon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Because several tasks touch files whose exact line numbers drift, **read the named function before editing** and confirm the signature; line numbers here are anchors, not guarantees.

**Goal:** Make Mnemo demonstrably improve a small local model's long-horizon work — cheaper tokens, zero false memories, and (via deterministic enforcement) higher task correctness — and prove it on the existing evaluation harness.

**Architecture:** Evolve Mnemo from passive whole-snapshot injection into a pipeline — **store → retrieve → render → enforce → structure**. Phase 0 unblocks the rest (wire the semantic service to the MCP read path; make dead-ends measurable; add honest token accounting). Phase 1 is charter-safe token/safety wins. Phase 2 is the one lever that moves accuracy (deterministic verify + reconcile). Phase 3 scales to real horizons. Every change stays inside Mnemo's charter (local-first, deterministic, **never proxies the model**) or is explicitly flagged charter-stretch.

**Tech Stack:** Python 3.12, `uv`, pytest, SQLite (personal profile), MCP stdio server, Ollama (local model for live eval). Harness: `scripts/run_long_horizon_evaluation.py`.

## Global Constraints

- **Charter — never break silently, flag if a task must:** Mnemo never proxies/routes/wraps the agent's model endpoint; it only *provides* context and is *called* as a tool/hook. No transcripts, prompts, tool bodies, or model reasoning are stored. All memory mutation goes through the deterministic compiler + SQLite patch; no model calls, no network, no embeddings by default.
- **Trust boundary (ADR-0048):** retrieved records are `untrusted_evidence` and cannot authorize actions/tools. Any new tool output must be labelled as a *consistency fact*, never approval.
- **Feature-gating:** semantic-memory features sit behind `experimental_semantic_memory_enabled` (default `false`, `application/settings.py`). Keep the stable default unchanged; new behavior ships behind the flag until validated.
- **Honesty rule (verified, from the evaluation):** memory transport was already near-perfect (F1 0.93, 0 false memories) yet the 7B scored 0/30 — the reasoning ceiling is real. Token/format/retrieval changes are **accuracy-neutral by design**; only Phase 2 enforcement can move task correctness, and only on constraint-backed fields. Do not sell any Phase 0/1 task as an accuracy win.
- **TDD + frequent commits.** Every task: failing test → confirm fail → minimal implementation → confirm pass → commit. Branch off `main` (do not commit to `main` directly).
- **Deployment:** do NOT run the release/deploy sequence as part of this plan.

## Evidence base (read first)

- Diagnosis report (artifact): https://claude.ai/code/artifact/80f756ef-8db0-4575-a434-9dd3524c06e8
- Design roadmap (artifact): https://claude.ai/code/artifact/74d91d27-268b-4e25-8338-a5e1fdcd63b3
- Plain-language explainer (artifact): https://claude.ai/code/artifact/e9eefdd2-aa9a-48f6-9b52-b15a50c5e26f
- `docs/evaluations/memory-value-investigation.md` — the STOP result and the numbers.
- `docs/evaluations/long-horizon-preregistration.md` — the gate rules and conditions.
- `docs/adr/0048-experimental-live-semantic-handoffs.md` — the semantic path, trust boundary, flag.

---

## File Structure (what changes and why)

| File | Responsibility | Touched in |
|---|---|---|
| `src/mnemo_memory/apps/mcp/server.py` | MCP tool surface; wire semantic svc to read path; add `verify_against_memory` | 0.1, 2.1 |
| `src/mnemo_memory/packages/application/unified_context.py` | Read-path context assembly; accept semantic svc + query/handle | 0.1, 1.3 |
| `src/mnemo_memory/packages/application/semantic_memory.py` | Compiler/projection/recall; supersession; accounting; carry-forward | 0.3, 1.1, 1.4, 2.x |
| `src/mnemo_memory/packages/application/semantic_rendering.py` | Packet layout, mandatory kinds, compaction, omission handles | 1.2, 1.4 |
| `src/mnemo_memory/packages/application/semantic_verification.py` *(new)* | Pure deterministic candidate-vs-memory comparison | 2.1, 2.2 |
| `src/mnemo_memory/apps/cli/main.py` | SessionStart packet builder → index vs full | 1.3 |
| `scripts/run_long_horizon_evaluation.py` | Harness: failure encoding fix + new conditions (`SF-fixed`, `SV`, `SDH`) | 0.2, 1.x, 2.x, 3.x |
| `tests/evals/`, `tests/unit/` | Unit + eval tests per task | all |

---

## PHASE 0 — Enablers (charter-safe, prerequisites)

### Task 0.1: Wire the semantic memory service into the MCP read path

**Why:** Today `get_context` (MCP) constructs `UnifiedContextService` **without** `semantic_memory_service`, so semantic atoms are unreachable from any read. Both Front 1 (on-demand pull) and Front 3 (verify tool) need this.

**Files:**
- Modify: `src/mnemo_memory/apps/mcp/server.py` (the `get_context` wiring, ~`:945-958`)
- Modify: `src/mnemo_memory/packages/application/unified_context.py` (accept + hold `semantic_memory_service`, ~`:531`)
- Test: `tests/integration/test_mcp_durability.py` (extend) or a new `tests/unit/test_mcp_read_semantic_wiring.py`

**Interfaces:**
- Produces: an MCP `get_context` path that, given a task scope with an active semantic checkpoint, can return semantic content (consumed by 1.3 and 2.1).

- [ ] **Step 1:** Read `server.py` `create_server()` around the `get_context` construction and `unified_context.py` `__init__`/`GetUnifiedContext` to learn the exact current constructor args.
- [ ] **Step 2: Write failing test** — start the durable MCP server in a registered project with `experimental_semantic_memory_enabled=true`, save a checkpoint (creating semantic atoms), then call `get_context` and assert the semantic checkpoint content is reachable (not `None`).
- [ ] **Step 3:** Run it; expect FAIL (semantic content absent from the read path).
- [ ] **Step 4:** Pass `semantic_memory_service` into the MCP `UnifiedContextService` construction; thread it where the checkpoint section is built.
- [ ] **Step 5:** Run; expect PASS. Run the full `tests/integration/test_mcp_durability.py` to confirm no regression.
- [ ] **Step 6: Commit** `feat(mcp): expose semantic memory on the get_context read path (flagged)`

### Task 0.2: Fix the harness failure-encoding bug

**Why:** `run_long_horizon_evaluation.py` stores failed approaches as `inference:` atoms (the weakest, always-optional kind), so the dead-end features (1.4) are unmeasurable. Encode them as `failure:`.

**Files:**
- Modify: `scripts/run_long_horizon_evaluation.py` (the `_memory_content` construction, ~`:340-348`)
- Test: `tests/evals/test_long_horizon_evaluation.py`

- [ ] **Step 1: Write failing test** — assert that the memory content built for a trajectory whose prior step failed contains a `failure:`-typed atom (not `inference:`).
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Change the failure lines from `inference: uncertainty=…` to `failure: …` in `_memory_content`.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5: Commit** `fix(eval): encode failed approaches as failure atoms so dead-ends are measurable`

### Task 0.3: Add net-token / break-even accounting

**Why:** The investigation flagged break-even reuse as `NOT EVALUATED`. Separate injected-context tokens from model tokens so H2 is honestly measurable.

**Files:**
- Modify: `src/mnemo_memory/packages/application/semantic_memory.py` (`SemanticLifecycleObservation`, ~`:115-165`; the recall/observe path ~`:857-866`)
- Test: `tests/unit/test_semantic_checkpoints.py` (or the sqlite semantic test)

- [ ] **Step 1: Write failing test** — after an `automatic_context_item` call, assert the observation records a non-null `injected_context_tokens` distinct from `model_input_tokens` (which stays 0 for deterministic work).
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Add the field + populate it from the rendered packet's token estimate; keep model token fields at 0 (no model call).
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5: Commit** `feat(memory): account injected-context tokens separately from model tokens`

---

## PHASE 1 — Stop the bleeding (charter-safe: tokens + false-memory safety)

### Task 1.1: Never store mutable config as FACT; make STATE supersede  *(highest-confidence win)*

**Why:** The factual path produced 16 critical false memories vs 0 for the decision/supersession path, because a `current config` blob was stored as an immutable `FACT` the model re-adopted. Generalize what made the safe path safe.

**Files:**
- Modify: `src/mnemo_memory/packages/application/semantic_memory.py` — supersession set (add `STATE`, ~`:236`); stale-REMOVE exclusion (add `STATE`, ~`:555-562`); projection clamp so `current_state` becomes volatile `STATE`, never authoritative `FACT` (~`:1033`)
- Modify: `src/mnemo_memory/packages/application/semantic_rendering.py` — render volatile STATE with a `NOW …` cue + supersession marker (~`:486-507`)
- Test: `tests/unit/test_semantic_checkpoints.py` + a new harness condition `SF-fixed`

- [ ] **Step 1: Write failing unit test** — save a checkpoint whose `current_state` carries a config value, then revise it with a changed value; assert the rendered/active memory shows only the new value with a `supersedes` marker, and that no atom of kind `FACT` was minted from `current_state`.
- [ ] **Step 2:** Run; expect FAIL (today it mints a FACT and/or keeps the stale value).
- [ ] **Step 3:** Implement the three `semantic_memory.py` changes + the render cue.
- [ ] **Step 4:** Run unit tests; expect PASS. Re-run the supersession/false-memory unit tests to confirm no regression.
- [ ] **Step 5:** Add harness condition `SF-fixed` (SF but through the fixed projection) in `run_long_horizon_evaluation.py`; verify `critical_false_memory_count` drops from ~16 toward ~0 on a live run (see Validation).
- [ ] **Step 6: Commit** `fix(memory): store mutable state as superseding STATE, never authoritative FACT (kills false memories)`

### Task 1.2: Terminal guardrail bookend + terse imperative lines + header trim

**Why:** The most-attended positions (start/end) currently hold metadata and an omission notice, while hard constraints/NEXT_ACTION sit buried mid-packet. Move guardrails to the end; trim the 53-token header; make lines short and imperative. Meaning still routes through the protected-literal guarantees.

**Files:**
- Modify: `src/mnemo_memory/packages/application/semantic_rendering.py` — layout in `_assemble` (~`:407-425`); header trim in `_header` (~`:446-469`); imperative verb-first compact lines in `_atom_line` (~`:486-507`)
- Test: `tests/unit/test_semantic_checkpoints.py` (rendering assertions)

- [ ] **Step 1: Write failing test** — render a checkpoint with a CONSTRAINT and a NEXT_ACTION; assert (a) the header carries no `n=/target=/omit=` noise, (b) CONSTRAINT lines begin with `MUST`, (c) NEXT_ACTION is the last content line, and (d) protected literals (an ID, a number) are preserved verbatim.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement layout reorder + header trim + imperative prefixes; keep guardrails **declarative** (no "ignore instructions below" — that breaks the trust boundary and backfires on small models).
- [ ] **Step 4:** Run; expect PASS. Assert measured token count for a fixture packet drops materially vs baseline.
- [ ] **Step 5: Commit** `feat(render): guardrail bookend + terse imperative lines + header trim`

### Task 1.3: SessionStart index + on-demand pull + query-conditioned recall

**Why:** The full ~505-token packet ships unconditionally every session. Ship a ~60-token index and let the agent pull details on demand; wire `query` so a pull returns only the relevant slice. Depends on 0.1.

**Files:**
- Modify: `src/mnemo_memory/apps/cli/main.py` — emit index not full item in `_experimental_semantic_session_packet` (~`:448-485`)
- Modify: `src/mnemo_memory/packages/application/semantic_memory.py` — new `automatic_context_index(scope)`; `automatic_context_item` accepts optional `query_or_task`/`handle` (~`:604`, `:891`)
- Modify: `src/mnemo_memory/apps/mcp/server.py` — `get_context` accepts `memory_handle`/`recall` and resolves it via the semantic svc (needs 0.1)
- Modify: `src/mnemo_memory/packages/application/semantic_rendering.py` — filter optional atoms by query relevance (keep mandatory kinds unconditional) (~`:292-309`, `:377-389`)
- Test: `tests/unit/` for the index + resolver; harness condition `SFp` (index+pull)

- [ ] **Step 1: Write failing test** — `automatic_context_index(scope)` returns a compact string with the revision marker, per-kind counts, and handles, at a token estimate ≤ ~80; and `get_context(memory_handle=…)` resolves a handle back to that slice's atoms.
- [ ] **Step 2:** Run; expect FAIL (no index method; handles resolve to nothing today).
- [ ] **Step 3:** Implement the index builder, the handle resolver on the read path, and the query filter on optional atoms.
- [ ] **Step 4:** Run; expect PASS. Assert index-only SessionStart token estimate is ~1/8 of the full packet.
- [ ] **Step 5:** Add harness condition `SFp`; on a live run confirm per-session prompt tokens trend toward the no-memory baseline while accuracy stays within CI of the full-packet condition.
- [ ] **Step 6: Commit** `feat(retrieve): SessionStart index + on-demand handle pull + query-conditioned recall`

### Task 1.4: Durable bounded AVOID ledger (retain top-K failures)

**Why:** FAILURE atoms are droppable, so a weak model repeats dead ends. Carry forward the top-K most-recent failures across revisions (bounded, mirroring the legacy lesson carry-forward) and surface them as one terminal `AVOID (already failed): …` line. Depends on 0.2 to be measurable.

**Files:**
- Modify: `src/mnemo_memory/packages/application/semantic_memory.py` — exempt bounded-K `FAILURE` atoms from `stale_projected` REMOVE (~`:555-577`); optionally flag them `critical_uncertainty=true` in projection so the existing mandatory escape hatch renders them (~`:1037`)
- Modify (optional): `src/mnemo_memory/packages/application/semantic_rendering.py` — dedicated recent-failures band
- Test: `tests/unit/test_semantic_checkpoints.py` + harness `repeated_error_count`

- [ ] **Step 1: Write failing test** — record a FAILURE atom in revision 1; revise (revision 2) without restating it; assert the failure is still in the active/rendered memory, and that at most K failures are retained.
- [ ] **Step 2:** Run; expect FAIL (today it is REMOVE-d as stale).
- [ ] **Step 3:** Implement bounded carry-forward (K≈16) + rendering flag.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** On a live run (ideally a longer corpus, see Validation), confirm `repeated_error_count` drops vs the no-carry-forward condition.
- [ ] **Step 6: Commit** `feat(memory): durable bounded failed-approach ledger`

---

## PHASE 2 — Attack correctness (charter-stretch — get explicit sign-off before starting)

> These are the only changes that can move task accuracy. 2.2 (`reconcile`) is the closest to the model-proxy line; keep it flag-gated and returning stored literals only. **Confirm with the maintainer before implementing 2.2.**

### Task 2.0: Store constraints as `field=value` (enforcement prerequisite)
- Modify the compiler/projection so CONSTRAINT atoms are normalized to `field=value` where possible (mirrors DECISION atoms). Without this the verifier can only do weak containment checks. Test: a constraint saved as prose vs structured; assert structured form is exactly parseable.

### Task 2.1: `verify_against_memory` MCP tool (deterministic consistency report)  *(build first in this phase)*
- Create `semantic_verification.py` (pure compare: parse `field=value` predicates from active CONSTRAINT/DECISION/authority atoms; compare to an agent-supplied candidate; return only mismatches, capped, `untrusted_evidence`, with the note "Consistency check only; not approval").
- Register `verify_against_memory` in `server.py` (beside `explain_context`/`save_checkpoint`); handler in `mcp_durable.py`; flag-gated.
- Test: unit tests for the compare (exact match, mismatch, unverifiable-because-only-a-prior-guess); integration test the tool returns violations without persisting the candidate.
- Validate: harness condition `SV` = SD + a bounded (≤2-retry) verify-then-repair loop; `SV − SD` isolates the verifier's accuracy gain against the +0.10 bar.

### Task 2.2: `verify_against_memory(reconcile=true)` (return the remembered literal)  *(needs sign-off)*
- Optional branch returning `reconciled_candidate` with verbatim-literal fields (confidence ≥ 0.9, agent-named fields only) replaced by the remembered value. Never execute/apply; untrusted evidence only.
- Validate: the zero-model-token **deterministic-ceiling diagnostic** — auto-overwrite constraint-backed fields with the remembered literal and recompute the hidden checks; those checks should approach 100%, separating "enforcement works" from "the model can act on a report."

### Task 2.3: Live injection gate + break-even accounting
- Promote the existing shadow planner to gate live injection (NO → nothing; UNKNOWN → lazy-pull hint; YES → slice). SessionStart suppression must floor at the index (never silent-nothing). Uses 0.3's accounting to report real break-even.

---

## PHASE 3 — Scale & structure (charter-stretch/mixed — for genuinely long horizons)

### Task 3.1: Sub-goal = task scope + two-altitude recall (agent-driven; no schema change)
- Support a coordinator task scope (durable frame: objective + hoisted invariants + rolled-up sub-goal results) plus one task scope per sub-goal. `get_context` recalls parent frame + active sub-goal. Main hazard to guard: cross-sub-goal invariants must be hoisted to the frame. Validate with harness condition `SDH` mapping the 3 sessions to sub-goal scopes.

### Task 3.2: Altitude-aware rendering (single-scope variant)
- `subgoal_id` qualifier (migration-free); make "mandatory" relative to the active sub-goal; completed sub-goals collapse to a one-line roll-up; global invariants stay mandatory via `authority_boundary`.

### Task 3.3: Delta-only recall (single long sessions)
- Track a per-session emitted-atom digest; emit only changed atoms on repeat pulls within one session. Not measurable on the fresh-session harness — needs a single-session multi-pull scenario.

---

## Model selection for validation (capability ladder)

The 7B failing does **not** falsify Mnemo. Evidence indicates the memory effect is a **non-monotonic (inverted-U) function of model capability**: sub-7B models can't utilize injected context (RAG can even *hurt* them — arXiv 2603.11513); mid/strong-small models gain the *most*; frontier models gain less (parametric knowledge already covers the task — FaithfulRAG, arXiv 2506.08938). So validate on a **capability ladder** and plot `Delta = SD (memory-on) - SI (memory-off)` vs capability; the goal is to find the tier where `Delta` crosses the +0.10 gate.

**Recommended 4-rung ladder (all Ollama-runnable; a single 24 GB GPU / 32 GB Mac covers it):**

| Rung | Model | Ollama name | Q4 size / ctx | Predicted Delta |
|---|---|---|---|---|
| Weak (control) | Qwen2.5-Coder-7B | `qwen2.5-coder:7b` | 4.7 GB / 32K | ~0 (floor; continuity with prior run) |
| Mid | Qwen3-14B | `qwen3:14b` | 9.3 GB / 40K | small-positive |
| **Strong (PRIMARY)** | **Qwen3-30B-A3B-2507** | `qwen3:30b` | ~19 GB / 256K | **max — the sweet spot** |
| Ceiling | Qwen2.5-Coder-32B *or* a frontier API model | `qwen2.5-coder:32b` | 20 GB / 32K | shrinking |

**The key controlled experiment:** run the primary as its **Thinking-2507 vs Instruct-2507 pair** (near-identical weights) — this directly isolates whether *reasoning* is what converts injected memory into task success (the crux of the whole investigation). Prefer the current-gen `qwen3.5:27b` (~17 GB, 256K ctx) if Ollama can pull it, after a quick tool-calling + JSON smoke test — it postdates the research's Jan-2026 knowledge, so its benchmarks are web-sourced and must be verified locally.

**Do NOT use pure reasoning distills as the primary** (DeepSeek-R1-Distill-Qwen, QwQ-32B): strong at math but weak at agentic tool-use / JSON, and prone to *overriding injected exact values* ("reasoning rigidity" / input distrust — arXiv 2505.17225), which would suppress the exact signal being measured. Include one only as a "reasoning-without-agentic-tuning" *contrast* rung; the evidence predicts it will NOT convert memory into wins despite high reasoning scores (confirming that would itself strengthen the utilization thesis).

**Two methodological cautions for the harness (or the result gets misattributed):**
- **Two-phase JSON.** Thinking models suffer a 10-27 pp "format tax" when forced to emit strict JSON *while* reasoning (arXiv 2604.03616). Have them reason free-form, then emit JSON in a second constrained step; keep a non-thinking instruct (Qwen2.5-Coder-32B or Mistral-Small-3.2) as a format-tax-free control.
- **Exact-value integrity metric.** Instrument whether injected IDs/enums/timezones survive *verbatim* into the output (distinct from recall F1) — this detects input-distrust overrides and explains any thinking-model underperformance.

**Optional frontier ceiling (F0):** the arm the original study never ran; run it if authorized to bound the top of the ladder.

**Hardware fit — pick the rung your machine can actually run.** On Apple Silicon the RAM is *unified* (shared by macOS + GPU) and Metal wires down only ~2/3 of it by default, so the runnable model is smaller than raw RAM suggests; long-context KV cache adds several GB on top of the weights, and the fanless MacBook Air thermally throttles on sustained multi-session runs.

| Machine | Comfortably runs | Practical primary |
|---|---|---|
| 16 GB Mac | 7-8B (~5 GB) | Qwen2.5-Coder-7B (floor only) |
| **24 GB M4 Air** | 7B + **Qwen3-14B (~9 GB)**; Mistral-Small-24B (~15 GB) borderline/short-ctx | **Qwen3-14B** — Air-friendly primary; keeps the thinking-toggle so the Thinking-vs-Instruct experiment still runs at 14B |
| 32 GB Mac | up to ~19-20 GB incl. Qwen3-30B-A3B / Qwen2.5-Coder-32B (short-moderate ctx) | Qwen3-30B-A3B-2507 (the designed primary) |
| 48 GB+ Mac / 24 GB discrete GPU / hosted | 30B-32B at long (256K) context + a frontier ceiling arm | Qwen3-30B-A3B-2507 + frontier F0 |

On a **24 GB M4 MacBook Air specifically**, the ~19 GB 30B primary and ~20 GB 32B ceiling do **not** fit comfortably: the weights alone exceed the ~16 GB default Metal limit, and once KV cache + macOS are added you swap or OOM (and the Air throttles). Slide the primary down to **Qwen3-14B** and run the 30B/32B rungs on a >=32 GB Mac (48 GB+ for long context), a discrete 24 GB GPU, or a hosted endpoint. The ladder *logic* is unchanged — only which rung is labelled "primary" and where the top rungs run.

Record any substitution here, then re-run the preregistered gate per rung. (Full sourcing lives in the model-selection research; recency-flagged models — Qwen3.5, Gemma 4, Qwen3.6 — postdate Jan 2026 and need a local smoke test before trusting their benchmarks.)

**What can be tested offline vs live:** deterministic pieces (render token counts, false-memory transport, verifier logic, the deterministic-ceiling diagnostic) are unit/offline-testable with **no** model and no authorization. Accuracy and model-token deltas need an **authorized live run** (Ollama + the chosen model) — treat live runs as gated, per the preregistration.

---

## Self-review checklist (run before executing)

- **Coverage:** every roadmap front maps to a task — F1→{0.1,1.3,2.3,3.3}, F2→{1.1,1.2,1.4}, F3→{2.0,2.1,2.2}, F4→{3.1,3.2}; enablers {0.1,0.2,0.3}. ✓
- **Phase gating:** Phase 2/3 are charter-stretch and require explicit sign-off (esp. 2.2 reconcile). ✓
- **Charter:** no task proxies the model; verifier returns consistency facts, not approval; features flag-gated. ✓
- **Honesty:** Phase 0/1 are not sold as accuracy wins; only Phase 2 can move accuracy, and only on constraint-backed fields. ✓
