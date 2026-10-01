# Jev hook wiring (phase 2, part 1) — design spec

- **Date:** 2026-10-02
- **Status:** draft for maintainer review
- **Builds on:**
  - `2026-09-30-jev-typed-decision-routing-design.md` (the "parent spec")
  - ADR 0049
  - the 2026-10-02 phase-2 hardening (merge `7a7c51a`)

---

## 1. Goal, in plain words

Phase 1 showed that Jev can answer Mnemo's per-prompt questions well on made-up data. This sub-project connects those answers to the real prompt hook. Later, switching Jev on becomes mostly a settings change, backed by evidence, not a new build.

It wires four decisions into the hook:
- **memory need**
- the per-note **filler check**
- the **task-size hint**
- **skill pick**

Each decision has three modes:
- `off`: today's behaviour.
- `shadow`: Jev answers, and the answers are recorded next to the rules' answers. Nothing changes.
- `live`: Jev's answers change what gets attached.

**Real data still never leaves the machine.** The data route stays `synthetic_only`, which blocks every real-prompt request before any network call. Live mode is **locked** by settings until a zero-data-retention (ZDR) route exists. Adding that route is a later, explicit maintainer decision.

## 2. Decisions made (maintainer answers, 2026-10-02)

| Question | Answer |
|---|---|
| Wait for ZDR terms before building? | **No.** Skip the ZDR confirmation for engineering only. Real data stays blocked. Letting it through is a separate explicit decision |
| First phase-2 sub-project | **Hook wiring.** Redaction, Haiku extraction and the ZDR route adapter come later |
| End state | **Shadow + live, live locked** |
| Decisions included | **Memory need, filler check, task-size hint, skill pick** |
| Worst-case added wait per prompt | **≤ 0.8 s.** All Jev requests are sent at once under one cap |
| Approach | **One decision step in the CLI layer**, plus a **synthetic replay** through the real hook |

---

## 3. Per-prompt flow and where the code lives

```
prompt arrives (one short-lived hook process, as today)
 │
 ├─ feature off ──────────────────────► today's path, output byte-identical
 │
 1. Rules decide first (unchanged): route rules + ADR 0046 planner + learned phrases
 │   └─ a hard rule fired (§4.1) ──────► today's result for memory need; no front-door
 │                                       request (skill pick may still ask, §4.4)
 │
 2. Local preparation, nothing leaves the machine:
 │   - list current skills (only when skill pick is not off)
 │   - pre-fetch candidate notes for the route the rules picked, only on push routes
 │     (at most 8 knowledge sections + 8 events, exactly as today's retrieval)
 │
 3. ONE asyncio run, everything sent at once, 0.8 s total cap (`ask_each`):
 │   - front-door request: memory need (5-way), complexity, tool need, skill pick
 │   - filler requests: one per eligible pre-fetched note (≤ 16)
 │   A request not answered within the cap counts as "no answer".
 │
 4. Combine (pure functions, no I/O) into plain values:
 │   - shadow → recorded only; output unchanged
 │   - live   → applied as in §4
 │
 5. Render and write telemetry, as today
```

**Code placement**
- **The new module `apps/cli/typed_decision_hook.py`** runs steps 2–4. Step 4 returns a frozen `TypedPromptDecisions` record of plain values:
  - the two needs, as `AutomaticContextNeed`
  - the item IDs to drop
  - the chosen skill name, or none
  - whether to show the hint
  - the telemetry fields
- **`packages/application` never imports `model_gateway`**, so the layer rules hold. Mapping Jev answers to `AutomaticContextNeed` happens in `apps/cli`.
- **`plan_automatic_context_needs` gains one optional keyword**, `typed_needs: tuple[AutomaticContextNeed, AutomaticContextNeed] | None`. Its closed reason set gains `typed_decision`.
- **The guard is still built only by `apps/cli/typed_decision_composition.py`.** It is the only importer of the Jev connector.
  - Its per-request deadline becomes **0.8 s**, replacing 0.6 s, and `ask_each` runs with `total_deadline_seconds = FILLER_CHECK_BUDGET_SECONDS` (0.8 s).
  - This closes open item 2 in the phase-2 list.
- **`_automatic_prompt_context_for_hook` gains an optional `replay_overrides: TypedHookOverrides | None` parameter.** It defaults to `None`, which means runtime composition plus modes read from settings.
  - A `TypedHookOverrides` carries a `guard_factory`, which builds a `SYNTHETIC_FIXTURE`-source guard, and the four modes.
  - Only the synthetic replay (§8.2) passes one. The settings locks (§6) therefore never need an exception: the replay's modes do not come from settings.
  - The `automatic-memory-hook` command never passes overrides, so the real hook always uses `TypedDecisionSource.RUNTIME` and the locked settings.
- **No worker-thread cap is added here.** A hook process sends at most 17 requests and then exits, so abandoned daemon threads cannot accumulate. The cap stays an open item for the long-lived MCP server (extraction sub-project).

---

## 4. Decision rules

**General rule:** if Jev does not answer, answers late, or is below a question's confidence bar, that decision falls back to **today's rules result**. Jev never overrides a hard rule, the secret scan or the data-route gate.

### 4.1 Memory need

**Question.** The 5-way `MEMORY_NEED` choice: `past_sessions`, `project_docs`, `code_structure`, `code_and_history`, `nothing`. An answer is accepted only at confidence ≥ 0.6 (`CHOICE_CONFIDENCE_BAR`).

**Hard rules** (Jev is not asked, and today's planner result stands):
- the route rules, as the planner runs them (without skill candidates), return `NONE`, `DIRECT_LOOKUP` or `LOCAL_DIAGNOSTICS`
- a learned phrase matched
- the current-session cue matched

Otherwise, Jev's accepted answer **replaces the planner's keyword cues**:

| Jev label | long-term need | structure need | retrieval route in live mode |
|---|---|---|---|
| `past_sessions` | yes | no | `PRIOR_MEMORY` |
| `project_docs` | yes | no | `KNOWLEDGE` |
| `code_structure` | no | yes | `STRUCTURE` |
| `code_and_history` | yes | yes | the rules' route if it is `PRIOR_MEMORY`, `KNOWLEDGE` or `STRUCTURE`; otherwise `KNOWLEDGE`. No single route fetches both today |
| `nothing` | no | no | none |
| below 0.6, or no answer | **the rules' own needs** | **the rules' own needs** | the rules' route |

Through the existing ADR 0046 gate, the needs then become an action: both no means `none`, any unknown means `lazy_pull`, and any yes means push.

**Live** (requires `experimental_semantic_memory_enabled`, §6):
- the planner receives `typed_needs`, and the plan's reason is `typed_decision`
- a confident `nothing` attaches nothing
- if Jev's route differs from the rules' route, the pre-fetched notes are discarded and the Jev route's notes are fetched locally after the answer, **unfiltered** (keep is the safe side), within the route's existing token ceiling, with no second Jev call

**Shadow:** record the label, a confidence bucket, the needs, the resulting action, and whether it agrees with the rules' action.

### 4.2 Filler check

**When it runs:** only on push actions, and only on **automatic** attachment. An explicit `get_context` call is never filtered.

**Eligible notes:** the pre-fetched knowledge sections and approved events. These are never sent and always kept:
- the active task checkpoint
- pinned and mandatory items
- conflict notices
- the selected procedure
- any item whose sensitivity is not `normal`
- any text the secret scan flags (the guard also blocks it)

**Question:** `NOTE_SUBSTANCE` (task information vs filler), one request per note, text = `note_text(...)` (300 characters).

**Drop rule:** drop only when p(filler) ≥ 0.7 (`should_drop_note`). An unanswered, late or unsure answer keeps the note. Space freed by a drop is **not** refilled.

**Live:** dropped notes leave the packet and stay reachable (§5).

**Shadow:** count the notes checked, the notes that would be dropped, and the notes left unanswered.

### 4.3 Task-size hint

**Committee:** `tier_committee()`, with phase-1 weights and threshold:
- complexity (Jev, weight 0.6)
- tool need (Jev, weight 0.4)
- local risk terms (veto)

Complexity and tool need ride in the front-door request, so they add no calls.

**The hint is shown only when** the tier is light, the tool need is `read_heavy`, and there is no veto (`hint_eligible`).
- The text is `HINT_TEXT` (about 20 tokens): *"Mnemo: light, reading-heavy task; a Haiku subagent could do the reading."*
- It is appended to whatever the hook attaches, or attached on its own, and merged with the lazy-pull hint when both fire.
- Hint lines stay within ADR 0046's 40-token limit.

**Fallback:** an unavailable committee resolves to heavy, which means no hint. That is today's behaviour.

### 4.4 Skill pick

**Question:** a new `SKILL_PICK` choice axis.
- **Labels:** the current skill names (at most 32) plus `none`.
- **More than 32 skills:** the question is skipped and keyword matching stays in charge.
- **Text:** the bounded prompt, as for the front door.
- **When asked:** whenever skill pick is not `off`. This includes prompts where a hard rule fixed memory need. In that case the front-door request carries only the skill question.

**Live:**
- an accepted answer (≥ 0.6) replaces keyword matching
- a skill label makes that skill the single candidate
- `none` means no candidates, and the route continues as if no skill matched (any retrieval this needs runs after the answer, unfiltered)
- an unsure answer falls back to keyword matching

**When keyword candidates make today's route `SKILL_DISCOVERY`:**
- that route skips retrieval, so no notes are pre-fetched and no filler checks run
- in live skill mode, Jev's answer decides whether skill discovery holds
- if Jev answers `none`, the memory-need result decides retrieval, which runs after the answer, unfiltered

**Shadow:** record only `agreed`, `differs`, `unsure`, `skipped` or `not_asked`, compared with the keyword result. **No skill names are recorded.**

**Before it can go live:** skill pick must pass its own new gates (§8.3).

---

## 5. Dropped notes stay reachable

1. **New omission reason.** `OmissionReason.LOW_RELEVANCE = "low_relevance"`.
2. **One summary omission per prompt**, not one per note:
   ```
   MNEMO_OMISSION {"item_id":"low-relevance","reason":"low_relevance",
     "detail":"2 notes judged filler; fetch with get_context item_ids",
     "item_ids":["approved-episodic:<id>","knowledge:<doc>:revision:<rev>:section:3"]}
   ```
   - The IDs are the ones the agent already sees on attached items. There is no new identifier scheme and no new store.
   - **If the summary line does not fit the automatic attachment budget, the drops are cancelled and the notes are kept.** A note is never dropped without a way back to it.
3. **`get_context` gains an optional `item_ids` parameter** (1–16 IDs). It returns exactly those items.
   - It runs the same scope, authorization and sensitivity checks as any fetch, and never involves Jev.
   - An item that no longer exists, has changed or is not permitted comes back as an omission with the usual reason (`superseded`, `expired`, `unauthorized_scope`, `prohibited_sensitivity`).
   - Without `item_ids`, the tool behaves exactly as today.
   - The lookup lives in `packages/application` (no Jev involvement), so `apps/mcp` needs no new import.
4. **Shadow mode adds no omission line,** because nothing is dropped.

---

## 6. Settings, locks, budget and CLI

**Settings** in `packages/application/settings.py`:
- **Existing:**
  - `experimental_typed_decisions_enabled` (default false)
  - `typed_decision_data_route` (only `synthetic_only`)
  - `typed_decision_model_id` (`jev-1.13.0`)
  - `typed_decision_modes`
- **New decision kinds:** `tier_hint` and `skill`. The hook uses four kinds:
  - `front_door` (memory need)
  - `relevance` (filler check)
  - `tier_hint`
  - `skill`
- **New:** `typed_decision_daily_input_tokens`, default `10_000_000`, an integer in `[1, 1_000_000_000]`.

**Locks**, checked on load and on every change, refused with a plain message:
1. **Live needs a real route.** No mode may be `live` while the route is `synthetic_only`.
2. **Memory need needs the live gate.** `front_door: live` requires `experimental_semantic_memory_enabled`.
3. **Any mode needs the master switch.** Any mode other than `off` requires `experimental_typed_decisions_enabled` (already enforced).

Shadow **is** allowed on `synthetic_only`. It runs the new code on real prompts, and every request stops at the data-route gate as `data_route_blocked`. That proves the wiring is harmless in real use.

**Budget.** Today's runtime budget, `DenyAllModelBudget`, denies everything. It is replaced by a **local daily counter** for `ModelTaskType.TYPED_DECISION`:
- a JSON file in the data directory under a file lock
- a reset at each UTC day
- the same reservation per request as today: 1,000 input tokens, 1 output token, cost 0

When a reservation would exceed the daily limit, the guard returns `budget_denied` and the rules decide. At $0.042 per million tokens, the default caps spend at about $0.42 a day, roughly 600 prompts at the worst-case reservation (17 requests each).

**CLI.** A `typed-decisions` sub-command, following the `memory-router` pattern:
- **`mnemo-memory typed-decisions status`** shows:
  - the master switch, the route and each mode
  - which locks are active
  - whether `TYPESAFE_API_KEY` is present (yes/no only, never the value)
  - today's reserved tokens and the limit
- **`mnemo-memory typed-decisions set <front_door|relevance|tier_hint|skill> <off|shadow|live>`** changes one mode, applying the locks.

---

## 7. Failure handling and telemetry

**Failure handling.** Every failure leads to today's behaviour for that prompt.
- The guard never raises.
- **The new step is wrapped as a whole** (skill listing, pre-fetch, combine). Any exception makes the hook use the rules' result, recorded as `typed_step_error`. This matters because the hook's existing outer catch returns **no context at all**.
- Jev is asked only where §4 says. Notes are pre-fetched only on routes the rules would retrieve anyway, so there is no extra local work on `none` or `lazy_pull` prompts.
- Requests still running at the 0.8 s cap are cancelled and recorded as `timeout`. Their daemon workers never block process exit.

**Telemetry: one record per prompt, never any content.**
- `AutomaticRouteEvent` gains a new versioned field group, `typed_v1`, which `from_dict` accepts as a new key-set tier. Older records still load.
- It is written only when diagnostics are `SUMMARY` or `TRACE`, with today's retention: 256 events, 1–90 days.

| Field | Values |
|---|---|
| `typed_front_door_mode`, `typed_relevance_mode`, `typed_tier_hint_mode`, `typed_skill_mode` | `off` / `shadow` / `live` |
| `typed_front_door_outcome` | `answered`, `not_asked`, `typed_step_error`, or a closed `unavailable_reason` |
| `typed_step_ms` | whole step wall time, integer ms |
| `typed_model_version` | the pinned-version pattern, or null |
| `typed_memory_label` | the 5 labels, `unsure`, or null |
| `typed_memory_confidence_bucket` | `<0.5`, `0.5-0.6`, `0.6-0.8`, `0.8-0.9`, `>=0.9`, or null |
| `typed_action` | an ADR 0046 action, or null |
| `typed_agrees_with_rules` | bool or null |
| `typed_notes_checked`, `typed_notes_dropped`, `typed_notes_unanswered` | integers 0–16 (`dropped` means "would drop" in shadow) |
| `typed_tier` | `light` / `heavy` / null |
| `typed_hint` | `shown`, `would_show`, `none` |
| `typed_skill` | `agreed`, `differs`, `unsure`, `skipped`, `not_asked` |

- **A runtime `TypedDecisionRecorder`** collects the guard's per-request outcomes in memory and folds them into this one record. Today only the evaluation script has a recorder.
- Cancelled requests are recorded with the budget as their duration, which is the known phase-2 telemetry gap.
- Every field is a closed value, a bounded integer or a bool. A test asserts that no prompt text, note text or skill name can enter the record.

---

## 8. Testing, synthetic replay and exit gates

### 8.1 Tests with a fake Jev (no network; part of `npm run check`)

- **Off is unchanged.** With the master switch off, hook output and the MCP surface are **byte-identical** to today.
- **Shadow is unchanged.** Shadow-mode output is **byte-identical** to off-mode output, prompt for prompt, over the 60 routing prompts plus the holdout set.
  - This runs through replay overrides with a fake Jev that answers every question, so shadow really receives answers. It is not trivially blocked.
- **Nothing sends while blocked.** Under `synthetic_only`, the real hook path makes **zero** transport calls in every mode, and settings refuse `live`.
- **The entry point is pinned to runtime.** `automatic-memory-hook` builds its guard with `TypedDecisionSource.RUNTIME` and never passes `replay_overrides`.
- **Everything else:**
  - the pure combine functions for each decision (§4), including the hard-rule list and the route table
  - the three locks
  - the daily budget counter (limit, UTC reset, file lock, corrupt-file fallback to deny)
  - `get_context item_ids`: scope, sensitivity, changed and missing items
  - the summary omission and the "keep if it doesn't fit" rule
  - the whole-step fallback on an exception
  - the telemetry content-free check and the `from_dict` key-set tiers
  - the `typed-decisions` CLI
- **Architecture:** `npm run architecture:check` passes, and the Jev-connector importer list is unchanged.

### 8.2 Synthetic replay

- **Files:** `scripts/typed_decision_replay.py` (library) and `scripts/run_typed_decision_replay.py` (CLI), next to the phase-1 harness.
- **What it does:**
  1. Builds a temporary data directory seeded with **synthetic** knowledge notes, approved events and skills, taken from fixtures whose provenance declares them synthetic. The harness already rejects any other fixture.
  2. Runs each synthetic prompt through `_automatic_prompt_context_for_hook` in **a fresh process per prompt**, so cold start-up counts as in the real hook, with `replay_overrides` that build a `SYNTHETIC_FIXTURE`-source guard.
  3. Runs every prompt twice, rules-only and typed-live, and compares them.
- **Live Jev runs need the maintainer's go-ahead each time,** as in phase 1.
- **The report holds:** per-decision gate results, attached tokens for rules-only and typed-live, notes dropped (relevant vs noise), and the step and hook latency (p50, p95, maximum).

### 8.3 Exit gates (per decision, scored on a live replay)

| Decision | Gate |
|---|---|
| All | At most 5% of prompts hit the 0.8 s cap (any request cut off). Added hook wall time is reported (p50, p95, maximum), and the maximum is ≤ 0.85 s (cap plus overhead) |
| Memory need | The phase-1 routing gates through the hook: accuracy ≥ 0.80, prior-memory recall ≥ 0.90, structure recall ≥ 0.80, `nothing` precision = 1.0, on the dev set **and** the holdout |
| Filler check | Zero relevant notes dropped, ≥ 90% of noise dropped (dev and holdout) |
| Task-size hint | Heavy recall ≥ 0.95 on the phase-1 tier set |
| Skill pick | On a new synthetic skill set (`typed-decision-skills-v1.json`: 12 skills, 40 prompts, at least 10 needing no skill) **and** a separate holdout written after the wording is tuned (`typed-decision-skills-holdout-v1.json`: 24 prompts, at least 6 needing no skill): correct skill on ≥ 85% of skill prompts, `none` on ≥ 95% of no-skill prompts, and better than keyword matching on both |
| Tokens | Total attached tokens on the replay are lower for typed-live than rules-only (reported per decision) |

**Gates are per decision.** A decision that fails stays at most in shadow, and the others are unaffected. Passing these gates finishes this sub-project. **Real-traffic** promotion still follows the parent spec §9: a ZDR route, then shadow on real traffic, then live.

---

## 9. Out of scope

- A ZDR data route, or any change letting `runtime` text leave the machine.
- Name and identifier redaction.
- Haiku extraction and the MCP `extract_episodic` path.
- A worker-thread cap for long-lived processes.
- Real-traffic shadow analysis, which needs a ZDR route.
- Topical relevance (parent spec §12), and a lower per-label confidence bar.
- A short per-prompt handle for dropped notes. It was considered and rejected as not worth a new shared store.

## 10. Paperwork

- **ADR 0049** (still proposed): add the hook wiring, the four kinds, the locks, the local daily budget and the replay gates to Consequences.
- **Threat model:** an entry for hook-path typed-decision traffic. It is blocked under `synthetic_only`, and the replay sends only synthetic fixtures.
- **`docs/implementation-status.md`:** a dated paragraph.
- **User guide:** the `typed-decisions` command and the `get_context item_ids` parameter.
- **Memory `jev-phase2-prerequisites`:** close open item 2 (filler guard deadline), and keep the thread cap open for MCP.
