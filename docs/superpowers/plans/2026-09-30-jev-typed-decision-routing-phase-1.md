# Jev Typed-Decision Routing, Phase 1: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the synthetic-only first phase of Jev typed-decision routing. This covers four pieces:
- a revised cascade-router port
- a guard that blocks all runtime text by default
- a TypeSafe Jev connector
- an offline evaluation harness that measures Jev on Mnemo's synthetic fixtures

**Architecture:** Every closed "pick one from a list" decision is asked through one port: `PromptClassifier`, plus `CascadeCommittee` for the light/heavy verdict. A guard (`GuardedTypedDecisionClassifier`) sits in front of any hosted adapter. It runs a fixed chain of checks, and each check fails closed to a named "unavailable" reason: data route, credential, request shape, secret scan, sensitivity, size cap, budget, deadline and label check. In phase 1 the only data route is `synthetic_only`, so the guard refuses runtime text before any network work. Only the offline harness, which reads fixtures that declare synthetic provenance, ever reaches the Jev connector.

**Tech Stack:** Python 3.12 standard library only (`asyncio`, `urllib`, `dataclasses`, `enum`, `json`). Tests use `pytest` with `asyncio.run` and no pytest-asyncio. Checks use `mypy --strict` (which covers `src`, `tests` and `scripts`), `ruff` (line length 100) and `scripts/check_architecture.py`.

**Spec:** `docs/superpowers/specs/2026-09-30-jev-typed-decision-routing-design.md` (committed `b7a4c91`). Read §3, §5, §6.1–§6.3, §8 and §9 before starting. This plan implements **phase 1 only** (spec §9).

## Execution precondition (read first)

The cascade-router files from `stash@{0}`/`stash@{1}` are **staged but not committed** in the main checkout:
- `cascade_router.py`
- `tests/unit/test_cascade_router.py`
- the `model_gateway/__init__.py` exports
- a status note in `docs/implementation-status.md`

A fresh `git worktree` would not contain them. So execute in the main checkout, on a new branch created from the spec branch. `git switch -c` keeps the index:

```bash
cd /Users/keithfajardo/Desktop/local/personal/mnemo-memory
git switch docs/jev-typed-decision-routing
git switch -c feat/jev-typed-decisions
git status --short   # expect: staged cascade_router.py, test_cascade_router.py, __init__.py, implementation-status.md
```

If the staged files are missing, restore them with `git stash apply 'stash@{1}' && git stash apply 'stash@{0}'`. On a conflict in `docs/implementation-status.md`, keep HEAD's text and append the stashed section after it.

## Global Constraints

- **Dependencies:** Python 3.12 standard library only. No change to `pyproject.toml`, `uv.lock` or `docs/dependency-register.toml`.
- **Quality gates per task:** mypy strict must pass over `src/mnemo_memory`, `tests` and `scripts`. Ruff line length is 100.
  - After each task, run `uv run ruff format <changed files>`, `uv run ruff check --fix <changed files>` and `uv run mypy`.
- **Phase 1 sends no runtime text to any provider.**
  - `TypedDecisionDataRoute` has exactly one value, `synthetic_only`.
  - Runtime composition always binds `TypedDecisionSource.RUNTIME`, which that route blocks before any other work.
- **Guard check order is fixed:**
  1. data route
  2. credential
  3. request shape
  4. secret scan of the text **and every axis instruction and criteria string**
  5. sensitivity (only `normal` may be sent)
  6. size (text is bounded to 512 characters, head 256 + tail 255; each axis string must be ≤ 400 characters)
  7. budget (`ModelTaskType.TYPED_DECISION`)
  8. deadline
  9. label check
- **The guard never raises from `ask`.** Every failure is exactly one of these values:
  - `disabled`
  - `data_route_blocked`
  - `no_credential`
  - `secret_blocked`
  - `sensitivity_blocked`
  - `budget_denied`
  - `timeout`
  - `http_error`
  - `schema_invalid`
- **Jev API:**
  - `POST https://api.typesafe.ai/v1/systemone`
  - header `Authorization: Bearer <key>`
  - body `{"model", "state", "questions"}`
  - pinned model `jev-1.13.0`
  - yes/no axes are sent as `noul`, choice axes as `choice` with `criteria`
- **API key handling:** the key is read only from the environment variable `TYPESAFE_API_KEY`. It never appears in settings, logs, telemetry, reports, `repr`, or exception text.
- **Deadlines:**
  - runtime per-prompt deadline: **600 ms**
  - offline harness deadline: **5 s**, so true latency is measured
  - latency gate: at least 50 cold samples, and at most 5% of them over 600 ms
- **Starting thresholds** (spec §6):

  | Threshold | Starting value |
  |---|---|
  | Memory need | yes at p ≥ 0.7, no at p ≤ 0.3 |
  | Relevance | drop at p(helps) ≤ 0.2 |
  | Extraction | skip at p(worth) ≤ 0.3 |
  | `choice` answers | used only when `confidence ≥ 0.6` |

- **Committee:**

  | Axis | Weight | Escalation score |
  |---|---|---|
  | `complexity` | 0.6 | p(heavy) |
  | `tool_need` | 0.4 | none 0.0, read_heavy 0.25, edit 0.75 (expected value) |
  | `risk` | 0.0 | local rules; veto at 1.0 |

  The escalation threshold is 0.5.
- **Hint:**
  - Text, exactly: `Mnemo: light, reading-heavy task; a Haiku subagent could do the reading.`
  - Emitted only when the route is `light` **and** the accepted `tool_need` is `read_heavy`.
- **No live network in any test.** Use fake transports and fake adapters only.
- **Out of scope for phase 1.** Do **not** wire anything into:
  - `connectors/automatic_memory/hook.py`
  - `apps/mcp/server.py`
  - `context_routing.py`
  - `extract_episodic`

  Ollama stays the extraction provider.
- **Commit only the paths each task names.** Use `git commit -- <paths>` so other staged files stay out.

## Review Focus

These five failure modes are the most likely to bite a real user. The spec implies them, but no task's main tests would catch them. Each one has a pinning test in the owning task.

1. **A secret hidden in question text rather than the prompt.** Relevance snippets live inside axis `instructions`. The guard must scan every string it sends and return `secret_blocked` with zero adapter calls. Pinned by `test_secret_in_axis_text_is_never_sent` (Task 5).
2. **Jev returns a probability of exactly 0 or 1, or an answer map with an extra or missing label.** The log-probability must stay finite, and malformed maps must become `schema_invalid`, never a crash. Pinned by `test_extreme_probabilities_stay_finite` and `test_malformed_answers_are_schema_invalid` (Task 6).
3. **A `settings.json` written before this change.** It must load with the typed-decision defaults and must not be rejected. Pinned by `test_legacy_settings_without_typed_decision_fields_load` (Task 7).
4. **The API key leaking** into `repr`, exception text, or an evaluation report file. Pinned by `test_api_key_never_appears_in_repr_or_errors` (Task 6) and `test_cli_report_is_content_free_and_key_free` (Task 11).
5. **A slow provider while its worker thread is still running.** `ask` must return `timeout` close to the deadline instead of waiting for the thread. Pinned by `test_deadline_returns_promptly_while_adapter_still_runs` (Task 5).

---

### Task 1: Domain vocabularies, budget task type and status note

**Files:**
- Create: `src/mnemo_memory/packages/domain/typed_decisions.py`
- Modify: `src/mnemo_memory/packages/domain/model_budget.py` (enum `ModelTaskType`)
- Modify: `src/mnemo_memory/packages/domain/__init__.py` (imports and `__all__`)
- Modify: `docs/implementation-status.md` (replace the staged cascade-router section)
- Test: `tests/unit/test_typed_decision_domain.py`

**Interfaces:**
- Produces (all importable from `mnemo_memory.packages.domain`):
  - `TypedDecisionDataRoute(StrEnum)` with one value: `SYNTHETIC_ONLY = "synthetic_only"`
  - `TypedDecisionSource(StrEnum)`: `RUNTIME = "runtime"`, `SYNTHETIC_FIXTURE = "synthetic_fixture"`
  - `TypedDecisionMode(StrEnum)`: `OFF`, `SHADOW`, `LIVE`
  - `TypedDecisionKind(StrEnum)`: `FRONT_DOOR`, `RELEVANCE`, `EXTRACTION_GATE`, `COMPACTION`, `DEDUPE`, `SEMANTIC_KIND`, `VERIFY`
  - `TypedDecisionUnavailableReason(StrEnum)`: the nine closed reasons
  - `typed_decision_source_permitted(route: TypedDecisionDataRoute, source: TypedDecisionSource) -> bool`
  - `ModelTaskType.TYPED_DECISION = "typed_decision"`

- [ ] **Step 1: Declare the issue in the status document**

AGENTS.md makes `docs/implementation-status.md` name the only issue that may be worked on. In that file, replace the staged section that starts with `### MiniCPM cascade router Phase 1 — In progress (2026-09-02)` and runs to the end of the file with:

```markdown
### Jev typed-decision routing phase 1 — In progress (2026-09-30)

The maintainer approved `docs/superpowers/specs/2026-09-30-jev-typed-decision-routing-design.md`
and its phase-1 plan. This issue revises the uncommitted MiniCPM cascade-router contract
(2026-09-02) into a provider-neutral typed-decision port with `light`/`heavy` routes. It also adds
a guarded hosted-classifier boundary, a TypeSafe Jev connector, default-off settings and an
offline evaluation harness over synthetic fixtures.

Phase 1 sends no runtime text to any provider. The only data route is `synthetic_only`, and
runtime requests are refused before any network call. It does not wire the hook, the MCP server,
the prompt router or episodic extraction, and Ollama remains the extraction provider. Live Jev
evaluation runs only with explicit maintainer authorization.
```

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_typed_decision_domain.py
from mnemo_memory.packages.domain import (
    ModelTaskType,
    TypedDecisionDataRoute,
    TypedDecisionKind,
    TypedDecisionMode,
    TypedDecisionSource,
    TypedDecisionUnavailableReason,
    typed_decision_source_permitted,
)


def test_phase_one_has_exactly_one_data_route() -> None:
    assert [route.value for route in TypedDecisionDataRoute] == ["synthetic_only"]


def test_synthetic_only_route_permits_fixtures_and_blocks_runtime() -> None:
    route = TypedDecisionDataRoute.SYNTHETIC_ONLY
    assert typed_decision_source_permitted(route, TypedDecisionSource.SYNTHETIC_FIXTURE)
    assert not typed_decision_source_permitted(route, TypedDecisionSource.RUNTIME)


def test_closed_vocabularies_match_the_spec() -> None:
    assert {reason.value for reason in TypedDecisionUnavailableReason} == {
        "disabled",
        "data_route_blocked",
        "no_credential",
        "secret_blocked",
        "sensitivity_blocked",
        "budget_denied",
        "timeout",
        "http_error",
        "schema_invalid",
    }
    assert {kind.value for kind in TypedDecisionKind} == {
        "front_door",
        "relevance",
        "extraction_gate",
        "compaction",
        "dedupe",
        "semantic_kind",
        "verify",
    }
    assert [mode.value for mode in TypedDecisionMode] == ["off", "shadow", "live"]
    assert ModelTaskType.TYPED_DECISION.value == "typed_decision"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_typed_decision_domain.py -v`
Expected: FAIL with `ImportError: cannot import name 'TypedDecisionDataRoute'`

- [ ] **Step 4: Write the implementation**

```python
# src/mnemo_memory/packages/domain/typed_decisions.py
"""Closed vocabularies for hosted typed-decision classification (ADR 0049)."""

from __future__ import annotations

from enum import StrEnum


class TypedDecisionDataRoute(StrEnum):
    """Where decision text may be sent. Phase 1 permits synthetic fixtures only."""

    SYNTHETIC_ONLY = "synthetic_only"


class TypedDecisionSource(StrEnum):
    """Who supplies decision text; bound when a classifier is composed, never per call."""

    RUNTIME = "runtime"
    SYNTHETIC_FIXTURE = "synthetic_fixture"


class TypedDecisionMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"
    LIVE = "live"


class TypedDecisionKind(StrEnum):
    FRONT_DOOR = "front_door"
    RELEVANCE = "relevance"
    EXTRACTION_GATE = "extraction_gate"
    COMPACTION = "compaction"
    DEDUPE = "dedupe"
    SEMANTIC_KIND = "semantic_kind"
    VERIFY = "verify"


class TypedDecisionUnavailableReason(StrEnum):
    """Why a decision fell back to today's rules; content-free by construction."""

    DISABLED = "disabled"
    DATA_ROUTE_BLOCKED = "data_route_blocked"
    NO_CREDENTIAL = "no_credential"
    SECRET_BLOCKED = "secret_blocked"
    SENSITIVITY_BLOCKED = "sensitivity_blocked"
    BUDGET_DENIED = "budget_denied"
    TIMEOUT = "timeout"
    HTTP_ERROR = "http_error"
    SCHEMA_INVALID = "schema_invalid"


def typed_decision_source_permitted(
    route: TypedDecisionDataRoute, source: TypedDecisionSource
) -> bool:
    """Return whether ``route`` allows text from ``source`` to leave the machine."""

    if route is TypedDecisionDataRoute.SYNTHETIC_ONLY:
        return source is TypedDecisionSource.SYNTHETIC_FIXTURE
    return False
```

In `model_budget.py`, add a third enum member:

```python
class ModelTaskType(StrEnum):
    EPISODIC_CANDIDATE_EXTRACTION = "episodic_candidate_extraction"
    FRONTIER_TAKEOVER = "frontier_takeover"
    TYPED_DECISION = "typed_decision"
```

In `domain/__init__.py`, add this import block next to the other `from .x import (...)` blocks:

```python
from .typed_decisions import (
    TypedDecisionDataRoute,
    TypedDecisionKind,
    TypedDecisionMode,
    TypedDecisionSource,
    TypedDecisionUnavailableReason,
    typed_decision_source_permitted,
)
```

Then add the six names to `__all__` in alphabetical position. The `Typed…` names go after the existing `T…` entries, and `typed_decision_source_permitted` goes among the lowercase names at the end.

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_typed_decision_domain.py -v`
Expected: 3 passed

- [ ] **Step 6: Lint, type-check and commit**

```bash
uv run ruff format src/mnemo_memory/packages/domain tests/unit/test_typed_decision_domain.py
uv run ruff check --fix src/mnemo_memory/packages/domain tests/unit/test_typed_decision_domain.py
uv run mypy
git add src/mnemo_memory/packages/domain/typed_decisions.py tests/unit/test_typed_decision_domain.py
git commit -m "feat(domain): typed-decision vocabularies and budget task type" -- \
  src/mnemo_memory/packages/domain/typed_decisions.py \
  src/mnemo_memory/packages/domain/model_budget.py \
  src/mnemo_memory/packages/domain/__init__.py \
  tests/unit/test_typed_decision_domain.py \
  docs/implementation-status.md
```

---

### Task 2: Revise the cascade router into the typed-decision port

**Files:**
- Modify (full replacement): `src/mnemo_memory/packages/model_gateway/cascade_router.py`
- Modify: `src/mnemo_memory/packages/model_gateway/__init__.py`
- Test (full replacement): `tests/unit/test_cascade_router.py`

**Interfaces:**
- Consumes: nothing new.
- Produces (module `mnemo_memory.packages.model_gateway.cascade_router`):
  - `YES_NO_LABELS: tuple[str, str] = ("no", "yes")`
  - `AxisKind(StrEnum)`: `CHOICE = "choice"`, `YES_NO = "yes_no"`
  - `ClassifierAxis(name: str, instructions: str, allowed_labels: tuple[str, ...], score_weight: float, veto_score: float | None = None, *, kind: AxisKind = AxisKind.CHOICE, criteria: tuple[tuple[str, str], ...] = (), label_scores: tuple[float, ...] = ())`, with the method `.escalation_score_for(probabilities: Mapping[str, float]) -> float`
  - `ClassifierResult(axis_name: str, label: str, label_logprob: float, escalation_score: float, *, confidence: float | None = None)`
  - `logprob_from_probability(probability: float) -> float`, which returns `log(max(p, 1e-6))`
  - `PromptClassifier` (Protocol) with `async classify(axis, prompt) -> ClassifierResult`
  - `BatchPromptClassifier` (runtime-checkable Protocol) with `async classify_batch(axes: tuple[ClassifierAxis, ...], prompt: str) -> tuple[ClassifierResult, ...]`
  - `classify_axes(classifier, axes, prompt) -> tuple[ClassifierResult, ...]` (async). It uses one batch call when the classifier supports batching, else `gather`.
  - `CascadeRouteDecision(route: str, route_score: float, reason: str, results: tuple[ClassifierResult, ...])`
    - `route` is `"light"` or `"heavy"`
    - `reason` is `"light"`, `"threshold"` or `"veto:<axis>"`
  - `CascadeCommittee(axes, *, escalation_threshold)` with `async classify(prompt, classifier) -> CascadeRouteDecision`
  - `AxisRoutedClassifier(routes: Mapping[str, PromptClassifier])`, which implements both `classify` and `classify_batch`
  - `PrecomputedClassifier(results: Sequence[ClassifierResult])`, which implements `classify`
  - `CascadeRouterError(ValueError)`

- [ ] **Step 1: Write the failing tests (replace the whole file)**

```python
# tests/unit/test_cascade_router.py
import asyncio
import math

import pytest

from mnemo_memory.packages.model_gateway.cascade_router import (
    YES_NO_LABELS,
    AxisKind,
    AxisRoutedClassifier,
    CascadeCommittee,
    CascadeRouterError,
    ClassifierAxis,
    ClassifierResult,
    PrecomputedClassifier,
    logprob_from_probability,
)

AXES = (
    ClassifierAxis("domain", "classify domain fit", ("FIT", "MISS"), -0.25),
    ClassifierAxis("complexity", "classify complexity", ("EASY", "HARD"), 0.5),
    ClassifierAxis("risk", "classify stakes", ("LOW", "HIGH"), 0.6, veto_score=0.9),
    ClassifierAxis("tool_need", "classify tools", ("NO", "YES"), 0.4),
)


class StubClassifier:
    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores
        self.calls: list[str] = []

    async def classify(self, axis: ClassifierAxis, prompt: str) -> ClassifierResult:
        self.calls.append(axis.name)
        return ClassifierResult(axis.name, axis.allowed_labels[0], -0.1, self.scores[axis.name])


class BatchStub:
    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores
        self.batches: list[tuple[str, ...]] = []

    async def classify(self, axis: ClassifierAxis, prompt: str) -> ClassifierResult:
        raise AssertionError("batch-capable adapters must not be asked axis by axis")

    async def classify_batch(
        self, axes: tuple[ClassifierAxis, ...], prompt: str
    ) -> tuple[ClassifierResult, ...]:
        self.batches.append(tuple(axis.name for axis in axes))
        return tuple(
            ClassifierResult(axis.name, axis.allowed_labels[0], -0.1, self.scores[axis.name])
            for axis in axes
        )


def test_committee_consolidates_diverse_axis_scores_to_light() -> None:
    classifier = StubClassifier({"domain": 1.0, "complexity": 0.1, "risk": 0.0, "tool_need": 0.0})
    decision = asyncio.run(
        CascadeCommittee(AXES, escalation_threshold=0.4).classify("write a title", classifier)
    )
    assert decision.route == "light"
    assert decision.reason == "light"
    assert decision.route_score == pytest.approx(-0.2)
    assert classifier.calls == ["domain", "complexity", "risk", "tool_need"]


def test_committee_threshold_and_any_veto_escalate_to_heavy() -> None:
    threshold = StubClassifier({"domain": 0.0, "complexity": 0.9, "risk": 0.0, "tool_need": 0.0})
    decision = asyncio.run(
        CascadeCommittee(AXES, escalation_threshold=0.4).classify("prove theorem", threshold)
    )
    assert (decision.route, decision.reason) == ("heavy", "threshold")

    veto = StubClassifier({"domain": 1.0, "complexity": 0.0, "risk": 0.9, "tool_need": 0.0})
    decision = asyncio.run(
        CascadeCommittee(AXES, escalation_threshold=0.99).classify("medical dosage", veto)
    )
    assert (decision.route, decision.reason) == ("heavy", "veto:risk")


def test_committee_starts_all_classifiers_before_waiting_for_any_result() -> None:
    started: set[str] = set()
    release = asyncio.Event()

    class BlockingClassifier:
        async def classify(self, axis: ClassifierAxis, prompt: str) -> ClassifierResult:
            started.add(axis.name)
            await release.wait()
            return ClassifierResult(axis.name, axis.allowed_labels[0], -0.1, 0.0)

    async def exercise() -> None:
        task = asyncio.create_task(
            CascadeCommittee(AXES, escalation_threshold=0.4).classify("task", BlockingClassifier())
        )
        for _ in range(10):
            if len(started) == len(AXES):
                break
            await asyncio.sleep(0)
        assert started == {axis.name for axis in AXES}
        release.set()
        assert (await task).route == "light"

    asyncio.run(exercise())


def test_committee_rejects_unconstrained_or_mismatched_provider_output() -> None:
    class BadClassifier:
        async def classify(self, axis: ClassifierAxis, prompt: str) -> ClassifierResult:
            return ClassifierResult(axis.name, "unconstrained prose", -0.1, 0.0)

    with pytest.raises(CascadeRouterError, match="LABEL_INVALID"):
        asyncio.run(
            CascadeCommittee(AXES, escalation_threshold=0.4).classify("task", BadClassifier())
        )


def test_router_rejects_empty_prompt_and_invalid_configuration() -> None:
    with pytest.raises(CascadeRouterError, match="PROMPT_INVALID"):
        asyncio.run(
            CascadeCommittee(AXES, escalation_threshold=0.4).classify(" ", StubClassifier({}))
        )
    with pytest.raises(CascadeRouterError, match="THRESHOLD_INVALID"):
        CascadeCommittee(AXES, escalation_threshold=1.1)
    with pytest.raises(CascadeRouterError, match="AXIS_INVALID"):
        ClassifierAxis("risk", "prompt", ("ONE",), 1.0)
    with pytest.raises(CascadeRouterError, match="CLASSIFIER_RESULT_INVALID"):
        ClassifierResult("risk", "HIGH", float("nan"), 1.0)


def test_committee_asks_a_batch_capable_adapter_exactly_once() -> None:
    stub = BatchStub({"domain": 0.0, "complexity": 0.9, "risk": 0.0, "tool_need": 0.0})
    decision = asyncio.run(CascadeCommittee(AXES, escalation_threshold=0.4).classify("task", stub))
    assert decision.route == "heavy"
    assert stub.batches == [("domain", "complexity", "risk", "tool_need")]


def test_yes_no_axis_requires_no_yes_labels_and_scores_probability_of_yes() -> None:
    axis = ClassifierAxis("needs_x", "needs x", YES_NO_LABELS, 0.0, kind=AxisKind.YES_NO)
    assert axis.escalation_score_for({"yes": 0.93, "no": 0.07}) == pytest.approx(0.93)
    with pytest.raises(CascadeRouterError, match="AXIS_INVALID"):
        ClassifierAxis("needs_x", "needs x", ("yes", "no"), 0.0, kind=AxisKind.YES_NO)


def test_choice_axis_escalation_is_the_expected_label_score() -> None:
    axis = ClassifierAxis(
        "tool_need",
        "tools",
        ("none", "read_heavy", "edit"),
        0.4,
        label_scores=(0.0, 0.25, 0.75),
    )
    probabilities = {"none": 0.18, "read_heavy": 0.82, "edit": 0.0}
    assert axis.escalation_score_for(probabilities) == pytest.approx(0.205)
    assert ClassifierAxis("kind", "kind", ("a", "b"), 0.0).escalation_score_for({"a": 1.0}) == 0.0
    with pytest.raises(CascadeRouterError, match="AXIS_INVALID"):
        ClassifierAxis("t", "t", ("a", "b"), 0.0, label_scores=(0.0,))
    with pytest.raises(CascadeRouterError, match="AXIS_INVALID"):
        ClassifierAxis("t", "t", ("a", "b"), 0.0, label_scores=(0.0, 1.5))


def test_criteria_must_follow_allowed_labels() -> None:
    ClassifierAxis("c", "c", ("light", "heavy"), 0.0, criteria=(("light", "easy"), ("heavy", "hard")))
    with pytest.raises(CascadeRouterError, match="AXIS_INVALID"):
        ClassifierAxis(
            "c", "c", ("light", "heavy"), 0.0, criteria=(("heavy", "hard"), ("light", "easy"))
        )
    with pytest.raises(CascadeRouterError, match="AXIS_INVALID"):
        ClassifierAxis(
            "c", "c", ("light", "heavy"), 0.0, criteria=(("light", " "), ("heavy", "hard"))
        )


def test_logprob_from_probability_clamps_zero_and_rejects_invalid() -> None:
    assert logprob_from_probability(1.0) == 0.0
    assert logprob_from_probability(0.0) == pytest.approx(math.log(1e-6))
    for bad in (float("nan"), -0.1, 1.1):
        with pytest.raises(CascadeRouterError, match="PROBABILITY_INVALID"):
            logprob_from_probability(bad)


def test_result_confidence_must_be_a_probability() -> None:
    assert ClassifierResult("a", "x", -0.1, 0.5, confidence=0.6).confidence == 0.6
    with pytest.raises(CascadeRouterError, match="CLASSIFIER_RESULT_INVALID"):
        ClassifierResult("a", "x", -0.1, 0.5, confidence=1.2)


def test_axis_routed_classifier_batches_per_source_and_preserves_axis_order() -> None:
    remote = BatchStub({"complexity": 0.2, "tool_need": 0.25})
    local = StubClassifier({"domain": 1.0, "risk": 0.0})
    routed = AxisRoutedClassifier(
        {"complexity": remote, "tool_need": remote, "domain": local, "risk": local}
    )
    results = asyncio.run(routed.classify_batch(AXES, "task"))
    assert [result.axis_name for result in results] == [
        "domain",
        "complexity",
        "risk",
        "tool_need",
    ]
    assert remote.batches == [("complexity", "tool_need")]
    assert sorted(local.calls) == ["domain", "risk"]


def test_axis_routed_classifier_rejects_unrouted_axis() -> None:
    routed = AxisRoutedClassifier({"domain": StubClassifier({"domain": 0.0})})
    with pytest.raises(CascadeRouterError, match="AXIS_UNROUTED"):
        asyncio.run(routed.classify_batch(AXES, "task"))


def test_precomputed_classifier_serves_known_axes_and_rejects_unknown() -> None:
    known = ClassifierResult("complexity", "EASY", -0.1, 0.2)
    precomputed = PrecomputedClassifier((known,))
    assert asyncio.run(precomputed.classify(AXES[1], "task")) is known
    with pytest.raises(CascadeRouterError, match="AXIS_MISMATCH"):
        asyncio.run(precomputed.classify(AXES[0], "task"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_cascade_router.py -v`
Expected: FAIL with `ImportError: cannot import name 'YES_NO_LABELS'`

- [ ] **Step 3: Replace `cascade_router.py`**

```python
# src/mnemo_memory/packages/model_gateway/cascade_router.py
"""Provider-neutral concurrent pre-routing for Mnemo's own model tasks.

This module classifies a task before any answer generation. It does not call a model provider,
retain prompts, or intercept an agent's configured model endpoint. Composition supplies each
classifier adapter (a hosted typed classifier, deterministic rules, or a local model) and remains
responsible for score calibration. A ``heavy`` route is only a recommendation.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

YES_NO_LABELS: tuple[str, str] = ("no", "yes")
_MINIMUM_PROBABILITY = 1e-6


class CascadeRouterError(ValueError):
    """Stable, payload-free invalid-router-input error."""


class AxisKind(StrEnum):
    """How an axis is asked: one label from a closed list, or a yes/no probability."""

    CHOICE = "choice"
    YES_NO = "yes_no"


@dataclass(frozen=True, slots=True)
class ClassifierAxis:
    """One independent, fixed-label classification question."""

    name: str
    instructions: str
    allowed_labels: tuple[str, ...]
    score_weight: float
    veto_score: float | None = None
    kind: AxisKind = field(default=AxisKind.CHOICE, kw_only=True)
    criteria: tuple[tuple[str, str], ...] = field(default=(), kw_only=True)
    label_scores: tuple[float, ...] = field(default=(), kw_only=True)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise CascadeRouterError("MNEMO_CASCADE_AXIS_INVALID")
        if not isinstance(self.instructions, str) or not self.instructions.strip():
            raise CascadeRouterError("MNEMO_CASCADE_AXIS_INVALID")
        if (
            len(self.allowed_labels) < 2
            or any(not isinstance(label, str) or not label.strip() for label in self.allowed_labels)
            or len(set(self.allowed_labels)) != len(self.allowed_labels)
        ):
            raise CascadeRouterError("MNEMO_CASCADE_AXIS_INVALID")
        if (
            isinstance(self.score_weight, bool)
            or not isinstance(self.score_weight, (int, float))
            or not math.isfinite(self.score_weight)
        ):
            raise CascadeRouterError("MNEMO_CASCADE_AXIS_INVALID")
        if self.veto_score is not None and not _probability(self.veto_score):
            raise CascadeRouterError("MNEMO_CASCADE_AXIS_INVALID")
        if not isinstance(self.kind, AxisKind):
            raise CascadeRouterError("MNEMO_CASCADE_AXIS_INVALID")
        if self.kind is AxisKind.YES_NO and self.allowed_labels != YES_NO_LABELS:
            raise CascadeRouterError("MNEMO_CASCADE_AXIS_INVALID")
        if self.criteria and (
            tuple(label for label, _ in self.criteria) != self.allowed_labels
            or any(not isinstance(text, str) or not text.strip() for _, text in self.criteria)
        ):
            raise CascadeRouterError("MNEMO_CASCADE_AXIS_INVALID")
        if self.label_scores and (
            len(self.label_scores) != len(self.allowed_labels)
            or not all(_probability(score) for score in self.label_scores)
        ):
            raise CascadeRouterError("MNEMO_CASCADE_AXIS_INVALID")

    def escalation_score_for(self, probabilities: Mapping[str, float]) -> float:
        """Return p(yes) for a yes/no axis, else the expected label score (0 without scores)."""

        if self.kind is AxisKind.YES_NO:
            return _clamp(probabilities.get("yes", 0.0))
        if not self.label_scores:
            return 0.0
        return _clamp(
            sum(
                probabilities.get(label, 0.0) * score
                for label, score in zip(self.allowed_labels, self.label_scores, strict=True)
            )
        )


@dataclass(frozen=True, slots=True)
class ClassifierResult:
    """A constrained label, its log-probability, escalation score and optional confidence."""

    axis_name: str
    label: str
    label_logprob: float
    escalation_score: float
    confidence: float | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.axis_name, str)
            or not self.axis_name.strip()
            or not isinstance(self.label, str)
            or not self.label.strip()
            or isinstance(self.label_logprob, bool)
            or not isinstance(self.label_logprob, (int, float))
            or not math.isfinite(self.label_logprob)
            or not _probability(self.escalation_score)
            or (self.confidence is not None and not _probability(self.confidence))
        ):
            raise CascadeRouterError("MNEMO_CASCADE_CLASSIFIER_RESULT_INVALID")


def logprob_from_probability(probability: float) -> float:
    """Convert a provider probability to a finite log-probability (p = 0 maps to log(1e-6))."""

    if not _probability(probability):
        raise CascadeRouterError("MNEMO_CASCADE_PROBABILITY_INVALID")
    return math.log(max(float(probability), _MINIMUM_PROBABILITY))


class PromptClassifier(Protocol):
    """Adapter boundary for one constrained, fixed-label classifier request."""

    async def classify(self, axis: ClassifierAxis, prompt: str) -> ClassifierResult: ...


@runtime_checkable
class BatchPromptClassifier(Protocol):
    """Optional adapter capability: answer several axes about one text in one request."""

    async def classify_batch(
        self, axes: tuple[ClassifierAxis, ...], prompt: str
    ) -> tuple[ClassifierResult, ...]: ...


@dataclass(frozen=True, slots=True)
class CascadeRouteDecision:
    """Deterministic pre-route outcome; ``heavy`` is only a recommendation."""

    route: str
    route_score: float
    reason: str
    results: tuple[ClassifierResult, ...]


class CascadeCommittee:
    """Ask heterogeneous classifiers and consolidate their scores into light or heavy."""

    def __init__(self, axes: Sequence[ClassifierAxis], *, escalation_threshold: float) -> None:
        self._axes = tuple(axes)
        if len(self._axes) < 2 or len({axis.name for axis in self._axes}) != len(self._axes):
            raise CascadeRouterError("MNEMO_CASCADE_AXES_INVALID")
        if not _probability(escalation_threshold):
            raise CascadeRouterError("MNEMO_CASCADE_THRESHOLD_INVALID")
        self._escalation_threshold = float(escalation_threshold)

    async def classify(self, prompt: str, classifier: PromptClassifier) -> CascadeRouteDecision:
        """Ask every axis in one batch when supported, else concurrently through ``gather``."""

        if not isinstance(prompt, str) or not prompt.strip():
            raise CascadeRouterError("MNEMO_CASCADE_PROMPT_INVALID")
        results = await classify_axes(classifier, self._axes, prompt)
        by_axis = {result.axis_name: result for result in results}
        if len(by_axis) != len(results) or set(by_axis) != {axis.name for axis in self._axes}:
            raise CascadeRouterError("MNEMO_CASCADE_CLASSIFIER_AXIS_MISMATCH")
        for axis in self._axes:
            if by_axis[axis.name].label not in axis.allowed_labels:
                raise CascadeRouterError("MNEMO_CASCADE_CLASSIFIER_LABEL_INVALID")
        vetoed = next(
            (
                axis
                for axis in self._axes
                if axis.veto_score is not None
                and by_axis[axis.name].escalation_score >= axis.veto_score
            ),
            None,
        )
        route_score = sum(
            axis.score_weight * by_axis[axis.name].escalation_score for axis in self._axes
        )
        if vetoed is not None:
            return CascadeRouteDecision("heavy", route_score, f"veto:{vetoed.name}", results)
        route = "heavy" if route_score >= self._escalation_threshold else "light"
        reason = "threshold" if route == "heavy" else "light"
        return CascadeRouteDecision(route, route_score, reason, results)


async def classify_axes(
    classifier: PromptClassifier, axes: tuple[ClassifierAxis, ...], prompt: str
) -> tuple[ClassifierResult, ...]:
    """Use one ``classify_batch`` call when supported; never serialize independent axes."""

    if isinstance(classifier, BatchPromptClassifier):
        return tuple(await classifier.classify_batch(axes, prompt))
    return tuple(await asyncio.gather(*(classifier.classify(axis, prompt) for axis in axes)))


class AxisRoutedClassifier:
    """Send each axis to its configured classifier; one batch per classifier, all concurrent."""

    def __init__(self, routes: Mapping[str, PromptClassifier]) -> None:
        if not routes or any(not isinstance(name, str) or not name.strip() for name in routes):
            raise CascadeRouterError("MNEMO_CASCADE_ROUTES_INVALID")
        self._routes = dict(routes)

    async def classify(self, axis: ClassifierAxis, prompt: str) -> ClassifierResult:
        return (await self.classify_batch((axis,), prompt))[0]

    async def classify_batch(
        self, axes: tuple[ClassifierAxis, ...], prompt: str
    ) -> tuple[ClassifierResult, ...]:
        groups: dict[int, tuple[PromptClassifier, list[ClassifierAxis]]] = {}
        for axis in axes:
            target = self._routes.get(axis.name)
            if target is None:
                raise CascadeRouterError("MNEMO_CASCADE_AXIS_UNROUTED")
            groups.setdefault(id(target), (target, []))[1].append(axis)
        answered = await asyncio.gather(
            *(classify_axes(target, tuple(group), prompt) for target, group in groups.values())
        )
        by_axis = {result.axis_name: result for results in answered for result in results}
        if set(by_axis) != {axis.name for axis in axes}:
            raise CascadeRouterError("MNEMO_CASCADE_CLASSIFIER_AXIS_MISMATCH")
        return tuple(by_axis[axis.name] for axis in axes)


class PrecomputedClassifier:
    """Serve answers already obtained in one batched request, so no second request is made."""

    def __init__(self, results: Sequence[ClassifierResult]) -> None:
        self._by_axis = {result.axis_name: result for result in results}

    async def classify(self, axis: ClassifierAxis, prompt: str) -> ClassifierResult:
        result = self._by_axis.get(axis.name)
        if result is None:
            raise CascadeRouterError("MNEMO_CASCADE_CLASSIFIER_AXIS_MISMATCH")
        return result


def _probability(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and 0.0 <= float(value) <= 1.0
    )


def _clamp(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CascadeRouterError("MNEMO_CASCADE_PROBABILITY_INVALID")
    return min(1.0, max(0.0, float(value)))
```

In `model_gateway/__init__.py`, replace the staged `from .cascade_router import (...)` block and its `__all__` names with:

```python
from .cascade_router import (
    YES_NO_LABELS,
    AxisKind,
    AxisRoutedClassifier,
    BatchPromptClassifier,
    CascadeCommittee,
    CascadeRouteDecision,
    CascadeRouterError,
    ClassifierAxis,
    ClassifierResult,
    PrecomputedClassifier,
    PromptClassifier,
    classify_axes,
    logprob_from_probability,
)
```

Add every one of those names to `__all__`, keeping the file's existing sort order.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_cascade_router.py -v`
Expected: 14 passed

- [ ] **Step 5: Lint, type-check, check the architecture and commit**

```bash
uv run ruff format src/mnemo_memory/packages/model_gateway tests/unit/test_cascade_router.py
uv run ruff check --fix src/mnemo_memory/packages/model_gateway tests/unit/test_cascade_router.py
uv run mypy
npm run -s architecture:check
git commit -m "feat(model-gateway): typed-decision cascade port with light/heavy routes" -- \
  src/mnemo_memory/packages/model_gateway/cascade_router.py \
  src/mnemo_memory/packages/model_gateway/__init__.py \
  tests/unit/test_cascade_router.py
```

---

### Task 3: Local risk rule axis

**Files:**
- Create: `src/mnemo_memory/packages/model_gateway/rule_axes.py`
- Test: `tests/unit/test_rule_axes.py`

**Interfaces:**
- Consumes (from Task 2): `ClassifierAxis`, `ClassifierResult`, `CascadeRouterError` and `logprob_from_probability`.
- Produces:
  - `RISK_AXIS: ClassifierAxis` with name `"risk"`, labels `("low", "high")`, weight 0.0, `veto_score=1.0` and `label_scores=(0.0, 1.0)`
  - `matched_risk_tags(prompt: str) -> tuple[str, ...]`, whose tags come from `{"migration", "authorization", "deletion", "security", "external_write"}` (the long-horizon harness tag names)
  - `RiskTermClassifier`, which implements `PromptClassifier` for `RISK_AXIS` only

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_rule_axes.py
import asyncio

import pytest

from mnemo_memory.packages.model_gateway.cascade_router import (
    AxisRoutedClassifier,
    CascadeCommittee,
    CascadeRouterError,
    ClassifierAxis,
    ClassifierResult,
    PrecomputedClassifier,
)
from mnemo_memory.packages.model_gateway.rule_axes import (
    RISK_AXIS,
    RiskTermClassifier,
    matched_risk_tags,
)


@pytest.mark.parametrize(
    ("prompt", "tag"),
    [
        ("Write the schema migration for the events table", "migration"),
        ("Add authorization checks to the export endpoint", "authorization"),
        ("Delete the stale cache rows", "deletion"),
        ("Rotate the leaked credentials", "security"),
        ("Deploy the build to production", "external_write"),
    ],
)
def test_risk_terms_map_to_the_harness_tags(prompt: str, tag: str) -> None:
    assert tag in matched_risk_tags(prompt)


def test_everyday_prompts_are_low_risk() -> None:
    for prompt in (
        "Rename the helper parse_row to parse_record in utils.py",
        "Summarize the README",
        "What did we decide about the checkpoint token budget?",
    ):
        assert matched_risk_tags(prompt) == ()
        result = asyncio.run(RiskTermClassifier().classify(RISK_AXIS, prompt))
        assert (result.label, result.escalation_score) == ("low", 0.0)


def test_risk_veto_forces_heavy_even_when_every_other_axis_is_light() -> None:
    complexity = ClassifierAxis(
        "complexity", "how hard", ("light", "heavy"), 0.6, label_scores=(0.0, 1.0)
    )
    easy = ClassifierResult("complexity", "light", -0.01, 0.0)
    classifier = AxisRoutedClassifier(
        {"complexity": PrecomputedClassifier((easy,)), "risk": RiskTermClassifier()}
    )
    committee = CascadeCommittee((complexity, RISK_AXIS), escalation_threshold=0.5)
    decision = asyncio.run(committee.classify("Delete the old audit rows", classifier))
    assert (decision.route, decision.reason) == ("heavy", "veto:risk")
    assert asyncio.run(committee.classify("Rename a local variable", classifier)).route == "light"


def test_rule_classifier_answers_only_the_risk_axis() -> None:
    other = ClassifierAxis("complexity", "how hard", ("light", "heavy"), 0.6)
    with pytest.raises(CascadeRouterError, match="RULE_AXIS_UNSUPPORTED"):
        asyncio.run(RiskTermClassifier().classify(other, "anything"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_rule_axes.py -v`
Expected: FAIL with `ModuleNotFoundError: ... rule_axes`

- [ ] **Step 3: Write the implementation**

```python
# src/mnemo_memory/packages/model_gateway/rule_axes.py
"""Deterministic local rule axes for the cascade committee (no model, no network)."""

from __future__ import annotations

import re

from .cascade_router import (
    CascadeRouterError,
    ClassifierAxis,
    ClassifierResult,
    logprob_from_probability,
)

RISK_AXIS = ClassifierAxis(
    "risk",
    "Local rule: the task touches migrations, authorization, deletion, security "
    "or external writes",
    ("low", "high"),
    0.0,
    veto_score=1.0,
    label_scores=(0.0, 1.0),
)

# Tag names follow the long-horizon harness risk tags; patterns are Mnemo-owned term lists.
_RISK_TERMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "migration",
        re.compile(r"\b(?:migrat\w*|schema changes?|alter table|backfill\w*)\b", re.IGNORECASE),
    ),
    (
        "authorization",
        re.compile(
            r"\b(?:authoriz\w*|authenticat\w*|auth|oauth|permission\w*"
            r"|login|rbac|access control)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "deletion",
        re.compile(
            r"\b(?:delet\w*|purg\w*|truncat\w*|wipe\w*|drop (?:table|database|column))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "security",
        re.compile(
            r"\b(?:secret\w*|credential\w*|password\w*|api keys?|encrypt\w*|vulnerab\w*)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "external_write",
        re.compile(r"\b(?:deploy\w*|publish\w*|production|force[- ]push\w*)\b", re.IGNORECASE),
    ),
)


def matched_risk_tags(prompt: str) -> tuple[str, ...]:
    """Return the risk tags whose terms appear in ``prompt`` (content-free output)."""

    return tuple(tag for tag, pattern in _RISK_TERMS if pattern.search(prompt))


class RiskTermClassifier:
    """Answer only ``RISK_AXIS``; a match is certain ``high`` and vetoes a light verdict."""

    async def classify(self, axis: ClassifierAxis, prompt: str) -> ClassifierResult:
        if axis != RISK_AXIS:
            raise CascadeRouterError("MNEMO_CASCADE_RULE_AXIS_UNSUPPORTED")
        high = bool(matched_risk_tags(prompt))
        return ClassifierResult(
            axis.name,
            "high" if high else "low",
            logprob_from_probability(1.0),
            1.0 if high else 0.0,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_rule_axes.py -v`
Expected: 8 passed

- [ ] **Step 5: Lint, type-check and commit**

```bash
uv run ruff format src/mnemo_memory/packages/model_gateway/rule_axes.py tests/unit/test_rule_axes.py
uv run ruff check --fix src/mnemo_memory/packages/model_gateway/rule_axes.py tests/unit/test_rule_axes.py
uv run mypy
git add src/mnemo_memory/packages/model_gateway/rule_axes.py tests/unit/test_rule_axes.py
git commit -m "feat(model-gateway): deterministic risk veto axis" -- \
  src/mnemo_memory/packages/model_gateway/rule_axes.py tests/unit/test_rule_axes.py
```

---

### Task 4: Phase-1 question catalogue and thresholds

**Files:**
- Create: `src/mnemo_memory/packages/model_gateway/decision_axes.py`
- Test: `tests/unit/test_decision_axes.py`

**Interfaces:**
- Consumes: `ClassifierAxis`, `ClassifierResult`, `AxisKind`, `YES_NO_LABELS` and `CascadeCommittee` from Task 2; `RISK_AXIS` from Task 3; `EpisodicMemoryKind` from `mnemo_memory.packages.domain`.
- Produces:
  - Axes:
    - `NEEDS_LONG_TERM`, `NEEDS_STRUCTURE` (yes/no)
    - `COMPLEXITY` (`light`/`heavy`, weight 0.6)
    - `TOOL_NEED` (`none`/`read_heavy`/`edit`, weight 0.4)
    - `WORTH_REMEMBERING` (yes/no)
    - `EPISODIC_KIND` (labels are the five `EpisodicMemoryKind` values, in order)
  - Axis groups: `FRONT_DOOR_AXES = (NEEDS_LONG_TERM, NEEDS_STRUCTURE, COMPLEXITY, TOOL_NEED)` and `TIER_AXES = (COMPLEXITY, TOOL_NEED, RISK_AXIS)`
  - Committee: `TIER_ESCALATION_THRESHOLD = 0.5` and `tier_committee() -> CascadeCommittee`
  - Memory need: `NeedAnswer(StrEnum)` with `YES`/`NO`/`UNKNOWN`, `NEED_YES_AT = 0.7`, `NEED_NO_AT = 0.3`, and `need_from_result(result: ClassifierResult | None) -> NeedAnswer`
  - Relevance: `RELEVANCE_DROP_AT = 0.2`, `RELEVANCE_SNIPPET_CHARACTERS = 300`, `relevance_axis(index: int, snippet: str) -> ClassifierAxis` (name `helps_<index>`, index 0–31), and `should_drop_candidate(result: ClassifierResult | None) -> bool`
  - Extraction: `WORTH_SKIP_AT = 0.3` and `worth_extracting(result: ClassifierResult | None) -> bool`
  - Choice answers: `CHOICE_CONFIDENCE_BAR = 0.6` and `accepted_choice(result: ClassifierResult | None) -> str | None`
  - Hint: `HINT_TEXT: str` and `hint_eligible(route: str, tool_need: ClassifierResult | None) -> bool`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_decision_axes.py
import asyncio

import pytest

from mnemo_memory.packages.domain import EpisodicMemoryKind
from mnemo_memory.packages.model_gateway.cascade_router import (
    AxisKind,
    ClassifierResult,
    PrecomputedClassifier,
)
from mnemo_memory.packages.model_gateway.decision_axes import (
    EPISODIC_KIND,
    FRONT_DOOR_AXES,
    HINT_TEXT,
    TIER_AXES,
    NeedAnswer,
    accepted_choice,
    hint_eligible,
    need_from_result,
    relevance_axis,
    should_drop_candidate,
    tier_committee,
    worth_extracting,
)
from mnemo_memory.packages.model_gateway.rule_axes import RISK_AXIS


def _result(name: str, label: str, score: float, confidence: float | None = None) -> ClassifierResult:
    return ClassifierResult(name, label, -0.1, score, confidence=confidence)


def test_front_door_asks_two_needs_and_two_tier_axes() -> None:
    assert [axis.name for axis in FRONT_DOOR_AXES] == [
        "needs_long_term",
        "needs_structure",
        "complexity",
        "tool_need",
    ]
    assert [axis.kind for axis in FRONT_DOOR_AXES[:2]] == [AxisKind.YES_NO, AxisKind.YES_NO]
    assert [axis.name for axis in TIER_AXES] == ["complexity", "tool_need", "risk"]
    assert TIER_AXES[2] is RISK_AXIS


def test_every_catalogue_axis_fits_the_guard_text_limit() -> None:
    for axis in (*FRONT_DOOR_AXES, EPISODIC_KIND):
        assert len(axis.instructions) <= 400
        assert all(len(text) <= 400 for _, text in axis.criteria)


@pytest.mark.parametrize(
    ("probability", "expected"),
    [(0.7, NeedAnswer.YES), (0.69, NeedAnswer.UNKNOWN), (0.31, NeedAnswer.UNKNOWN), (0.3, NeedAnswer.NO)],
)
def test_need_thresholds(probability: float, expected: NeedAnswer) -> None:
    assert need_from_result(_result("needs_long_term", "yes", probability)) is expected


def test_missing_need_answer_is_unknown() -> None:
    assert need_from_result(None) is NeedAnswer.UNKNOWN


def test_relevance_axis_embeds_a_bounded_snippet() -> None:
    axis = relevance_axis(3, "  heading \n\n" + "x" * 500)
    assert axis.name == "helps_3"
    assert axis.kind is AxisKind.YES_NO
    assert axis.instructions.startswith("This stored note would help answer the request: heading x")
    assert len(axis.instructions) <= 400
    for bad_index in (-1, 32):
        with pytest.raises(ValueError):
            relevance_axis(bad_index, "note")
    with pytest.raises(ValueError):
        relevance_axis(0, "   ")


def test_relevance_drops_only_a_confident_no() -> None:
    assert should_drop_candidate(None) is False
    assert should_drop_candidate(_result("helps_0", "no", 0.2)) is True
    assert should_drop_candidate(_result("helps_0", "no", 0.21)) is False


def test_worth_extracting_skips_only_a_confident_no() -> None:
    assert worth_extracting(None) is True
    assert worth_extracting(_result("worth_remembering", "no", 0.3)) is False
    assert worth_extracting(_result("worth_remembering", "no", 0.31)) is True


def test_choice_answers_need_confidence() -> None:
    assert accepted_choice(None) is None
    assert accepted_choice(_result("tool_need", "read_heavy", 0.2)) is None
    assert accepted_choice(_result("tool_need", "read_heavy", 0.2, 0.59)) is None
    assert accepted_choice(_result("tool_need", "read_heavy", 0.2, 0.6)) == "read_heavy"


def test_hint_only_for_light_reading_heavy_tasks() -> None:
    reading = _result("tool_need", "read_heavy", 0.2, 0.7)
    assert hint_eligible("light", reading) is True
    assert hint_eligible("heavy", reading) is False
    assert hint_eligible("light", _result("tool_need", "edit", 0.7, 0.7)) is False
    assert hint_eligible("light", _result("tool_need", "read_heavy", 0.2, 0.5)) is False
    assert HINT_TEXT == "Mnemo: light, reading-heavy task; a Haiku subagent could do the reading."
    assert (len(HINT_TEXT) + 3) // 4 <= 40


def test_tier_committee_uses_the_spec_weights_and_threshold() -> None:
    low_risk = _result("risk", "low", 0.0)
    heavy = PrecomputedClassifier(
        (_result("complexity", "heavy", 0.8), _result("tool_need", "read_heavy", 0.205), low_risk)
    )
    light = PrecomputedClassifier(
        (_result("complexity", "light", 0.2), _result("tool_need", "read_heavy", 0.205), low_risk)
    )
    assert asyncio.run(tier_committee().classify("task", heavy)).route == "heavy"  # 0.562
    assert asyncio.run(tier_committee().classify("task", light)).route == "light"  # 0.202


def test_episodic_kind_labels_match_the_domain() -> None:
    assert EPISODIC_KIND.allowed_labels == tuple(kind.value for kind in EpisodicMemoryKind)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_decision_axes.py -v`
Expected: FAIL with `ModuleNotFoundError: ... decision_axes`

- [ ] **Step 3: Write the implementation**

```python
# src/mnemo_memory/packages/model_gateway/decision_axes.py
"""Phase-1 typed-decision questions, thresholds and committee (spec §6).

Every threshold is a starting value, tuned on synthetic fixtures and recorded with the pinned
model version. Instructions stay short because they dominate billed input tokens.
"""

from __future__ import annotations

from enum import StrEnum

from mnemo_memory.packages.domain import EpisodicMemoryKind

from .cascade_router import (
    YES_NO_LABELS,
    AxisKind,
    CascadeCommittee,
    ClassifierAxis,
    ClassifierResult,
)
from .rule_axes import RISK_AXIS

NEED_YES_AT = 0.7
NEED_NO_AT = 0.3
RELEVANCE_DROP_AT = 0.2
RELEVANCE_SNIPPET_CHARACTERS = 300
WORTH_SKIP_AT = 0.3
CHOICE_CONFIDENCE_BAR = 0.6
TIER_ESCALATION_THRESHOLD = 0.5
_MAXIMUM_RELEVANCE_AXES = 32
_RELEVANCE_PREFIX = "This stored note would help answer the request: "

HINT_TEXT = "Mnemo: light, reading-heavy task; a Haiku subagent could do the reading."

NEEDS_LONG_TERM = ClassifierAxis(
    "needs_long_term",
    "Answering needs stored memory from earlier sessions (past decisions, notes, history) "
    "that is not in the current message",
    YES_NO_LABELS,
    0.0,
    kind=AxisKind.YES_NO,
)
NEEDS_STRUCTURE = ClassifierAxis(
    "needs_structure",
    "Answering needs knowledge of source code or database structure "
    "(files, symbols, migrations, lineage)",
    YES_NO_LABELS,
    0.0,
    kind=AxisKind.YES_NO,
)
COMPLEXITY = ClassifierAxis(
    "complexity",
    "How hard is this task for an AI coding assistant",
    ("light", "heavy"),
    0.6,
    criteria=(
        ("light", "Simple lookup, rename, formatting or summarizing"),
        ("heavy", "Needs reasoning across several facts, design judgement, or risky changes"),
    ),
    label_scores=(0.0, 1.0),
)
TOOL_NEED = ClassifierAxis(
    "tool_need",
    "What kind of tool work the task needs",
    ("none", "read_heavy", "edit"),
    0.4,
    criteria=(
        ("none", "Answerable without tools"),
        ("read_heavy", "Mostly reading or searching files"),
        ("edit", "Changing files or running commands"),
    ),
    label_scores=(0.0, 0.25, 0.75),
)
WORTH_REMEMBERING = ClassifierAxis(
    "worth_remembering",
    "This event records a durable decision, failure, outcome, lesson or preference worth "
    "remembering in a later session",
    YES_NO_LABELS,
    0.0,
    kind=AxisKind.YES_NO,
)
EPISODIC_KIND = ClassifierAxis(
    "episodic_kind",
    "Which kind of memory this event is",
    tuple(kind.value for kind in EpisodicMemoryKind),
    0.0,
    criteria=(
        ("decision", "A choice that was made"),
        ("failure", "Something that broke, or an approach that did not work"),
        ("outcome", "A result that was achieved, shipped or fixed"),
        ("lesson", "Something learned for next time"),
        ("preference", "How someone likes things done"),
    ),
)

FRONT_DOOR_AXES = (NEEDS_LONG_TERM, NEEDS_STRUCTURE, COMPLEXITY, TOOL_NEED)
TIER_AXES = (COMPLEXITY, TOOL_NEED, RISK_AXIS)


class NeedAnswer(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


def tier_committee() -> CascadeCommittee:
    return CascadeCommittee(TIER_AXES, escalation_threshold=TIER_ESCALATION_THRESHOLD)


def need_from_result(result: ClassifierResult | None) -> NeedAnswer:
    """Map p(yes) to yes/no/unknown; a missing answer is unknown, which means lazy pull."""

    if result is None:
        return NeedAnswer.UNKNOWN
    if result.escalation_score >= NEED_YES_AT:
        return NeedAnswer.YES
    if result.escalation_score <= NEED_NO_AT:
        return NeedAnswer.NO
    return NeedAnswer.UNKNOWN


def relevance_axis(index: int, snippet: str) -> ClassifierAxis:
    """Build one per-candidate yes/no question carrying a bounded snippet."""

    if isinstance(index, bool) or not 0 <= index < _MAXIMUM_RELEVANCE_AXES:
        raise ValueError("relevance axis index is out of range")
    text = " ".join(snippet.split())[:RELEVANCE_SNIPPET_CHARACTERS]
    if not text:
        raise ValueError("relevance snippet is empty")
    return ClassifierAxis(
        f"helps_{index}", _RELEVANCE_PREFIX + text, YES_NO_LABELS, 0.0, kind=AxisKind.YES_NO
    )


def should_drop_candidate(result: ClassifierResult | None) -> bool:
    """Drop only on a confident no; keep is the safe side."""

    return result is not None and result.escalation_score <= RELEVANCE_DROP_AT


def worth_extracting(result: ClassifierResult | None) -> bool:
    """Skip extraction only on a confident no; an unavailable answer runs the model."""

    return result is None or result.escalation_score > WORTH_SKIP_AT


def accepted_choice(result: ClassifierResult | None) -> str | None:
    """Use a choice label only when its confidence reaches the bar."""

    if result is None or result.confidence is None or result.confidence < CHOICE_CONFIDENCE_BAR:
        return None
    return result.label


def hint_eligible(route: str, tool_need: ClassifierResult | None) -> bool:
    """Hint only light, reading-heavy tasks; delegating tiny or hard tasks wastes tokens."""

    return route == "light" and accepted_choice(tool_need) == "read_heavy"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_decision_axes.py -v`
Expected: 14 passed

- [ ] **Step 5: Lint, type-check and commit**

```bash
uv run ruff format src/mnemo_memory/packages/model_gateway/decision_axes.py tests/unit/test_decision_axes.py
uv run ruff check --fix src/mnemo_memory/packages/model_gateway/decision_axes.py tests/unit/test_decision_axes.py
uv run mypy
git add src/mnemo_memory/packages/model_gateway/decision_axes.py tests/unit/test_decision_axes.py
git commit -m "feat(model-gateway): phase-1 typed-decision question catalogue" -- \
  src/mnemo_memory/packages/model_gateway/decision_axes.py tests/unit/test_decision_axes.py
```

---

### Task 5: The guard

**Files:**
- Create: `src/mnemo_memory/packages/model_gateway/typed_decisions.py`
- Test: `tests/unit/test_typed_decision_guard.py`

**Interfaces:**
- Consumes: the domain names from Task 1; `ClassifierAxis`, `ClassifierResult`, `CascadeCommittee`, `CascadeRouteDecision`, `CascadeRouterError` and `PromptClassifier` from Task 2; `contains_high_confidence_secret` from `mnemo_memory.packages.policy.content_safety`.
- Produces:
  - Limits and bounding: `MAXIMUM_TEXT_CHARACTERS = 512`, `MAXIMUM_AXIS_TEXT_CHARACTERS = 400`, `MAXIMUM_AXES = 32`, and `bounded_decision_text(text: str) -> str`
  - Adapter errors: `TypedDecisionAdapterError(reason: TypedDecisionUnavailableReason)`, raised by adapters
  - `AdapterAnswer(results: tuple[ClassifierResult, ...], model_version: str, input_tokens: int)`
  - `TypedDecisionAdapter` (Protocol) with `provider_id`, `model_id`, and `answer(axes, text, *, timeout_seconds: float) -> AdapterAnswer` (synchronous; run in a worker thread)
  - Telemetry: `TypedDecisionRecord(source: str, axis_count: int, outcome: str, duration_ms: int, model_version: str | None, input_tokens: int)` and `TypedDecisionRecorder` (Protocol) with `record(record) -> None`
  - `TypedDecisionOutcome(results, unavailable_reason, duration_ms, model_version)` with the property `.available`
  - `TypedDecisionUnavailable(reason)`, raised only by `classify_batch`/`classify`
  - The guard: `GuardedTypedDecisionClassifier(adapter: TypedDecisionAdapter | None, *, data_route, source, budget, workspace_id, reservation, deadline_seconds, recorder=None, clock=time.perf_counter)`, with:
    - `async ask(axes, text, *, sensitivity=Sensitivity.NORMAL) -> TypedDecisionOutcome` (never raises)
    - `async classify_batch(axes, prompt)` and `async classify(axis, prompt)`
  - Tier: `TierDecision(route: str, reason: str, decision: CascadeRouteDecision | None)` and `async decide_tier(committee, classifier, prompt) -> TierDecision`. Any failure gives `("heavy", "unavailable:<reason>", None)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_typed_decision_guard.py
import asyncio
import time
from uuid import UUID

import pytest

from mnemo_memory.packages.domain import (
    ModelBudgetDenied,
    ModelBudgetReservation,
    ModelTaskType,
    Sensitivity,
    TypedDecisionDataRoute,
    TypedDecisionSource,
    TypedDecisionUnavailableReason,
    WorkspaceId,
)
from mnemo_memory.packages.model_gateway.cascade_router import (
    YES_NO_LABELS,
    AxisKind,
    AxisRoutedClassifier,
    CascadeCommittee,
    ClassifierAxis,
    ClassifierResult,
)
from mnemo_memory.packages.model_gateway.rule_axes import RISK_AXIS, RiskTermClassifier
from mnemo_memory.packages.model_gateway.typed_decisions import (
    AdapterAnswer,
    GuardedTypedDecisionClassifier,
    TypedDecisionAdapterError,
    TypedDecisionRecord,
    TypedDecisionUnavailable,
    bounded_decision_text,
    decide_tier,
)

Reason = TypedDecisionUnavailableReason
NEED = ClassifierAxis("needs_long_term", "needs memory", YES_NO_LABELS, 0.0, kind=AxisKind.YES_NO)
TIER = ClassifierAxis(
    "complexity", "how hard", ("light", "heavy"), 0.6, label_scores=(0.0, 1.0)
)
WORKSPACE = WorkspaceId(UUID(int=7))
RESERVATION = ModelBudgetReservation(input_tokens=1_000, output_tokens=1, cost_microusd=0)


class FakeAdapter:
    provider_id = "fake"
    model_id = "fake-1"

    def __init__(
        self,
        *,
        answer: AdapterAnswer | None = None,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.calls: list[tuple[tuple[str, ...], str, float]] = []
        self._answer = answer
        self._error = error
        self._delay = delay

    def answer(
        self, axes: tuple[ClassifierAxis, ...], text: str, *, timeout_seconds: float
    ) -> AdapterAnswer:
        self.calls.append((tuple(axis.name for axis in axes), text, timeout_seconds))
        if self._delay:
            time.sleep(self._delay)
        if self._error is not None:
            raise self._error
        if self._answer is not None:
            return self._answer
        return AdapterAnswer(
            tuple(ClassifierResult(axis.name, axis.allowed_labels[-1], -0.1, 0.9) for axis in axes),
            "fake-1",
            12,
        )


class CountingBudget:
    def __init__(self, *, deny: bool = False) -> None:
        self.calls = 0
        self.deny = deny

    def reserve(
        self, workspace_id: WorkspaceId, task_type: ModelTaskType, reservation: ModelBudgetReservation
    ) -> None:
        assert task_type is ModelTaskType.TYPED_DECISION
        self.calls += 1
        if self.deny:
            raise ModelBudgetDenied("denied")


class ListRecorder:
    def __init__(self) -> None:
        self.records: list[TypedDecisionRecord] = []

    def record(self, record: TypedDecisionRecord) -> None:
        self.records.append(record)


def _guard(
    adapter: FakeAdapter | None,
    *,
    source: TypedDecisionSource = TypedDecisionSource.SYNTHETIC_FIXTURE,
    budget: CountingBudget | None = None,
    recorder: ListRecorder | None = None,
    deadline: float = 1.0,
) -> GuardedTypedDecisionClassifier:
    return GuardedTypedDecisionClassifier(
        adapter,
        data_route=TypedDecisionDataRoute.SYNTHETIC_ONLY,
        source=source,
        budget=budget or CountingBudget(),
        workspace_id=WORKSPACE,
        reservation=RESERVATION,
        deadline_seconds=deadline,
        recorder=recorder,
    )


def test_runtime_text_is_blocked_before_any_other_work() -> None:
    adapter, budget, recorder = FakeAdapter(), CountingBudget(), ListRecorder()
    guard = _guard(adapter, source=TypedDecisionSource.RUNTIME, budget=budget, recorder=recorder)
    outcome = asyncio.run(guard.ask((NEED,), "api_key = abcdefghijklmnop1234"))
    assert outcome.unavailable_reason is Reason.DATA_ROUTE_BLOCKED
    assert outcome.results == ()
    assert adapter.calls == [] and budget.calls == 0
    assert [record.outcome for record in recorder.records] == ["data_route_blocked"]


def test_missing_credential_is_reported_without_budget_or_network() -> None:
    budget = CountingBudget()
    outcome = asyncio.run(_guard(None, budget=budget).ask((NEED,), "what did we decide?"))
    assert outcome.unavailable_reason is Reason.NO_CREDENTIAL
    assert budget.calls == 0


def test_secret_in_text_is_never_sent() -> None:
    adapter = FakeAdapter()
    outcome = asyncio.run(_guard(adapter).ask((NEED,), "api_key = abcdefghijklmnop1234"))
    assert outcome.unavailable_reason is Reason.SECRET_BLOCKED
    assert adapter.calls == []


def test_secret_in_axis_text_is_never_sent() -> None:
    adapter = FakeAdapter()
    leaky = ClassifierAxis(
        "helps_0",
        "This stored note would help answer the request: sk-abcdefghijklmnopqrstuvwxyz",
        YES_NO_LABELS,
        0.0,
        kind=AxisKind.YES_NO,
    )
    outcome = asyncio.run(_guard(adapter).ask((leaky,), "resume the task"))
    assert outcome.unavailable_reason is Reason.SECRET_BLOCKED
    assert adapter.calls == []


def test_non_normal_sensitivity_is_never_sent() -> None:
    adapter = FakeAdapter()
    outcome = asyncio.run(
        _guard(adapter).ask((NEED,), "resume", sensitivity=Sensitivity.PERSONAL)
    )
    assert outcome.unavailable_reason is Reason.SENSITIVITY_BLOCKED
    assert adapter.calls == []


def test_long_text_is_bounded_head_and_tail() -> None:
    adapter = FakeAdapter()
    asyncio.run(_guard(adapter).ask((NEED,), "A" * 300 + "B" * 300))
    sent = adapter.calls[0][1]
    assert sent == "A" * 256 + "\n" + "B" * 255
    assert bounded_decision_text("  short  ") == "short"


def test_malformed_requests_are_schema_invalid_without_network() -> None:
    adapter = FakeAdapter()
    guard = _guard(adapter)
    oversized = ClassifierAxis("big", "x" * 401, YES_NO_LABELS, 0.0, kind=AxisKind.YES_NO)
    for axes, text in (((oversized,), "t"), ((NEED, NEED), "t"), ((), "t"), ((NEED,), "  ")):
        outcome = asyncio.run(guard.ask(axes, text))
        assert outcome.unavailable_reason is Reason.SCHEMA_INVALID
    assert adapter.calls == []


def test_budget_denial_blocks_the_call() -> None:
    adapter = FakeAdapter()
    outcome = asyncio.run(_guard(adapter, budget=CountingBudget(deny=True)).ask((NEED,), "t"))
    assert outcome.unavailable_reason is Reason.BUDGET_DENIED
    assert adapter.calls == []


def test_deadline_returns_promptly_while_adapter_still_runs() -> None:
    adapter = FakeAdapter(delay=0.5)
    started = time.perf_counter()
    outcome = asyncio.run(_guard(adapter, deadline=0.05).ask((NEED,), "t"))
    assert outcome.unavailable_reason is Reason.TIMEOUT
    assert outcome.duration_ms < 400  # ask() returned long before the 0.5 s adapter finished
    assert adapter.calls[0][2] == 0.05
    assert time.perf_counter() - started >= 0.05


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (TypedDecisionAdapterError(Reason.HTTP_ERROR), Reason.HTTP_ERROR),
        (TypedDecisionAdapterError(Reason.SCHEMA_INVALID), Reason.SCHEMA_INVALID),
        (TimeoutError(), Reason.TIMEOUT),
        (RuntimeError("boom"), Reason.HTTP_ERROR),
    ],
)
def test_adapter_errors_map_to_closed_reasons(error: Exception, reason: Reason) -> None:
    outcome = asyncio.run(_guard(FakeAdapter(error=error)).ask((NEED,), "t"))
    assert outcome.unavailable_reason is reason


def test_answers_outside_the_contract_are_schema_invalid() -> None:
    wrong_label = AdapterAnswer((ClassifierResult("needs_long_term", "maybe", -0.1, 0.5),), "v", 1)
    wrong_axis = AdapterAnswer((ClassifierResult("other", "yes", -0.1, 0.5),), "v", 1)
    for answer in (wrong_label, wrong_axis):
        outcome = asyncio.run(_guard(FakeAdapter(answer=answer)).ask((NEED,), "t"))
        assert outcome.unavailable_reason is Reason.SCHEMA_INVALID


def test_successful_answer_returns_results_and_content_free_telemetry() -> None:
    recorder = ListRecorder()
    outcome = asyncio.run(_guard(FakeAdapter(), recorder=recorder).ask((NEED, TIER), "secretless"))
    assert outcome.available
    assert [result.axis_name for result in outcome.results] == ["needs_long_term", "complexity"]
    assert outcome.model_version == "fake-1"
    (record,) = recorder.records
    assert (record.source, record.axis_count, record.outcome, record.input_tokens) == (
        "synthetic_fixture",
        2,
        "answered",
        12,
    )
    assert "secretless" not in repr(record)


def test_a_failing_recorder_never_breaks_the_caller() -> None:
    class BrokenRecorder:
        def record(self, record: TypedDecisionRecord) -> None:
            raise RuntimeError("telemetry down")

    guard = GuardedTypedDecisionClassifier(
        FakeAdapter(),
        data_route=TypedDecisionDataRoute.SYNTHETIC_ONLY,
        source=TypedDecisionSource.SYNTHETIC_FIXTURE,
        budget=CountingBudget(),
        workspace_id=WORKSPACE,
        reservation=RESERVATION,
        deadline_seconds=1.0,
        recorder=BrokenRecorder(),
    )
    assert asyncio.run(guard.ask((NEED,), "t")).available


def test_classify_batch_raises_typed_unavailable_for_committees() -> None:
    guard = _guard(FakeAdapter(), source=TypedDecisionSource.RUNTIME)
    with pytest.raises(TypedDecisionUnavailable) as caught:
        asyncio.run(guard.classify_batch((NEED,), "t"))
    assert caught.value.reason is Reason.DATA_ROUTE_BLOCKED


def test_decide_tier_falls_back_to_heavy_when_unavailable() -> None:
    committee = CascadeCommittee((TIER, RISK_AXIS), escalation_threshold=0.5)
    blocked = _guard(FakeAdapter(), source=TypedDecisionSource.RUNTIME)
    classifier = AxisRoutedClassifier({"complexity": blocked, "risk": RiskTermClassifier()})
    tier = asyncio.run(decide_tier(committee, classifier, "rename a variable"))
    assert (tier.route, tier.reason, tier.decision) == (
        "heavy",
        "unavailable:data_route_blocked",
        None,
    )

    light_answer = AdapterAnswer((ClassifierResult("complexity", "light", -0.1, 0.1),), "v", 1)
    allowed = _guard(FakeAdapter(answer=light_answer))
    classifier = AxisRoutedClassifier({"complexity": allowed, "risk": RiskTermClassifier()})
    assert asyncio.run(decide_tier(committee, classifier, "rename a variable")).route == "light"


def test_guard_rejects_invalid_configuration() -> None:
    for deadline in (0.0, 31.0):
        with pytest.raises(ValueError):
            _guard(FakeAdapter(), deadline=deadline)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_typed_decision_guard.py -v`
Expected: FAIL with `ModuleNotFoundError: ... typed_decisions`

- [ ] **Step 3: Write the implementation**

```python
# src/mnemo_memory/packages/model_gateway/typed_decisions.py
"""Guarded boundary for hosted typed-decision classifiers (spec §5.2, ADR 0049).

Checks run in a fixed order and each fails closed to one ``unavailable`` reason: data route,
credential, request shape, secret scan, sensitivity, size cap, budget, deadline, label check.
The guard never raises from ``ask`` and never records decision text.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from mnemo_memory.packages.domain import (
    ModelBudgetReservation,
    ModelBudgetReservationPort,
    ModelTaskType,
    Sensitivity,
    TypedDecisionDataRoute,
    TypedDecisionSource,
    TypedDecisionUnavailableReason,
    WorkspaceId,
    typed_decision_source_permitted,
)
from mnemo_memory.packages.policy.content_safety import contains_high_confidence_secret

from .cascade_router import (
    CascadeCommittee,
    CascadeRouteDecision,
    CascadeRouterError,
    ClassifierAxis,
    ClassifierResult,
    PromptClassifier,
)

MAXIMUM_TEXT_CHARACTERS = 512
MAXIMUM_AXIS_TEXT_CHARACTERS = 400
MAXIMUM_AXES = 32
_TEXT_HEAD_CHARACTERS = 256
_TEXT_TAIL_CHARACTERS = 255
_MAXIMUM_DEADLINE_SECONDS = 30.0
_MAXIMUM_MODEL_VERSION_LENGTH = 128

Reason = TypedDecisionUnavailableReason


class TypedDecisionAdapterError(RuntimeError):
    """Payload-free adapter failure that carries one closed unavailable reason."""

    def __init__(self, reason: TypedDecisionUnavailableReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True, slots=True)
class AdapterAnswer:
    """One provider response already parsed into constrained axis results."""

    results: tuple[ClassifierResult, ...]
    model_version: str
    input_tokens: int


class TypedDecisionAdapter(Protocol):
    """A classifier that answers several axes about one text in one synchronous request."""

    @property
    def provider_id(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    def answer(
        self, axes: tuple[ClassifierAxis, ...], text: str, *, timeout_seconds: float
    ) -> AdapterAnswer: ...


@dataclass(frozen=True, slots=True)
class TypedDecisionRecord:
    """Content-free telemetry for one guarded request: never text, labels or scores."""

    source: str
    axis_count: int
    outcome: str
    duration_ms: int
    model_version: str | None
    input_tokens: int


class TypedDecisionRecorder(Protocol):
    def record(self, record: TypedDecisionRecord) -> None: ...


@dataclass(frozen=True, slots=True)
class TypedDecisionOutcome:
    """Answers in axis order, or no answers and exactly one unavailable reason."""

    results: tuple[ClassifierResult, ...]
    unavailable_reason: TypedDecisionUnavailableReason | None
    duration_ms: int
    model_version: str | None

    @property
    def available(self) -> bool:
        return self.unavailable_reason is None


class TypedDecisionUnavailable(RuntimeError):
    """Raised only through the committee-facing ``classify`` methods."""

    def __init__(self, reason: TypedDecisionUnavailableReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


def bounded_decision_text(text: str) -> str:
    """Keep the head and tail of long text, matching the hook's 512-character routing bound."""

    value = text.strip()
    if len(value) <= MAXIMUM_TEXT_CHARACTERS:
        return value
    return (
        value[:_TEXT_HEAD_CHARACTERS].rstrip() + "\n" + value[-_TEXT_TAIL_CHARACTERS:].lstrip()
    )


class GuardedTypedDecisionClassifier:
    """Wrap one adapter with data-route, secret, sensitivity, budget and deadline policy."""

    def __init__(
        self,
        adapter: TypedDecisionAdapter | None,
        *,
        data_route: TypedDecisionDataRoute,
        source: TypedDecisionSource,
        budget: ModelBudgetReservationPort,
        workspace_id: WorkspaceId,
        reservation: ModelBudgetReservation,
        deadline_seconds: float,
        recorder: TypedDecisionRecorder | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if not isinstance(data_route, TypedDecisionDataRoute) or not isinstance(
            source, TypedDecisionSource
        ):
            raise TypeError("typed decision route and source must be closed values")
        if not isinstance(workspace_id, WorkspaceId) or not isinstance(
            reservation, ModelBudgetReservation
        ):
            raise TypeError("typed decision budget scope is invalid")
        if (
            isinstance(deadline_seconds, bool)
            or not isinstance(deadline_seconds, (int, float))
            or not math.isfinite(deadline_seconds)
            or not 0.0 < deadline_seconds <= _MAXIMUM_DEADLINE_SECONDS
        ):
            raise ValueError("typed decision deadline must be within (0, 30] seconds")
        self._adapter = adapter
        self._data_route = data_route
        self._source = source
        self._budget = budget
        self._workspace_id = workspace_id
        self._reservation = reservation
        self._deadline = float(deadline_seconds)
        self._recorder = recorder
        self._clock = clock

    async def ask(
        self,
        axes: Sequence[ClassifierAxis],
        text: str,
        *,
        sensitivity: Sensitivity = Sensitivity.NORMAL,
    ) -> TypedDecisionOutcome:
        """Return answers in axis order or one closed unavailable reason; never raises."""

        started = self._clock()
        axis_tuple = tuple(axes)
        try:
            reason, answer = await self._ask(axis_tuple, text, sensitivity)
        except Exception:
            reason, answer = Reason.HTTP_ERROR, None
        duration_ms = max(0, round((self._clock() - started) * 1_000))
        outcome = TypedDecisionOutcome(
            () if answer is None else answer.results,
            reason,
            duration_ms,
            None if answer is None else answer.model_version,
        )
        self._record(len(axis_tuple), outcome, 0 if answer is None else answer.input_tokens)
        return outcome

    async def classify_batch(
        self, axes: tuple[ClassifierAxis, ...], prompt: str
    ) -> tuple[ClassifierResult, ...]:
        outcome = await self.ask(axes, prompt)
        if outcome.unavailable_reason is not None:
            raise TypedDecisionUnavailable(outcome.unavailable_reason)
        return outcome.results

    async def classify(self, axis: ClassifierAxis, prompt: str) -> ClassifierResult:
        return (await self.classify_batch((axis,), prompt))[0]

    async def _ask(
        self, axes: tuple[ClassifierAxis, ...], text: object, sensitivity: Sensitivity
    ) -> tuple[TypedDecisionUnavailableReason | None, AdapterAnswer | None]:
        adapter = self._adapter
        if not typed_decision_source_permitted(self._data_route, self._source):
            return Reason.DATA_ROUTE_BLOCKED, None
        if adapter is None:
            return Reason.NO_CREDENTIAL, None
        if not isinstance(text, str) or not _request_shape_valid(axes, text):
            return Reason.SCHEMA_INVALID, None
        axis_texts = tuple(
            value
            for axis in axes
            for value in (axis.instructions, *(description for _, description in axis.criteria))
        )
        if contains_high_confidence_secret(text, *axis_texts):
            return Reason.SECRET_BLOCKED, None
        if sensitivity is not Sensitivity.NORMAL:
            return Reason.SENSITIVITY_BLOCKED, None
        if any(len(value) > MAXIMUM_AXIS_TEXT_CHARACTERS for value in axis_texts):
            return Reason.SCHEMA_INVALID, None
        bounded = bounded_decision_text(text)
        try:
            self._budget.reserve(
                self._workspace_id, ModelTaskType.TYPED_DECISION, self._reservation
            )
        except Exception:
            return Reason.BUDGET_DENIED, None
        try:
            answer = await asyncio.wait_for(
                asyncio.to_thread(adapter.answer, axes, bounded, timeout_seconds=self._deadline),
                timeout=self._deadline,
            )
        except TypedDecisionAdapterError as error:
            return error.reason, None
        except TimeoutError:
            return Reason.TIMEOUT, None
        except Exception:
            return Reason.HTTP_ERROR, None
        if not _answer_matches(axes, answer):
            return Reason.SCHEMA_INVALID, None
        return None, answer

    def _record(self, axis_count: int, outcome: TypedDecisionOutcome, input_tokens: int) -> None:
        recorder = self._recorder
        if recorder is None:
            return
        reason = outcome.unavailable_reason
        record = TypedDecisionRecord(
            self._source.value,
            axis_count,
            "answered" if reason is None else reason.value,
            outcome.duration_ms,
            outcome.model_version,
            input_tokens,
        )
        with contextlib.suppress(Exception):
            recorder.record(record)


@dataclass(frozen=True, slots=True)
class TierDecision:
    """Committee verdict, or ``heavy`` (the safe side) with an ``unavailable:`` reason."""

    route: str
    reason: str
    decision: CascadeRouteDecision | None


async def decide_tier(
    committee: CascadeCommittee, classifier: PromptClassifier, prompt: str
) -> TierDecision:
    """Return the committee verdict; any unavailable axis or router error means heavy."""

    try:
        decision = await committee.classify(prompt, classifier)
    except TypedDecisionUnavailable as error:
        return TierDecision("heavy", f"unavailable:{error.reason.value}", None)
    except CascadeRouterError:
        return TierDecision("heavy", "unavailable:router_error", None)
    except Exception:
        return TierDecision("heavy", "unavailable:error", None)
    return TierDecision(decision.route, decision.reason, decision)


def _request_shape_valid(axes: tuple[ClassifierAxis, ...], text: str) -> bool:
    return (
        bool(text.strip())
        and 0 < len(axes) <= MAXIMUM_AXES
        and all(isinstance(axis, ClassifierAxis) for axis in axes)
        and len({axis.name for axis in axes}) == len(axes)
    )


def _answer_matches(axes: tuple[ClassifierAxis, ...], answer: object) -> bool:
    if not isinstance(answer, AdapterAnswer):
        return False
    version = answer.model_version
    tokens = answer.input_tokens
    return (
        isinstance(version, str)
        and bool(version.strip())
        and len(version) <= _MAXIMUM_MODEL_VERSION_LENGTH
        and not isinstance(tokens, bool)
        and isinstance(tokens, int)
        and tokens >= 0
        and len(answer.results) == len(axes)
        and all(
            isinstance(result, ClassifierResult)
            and result.axis_name == axis.name
            and result.label in axis.allowed_labels
            for axis, result in zip(axes, answer.results, strict=True)
        )
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_typed_decision_guard.py -v`
Expected: 19 passed

- [ ] **Step 5: Lint, type-check, check the architecture and commit**

```bash
uv run ruff format src/mnemo_memory/packages/model_gateway/typed_decisions.py tests/unit/test_typed_decision_guard.py
uv run ruff check --fix src/mnemo_memory/packages/model_gateway/typed_decisions.py tests/unit/test_typed_decision_guard.py
uv run mypy
npm run -s architecture:check
git add src/mnemo_memory/packages/model_gateway/typed_decisions.py tests/unit/test_typed_decision_guard.py
git commit -m "feat(model-gateway): guarded typed-decision boundary with data-route gate" -- \
  src/mnemo_memory/packages/model_gateway/typed_decisions.py tests/unit/test_typed_decision_guard.py
```

---

### Task 6: TypeSafe Jev connector

**Files:**
- Create: `src/mnemo_memory/connectors/typesafe/__init__.py`
- Create: `src/mnemo_memory/connectors/typesafe/jev_provider.py`
- Modify: `scripts/check_architecture.py` (add `"connectors/typesafe"` to `CONNECTOR_COMPONENTS`)
- Test: `tests/unit/test_jev_provider.py`

**Interfaces:**
- Consumes: `AxisKind`, `ClassifierAxis`, `ClassifierResult` and `logprob_from_probability` from Task 2; `AdapterAnswer` and `TypedDecisionAdapterError` from Task 5; `TypedDecisionUnavailableReason` from Task 1.
- Produces (importable from `mnemo_memory.connectors.typesafe`):
  - `JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"`
  - `JEV_DEFAULT_MODEL = "jev-1.13.0"`
  - `JevTransport = Callable[[str, bytes, Mapping[str, str], float], bytes]`
  - `JevClassifier(api_key: str, *, model_id: str = JEV_DEFAULT_MODEL, endpoint: str = JEV_ENDPOINT, transport: JevTransport | None = None)`, which implements `TypedDecisionAdapter`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_jev_provider.py
import json
from collections.abc import Mapping
from email.message import Message
from urllib import error as urllib_error

import pytest

from mnemo_memory.connectors.typesafe import JEV_DEFAULT_MODEL, JEV_ENDPOINT, JevClassifier
from mnemo_memory.packages.domain import TypedDecisionUnavailableReason
from mnemo_memory.packages.model_gateway.decision_axes import FRONT_DOOR_AXES
from mnemo_memory.packages.model_gateway.typed_decisions import TypedDecisionAdapterError

KEY = "test-key-not-real-0000"
# Recorded 2026-09-29 from jev-1.13.0 for a synthetic prompt (docs/superpowers/specs §4.2).
SMOKE_RESPONSE = {
    "model": "jev-1.13.0",
    "answers": {
        "needs_long_term": {"type": "noul", "noul": 0.93},
        "needs_structure": {"type": "noul", "noul": 0.61},
        "complexity": {
            "type": "choice",
            "choice": "heavy",
            "confidence": 0.6,
            "probabilities": {"heavy": 0.8, "light": 0.2},
        },
        "tool_need": {
            "type": "choice",
            "choice": "read_heavy",
            "confidence": 0.73,
            "probabilities": {"none": 0.18, "read_heavy": 0.82, "edit": 0.0},
        },
    },
    "usage": {"input_tokens": 485, "output_tokens": 108},
}


class RecordingTransport:
    def __init__(self, response: object = SMOKE_RESPONSE, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple[str, dict[str, object], dict[str, str], float]] = []

    def __call__(
        self, url: str, body: bytes, headers: Mapping[str, str], timeout: float
    ) -> bytes:
        self.calls.append((url, json.loads(body), dict(headers), timeout))
        if self.error is not None:
            raise self.error
        if isinstance(self.response, bytes):
            return self.response
        return json.dumps(self.response).encode()


def test_request_uses_pinned_model_bearer_key_and_typed_questions() -> None:
    transport = RecordingTransport()
    JevClassifier(KEY, transport=transport).answer(FRONT_DOOR_AXES, "prompt", timeout_seconds=0.6)
    url, body, headers, timeout = transport.calls[0]
    assert (url, timeout) == (JEV_ENDPOINT, 0.6)
    assert headers == {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
    assert body["model"] == JEV_DEFAULT_MODEL == "jev-1.13.0"
    assert body["state"] == "prompt"
    questions = body["questions"]
    assert isinstance(questions, dict)
    assert questions["needs_long_term"]["type"] == "noul"
    assert questions["complexity"] == {
        "type": "choice",
        "instructions": "How hard is this task for an AI coding assistant",
        "criteria": {
            "light": "Simple lookup, rename, formatting or summarizing",
            "heavy": "Needs reasoning across several facts, design judgement, or risky changes",
        },
    }


def test_recorded_smoke_response_parses_into_axis_results() -> None:
    answer = JevClassifier(KEY, transport=RecordingTransport()).answer(
        FRONT_DOOR_AXES, "prompt", timeout_seconds=0.6
    )
    by_axis = {result.axis_name: result for result in answer.results}
    assert (answer.model_version, answer.input_tokens) == ("jev-1.13.0", 485)
    assert (by_axis["needs_long_term"].label, by_axis["needs_long_term"].escalation_score) == (
        "yes",
        0.93,
    )
    assert by_axis["needs_structure"].escalation_score == pytest.approx(0.61)
    assert by_axis["complexity"].label == "heavy"
    assert by_axis["complexity"].escalation_score == pytest.approx(0.8)
    assert by_axis["complexity"].confidence == 0.6
    assert by_axis["tool_need"].escalation_score == pytest.approx(0.205)
    assert by_axis["tool_need"].confidence == 0.73


def test_extreme_probabilities_stay_finite() -> None:
    response = json.loads(json.dumps(SMOKE_RESPONSE))
    response["answers"]["needs_long_term"]["noul"] = 0.0
    response["answers"]["needs_structure"]["noul"] = 1.0
    answer = JevClassifier(KEY, transport=RecordingTransport(response)).answer(
        FRONT_DOOR_AXES, "prompt", timeout_seconds=0.6
    )
    assert answer.results[0].label == "no" and answer.results[0].label_logprob == 0.0
    assert answer.results[1].label == "yes" and answer.results[1].label_logprob == 0.0


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["answers"].pop("tool_need"),
        lambda r: r["answers"]["tool_need"]["probabilities"].update({"delete": 0.1}),
        lambda r: r["answers"]["complexity"].update({"choice": "medium"}),
        lambda r: r["answers"]["needs_long_term"].update({"noul": 1.5}),
        lambda r: r["answers"]["needs_long_term"].update({"type": "choice"}),
        lambda r: r.update({"model": ""}),
    ],
)
def test_malformed_answers_are_schema_invalid(mutate: object) -> None:
    response = json.loads(json.dumps(SMOKE_RESPONSE))
    assert callable(mutate)
    mutate(response)
    with pytest.raises(TypedDecisionAdapterError) as caught:
        JevClassifier(KEY, transport=RecordingTransport(response)).answer(
            FRONT_DOOR_AXES, "prompt", timeout_seconds=0.6
        )
    assert caught.value.reason is TypedDecisionUnavailableReason.SCHEMA_INVALID


def test_non_json_body_is_schema_invalid() -> None:
    with pytest.raises(TypedDecisionAdapterError) as caught:
        JevClassifier(KEY, transport=RecordingTransport(b"<html>")).answer(
            FRONT_DOOR_AXES, "prompt", timeout_seconds=0.6
        )
    assert caught.value.reason is TypedDecisionUnavailableReason.SCHEMA_INVALID


def test_http_and_network_failures_map_to_closed_reasons() -> None:
    overloaded = urllib_error.HTTPError(JEV_ENDPOINT, 529, "overloaded", Message(), None)
    with pytest.raises(TypedDecisionAdapterError) as caught:
        JevClassifier(KEY, transport=RecordingTransport(error=overloaded)).answer(
            FRONT_DOOR_AXES, "prompt", timeout_seconds=0.6
        )
    assert caught.value.reason is TypedDecisionUnavailableReason.HTTP_ERROR
    slow = urllib_error.URLError(TimeoutError("timed out"))
    with pytest.raises(TimeoutError):
        JevClassifier(KEY, transport=RecordingTransport(error=slow)).answer(
            FRONT_DOOR_AXES, "prompt", timeout_seconds=0.6
        )


def test_api_key_never_appears_in_repr_or_errors() -> None:
    classifier = JevClassifier(KEY, transport=RecordingTransport(b"not json"))
    assert KEY not in repr(classifier)
    with pytest.raises(TypedDecisionAdapterError) as caught:
        classifier.answer(FRONT_DOOR_AXES, "prompt", timeout_seconds=0.6)
    assert KEY not in str(caught.value) and caught.value.__cause__ is None
    with pytest.raises(ValueError) as missing:
        JevClassifier("   ")
    assert str(missing.value) == "MNEMO_TYPED_DECISION_CREDENTIAL_MISSING"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_jev_provider.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mnemo_memory.connectors.typesafe'`

- [ ] **Step 3: Write the implementation**

```python
# src/mnemo_memory/connectors/typesafe/jev_provider.py
"""TypeSafe Jev typed-classification adapter (stdlib HTTP, no new dependency).

The adapter only builds and parses requests. Mnemo's guard owns data-route, secret, sensitivity,
budget and deadline policy. This module never logs or returns request text or the API key.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from typing import Any
from urllib import error as _error
from urllib import request as _request

from mnemo_memory.packages.domain import TypedDecisionUnavailableReason
from mnemo_memory.packages.model_gateway.cascade_router import (
    AxisKind,
    ClassifierAxis,
    ClassifierResult,
    logprob_from_probability,
)
from mnemo_memory.packages.model_gateway.typed_decisions import (
    AdapterAnswer,
    TypedDecisionAdapterError,
)

JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
JEV_DEFAULT_MODEL = "jev-1.13.0"
_MAXIMUM_RESPONSE_BYTES = 262_144

JevTransport = Callable[[str, bytes, Mapping[str, str], float], bytes]


def _urllib_transport(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
    request = _request.Request(url, data=body, headers=dict(headers), method="POST")
    with _request.urlopen(request, timeout=timeout) as response:
        payload: bytes = response.read(_MAXIMUM_RESPONSE_BYTES + 1)
    if len(payload) > _MAXIMUM_RESPONSE_BYTES:
        raise TypedDecisionAdapterError(TypedDecisionUnavailableReason.SCHEMA_INVALID)
    return payload


class JevClassifier:
    """Ask several closed questions about one bounded text in a single Jev request."""

    def __init__(
        self,
        api_key: str,
        *,
        model_id: str = JEV_DEFAULT_MODEL,
        endpoint: str = JEV_ENDPOINT,
        transport: JevTransport | None = None,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("MNEMO_TYPED_DECISION_CREDENTIAL_MISSING")
        self._api_key = api_key.strip()
        self._model_id = model_id
        self._endpoint = endpoint
        self._transport = transport or _urllib_transport

    def __repr__(self) -> str:
        return f"JevClassifier(model_id={self._model_id!r})"

    @property
    def provider_id(self) -> str:
        return "typesafe"

    @property
    def model_id(self) -> str:
        return self._model_id

    def answer(
        self, axes: tuple[ClassifierAxis, ...], text: str, *, timeout_seconds: float
    ) -> AdapterAnswer:
        body = json.dumps(
            {
                "model": self._model_id,
                "state": text,
                "questions": {axis.name: _question(axis) for axis in axes},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        try:
            raw = self._transport(self._endpoint, body, headers, timeout_seconds)
        except (TypedDecisionAdapterError, TimeoutError):
            raise
        except _error.HTTPError:
            raise TypedDecisionAdapterError(TypedDecisionUnavailableReason.HTTP_ERROR) from None
        except (_error.URLError, OSError) as failure:
            if isinstance(getattr(failure, "reason", None), TimeoutError):
                raise TimeoutError from None
            raise TypedDecisionAdapterError(TypedDecisionUnavailableReason.HTTP_ERROR) from None
        return _parse(axes, raw)


def _question(axis: ClassifierAxis) -> dict[str, object]:
    if axis.kind is AxisKind.YES_NO:
        return {"type": "noul", "instructions": axis.instructions}
    criteria = dict(axis.criteria) if axis.criteria else {label: label for label in axis.allowed_labels}
    return {"type": "choice", "instructions": axis.instructions, "criteria": criteria}


def _parse(axes: tuple[ClassifierAxis, ...], raw: bytes) -> AdapterAnswer:
    try:
        value: Any = json.loads(raw.decode("utf-8"))
        model = value["model"]
        answers = value["answers"]
        if (
            not isinstance(model, str)
            or not model.strip()
            or not isinstance(answers, dict)
            or set(answers) != {axis.name for axis in axes}
        ):
            raise ValueError("answers do not match the questions")
        results = tuple(_result(axis, answers[axis.name]) for axis in axes)
        usage = value.get("usage")
        tokens = usage.get("input_tokens", 0) if isinstance(usage, dict) else 0
        input_tokens = tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else 0
    except (UnicodeDecodeError, ValueError, KeyError, TypeError, AttributeError):
        raise TypedDecisionAdapterError(TypedDecisionUnavailableReason.SCHEMA_INVALID) from None
    return AdapterAnswer(results, model, max(0, input_tokens))


def _result(axis: ClassifierAxis, answer: object) -> ClassifierResult:
    if not isinstance(answer, dict):
        raise ValueError("answer is not an object")
    if axis.kind is AxisKind.YES_NO:
        if answer.get("type") != "noul":
            raise ValueError("expected a noul answer")
        yes = _probability(answer.get("noul"))
        label = "yes" if yes >= 0.5 else "no"
        chosen = yes if label == "yes" else 1.0 - yes
        return ClassifierResult(
            axis.name,
            label,
            logprob_from_probability(chosen),
            axis.escalation_score_for({"yes": yes, "no": 1.0 - yes}),
        )
    if answer.get("type") != "choice":
        raise ValueError("expected a choice answer")
    label = answer.get("choice")
    probabilities = answer.get("probabilities")
    if label not in axis.allowed_labels or not isinstance(probabilities, dict):
        raise ValueError("choice is outside the allowed labels")
    if set(probabilities) - set(axis.allowed_labels):
        raise ValueError("probabilities name unknown labels")
    by_label = {name: _probability(probabilities.get(name, 0.0)) for name in axis.allowed_labels}
    return ClassifierResult(
        axis.name,
        str(label),
        logprob_from_probability(by_label[str(label)]),
        axis.escalation_score_for(by_label),
        confidence=_probability(answer.get("confidence")),
    )


def _probability(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0.0 <= value <= 1.0
    ):
        raise ValueError("probability is invalid")
    return float(value)
```

```python
# src/mnemo_memory/connectors/typesafe/__init__.py
from .jev_provider import JEV_DEFAULT_MODEL, JEV_ENDPOINT, JevClassifier, JevTransport

__all__ = ["JEV_DEFAULT_MODEL", "JEV_ENDPOINT", "JevClassifier", "JevTransport"]
```

In `scripts/check_architecture.py`, add `"connectors/typesafe",` to `CONNECTOR_COMPONENTS`, in alphabetical order after `"connectors/postgresql",`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_jev_provider.py -v`
Expected: 12 passed

- [ ] **Step 5: Lint, type-check, check the architecture and commit**

```bash
uv run ruff format src/mnemo_memory/connectors/typesafe tests/unit/test_jev_provider.py scripts/check_architecture.py
uv run ruff check --fix src/mnemo_memory/connectors/typesafe tests/unit/test_jev_provider.py scripts/check_architecture.py
uv run mypy
npm run -s architecture:check
git add src/mnemo_memory/connectors/typesafe tests/unit/test_jev_provider.py
git commit -m "feat(connectors): TypeSafe Jev typed-classification adapter" -- \
  src/mnemo_memory/connectors/typesafe tests/unit/test_jev_provider.py scripts/check_architecture.py
```

---

### Task 7: Default-off typed-decision settings

**Files:**
- Modify: `src/mnemo_memory/packages/application/settings.py`
- Test: `tests/unit/test_personal_settings.py` (update one assertion and add tests)

**Interfaces:**
- Consumes: `TypedDecisionDataRoute`, `TypedDecisionKind` and `TypedDecisionMode` from Task 1.
- Produces these new `PersonalSettings` fields, all at the end of the dataclass:

  | Field | Type | Default |
  |---|---|---|
  | `experimental_typed_decisions_enabled` | `bool` | `False` |
  | `typed_decision_data_route` | `str` | `"synthetic_only"` |
  | `typed_decision_model_id` | `str` | `"jev-1.13.0"` |
  | `typed_decision_modes` | `tuple[tuple[str, str], ...]` | `()` |

  It also adds the method `typed_decision_mode(kind: TypedDecisionKind) -> TypedDecisionMode`.
- Serialization: `to_dict()` emits `typed_decision_modes` as a JSON object `{kind: mode}`, and `from_dict()` accepts that object.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_personal_settings.py`, make three changes:
1. Add `from mnemo_memory.packages.domain import TypedDecisionKind, TypedDecisionMode` to the import block at the **top** of the file. A mid-file import fails ruff E402.
2. Add these four keys to the set literal in `test_settings_defaults_are_strict_bounded_and_secret_free`: `"experimental_typed_decisions_enabled"`, `"typed_decision_data_route"`, `"typed_decision_model_id"` and `"typed_decision_modes"`.
3. Append:

```python
def test_typed_decision_settings_default_off_and_synthetic_only() -> None:
    settings = PersonalSettings()
    assert settings.experimental_typed_decisions_enabled is False
    assert settings.typed_decision_data_route == "synthetic_only"
    assert settings.typed_decision_model_id == "jev-1.13.0"
    assert settings.typed_decision_mode(TypedDecisionKind.FRONT_DOOR) is TypedDecisionMode.OFF
    assert settings.to_dict()["typed_decision_modes"] == {}


@pytest.mark.parametrize(
    "overrides",
    [
        {"typed_decision_data_route": "vercel_zdr"},
        {"typed_decision_data_route": "standard_terms"},
        {"typed_decision_model_id": "bad model id"},
        {"typed_decision_modes": {"front_door": "shadow"}},  # switch still off
        {"experimental_typed_decisions_enabled": True, "typed_decision_modes": {"nope": "live"}},
        {"experimental_typed_decisions_enabled": True, "typed_decision_modes": {"front_door": "on"}},
        {"experimental_typed_decisions_enabled": "yes"},
        {"typed_decision_modes": ["front_door"]},
    ],
)
def test_typed_decision_settings_reject_unsupported_values(overrides: dict[str, object]) -> None:
    with pytest.raises(PersonalSettingsError):
        PersonalSettings.from_dict({**PersonalSettings().to_dict(), **overrides})


def test_typed_decision_modes_round_trip_through_the_store(tmp_path: Path) -> None:
    store = PersonalSettingsStore(tmp_path / "profile")
    expected = PersonalSettings.from_dict(
        {
            **PersonalSettings().to_dict(),
            "experimental_typed_decisions_enabled": True,
            "typed_decision_modes": {"relevance": "shadow", "front_door": "shadow"},
        }
    )
    store.save(expected)
    loaded = store.load()
    assert loaded == expected
    assert loaded.typed_decision_mode(TypedDecisionKind.RELEVANCE) is TypedDecisionMode.SHADOW
    assert loaded.to_dict()["typed_decision_modes"] == {"front_door": "shadow", "relevance": "shadow"}


def test_legacy_settings_without_typed_decision_fields_load(tmp_path: Path) -> None:
    store = PersonalSettingsStore(tmp_path / "profile")
    store.save(PersonalSettings())
    path = tmp_path / "profile" / "settings.json"
    legacy = json.loads(path.read_text("utf-8"))
    for name in (
        "experimental_typed_decisions_enabled",
        "typed_decision_data_route",
        "typed_decision_model_id",
        "typed_decision_modes",
    ):
        del legacy[name]
    path.write_text(json.dumps(legacy), encoding="utf-8")
    assert store.load() == PersonalSettings()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_personal_settings.py -v`
Expected: FAIL. The defaults test fails on the key set, and the new tests fail with `AttributeError: ... 'experimental_typed_decisions_enabled'`.

- [ ] **Step 3: Write the implementation (edits to `settings.py`)**

Change the imports:

```python
from typing import Any, ClassVar, Self

from mnemo_memory.packages.application.automatic_memory import (
    AutomaticMemoryBindingError,
    exclusive_local_file_lock,
)
from mnemo_memory.packages.domain import (
    ContextBudget,
    TypedDecisionDataRoute,
    TypedDecisionKind,
    TypedDecisionMode,
)
```

Add the four names to `_FIELDS`:

```python
    "experimental_typed_decisions_enabled",
    "typed_decision_data_route",
    "typed_decision_model_id",
    "typed_decision_modes",
```

Append the fields after `context_save_growth_bytes` in the dataclass:

```python
    experimental_typed_decisions_enabled: bool = False
    typed_decision_data_route: str = TypedDecisionDataRoute.SYNTHETIC_ONLY.value
    typed_decision_model_id: str = "jev-1.13.0"
    typed_decision_modes: tuple[tuple[str, str], ...] = ()
```

In `__post_init__`, add `"experimental_typed_decisions_enabled"` to the tuple of names that are checked as booleans. Then add these checks immediately before the `try: _ = self.context_budget` block:

```python
        try:
            TypedDecisionDataRoute(self.typed_decision_data_route)
        except ValueError as error:
            raise PersonalSettingsError("typed decision data route is not supported") from error
        if _optional_metadata(self.typed_decision_model_id, "typed decision model id") is None:
            raise PersonalSettingsError("typed decision model id is required")
        object.__setattr__(
            self, "typed_decision_modes", _typed_decision_modes(self.typed_decision_modes)
        )
        if not self.experimental_typed_decisions_enabled and any(
            mode != TypedDecisionMode.OFF.value for _, mode in self.typed_decision_modes
        ):
            raise PersonalSettingsError(
                "typed decision modes require experimental_typed_decisions_enabled"
            )
```

Add the accessor method below `episodic_extraction_enabled`:

```python
    def typed_decision_mode(self, kind: TypedDecisionKind) -> TypedDecisionMode:
        """Return one decision's mode; decisions not listed are off."""

        return TypedDecisionMode(
            dict(self.typed_decision_modes).get(kind.value, TypedDecisionMode.OFF.value)
        )
```

In `to_dict()`, add the entries in alphabetical key order:

```python
            "experimental_typed_decisions_enabled": self.experimental_typed_decisions_enabled,
            "typed_decision_data_route": self.typed_decision_data_route,
            "typed_decision_model_id": self.typed_decision_model_id,
            "typed_decision_modes": dict(self.typed_decision_modes),
```

Replace `_MIGRATED_DEFAULTS` and `from_dict` with:

```python
    _MIGRATED_DEFAULTS: ClassVar[dict[str, object]] = {
        "experimental_semantic_memory_enabled": False,
        "experimental_local_first_takeover_enabled": False,
        "local_first_takeover_live_calls_authorized": False,
        "context_save_growth_bytes": 200_000,
        "experimental_typed_decisions_enabled": False,
        "typed_decision_data_route": TypedDecisionDataRoute.SYNTHETIC_ONLY.value,
        "typed_decision_model_id": "jev-1.13.0",
        "typed_decision_modes": {},
    }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, dict):
            raise PersonalSettingsError("personal settings fields are invalid")
        fields: dict[str, Any] = dict(value)
        missing = _FIELDS - set(fields)
        if missing and missing <= set(cls._MIGRATED_DEFAULTS):
            fields = {**{k: cls._MIGRATED_DEFAULTS[k] for k in missing}, **fields}
        if set(fields) != _FIELDS:
            raise PersonalSettingsError("personal settings fields are invalid")
        modes = fields["typed_decision_modes"]
        if not isinstance(modes, dict):
            raise PersonalSettingsError("personal settings values are invalid")
        fields["typed_decision_modes"] = tuple(modes.items())
        try:
            return cls(**fields)
        except TypeError as error:
            raise PersonalSettingsError("personal settings values are invalid") from error
```

Add this module-level helper next to `_optional_metadata`:

```python
def _typed_decision_modes(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, tuple):
        raise PersonalSettingsError("typed decision modes are invalid")
    modes: dict[str, str] = {}
    for item in value:
        if not isinstance(item, tuple) or len(item) != 2:
            raise PersonalSettingsError("typed decision modes are invalid")
        try:
            kind = TypedDecisionKind(item[0]).value
            mode = TypedDecisionMode(item[1]).value
        except ValueError as error:
            raise PersonalSettingsError("typed decision modes are invalid") from error
        if kind in modes:
            raise PersonalSettingsError("typed decision modes are invalid")
        modes[kind] = mode
    return tuple(sorted(modes.items()))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_personal_settings.py -v`
Expected: all pass, including the 11 new cases (1 + 8 parametrized + 1 + 1).

- [ ] **Step 5: Lint, type-check and commit**

```bash
uv run ruff format src/mnemo_memory/packages/application/settings.py tests/unit/test_personal_settings.py
uv run ruff check --fix src/mnemo_memory/packages/application/settings.py tests/unit/test_personal_settings.py
uv run mypy
git commit -m "feat(settings): default-off typed-decision settings, synthetic-only route" -- \
  src/mnemo_memory/packages/application/settings.py tests/unit/test_personal_settings.py
```

---

### Task 8: Runtime composition that cannot send runtime text, plus architecture guards

**Files:**
- Create: `src/mnemo_memory/apps/cli/typed_decision_composition.py`
- Test: `tests/unit/test_typed_decision_composition.py`
- Test: `tests/architecture/test_typed_decision_boundaries.py`

**Interfaces:**
- Consumes: `PersonalSettings` (Task 7), `JevClassifier` and `JevTransport` (Task 6), `GuardedTypedDecisionClassifier` (Task 5), and the domain names (Task 1).
- Produces:
  - `RUNTIME_DEADLINE_SECONDS = 0.6`
  - `DenyAllModelBudget`, whose every reservation raises `ModelBudgetDenied`
  - `build_runtime_typed_decision_classifier(settings: PersonalSettings, *, environ: Mapping[str, str] | None = None, jev_transport: JevTransport | None = None) -> GuardedTypedDecisionClassifier | None`
- Nothing in the hook or the MCP server calls this in phase 1. It exists so the data-route rule is enforced and tested at a real composition root.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_typed_decision_composition.py
import asyncio
import inspect
from collections.abc import Mapping

from mnemo_memory.apps.cli.typed_decision_composition import (
    RUNTIME_DEADLINE_SECONDS,
    build_runtime_typed_decision_classifier,
)
from mnemo_memory.packages.application import PersonalSettings
from mnemo_memory.packages.domain import TypedDecisionUnavailableReason
from mnemo_memory.packages.model_gateway.decision_axes import FRONT_DOOR_AXES


class NeverTransport:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        self.calls += 1
        raise AssertionError("runtime text must never reach the network in phase 1")


def test_default_settings_compose_nothing() -> None:
    assert build_runtime_typed_decision_classifier(PersonalSettings(), environ={}) is None


def test_enabled_runtime_classifier_is_data_route_blocked_without_network() -> None:
    transport = NeverTransport()
    settings = PersonalSettings(experimental_typed_decisions_enabled=True)
    environments: tuple[dict[str, str], ...] = ({"TYPESAFE_API_KEY": "test-key-not-real"}, {})
    for environ in environments:
        guard = build_runtime_typed_decision_classifier(
            settings, environ=environ, jev_transport=transport
        )
        assert guard is not None
        outcome = asyncio.run(guard.ask(FRONT_DOOR_AXES, "What did we decide last week?"))
        assert outcome.unavailable_reason is TypedDecisionUnavailableReason.DATA_ROUTE_BLOCKED
    assert transport.calls == 0


def test_runtime_source_cannot_be_chosen_by_callers() -> None:
    parameters = inspect.signature(build_runtime_typed_decision_classifier).parameters
    assert "source" not in parameters and "data_route" not in parameters
    assert RUNTIME_DEADLINE_SECONDS == 0.6
```

```python
# tests/architecture/test_typed_decision_boundaries.py
import ast
import pathlib

NETWORK_MODULES = {"urllib", "http", "socket", "ssl", "requests", "httpx"}
POLICY_MODULES = (
    pathlib.Path("src/mnemo_memory/packages/domain/typed_decisions.py"),
    pathlib.Path("src/mnemo_memory/packages/model_gateway/cascade_router.py"),
    pathlib.Path("src/mnemo_memory/packages/model_gateway/rule_axes.py"),
    pathlib.Path("src/mnemo_memory/packages/model_gateway/decision_axes.py"),
    pathlib.Path("src/mnemo_memory/packages/model_gateway/typed_decisions.py"),
)
TYPESAFE_CONNECTOR = pathlib.Path("src/mnemo_memory/connectors/typesafe")
RUNTIME_COMPOSITION = pathlib.Path("src/mnemo_memory/apps/cli/typed_decision_composition.py")


def _imports(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
    return names


def test_decision_policy_modules_never_open_network_connections() -> None:
    for path in POLICY_MODULES:
        for module in _imports(path):
            assert module.split(".")[0] not in NETWORK_MODULES, f"{path} imports {module}"


def test_typesafe_connector_and_runtime_composition_never_import_scripts() -> None:
    for path in (*TYPESAFE_CONNECTOR.glob("*.py"), RUNTIME_COMPOSITION):
        for module in _imports(path):
            assert "scripts" not in module, f"{path} imports the eval harness"


def test_only_the_runtime_composition_imports_the_jev_connector() -> None:
    importers = sorted(
        str(path)
        for path in pathlib.Path("src/mnemo_memory").rglob("*.py")
        if path.parent != TYPESAFE_CONNECTOR
        and any(module.startswith("mnemo_memory.connectors.typesafe") for module in _imports(path))
    )
    assert importers == [str(RUNTIME_COMPOSITION)]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_typed_decision_composition.py tests/architecture/test_typed_decision_boundaries.py -v`
Expected: FAIL with `ModuleNotFoundError: ... typed_decision_composition`

- [ ] **Step 3: Write the implementation**

```python
# src/mnemo_memory/apps/cli/typed_decision_composition.py
"""Runtime composition for typed decisions (phase 1: never sends runtime text).

Phase 1 composes the guard only so the data-route rule is enforced and tested at a real
composition root. Nothing in the hook or the MCP server calls it yet (spec §9).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from uuid import UUID

from mnemo_memory.connectors.typesafe import JevClassifier, JevTransport
from mnemo_memory.packages.application import PersonalSettings
from mnemo_memory.packages.domain import (
    ModelBudgetDenied,
    ModelBudgetReservation,
    ModelTaskType,
    TypedDecisionDataRoute,
    TypedDecisionSource,
    WorkspaceId,
)
from mnemo_memory.packages.model_gateway.typed_decisions import GuardedTypedDecisionClassifier

RUNTIME_DEADLINE_SECONDS = 0.6
_RUNTIME_RESERVATION = ModelBudgetReservation(input_tokens=1_000, output_tokens=1, cost_microusd=0)
_LOCAL_WORKSPACE = WorkspaceId(UUID(int=0))


class DenyAllModelBudget:
    """Phase 1 has no personal typed-decision budget, so every reservation is denied."""

    def reserve(
        self,
        workspace_id: WorkspaceId,
        task_type: ModelTaskType,
        reservation: ModelBudgetReservation,
    ) -> None:
        raise ModelBudgetDenied("MNEMO_TYPED_DECISION_BUDGET_UNAVAILABLE")


def build_runtime_typed_decision_classifier(
    settings: PersonalSettings,
    *,
    environ: Mapping[str, str] | None = None,
    jev_transport: JevTransport | None = None,
) -> GuardedTypedDecisionClassifier | None:
    """Return ``None`` when disabled; otherwise a guard bound to the runtime source."""

    if not settings.experimental_typed_decisions_enabled:
        return None
    variables = os.environ if environ is None else environ
    api_key = variables.get("TYPESAFE_API_KEY", "").strip()
    adapter = (
        JevClassifier(api_key, model_id=settings.typed_decision_model_id, transport=jev_transport)
        if api_key
        else None
    )
    return GuardedTypedDecisionClassifier(
        adapter,
        data_route=TypedDecisionDataRoute(settings.typed_decision_data_route),
        source=TypedDecisionSource.RUNTIME,
        budget=DenyAllModelBudget(),
        workspace_id=_LOCAL_WORKSPACE,
        reservation=_RUNTIME_RESERVATION,
        deadline_seconds=RUNTIME_DEADLINE_SECONDS,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_typed_decision_composition.py tests/architecture/test_typed_decision_boundaries.py tests/architecture/test_takeover_boundaries.py -v`
Expected: all pass (3 + 3 new tests, plus the existing takeover tests).

- [ ] **Step 5: Lint, type-check, check the architecture and commit**

```bash
uv run ruff format src/mnemo_memory/apps/cli/typed_decision_composition.py tests/unit/test_typed_decision_composition.py tests/architecture/test_typed_decision_boundaries.py
uv run ruff check --fix src/mnemo_memory/apps/cli/typed_decision_composition.py tests/unit/test_typed_decision_composition.py tests/architecture/test_typed_decision_boundaries.py
uv run mypy
npm run -s architecture:check
git add src/mnemo_memory/apps/cli/typed_decision_composition.py tests/unit/test_typed_decision_composition.py tests/architecture/test_typed_decision_boundaries.py
git commit -m "feat(cli): runtime typed-decision composition blocked by data route" -- \
  src/mnemo_memory/apps/cli/typed_decision_composition.py \
  tests/unit/test_typed_decision_composition.py \
  tests/architecture/test_typed_decision_boundaries.py
```

---

### Task 9: Synthetic tier and worth-remembering fixture

**Files:**
- Create: `tests/fixtures/evals/typed-decision-v1.json`
- Test: `tests/evals/test_typed_decision_fixture.py`

**Interfaces:**
- Consumes: `matched_risk_tags` (Task 3).
- Produces: the fixture file, with these keys:
  - `fixture_kind: "synthetic-typed-decision-acceptance"`
  - `provenance`, the same object as the routing fixture
  - `tier_cases`: 40 items, each `{id, expected_tier, expected_tool_need, prompt}`
  - `worth_negatives`: 12 items, each `{id, summary}`

- [ ] **Step 1: Write the failing test**

```python
# tests/evals/test_typed_decision_fixture.py
"""Shape checks for the Mnemo-owned synthetic typed-decision fixture."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mnemo_memory.packages.model_gateway.rule_axes import matched_risk_tags

FIXTURE = Path(__file__).parents[1] / "fixtures/evals/typed-decision-v1.json"


def _fixture() -> dict[str, Any]:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_fixture_declares_synthetic_provenance() -> None:
    value = _fixture()
    assert value["schema_version"] == 1
    assert value["fixture_kind"] == "synthetic-typed-decision-acceptance"
    assert value["provenance"] == {
        "origin": "Mnemo-owned original synthetic prompts",
        "competing_product_artifacts_used": False,
    }


def test_tier_cases_are_balanced_unique_and_labelled() -> None:
    cases = _fixture()["tier_cases"]
    assert len(cases) == 40
    assert len({case["id"] for case in cases}) == 40
    assert len({case["prompt"] for case in cases}) == 40
    assert sum(case["expected_tier"] == "heavy" for case in cases) == 20
    assert {case["expected_tier"] for case in cases} == {"light", "heavy"}
    assert {case["expected_tool_need"] for case in cases} == {"none", "read_heavy", "edit"}


def test_light_cases_carry_no_risk_terms_and_ten_heavy_cases_do() -> None:
    cases = _fixture()["tier_cases"]
    light = [case for case in cases if case["expected_tier"] == "light"]
    heavy = [case for case in cases if case["expected_tier"] == "heavy"]
    assert all(matched_risk_tags(case["prompt"]) == () for case in light)
    assert sum(bool(matched_risk_tags(case["prompt"])) for case in heavy) == 10


def test_worth_negatives_are_unique_no_op_events() -> None:
    negatives = _fixture()["worth_negatives"]
    assert len(negatives) == 12
    assert len({item["id"] for item in negatives}) == 12
    assert len({item["summary"] for item in negatives}) == 12
    assert all(matched_risk_tags(item["summary"]) == () for item in negatives)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/evals/test_typed_decision_fixture.py -v`
Expected: FAIL with `FileNotFoundError: ... typed-decision-v1.json`

- [ ] **Step 3: Create the fixture**

```json
{
  "schema_version": 1,
  "fixture_kind": "synthetic-typed-decision-acceptance",
  "provenance": {
    "origin": "Mnemo-owned original synthetic prompts",
    "competing_product_artifacts_used": false
  },
  "tier_cases": [
    {"id": "light-01", "expected_tier": "light", "expected_tool_need": "read_heavy", "prompt": "Find where the helper parse_row is defined."},
    {"id": "light-02", "expected_tier": "light", "expected_tool_need": "read_heavy", "prompt": "List the test files under tests/unit that mention checkpoints."},
    {"id": "light-03", "expected_tier": "light", "expected_tool_need": "read_heavy", "prompt": "Summarize what the README says about installation."},
    {"id": "light-04", "expected_tier": "light", "expected_tool_need": "read_heavy", "prompt": "Show me which modules import the settings store."},
    {"id": "light-05", "expected_tier": "light", "expected_tool_need": "read_heavy", "prompt": "Search the docs folder for the phrase lazy pull."},
    {"id": "light-06", "expected_tier": "light", "expected_tool_need": "read_heavy", "prompt": "Read the changelog and tell me the latest version number."},
    {"id": "light-07", "expected_tier": "light", "expected_tool_need": "read_heavy", "prompt": "Which file defines the ClassifierAxis dataclass?"},
    {"id": "light-08", "expected_tier": "light", "expected_tool_need": "read_heavy", "prompt": "Count how many ADR files are in docs/adr."},
    {"id": "light-09", "expected_tier": "light", "expected_tool_need": "none", "prompt": "What does the acronym ADR stand for?"},
    {"id": "light-10", "expected_tier": "light", "expected_tool_need": "none", "prompt": "Convert 90 minutes to seconds."},
    {"id": "light-11", "expected_tier": "light", "expected_tool_need": "none", "prompt": "Suggest a clearer name for a variable called tmp2."},
    {"id": "light-12", "expected_tier": "light", "expected_tool_need": "none", "prompt": "Translate 'good morning' into Spanish."},
    {"id": "light-13", "expected_tier": "light", "expected_tool_need": "none", "prompt": "Explain what a Python dataclass is in one sentence."},
    {"id": "light-14", "expected_tier": "light", "expected_tool_need": "none", "prompt": "Write a one-line commit message for fixing a typo in the README."},
    {"id": "light-15", "expected_tier": "light", "expected_tool_need": "edit", "prompt": "Rename the helper parse_row to parse_record in utils.py."},
    {"id": "light-16", "expected_tier": "light", "expected_tool_need": "edit", "prompt": "Fix the typo 'recieve' in the user guide."},
    {"id": "light-17", "expected_tier": "light", "expected_tool_need": "edit", "prompt": "Add a trailing newline to the end of pyproject.toml."},
    {"id": "light-18", "expected_tier": "light", "expected_tool_need": "edit", "prompt": "Sort the import lines at the top of cascade_router.py."},
    {"id": "light-19", "expected_tier": "light", "expected_tool_need": "edit", "prompt": "Format the JSON fixture with two-space indentation."},
    {"id": "light-20", "expected_tier": "light", "expected_tool_need": "edit", "prompt": "Bump the copyright year in the LICENSE file to 2026."},
    {"id": "heavy-01", "expected_tier": "heavy", "expected_tool_need": "edit", "prompt": "Design the schema migration that splits the events table without losing rollback safety."},
    {"id": "heavy-02", "expected_tier": "heavy", "expected_tool_need": "edit", "prompt": "Add authorization checks so only project owners can export episodic memory."},
    {"id": "heavy-03", "expected_tier": "heavy", "expected_tool_need": "edit", "prompt": "Implement deletion propagation from canonical memories to every cache and backup."},
    {"id": "heavy-04", "expected_tier": "heavy", "expected_tool_need": "edit", "prompt": "Rotate the API key handling so credentials never reach logs, and prove it with tests."},
    {"id": "heavy-05", "expected_tier": "heavy", "expected_tool_need": "edit", "prompt": "Deploy the new MCP server build to production and plan the rollback."},
    {"id": "heavy-06", "expected_tier": "heavy", "expected_tool_need": "read_heavy", "prompt": "Review the OAuth connector for leaked tokens and propose fixes."},
    {"id": "heavy-07", "expected_tier": "heavy", "expected_tool_need": "edit", "prompt": "Backfill sensitivity labels for all stored memories without changing their scopes."},
    {"id": "heavy-08", "expected_tier": "heavy", "expected_tool_need": "read_heavy", "prompt": "Decide whether to encrypt the SQLite profile at rest and document the trade-offs."},
    {"id": "heavy-09", "expected_tier": "heavy", "expected_tool_need": "edit", "prompt": "Purge expired episodic events while keeping audit evidence intact."},
    {"id": "heavy-10", "expected_tier": "heavy", "expected_tool_need": "read_heavy", "prompt": "Audit every permission check in the team API for cross-tenant access."},
    {"id": "heavy-11", "expected_tier": "heavy", "expected_tool_need": "edit", "prompt": "Redesign the retrieval ranking so recent decisions outrank stale notes, and justify the weights."},
    {"id": "heavy-12", "expected_tier": "heavy", "expected_tool_need": "edit", "prompt": "Work out why the checkpoint compaction drops the next action in long sessions and fix it."},
    {"id": "heavy-13", "expected_tier": "heavy", "expected_tool_need": "read_heavy", "prompt": "Compare three caching strategies for the structural index and recommend one."},
    {"id": "heavy-14", "expected_tier": "heavy", "expected_tool_need": "edit", "prompt": "Refactor the context engine so relevance filtering happens before rendering, keeping every test green."},
    {"id": "heavy-15", "expected_tier": "heavy", "expected_tool_need": "read_heavy", "prompt": "Diagnose the intermittent failure in the viability evaluation and propose a root cause."},
    {"id": "heavy-16", "expected_tier": "heavy", "expected_tool_need": "read_heavy", "prompt": "Plan how to split the 3,000-line CLI module into cohesive components."},
    {"id": "heavy-17", "expected_tier": "heavy", "expected_tool_need": "none", "prompt": "Design an evaluation that proves the relevance filter never drops a required fact."},
    {"id": "heavy-18", "expected_tier": "heavy", "expected_tool_need": "edit", "prompt": "Reconcile two conflicting architecture decisions about checkpoint budgets and write the resolution."},
    {"id": "heavy-19", "expected_tier": "heavy", "expected_tool_need": "none", "prompt": "Explain the trade-offs between push and lazy-pull context delivery for long sessions."},
    {"id": "heavy-20", "expected_tier": "heavy", "expected_tool_need": "read_heavy", "prompt": "Trace how a prompt flows from the hook through routing to the rendered attachment, and find the slowest step."}
  ],
  "worth_negatives": [
    {"id": "noop-01", "summary": "Ran git status; the working tree was clean."},
    {"id": "noop-02", "summary": "Opened the README to check the heading level."},
    {"id": "noop-03", "summary": "Listed the files in the tests directory."},
    {"id": "noop-04", "summary": "Re-ran the same test command; the output was unchanged."},
    {"id": "noop-05", "summary": "Scrolled through the log output; nothing new appeared."},
    {"id": "noop-06", "summary": "Checked the current branch name."},
    {"id": "noop-07", "summary": "Printed the Python version."},
    {"id": "noop-08", "summary": "Waited for the formatter to finish."},
    {"id": "noop-09", "summary": "Viewed the first 20 lines of a fixture file."},
    {"id": "noop-10", "summary": "Said hello and asked how to begin."},
    {"id": "noop-11", "summary": "Cleared the terminal screen."},
    {"id": "noop-12", "summary": "Opened the settings file and closed it without changes."}
  ]
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/evals/test_typed_decision_fixture.py -v`
Expected: 4 passed. If `test_light_cases_carry_no_risk_terms_and_ten_heavy_cases_do` fails, a risk regex from Task 3 matched an unintended word. Fix the **prompt wording** in the fixture, not the regex, and re-run.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/evals/typed-decision-v1.json tests/evals/test_typed_decision_fixture.py
git commit -m "test(evals): synthetic typed-decision tier and worth fixture" -- \
  tests/fixtures/evals/typed-decision-v1.json tests/evals/test_typed_decision_fixture.py
```

---

### Task 10: Offline evaluation scoring (no network)

**Files:**
- Create: `scripts/typed_decision_evaluation.py`
- Test: `tests/evals/test_typed_decision_evaluation.py`

**Interfaces:**
- Consumes: from Task 4, `FRONT_DOOR_AXES`, `COMPLEXITY`, `TOOL_NEED`, `WORTH_REMEMBERING`, `EPISODIC_KIND`, `NeedAnswer`, `need_from_result`, `relevance_axis`, `should_drop_candidate`, `worth_extracting`, `accepted_choice`, `hint_eligible` and `tier_committee`; from Task 3, `RISK_AXIS` and `RiskTermClassifier`; from Task 5, `GuardedTypedDecisionClassifier` and `decide_tier`; from Task 2, `AxisRoutedClassifier`; and `parse_episodic_output` (existing).
- Produces (module `scripts.typed_decision_evaluation`):
  - Fixture handling: `FixtureProvenanceError`, `load_synthetic_fixture(path) -> dict[str, Any]`, and the fixture path constants `ROUTING_FIXTURE`, `VIABILITY_FIXTURE`, `TYPED_DECISION_FIXTURE`, `TELEHEALTH_FIXTURE` and `REPOSITORY_ROOT`
  - Row types: `FrontDoorRow`, `RelevanceRow`, `ExtractionRow` and `TierRow` (frozen dataclasses)
  - Scoring: `derived_route(structure: NeedAnswer, long_term: NeedAnswer) -> str`, `score_front_door`, `score_latency`, `score_relevance`, `score_extraction` and `score_tier`. Each returns `dict[str, Any]` with a `"gates"` dict.
  - Evaluation: `evaluate_front_door`, `evaluate_relevance`, `evaluate_extraction_jev` and `evaluate_tier` (all async and taking the guard), plus `evaluate_extraction_ollama(provider)` (sync)
  - `EpisodicProvider` (Protocol) with `generate(request: object) -> object`
  - `phase_one_complete(report: dict[str, Any]) -> bool`
  - `async run_phase_one(guard, ollama: EpisodicProvider | None) -> dict[str, Any]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/evals/test_typed_decision_evaluation.py
"""Scoring and provenance checks for the phase-1 typed-decision evaluation (no network)."""

from __future__ import annotations

from typing import Any

import pytest

from mnemo_memory.packages.model_gateway.decision_axes import NeedAnswer
from scripts.typed_decision_evaluation import (
    ROUTING_FIXTURE,
    TELEHEALTH_FIXTURE,
    TYPED_DECISION_FIXTURE,
    VIABILITY_FIXTURE,
    ExtractionRow,
    FixtureProvenanceError,
    FrontDoorRow,
    RelevanceRow,
    TierRow,
    derived_route,
    load_synthetic_fixture,
    phase_one_complete,
    score_extraction,
    score_front_door,
    score_latency,
    score_relevance,
    score_tier,
)

Y, N, U = NeedAnswer.YES, NeedAnswer.NO, NeedAnswer.UNKNOWN


def test_only_fixtures_with_synthetic_provenance_are_accepted() -> None:
    for path in (ROUTING_FIXTURE, VIABILITY_FIXTURE, TYPED_DECISION_FIXTURE):
        assert isinstance(load_synthetic_fixture(path), dict)
    with pytest.raises(FixtureProvenanceError):
        load_synthetic_fixture(TELEHEALTH_FIXTURE)


def test_derived_route_prefers_structure_then_long_term() -> None:
    assert derived_route(Y, Y) == "structure"
    assert derived_route(N, Y) == "long_term"
    assert derived_route(N, N) == "none"
    assert derived_route(U, N) == "unknown"


def _front(expected: str, structure: NeedAnswer, long_term: NeedAnswer, n: int) -> list[FrontDoorRow]:
    return [FrontDoorRow(f"{expected}-{i}", expected, structure, long_term, 100, None) for i in range(n)]


def test_front_door_scoring_applies_the_existing_routing_gates() -> None:
    perfect = (
        _front("prior_memory", N, Y, 15)
        + _front("knowledge", N, Y, 15)
        + _front("structure", Y, N, 15)
        + _front("none", N, N, 15)
    )
    score = score_front_door(perfect)
    assert score["accuracy"] == 1.0 and all(score["gates"].values())

    leaky = [*perfect[:-1], FrontDoorRow("prior-x", "prior_memory", N, N, 100, None)]
    assert score_front_door(leaky)["gates"]["none_precision"] is False

    cautious = _front("prior_memory", N, Y, 15) + _front("none", U, U, 15)
    assert score_front_door(cautious)["none_precision"] == 1.0  # nothing predicted none


def test_latency_uses_nearest_rank_and_the_600ms_share() -> None:
    passing = score_latency([100] * 57 + [700] * 3)
    assert (passing["p95_ms"], passing["share_over_deadline"]) == (100, 0.05)
    assert all(passing["gates"].values())
    assert score_latency([100] * 56 + [700] * 4)["gates"]["share_over_deadline"] is False
    assert score_latency([100] * 49)["gates"]["samples"] is False


def test_relevance_gate_fails_if_any_relevant_candidate_is_dropped() -> None:
    rows = [
        RelevanceRow("t", "a", "relevant", False),
        RelevanceRow("t", "n", "noise", True),
    ]
    assert score_relevance(rows)["gates"]["relevant_dropped"] is True
    rows.append(RelevanceRow("t", "b", "relevant", True))
    assert score_relevance(rows)["gates"]["relevant_dropped"] is False


def test_extraction_gate_needs_the_ollama_baseline() -> None:
    jev = [ExtractionRow("jev", "e1", True, "decision", True, "decision")]
    assert score_extraction(jev)["gates"] == {"baseline": "not_evaluated"}
    ollama = [ExtractionRow("ollama", "e1", True, "decision", False, None)]
    assert all(value is True for value in score_extraction(jev + ollama)["gates"].values())


def test_tier_gate_requires_heavy_recall_of_095() -> None:
    rows = [TierRow(f"h{i}", "heavy", "edit", "heavy", "threshold", "edit", False) for i in range(19)]
    rows.append(TierRow("h19", "heavy", "edit", "light", "light", "edit", False))
    assert score_tier(rows)["gates"]["heavy_recall"] is True  # 19/20 = 0.95
    rows.append(TierRow("h20", "heavy", "edit", "light", "light", "edit", False))
    assert score_tier(rows)["gates"]["heavy_recall"] is False


def test_phase_one_completion_requires_every_gate_true() -> None:
    report: dict[str, Any] = {
        section: {"gates": {"g": True}}
        for section in ("front_door", "latency", "relevance", "extraction", "tier")
    }
    assert phase_one_complete(report) is True
    report["extraction"]["gates"] = {"baseline": "not_evaluated"}
    assert phase_one_complete(report) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/evals/test_typed_decision_evaluation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.typed_decision_evaluation'`

- [ ] **Step 3: Write the implementation**

```python
# scripts/typed_decision_evaluation.py
"""Phase-1 typed-decision evaluation over synthetic fixtures (spec §8).

This module never opens a network connection itself. Callers pass a guarded classifier whose
source is ``synthetic_fixture``, and only fixtures that declare synthetic provenance are read.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from mnemo_memory.packages.domain import TypedDecisionUnavailableReason
from mnemo_memory.packages.model_gateway.cascade_router import (
    AxisRoutedClassifier,
    ClassifierResult,
)
from mnemo_memory.packages.model_gateway.decision_axes import (
    COMPLEXITY,
    EPISODIC_KIND,
    FRONT_DOOR_AXES,
    TOOL_NEED,
    WORTH_REMEMBERING,
    NeedAnswer,
    accepted_choice,
    hint_eligible,
    need_from_result,
    relevance_axis,
    should_drop_candidate,
    tier_committee,
    worth_extracting,
)
from mnemo_memory.packages.model_gateway.episodic_extraction import parse_episodic_output
from mnemo_memory.packages.model_gateway.rule_axes import RISK_AXIS, RiskTermClassifier
from mnemo_memory.packages.model_gateway.typed_decisions import (
    GuardedTypedDecisionClassifier,
    decide_tier,
)

REPOSITORY_ROOT = Path(__file__).parents[1]
_FIXTURES = REPOSITORY_ROOT / "tests/fixtures/evals"
ROUTING_FIXTURE = _FIXTURES / "automatic-context-routing-v1.json"
VIABILITY_FIXTURE = _FIXTURES / "viability-corpus-v1.json"
TYPED_DECISION_FIXTURE = _FIXTURES / "typed-decision-v1.json"
TELEHEALTH_FIXTURE = _FIXTURES / "telehealth-long-horizon-phase2-qwen25coder7b.json"

LATENCY_DEADLINE_MS = 600
LATENCY_MINIMUM_SAMPLES = 50
LATENCY_MAXIMUM_SHARE_OVER = 0.05
RELEVANCE_BATCH_SIZE = 8
_SYNTHETIC_PROVENANCE: tuple[object, ...] = (
    {
        "origin": "Mnemo-owned original synthetic prompts",
        "competing_product_artifacts_used": False,
    },
    "Original synthetic Mnemo evaluation data; no production, personal, secret, or competitor "
    "content.",
)
_EXPECTED_NEED = {
    "prior_memory": "long_term",
    "knowledge": "long_term",
    "structure": "structure",
    "none": "none",
}
_EPISODIC_KIND_BY_PREFIX = {"decision": "decision", "failure": "failure", "result": "outcome"}
_NOISE_SUMMARY = (
    "Background conversation {index:04d} for synthetic workflow {template}; it is unrelated "
    "and must not displace active task state."
)
_MEASURED_OUTCOMES = (None, TypedDecisionUnavailableReason.TIMEOUT.value)
_SECTIONS = ("front_door", "latency", "relevance", "extraction", "tier")


class FixtureProvenanceError(ValueError):
    """A fixture does not declare synthetic provenance, so it may not be sent to Jev."""


class EpisodicProvider(Protocol):
    def generate(self, request: object) -> object: ...


@dataclass(frozen=True, slots=True)
class FrontDoorRow:
    case_id: str
    expected_route: str
    structure: NeedAnswer
    long_term: NeedAnswer
    duration_ms: int
    unavailable_reason: str | None


@dataclass(frozen=True, slots=True)
class RelevanceRow:
    template_id: str
    candidate_id: str
    category: str  # "relevant", "superseded" or "noise"
    dropped: bool


@dataclass(frozen=True, slots=True)
class ExtractionRow:
    arm: str  # "jev" or "ollama"
    event_id: str
    expected_worth: bool
    expected_kind: str | None
    predicted_worth: bool
    predicted_kind: str | None


@dataclass(frozen=True, slots=True)
class TierRow:
    case_id: str
    expected_tier: str
    expected_tool_need: str
    route: str
    reason: str
    tool_need: str | None
    hint: bool


@dataclass(frozen=True, slots=True)
class _OllamaRequest:
    summary: str
    max_candidates: int = 4


def load_synthetic_fixture(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("provenance") not in _SYNTHETIC_PROVENANCE:
        raise FixtureProvenanceError(path.name)
    return value


def derived_route(structure: NeedAnswer, long_term: NeedAnswer) -> str:
    if structure is NeedAnswer.YES:
        return "structure"
    if long_term is NeedAnswer.YES:
        return "long_term"
    if structure is NeedAnswer.NO and long_term is NeedAnswer.NO:
        return "none"
    return "unknown"


def score_front_door(rows: Sequence[FrontDoorRow]) -> dict[str, Any]:
    prior = [row for row in rows if row.expected_route == "prior_memory"]
    structure = [row for row in rows if row.expected_route == "structure"]
    derived = {row.case_id: derived_route(row.structure, row.long_term) for row in rows}
    predicted_none = [row for row in rows if derived[row.case_id] == "none"]
    missed = sorted(
        row.case_id for row in rows if derived[row.case_id] != _EXPECTED_NEED[row.expected_route]
    )
    accuracy = _share(len(rows) - len(missed), len(rows))
    prior_recall = _share(sum(row.long_term is NeedAnswer.YES for row in prior), len(prior))
    structure_recall = _share(
        sum(row.structure is NeedAnswer.YES for row in structure), len(structure)
    )
    none_precision = _share(
        sum(row.expected_route == "none" for row in predicted_none), len(predicted_none), empty=1.0
    )
    return {
        "cases": len(rows),
        "unavailable": sum(row.unavailable_reason is not None for row in rows),
        "accuracy": accuracy,
        "prior_memory_recall": prior_recall,
        "structure_recall": structure_recall,
        "none_predicted": len(predicted_none),
        "none_precision": none_precision,
        "missed_case_ids": missed,
        "gates": {
            "accuracy": accuracy >= 0.80,
            "prior_memory_recall": prior_recall >= 0.90,
            "structure_recall": structure_recall >= 0.80,
            "none_precision": none_precision == 1.0,
        },
    }


def score_latency(durations_ms: Sequence[int]) -> dict[str, Any]:
    ordered = sorted(durations_ms)
    share_over = _share(
        sum(value > LATENCY_DEADLINE_MS for value in ordered), len(ordered), empty=1.0
    )
    return {
        "samples": len(ordered),
        "p50_ms": _nearest_rank(ordered, 0.50),
        "p95_ms": _nearest_rank(ordered, 0.95),
        "max_ms": ordered[-1] if ordered else None,
        "deadline_ms": LATENCY_DEADLINE_MS,
        "share_over_deadline": share_over,
        "gates": {
            "samples": len(ordered) >= LATENCY_MINIMUM_SAMPLES,
            "share_over_deadline": share_over <= LATENCY_MAXIMUM_SHARE_OVER,
        },
    }


def score_relevance(rows: Sequence[RelevanceRow]) -> dict[str, Any]:
    relevant = [row for row in rows if row.category == "relevant"]
    dropped = sum(row.dropped for row in relevant)
    return {
        "candidates": len(rows),
        "relevant_dropped": dropped,
        "noise_drop_rate": _drop_rate(rows, "noise"),
        "superseded_drop_rate": _drop_rate(rows, "superseded"),
        "gates": {"relevant_dropped": bool(relevant) and dropped == 0},
    }


def score_extraction(rows: Sequence[ExtractionRow]) -> dict[str, Any]:
    arms: dict[str, dict[str, Any]] = {}
    for arm in ("jev", "ollama"):
        arm_rows = [row for row in rows if row.arm == arm]
        if not arm_rows:
            continue
        kinded = [row for row in arm_rows if row.expected_kind is not None]
        arms[arm] = {
            "events": len(arm_rows),
            "worth_accuracy": _share(
                sum(row.predicted_worth == row.expected_worth for row in arm_rows), len(arm_rows)
            ),
            "kind_accuracy": _share(
                sum(row.predicted_kind == row.expected_kind for row in kinded), len(kinded)
            ),
        }
    if "jev" not in arms or "ollama" not in arms:
        return {"arms": arms, "gates": {"baseline": "not_evaluated"}}
    return {
        "arms": arms,
        "gates": {
            "worth_accuracy": arms["jev"]["worth_accuracy"] >= arms["ollama"]["worth_accuracy"],
            "kind_accuracy": arms["jev"]["kind_accuracy"] >= arms["ollama"]["kind_accuracy"],
        },
    }


def score_tier(rows: Sequence[TierRow]) -> dict[str, Any]:
    heavy = [row for row in rows if row.expected_tier == "heavy"]
    light = [row for row in rows if row.expected_tier == "light"]
    heavy_recall = _share(sum(row.route == "heavy" for row in heavy), len(heavy))
    return {
        "cases": len(rows),
        "heavy_recall": heavy_recall,
        "light_recall": _share(sum(row.route == "light" for row in light), len(light)),
        "tool_need_accuracy": _share(
            sum(row.tool_need == row.expected_tool_need for row in rows), len(rows)
        ),
        "hint_eligible": sum(row.hint for row in rows),
        "unavailable": sum(row.reason.startswith("unavailable:") for row in rows),
        "missed_heavy_case_ids": sorted(row.case_id for row in heavy if row.route != "heavy"),
        "gates": {"heavy_recall": heavy_recall >= 0.95},
    }


async def evaluate_front_door(guard: GuardedTypedDecisionClassifier) -> list[FrontDoorRow]:
    rows: list[FrontDoorRow] = []
    for case in load_synthetic_fixture(ROUTING_FIXTURE)["cases"]:
        outcome = await guard.ask(FRONT_DOOR_AXES, case["prompt"])
        results = {result.axis_name: result for result in outcome.results}
        reason = outcome.unavailable_reason
        rows.append(
            FrontDoorRow(
                case["id"],
                case["expected_route"],
                need_from_result(results.get("needs_structure")),
                need_from_result(results.get("needs_long_term")),
                outcome.duration_ms,
                None if reason is None else reason.value,
            )
        )
    return rows


async def evaluate_relevance(guard: GuardedTypedDecisionClassifier) -> list[RelevanceRow]:
    rows: list[RelevanceRow] = []
    for template in load_synthetic_fixture(VIABILITY_FIXTURE)["templates"]:
        relevant = set(template["ground_truth"]["relevant_evidence"])
        candidates = [
            (
                event["event_key"],
                event["summary"],
                "relevant" if event["event_key"] in relevant else "superseded",
            )
            for event in template["events"]
        ]
        candidates += [
            (
                f"noise-{index}",
                _NOISE_SUMMARY.format(index=index, template=template["template_id"]),
                "noise",
            )
            for index in (1, 2)
        ]
        for start in range(0, len(candidates), RELEVANCE_BATCH_SIZE):
            batch = candidates[start : start + RELEVANCE_BATCH_SIZE]
            axes = tuple(relevance_axis(index, item[1]) for index, item in enumerate(batch))
            outcome = await guard.ask(axes, template["task_prompt"])
            results = {result.axis_name: result for result in outcome.results}
            for index, (candidate_id, _, category) in enumerate(batch):
                rows.append(
                    RelevanceRow(
                        template["template_id"],
                        candidate_id,
                        category,
                        should_drop_candidate(results.get(f"helps_{index}")),
                    )
                )
    return rows


def extraction_events() -> list[tuple[str, str, bool, str | None]]:
    events: list[tuple[str, str, bool, str | None]] = []
    for template in load_synthetic_fixture(VIABILITY_FIXTURE)["templates"]:
        for event in template["events"]:
            prefix, _, body = event["summary"].partition(": ")
            events.append(
                (
                    event["event_key"],
                    body or event["summary"],
                    True,
                    _EPISODIC_KIND_BY_PREFIX.get(prefix),
                )
            )
    for negative in load_synthetic_fixture(TYPED_DECISION_FIXTURE)["worth_negatives"]:
        events.append((negative["id"], negative["summary"], False, None))
    return events


async def evaluate_extraction_jev(guard: GuardedTypedDecisionClassifier) -> list[ExtractionRow]:
    rows: list[ExtractionRow] = []
    for event_id, summary, worth, kind in extraction_events():
        outcome = await guard.ask((WORTH_REMEMBERING, EPISODIC_KIND), summary)
        results = {result.axis_name: result for result in outcome.results}
        rows.append(
            ExtractionRow(
                "jev",
                event_id,
                worth,
                kind,
                worth_extracting(results.get(WORTH_REMEMBERING.name)),
                accepted_choice(results.get(EPISODIC_KIND.name)),
            )
        )
    return rows


def evaluate_extraction_ollama(provider: EpisodicProvider) -> list[ExtractionRow]:
    rows: list[ExtractionRow] = []
    for event_id, summary, worth, kind in extraction_events():
        try:
            proposals = parse_episodic_output(provider.generate(_OllamaRequest(summary)), 4)
        except Exception:
            proposals = ()
        rows.append(
            ExtractionRow(
                "ollama",
                event_id,
                worth,
                kind,
                bool(proposals),
                proposals[0].kind.value if proposals else None,
            )
        )
    return rows


async def evaluate_tier(guard: GuardedTypedDecisionClassifier) -> list[TierRow]:
    classifier = AxisRoutedClassifier(
        {COMPLEXITY.name: guard, TOOL_NEED.name: guard, RISK_AXIS.name: RiskTermClassifier()}
    )
    committee = tier_committee()
    rows: list[TierRow] = []
    for case in load_synthetic_fixture(TYPED_DECISION_FIXTURE)["tier_cases"]:
        tier = await decide_tier(committee, classifier, case["prompt"])
        results: dict[str, ClassifierResult] = (
            {} if tier.decision is None else {r.axis_name: r for r in tier.decision.results}
        )
        tool_need = results.get(TOOL_NEED.name)
        rows.append(
            TierRow(
                case["id"],
                case["expected_tier"],
                case["expected_tool_need"],
                tier.route,
                tier.reason,
                accepted_choice(tool_need),
                hint_eligible(tier.route, tool_need),
            )
        )
    return rows


def phase_one_complete(report: dict[str, Any]) -> bool:
    return all(
        value is True for section in _SECTIONS for value in report[section]["gates"].values()
    )


async def run_phase_one(
    guard: GuardedTypedDecisionClassifier, ollama: EpisodicProvider | None
) -> dict[str, Any]:
    front = await evaluate_front_door(guard)
    extraction = await evaluate_extraction_jev(guard)
    if ollama is not None:
        extraction += evaluate_extraction_ollama(ollama)
    report: dict[str, Any] = {
        "front_door": score_front_door(front),
        "latency": score_latency(
            [row.duration_ms for row in front if row.unavailable_reason in _MEASURED_OUTCOMES]
        ),
        "relevance": score_relevance(await evaluate_relevance(guard)),
        "extraction": score_extraction(extraction),
        "tier": score_tier(await evaluate_tier(guard)),
        "excluded_fixtures": _excluded_fixtures(),
    }
    report["phase_1_complete"] = phase_one_complete(report)
    return report


def _excluded_fixtures() -> dict[str, str]:
    try:
        load_synthetic_fixture(TELEHEALTH_FIXTURE)
    except FixtureProvenanceError:
        return {TELEHEALTH_FIXTURE.name: "no synthetic provenance declared"}
    return {}


def _share(numerator: int, denominator: int, *, empty: float = 0.0) -> float:
    return numerator / denominator if denominator else empty


def _drop_rate(rows: Sequence[RelevanceRow], category: str) -> float:
    selected = [row for row in rows if row.category == category]
    return _share(sum(row.dropped for row in selected), len(selected))


def _nearest_rank(ordered: Sequence[int], quantile: float) -> int | None:
    if not ordered:
        return None
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/evals/test_typed_decision_evaluation.py -v`
Expected: 8 passed

- [ ] **Step 5: Lint, type-check and commit**

```bash
uv run ruff format scripts/typed_decision_evaluation.py tests/evals/test_typed_decision_evaluation.py
uv run ruff check --fix scripts/typed_decision_evaluation.py tests/evals/test_typed_decision_evaluation.py
uv run mypy
git add scripts/typed_decision_evaluation.py tests/evals/test_typed_decision_evaluation.py
git commit -m "feat(evals): phase-1 typed-decision scoring over synthetic fixtures" -- \
  scripts/typed_decision_evaluation.py tests/evals/test_typed_decision_evaluation.py
```

---

### Task 11: Live-authorized evaluation CLI

**Files:**
- Create: `scripts/run_typed_decision_evaluation.py`
- Modify: `package.json` (add the `eval:typed-decisions` script)
- Test: `tests/evals/test_run_typed_decision_evaluation.py`

**Interfaces:**
- Consumes: `run_phase_one` and `REPOSITORY_ROOT` (Task 10), `JevClassifier`, `JevTransport` and `JEV_DEFAULT_MODEL` (Task 6), `GuardedTypedDecisionClassifier` and `TypedDecisionRecord` (Task 5), and `OllamaEpisodicProvider` (existing).
- Produces:
  - `main(argv: Sequence[str] | None = None, *, environ: Mapping[str, str] | None = None, jev_transport: JevTransport | None = None, ollama_transport: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None) -> int`
  - Exit codes: `2` means refused, `1` means phase 1 is not complete, `0` means complete.
  - It writes `evaluation-results/typed-decisions/<run-id>/report.json`. That path is gitignored, and the file is content-free.

- [ ] **Step 1: Write the failing tests**

```python
# tests/evals/test_run_typed_decision_evaluation.py
"""End-to-end CLI checks with an oracle transport; no network, no real key."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.run_typed_decision_evaluation import main

FIXTURES = Path(__file__).parents[1] / "fixtures/evals"
KEY = "test-key-not-real-0000"
ROUTING = json.loads((FIXTURES / "automatic-context-routing-v1.json").read_text("utf-8"))
TYPED = json.loads((FIXTURES / "typed-decision-v1.json").read_text("utf-8"))
VIABILITY = json.loads((FIXTURES / "viability-corpus-v1.json").read_text("utf-8"))
EXPECTED_ROUTE = {case["prompt"]: case["expected_route"] for case in ROUTING["cases"]}
TIER = {case["prompt"]: (case["expected_tier"], case["expected_tool_need"]) for case in TYPED["tier_cases"]}
NEGATIVES = {item["summary"] for item in TYPED["worth_negatives"]}
KIND_BY_SUMMARY: dict[str, str | None] = {}
for _template in VIABILITY["templates"]:
    for _event in _template["events"]:
        _prefix, _, _body = _event["summary"].partition(": ")
        KIND_BY_SUMMARY[_body] = {"decision": "decision", "failure": "failure", "result": "outcome"}.get(
            _prefix
        )


def _noul(probability: float) -> dict[str, object]:
    return {"type": "noul", "noul": probability}


def _choice(labels: Sequence[str], chosen: str, confidence: float) -> dict[str, object]:
    others = [label for label in labels if label != chosen]
    probabilities = {label: 0.1 / len(others) for label in others}
    probabilities[chosen] = 0.9
    return {"type": "choice", "choice": chosen, "confidence": confidence, "probabilities": probabilities}


class OracleTransport:
    """Answers from fixture labels so every gate should pass."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        self.calls += 1
        request = json.loads(body)
        state = request["state"]
        route = EXPECTED_ROUTE.get(state)
        tier, tool = TIER.get(state, ("heavy", "read_heavy"))
        answers: dict[str, dict[str, object]] = {}
        for name, question in request["questions"].items():
            if name == "needs_long_term":
                answers[name] = _noul(0.95 if route in {"prior_memory", "knowledge"} else 0.05)
            elif name == "needs_structure":
                answers[name] = _noul(0.95 if route == "structure" else 0.05)
            elif name == "complexity":
                answers[name] = _choice(("light", "heavy"), tier, 0.8)
            elif name == "tool_need":
                answers[name] = _choice(("none", "read_heavy", "edit"), tool, 0.8)
            elif name.startswith("helps_"):
                noise = "Background conversation" in question["instructions"]
                answers[name] = _noul(0.05 if noise else 0.9)
            elif name == "worth_remembering":
                answers[name] = _noul(0.05 if state in NEGATIVES else 0.9)
            elif name == "episodic_kind":
                kind = KIND_BY_SUMMARY.get(state)
                labels = ("decision", "failure", "outcome", "lesson", "preference")
                answers[name] = _choice(labels, kind or "decision", 0.8 if kind else 0.3)
            else:
                raise AssertionError(f"unexpected question {name}")
        return json.dumps(
            {"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 100}}
        ).encode()


def _empty_ollama(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"response": json.dumps({"candidates": []})}


def _run(tmp_path: Path, *extra: str, transport: OracleTransport, env: dict[str, str] | None = None) -> int:
    return main(
        ["--run-id", "test-run", "--results-root", str(tmp_path), *extra],
        environ={"TYPESAFE_API_KEY": KEY} if env is None else env,
        jev_transport=transport,
        ollama_transport=_empty_ollama,
    )


def test_cli_refuses_without_live_authorization_or_credential(tmp_path: Path) -> None:
    transport = OracleTransport()
    assert _run(tmp_path, transport=transport) == 2
    assert _run(tmp_path, "--live-calls-authorized", transport=transport, env={}) == 2
    assert transport.calls == 0
    assert not (tmp_path / "test-run").exists()


def test_cli_passes_every_gate_with_an_oracle_and_ollama_baseline(tmp_path: Path) -> None:
    transport = OracleTransport()
    code = _run(tmp_path, "--live-calls-authorized", "--ollama-model", "fake", transport=transport)
    report = json.loads((tmp_path / "test-run" / "report.json").read_text("utf-8"))
    assert code == 0 and report["phase_1_complete"] is True
    assert report["run"]["model_versions"] == ["jev-1.13.0"]
    assert report["run"]["pinned_model_only"] is True
    assert report["excluded_fixtures"] == {
        "telehealth-long-horizon-phase2-qwen25coder7b.json": "no synthetic provenance declared"
    }
    assert report["latency"]["samples"] == 60


def test_cli_is_incomplete_without_the_ollama_baseline(tmp_path: Path) -> None:
    code = _run(tmp_path, "--live-calls-authorized", transport=OracleTransport())
    report = json.loads((tmp_path / "test-run" / "report.json").read_text("utf-8"))
    assert code == 1
    assert report["extraction"]["gates"] == {"baseline": "not_evaluated"}


def test_cli_report_is_content_free_and_key_free(tmp_path: Path) -> None:
    _run(tmp_path, "--live-calls-authorized", "--ollama-model", "fake", transport=OracleTransport())
    text = (tmp_path / "test-run" / "report.json").read_text("utf-8")
    assert KEY not in text
    assert ROUTING["cases"][0]["prompt"] not in text
    assert TYPED["tier_cases"][0]["prompt"] not in text


def test_cli_refuses_to_overwrite_an_existing_report(tmp_path: Path) -> None:
    (tmp_path / "test-run").mkdir()
    (tmp_path / "test-run" / "report.json").write_text("{}", encoding="utf-8")
    transport = OracleTransport()
    assert _run(tmp_path, "--live-calls-authorized", transport=transport) == 2
    assert transport.calls == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/evals/test_run_typed_decision_evaluation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.run_typed_decision_evaluation'`

- [ ] **Step 3: Write the implementation**

```python
# scripts/run_typed_decision_evaluation.py
"""Run the phase-1 typed-decision evaluation against Jev on synthetic fixtures only.

Live calls need both --live-calls-authorized (the maintainer's explicit go-ahead) and
TYPESAFE_API_KEY in the environment. Example:

    npm run eval:typed-decisions -- --run-id 2026-10-01-a --live-calls-authorized \
        --ollama-model qwen2.5-coder:7b
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from mnemo_memory.connectors.ollama import OllamaEpisodicProvider
from mnemo_memory.connectors.typesafe import JEV_DEFAULT_MODEL, JevClassifier, JevTransport
from mnemo_memory.packages.domain import (
    ModelBudgetDenied,
    ModelBudgetReservation,
    ModelTaskType,
    TypedDecisionDataRoute,
    TypedDecisionSource,
    WorkspaceId,
)
from mnemo_memory.packages.model_gateway.typed_decisions import (
    GuardedTypedDecisionClassifier,
    TypedDecisionRecord,
)
from scripts.typed_decision_evaluation import REPOSITORY_ROOT, run_phase_one

DEFAULT_RESULTS_ROOT = REPOSITORY_ROOT / "evaluation-results" / "typed-decisions"
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_EVALUATION_WORKSPACE = WorkspaceId(UUID(int=1))
_RESERVATION = ModelBudgetReservation(input_tokens=2_000, output_tokens=1, cost_microusd=0)


class CallCapBudget:
    """Deny every reservation after ``maximum_calls`` so a runaway run cannot spend freely."""

    def __init__(self, maximum_calls: int) -> None:
        self.maximum_calls = maximum_calls
        self.calls = 0

    def reserve(
        self,
        workspace_id: WorkspaceId,
        task_type: ModelTaskType,
        reservation: ModelBudgetReservation,
    ) -> None:
        if self.calls >= self.maximum_calls:
            raise ModelBudgetDenied("MNEMO_TYPED_DECISION_EVALUATION_CALL_CAP")
        self.calls += 1


class VersionRecorder:
    """Collect content-free run totals from guard telemetry."""

    def __init__(self) -> None:
        self.versions: set[str] = set()
        self.input_tokens = 0
        self.answered = 0

    def record(self, record: TypedDecisionRecord) -> None:
        if record.model_version is not None:
            self.versions.add(record.model_version)
        self.input_tokens += record.input_tokens
        self.answered += int(record.outcome == "answered")


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    jev_transport: JevTransport | None = None,
    ollama_transport: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--live-calls-authorized", action="store_true")
    parser.add_argument("--max-calls", type=int, default=400)
    parser.add_argument("--deadline-seconds", type=float, default=5.0)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--ollama-model")
    args = parser.parse_args(argv)
    variables = os.environ if environ is None else environ

    if not _RUN_ID.fullmatch(args.run_id):
        return _refuse("--run-id must be 1-64 letters, digits, '.', '_' or '-'")
    if not args.live_calls_authorized:
        return _refuse("live Jev calls need --live-calls-authorized (maintainer go-ahead)")
    api_key = variables.get("TYPESAFE_API_KEY", "").strip()
    if not api_key:
        return _refuse("TYPESAFE_API_KEY is not set")
    output = args.results_root / args.run_id / "report.json"
    if output.exists():
        return _refuse(f"{output} already exists")

    recorder = VersionRecorder()
    budget = CallCapBudget(args.max_calls)
    guard = GuardedTypedDecisionClassifier(
        JevClassifier(api_key, transport=jev_transport),
        data_route=TypedDecisionDataRoute.SYNTHETIC_ONLY,
        source=TypedDecisionSource.SYNTHETIC_FIXTURE,
        budget=budget,
        workspace_id=_EVALUATION_WORKSPACE,
        reservation=_RESERVATION,
        deadline_seconds=args.deadline_seconds,
        recorder=recorder,
    )
    ollama = (
        None
        if args.ollama_model is None
        else OllamaEpisodicProvider(args.ollama_url, args.ollama_model, transport=ollama_transport)
    )
    report = asyncio.run(run_phase_one(guard, ollama))
    pinned = recorder.versions == {JEV_DEFAULT_MODEL}
    report["run"] = {
        "run_id": args.run_id,
        "model_versions": sorted(recorder.versions),
        "pinned_model_only": pinned,
        "answered_requests": recorder.answered,
        "reserved_calls": budget.calls,
        "input_tokens": recorder.input_tokens,
        "ollama_model": args.ollama_model,
    }
    report["phase_1_complete"] = bool(report["phase_1_complete"]) and pinned
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    status = "complete" if report["phase_1_complete"] else "NOT complete"
    print(f"typed-decision phase 1 {status}: {output}")
    return 0 if report["phase_1_complete"] else 1


def _refuse(message: str) -> int:
    print(f"refusing: {message}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

In `package.json`, add this line to `"scripts"` after `"eval:viability"`:

```json
    "eval:typed-decisions": "uv run python -m scripts.run_typed_decision_evaluation",
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/evals/test_run_typed_decision_evaluation.py -v`
Expected: 5 passed

- [ ] **Step 5: Lint, type-check and commit**

```bash
uv run ruff format scripts/run_typed_decision_evaluation.py tests/evals/test_run_typed_decision_evaluation.py
uv run ruff check --fix scripts/run_typed_decision_evaluation.py tests/evals/test_run_typed_decision_evaluation.py
uv run mypy
git add scripts/run_typed_decision_evaluation.py tests/evals/test_run_typed_decision_evaluation.py
git commit -m "feat(evals): live-authorized typed-decision evaluation CLI" -- \
  scripts/run_typed_decision_evaluation.py tests/evals/test_run_typed_decision_evaluation.py package.json
```

---

### Task 12: ADR 0049 rewrite, threat model, status and the full gate

**Files:**
- Replace: the untracked, never-accepted draft `docs/adr/0049-jev-shadow-memory-need-classifier.md`. Move it with plain `mv` to `docs/adr/0049-hosted-typed-classifier-and-cheap-model-tier.md` and rewrite it.
- Modify: `docs/threat-model.md` (new scenario before `## Security gates and ownership`)
- Modify: `docs/local-first-token-efficiency-plan.md` (item 3 note)
- Modify: `docs/adr/0045-compact-local-memory-router.md` (a link line only; accepted ADRs are immutable except for links)
- Modify: `docs/implementation-status.md` (the section from Task 1)

**Interfaces:** documentation only. No later task depends on it.

- [ ] **Step 1: Replace ADR 0049**

```bash
mv docs/adr/0049-jev-shadow-memory-need-classifier.md docs/adr/0049-hosted-typed-classifier-and-cheap-model-tier.md
```

Write this content to the new file:

```markdown
# ADR 0049: Hosted typed classifier and cheap-model tier

- **Status:** proposed
- **Date:** 2026-09-30
- **Deciders:** Mnemo maintainer
- **Issue:** Jev typed-decision routing, phase 1
  (`docs/superpowers/specs/2026-09-30-jev-typed-decision-routing-design.md`)
- **Supersedes:** none. It amends ADR 0045's local-only routing. For typed decisions only, it
  supersedes the "make no hosted-provider call" clause of item 3 in
  `docs/local-first-token-efficiency-plan.md`.
- **Superseded by:** none

## Context

Mnemo makes many closed decisions with keyword rules and a small local word-count classifier
(ADR 0045, ADR 0046). Examples are whether a prompt needs stored memory, whether a snippet is
relevant, and whether an event is worth remembering. Local Ollama extraction can take up to 30
seconds, and nothing checks correctness before a candidate is approved.

TypeSafe's Jev is a hosted classifier. It returns one value from a closed list with a probability,
cannot generate text, and bills $0.042 per million input tokens with output free. One request
answers many questions about one text. The maintainer holds an API key.

On 2026-09-29, 11 synthetic calls to pinned `jev-1.13.0` (4 questions each) took 0.37–0.52 s on
fresh connections and 0.27–0.45 s on reused ones. Processing time dominated.

TypeSafe's standard terms allow retention:
- The Master Customer Agreement (2026-09-23) §4.1 permits storing Input and Output. Since
  2026-09-19, it also keeps a perpetual right to use Customer Data for telemetry, abuse
  monitoring and legal compliance.
- The Data Processing Agreement (2026-04-24) keeps data "as long as necessary".
- The Privacy Policy (2025-11-19) promises no training on Input.
- Zero data retention (ZDR) is offered only to enterprise customers through sales. Vercel's AI
  Gateway offers per-request ZDR under a negotiated clause.

Outside this decision: routing or proxying the coding agent's own model (ADR 0048).

## Decision

1. **One port.** Every closed decision is asked through `PromptClassifier`, with
   `CascadeCommittee` producing `light` or `heavy` (`packages/model_gateway/cascade_router.py`).
   Local rule axes and hosted axes may share one committee, and a local risk axis can veto a light
   verdict.
2. **One guard.** `GuardedTypedDecisionClassifier` runs data route, credential, request shape,
   secret scan (text and all question text), sensitivity (only `normal`), size (512-character
   text, 400-character question text), budget (`ModelTaskType.TYPED_DECISION`), deadline and label
   check, in that order. Each failure falls back to today's rules with one closed reason. The
   guard never raises from `ask`.
3. **Data route.** `TypedDecisionDataRoute` has one value, `synthetic_only`, and runtime text is
   refused before any network call. Adding a route requires all three of the following:
   - signed ZDR terms covering request logs, abuse-monitoring copies, backups and telemetry
     derived from content, obtained through Vercel or TypeSafe enterprise
   - redaction of names and identifiers before the guard
   - an amendment to this ADR that records those terms
4. **Model pinning.** Jev is pinned to `jev-1.13.0`. Thresholds are recorded with the model
   version, and a model change re-runs the phase-1 evaluation.
5. **Latency.** The per-prompt deadline is 600 ms. No per-prompt decision goes live until at most
   5% of at least 50 cold round trips exceed it.
6. **Defaults.** Settings default to off. The key comes only from `TYPESAFE_API_KEY`.
7. **Model output is an untrusted proposal.** Jev may filter or propose. It never approves,
   supersedes or deletes memory.

## Alternatives considered

- **Keep local rules and Ollama only (status quo).** This sends nothing off the machine. It keeps
  the weak keyword routing, has no pre-approval correctness check, and keeps Ollama's up-to-30 s
  extraction. It stays the fallback everywhere.
- **Accept standard TypeSafe terms for real prompts.** This is faster to value, but it allows
  open-ended retention and perpetual rights over derived telemetry. It is rejected until ZDR
  exists.
- **Haiku structured output for every decision.** This is simpler, but it costs more per call and
  has no calibrated probability for gating. Haiku remains the planned light tier for writing
  (phase 2).
- **A long-lived warm-connection process.** It saves about 0.1 s per call and adds a new moving
  part. It is rejected on the measured latency split.
- **Defer.** This is viable, but phase 1 has no privacy cost and produces the measurements the
  decision needs.

## Consequences

- Positive: one boundary owns every hosted decision, and the fallback is always today's
  behavior. Phase 1 measures accuracy and latency without real data.
- Negative: there is a new vendor dependency on the hosted path, a per-prompt latency cost of up
  to 600 ms once live, and vendor terms that changed twice in the week after launch.
- Follow-up: phase 2 (ZDR route, redaction, Haiku connector, shadow runs on real traffic) and
  phase 3 (write-path decisions), each gated by the spec's evaluations.

## Security and privacy implications

- Assets: prompts, stored memory snippets and event summaries.
- Phase 1 sends only synthetic fixture text, and runtime text is blocked before any network call.
  The secret scan covers every string sent, including snippets embedded in question text.
  Non-`normal` sensitivity is never sent.
- The key never appears in settings, logs, telemetry, reports, `repr` or exception text.
  Telemetry and reports are content-free.
- Required tests: data-route blocking with zero transport calls, secret and sensitivity
  blocking, deadline, schema rejection, key redaction, and the architecture guards (only the
  runtime composition imports the connector; the policy modules import no network library).
- See the threat-model entry "Hosted typed-decision classifier data exposure".

## Token and cost implications

Jev requests do not enter the agent's context. The measured cost was about $0.00002 per
front-door request (485 input tokens). The phase-1 evaluation makes about 180 requests. Savings
claims wait for client-reported usage in phase 2; none are made from character estimates.

## Dependency and licensing implications

No package is added: the connector uses standard-library `urllib`. TypeSafe is a hosted service
under proprietary terms, not an installable component. The dependency register records only
installable components under approved open-source licenses, so the vendor terms and their
versions are recorded here and in the threat model instead.

## Reversal or migration strategy

Every setting defaults to off, and removing the settings fields is covered by the migrated
defaults. Deleting `connectors/typesafe` and the runtime composition leaves today's behavior.
Nothing persists provider output.

## Verification

- The unit, architecture and evaluation tests named in the phase-1 plan.
- `npm run check`.
- The maintainer-authorized phase-1 evaluation report, with every gate true, before any
  phase-2 work.

## References

- `docs/superpowers/specs/2026-09-30-jev-typed-decision-routing-design.md`
- `docs/superpowers/plans/2026-09-30-jev-typed-decision-routing-phase-1.md`
- `reports/TypeSafe Jev data retention.md` and `research_notes/TypeSafe Jev data retention/`
- TypeSafe API reference: https://docs.typesafe.ai/api.md
- TypeSafe legal index: https://docs.typesafe.ai/legal.md
- ADR 0043, ADR 0045, ADR 0046, ADR 0047, ADR 0048
```

- [ ] **Step 2: Add the threat-model scenario**

In `docs/threat-model.md`, insert this immediately before the line `## Security gates and ownership`:

```markdown
### Hosted typed-decision classifier data exposure

**Scenario:** A prompt, stored memory snippet or event summary is sent to TypeSafe's Jev API.
TypeSafe's standard terms allow storage and perpetual telemetry use. A snippet embedded in
question text carries a secret past a prompt-only scan. A runtime caller claims to be a
synthetic fixture. The API key reaches a log, report or exception. A slow provider delays the
hook.

**Required controls:** The data route is a closed enum whose only phase-1 value,
`synthetic_only`, blocks every runtime request before credential, budget or network work. The
runtime composition binds the runtime source itself and exposes no source parameter. The guard
secret-scans the text and every question string before sending, never sends non-`normal`
sensitivity, bounds text to 512 characters and question text to 400, reserves
`typed_decision` budget, and enforces a hard deadline (600 ms per prompt) with no retry. The
connector reads the key only from `TYPESAFE_API_KEY`, redacts it from `repr`, and raises
payload-free errors without chained causes. Telemetry and evaluation reports record counts,
reasons, durations and model versions only. Only fixtures that declare synthetic provenance may
be sent, and live evaluation needs `--live-calls-authorized`. Adding a data route requires
signed zero-data-retention terms, name and identifier redaction, and an ADR 0049 amendment.
```

- [ ] **Step 3: Add the plan note and the ADR 0045 link**

In `docs/local-first-token-efficiency-plan.md`, append this sentence to the end of item 3 ("Local semantic compiler"):

```markdown
   *Amended by ADR 0049:* typed decisions may call the hosted Jev classifier behind its
   data-route gate. The semantic compiler itself still makes no hosted-provider call.
```

In `docs/adr/0045-compact-local-memory-router.md`, add this line directly under the `## Status` paragraph:

```markdown
Amended by [ADR 0049](0049-hosted-typed-classifier-and-cheap-model-tier.md) for hosted typed
classification behind a data-route gate.
```

- [ ] **Step 4: Update the status section**

In `docs/implementation-status.md`, replace the heading added in Task 1 with the one below. Use today's date from `date +%F`:

```markdown
### Jev typed-decision routing phase 1 — Implemented; live evaluation pending (<today>)
```

Then append this paragraph to that section:

```markdown
Implemented:
- the typed-decision domain vocabularies
- the `light`/`heavy` cascade port with batching and mixed-source axes
- a deterministic risk veto axis
- the phase-1 question catalogue
- `GuardedTypedDecisionClassifier`
- the TypeSafe Jev connector
- default-off settings
- a runtime composition that is blocked by the data route
- a synthetic tier and worth fixture
- the live-authorized evaluation CLI (`npm run eval:typed-decisions`)

No runtime path calls the classifier, and ADR 0049 remains proposed. Phase 2 needs every phase-1
gate true in a maintainer-authorized report, plus signed zero-data-retention terms.
```

- [ ] **Step 5: Run the full gate**

```bash
npm run check
```

Expected: every step passes. If `postgres:check` cannot reach a local PostgreSQL, say so. Then run the other steps individually (`format:check`, `lint`, `typecheck`, `test`, `schema:check`, `dependencies:check`, `architecture:check`, `package:check`) and report the PostgreSQL step as not run. Do not claim it passed.

- [ ] **Step 6: Commit**

```bash
git add docs/adr/0049-hosted-typed-classifier-and-cheap-model-tier.md
git commit -m "docs: ADR 0049 hosted typed classifier, threat model, phase-1 status" -- \
  docs/adr/0049-hosted-typed-classifier-and-cheap-model-tier.md \
  docs/threat-model.md \
  docs/local-first-token-efficiency-plan.md \
  docs/adr/0045-compact-local-memory-router.md \
  docs/implementation-status.md
```

---

### Task 13: Maintainer-authorized live evaluation (STOP: needs explicit go-ahead)

**Files:** none are committed. The report is written under the gitignored `evaluation-results/`.

This task sends synthetic fixture text to TypeSafe and spends a small amount of money (about 180 requests, well under $0.01). **Do not run it** until the maintainer says so in the current conversation. An earlier approval does not carry over.

- [ ] **Step 1: Confirm authorization and the key without printing it**

```bash
[ -n "$TYPESAFE_API_KEY" ] && echo "key loaded" || echo "TYPESAFE_API_KEY not set"
```

If the key is not set, ask the maintainer to put `export TYPESAFE_API_KEY=...` in `~/.zshenv` and restart the session. Never ask them to paste the key into the conversation.

- [ ] **Step 2: Run the evaluation**

```bash
npm run eval:typed-decisions -- --run-id "$(date +%F)-phase1-a" --live-calls-authorized \
  --ollama-model "<the maintainer's Ollama model id>"
```

Ask the maintainer for the Ollama model ID. If Ollama is not running, run without `--ollama-model`. The extraction gate will then report `not_evaluated`, and phase 1 stays incomplete.

- [ ] **Step 3: Report the results**

Summarize for the maintainer:
- each gate's value and threshold
- latency p50, p95 and max, and the share over 600 ms
- `missed_case_ids` and `missed_heavy_case_ids`
- the model versions seen

Do not tune thresholds in the same step. Propose each threshold change as its own change, with the report as evidence.
