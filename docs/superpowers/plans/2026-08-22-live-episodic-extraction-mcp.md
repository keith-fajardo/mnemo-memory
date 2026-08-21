# Live episodic extraction with MCP host-agent takeover — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make episodic extraction actually run — a local Ollama provider + two MCP tools (`extract_episodic`, `submit_episodic_candidates`) where the takeover "frontier" is the host agent, triggered by a Stop/PreCompact hook nudge — all default-off and fail-open.

**Architecture:** `extract_episodic` runs the local model and validates with `parse_episodic_output`; valid proposals are ingested to approved episodic events; invalid output (with both takeover flags on) returns a handoff and writes a JSON pending marker, and the host agent submits candidates back via `submit_episodic_candidates`, which validates and persists. A hook nudge at the work boundary asks the agent to call the tool; no model work runs in the hook.

**Tech Stack:** Python 3.12, `urllib`/`http` for the Ollama call (no new deps), FastMCP tool registration, `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-22-live-episodic-extraction-mcp-takeover-design.md`

## Global Constraints
- Python 3.12; **no new runtime dependencies** (use stdlib `urllib.request` for Ollama HTTP).
- **Default-off everywhere.** `extract_episodic` runs the local model only if `optional_model_enabled` and a `model_provider`/`model_id` are set; host-agent handoff only if BOTH `experimental_local_first_takeover_enabled` and `local_first_takeover_live_calls_authorized` are true; persistence only if `approved_event_capture_enabled`. Any gate false → a no-op status, never an error that breaks a session.
- **Fail-open.** Any provider/HTTP/parse error yields a status dict, never an exception that escapes the tool; nothing is persisted on failure.
- **No live model calls in tests.** Fake the Ollama HTTP; simulate the host-agent handoff.
- Verify lint/types with `.venv/bin/ruff` and `.venv/bin/mypy` (PATH copies are rtk-intercepted). New test files must be ruff-clean (I001/E402/E501).
- Runtime must not import `scripts/` (guard test extended).
- **Design overrides vs spec:** pending marker is a JSON-file store mirroring `_ProjectHandoffStateStore` (NOT SQLite — no migration); persistence targets **approved episodic events** via `record_approved_event`; `EpisodicMemoryKind`→`ApprovedEventKind` maps `decision→decision`, `failure→failure`, `outcome→tool_outcome`, and **drops** `lesson`/`preference` in v1.

---

### Task 1: Episodic-kind → approved-kind mapping (pure)

**Files:**
- Create: `src/mnemo_memory/packages/application/episodic_extraction_ingest.py`
- Test: `tests/unit/test_episodic_extraction_ingest.py`

**Interfaces:**
- Produces: `approved_kind_for_episodic(kind: EpisodicMemoryKind) -> ApprovedEventKind | None` — returns the mapped kind, or `None` for kinds with no approved equivalent (`lesson`, `preference`).

- [ ] **Step 1: Write the failing test**
```python
# tests/unit/test_episodic_extraction_ingest.py
from mnemo_memory.packages.application.episodic_extraction_ingest import (
    approved_kind_for_episodic,
)
from mnemo_memory.packages.domain.episodic_candidates import EpisodicMemoryKind
from mnemo_memory.packages.domain.approved_episodic_events import ApprovedEventKind


def test_kind_mapping_maps_known_and_drops_unmapped():
    assert approved_kind_for_episodic(EpisodicMemoryKind.DECISION) is ApprovedEventKind.DECISION
    assert approved_kind_for_episodic(EpisodicMemoryKind.FAILURE) is ApprovedEventKind.FAILURE
    assert approved_kind_for_episodic(EpisodicMemoryKind.OUTCOME) is ApprovedEventKind.TOOL_OUTCOME
    assert approved_kind_for_episodic(EpisodicMemoryKind.LESSON) is None
    assert approved_kind_for_episodic(EpisodicMemoryKind.PREFERENCE) is None
```

- [ ] **Step 2: Run to verify it fails**
Run: `.venv/bin/python -m pytest tests/unit/test_episodic_extraction_ingest.py -q`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement the mapping**
```python
# src/mnemo_memory/packages/application/episodic_extraction_ingest.py
"""Convert validated episodic-extraction proposals into approved episodic events."""

from __future__ import annotations

from ..domain.approved_episodic_events import ApprovedEventKind
from ..domain.episodic_candidates import EpisodicMemoryKind

_APPROVED_KIND: dict[EpisodicMemoryKind, ApprovedEventKind] = {
    EpisodicMemoryKind.DECISION: ApprovedEventKind.DECISION,
    EpisodicMemoryKind.FAILURE: ApprovedEventKind.FAILURE,
    EpisodicMemoryKind.OUTCOME: ApprovedEventKind.TOOL_OUTCOME,
}


def approved_kind_for_episodic(kind: EpisodicMemoryKind) -> ApprovedEventKind | None:
    """Map an episodic kind to an approved-event kind, or None if it has no equivalent."""
    return _APPROVED_KIND.get(kind)
```

- [ ] **Step 4: Run to verify it passes**
Run: `.venv/bin/python -m pytest tests/unit/test_episodic_extraction_ingest.py -q`  Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add src/mnemo_memory/packages/application/episodic_extraction_ingest.py tests/unit/test_episodic_extraction_ingest.py
git commit -m "feat(episodic): add episodic-kind to approved-kind mapping"
```

---

### Task 2: Ingest service — proposals → approved episodic events

**Files:**
- Modify: `src/mnemo_memory/packages/application/episodic_extraction_ingest.py`
- Test: `tests/unit/test_episodic_extraction_ingest.py`

**Interfaces:**
- Consumes: `approved_kind_for_episodic` (Task 1); `RecordApprovedEpisodicEvent` + a service exposing `record_approved_event(command) -> object` (from `application/checkpoints.py`); `EpisodicExtractionProposal`, `MemoryScope`, `EvidenceReference`.
- Produces: `ingest_episodic_proposals(*, service, scope, source_event_key, evidence_references, proposals) -> IngestResult` where `IngestResult` has `.persisted: int` and `.dropped: int`. Each mapped proposal becomes one `RecordApprovedEpisodicEvent(scope, mapped_kind, proposal.claim, f"{source_event_key}:{i}", evidence_references)`; unmapped proposals increment `dropped`. Persistence is delegated to `service.record_approved_event`; the caller has already checked `approved_event_capture_enabled`.

- [ ] **Step 1: Write the failing test**
```python
def test_ingest_persists_mapped_and_drops_unmapped():
    from mnemo_memory.packages.application.episodic_extraction_ingest import (
        ingest_episodic_proposals,
    )
    from mnemo_memory.packages.domain.episodic_candidates import (
        EpisodicExtractionProposal, EpisodicMemoryKind,
    )
    from mnemo_memory.packages.domain.models import Sensitivity

    calls = []
    class FakeService:
        def record_approved_event(self, command):
            calls.append(command)
            return object()

    proposals = (
        EpisodicExtractionProposal(EpisodicMemoryKind.DECISION, "chose X", 0.9, Sensitivity.NORMAL),
        EpisodicExtractionProposal(EpisodicMemoryKind.LESSON, "note", 0.5, Sensitivity.NORMAL),
    )
    result = ingest_episodic_proposals(
        service=FakeService(), scope=_task_scope(), source_event_key="evt-1",
        evidence_references=(_evidence(),), proposals=proposals,
    )
    assert result.persisted == 1 and result.dropped == 1 and len(calls) == 1
```
(Use existing test helpers `_task_scope()`/`_evidence()` from `tests/unit/test_checkpoints*.py`; if absent in this file, construct a TASK-level `MemoryScope` and a single `EvidenceReference` inline following `tests/unit/test_episodic_candidate_extraction.py`.)

- [ ] **Step 2: Run to verify it fails** — `ImportError: ingest_episodic_proposals`.

- [ ] **Step 3: Implement**
```python
from collections.abc import Sequence
from dataclasses import dataclass

from ..domain.episodic_candidates import EpisodicExtractionProposal
from ..domain.models import EvidenceReference, MemoryScope
from .checkpoints import RecordApprovedEpisodicEvent


@dataclass(frozen=True, slots=True)
class IngestResult:
    persisted: int
    dropped: int


def ingest_episodic_proposals(
    *,
    service: object,
    scope: MemoryScope,
    source_event_key: str,
    evidence_references: tuple[EvidenceReference, ...],
    proposals: Sequence[EpisodicExtractionProposal],
) -> IngestResult:
    """Persist mapped proposals as approved episodic events; drop unmapped kinds."""
    persisted = 0
    dropped = 0
    for index, proposal in enumerate(proposals):
        mapped = approved_kind_for_episodic(proposal.kind)
        if mapped is None:
            dropped += 1
            continue
        service.record_approved_event(  # type: ignore[attr-defined]
            RecordApprovedEpisodicEvent(
                scope, mapped, proposal.claim, f"{source_event_key}:{index}", evidence_references,
            )
        )
        persisted += 1
    return IngestResult(persisted, dropped)
```
(Confirm the exact `RecordApprovedEpisodicEvent` field order against `application/checkpoints.py:187-194` and adjust if needed: `scope, kind, summary, source_event_key, evidence_references`.)

- [ ] **Step 4: Run to verify it passes.**
- [ ] **Step 5: Commit** — `git commit -m "feat(episodic): ingest validated proposals into approved events"`

---

### Task 3: OllamaEpisodicProvider (local `RawEpisodicExtractionProvider`)

**Files:**
- Create: `src/mnemo_memory/connectors/ollama/__init__.py`, `src/mnemo_memory/connectors/ollama/episodic_provider.py`
- Test: `tests/unit/test_ollama_episodic_provider.py`

**Interfaces:**
- Produces: `OllamaEpisodicProvider(endpoint: str, model_id: str, *, timeout_seconds: float = 30.0, transport: Callable[[str, dict], dict] | None = None)` implementing `RawEpisodicExtractionProvider` (`provider_id` == "ollama", `model_id`, `generate(request) -> object`). `generate` builds a prompt from `request.summary`, POSTs `{"model", "prompt", "stream": false, "format": "json"}` to `{endpoint}/api/generate` via `transport` (default: stdlib `urllib.request`), and returns `json.loads(response["response"])` for `parse_episodic_output` to validate. The `transport` seam lets tests inject a fake with no network.

- [ ] **Step 1: Write the failing test**
```python
# tests/unit/test_ollama_episodic_provider.py
import json

from mnemo_memory.connectors.ollama.episodic_provider import OllamaEpisodicProvider
from mnemo_memory.packages.model_gateway.episodic_extraction import parse_episodic_output


def _fake_transport(captured):
    def transport(url, payload):
        captured["url"] = url
        captured["payload"] = payload
        candidates = {"candidates": [
            {"kind": "decision", "claim": "x", "confidence": 0.9, "sensitivity": "normal"}]}
        return {"response": json.dumps(candidates)}
    return transport


def test_generate_returns_parseable_candidates():
    captured = {}
    p = OllamaEpisodicProvider("http://127.0.0.1:11434", "ministral-3:8b",
                               transport=_fake_transport(captured))

    class Req:
        summary = "did a thing"
        max_candidates = 4
    raw = p.generate(Req())
    assert parse_episodic_output(raw, 4)[0].claim == "x"
    assert captured["url"].endswith("/api/generate")
    assert captured["payload"]["model"] == "ministral-3:8b"
    assert p.provider_id == "ollama" and p.model_id == "ministral-3:8b"
```

- [ ] **Step 2: Run to verify it fails** — ModuleNotFoundError.

- [ ] **Step 3: Implement**
```python
# src/mnemo_memory/connectors/ollama/episodic_provider.py
"""Local Ollama-backed episodic-extraction provider (loopback HTTP, no new deps)."""

from __future__ import annotations

import json
from collections.abc import Callable
from urllib import request as _request

_PROMPT = (
    "Extract at most {n} episodic memory candidates from the event summary below. "
    'Reply with ONLY JSON of the form {{"candidates":[{{"kind":"decision|failure|outcome|'
    'lesson|preference","claim":"...","confidence":0.0,"sensitivity":"normal"}}]}}. '
    "Emit an empty candidates list if nothing is worth remembering.\n\nEVENT:\n{summary}"
)


def _urllib_transport(url: str, payload: dict) -> dict:
    body = json.dumps(payload).encode()
    req = _request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with _request.urlopen(req, timeout=payload.pop("_timeout", 30.0)) as response:  # noqa: S310
        return json.loads(response.read().decode())


class OllamaEpisodicProvider:
    """RawEpisodicExtractionProvider that calls a loopback Ollama /api/generate."""

    def __init__(
        self,
        endpoint: str,
        model_id: str,
        *,
        timeout_seconds: float = 30.0,
        transport: Callable[[str, dict], dict] | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._model_id = model_id
        self._timeout = timeout_seconds
        self._transport = transport or _urllib_transport

    @property
    def provider_id(self) -> str:
        return "ollama"

    @property
    def model_id(self) -> str:
        return self._model_id

    def generate(self, request: object) -> object:
        summary = getattr(request, "summary", "")
        max_candidates = getattr(request, "max_candidates", 4)
        payload = {
            "model": self._model_id,
            "prompt": _PROMPT.format(n=max_candidates, summary=summary),
            "stream": False,
            "format": "json",
            "_timeout": self._timeout,
        }
        result = self._transport(f"{self._endpoint}/api/generate", payload)
        return json.loads(str(result.get("response", "")))
```
`src/mnemo_memory/connectors/ollama/__init__.py`:
```python
from .episodic_provider import OllamaEpisodicProvider

__all__ = ["OllamaEpisodicProvider"]
```

- [ ] **Step 4: Run to verify it passes.**
- [ ] **Step 5: Commit** — `git commit -m "feat(connectors): Ollama episodic extraction provider (loopback, faked in tests)"`

---

### Task 4: Pending-takeover marker (JSON-file store)

**Files:**
- Create: `src/mnemo_memory/connectors/automatic_memory/pending_takeover.py`
- Test: `tests/unit/test_pending_takeover_store.py`

**Interfaces:**
- Produces: `LocalPendingTakeoverStore(data_directory: Path)` with `mark(scope: MemoryScope, source_event_key: str) -> None`, `pending(scope) -> str | None` (returns the stored source_event_key or None), `clear(scope) -> None`. JSON file under `data_directory`, atomic write (`NamedTemporaryFile` + `os.replace`), scope-keyed by a stable non-path key — mirror `_ProjectHandoffStateStore` in `connectors/automatic_memory/hook.py:~815-883`.

- [ ] **Step 1: Write the failing test**
```python
# tests/unit/test_pending_takeover_store.py
from mnemo_memory.connectors.automatic_memory.pending_takeover import LocalPendingTakeoverStore


def test_mark_pending_clear_roundtrip(tmp_path):
    store = LocalPendingTakeoverStore(tmp_path)
    scope = _task_scope()  # construct a TASK-level MemoryScope inline
    assert store.pending(scope) is None
    store.mark(scope, "evt-1")
    assert store.pending(scope) == "evt-1"
    store.clear(scope)
    assert store.pending(scope) is None
```

- [ ] **Step 2: Run — fails (ModuleNotFoundError).**
- [ ] **Step 3: Implement** — mirror `_ProjectHandoffStateStore`: a dict `{scope_key: source_event_key}` persisted as JSON via a temp file + `os.replace`; `_scope_key(scope)` from the same non-path derivation used at `hook.py:_handoff_scope_key`. Reject symlinked files (match the store's safety checks).
- [ ] **Step 4: Run — passes.**
- [ ] **Step 5: Commit** — `git commit -m "feat(automatic_memory): JSON pending-takeover marker store"`

---

### Task 5: Port methods `extract_episodic` + `submit_episodic_candidates`

**Files:**
- Modify: `src/mnemo_memory/packages/application/mcp_port.py` (add 2 Protocol methods)
- Modify: `src/mnemo_memory/packages/application/mcp_durable.py` (implement both)
- Test: `tests/unit/test_extract_episodic_port.py`

**Interfaces:**
- Consumes: `OllamaEpisodicProvider` (T3), `parse_episodic_output`, `ingest_episodic_proposals` (T2), `LocalPendingTakeoverStore` (T4), settings (`optional_model_enabled`, `model_provider`, `model_id`, both takeover flags, `approved_event_capture_enabled`), `TakeoverRouteTelemetry`.
- Produces on `McpContextPort`:
  - `extract_episodic(self, request: dict) -> dict` — resolves the target event (by `event_id` in request, else the latest un-extracted `TaskActivityEvent` via `list_task_activity_events(scope, offset=0, limit=1)`); gate-checks; runs local provider + `parse_episodic_output`; **valid** → `ingest_episodic_proposals` → `{"status":"extracted","persisted":N,"dropped":D}`; **invalid** → if both flags on → `pending.mark(scope, key)` + `{"status":"handoff","event":{...bounded...},"schema":{...},"reason":"local_invalid"}`, else `{"status":"local_failed"}`; disabled/misconfigured → `{"status":"extraction_disabled"}`. All exceptions caught → `{"status":"error"}` (fail-open, nothing persisted).
  - `submit_episodic_candidates(self, request: dict) -> dict` — requires a pending marker for the scope; validates `request["candidates"]` via `parse_episodic_output`; on valid → `ingest_episodic_proposals` → `pending.clear` → `{"status":"persisted","persisted":N,"dropped":D}`; on invalid or no marker → `{"status":"rejected","reason":...}` (nothing persisted).

- [ ] **Step 1–5:** Write failing unit tests using a `DurableMcpContextPort` built with fakes (fake settings object, fake extraction provider returning valid/invalid dicts, fake approved-event service capturing commands, temp-dir pending store). Cover: extracted / handoff / local_failed / extraction_disabled / error for `extract_episodic`; persisted / rejected(no-marker) / rejected(invalid) for `submit_episodic_candidates`; and **≤1 handoff** (second submit with no marker rejects). Implement the two methods on the Protocol and `DurableMcpContextPort` (thread the provider/pending-store/telemetry through the constructor — extend `DurableMcpContextPort.__init__` with optional, default-None collaborators so existing construction is unchanged). Then commit: `git commit -m "feat(mcp): extract_episodic + submit_episodic_candidates port methods"`.

(Reference the exact constructor at `mcp_durable.py:92-125` and the `save_checkpoint` port-call shape at `:759`. New collaborators default to None so the feature is inert unless wired.)

---

### Task 6: Register the two MCP tools (gated)

**Files:**
- Modify: `src/mnemo_memory/apps/mcp/server.py`
- Test: `tests/unit/test_mcp_server.py` (add cases)

**Interfaces:**
- Consumes: the port methods from T5.
- Produces: two `@server.tool(...)` registrations. Gate the pair behind a new `create_server(..., episodic_extraction_enabled: bool = False)` parameter (mirror the `experimental_semantic_memory_enabled` block at `server.py:622`). Add `"extract_episodic"`, `"submit_episodic_candidates"` to the `additionalProperties` `names` list at `server.py:921` **inside** the same gate (only when registered). `extract_episodic` carries `ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)`; `submit_episodic_candidates` likewise.

- [ ] **Step 1–5:** Failing test: with `episodic_extraction_enabled=True`, both tools are registered (`server._tool_manager._tools` contains them) and hardened (`additionalProperties is False`); with it False (default), neither is present and the tool set is unchanged. Implement the gated registration + names-list addition. Commit: `git commit -m "feat(mcp): register default-off episodic extraction tools"`.

---

### Task 7: Hook nudge at the work boundary

**Files:**
- Modify: `src/mnemo_memory/connectors/automatic_memory/hook.py`
- Test: `tests/unit/test_automatic_memory.py` (add cases)

**Interfaces:**
- Consumes: the enablement settings + `LocalPendingTakeoverStore`.
- Produces: in the `Stop`/`PreCompact` branch (`hook.py:322-343`), when episodic extraction is enabled AND there is at least one un-extracted `TaskActivityEvent` for the scope, append a bounded instruction to `instruction` (before the returns): `"\n\nMnemo: run extract_episodic on this session's recent events."` Suppressed entirely when disabled or when a handoff is already pending. No model call.

- [ ] **Step 1–5:** Failing test: with the feature enabled + events present, the Stop output's instruction contains the nudge; with the feature disabled (default), the output is byte-identical to today. Implement (guard on a passed-in enablement predicate + event presence; reuse the existing `instruction += ...` idiom at `hook.py:984`). Commit: `git commit -m "feat(automatic_memory): work-boundary nudge to run extract_episodic"`.

---

### Task 8: Production wiring — construct the provider + enable the gate from settings

**Files:**
- Modify: the construction site of `create_server(...)` and `DurableMcpContextPort(...)` (locate by grepping `create_server(` and `DurableMcpContextPort(` — likely `apps/mcp/server.py` entrypoint / `packages/application/bootstrap.py`)
- Modify: `src/mnemo_memory/apps/cli/main.py` where `AutomaticMemoryHook` is built (`main.py:3499`) — pass the enablement predicate + pending store to the hook
- Test: an integration test under `tests/integration/`

**Interfaces:**
- Consumes: everything from T3–T7 + `PersonalSettings`.
- Produces: at the construction site, when `optional_model_enabled` and `model_provider == "ollama"` and `model_id` is set, build an `OllamaEpisodicProvider(endpoint_from(model_provider/settings), model_id)` and a `LocalPendingTakeoverStore(data_directory)`, pass both into `DurableMcpContextPort`, and pass `episodic_extraction_enabled = optional_model_enabled` into `create_server`. The two takeover flags gate the handoff branch **inside** the port method (already implemented in T5), so the tool is registered whenever local extraction is enabled, and host-agent takeover only escalates when both flags are on. The hook (T7) receives an `episodic_extraction_enabled` predicate derived the same way. Everything stays default-off because all the settings default false.

- [ ] **Step 1:** Write a failing integration test: construct the server/port from a fake settings object with `optional_model_enabled=True, model_provider="ollama", model_id="ministral-3:8b"` (+ a fake Ollama transport) and assert `extract_episodic` returns `"extracted"`; with `optional_model_enabled=False` assert `"extraction_disabled"` and that the tools are absent.
- [ ] **Step 2:** Run — fails.
- [ ] **Step 3:** Implement the wiring at the located construction site (grep first; follow the existing constructor call shape). Keep the endpoint configurable (default `http://127.0.0.1:11434`).
- [ ] **Step 4:** Run — passes; confirm the default-off path (no settings) is unchanged.
- [ ] **Step 5:** Commit — `git commit -m "feat(mcp): wire Ollama episodic extraction + gate from settings"`

---

### Task 9: Architecture guard + full authoritative gate

**Files:**
- Modify: `tests/architecture/test_takeover_boundaries.py` (extend to the new dirs)
- (verification task)

- [ ] **Step 1:** Extend the guard to assert the new `connectors/ollama/` module and the new `application/episodic_extraction_ingest.py` import nothing from `scripts` (mirror the existing eval-harness guard). Run it green. Commit.
- [ ] **Step 2:** `.venv/bin/ruff check <all new/changed files>` → "All checks passed!"; fix any I001/E402/E501/B904/RUF012.
- [ ] **Step 3:** `.venv/bin/ruff format --check <files>` and `.venv/bin/mypy` on the new `src` modules → clean.
- [ ] **Step 4:** `.venv/bin/python -m pytest` on all new test files + `tests/unit/test_mcp_server.py` + `tests/unit/test_automatic_memory.py` → pass.
- [ ] **Step 5:** Commit any fixups: `git commit -m "chore: episodic extraction passes ruff/mypy/tests"`.

---

## Deferred / out of scope
- A true high-water-mark query (`event_sequence > ?`) — v1 pages from offset 0 and relies on approved-event idempotency (dedupe by `source_event_key:index`) to avoid re-persisting; a watermark column is a later optimization.
- `lesson`/`preference` proposal kinds (no approved-kind equivalent) — dropped + counted in v1.
- Subprocess `codex`/`claude` frontier, detached worker, team mode, semantic-compiler adapter, enabling by default.
- **Turning the settings flags ON + installing + a real Ollama run** — this plan makes the feature *work when enabled* but ships every default false; actually enabling it (and pulling an Ollama model, logging in the host agent) is a deliberate operator step after this lands, and remains gated on the evidence prerequisites (non-synthetic validation, cost/latency budget, human-quality pass).
