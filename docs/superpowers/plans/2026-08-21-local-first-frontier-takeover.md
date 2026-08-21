# Local-first + direct frontier takeover — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a default-off, opt-in local-first-with-frontier-takeover router for Mnemo's own `model_gateway` operations, escalating on a deterministic validity gate and failing closed.

**Architecture:** A pure generic combinator (`run_local_first_takeover`) runs a local attempt, validates it with the same pure validator the gateway enforces, and — only when valid escalation is authorized — makes at most one frontier attempt, re-validates, and otherwise fails closed. Two thin adapters apply the core at the two Protocol seams (`RawEpisodicExtractionProvider` now; `MemoryCompiler` structurally). Default-off is structural: no frontier callable is constructed.

**Tech Stack:** Python 3.12, `dataclasses`, `typing.Protocol`, `pytest`. No new third-party dependencies.

**Spec:** `docs/superpowers/specs/2026-08-21-local-first-frontier-takeover-design.md`

## Global Constraints

- Python floor 3.12; no new runtime dependencies.
- Two independent locks, both default **false**: `experimental_local_first_takeover_enabled` (code path) and `local_first_takeover_live_calls_authorized` (permits an outbound frontier call). Frontier runs only when **both** are true.
- **Decided open items** (from spec §10): use a **dedicated** `ModelTaskType.FRONTIER_TAKEOVER`; default frontier timeout **30s**; default frontier reservation `input_tokens=2000, output_tokens=1000, cost_microusd=0` (all configurable later).
- Escalation trigger is **validity only** (`TypeError`/`ValueError` from the local attempt or the validator). A schema-valid **empty** result is valid — no escalation. Never escalate on self-reported confidence.
- **At most one** frontier attempt per operation. Frontier-invalid/timeout/denied ⇒ fail closed with the existing behavior (never a silent bad write).
- No prompt/output content in logs — telemetry records route name, escalation bool, reason code, counts, durations only.
- Personal mode only. Do not touch `apps/team_mcp`, the §03 prompt router (`context_routing.py`), the §04 gate, or `scripts/`.
- No live model calls in tests; fakes only.

---

### Task 1: Extract a pure, importable episodic validator

**Files:**
- Modify: `src/mnemo_memory/packages/model_gateway/episodic_extraction.py`
- Test: `tests/unit/test_episodic_candidate_extraction.py` (existing; add cases)

**Interfaces:**
- Produces: `parse_episodic_output(raw: object, max_candidates: int) -> tuple[EpisodicExtractionProposal, ...]` (public; raises `TypeError`/`ValueError` on invalid). This is the exact current `_parse_output` body, renamed and exported.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_episodic_candidate_extraction.py
from mnemo_memory.packages.model_gateway.episodic_extraction import parse_episodic_output

def test_parse_episodic_output_is_public_and_parses_valid():
    raw = {"candidates": [{"kind": "decision", "claim": "x", "confidence": 0.9, "sensitivity": "normal"}]}
    result = parse_episodic_output(raw, 4)
    assert len(result) == 1 and result[0].claim == "x"

def test_parse_episodic_output_rejects_bad_fields():
    import pytest
    with pytest.raises(ValueError):
        parse_episodic_output({"nope": []}, 4)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_episodic_candidate_extraction.py::test_parse_episodic_output_is_public_and_parses_valid -v`
Expected: FAIL with `ImportError: cannot import name 'parse_episodic_output'`

- [ ] **Step 3: Rename `_parse_output` → `parse_episodic_output` and keep a thin alias**

In `episodic_extraction.py`, rename the function `def _parse_output(` to `def parse_episodic_output(`, update the one call site inside `extract` (`return parse_episodic_output(raw, request.max_candidates)`), and add a module-level alias so nothing else breaks:

```python
_parse_output = parse_episodic_output  # backward-compatible internal alias
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/test_episodic_candidate_extraction.py -v`
Expected: PASS (existing tests still green)

- [ ] **Step 5: Commit**

```bash
git add src/mnemo_memory/packages/model_gateway/episodic_extraction.py tests/unit/test_episodic_candidate_extraction.py
git commit -m "refactor(model_gateway): expose pure parse_episodic_output validator"
```

---

### Task 2: Add the dedicated frontier-takeover budget task type

**Files:**
- Modify: `src/mnemo_memory/packages/domain/model_budget.py:14-15`
- Test: `tests/unit/test_model_budget.py` (create if absent)

**Interfaces:**
- Produces: `ModelTaskType.FRONTIER_TAKEOVER = "frontier_takeover"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_model_budget.py
from mnemo_memory.packages.domain.model_budget import ModelTaskType

def test_frontier_takeover_task_type_exists():
    assert ModelTaskType.FRONTIER_TAKEOVER == "frontier_takeover"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_model_budget.py -v`
Expected: FAIL with `AttributeError: FRONTIER_TAKEOVER`

- [ ] **Step 3: Add the enum member**

```python
class ModelTaskType(StrEnum):
    EPISODIC_CANDIDATE_EXTRACTION = "episodic_candidate_extraction"
    FRONTIER_TAKEOVER = "frontier_takeover"
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/test_model_budget.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mnemo_memory/packages/domain/model_budget.py tests/unit/test_model_budget.py
git commit -m "feat(domain): add FRONTIER_TAKEOVER model budget task type"
```

---

### Task 3: The generic takeover core

**Files:**
- Create: `src/mnemo_memory/packages/model_gateway/local_first_takeover.py`
- Test: `tests/unit/test_local_first_takeover.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `class TakeoverError(RuntimeError)` with `.code: str`.
  - `run_local_first_takeover(*, local, frontier, validate, authorized, reserve_frontier, on_route=...) -> Raw` where:
    - `local: Callable[[], Raw]`, `frontier: Callable[[], Raw] | None`,
    - `validate: Callable[[Raw], None]` (raises `TypeError`/`ValueError` on invalid),
    - `authorized: Callable[[], bool]`,
    - `reserve_frontier: Callable[[], None]` (raises on denial),
    - `on_route: Callable[[str], None]` receiving `"local"` or `"frontier"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_local_first_takeover.py
import pytest
from mnemo_memory.packages.model_gateway.local_first_takeover import (
    run_local_first_takeover,
)

def _ok(x): return None
def _bad(x): raise ValueError("invalid")

def test_valid_local_returns_local_no_frontier():
    routes = []
    calls = {"frontier": 0}
    def frontier():
        calls["frontier"] += 1
        return "F"
    out = run_local_first_takeover(
        local=lambda: "L", frontier=frontier, validate=_ok,
        authorized=lambda: True, reserve_frontier=lambda: None,
        on_route=routes.append,
    )
    assert out == "L" and calls["frontier"] == 0 and routes == ["local"]

def test_invalid_local_escalates_once_to_frontier():
    routes = []
    validate = lambda x: None if x == "F" else (_ for _ in ()).throw(ValueError())
    out = run_local_first_takeover(
        local=lambda: "L", frontier=lambda: "F", validate=validate,
        authorized=lambda: True, reserve_frontier=lambda: None,
        on_route=routes.append,
    )
    assert out == "F" and routes == ["frontier"]

def test_no_frontier_provider_fails_closed():
    with pytest.raises(ValueError):
        run_local_first_takeover(
            local=lambda: "L", frontier=None, validate=_bad,
            authorized=lambda: True, reserve_frontier=lambda: None,
        )

def test_unauthorized_does_not_call_frontier():
    calls = {"frontier": 0}
    def frontier():
        calls["frontier"] += 1
        return "F"
    with pytest.raises(ValueError):
        run_local_first_takeover(
            local=lambda: "L", frontier=frontier, validate=_bad,
            authorized=lambda: False, reserve_frontier=lambda: None,
        )
    assert calls["frontier"] == 0

def test_budget_denied_fails_closed_without_frontier_call():
    calls = {"frontier": 0}
    def frontier():
        calls["frontier"] += 1
        return "F"
    def reserve():
        raise RuntimeError("denied")
    with pytest.raises(ValueError):
        run_local_first_takeover(
            local=lambda: "L", frontier=frontier, validate=_bad,
            authorized=lambda: True, reserve_frontier=reserve,
        )
    assert calls["frontier"] == 0

def test_frontier_invalid_fails_closed():
    with pytest.raises(ValueError):
        run_local_first_takeover(
            local=lambda: "L", frontier=lambda: "F", validate=_bad,
            authorized=lambda: True, reserve_frontier=lambda: None,
        )

def test_local_raising_typed_error_triggers_escalation():
    def local(): raise ValueError("local blew up")
    out = run_local_first_takeover(
        local=local, frontier=lambda: "F", validate=lambda x: None,
        authorized=lambda: True, reserve_frontier=lambda: None,
    )
    assert out == "F"
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/unit/test_local_first_takeover.py -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError`

- [ ] **Step 3: Implement the core**

```python
# src/mnemo_memory/packages/model_gateway/local_first_takeover.py
"""Provider-neutral local-first execution with one bounded frontier takeover."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

Raw = TypeVar("Raw")

_ESCALATION_TRIGGERS = (TypeError, ValueError)


class TakeoverError(RuntimeError):
    """Payload-free takeover failure with a stable diagnostic code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def run_local_first_takeover(
    *,
    local: Callable[[], Raw],
    frontier: Callable[[], Raw] | None,
    validate: Callable[[Raw], None],
    authorized: Callable[[], bool],
    reserve_frontier: Callable[[], None],
    on_route: Callable[[str], None] = lambda route: None,
) -> Raw:
    """Run local, validate, and escalate to frontier at most once on a validity failure."""

    try:
        candidate = local()
        validate(candidate)
    except _ESCALATION_TRIGGERS as local_failure:
        if frontier is None or not authorized():
            raise
        try:
            reserve_frontier()
        except Exception:
            # Denied/unavailable frontier budget: fail closed to the local failure.
            raise local_failure
        escalated = frontier()
        validate(escalated)  # frontier invalid -> propagate -> fail closed
        on_route("frontier")
        return escalated
    on_route("local")
    return candidate
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/unit/test_local_first_takeover.py -v`
Expected: PASS (all 7)

- [ ] **Step 5: Commit**

```bash
git add src/mnemo_memory/packages/model_gateway/local_first_takeover.py tests/unit/test_local_first_takeover.py
git commit -m "feat(model_gateway): add generic local-first frontier-takeover core"
```

---

### Task 4: Episodic takeover provider adapter

**Files:**
- Create: `src/mnemo_memory/packages/model_gateway/episodic_takeover.py`
- Test: `tests/unit/test_episodic_takeover.py`

**Interfaces:**
- Consumes: `run_local_first_takeover` (Task 3); `parse_episodic_output` (Task 1); `RawEpisodicExtractionProvider`, `EpisodicExtractionRequest` (existing); `ModelTaskType.FRONTIER_TAKEOVER`, `ModelBudgetReservation`, `ModelBudgetReservationPort` (existing + Task 2).
- Produces: `class TakeoverEpisodicProvider` implementing `RawEpisodicExtractionProvider` (`provider_id`, `model_id`, `generate(request) -> object`) that wraps a `local` and optional `frontier` `RawEpisodicExtractionProvider`. `provider_id`/`model_id` mirror the **local** provider (so the gateway's metadata check passes).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_episodic_takeover.py
from types import SimpleNamespace

import pytest
from mnemo_memory.packages.model_gateway.episodic_takeover import TakeoverEpisodicProvider

VALID = {"candidates": [{"kind": "decision", "claim": "x", "confidence": 0.9, "sensitivity": "normal"}]}
INVALID = {"candidates": [{"bad": True}]}

class FakeProvider:
    def __init__(self, out, pid="ollama", mid="ministral-3:8b"):
        self._out, self._pid, self._mid = out, pid, mid
    @property
    def provider_id(self): return self._pid
    @property
    def model_id(self): return self._mid
    def generate(self, request): return self._out

def _req():
    # The adapter only reads request.max_candidates and passes the object through,
    # so a lightweight stand-in avoids constructing heavy domain objects.
    return SimpleNamespace(max_candidates=4)

def test_valid_local_no_frontier():
    frontier = FakeProvider(VALID)
    p = TakeoverEpisodicProvider(
        local=FakeProvider(VALID), frontier=frontier,
        authorized=lambda: True, budget=_NullBudget(), reservation=_res(),
        workspace_id="ws",
    )
    assert p.generate(_req()) == VALID

def test_invalid_local_escalates_to_frontier():
    p = TakeoverEpisodicProvider(
        local=FakeProvider(INVALID), frontier=FakeProvider(VALID),
        authorized=lambda: True, budget=_NullBudget(), reservation=_res(),
        workspace_id="ws",
    )
    assert p.generate(_req()) == VALID

def test_default_off_no_frontier_provider_fails_closed():
    p = TakeoverEpisodicProvider(
        local=FakeProvider(INVALID), frontier=None,
        authorized=lambda: True, budget=_NullBudget(), reservation=_res(),
        workspace_id="ws",
    )
    with pytest.raises((TypeError, ValueError)):
        p.generate(_req())

# helpers
from mnemo_memory.packages.domain.model_budget import ModelBudgetReservation
def _res(): return ModelBudgetReservation(input_tokens=2000, output_tokens=1000, cost_microusd=0)
class _NullBudget:
    def reserve(self, workspace_id, task_type, reservation): return None
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/unit/test_episodic_takeover.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement the adapter**

```python
# src/mnemo_memory/packages/model_gateway/episodic_takeover.py
"""RawEpisodicExtractionProvider adapter that adds local-first frontier takeover."""

from __future__ import annotations

from collections.abc import Callable

from ..domain.model_budget import (
    ModelBudgetReservation,
    ModelBudgetReservationPort,
    ModelTaskType,
)
from .episodic_extraction import (
    EpisodicExtractionRequest,
    RawEpisodicExtractionProvider,
    parse_episodic_output,
)
from .local_first_takeover import run_local_first_takeover


class TakeoverEpisodicProvider:
    """Local-first provider; escalates to an optional frontier provider on invalid output."""

    def __init__(
        self,
        *,
        local: RawEpisodicExtractionProvider,
        frontier: RawEpisodicExtractionProvider | None,
        authorized: Callable[[], bool],
        budget: ModelBudgetReservationPort,
        reservation: ModelBudgetReservation,
        workspace_id: str,
        on_route: Callable[[str], None] = lambda route: None,
    ) -> None:
        self._local = local
        self._frontier = frontier
        self._authorized = authorized
        self._budget = budget
        self._reservation = reservation
        self._workspace_id = workspace_id
        self._on_route = on_route

    @property
    def provider_id(self) -> str:
        return self._local.provider_id

    @property
    def model_id(self) -> str:
        return self._local.model_id

    def generate(self, request: EpisodicExtractionRequest) -> object:
        frontier = None
        if self._frontier is not None:
            frontier = lambda: self._frontier.generate(request)  # noqa: E731
        return run_local_first_takeover(
            local=lambda: self._local.generate(request),
            frontier=frontier,
            validate=lambda raw: parse_episodic_output(raw, request.max_candidates),
            authorized=self._authorized,
            reserve_frontier=lambda: self._budget.reserve(
                self._workspace_id, ModelTaskType.FRONTIER_TAKEOVER, self._reservation
            ),
            on_route=self._on_route,
        )
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/unit/test_episodic_takeover.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mnemo_memory/packages/model_gateway/episodic_takeover.py tests/unit/test_episodic_takeover.py
git commit -m "feat(model_gateway): episodic local-first frontier-takeover provider"
```

---

### Task 5: The two opt-in settings (default false, migrated)

**Files:**
- Modify: `src/mnemo_memory/packages/application/settings.py`
- Test: `tests/unit/test_personal_settings.py` (existing; add cases)

**Interfaces:**
- Produces: `PersonalSettings.experimental_local_first_takeover_enabled: bool = False` and `PersonalSettings.local_first_takeover_live_calls_authorized: bool = False`, both in `_FIELDS`, `to_dict`, `from_dict` migration, and `__post_init__` bool validation.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_personal_settings.py
from mnemo_memory.packages.application.settings import PersonalSettings

def test_takeover_flags_default_false():
    s = PersonalSettings()
    assert s.experimental_local_first_takeover_enabled is False
    assert s.local_first_takeover_live_calls_authorized is False

def test_from_dict_migrates_missing_takeover_flags():
    d = PersonalSettings().to_dict()
    d.pop("experimental_local_first_takeover_enabled")
    d.pop("local_first_takeover_live_calls_authorized")
    migrated = PersonalSettings.from_dict(d)
    assert migrated.experimental_local_first_takeover_enabled is False
    assert migrated.local_first_takeover_live_calls_authorized is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/unit/test_personal_settings.py::test_takeover_flags_default_false -v`
Expected: FAIL (`AttributeError` / `TypeError` unexpected kwarg)

- [ ] **Step 3: Add the fields, defaults, validation, serialization, migration**

In `settings.py`:

1. Add both names to `_FIELDS`.
2. Add fields to the dataclass (place after `optional_model_enabled`):

```python
    experimental_local_first_takeover_enabled: bool = False
    local_first_takeover_live_calls_authorized: bool = False
```

3. Add both to the `__post_init__` boolean loop tuple.
4. Add both keys to `to_dict`.
5. Extend `from_dict` migration to inject defaults when the persisted dict predates these fields:

```python
    _MIGRATED_DEFAULTS = {
        "experimental_semantic_memory_enabled": False,
        "experimental_local_first_takeover_enabled": False,
        "local_first_takeover_live_calls_authorized": False,
    }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, dict):
            raise PersonalSettingsError("personal settings fields are invalid")
        missing = _FIELDS - set(value)
        if missing and missing <= set(cls._MIGRATED_DEFAULTS):
            value = {**{k: cls._MIGRATED_DEFAULTS[k] for k in missing}, **value}
        if set(value) != _FIELDS:
            raise PersonalSettingsError("personal settings fields are invalid")
        try:
            return cls(**value)
        except TypeError as error:
            raise PersonalSettingsError("personal settings values are invalid") from error
```

(This replaces the single-field back-compat line; the pre-existing
`experimental_semantic_memory_enabled` migration is subsumed by the same map.)

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/unit/test_personal_settings.py -v`
Expected: PASS (existing settings tests still green)

- [ ] **Step 5: Commit**

```bash
git add src/mnemo_memory/packages/application/settings.py tests/unit/test_personal_settings.py
git commit -m "feat(settings): add default-off local-first takeover opt-in + authorization flags"
```

---

### Task 6: Content-free route telemetry

**Files:**
- Create: `src/mnemo_memory/packages/telemetry/takeover_routes.py`
- Test: `tests/unit/test_takeover_route_telemetry.py`

**Interfaces:**
- Consumes: nothing from other tasks (pure recorder).
- Produces: `class TakeoverRouteTelemetry` with `record(route: str, *, escalated: bool, duration_ms: int) -> None` and `counts() -> dict[str, int]`; rejects any non-`{"local","frontier"}` route with `ValueError`. Content-free by construction (no text params).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_takeover_route_telemetry.py
import pytest
from mnemo_memory.packages.telemetry.takeover_routes import TakeoverRouteTelemetry

def test_records_route_counts_content_free():
    t = TakeoverRouteTelemetry()
    t.record("local", escalated=False, duration_ms=12)
    t.record("frontier", escalated=True, duration_ms=1800)
    assert t.counts() == {"local": 1, "frontier": 1}

def test_rejects_unknown_route():
    t = TakeoverRouteTelemetry()
    with pytest.raises(ValueError):
        t.record("secretstuff", escalated=False, duration_ms=1)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_takeover_route_telemetry.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement the recorder**

```python
# src/mnemo_memory/packages/telemetry/takeover_routes.py
"""Content-free counters for local-first takeover route decisions."""

from __future__ import annotations

from collections import Counter

_ROUTES = frozenset({"local", "frontier"})


class TakeoverRouteTelemetry:
    """Record only route name, escalation flag, and duration — never content."""

    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()

    def record(self, route: str, *, escalated: bool, duration_ms: int) -> None:
        if route not in _ROUTES:
            raise ValueError("takeover route is invalid")
        if not isinstance(escalated, bool):
            raise TypeError("escalated must be a boolean")
        if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or duration_ms < 0:
            raise ValueError("duration_ms must be a non-negative integer")
        self._counts[route] += 1

    def counts(self) -> dict[str, int]:
        return {route: self._counts.get(route, 0) for route in sorted(_ROUTES)}
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/test_takeover_route_telemetry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mnemo_memory/packages/telemetry/takeover_routes.py tests/unit/test_takeover_route_telemetry.py
git commit -m "feat(telemetry): content-free takeover route counters"
```

---

### Task 7: Architecture guard — no eval-harness dependency

**Files:**
- Test: `tests/architecture/test_takeover_boundaries.py` (create)

**Interfaces:**
- Consumes: the new modules from Tasks 3, 4, 6.

- [ ] **Step 1: Write the failing test**

```python
# tests/architecture/test_takeover_boundaries.py
import ast
import pathlib

SRC = pathlib.Path("src/mnemo_memory/packages/model_gateway")

def _imports(path):
    tree = ast.parse(path.read_text())
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
    return names

def test_model_gateway_does_not_import_eval_or_apps():
    for path in SRC.glob("*.py"):
        for mod in _imports(path):
            assert "scripts" not in mod, f"{path} imports eval harness"
            assert not mod.startswith("mnemo_memory.apps"), f"{path} imports apps layer"
```

- [ ] **Step 2: Run to verify it passes (guard holds now, prevents regressions)**

Run: `pytest tests/architecture/test_takeover_boundaries.py -v`
Expected: PASS (this is a guard; it must be green immediately)

- [ ] **Step 3: (No implementation — guard only.)**

- [ ] **Step 4: Commit**

```bash
git add tests/architecture/test_takeover_boundaries.py
git commit -m "test(architecture): forbid takeover model_gateway from importing eval/apps"
```

---

### Task 8: Full-suite green + quality gates

**Files:** none (verification task).

- [ ] **Step 1: Run the focused new suites**

Run: `pytest tests/unit/test_local_first_takeover.py tests/unit/test_episodic_takeover.py tests/unit/test_personal_settings.py tests/unit/test_takeover_route_telemetry.py tests/unit/test_model_budget.py tests/unit/test_episodic_candidate_extraction.py tests/architecture/test_takeover_boundaries.py -v`
Expected: PASS

- [ ] **Step 2: Run linize/type/format gates the repo uses**

Run: `ruff check . && ruff format --check . && mypy src/mnemo_memory`
Expected: clean

- [ ] **Step 3: Run the full test suite**

Run: `pytest -q`
Expected: PASS (no regressions)

- [ ] **Step 4: Commit any fixups**

```bash
git add -A
git commit -m "chore: takeover feature passes full suite + lint/type/format"
```

---

## Deferred to a follow-up plan (explicitly out of this plan)

- **Live wiring of episodic extraction into a runtime flow.** `SchemaBoundEpisodicExtractionGateway` is not constructed anywhere in the runtime today; assembling it from settings (injecting `TakeoverEpisodicProvider` with a real local Ollama provider + optional frontier provider) is its own effort and needs a construction/factory site that does not yet exist.
- **Semantic-compiler adapter** (`MemoryCompiler` seam). **Deviation from spec §3.3, on purpose:** the spec assumed the compiler seam could be wired structurally in v1, but the `MemoryCompiler.compile(scope, events, active_atoms, *, base_checkpoint_id) -> SemanticCheckpointPatch` seam does **not** receive the ledger/active-references/available-event-ids/applied-at that the real validity gate (`apply_semantic_checkpoint_patch`) needs. So the compiler's takeover gate cannot reuse the full deterministic validator at that seam without either a seam change or a weaker structural-only patch check. That is a genuine design decision, not a mechanical task, so it is pulled out of this plan. The reusable core (Task 3) already serves both operations — "one shared router" holds architecturally — but the compiler *adapter* needs its own short design pass. Recommend a follow-up brainstorm.
- **Frontier provider adapters** (BYO OpenAI-compatible / codex_cli), config plumbing for endpoint/credentials, and the frontier timeout wrapper.

These are named here so the scope of *this* plan is unambiguous: it delivers the reusable core, the episodic adapter, the two opt-in locks, telemetry, and guards — all default-off and fully tested — without making any live call or altering any runtime path.
