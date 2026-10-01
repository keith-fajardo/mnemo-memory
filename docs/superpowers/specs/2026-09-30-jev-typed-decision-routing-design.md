# Jev typed-decision routing — design spec

**Date:** 2026-09-30
**Status:** Draft for review
**Author:** Claude (paired with maintainer)
**Builds on:**
- The uncommitted MiniCPM cascade router (`packages/model_gateway/cascade_router.py` and `tests/unit/test_cascade_router.py`, staged from `stash@{0}` and `stash@{1}`). It passes 5/5 tests and `npm run architecture:check`.
- ADR 0043 (team model budgets), ADR 0045 (compact local router), ADR 0046 (selective push/lazy pull), ADR 0047 (sparse checkpoints) and ADR 0048 (no proxying of the agent's model endpoint).
- `docs/superpowers/specs/2026-08-22-live-episodic-extraction-mcp-takeover-design.md`, which covers extraction and host-agent handoff.
- Research: `reports/TypeSafe Jev data retention.md` and `research_notes/TypeSafe Jev data retention/`.

---

## 1. Goal, in plain words

Mnemo makes many small "pick one from a list" decisions:
- Does this prompt need old memory?
- Is this snippet relevant?
- Is this event worth remembering?

Today it makes them with keyword lists and a tiny word-counting classifier. **Jev** is a hosted model from TypeSafe that only makes this kind of decision. You give it text and a closed list of answers, and it returns one answer with a probability. It cannot write sentences, which makes it cheap: $0.042 per million input tokens, and output is free.

The goal is to use Jev as a **sorting desk** in front of Mnemo's work:
- **No model needed** means skip the work entirely. This saves the most tokens.
- **Light** means a cheap model (Haiku) does it.
- **Heavy** means the smartest model in the loop, the host agent, does it.

Jev also decides whether a prompt needs **long-term** memory (something Mnemo must fetch) or only **short-term** memory (what the agent already has: this conversation plus the session-start checkpoint). The aim is to attach fewer tokens, get faster answers on easy work, and remember better on the write path.

**Boundary (ADR 0048):** Mnemo may not route or proxy the host agent's own model calls. For the agent's turns, Jev can only **advise**, through a short hint. It **routes** only Mnemo's own model tasks.

---

## 2. Decisions already made (maintainer answers, 2026-09-29)

| Question | Answer |
|---|---|
| Whose tasks | **Both.** Mnemo's own tasks are routed (none, light or heavy). The host agent gets an advisory hint only. |
| What text may leave the machine | **Bounded, scrubbed text.** This is the 512-character prompt cap or a bounded event summary. The secret scan runs first, and the whole feature is opt-in. |
| How the tiers are called | **Haiku direct, smart via agent.** Light tasks go to Haiku through the Anthropic API with Mnemo's own key and budget. Heavy tasks use the existing host-agent handoff. Ollama becomes optional. |
| Approach | **One shared decision port, rolled out in phases** (§5–§6). |
| Data route | **Synthetic now, zero data retention (ZDR) later.** Jev sees only synthetic or public text until ZDR is in writing, because TypeSafe's standard terms allow open-ended retention (§4.1). |

---

## 3. Changes since the approved sections — PLEASE REVIEW

Design sections 1 to 5 were approved one at a time. Two things came afterwards: 11 real Jev latency calls, and the data-route choice above. Together they led to five changes. These are the only parts of this spec the maintainer has not yet seen and approved.

1. **One Jev call per prompt, with a 600 ms limit.**
   - This replaces the 300 ms front-door timeout (section 2) and the two-call 400 ms shared deadline (section 3).
   - Local retrieval runs first. Then a single Jev request asks the front-door questions and, once real data is allowed, the relevance questions too.
2. **A data-route gate in code, not just in the ADR.**
   - A new setting, `typed_decision_data_route`, defaults to `synthetic_only`.
   - At runtime, real prompts and stored snippets are refused *before any network call* unless a zero-retention route is configured (§5.2).
3. **Phase 1 is synthetic-only.**
   - Extraction stays on Ollama for now.
   - Haiku and shadow runs on real traffic move to phase 2, which starts only after ZDR is in writing.
4. **Latency is a promotion gate.** No per-prompt decision goes live until at most 5% of measured round trips exceed the 600 ms limit, on cold connections.
5. **The ADR records the vendor terms versions**:
   - Master Customer Agreement (MCA): 2026-09-23
   - Data Processing Agreement (DPA): 2026-04-24
   - Privacy Policy: 2025-11-19

   It also requires names and identifiers to be redacted before any real traffic.

**Details filled in while writing, also unreviewed:**
- The credential variable is `TYPESAFE_API_KEY`, the name already in use, instead of `MNEMO_TYPESAFE_API_KEY` (§5.2).
- `ClassifierAxis.system_prompt` becomes `instructions` plus an optional `criteria` mapping (§5.1).
- The committee's starting weights and threshold, and a skill confidence bar of 0.6 (§6.1).
- When Jev says a retrieval is needed that the rules didn't pre-run, Mnemo runs it afterwards and attaches it unfiltered, with no second Jev call (§6.1).
- The tier-set gate (heavy recall ≥ 0.95) and a latency sample size of at least 50 calls (§8).

---

## 4. Honest limitations (load-bearing)

### 4.1 Data retention

TypeSafe promises it will not train on customer data. That is **not** a promise to keep nothing.
- The binding MCA §4.1 lets TypeSafe store inputs and outputs. It also keeps a right, *in perpetuity*, to use customer data for telemetry, abuse monitoring and legal compliance. That clause was added on 2026-09-19.
- No official retention period exists.
- ZDR is available for enterprise accounts by request to sales. Vercel's AI Gateway also offers a per-request ZDR option with a negotiated TypeSafe clause. Neither clearly covers telemetry derived from the content.

That is why phase 1 is synthetic-only.

### 4.2 Latency

The table below shows measured samples from 2026-09-29: pinned `jev-1.13.0`, 4 questions per request, all HTTP 200.

| Connection | Calls | Total time | Connection setup | TypeSafe processing |
|---|---|---|---|---|
| Cold (new connection, as each hook process opens one) | 6 | 0.37–0.52 s, median about 0.45 s | 0.08–0.16 s | about 0.28–0.39 s |
| Warm (reused connection) | 4 | 0.27–0.45 s | 0 | 0.27–0.45 s |

- TypeSafe's processing time dominates. A long-lived warm-connection process would save only about 0.1 s, so it is **rejected**.
- Third-party claims of 95 ms p50 did not hold up.
- Eleven calls is a small sample. Phase 1 measures latency properly.

### 4.3 Probability is not correctness

A confident Jev answer can still be wrong. Every decision therefore has a **safe side**: the choice that costs a few more tokens rather than losing information. Answers below a question's bar resolve to that safe side.

### 4.4 Jev sees only bounded text

It judges from a 512-character prompt or a bounded summary, so it can misjudge how hard a task is. The committee (§5.1) reduces this risk with local rule axes and a veto.

### 4.5 A hint is advice

The host agent may ignore it. Hint value is measured (§8), not assumed.

---

## 5. Architecture

A **port** is a single doorway that every feature uses to ask a question. An **adapter** is whatever answers behind it: Jev, or today's rules. Swapping the adapter never changes the features.

```
decision site (front door, relevance, extraction, compaction, ...)
      │  ask(axes, bounded text)
      ▼
GuardedTypedDecisionClassifier   (implements PromptClassifier)
  data-route gate → secret scan → sensitivity check → size cap → budget → deadline → label check → telemetry
      │                                                 │
      ▼                                                 ▼  unavailable / below bar
  JevClassifier (connectors/typesafe)            RulesClassifier (today's logic)
```

### 5.1 The port: the cascade router, lightly revised

`PromptClassifier` (one axis in, one label and score out) is the single port, and `CascadeCommittee` decides light vs heavy. Because the code is uncommitted, these contract changes are cheap now:

1. **Rename the routes** from `local`/`frontier` to `light`/`heavy`. "No model needed" is a separate gate *before* the committee, not a third route.
2. **Add a batch method.** It becomes `classify_batch(axes, text) -> tuple[ClassifierResult, ...]` on the port, and `CascadeCommittee` prefers it. A Jev request answers many questions over one text, billed once. Adapters without batching fall back to the existing concurrent `gather`.
3. **Change the axis prompt field.** `ClassifierAxis.system_prompt` becomes `instructions`, and an optional `criteria` mapping (label to description) is added. This matches Jev's question format. Local-model adapters may still render both as a system prompt.
4. **Convert probabilities to log-probabilities.** Jev gives a probability p. The adapter sets `label_logprob = log(max(p, 1e-6))`, so p = 0 can never produce minus infinity.
5. **Axes can come from different sources.** In the same committee, cheap local rule axes are answered by `RulesClassifier` and judgement axes by Jev. If Jev is unavailable, the rule axes still produce a weaker, conservative decision.

Module docstrings are updated to drop "optional local-model cascade" wording.

### 5.2 The guard: `packages/model_gateway/typed_decisions.py`

`GuardedTypedDecisionClassifier` wraps an adapter. It **never raises** into a hook or tool call. Every check below either passes, or returns a closed `unavailable(reason)` that makes the call site use its rules result.

1. **Data-route gate.** Each caller states its source: `runtime` (hook or MCP) or `synthetic_fixture` (offline eval harness).
   - The route `synthetic_only` blocks every `runtime` request with `data_route_blocked`, before any other work and with no network call.
   - The harness accepts only fixtures whose provenance declares synthetic data.
2. **Secret scan.** `policy/content_safety.py` checks the text (`contains_high_confidence_secret`). Flagged text returns `secret_blocked`.
3. **Sensitivity.** Any item with sensitivity other than `normal` is never sent (`sensitivity_blocked`).
4. **Size cap.** Prompts use the existing 512-character bound. Snippets are a heading plus the first 300 characters.
5. **Budget.** A reservation is made under a new `ModelTaskType.TYPED_DECISION` (ADR 0043). A denial returns `budget_denied`.
6. **Deadline.** Each call gets a hard wall-clock deadline, 600 ms on the per-prompt path. There is no retry, because backoff would blow the deadline.
7. **Label check.** Any answer outside an axis's allowed labels is rejected as `schema_invalid`.
8. **Telemetry.** Content-free (§7).

**Settings** are off by default, in `packages/application/settings.py`:
- `experimental_typed_decisions_enabled: bool = False` is the master switch.
- A per-decision mode `off` | `shadow` | `live` covers each of these decisions:
  - `front_door`
  - `relevance`
  - `extraction_gate`
  - `compaction`
  - `dedupe`
  - `semantic_kind`
  - `verify`
- `typed_decision_data_route`: phase 1 accepts **only** `synthetic_only`. Phase 2 adds exactly one route once the maintainer has signed ZDR terms: `vercel_zdr` or `typesafe_enterprise_zdr`. Settings validation rejects routes that are not built yet.
- `typed_decision_model_id`, default `jev-1.13.0` (pinned). A changed model ID needs thresholds re-validated.

**Credentials** come only from environment variables: `TYPESAFE_API_KEY`, the name the maintainer already uses, and `ANTHROPIC_API_KEY` from phase 2. They are never written to settings, logs or telemetry.
- Missing credentials return `no_credential`.
- `~/.zshenv` is the recommended place for them, because `.zshrc` is not read by every launch path.

### 5.3 Jev connector: `connectors/typesafe/jev_provider.py`

It uses stdlib `urllib`, the same as the Ollama connector, so there is no new dependency.

**The API, from TypeSafe's docs** (verified with the 2026-09-29 smoke call):
- `POST https://api.typesafe.ai/v1/systemone` with body `{"model", "state", "questions": {name: {...}}}`.
  - `state` is the text to judge, billed once however many questions are asked.
  - The TypeSafe cookbook shows 13 questions in 0.27 s at 12× less cost than 13 separate calls.
- **Question types:**
  - `noul`: a yes/no probability.
  - `choice`: up to 255 labels, each with an optional `criteria` text. It returns `choice`, `confidence` and `probabilities`.
  - `score`: a rubric with 2 to 10 levels.
- **Confidence vs probability.** For `choice`, `confidence` is computed from how spread out the probabilities are, and TypeSafe recommends thresholding on it. `noul` has no confidence, so it thresholds on the probability.
- **Limits:** 64k tokens per request (32k for `state`) and 1,200 requests per minute.
- **Errors:** 401, 422, 429 and 529 all map to `http_error`, with no retry.
- **Version:** each response reports the model version used. Telemetry records it for provenance.
- **Keep instructions short.** Question instructions made up most of the 485 input tokens in the smoke call.

### 5.4 Rules adapter: `RulesClassifier`

Today's deterministic logic goes behind the same port. It includes:
- the `choose_automatic_context_route` rule cascade and `CompactLocalMemoryRouter`
- `plan_automatic_context_needs` cues
- the eval harness's risk tags: migration, auth, deletion, secrets, deploy

Each call site has one code path: ask, and if the answer is `unavailable` or below its bar, use the rules result.

---

## 6. Decision sites

**How thresholds are read:**
- For `noul`, compare the probability p.
- For `choice`, the chosen label counts only when `confidence ≥ 0.6`. Label-specific bars use `probabilities[label]`.
- Every threshold below is a **starting value**, tuned on phase 1 fixtures and recorded with the pinned model version.

### 6.1 Front door, per prompt (maintainer-approved section 2, updated by change 1)

```
prompt
 ├─ hard rule or learned phrase matches ─────────► today's route, no Jev call
 │   (greeting, local Mnemo op, exact path/symbol, learned phrase)
 ├─ guard blocks (route/secret/budget/credential) ─► today's rules
 ▼
local retrieval for the rules-predicted route (unchanged, local)
 ▼
ONE Jev request, 600 ms deadline ── unavailable ──► today's rules
 │  needs_long_term  noul                         ┐ memory need
 │  needs_structure  noul                         ┘ (short-term only = both "no")
 │  complexity       choice light/heavy           ┐
 │  tool_need        choice none/read_heavy/edit  ├─ committee → light / heavy
 │  risk             LOCAL rule axis, veto        ┘
 │  skill            choice ≤32 skills + none
 │  helps_<i>        noul per retrieved candidate  (relevance, §6.2)
 ▼
ADR 0046 action (none / push_* / lazy_pull) + skill slice + optional hint
```

**Memory need** replaces the keyword cues inside `plan_automatic_context_needs` (`context_routing.py:406`). The rest of ADR 0046's gate is unchanged.
- p ≥ 0.7 means yes, p ≤ 0.3 means no, and anything between means unknown, which leads to `lazy_pull`.
- Both no means short-term only: nothing is attached, and the pre-run retrieval is discarded.
- Sometimes Jev says yes to a need whose retrieval the rules did not pre-run. Mnemo then runs that local retrieval after the answer and attaches it **unfiltered**, because keep is the safe side, within the route's existing token ceiling. There is no second Jev call.

**Skill** replaces keyword-overlap matching in `packages/skills_registry/registry.py`. If `confidence` is below 0.6, the keyword match is used instead.

**Tier (committee)** drives only the hint. The starting axes are:

| Axis | Source | Escalation score | Weight | Veto |
|---|---|---|---|---|
| complexity | Jev | p(heavy) | 0.6 | none |
| tool_need | Jev | none 0.0, read_heavy 0.25, edit 0.75 | 0.4 | none |
| risk | local rules | 1.0 if any risk tag matches, else 0.0 | 0.0 | 1.0 |

The escalation threshold is 0.5. A risk match always forces heavy.

**The hint** is about 20 tokens, within ADR 0046's 40-token limit. It is emitted **only** when all three hold: the tier is light, `tool_need = read_heavy`, and there is no veto. The text is *"Mnemo: light, reading-heavy task; a Haiku subagent could do the reading."* It merges with the lazy-pull hint when both fire. There is no hint for heavy or small tasks, because delegating a tiny task costs more than doing it.

**What never changes:** Jev never overrides a hard rule, a learned phrase, the secret scan or the data-route gate.

### 6.2 Relevance filter, per prompt (approved section 3, updated by change 1)

Today, knowledge sections qualify on one matching word (`unified_context.py:909-945`). Episodic events are picked pinned-first, then newest, and ignore the query (`checkpoints.py:1330`). The fix adds one `helps_<i>` yes/no question per candidate, **inside the same front-door request**.

- **Never sent, always kept:** pinned, mandatory, conflict-notice and selected-procedure items, plus anything with sensitivity other than `normal` or flagged by the secret scan.
- **Scope:** up to 8 knowledge sections and 8 events. Each is sent as a heading plus the first 300 characters.
- **Dropping:** a candidate is dropped only when p(helps) ≤ 0.2. Anything else, including an unknown answer or a timeout, is kept, which is today's behavior.
- **No backfill.** Freed budget stays unspent.
- **Dropped items stay reachable.** They are listed in the existing `MNEMO_OMISSION` line with reason `low_relevance` and their retrieval handles, so the agent can still fetch them.
- **Automatic attachment only.** An explicit `get_context` call stays unfiltered.
- It runs only on push routes. `none` and `lazy_pull` skip both retrieval and the filter.
- **Phase gate:** stored snippets leave the machine only on a ZDR route. Under `synthetic_only` the filter runs only in the offline harness.

### 6.3 Extraction without the local model (approved section 4; goes live in phase 2)

```
extract_episodic (unchanged MCP tool and Stop nudge)
 ▼ bounded event summary → guard
Pass 1 (one Jev request): worth_remembering noul
                          committee: complexity, multiple_facts, risk (local veto)
 ├─ p(worth) ≤ 0.3 ──► "nothing to extract", no model call        (none tier)
 ├─ light ──────────► Haiku writes {kind, claim}, forced JSON schema (cheap tier)
 └─ heavy ──────────► existing host-agent handoff                   (smart tier)
                        ▲ invalid or failed Haiku output also lands here
 ▼
parse_episodic_output (unchanged) + secret scan
 ▼
Pass 3 (one Jev request): supported_by_event_<i> choice supports/contradicts/unrelated
                          sensitivity_<i> choice (may only make it stricter)
 ├─ probabilities.supports ≥ 0.8 ──► approved (as today)
 └─ otherwise ─────────────────────► dropped, counted in "dropped"
```

**Why pass 3 matters:** today, valid candidates go straight to approved memory and their confidence is ignored (`episodic_extraction_ingest.py:35`). The validity gate "is not a correctness oracle", so pass 3 adds an independent check that each claim is supported by the event. Dropping a candidate is not a mutation, so this follows AGENTS.md's rule that "LLM output is an untrusted proposal".

**Haiku connector:**
- A new `connectors/anthropic/haiku_provider.py` implements the existing `RawEpisodicExtractionProvider`, using stdlib `urllib`.
- It has a 20 s timeout. The model ID comes from `model_id` and is never hard-coded.
- The budget reservation carries a real `cost_microusd`, taken from the API response's usage field.
- `model_provider` accepts `anthropic` alongside `ollama`, and `optional_model_enabled` stays the master switch.
- **Ollama remains selectable**, so reverting is one settings change.
- The exact Messages API structured-output parameters are confirmed at implementation time.

**Out of scope:** the `lesson` and `preference` kinds, which ingest still drops, and wiring the review queue. Low-confidence candidates are dropped rather than queued.

### 6.4 Write-path quality (approved section 5; phase 3)

These four decisions run only at explicit MCP calls, never per prompt. Each falls back to today's behavior.

| Decision | Today | With Jev | Safe side |
|---|---|---|---|
| Checkpoint compaction (`checkpoints.py:1485`) | Keeps the first item of each list | For each list over its limit: which item matters most for resuming? The 200-token target and the mechanical truncation stay unchanged | The first item |
| Near-duplicate events (`checkpoints.py:593`, extraction ingest) | Exact match only | Duplicate of a recent event of the same kind? At p ≥ 0.9 it is skipped and counted in `dropped` | Persist |
| Semantic-compiler kind and flags (`semantic_memory.py:316,357,384`) | Unlabelled items become `INFERENCE`; flags come from regexes; confidence is a fixed constant | Choose among the 11 kinds only when there is no explicit `kind:` prefix. Flags can only be **added**. Calibrated confidence replaces the constants | Today's regex result |
| `verify_against_memory` for prose (`semantic_verification.py:73`) | All prose is `unverifiable` | Supports, contradicts or unrelated. Reports `consistent`/`mismatch` only at p ≥ 0.9 and never reconciles automatically | `unverifiable` |

**Deferred:**
- lesson dedupe, which would block a write, and AGENTS.md forbids delegating canonical mutation
- contradiction detection at retrieval
- obsolete-memory detection
- the review queue

---

## 7. Failure handling and telemetry

**One rule: every failure falls back to today's behavior.** Each failure records a closed `unavailable_reason`:
- `disabled`
- `data_route_blocked`
- `no_credential`
- `secret_blocked`
- `sensitivity_blocked`
- `budget_denied`
- `timeout`
- `http_error`
- `schema_invalid`

**Telemetry** is content-free and per decision. It extends the automatic route record in `packages/telemetry/automatic_routes.py`, following the same pattern as the existing `shadow_*` fields. It records:
- the decision mode
- each answer's probability bucket
- whether the answer agrees with the rules
- the Jev round-trip time
- the reported model version
- `unavailable_reason`
- for extraction, the tier used and Haiku's **real** usage from the API response

A closed `subagent_delegation` tool category measures whether the agent delegates after a hint. **No savings claims are made from character estimates.**

---

## 8. Testing and evaluation

- **Unit tests.** Fake Jev and Haiku transports, with no live calls in any test.
  - Guard ordering: the data-route gate runs first and makes no network call.
  - Every `unavailable_reason`.
  - Label rejection and log-probability clamping.
  - `classify_batch` and the committee's fallback to `gather`.
  - Committee axes from mixed sources, and the veto.
- **Contract test.** With `experimental_typed_decisions_enabled = false`, hook output and the MCP surface are byte-identical to today.
- **Security tests.** Nothing reaches the transport when:
  - the route is `synthetic_only` and the source is `runtime`
  - the text contains a secret
  - an item's sensitivity is not `normal`

  Also covered: the deadline, and malformed responses falling back.
- **Architecture check.** New modules pass `npm run architecture:check`. Connectors are imported only through composition.
- **Offline harness** (phase 1, synthetic fixtures only; live Jev runs need the maintainer's explicit go-ahead):

| Decision | Fixture | Gate |
|---|---|---|
| Memory need | `tests/fixtures/evals/automatic-context-routing-v1.json` (60 synthetic prompts) | The existing gates in `tests/evals/test_automatic_context_routing.py`: accuracy ≥ 0.80, prior-memory recall ≥ 0.90, structure recall ≥ 0.80, no-memory precision = 1.0 |
| Relevance | `tests/fixtures/evals/viability-corpus-v1.json` ("required facts" vs "irrelevant_or_obsolete") | Zero required facts dropped |
| Worth-remembering and kind | Viability corpus, with `kind:` prefixes stripped | At least matches the Ollama baseline |
| Closed-field extraction | `tests/fixtures/evals/telehealth-long-horizon-phase2-qwen25coder7b.json`, used only after its provenance is confirmed synthetic | At least matches the Ollama baseline |
| Tier | A new synthetic labelled tier set (built in phase 1) | Heavy recall ≥ 0.95: a misroute toward light is the costly error |
| Latency | At least 50 cold synthetic calls | At most 5% exceed 600 ms, reported as p50, p95 and maximum |

The 750 ms p95 target in `docs/evaluation-baseline.md` covers deterministic local assembly. Jev round-trip time is reported separately, and the worst case added to a prompt is the 600 ms deadline.

---

## 9. Rollout

| Phase | What it builds | Real data leaves the machine? | Exit gate |
|---|---|---|---|
| **1: synthetic** | Commit the cascade router with the §5.1 changes. Add the guard with the data-route gate, the Jev connector, the rules adapter, the settings, the offline harness, the synthetic tier set and the latency benchmark | **No** | Every §8 offline gate passes on synthetic fixtures |
| **2: real traffic** | The ZDR route adapter, name and identifier redaction, and the Haiku connector. Front door and relevance run in shadow, then go live. Extraction moves off Ollama | Yes, only via ZDR | The phase 1 gates pass again on the ZDR route. In shadow, attached tokens fall and the rate of "missing context" labels does not rise. Extraction approves 0 unsupported claims |
| **3: write path** | The four §6.4 decisions | Yes, only via ZDR | Each decision passes its own eval |

**What phase 2 needs before it starts:**
1. **Signed ZDR terms.** These can come from Vercel (Pro plan, `zeroDataRetention: true`, Vercel credentials) or from TypeSafe enterprise via sales@typesafe.ai. The terms must cover logs, abuse-monitoring copies, backups and telemetry derived from content.
2. **Anthropic's API data terms** recorded in the dependency register, since Haiku receives event summaries. This spec did not research them.
3. **Name and identifier redaction** added before the guard sends anything.
4. **ADR 0049 accepted** (§10).

---

## 10. Paperwork

**ADR 0049.** Rewrite the uncommitted draft *in place*. It was never accepted, and it still describes the early "shadow-only routing" plan. The new title is "Hosted typed classifier and cheap-model tier". It must:
- supersede item 3 of `docs/local-first-token-efficiency-plan.md` ("make no hosted-provider call") for these decisions only
- amend ADR 0045's local-only routing
- record the terms versions from §3.5 and the data-route rule

**Other docs:**
- threat-model entries for traffic sent to TypeSafe and Anthropic
- dependency-register entries covering vendor data terms
- an updated note on the cascade router's status in `docs/implementation-status.md`

---

## 11. Out of scope

- Routing or proxying the host agent's model (ADR 0048).
- A long-lived warm-connection process (§4.2).
- Sending real prompts or stored snippets on a standard TypeSafe key.
- Enabling anything by default.
- Team mode.
- Deleting the Ollama connector.
- Contradiction detection at retrieval, lesson dedupe, obsolete-memory detection and the review queue.

## 12. Revision 2026-10-01 (after the first live run)

The first live run failed two gates. This section records what we changed and why. Earlier
sections are left as they were written.

**Memory need is now one choice question.** The two yes/no questions ("needs earlier sessions",
"needs code structure") failed. Accuracy was 0.62, and 14 of 15 project-document prompts were
missed because the first question was worded around "earlier sessions". Rewording made the two
questions interfere: anything "not in the message" triggered both. A single five-way choice
(`past_sessions`, `project_docs`, `code_structure`, `code_and_history`, `nothing`) passed every
routing gate on the dev set: accuracy 0.90, prior-memory recall 1.0, structure recall 0.93,
none precision 1.0. Every miss was a low-confidence answer, which becomes unknown and so leads
to lazy pull.

**Relevance becomes a per-note filler check.** Asking "does this note help the request?" dropped
3 relevant notes and 0 filler. A relevant/unrelated choice dropped a critical "do not rerun"
warning at 94% confidence. Judging the note alone works: "task information vs filler" gave
p(info) of 0.97 to 1.00 for relevant notes and 0.11 to 0.27 for noise. It must be one request per
note, because batching 8 notes broke the scores. Only confident filler (p(filler) >= 0.7) is
dropped, and keep is the safe side. This check cannot judge whether a note is topically relevant
to a request; that is deferred.

**A held-out fixture joins the gates.** `typed-decision-holdout-v1.json` has 40 new routing
prompts and 24 notes (16 relevant, 8 noise), written after the questions were tuned. Its
front-door and relevance results are scored with the same gates, and phase 1 is complete only if
they pass too. Latency is still measured on the original 60 routing prompts. The relevance gates
now also require that at least 90% of noise is dropped, so a filter that drops nothing cannot
pass.

**The baseline is scored honestly.** A connection failure or timeout (including a refused or
unreachable Ollama) is unanswered. If the local model replies but the output is unreadable
(not JSON, truncated, or valid JSON in the wrong shape), that is a wrong answer: the row is
answered with no proposals and counted as a format failure. A new gate, `baseline_valid`,
requires at least 90% well-formed baseline output, because a mostly unreadable baseline would
make "Jev is at least as good as the baseline" meaningless.

**Cost and latency of one request per note.** For relevance this supersedes the single request
of section 3 change 1. The 600 ms per-prompt deadline applies to the front-door request. The
filler check sends one request per retrieved note, up to 16 per prompt. The evaluation reports
its per-request latency (`request_p50_ms`, `request_p95_ms`) and count, but does not gate them.
A concurrent total-time budget for those requests must be set and gated before the filler check
goes live in phase 2.

**Reading the holdout gates.** The holdout has 8 noise notes, so `filler_removed` (at least 90%
of noise dropped) means all 8 must be dropped; 7 of 8 is 0.875 and fails. The report's
`noise_dropped_share` is the share of all dropped notes that were noise, and is `None` when
nothing was dropped.

**Outcome (2026-10-02).** The live run `2026-10-01-phase1-b` passed every gate except two: the
held-out prior-memory recall (0.8 against 0.9; both misses were correct `past_sessions` answers
at confidence 0.58 and 0.50, below the 0.6 bar, so they resolve safely to lazy pull) and
`baseline_answered` (one Ollama transport failure, now mitigated by one retry in the harness).
The maintainer accepted phase 1 as passed with those two documented exceptions and did not lower
the 0.6 bar, since tuning on the holdout would invalidate it; a lower per-label bar would need a
third fresh prompt set.
