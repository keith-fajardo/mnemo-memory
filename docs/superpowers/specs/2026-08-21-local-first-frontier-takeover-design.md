# Local-first + direct frontier takeover — design spec

**Date:** 2026-08-21
**Status:** Draft for review
**Author:** Claude (paired with maintainer)
**Related evidence:** `evaluation-results/long-horizon-v1/final-20260820-*-takeover-v6-30pair-001`, `docs/evaluations/supervised-small-model-shadow-takeover-30-pair-v6-preregistration.md`, `docs/implementation-status.md` (lines ~5984–6346)

---

## 1. Problem & motivation

Mnemo performs a few of its own **internal, model-backed operations** (via `packages/model_gateway`): today, optional **episodic-candidate extraction**; and, as a future capability, a **semantic-memory compiler** that turns evidence into typed atoms. These run a model to produce a *closed, deterministically-validated* structured proposal.

The `takeover-v6` evaluation (live models, synthetic corpus, 2026-08-20) is the only routing strategy that passed its preregistered goal: **local-first execution with direct frontier takeover** — run a cheap local model, and hand the whole task to a frontier model only when a deterministic gate on the local output fails. On that synthetic corpus it achieved 30/30 completion, 0 critical false memories, quality parity with direct-frontier, and ~80% frontier-token savings.

This spec productionizes **only that mechanism**, applied **only** to Mnemo's own internal model calls, **default-off and opt-in**, fail-closed.

### What this is NOT
- Not the supervised repair loop (evidence **rejected** it: SS−SD never reached the +0.10 margin).
- Not the hybrid *plan-first* / risk-tag pre-routing (no measured win; and production has no risk-tag oracle).
- Not a router over the **host** agent's model (Claude Code / Codex) — Mnemo does not own that endpoint.
- Not enabled by default, and it ships **no** bundled frontier model, endpoint, or credential.

### Honest limitation (load-bearing)
In the eval, the takeover trigger was a **correctness oracle** (hidden tests / known-good fields). Production has **no oracle**. The only gate Mnemo can run on local output is a **validity/constraint check** (schema, scope, source-event existence, secret rejection; for the compiler also `verify_against_memory` contradiction). Therefore production escalates on **invalid** output, not on **wrong-but-valid** output. This is strictly weaker than the eval's gate. **We must not carry the eval's ~80% savings or quality-parity numbers into production copy or docs.** The product claim is limited to: "when the local model produces invalid/unusable output, a stronger model gets one bounded chance to produce a valid one; otherwise we fail closed exactly as today."

---

## 2. Success criteria

1. With the feature **off** (default), every code path is byte-for-byte equivalent to today (no frontier provider is even constructed).
2. With the feature **on and authorized**, a valid local result is returned with **zero** frontier calls; an invalid local result triggers **at most one** frontier attempt; an invalid frontier result **fails closed** with the existing stable error code (never a silent bad write).
3. No live model call is ever made in tests; fixtures keep authorization `false`.
4. No content (prompt/output) is logged; only content-free route accounting.
5. Personal-mode only; team-mode untouched.

---

## 3. Architecture

Ports-and-adapters, reusing the existing `model_gateway` seam. The gateway (`SchemaBound…Gateway`) already consumes a `Raw…Provider` Protocol (`provider_id`, `model_id`, `generate(request) -> object`) and enforces Mnemo's closed schema on whatever the provider returns.

### 3.1 The shared router — `LocalFirstTakeoverProvider`
New file: `packages/model_gateway/local_first_takeover.py`.

A provider decorator, generic over an operation's `(request, raw-output)` types, that **implements the same `Raw…Provider` Protocol** the gateway already consumes. It composes:
- `local: Raw…Provider` — required.
- `frontier: Raw…Provider | None` — **optional; absent by default** (this is how default-off is structural, not a runtime `if`).
- `validate: Callable[[object, request], None]` — raises on invalid; the **same pure function** the gateway uses (see 3.2).
- `frontier_budget: ModelBudgetReservationPort` + a frontier `ModelBudgetReservation` — caps and authorizes the escalation.
- `authorized: Callable[[], bool]` — the live-call authorization check (see §5).

`generate(request)`:
1. `raw = local.generate(request)`; `validate(raw, request)` → on success, return `raw` (0 frontier calls).
2. On `validate` failure (or `local.generate` raising): if `frontier is None` or `not authorized()` → **re-raise** the validity failure (fail closed to today's behavior). A schema-valid **empty** result is a legitimate outcome and is returned as-is (no escalation).
3. Else reserve the frontier budget (`ModelBudgetDenied` → fail closed to local-if-valid, else re-raise); `raw2 = frontier.generate(request)` under a hard timeout; `validate(raw2, request)`.
4. Frontier valid → return `raw2`. Frontier invalid / timeout / provider error → **fail closed** with the existing stable error code. **At most one** frontier attempt.

Because the router *is* a provider, the existing gateway still validates the value it receives — so **schema-binding remains the single enforcement point**; the router's `validate` call only *decides* whether to escalate.

### 3.2 Shared validator refactor
Each op's deterministic check is refactored into a **pure, importable function** so the gateway and the router call the identical logic (no drift):
- `parse_episodic_output(raw, max_candidates)` — extracted from `episodic_extraction._parse_output`.
- `validate_semantic_patch(...)` — the compiler's existing deterministic patch validation, extracted similarly (exact module confirmed in the plan phase).

The gateways are refactored to call these; no behavior change.

### 3.3 Wiring points
- **Episodic extraction (v1, end-to-end):** at construction, inject `LocalFirstTakeoverProvider(local=<existing optional-model provider>, frontier=<configured|None>, validate=parse_episodic_output, …)` where `SchemaBoundEpisodicExtractionGateway` is built.
- **Semantic compiler (structural, activates later):** provide the same router at the compiler's model-provider seam. **Sequencing honesty:** the compiler's *model* provider is future work (today it is deterministic-only). v1 defines the compiler's `Raw…Provider` Protocol + wiring point and routes it through the same `LocalFirstTakeoverProvider`; takeover activates the moment a model-compiler provider is supplied. v1 ships **no** live model compiler.

---

## 4. Escalation policy (the gate)

Escalate local → frontier **only** on a validity failure:
- malformed output / wrong fields / count out of bounds,
- scope violation, missing source-event, immutable-field violation,
- secret-like content rejected by policy,
- local provider raised,
- (compiler only) `verify_against_memory` contradiction against active constraints/decisions.

Explicitly **not** an escalation trigger: the model's self-reported `confidence` (untrusted). Validity only. This keeps the trigger deterministic and content-free.

Directionality: we never fabricate and never regress. If frontier is unavailable/unauthorized/over-budget/times-out, we return the **valid local** result if there is one, else the **existing failure** path. Frontier can only *replace an invalid local result with a valid one*, or fail closed.

---

## 5. Frontier configuration & authorization (two independent locks)

1. `experimental_local_first_takeover_enabled: bool = False` — new `PersonalSettings` field (mirrors `experimental_semantic_memory_enabled`). Enables the **code path** / constructs the router with a frontier provider.
2. `local_first_takeover_live_calls_authorized: bool = False` — new field mirroring the eval's `live_calls_authorized`. Permits an actual **outbound** frontier call. **Both** must be true for any frontier request; otherwise the router behaves as if `frontier is None`.

Frontier provider is **provider-neutral, bring-your-own**: endpoint + credentials come from configuration (reuse the existing `model_provider` / `model_id` settings pattern and provider-adapter conventions). Ship **no** default endpoint, **no** bundled key, **no** enabled provider. Local provider continues to require `optional_model_enabled`.

Scope: **personal mode only** in v1. Team mode (`apps/team_mcp`, PostgreSQL) is explicitly out of scope and unchanged.

---

## 6. Cost & latency controls

- **Frontier budget:** a dedicated `ModelBudget` reservation, reusing `ModelBudgetReservationPort` / `ModelBudgetReservation`. Add a frontier `ModelTaskType` (e.g. `FRONTIER_TAKEOVER`) or a per-request frontier reservation capped at 1, so a second gateway retry cannot produce a second frontier call. Denial → fail closed.
- **Timeout:** a hard per-call frontier timeout; on timeout, fall back to local-if-valid, else existing failure.
- **At most one** frontier attempt per top-level operation.
- Latency note: these are **background/lifecycle** operations (not the interactive prompt path), so the frontier's ~5× latency is bounded and off the user's critical path. Documented, not hidden.

---

## 7. Telemetry (content-free)

Reuse `packages/telemetry` conventions. Record only: accepted provider (`local` | `frontier`), escalation occurred (bool), validity-failure reason code, frontier call count, and durations. **No** prompt or output content, **no** raw claims. Mirrors the eval's "route names, reason codes, counters" discipline.

---

## 8. Testing (TDD)

Unit (`tests/unit`), fakes only — **no live calls**:
- valid local → returns local, 0 frontier calls;
- schema-valid **empty** local → returns empty, 0 frontier calls (empty is not a failure);
- each invalidity class → exactly one frontier attempt;
- frontier valid → returns frontier;
- frontier invalid / timeout / raises → fail closed with the existing error code;
- frontier unauthorized / flag-off / over-budget → behaves as `frontier is None` (fail closed to local-if-valid);
- **default-off → identical to today** (no frontier provider constructed);
- ≤ 1 frontier call even across the gateway's internal retry.

Contract (`tests/contract`): both gateways unchanged when no frontier injected.
Architecture (`tests/architecture`): router lives in `model_gateway`; no `apps → scripts` import; no new runtime dependency on the eval harness.
Security (`tests/security`): secret-bearing local output escalates and is never persisted; frontier output passes the same secret policy.

---

## 9. Out of scope / YAGNI

Team mode; risk-tag pre-routing; hybrid plan-first; the supervised repair loop; any default-on behavior; bundling a frontier model/endpoint/key; changes to the §03 automatic prompt router or the §04 preflight gate; changes to the offline eval harness.

---

## 10. Open items to pin during planning

1. Exact module/name of the semantic compiler's deterministic validator and its provider seam.
2. Whether to add a `ModelTaskType.FRONTIER_TAKEOVER` vs. a generic frontier reservation.
3. The timeout + budget default values (personal-mode sensible defaults; both configurable).
4. Settings persistence/migration for the two new boolean fields (default-false back-compat, mirroring how `experimental_semantic_memory_enabled` is added in `settings.py`).
