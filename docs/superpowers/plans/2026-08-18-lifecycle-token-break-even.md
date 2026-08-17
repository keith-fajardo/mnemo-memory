# Lifecycle-Aware Token Break-Even Implementation Plan

> **Planning status:** reviewed design only. Do not implement until the maintainer explicitly
> approves this new issue. Start implementation on a new branch from updated `main`, after the
> Phase 2 verifier correction is available there. Use `superpowers:executing-plans` (or
> `superpowers:subagent-driven-development`) task-by-task when that skill is available.

**Goal:** Make Mnemo behave like a sparse long-term-memory layer—no repeated durable context while
the current client session already has it, a small index at a real session/compaction boundary, and
one bounded detail slice only when needed—and demonstrate at least 30% cumulative downstream-model
token savings against replaying usable growing history without losing required facts, provenance,
or task quality.

**Architecture:** Keep the existing experimental `none` / `lazy_pull` / bounded-push router. Add
content-free, session-local delivery identities so the hook suppresses only an exact context slice
that the same client session already received. Separately replace the misleading stateless token
comparison with a lifecycle driver that exercises the real hook sequence and compares Mnemo against
full usable history. Model prompts and outputs remain transient; result artifacts contain hashes,
counts, deterministic grades, and provenance only.

**Scope boundary:** This plan fixes repeated delivery and token-value measurement. It does not
change semantic mutation, verifier/reconcile behavior, task decomposition, model routing, the MCP
trust boundary, or the stable flag-off experience.

## Evidence and diagnosis

Read before implementation:

- `docs/evaluations/memory-value-investigation.md`
- `docs/evaluations/long-horizon-preregistration.md`
- `docs/evaluations/viability-evaluation.md`
- `docs/adr/0048-experimental-live-semantic-handoffs.md`
- `docs/superpowers/plans/2026-08-15-small-model-long-horizon.md`
- `evaluation-results/long-horizon-v1/final-20260816-qwen3-14b-instruct-phase2-001/analysis.json`

The correction starts from four verified observations:

1. Mnemo's deterministic compiler, SQLite patch, routing, and verification perform zero model
   calls. Only context delivered to the downstream agent and any agent repair retry consume model
   tokens.
2. The experimental prompt gate already maps self-contained work to no attachment, uncertainty to
   a fixed roughly 30-token pull hint, and a positive need to one bounded slice. SessionStart already
   substitutes a semantic index capped at 80 tokens for the full semantic checkpoint.
3. The hook does not currently retain a content-free identity for a delivered slice. The same
   unchanged slice can therefore be attached again on later `UserPromptSubmit` events in the same
   session.
4. The three-session telehealth harness cannot prove token savings. Its baseline calls are
   stateless and do not replay growing history, while memory conditions add context on top. In the
   immutable Qwen3-14B result, SD averaged 3,272 prompt tokens per trajectory versus SI's 1,878;
   SFp used 95,765 total model tokens versus S0's 68,462 across 30 trajectories. Those numbers are
   a valid failure for that tested comparison, not a lifecycle break-even measurement.

Do not "fix" the result by subtracting memory tokens after the fact or by relabeling deterministic
CPU/tokenizer equivalents as model-token savings. The comparison itself must change.

## Required behavior

```text
active client session
    self-contained prompt --------------------------> attach 0 tokens
    uncertain memory need (first occurrence) -------> attach one <=40-token pull hint
    explicit durable-memory need --------------------> attach one bounded selected slice
    exact unchanged slice already delivered --------> attach 0 duplicate tokens

new session or post-compaction SessionStart
    reset session-local delivery ledger
    attach the bounded handoff/index once
    allow a later detail slice because index != detail slice

observed PreCompact (whether or not the task is dirty)
    clear only delivered-context identities before the client trims context
    preserve dirty/checkpoint/reminder lifecycle state
    never rely solely on a later SessionStart to make redelivery safe

checkpoint/snapshot changes
    provenance identity changes
    allow the changed slice once
```

The delivery ledger contains only bounded provenance-derived identities and lifecycle booleans. It
must never contain prompts, context text, checkpoint bodies, source bodies, tool bodies/results,
model output, or reasoning.

## Non-negotiable constraints

- Mnemo never proxies, wraps, replaces, or re-runs the agent's configured model endpoint.
- No transcript, prompt, model response, tool body/result, or reasoning is persisted by Mnemo or
  the evaluation artifacts.
- Memory mutation remains deterministic compiler + SQLite patch; no model call or embedding is
  added.
- All new product behavior remains behind `experimental_semantic_memory_enabled`, default `false`.
- Retrieved memory remains `untrusted_evidence`; delivery or deduplication never authorizes an
  action.
- Scope filtering happens before retrieval. A delivery identity includes a version, client,
  one-way exact-scope digest, client session, item ID, and source digest. It cannot suppress a
  different project, task, client session, checkpoint revision, renderer version, or selected
  slice even if a client reuses an opaque session ID.
- Existing immutable evaluation directories are never edited or regenerated.
- No new dependency, migration, deploy, release, or Phase 3 decomposition is part of this issue.
- If a product change is not required to make a failing acceptance workflow pass, do not add it.

## Preregistered acceptance gates

### Product behavior gate

- Self-contained same-session prompts inject exactly 0 context tokens.
- The lazy-pull hint is at most 40 estimated tokens and is delivered at most once in a sequential
  client session until an observed SessionStart or PreCompact boundary.
- A selected long-term slice remains within the existing route and shared ceilings.
- An exact unchanged selected slice is delivered at most once in a sequential client session.
  Concurrent hook races and bounded-ledger eviction must fail toward harmless redelivery, never
  toward suppressing an attachment that was not emitted.
- A changed provenance digest, different selected slice, new client session, SessionStart, or
  PreCompact boundary is not suppressed.
- SessionStart semantic index remains at most 80 tokens.
- Corrupt or unavailable delivery-ledger state fails open and cannot block Codex or Claude Code.
- A PreCompact event clears delivery identities even when the task is clean and even when the
  client cannot accept additional context on that hook. Other session lifecycle fields survive.
- State and telemetry contain no sensitive payload, prompt fragment, memory content, or source
  content.

### Token-value gate

Primary comparison: lifecycle-routed Mnemo (`MR`) versus full usable growing history (`FH`). A
no-memory stateless condition (`NM`) is a quality floor, not the savings baseline. A deterministic
rolling summary (`RS`) is a labelled secondary baseline, not provider-native compaction.

- At the long horizon, cumulative downstream-model token savings
  `1 - MR_total_model_tokens / FH_total_model_tokens` are at least 30%.
- The earliest observed cumulative break-even is reported by session/reuse; no extrapolated reuse
  is presented as observed.
- Required-knowledge retention and protected-literal fidelity are each 1.0 for the deterministic
  token probe.
- Each scored reuse prompt omits its required prior-session facts from current input, current
  implementation state, allowed-value hints, and hidden metadata. Deterministic availability must
  be 1.0 in FH and MR and 0.0 in NM; otherwise the run is `INVALID` for the token-value claim.
- Critical false-memory count and cross-scope disclosure count are both 0.
- If task-generating model calls are authorized, MR hidden-test accuracy is no worse than FH by
  more than 0.02. Otherwise task-quality impact is `NOT EVALUATED`, never inferred from token probes.
- Mnemo model input/output tokens remain exactly 0; local deterministic CPU and tokenizer-equivalent
  work are reported separately from downstream-agent model tokens.
- Actual provider token counts are required for a final `PASS`. Without an authorized live token
  probe, the maximum verdict is `PROVISIONAL`; without either offline or live evidence it is
  `NOT EVALUATED`.

### Statistical and artifact gate

- Scenario family is the independence unit; repeated horizons/reuses are within-family measures.
- With only six scenario-family clusters, paired bootstrap intervals are descriptive sensitivity
  summaries, not reliable population inference. Do not report significance or generalize beyond
  the six preregistered families; report `NOT ESTIMABLE` where a resample lacks support.
- Every run uses exclusive immutable creation, append-and-fsync session rows, resumable completed
  keys, artifact hashes, a reproducibility manifest, and explicit excluded/orphaned call accounting.
- Raw artifacts store only condition, scenario/horizon IDs, lifecycle boundary, content-free prompt
  hash, actual/estimated tokens, latency, route action, delivery/suppression counts, deterministic
  grades, and environment/model provenance.

## File map

| File | Responsibility |
|---|---|
| `src/mnemo_memory/packages/application/context_routing.py` | Pure exact-redelivery decision over bounded delivery identities |
| `src/mnemo_memory/connectors/automatic_memory/hook.py` | Session-local delivered-identity state and boundary reset |
| `src/mnemo_memory/apps/cli/main.py` | Derive delivery identities from existing item IDs and provenance source digests; compose the real hook |
| `tests/unit/test_automatic_memory.py` | Lifecycle, privacy, scope, compaction, and fail-open regressions |
| `tests/unit/test_context_route_telemetry.py` | Final delivered/suppressed token accounting |
| `tests/fixtures/evals/lifecycle-token-break-even-v1.json` | Original lifecycle schedule over existing scenario families |
| `scripts/run_lifecycle_token_break_even.py` | Offline driver, optional loopback token probe, append-only artifacts, and report |
| `tests/evals/test_lifecycle_token_break_even.py` | Pairing, prompt privacy, accounting, break-even, and verdict contracts |
| `docs/evaluations/lifecycle-token-break-even-preregistration.md` | Frozen conditions, estimands, gates, exclusions, and claim boundary |
| `docs/implementation-status.md` | Issue progress and verified terminal evidence only after implementation starts |

## Task 0: Freeze the lifecycle/token claim before code

**Why:** The existing accuracy harness answers a different question. Thresholds and baselines must
be fixed before observing a new result.

**Files:**

- Create `docs/evaluations/lifecycle-token-break-even-preregistration.md`
- Create `tests/fixtures/evals/lifecycle-token-break-even-v1.json`
- Create `tests/evals/test_lifecycle_token_break_even.py`

- [ ] **Step 1:** Write the fixture-contract test first. Require original provenance, six existing
  scenario-family identities, frozen 1/10/30-session horizons, at most four prompt events inside
  any one client session, explicit client-session boundaries, repeated identical memory needs,
  changed-memory needs, and compaction.
- [ ] **Step 2:** Run it and confirm RED because the fixture and preregistration do not exist.
- [ ] **Step 3:** Add the smallest fixture and preregistration satisfying the contract. Reuse the
  existing Mnemo-owned viability scenario facts by identifier instead of copying or inventing a
  second truth set.
- [ ] **Step 4:** Freeze `FH`, `RS`, `NM`, and `MR`, the formulas above, the 30% savings gate,
  fidelity/false-memory gates, statistical unit, live authorization boundary, and artifact schema.
  For every scored reuse, put the required fact only in an earlier session. Assert that it is absent
  from the reuse prompt, current implementation state, enums/hints, filenames, and grader metadata.
  Freeze deterministic availability as FH=1.0, MR=1.0, NM=0.0; invalidate rather than score a
  scenario whose current input leaks the fact.
- [ ] **Step 5:** Confirm the test passes and review the documents for leakage, post-hoc thresholds,
  and accidental customer/market claims.
- [ ] **Step 6: Commit** `test(eval): preregister lifecycle token break-even`

## Task 1: Suppress exact same-session redelivery

**Why:** The router already makes sparse decisions, but the hook cannot recognize that an exact
selected slice was already delivered to the same live client session.

**Minimal interface:**

- Extend `PromptContextAttachment` with bounded `delivery_keys` that contain no content.
- Build each key from a fixed version, client name, one-way exact-scope digest, opaque client
  session ID, canonical context `item_id`, and existing provenance `source_digest`; use an equally
  scoped fixed versioned key for the lazy-pull hint.
- Persist at most a small bounded set of keys in `_SessionStateStore`. Clear only those keys at
  every SessionStart and every PreCompact, including clean-task and silent-Codex PreCompact paths.
  Preserve the dirty, saved, marker, Git, telemetry, and reminder fields.
- Union keys only after a nonempty client attachment has been constructed for return. A failed
  write, corrupt state, concurrent race, or bounded-key eviction must over-deliver later rather
  than suppress uncertain delivery; exactly-once delivery is not claimed under concurrent hooks.
- Suppress only when the candidate attachment has a nonempty key set and every key was already
  delivered. Do not perform fuzzy or semantic deduplication.

**Files:**

- Modify `src/mnemo_memory/packages/application/context_routing.py`
- Modify `src/mnemo_memory/connectors/automatic_memory/hook.py`
- Modify `src/mnemo_memory/apps/cli/main.py`
- Modify `tests/unit/test_automatic_memory.py`
- Modify `tests/unit/test_context_route_telemetry.py`

- [ ] **Step 1: Write failing lifecycle tests** — start an experimental session with a persisted
  checkpoint, deliver the SessionStart index, request one detailed prior-memory slice twice, and
  assert the second identical slice emits 0 context tokens. Confirm RED.
- [ ] **Step 2: Add failing edge tests** — require one lazy-pull hint per sequential session;
  changed checkpoint revision and a different query-conditioned source digest must deliver; a new
  SessionStart must reset; every PreCompact must reset before the dirty-only branch, including clean
  Claude Code and silent Codex paths; project/task/client/session/renderer scope cannot
  cross-suppress even if two projects reuse one opaque session ID.
- [ ] **Step 3: Add failing privacy/reliability tests** — inspect the local session-state and
  telemetry files and assert no prompt, rendered memory, evidence payload, source text, scope path,
  or model output is present. Corrupt state, state-write failure, bounded eviction, and a simulated
  concurrent stale read must fail toward duplicate delivery, not missing context.
- [ ] **Step 4:** Implement delivery-key derivation from canonical item/provenance metadata, the pure
  exact-redelivery decision, and bounded session-state persistence. Do not add a second retrieval
  cache or content index.
- [ ] **Step 5:** Record final emitted token counts. A suppressed route remains a retrieval `HIT`
  but finalizes delivery as `duplicate_render=true`, zero rendered characters/bytes, and zero
  estimated emitted tokens. Use the existing telemetry path after proving these semantics in a
  failing test; do not add a database migration.
- [ ] **Step 6:** Run the focused automatic-memory, routing, and telemetry suites. Confirm all pass.
- [ ] **Step 7: Commit** `fix(memory): suppress exact same-session context redelivery`

## Task 2: Build the lifecycle-faithful offline driver

**Why:** Token savings require Mnemo to replace growing usable history, not to be appended to a
stateless prompt.

**Condition construction:**

- `FH`: transiently assemble all usable prior public task facts and bounded prior result summaries,
  including every protected fact scored in MR, without access to hidden grader metadata.
- `RS`: transiently assemble the existing transparent deterministic rolling-summary baseline.
- `NM`: current-session input only, with no durable history or state field that carries the required
  prior fact; grade it as the memory-necessity control and quality floor.
- `MR`: current-session input plus outputs from the real `SessionStart` and `UserPromptSubmit` hook
  sequence. Do not call private renderer logic independently of the hook composition.

Prompts and result summaries may exist only in process memory for the next model request. They must
not be written to raw rows, failures, logs, exceptions, or manifests.

**Files:**

- Create `scripts/run_lifecycle_token_break_even.py`
- Extend `tests/evals/test_lifecycle_token_break_even.py`
- Optionally extract one small hook-composition helper in `src/mnemo_memory/apps/cli/main.py` so the
  CLI command and evaluation invoke the same adapters; do not create general evaluation framework.

- [ ] **Step 1: Write a failing pairing test** — for every scenario/horizon, require the four
  conditions to see identical current public facts, task instructions, lifecycle schedule, and
  starting state. Only history transport may differ. Separately prove that FH and MR expose the same
  scored prior-fact set, while NM exposes none of it; reject any scenario with current-input or
  carried-config leakage before token accounting.
- [ ] **Step 2: Write failing lifecycle tests** — MR must invoke actual SessionStart only at the
  fixture boundary, actual UserPromptSubmit for prompt routes, no attachment on self-contained
  active turns, one hint on uncertainty, one detail slice per provenance identity, and a reset after
  every PreCompact even if the task is clean or the client emits no hook context.
- [ ] **Step 3: Write failing privacy tests** — insert unique prompt/response/reasoning markers into
  transient inputs and assert none occur anywhere in the run directory or error output. Only
  SHA-256 prompt identities and bounded counts may persist.
- [ ] **Step 4:** Implement offline prompt assembly using the repository's conservative tokenizer.
  Store downstream prompt/output estimates separately from Mnemo's zero model tokens and from local
  deterministic CPU/tokenizer-equivalent work. The offline schedule may cover every session from 1
  through 30; actual-provider calibration is sampled only at preregistered horizons.
- [ ] **Step 5:** Implement exclusive run creation, append-and-fsync raw rows, resume-by-completed
  key, sanitized failures, immutable configuration, artifact hashes, and reproducibility manifest
  by following the existing long-horizon writer patterns.
- [ ] **Step 6:** Confirm all evaluation tests pass twice with byte-identical aggregate output for
  the same fixture/seed, except explicitly excluded wall-clock fields.
- [ ] **Step 7: Commit** `feat(eval): measure token break-even across real memory lifecycle`

## Task 3: Add honest break-even and quality analysis

**Why:** A small checkpoint is not itself a saving. The report must show cumulative downstream
model cost and the session at which MR actually becomes cheaper than FH.

**Files:**

- Modify `scripts/run_lifecycle_token_break_even.py`
- Extend `tests/evals/test_lifecycle_token_break_even.py`
- Modify `scripts/build_memory_viability_report.py` only if a shared pure formula can be reused
  without changing frozen result artifacts; otherwise keep the new analysis isolated.

- [ ] **Step 1: Write failing formula tests** for cumulative prompt/output model tokens,
  model-input-only savings, paired lifecycle TES, observed break-even session, duplicate tokens
  avoided, local deterministic cost, orphaned calls, and `None` when a denominator/success count is
  unavailable.
- [ ] **Step 2: Write failing verdict tests** — `PASS` requires actual provider counts, >=30% MR vs
  FH savings, FH/MR deterministic required-fact availability=1.0, NM availability=0.0, zero critical
  false memories/disclosure, and the applicable quality gate; a leaked or non-necessary memory probe
  is `INVALID`; tokenizer-only evidence yields at most `PROVISIONAL`; absent authorization is
  `NOT EVALUATED`.
- [ ] **Step 3:** Reuse the existing deterministic grader for required knowledge, protected spans,
  evidence attribution, supersession, and false-memory probes. Keep condition IDs hidden from the
  grader.
- [ ] **Step 4:** Implement paired scenario-family aggregation and bootstrap intervals. Do not
  treat sessions/reuses as independent samples. Label six-cluster intervals descriptive and make no
  significance or population-generalization claim.
- [ ] **Step 5:** Generate JSON plus a concise Markdown report that explicitly distinguishes:
  delivered context tokens, downstream actual/estimated model tokens, Mnemo model tokens (zero),
  deterministic local work, quality proxy, model-generated quality, and not-evaluated dimensions.
- [ ] **Step 6:** Run focused report tests and inspect a dry artifact for prompt/body leakage.
- [ ] **Step 7: Commit** `feat(eval): report observed lifecycle token break-even honestly`

## Task 4: Validate offline, then calibrate with the local model

**Offline validation requires no model authorization:**

- [ ] Run `uv run pytest -q tests/evals/test_lifecycle_token_break_even.py`.
- [ ] Run the focused automatic-memory, routing, telemetry, context-packet, semantic-memory, and
  privacy suites.
- [ ] Run one immutable offline dry evaluation. Confirm the MR/FH prompt construction, exact
  delivery suppression, long-horizon fidelity, false-memory result, and estimated break-even.
- [ ] If the offline 30%/fidelity gate fails, stop. Diagnose instead of changing the threshold.

**Live token calibration requires explicit authorization:**

- [ ] Preflight the loopback Ollama endpoint, exact `qwen3:14b` model identity/digest, free disk,
  configured context window, and one minimal non-thinking request. If the model is absent, stop and
  report it; model download is a separate authorized action.
- [ ] Use only `qwen3:14b` non-thinking mode on the 24 GB M4 Air. Do not run the 30B/32B rungs on
  this machine.
- [ ] First run one scenario/horizon smoke. Persist no prompt or response. Verify Ollama
  `prompt_eval_count`, `eval_count`, latency, model identity, call count, and append-only resume.
- [ ] Run a bounded primary token probe at only the frozen 1/10/30-session horizons: six families x
  FH/MR x three horizons = 36 calls, plus at most one preflight smoke call. Use temperature 0 and at
  most eight generated tokens per call. Stop at a 90-minute wall-clock cap. This calibrates actual
  prompt tokens; it does not establish task quality or actual per-session crossover between sampled
  horizons.
- [ ] Analyze under a new immutable run ID. Never rewrite the Qwen3 Phase 2 result.
- [ ] Run a task-generating quality comparison only under separately approved call/time bounds. If
  it is not run, report task-quality impact as `NOT EVALUATED`.
- [ ] Stop after the preregistered verdict. A reviewer may explain a failure but may not revise the
  gate post hoc.

## Task 5: Repository gate and issue handoff

- [ ] Update `docs/implementation-status.md` with the exact implemented scope, commit IDs,
  focused/full verification, immutable run IDs, verdict, limitations, and confirmation that no
  dependency/schema/default/release changed.
- [ ] Run `npm run architecture:check` after import or component changes.
- [ ] Run the complete `npm run check` gate.
- [ ] Review the final diff for issue scope, originality, dependencies, secrets, payload leakage,
  trust-boundary language, and preservation of unrelated user files.
- [ ] Save a Mnemo checkpoint with the exact next action and project-relative evidence files.
- [ ] Commit the final documentation/result references. Do not deploy or release.
- [ ] Stop for maintainer approval before any Phase 3 decomposition, new connector, router model,
  embedding, or broader product claim.

## Expected outcome and honest failure modes

This plan is likely to reduce waste when the same durable slice is repeatedly selected inside one
session and to create a fair test of the user's intended economics. It cannot guarantee a positive
30% result before execution.

- If duplicate suppression passes but MR still costs more than FH, the remaining issue is packet
  size or retrieval frequency; inspect section-level tokens before shrinking budgets.
- If MR is cheaper but loses required facts, improve selection/summary fidelity before claiming
  value. Do not relax the quality gate.
- If offline savings do not match actual Ollama counts, keep the offline result labelled estimated
  and use provider counts for the final verdict.
- If full history is cheaper at short horizons but MR wins later, report the observed crossover;
  do not market Mnemo as a universal short-session token saver.
- If the client evicts context without emitting SessionStart or PreCompact, the ledger cannot know
  that the model forgot an earlier attachment. The tested claim is limited to clients and bounded
  within-session schedules that expose those lifecycle boundaries; do not generalize past them.
- If bounded ledger eviction or concurrent hook processes cause redelivery, count the duplicate
  tokens. This is a safe efficiency loss, not permission to suppress an uncertain attachment.
- If Qwen3 remains accuracy-saturated, the token result can still stand as a resource result, but
  it does not prove Mnemo improves reasoning or task correctness.

## Independent review

Claude Opus 4.8 reviewed this plan read-only with only `Read`, `Grep`, and `Glob`; it did not edit
files, run commands, or call the network. Its verdict was `REVISE`, meaning the design is feasible
after correction, with two must-fix objections:

1. Resetting only on SessionStart could leave the ledger stale after Claude Code PreCompact. The
   plan now requires unconditional PreCompact delivery-key reset before the dirty-only branch,
   preserves unrelated lifecycle state, and tests both clients' clean/dirty/silent paths.
2. MR-versus-FH savings would be confounded if the current NM input already contained the required
   facts. The plan now requires prior-only protected facts, FH/MR parity, NM=0 deterministic
   availability, leakage rejection, and an `INVALID` verdict for a non-necessary-memory probe.

The review also requested honest six-cluster statistical limits, zero-emission duplicate telemetry,
an Ollama/model preflight, bounded-key overdelivery semantics, and an explicit limitation for
unobserved context eviction. Those changes are incorporated above. A second verification pass,
limited to the read-only `Read` tool, returned `PASS` with no remaining must-fix and confirmed that
both objections are
mapped to explicit behavior, gates, and tests. The review establishes that the revised work is
implementable and that its proposed test addresses the intended token question; it does not
establish that the unimplemented fix passes, that savings reach 30%, that Qwen is installed, or that
task quality is preserved. Only the preregistered offline and authorized live gates can do that.
