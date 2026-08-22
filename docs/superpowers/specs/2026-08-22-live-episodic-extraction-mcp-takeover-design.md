# Live episodic extraction with MCP host-agent takeover — design spec

**Date:** 2026-08-22
**Status:** Draft for review
**Author:** Claude (paired with maintainer)
**Builds on:** `docs/superpowers/specs/2026-08-21-local-first-frontier-takeover-design.md` (shipped: the reusable takeover core, `parse_episodic_output`, two opt-in locks, telemetry — all default-off, merged).

---

## 1. Goal & the reframe

Make model-backed **episodic extraction genuinely run** — the pipeline that today is pure scaffolding (no provider, no trigger, no caller). Extraction reads a bounded **task-activity event** (a summary of something that happened in a session) and proposes typed **episodic candidates** (`kind` / `claim` / `confidence` / `sensitivity`) that, after approval, become durable memory.

**The reframe that shapes this design:** the takeover "frontier" is a stronger model than the local one — and the strongest model already in the loop is the **host agent itself** (Claude Code / Codex). So instead of Mnemo spawning a second model (subprocess to `codex`/`claude`) or a detached background worker, Mnemo **hands the hard case back to the host agent** across two MCP calls. This removes the two riskiest pieces of the subprocess design and fits Mnemo's existing "agent calls MCP tools" pattern (the same pattern as `save_checkpoint`/`get_context`).

### Why not the alternatives (recorded so we don't relitigate)
- **Model call inside the hook** — rejected: hooks must never block the session; a local call takes seconds and a real frontier call tens of seconds to minutes (eval measured ~177s).
- **Subprocess frontier (`codex`/`claude`) + detached worker** — viable but heavy: two subprocess adapters, a background job runner, a result sink. The MCP/host-agent route makes all of that unnecessary.

---

## 2. Honest limitations (load-bearing)

1. **The host agent as frontier means you cannot independently pick "codex vs claude" as the frontier** — the frontier is whichever agent is driving the session. That is an accepted trade for dropping the subprocess machinery.
2. **The validity gate is not a correctness oracle** (carried from the prior spec): local output escalates on *invalid* structure, not *wrong-but-valid* content. The host-agent takeover produces a *valid* candidate set; it does not guarantee a *correct* one. Deterministic validation (`parse_episodic_output` + scope/secret checks) is the only automatic gate; final durability still passes the existing approval path.
3. **Evidence caution stands:** the takeover strategy was validated only on a synthetic corpus with ~5× latency; this feature is the production-live path that evidence advised holding. It ships **default-off** behind opt-in locks. Enabling it is the operator's explicit choice.

---

## 3. Success criteria

1. Default-off: with `optional_model_enabled=false` (and/or the takeover flags off), no model runs, no hook nudge is emitted, and behavior is identical to today.
2. The hook **never** runs a model and never blocks — it only emits a bounded, content-free-ish instruction at a work boundary when the feature is enabled.
3. `extract_episodic` runs the **local** model, and on invalid output escalates to the host agent **only** when both takeover flags are on.
4. Every persisted candidate passes the same deterministic validation the gateway enforces; nothing is stored unvalidated.
5. No live external subprocess; no detached worker; the runtime never imports `scripts/` (architecture guard extended).

---

## 4. Architecture

### 4.1 Local provider — `OllamaEpisodicProvider`
New file: `src/mnemo_memory/connectors/ollama/episodic_provider.py`.
Implements `RawEpisodicExtractionProvider` (`provider_id`, `model_id`, `generate(request) -> object`) by POSTing to a loopback Ollama endpoint `/api/generate` (mirrors the eval's `_post`), with:
- an **episodic-schema** prompt (NOT the eval's semantic-patch prompt) instructing the model to emit `{"candidates": [{"kind","claim","confidence","sensitivity"}...]}`,
- a hard HTTP timeout,
- output returned raw for `parse_episodic_output` to validate.
Endpoint/model come from the existing `model_provider`/`model_id` settings; requires `optional_model_enabled`.

### 4.2 MCP tools (on the existing MCP server)
- **`extract_episodic`** — input: an event reference (or the latest N unextracted task-activity events for the bound scope). Flow:
  1. If `optional_model_enabled` is false or no model configured → return a no-op status (`"extraction_disabled"`).
  2. Run `OllamaEpisodicProvider` → `parse_episodic_output`.
  3. **Valid** (including a schema-valid **empty** result — a legitimate "nothing to extract") → persist via §4.4 (0 rows if empty), return `{"status":"extracted","persisted":N}`. Empty never escalates.
  4. **Invalid** (malformed/typed failure from `parse_episodic_output`) → if **both** takeover flags on AND frontier budget available → return `{"status":"handoff","event":<bounded event>,"schema":<candidate schema>,"reason":"local_invalid"}` and record a pending-takeover marker. Else → return `{"status":"local_failed"}` (fail closed, nothing persisted).
- **`submit_episodic_candidates`** — input: the host agent's candidates for a pending handoff. Flow: validate with the **same** `parse_episodic_output` + scope/secret policy; on pass → persist via §4.4 and clear the marker; on fail → return `{"status":"rejected","reason":...}` (never persist invalid). Bounded to one handoff per event (≤1 takeover).

(The shipped `run_local_first_takeover` core stays as-is for a possible future *synchronous* frontier; this MCP flow is the async/host-agent equivalent and does not use it directly.)

### 4.3 The hook nudge (the "automatic" trigger)
Extend `AutomaticMemoryHook` (`connectors/automatic_memory/hook.py`): on **`Stop` / `PreCompact`** (work boundary), when the feature is enabled and there are un-extracted events, append a bounded instruction to the hook output — modeled exactly on the existing "call `get_context`" nudge at `hook.py:960` — e.g. *"Mnemo: run extract_episodic on this session's recent events."* No model call, no blocking, ~tokens only. Per-boundary, de-duplicated, and suppressed entirely when disabled. `PostToolUse` is explicitly **not** used (too frequent).

### 4.4 Persistence — wire the missing consumer
Extraction proposals → approved episodic events. Reuse the existing approved-episodic domain/storage (`packages/domain/approved_episodic_events.py`, the `record_event` path in `application/checkpoints.py`) and the `approved_event_capture_enabled` setting as the persistence gate. New application service `EpisodicExtractionIngest` converts validated `EpisodicExtractionProposal`s into approved episodic events under the bound scope, idempotently (dedupe by content hash + source event id).

### 4.5 Enablement chain (all default-off)
`optional_model_enabled` (local runs at all) → `experimental_local_first_takeover_enabled` + `local_first_takeover_live_calls_authorized` (host-agent takeover offered) → `approved_event_capture_enabled` (persistence). Frontier budget via the shipped `FRONTIER_TAKEOVER` reservation, per work-boundary window. Content-free `TakeoverRouteTelemetry` extended to record `handoff` outcomes. Everything fail-open: any error leaves the session unaffected and persists nothing.

---

## 5. Testing (TDD)
- Unit: `OllamaEpisodicProvider` builds the right request + parses valid/invalid (HTTP faked, no live call); `extract_episodic` returns `extracted`/`handoff`/`local_failed`/`extraction_disabled` per gate combination; `submit_episodic_candidates` persists only valid, rejects invalid, enforces ≤1 handoff; ingest is idempotent; hook nudge appears only when enabled and is absent by default.
- Contract: with feature off, MCP surface + hook output are byte-identical to today.
- Architecture: new connectors/app code never imports `scripts/`; guard extended.
- Security: secret-bearing candidates rejected before persistence in both local and host-agent paths.
- **No live model calls in any test** (Ollama HTTP faked; host-agent handoff simulated).

## 6. Out of scope
Subprocess `codex`/`claude` frontier adapters; detached background worker; automatic per-`PostToolUse` extraction; team mode; the semantic-compiler adapter; enabling any of this by default.

## 7. Open items for planning
1. Exact source of "un-extracted events" (a query over `TaskActivityEvent` by scope + a persisted high-water mark).
2. Whether `extract_episodic` + `submit_episodic_candidates` are two tools or one tool with a mode.
3. The pending-takeover marker store (local file vs SQLite row) and its TTL.
4. Ollama request/prompt shape + the exact episodic extraction prompt text.
