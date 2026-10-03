# Jev Hook Wiring (Phase 2, Part 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Jev's four per-prompt decisions (memory need, filler check, task-size hint, skill pick) into the automatic-memory prompt hook with `off` / `shadow` / `live` modes, keep `live` locked behind the `synthetic_only` data route, and prove the wiring with byte-identity contract tests and a synthetic fresh-process replay.

**Architecture:** One new CLI-layer module, `apps/cli/typed_decision_hook.py`, holds pure combine functions and one async step (`ask_each`, one `asyncio.run`, 0.8 s cap). `apps/cli/main.py` keeps today's rules render as a function, runs the typed step only when a mode is on, and applies live answers on top of the rules result. `packages/application` gains only Jev-free pieces (planner `typed_needs`, `get_context item_ids`, settings locks); the guard is still built only by `apps/cli/typed_decision_composition.py`.

**Tech Stack:** Python 3.12, standard library only (asyncio, fcntl, json, re), Typer CLI, FastMCP tools, pytest, mypy strict, ruff.

**Spec:** `docs/superpowers/specs/2026-10-02-jev-hook-wiring-design.md` (approved 2026-10-02). It builds on `docs/superpowers/specs/2026-09-30-jev-typed-decision-routing-design.md` §5–7 and §12 and on ADR 0049. Executors read both specs.

## Global Constraints

- Python 3.12. mypy strict over `src`, `tests` and `scripts` (`warn_unreachable = true`). ruff line length 100, rule sets B, E, F, I, RUF, SIM, UP (RUF022 keeps `__all__` sorted).
- Standard library only. No new dependencies.
- Layer rules (`scripts/check_architecture.py`): `packages/application` may import only `packages/domain` and `packages/storage`, never `model_gateway` or `telemetry`; `packages/storage` may import only `packages/domain` and `packages/policy`; `packages/telemetry` imports nothing internal; apps may not import other apps; connectors may not import other connectors; no relative import beyond one level.
- `tests/architecture/test_typed_decision_boundaries.py` requires the importer list of `mnemo_memory.connectors.typesafe` under `src/mnemo_memory` to equal exactly `[src/mnemo_memory/apps/cli/typed_decision_composition.py]`. It must stay that way: `typed_decision_hook.py` and `main.py` never import the connector.
- No live network in any test. Never read or print `TYPESAFE_API_KEY`; tests use `test-key-not-real-0000` through `environ=` or `monkeypatch.setenv`.
- Real data never leaves the machine: the data route stays `synthetic_only`; `automatic-memory-hook` always builds a `TypedDecisionSource.RUNTIME` guard and never passes `replay_overrides`.
- Telemetry is content-free: closed values, bounded integers and booleans only. No prompt text, note text or skill name.
- Every failure falls back to today's behaviour for that prompt; the guard never raises; the typed step is wrapped as a whole.
- Focused checks: `uv run pytest <files> -q`, `uv run ruff format`, `uv run ruff check`, `uv run mypy`, `npm run -s architecture:check`. Full check: `npm run check`.
- Commits: `git commit -- <paths>`; every message ends with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- No live Jev run is part of this plan. A live replay needs the maintainer's explicit go-ahead each time.

## Review Focus

The five input classes most likely to bite a real user that the spec implies but does not test; each has a pinning test in the owning task.

1. **A settings file that still holds a `live` mode** (hand edit, older build): loading it fails, so the hook silently runs with default settings. Expected: `typed-decisions status` prints the plain lock message instead of a traceback. Test: Task 11, `test_status_reports_a_locked_settings_file_in_plain_words`.
2. **Two hook processes reserving budget at the same moment** (two terminals, two sessions): expected no lost update and no crash. Test: Task 2, `test_concurrent_reservations_never_lose_an_update`.
3. **A corrupt or truncated budget counter**: expected every request is denied (`budget_denied`) and `typed-decisions status` says the counter is unavailable rather than crashing. Tests: Task 2, `test_corrupt_counter_denies_and_is_left_untouched`; Task 11, `test_status_reports_an_unreadable_counter`.
4. **A long pasted prompt with private text in the middle**: expected Jev only ever sees the 512-character head/tail view. Test: Task 8, `test_long_prompt_reaches_the_adapter_only_as_the_bounded_view`.
5. **An agent copies the `item_id` of an older `lower_rank` omission that is not a note** (for example `source-structure`, which shares the reason now used for filler drops) into `get_context item_ids`: expected a clear `MNEMO_INVALID_INPUT` error, not a crash and not a silently partial answer. Test: Task 6, `test_mcp_port_fetches_item_ids_and_refuses_mixed_requests` (the `source-structure` case). (The already-running event loop case is pinned separately in Task 8.)

## Decisions this plan makes where the spec is silent

Executors and reviewers should treat these as settled for this plan; each is also flagged to the maintainer.

- **`typed_needs` order is `(long_term, structural)`**, matching `needs_from_memory_choice` and the §4.1 table.
- **Hard-rule detection lives in the planner** as `AutomaticContextShadowPlan.hard_rule` (a new defaulted field), so the hook and the planner can never disagree about when Jev is not asked.
- **Lock check order is master switch → semantic gate → data route.** With only `synthetic_only`, any other order would make the front-door lock unobservable.
- **"Push action" when the semantic gate is off** means "the rules retrieved a packet" (there is no plan then). Memory-need live needs the gate; via replay overrides without the gate it is recorded but not applied.
- **Filler eligibility also excludes conflict participants and unreadable notes**, because removing a conflict participant would make the packet invalid and keep is the safe side.
- **`item_ids` omission reasons:** knowledge note gone from this scope → `expired` (a deleted note and a foreign ID look the same through scoped lookups, by design); revision changed → `superseded`; section index beyond the revision → `unauthorized_scope`; approved event corrected → `superseded`, retracted → `expired`, not in this scope → `unauthorized_scope`. A malformed ID or a mix with other retrieval fields is a request error.
- **The skill axis uses skill names only** (no criteria text), as the brief specifies; the `when_to_use` text is the first tuning lever if the replay gate fails.
- **Skill agreement compares with the keyword top candidate** (or `none` when keyword matching found nothing).
- **The hint is joined to the lazy-pull hint with a newline**; each line stays within 40 tokens.
- **`TypedHookOverrides` gains an optional `observer`** so the replay can score decisions (skill picks, would-drop notes) without putting names into telemetry.
- **Telemetry:** `typed_memory_label` is `unsure` whenever the question was asked but no accepted answer came back (including blocked requests); the outcome field says why. `typed_action` and `typed_agrees_with_rules` are null unless Jev gave an accepted answer. `typed_tier` is null when the tier question was not asked.
- **Telemetry field types are defined twice on purpose:** `TypedTelemetryValues` (apps, Task 7) and `AutomaticRouteTypedDecisions` (telemetry, Task 10, validated). `packages/telemetry` may import nothing internal, and keeping the brief's task order means Task 7 cannot use the Task 10 type.
- **No packet schema change (maintainer decision, spec §5 as amended).** Each dropped filler note leaves one standard `OmissionNotice` with the existing reason `lower_rank`: `item_id` is the note's own ID and `detail` is `judged filler; fetch with get_context item_ids`. `context-packet-v1.json`, `OmissionReason` and `docs/context-packet-schema.md` stay unchanged; tests in Tasks 6 and 12 pin that the omissions validate under the unchanged schema.
- **Per-note "keep if it doesn't fit" is iterative.** The hook renders with all drops, cancels the drops whose own omission line is missing, and renders again with the rest; the drop set shrinks every round, so it ends within 16 renders. Telemetry `typed_notes_dropped` (live) counts only drops that stayed applied.
- **MCP surface:** `item_ids` is a `get_context` tool input on both profiles, never part of a packet; every call without it returns byte-identical packets.

## Tasks

1. Decision kinds, daily token setting and the three locks
2. Local daily typed-decision budget
3. Runtime composition — 0.8 s deadline, local budget, recorder, synthetic builder
4. Skill-pick axis and the current skill listing
5. Planner typed needs, hard-rule flag and typed route decisions
6. `get_context item_ids` — exact item lookup (no packet schema change)
7. Pure combine functions in `typed_decision_hook.py`
8. The async step — one `asyncio.run`, one `ask_each`, a runtime recorder
9. Hook integration in `main.py`
10. Telemetry `typed_v1`
11. `typed-decisions status` and `typed-decisions set`
12. Contract and security tests
13. Synthetic skill fixtures
14. Synthetic fresh-process replay
15. Paperwork
16. Final step: `npm run check`

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `src/mnemo_memory/packages/domain/typed_decisions.py` | closed kinds: add `tier_hint`, `skill` | 1 |
| `src/mnemo_memory/packages/application/settings.py` | daily token setting, three locks, `with_typed_decision_mode`, `active_typed_decision_locks` | 1 |
| `src/mnemo_memory/packages/storage/local_model_budget.py` (new) | `LocalDailyModelBudget`: file-locked per-UTC-day counter | 2 |
| `src/mnemo_memory/packages/storage/__init__.py` | export `LocalDailyModelBudget` | 2 |
| `src/mnemo_memory/apps/cli/typed_decision_composition.py` | runtime guard (0.8 s, local budget, recorder) and the replay's synthetic builder | 3 |
| `src/mnemo_memory/packages/model_gateway/decision_axes.py` | `skill_pick_axis`, `accepted_skill` | 4 |
| `src/mnemo_memory/packages/skills_registry/registry.py`, `__init__.py` | `CurrentSkillListing`, `current_skill_listing` | 4 |
| `src/mnemo_memory/packages/application/context_routing.py` | planner `typed_needs`, plan `hard_rule`, reason `typed_decision`, `typed_route_decision` | 5 |
| `src/mnemo_memory/packages/application/checkpoints.py` | `approved_event_context_item` | 6 |
| `src/mnemo_memory/packages/application/unified_context.py` | `GetUnifiedContext.item_ids` (a request field, not a packet field), exact item lookup | 6 |
| `src/mnemo_memory/packages/context_engine/engine.py` | pass `item_ids` requests straight through | 6 |
| `src/mnemo_memory/packages/application/mcp_durable.py`, `src/mnemo_memory/apps/mcp/server.py` | `get_context item_ids` (full and compact) | 6 |
| `src/mnemo_memory/apps/cli/typed_decision_hook.py` (new) | modes, overrides, pure combine and per-note `lower_rank` filler omissions (Task 7), async step (Task 8), telemetry conversion (Task 10) | 7, 8, 10 |
| `src/mnemo_memory/apps/cli/main.py` | rules render extraction, typed path, live application, telemetry, CLI | 9, 10, 11 |
| `scripts/typed_decision_test_support.py` (new) | test-only scripted Jev transport and hook fixture | 9 |
| `src/mnemo_memory/packages/telemetry/automatic_routes.py`, `__init__.py` | `AutomaticRouteTypedDecisions` (`typed_v1`) and the new key-set tier | 10 |
| `tests/fixtures/evals/typed-decision-skills-v1.json`, `typed-decision-skills-holdout-v1.json` (new) | synthetic skill sets | 13 |
| `scripts/typed_decision_replay.py`, `scripts/run_typed_decision_replay.py` (new) | replay library and CLI | 14 |
| `docs/adr/0049-…`, `docs/threat-model.md`, `docs/implementation-status.md`, `docs/user-guide.md` | paperwork | 15 |

---

### Task 1: Decision kinds, daily token setting and the three locks

**Files:**
- Modify: `src/mnemo_memory/packages/domain/typed_decisions.py:27-34` (`TypedDecisionKind`)
- Modify: `src/mnemo_memory/packages/application/settings.py:1-23` (imports), `:25-47` (`_FIELDS`), `:54-126` (`PersonalSettings` fields and `__post_init__`), `:156-194` (`to_dict`, `_MIGRATED_DEFAULTS`), end of file (new helpers)
- Test: `tests/unit/test_typed_decision_domain.py:34-42`, `tests/unit/test_personal_settings.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `TypedDecisionKind.TIER_HINT = "tier_hint"`, `TypedDecisionKind.SKILL = "skill"` (domain).
  - `PersonalSettings.typed_decision_daily_input_tokens: int` (default `10_000_000`, `[1, 1_000_000_000]`).
  - `DEFAULT_TYPED_DECISION_DAILY_INPUT_TOKENS: int = 10_000_000`.
  - `class TypedDecisionLock(StrEnum)`: `MASTER_SWITCH = "master_switch"`, `SEMANTIC_MEMORY_GATE = "semantic_memory_gate"`, `DATA_ROUTE = "data_route"`.
  - `TYPED_DECISION_LOCK_MESSAGES: dict[TypedDecisionLock, str]`.
  - `active_typed_decision_locks(settings: PersonalSettings) -> tuple[TypedDecisionLock, ...]`.
  - `with_typed_decision_mode(settings: PersonalSettings, kind: TypedDecisionKind, mode: TypedDecisionMode) -> PersonalSettings` (raises `PersonalSettingsError` with the lock message).
  - All five names are imported from `mnemo_memory.packages.application.settings` (not re-exported by the package `__init__`).

Lock check order is master switch, then semantic-memory gate, then data route. The order is load-bearing: the only route today is `synthetic_only`, so the data-route lock refuses every `live`; checking the semantic gate first is the only way the front-door lock can ever be observed and tested.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_typed_decision_domain.py`, replace the `TypedDecisionKind` set inside `test_closed_vocabularies_match_the_spec` with:

```python
    assert {kind.value for kind in TypedDecisionKind} == {
        "front_door",
        "relevance",
        "extraction_gate",
        "compaction",
        "dedupe",
        "semantic_kind",
        "verify",
        "tier_hint",
        "skill",
    }
```

In `tests/unit/test_personal_settings.py`:

1. Add `"typed_decision_daily_input_tokens",` to the expected key set in `test_settings_defaults_are_strict_bounded_and_secret_free` (after `"typed_decision_data_route",`).
2. Add `"typed_decision_daily_input_tokens",` to the tuple of deleted names in `test_legacy_settings_without_typed_decision_fields_load`.
3. Extend the import block:

```python
from mnemo_memory.packages.application.settings import (
    TYPED_DECISION_LOCK_MESSAGES,
    TypedDecisionLock,
    active_typed_decision_locks,
    with_typed_decision_mode,
)
```

4. Append these tests:

```python
_HOOK_KINDS = ("front_door", "relevance", "tier_hint", "skill")


def _typed(**overrides: object) -> dict[str, object]:
    return {
        **PersonalSettings().to_dict(),
        "experimental_typed_decisions_enabled": True,
        **overrides,
    }


def test_typed_decision_daily_input_tokens_default_bounds_and_migration() -> None:
    assert PersonalSettings().typed_decision_daily_input_tokens == 10_000_000
    legacy = PersonalSettings().to_dict()
    legacy.pop("typed_decision_daily_input_tokens")
    assert PersonalSettings.from_dict(legacy).typed_decision_daily_input_tokens == 10_000_000
    assert PersonalSettings(typed_decision_daily_input_tokens=1).typed_decision_daily_input_tokens == 1
    for value in (0, 1_000_000_001, True, "10"):
        with pytest.raises(PersonalSettingsError, match="daily input tokens"):
            PersonalSettings.from_dict(
                {**PersonalSettings().to_dict(), "typed_decision_daily_input_tokens": value}
            )


@pytest.mark.parametrize("kind", _HOOK_KINDS)
def test_live_is_refused_while_the_route_is_synthetic_only(kind: str) -> None:
    with pytest.raises(PersonalSettingsError) as raised:
        PersonalSettings.from_dict(
            _typed(experimental_semantic_memory_enabled=True, typed_decision_modes={kind: "live"})
        )
    assert str(raised.value) == TYPED_DECISION_LOCK_MESSAGES[TypedDecisionLock.DATA_ROUTE]


def test_front_door_live_is_refused_without_the_semantic_memory_gate() -> None:
    with pytest.raises(PersonalSettingsError) as raised:
        PersonalSettings.from_dict(_typed(typed_decision_modes={"front_door": "live"}))
    assert str(raised.value) == TYPED_DECISION_LOCK_MESSAGES[
        TypedDecisionLock.SEMANTIC_MEMORY_GATE
    ]


def test_any_mode_other_than_off_needs_the_master_switch() -> None:
    with pytest.raises(PersonalSettingsError) as raised:
        PersonalSettings.from_dict(
            {**PersonalSettings().to_dict(), "typed_decision_modes": {"skill": "shadow"}}
        )
    assert str(raised.value) == TYPED_DECISION_LOCK_MESSAGES[TypedDecisionLock.MASTER_SWITCH]


def test_shadow_is_allowed_on_synthetic_only_for_every_hook_kind() -> None:
    settings = PersonalSettings.from_dict(
        _typed(typed_decision_modes={kind: "shadow" for kind in _HOOK_KINDS})
    )
    for kind in _HOOK_KINDS:
        assert settings.typed_decision_mode(TypedDecisionKind(kind)) is TypedDecisionMode.SHADOW


def test_with_typed_decision_mode_applies_the_locks() -> None:
    base = PersonalSettings(experimental_typed_decisions_enabled=True)
    shadow = with_typed_decision_mode(base, TypedDecisionKind.SKILL, TypedDecisionMode.SHADOW)
    assert shadow.typed_decision_mode(TypedDecisionKind.SKILL) is TypedDecisionMode.SHADOW
    with pytest.raises(PersonalSettingsError, match="synthetic_only"):
        with_typed_decision_mode(shadow, TypedDecisionKind.RELEVANCE, TypedDecisionMode.LIVE)
    off = with_typed_decision_mode(shadow, TypedDecisionKind.SKILL, TypedDecisionMode.OFF)
    assert off.typed_decision_mode(TypedDecisionKind.SKILL) is TypedDecisionMode.OFF


def test_active_typed_decision_locks_name_each_closed_lock() -> None:
    assert active_typed_decision_locks(PersonalSettings()) == (
        TypedDecisionLock.MASTER_SWITCH,
        TypedDecisionLock.SEMANTIC_MEMORY_GATE,
        TypedDecisionLock.DATA_ROUTE,
    )
    opened = PersonalSettings(
        experimental_typed_decisions_enabled=True, experimental_semantic_memory_enabled=True
    )
    assert active_typed_decision_locks(opened) == (TypedDecisionLock.DATA_ROUTE,)
    assert all(message.strip() for message in TYPED_DECISION_LOCK_MESSAGES.values())


def test_a_stored_live_mode_is_refused_on_load(tmp_path: Path) -> None:
    store = PersonalSettingsStore(tmp_path / "profile")
    store.save(PersonalSettings(experimental_typed_decisions_enabled=True))
    path = tmp_path / "profile" / "settings.json"
    stored = json.loads(path.read_text("utf-8"))
    stored["typed_decision_modes"] = {"skill": "live"}
    path.write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(PersonalSettingsError, match="MNEMO_SETTINGS_INVALID") as raised:
        store.load()
    assert str(raised.value.__cause__) == TYPED_DECISION_LOCK_MESSAGES[
        TypedDecisionLock.DATA_ROUTE
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_personal_settings.py tests/unit/test_typed_decision_domain.py -q`
Expected: FAIL — collection error `ImportError: cannot import name 'TYPED_DECISION_LOCK_MESSAGES'`.

- [ ] **Step 3: Implement the kinds**

In `src/mnemo_memory/packages/domain/typed_decisions.py`, append two members to `TypedDecisionKind` (after `VERIFY = "verify"`):

```python
    TIER_HINT = "tier_hint"
    SKILL = "skill"
```

- [ ] **Step 4: Implement the setting, the locks and the helpers**

In `src/mnemo_memory/packages/application/settings.py`:

Change the imports at the top to:

```python
import json
import os
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, ClassVar, Self
```

Add `"typed_decision_daily_input_tokens",` to `_FIELDS` (after `"typed_decision_data_route",`).

Directly after `_FIELDS = {...}` add:

```python
DEFAULT_TYPED_DECISION_DAILY_INPUT_TOKENS = 10_000_000
_MAXIMUM_TYPED_DECISION_DAILY_INPUT_TOKENS = 1_000_000_000


class TypedDecisionLock(StrEnum):
    """Why a typed-decision mode is refused (spec 2026-10-02 §6); checked in this order."""

    MASTER_SWITCH = "master_switch"
    SEMANTIC_MEMORY_GATE = "semantic_memory_gate"
    DATA_ROUTE = "data_route"


TYPED_DECISION_LOCK_MESSAGES: dict[TypedDecisionLock, str] = {
    TypedDecisionLock.MASTER_SWITCH: (
        "typed decision modes other than off need experimental_typed_decisions_enabled"
    ),
    TypedDecisionLock.SEMANTIC_MEMORY_GATE: (
        "front_door live needs experimental_semantic_memory_enabled"
    ),
    TypedDecisionLock.DATA_ROUTE: (
        "live typed decisions are locked while the data route is synthetic_only; "
        "use off or shadow"
    ),
}
```

Add the field after `typed_decision_modes`:

```python
    typed_decision_modes: tuple[tuple[str, str], ...] = ()
    typed_decision_daily_input_tokens: int = DEFAULT_TYPED_DECISION_DAILY_INPUT_TOKENS
```

In `__post_init__`, replace the block

```python
        if not self.experimental_typed_decisions_enabled and any(
            mode != TypedDecisionMode.OFF.value for _, mode in self.typed_decision_modes
        ):
            raise PersonalSettingsError(
                "typed decision modes require experimental_typed_decisions_enabled"
            )
```

with

```python
        modes = dict(self.typed_decision_modes)
        if not self.experimental_typed_decisions_enabled and any(
            mode != TypedDecisionMode.OFF.value for mode in modes.values()
        ):
            raise PersonalSettingsError(
                TYPED_DECISION_LOCK_MESSAGES[TypedDecisionLock.MASTER_SWITCH]
            )
        if (
            modes.get(TypedDecisionKind.FRONT_DOOR.value) == TypedDecisionMode.LIVE.value
            and not self.experimental_semantic_memory_enabled
        ):
            raise PersonalSettingsError(
                TYPED_DECISION_LOCK_MESSAGES[TypedDecisionLock.SEMANTIC_MEMORY_GATE]
            )
        if (
            TypedDecisionDataRoute(self.typed_decision_data_route)
            is TypedDecisionDataRoute.SYNTHETIC_ONLY
            and TypedDecisionMode.LIVE.value in modes.values()
        ):
            raise PersonalSettingsError(TYPED_DECISION_LOCK_MESSAGES[TypedDecisionLock.DATA_ROUTE])
        daily = self.typed_decision_daily_input_tokens
        if (
            isinstance(daily, bool)
            or not isinstance(daily, int)
            or not 1 <= daily <= _MAXIMUM_TYPED_DECISION_DAILY_INPUT_TOKENS
        ):
            raise PersonalSettingsError(
                "typed decision daily input tokens must be between 1 and 1000000000"
            )
```

In `to_dict`, add after `"typed_decision_data_route": ...`:

```python
            "typed_decision_daily_input_tokens": self.typed_decision_daily_input_tokens,
```

In `_MIGRATED_DEFAULTS`, add:

```python
        "typed_decision_daily_input_tokens": DEFAULT_TYPED_DECISION_DAILY_INPUT_TOKENS,
```

Append to the end of the file:

```python
def active_typed_decision_locks(settings: PersonalSettings) -> tuple[TypedDecisionLock, ...]:
    """Return the locks that would refuse some mode today, in check order."""

    locks: list[TypedDecisionLock] = []
    if not settings.experimental_typed_decisions_enabled:
        locks.append(TypedDecisionLock.MASTER_SWITCH)
    if not settings.experimental_semantic_memory_enabled:
        locks.append(TypedDecisionLock.SEMANTIC_MEMORY_GATE)
    if (
        TypedDecisionDataRoute(settings.typed_decision_data_route)
        is TypedDecisionDataRoute.SYNTHETIC_ONLY
    ):
        locks.append(TypedDecisionLock.DATA_ROUTE)
    return tuple(locks)


def with_typed_decision_mode(
    settings: PersonalSettings, kind: TypedDecisionKind, mode: TypedDecisionMode
) -> PersonalSettings:
    """Return ``settings`` with one mode changed; a lock refuses it with its plain message."""

    modes = dict(settings.typed_decision_modes)
    modes[TypedDecisionKind(kind).value] = TypedDecisionMode(mode).value
    return replace(settings, typed_decision_modes=tuple(sorted(modes.items())))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_personal_settings.py tests/unit/test_typed_decision_domain.py -q`
Expected: PASS.

- [ ] **Step 6: Lint, type-check and commit**

Run: `uv run ruff format src/mnemo_memory/packages tests/unit && uv run ruff check src tests && uv run mypy`
Expected: no errors.

```bash
git commit -m "feat(typed-decisions): hook decision kinds, daily token setting and live locks

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- \
  src/mnemo_memory/packages/domain/typed_decisions.py \
  src/mnemo_memory/packages/application/settings.py \
  tests/unit/test_typed_decision_domain.py tests/unit/test_personal_settings.py
```

---

### Task 2: Local daily typed-decision budget

**Files:**
- Create: `src/mnemo_memory/packages/storage/local_model_budget.py`
- Modify: `src/mnemo_memory/packages/storage/__init__.py` (import after the `from .contracts import (...)` block; `__all__` entry between `"KnowledgeDocumentSyncStoreResult"` and `"ManifestNodeNotFound"`)
- Test: `tests/unit/test_local_model_budget.py` (new)

**Interfaces:**
- Consumes: `ModelBudgetDenied`, `ModelBudgetReservation`, `ModelBudgetReservationPort`, `ModelTaskType`, `WorkspaceId` from `mnemo_memory.packages.domain`.
- Produces: `class LocalDailyModelBudget` (implements `ModelBudgetReservationPort`), exported from `mnemo_memory.packages.storage`:
  - `__init__(self, data_directory: Path, *, task_type: ModelTaskType, daily_input_tokens: int, clock: Callable[[], datetime] | None = None) -> None`
  - `path: Path` — `<data_directory>/<task_type.value>-budget.json` (so `typed_decision-budget.json`).
  - `daily_input_tokens: int` (property).
  - `reserve(self, workspace_id: WorkspaceId, task_type: ModelTaskType, reservation: ModelBudgetReservation) -> None` — raises `ModelBudgetDenied` on exhaustion and on any doubt.
  - `reserved_today(self) -> int | None` — `None` when the counter is unreadable.

The locking approach copies `LocalAutomaticRouteTelemetryStore._lock` (`packages/telemetry/automatic_routes.py:871-888`): an `O_NOFOLLOW` lock file plus `fcntl.flock`. Storage may not import telemetry or application, so the pattern is repeated, not imported.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_local_model_budget.py`:

```python
"""The local typed-decision budget is a file-locked per-UTC-day counter that fails closed."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from mnemo_memory.packages.domain import (
    ModelBudgetDenied,
    ModelBudgetReservation,
    ModelTaskType,
    WorkspaceId,
)
from mnemo_memory.packages.storage import LocalDailyModelBudget

WORKSPACE = WorkspaceId(UUID(int=0))
ONE_THOUSAND = ModelBudgetReservation(input_tokens=1_000, output_tokens=1, cost_microusd=0)
TYPED = ModelTaskType.TYPED_DECISION


def _budget(
    tmp_path: Path, limit: int = 10_000_000, at: datetime | None = None
) -> LocalDailyModelBudget:
    moment = at or datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    return LocalDailyModelBudget(
        tmp_path, task_type=TYPED, daily_input_tokens=limit, clock=lambda: moment
    )


def test_reservations_count_up_to_the_limit_then_deny(tmp_path: Path) -> None:
    budget = _budget(tmp_path, limit=2_500)
    assert budget.reserved_today() == 0
    budget.reserve(WORKSPACE, TYPED, ONE_THOUSAND)
    budget.reserve(WORKSPACE, TYPED, ONE_THOUSAND)
    with pytest.raises(ModelBudgetDenied):
        budget.reserve(WORKSPACE, TYPED, ONE_THOUSAND)
    assert budget.reserved_today() == 2_000
    assert budget.path.name == "typed_decision-budget.json"
    assert budget.path.stat().st_mode & 0o777 == 0o600
    assert json.loads(budget.path.read_text("utf-8")) == {
        "day": "2026-10-02",
        "input_tokens": 2_000,
        "task_type": "typed_decision",
        "version": 1,
    }


def test_the_counter_resets_at_each_utc_day(tmp_path: Path) -> None:
    late = _budget(tmp_path, limit=1_000, at=datetime(2026, 10, 2, 23, 59, 59, tzinfo=UTC))
    late.reserve(WORKSPACE, TYPED, ONE_THOUSAND)
    with pytest.raises(ModelBudgetDenied):
        late.reserve(WORKSPACE, TYPED, ONE_THOUSAND)
    early = _budget(tmp_path, limit=1_000, at=datetime(2026, 10, 3, 0, 0, 1, tzinfo=UTC))
    assert early.reserved_today() == 0
    early.reserve(WORKSPACE, TYPED, ONE_THOUSAND)
    assert early.reserved_today() == 1_000


def test_a_local_timezone_clock_still_counts_by_utc_day(tmp_path: Path) -> None:
    plus_fourteen = timezone(timedelta(hours=14))
    # 2026-10-03 09:00 at +14:00 is still 2026-10-02 19:00 UTC.
    budget = _budget(tmp_path, at=datetime(2026, 10, 3, 9, 0, tzinfo=plus_fourteen))
    budget.reserve(WORKSPACE, TYPED, ONE_THOUSAND)
    assert json.loads(budget.path.read_text("utf-8"))["day"] == "2026-10-02"


def test_corrupt_counter_denies_and_is_left_untouched(tmp_path: Path) -> None:
    budget = _budget(tmp_path)
    budget.path.write_text('{"version": 1, "day": "2026-10-02"', encoding="utf-8")
    with pytest.raises(ModelBudgetDenied):
        budget.reserve(WORKSPACE, TYPED, ONE_THOUSAND)
    assert budget.reserved_today() is None
    assert budget.path.read_text("utf-8") == '{"version": 1, "day": "2026-10-02"'


@pytest.mark.parametrize(
    "stored",
    [
        {"version": 1, "task_type": "frontier_takeover", "day": "2026-10-02", "input_tokens": 5},
        {"version": True, "task_type": "typed_decision", "day": "2026-10-02", "input_tokens": 5},
        {"version": 1, "task_type": "typed_decision", "day": "2026-10-02", "input_tokens": -1},
        {"version": 1, "task_type": "typed_decision", "day": "2026-10-02"},
    ],
)
def test_wrong_shape_denies(tmp_path: Path, stored: dict[str, object]) -> None:
    budget = _budget(tmp_path)
    budget.path.write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(ModelBudgetDenied):
        budget.reserve(WORKSPACE, TYPED, ONE_THOUSAND)


def test_a_symlinked_counter_denies(tmp_path: Path) -> None:
    budget = _budget(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    budget.path.symlink_to(outside)
    with pytest.raises(ModelBudgetDenied):
        budget.reserve(WORKSPACE, TYPED, ONE_THOUSAND)
    assert budget.reserved_today() is None


def test_wrong_task_type_naive_clock_and_bad_arguments_deny(tmp_path: Path) -> None:
    budget = _budget(tmp_path)
    with pytest.raises(ModelBudgetDenied):
        budget.reserve(WORKSPACE, ModelTaskType.FRONTIER_TAKEOVER, ONE_THOUSAND)
    naive = LocalDailyModelBudget(
        tmp_path, task_type=TYPED, daily_input_tokens=10, clock=lambda: datetime(2026, 10, 2)
    )
    with pytest.raises(ModelBudgetDenied):
        naive.reserve(WORKSPACE, TYPED, ONE_THOUSAND)
    with pytest.raises(ValueError, match="daily input token limit"):
        LocalDailyModelBudget(tmp_path, task_type=TYPED, daily_input_tokens=0)
    with pytest.raises(TypeError, match="task type"):
        LocalDailyModelBudget(tmp_path, task_type="typed_decision", daily_input_tokens=1)  # type: ignore[arg-type]


def test_concurrent_reservations_never_lose_an_update(tmp_path: Path) -> None:
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            budget = _budget(tmp_path)
            for _ in range(25):
                budget.reserve(WORKSPACE, TYPED, ONE_THOUSAND)
        except BaseException as error:  # pragma: no cover - surfaced below
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert _budget(tmp_path).reserved_today() == 8 * 25 * 1_000
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_local_model_budget.py -q`
Expected: FAIL — `ImportError: cannot import name 'LocalDailyModelBudget' from 'mnemo_memory.packages.storage'`.

- [ ] **Step 3: Implement the counter**

Create `src/mnemo_memory/packages/storage/local_model_budget.py`:

```python
"""File-locked daily reservations for one optional model task in the personal profile.

The counter is one small JSON file under an exclusive ``flock``. It resets at each UTC day. Any
doubt — a corrupt, unreadable or unsafe file, a wrong task type, a naive clock, a lock failure —
denies the reservation, so the caller falls back to its rules. A corrupt file is never
overwritten; ``typed-decisions status`` reports it so the owner can remove it.
"""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from mnemo_memory.packages.domain import (
    ModelBudgetDenied,
    ModelBudgetReservation,
    ModelTaskType,
    WorkspaceId,
)

_FORMAT_VERSION = 1
_MAXIMUM_FILE_BYTES = 4_096
_MAXIMUM_DAILY_INPUT_TOKENS = 1_000_000_000
_KEYS = frozenset({"version", "task_type", "day", "input_tokens"})


class _CounterUnavailable(RuntimeError):
    """The counter cannot be trusted; every reservation is denied."""


class LocalDailyModelBudget:
    """Reserve worst-case input tokens for one task type against a per-UTC-day limit."""

    def __init__(
        self,
        data_directory: Path,
        *,
        task_type: ModelTaskType,
        daily_input_tokens: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(task_type, ModelTaskType):
            raise TypeError("model budget task type is invalid")
        if (
            isinstance(daily_input_tokens, bool)
            or not isinstance(daily_input_tokens, int)
            or not 1 <= daily_input_tokens <= _MAXIMUM_DAILY_INPUT_TOKENS
        ):
            raise ValueError("daily input token limit is invalid")
        self._directory = data_directory.expanduser().resolve()
        self.path = self._directory / f"{task_type.value}-budget.json"
        self._lock_path = self._directory / f".{task_type.value}-budget.lock"
        self._task_type = task_type
        self._limit = daily_input_tokens
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))

    @property
    def daily_input_tokens(self) -> int:
        return self._limit

    def reserve(
        self,
        workspace_id: WorkspaceId,
        task_type: ModelTaskType,
        reservation: ModelBudgetReservation,
    ) -> None:
        if (
            not isinstance(workspace_id, WorkspaceId)
            or task_type is not self._task_type
            or not isinstance(reservation, ModelBudgetReservation)
        ):
            raise ModelBudgetDenied("MNEMO_LOCAL_MODEL_BUDGET_DENIED")
        try:
            day = self._day()
            with self._lock():
                total = self._read(day) + reservation.input_tokens
                if total > self._limit:
                    raise ModelBudgetDenied("MNEMO_LOCAL_MODEL_BUDGET_EXHAUSTED")
                self._write(day, total)
        except ModelBudgetDenied:
            raise
        except Exception as error:
            raise ModelBudgetDenied("MNEMO_LOCAL_MODEL_BUDGET_UNAVAILABLE") from error

    def reserved_today(self) -> int | None:
        """Return today's reserved input tokens, or ``None`` when the counter is unreadable."""

        try:
            return self._read(self._day())
        except Exception:
            return None

    def _day(self) -> str:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise _CounterUnavailable("budget clock must be timezone-aware")
        return now.astimezone(UTC).date().isoformat()

    def _read(self, day: str) -> int:
        if self.path.is_symlink():
            raise _CounterUnavailable("budget counter is unsafe")
        if not self.path.exists():
            return 0
        if not self.path.is_file() or self.path.stat().st_size > _MAXIMUM_FILE_BYTES:
            raise _CounterUnavailable("budget counter is unsafe")
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or set(value) != _KEYS:
            raise _CounterUnavailable("budget counter is invalid")
        version, tokens = value["version"], value["input_tokens"]
        if (
            isinstance(version, bool)
            or version != _FORMAT_VERSION
            or value["task_type"] != self._task_type.value
            or not isinstance(value["day"], str)
            or isinstance(tokens, bool)
            or not isinstance(tokens, int)
            or tokens < 0
        ):
            raise _CounterUnavailable("budget counter is invalid")
        used: int = tokens
        return used if value["day"] == day else 0

    def _write(self, day: str, input_tokens: int) -> None:
        payload = json.dumps(
            {
                "version": _FORMAT_VERSION,
                "task_type": self._task_type.value,
                "day": day,
                "input_tokens": input_tokens,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(
                "w", encoding="utf-8", dir=self._directory, delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self._lock_path, flags, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            with suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
```

In `src/mnemo_memory/packages/storage/__init__.py` add, directly after the closing `)` of the `from .contracts import (` block:

```python
from .local_model_budget import LocalDailyModelBudget
```

and add `"LocalDailyModelBudget",` to `__all__` between `"KnowledgeDocumentSyncStoreResult",` and `"ManifestNodeNotFound",`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_local_model_budget.py -q`
Expected: PASS (9 tests, including the parametrized ones).

- [ ] **Step 5: Lint, type-check, architecture and commit**

Run: `uv run ruff format src/mnemo_memory/packages/storage tests/unit/test_local_model_budget.py && uv run ruff check src tests && uv run mypy && npm run -s architecture:check`
Expected: no errors; architecture check passes (storage imports only domain).

```bash
git commit -m "feat(storage): file-locked local daily model budget

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- \
  src/mnemo_memory/packages/storage/local_model_budget.py \
  src/mnemo_memory/packages/storage/__init__.py \
  tests/unit/test_local_model_budget.py
```

---
### Task 3: Runtime composition — 0.8 s deadline, local budget, recorder, synthetic builder

**Files:**
- Modify (replace whole file): `src/mnemo_memory/apps/cli/typed_decision_composition.py` (currently 83 lines; `build_runtime_typed_decision_classifier` at `:42-61`)
- Test (replace whole file): `tests/unit/test_typed_decision_composition.py`

**Interfaces:**
- Consumes: `PersonalSettings.typed_decision_daily_input_tokens` (Task 1); `LocalDailyModelBudget` (Task 2); `FILLER_CHECK_BUDGET_SECONDS` from `mnemo_memory.packages.model_gateway.decision_axes`; `GuardedTypedDecisionClassifier`, `TypedDecisionRecorder` from `mnemo_memory.packages.model_gateway.typed_decisions`.
- Produces:
  - `RUNTIME_DEADLINE_SECONDS: float` (== `FILLER_CHECK_BUDGET_SECONDS` == 0.8).
  - `build_runtime_typed_decision_classifier(settings: PersonalSettings, *, data_directory: Path, environ: Mapping[str, str] | None = None, jev_transport: JevTransport | None = None, recorder: TypedDecisionRecorder | None = None) -> GuardedTypedDecisionClassifier | None` — `None` when the master switch is off; source is always `TypedDecisionSource.RUNTIME`.
  - `build_synthetic_typed_decision_classifier(settings: PersonalSettings, *, data_directory: Path, environ: Mapping[str, str] | None = None, jev_transport: JevTransport | None = None, recorder: TypedDecisionRecorder | None = None) -> GuardedTypedDecisionClassifier` — source `SYNTHETIC_FIXTURE`; for the replay and tests only. Task 12 pins that nothing under `src/` calls it.
  - `DenyAllModelBudget` is removed (no remaining caller).

- [ ] **Step 1: Write the failing tests**

Replace `tests/unit/test_typed_decision_composition.py` with:

```python
import asyncio
import inspect
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from mnemo_memory.apps.cli.typed_decision_composition import (
    RUNTIME_DEADLINE_SECONDS,
    _adapter_from_environment,
    build_runtime_typed_decision_classifier,
    build_synthetic_typed_decision_classifier,
)
from mnemo_memory.connectors.typesafe import JevClassifier
from mnemo_memory.packages.application import PersonalSettings
from mnemo_memory.packages.domain import ModelTaskType, TypedDecisionUnavailableReason
from mnemo_memory.packages.model_gateway.decision_axes import (
    FILLER_CHECK_BUDGET_SECONDS,
    FRONT_DOOR_AXES,
    NOTE_SUBSTANCE,
)
from mnemo_memory.packages.model_gateway.typed_decisions import TypedDecisionRecord
from mnemo_memory.packages.storage import LocalDailyModelBudget

KEY = "test-key-not-real-0000"


class NeverTransport:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        self.calls += 1
        raise AssertionError("runtime text must never reach the network")


class FillerTransport:
    """Answer every question as confident filler; never opens a socket."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        self.calls += 1
        request = json.loads(body)
        answers = {
            name: {
                "type": "choice",
                "choice": "filler",
                "confidence": 0.9,
                "probabilities": {"task_information": 0.1, "filler": 0.9},
            }
            for name in request["questions"]
        }
        return json.dumps(
            {"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 12}}
        ).encode()


class ListRecorder:
    def __init__(self) -> None:
        self.records: list[TypedDecisionRecord] = []

    def record(self, record: TypedDecisionRecord) -> None:
        self.records.append(record)


def test_default_settings_compose_nothing(tmp_path: Path) -> None:
    assert (
        build_runtime_typed_decision_classifier(
            PersonalSettings(), data_directory=tmp_path, environ={}
        )
        is None
    )


def test_enabled_runtime_classifier_is_data_route_blocked_without_network(tmp_path: Path) -> None:
    transport = NeverTransport()
    settings = PersonalSettings(experimental_typed_decisions_enabled=True)
    environments: tuple[dict[str, str], ...] = ({"TYPESAFE_API_KEY": KEY}, {})
    for environ in environments:
        guard = build_runtime_typed_decision_classifier(
            settings, data_directory=tmp_path, environ=environ, jev_transport=transport
        )
        assert guard is not None
        outcome = asyncio.run(guard.ask(FRONT_DOOR_AXES, "What did we decide last week?"))
        assert outcome.unavailable_reason is TypedDecisionUnavailableReason.DATA_ROUTE_BLOCKED
    assert transport.calls == 0
    assert not (tmp_path / "typed_decision-budget.json").exists()


def test_runtime_source_cannot_be_chosen_by_callers() -> None:
    for builder in (
        build_runtime_typed_decision_classifier,
        build_synthetic_typed_decision_classifier,
    ):
        parameters = inspect.signature(builder).parameters
        assert "source" not in parameters and "data_route" not in parameters
    assert RUNTIME_DEADLINE_SECONDS == FILLER_CHECK_BUDGET_SECONDS == 0.8


def test_runtime_guard_records_each_blocked_request(tmp_path: Path) -> None:
    recorder = ListRecorder()
    guard = build_runtime_typed_decision_classifier(
        PersonalSettings(experimental_typed_decisions_enabled=True),
        data_directory=tmp_path,
        environ={"TYPESAFE_API_KEY": KEY},
        jev_transport=NeverTransport(),
        recorder=recorder,
    )
    assert guard is not None
    asyncio.run(
        guard.ask_each(
            [(FRONT_DOOR_AXES, "a prompt"), ((NOTE_SUBSTANCE,), "a stored note")],
            total_deadline_seconds=FILLER_CHECK_BUDGET_SECONDS,
        )
    )
    assert [record.outcome for record in recorder.records] == [
        "data_route_blocked",
        "data_route_blocked",
    ]
    assert {record.source for record in recorder.records} == {"runtime"}


def test_synthetic_builder_reaches_the_adapter_and_reserves_the_local_budget(
    tmp_path: Path,
) -> None:
    transport = FillerTransport()
    recorder = ListRecorder()
    guard = build_synthetic_typed_decision_classifier(
        PersonalSettings(),
        data_directory=tmp_path,
        environ={"TYPESAFE_API_KEY": KEY},
        jev_transport=transport,
        recorder=recorder,
    )
    outcome = asyncio.run(guard.ask((NOTE_SUBSTANCE,), "Background chatter about lunch."))
    assert outcome.available and outcome.results[0].label == "filler"
    assert transport.calls == 1
    assert recorder.records[0].source == "synthetic_fixture"
    budget = LocalDailyModelBudget(
        tmp_path, task_type=ModelTaskType.TYPED_DECISION, daily_input_tokens=10_000_000
    )
    assert budget.reserved_today() == 1_000


def test_daily_limit_from_settings_denies_once_spent(tmp_path: Path) -> None:
    guard = build_synthetic_typed_decision_classifier(
        PersonalSettings(typed_decision_daily_input_tokens=1_500),
        data_directory=tmp_path,
        environ={"TYPESAFE_API_KEY": KEY},
        jev_transport=FillerTransport(),
    )
    first = asyncio.run(guard.ask((NOTE_SUBSTANCE,), "note one"))
    second = asyncio.run(guard.ask((NOTE_SUBSTANCE,), "note two"))
    assert first.available
    assert second.unavailable_reason is TypedDecisionUnavailableReason.BUDGET_DENIED


@pytest.mark.parametrize("key", ["bad key", "bad\nkey", "é"])
def test_malformed_key_disables_the_adapter_quietly(key: str, tmp_path: Path) -> None:
    settings = PersonalSettings(experimental_typed_decisions_enabled=True)
    transport = NeverTransport()
    assert _adapter_from_environment(settings, {"TYPESAFE_API_KEY": key}, transport) is None
    guard = build_runtime_typed_decision_classifier(
        settings,
        data_directory=tmp_path,
        environ={"TYPESAFE_API_KEY": key},
        jev_transport=transport,
    )
    assert guard is not None
    outcome = asyncio.run(guard.ask(FRONT_DOOR_AXES, "What did we decide last week?"))
    assert outcome.unavailable_reason is TypedDecisionUnavailableReason.DATA_ROUTE_BLOCKED
    assert transport.calls == 0


def test_adapter_from_environment_needs_a_well_formed_key() -> None:
    settings = PersonalSettings(experimental_typed_decisions_enabled=True)
    transport = NeverTransport()
    assert _adapter_from_environment(settings, {}, transport) is None
    assert _adapter_from_environment(settings, {"TYPESAFE_API_KEY": "  "}, transport) is None
    adapter = _adapter_from_environment(settings, {"TYPESAFE_API_KEY": KEY}, transport)
    assert isinstance(adapter, JevClassifier)
    assert adapter.model_id == settings.typed_decision_model_id
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_typed_decision_composition.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_synthetic_typed_decision_classifier'`.

- [ ] **Step 3: Implement the composition**

Replace `src/mnemo_memory/apps/cli/typed_decision_composition.py` with:

```python
"""Runtime composition for typed decisions (spec 2026-10-02 §3, §6).

The prompt hook asks Jev only through a guard built here and bound to the runtime source, so the
``synthetic_only`` data route blocks every real prompt before credential, budget or network
work. The replay's synthetic-source builder lives here too, so this module stays the only
importer of the Jev connector.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

from mnemo_memory.connectors.typesafe import JevClassifier, JevTransport
from mnemo_memory.packages.application import PersonalSettings
from mnemo_memory.packages.domain import (
    ModelBudgetReservation,
    ModelTaskType,
    TypedDecisionDataRoute,
    TypedDecisionSource,
    WorkspaceId,
)
from mnemo_memory.packages.model_gateway.decision_axes import FILLER_CHECK_BUDGET_SECONDS
from mnemo_memory.packages.model_gateway.typed_decisions import (
    GuardedTypedDecisionClassifier,
    TypedDecisionRecorder,
)
from mnemo_memory.packages.storage import LocalDailyModelBudget

RUNTIME_DEADLINE_SECONDS = FILLER_CHECK_BUDGET_SECONDS
_RUNTIME_RESERVATION = ModelBudgetReservation(input_tokens=1_000, output_tokens=1, cost_microusd=0)
_LOCAL_WORKSPACE = WorkspaceId(UUID(int=0))


def build_runtime_typed_decision_classifier(
    settings: PersonalSettings,
    *,
    data_directory: Path,
    environ: Mapping[str, str] | None = None,
    jev_transport: JevTransport | None = None,
    recorder: TypedDecisionRecorder | None = None,
) -> GuardedTypedDecisionClassifier | None:
    """Return ``None`` when disabled; otherwise a guard bound to the runtime source."""

    if not settings.experimental_typed_decisions_enabled:
        return None
    return _guard(
        settings, TypedDecisionSource.RUNTIME, data_directory, environ, jev_transport, recorder
    )


def build_synthetic_typed_decision_classifier(
    settings: PersonalSettings,
    *,
    data_directory: Path,
    environ: Mapping[str, str] | None = None,
    jev_transport: JevTransport | None = None,
    recorder: TypedDecisionRecorder | None = None,
) -> GuardedTypedDecisionClassifier:
    """Return a synthetic-fixture guard for the replay (spec §8.2); the hook never calls it.

    Its source lets fixture text through the ``synthetic_only`` route, so callers must pass only
    text read from fixtures that declare synthetic provenance.
    """

    return _guard(
        settings,
        TypedDecisionSource.SYNTHETIC_FIXTURE,
        data_directory,
        environ,
        jev_transport,
        recorder,
    )


def _guard(
    settings: PersonalSettings,
    source: TypedDecisionSource,
    data_directory: Path,
    environ: Mapping[str, str] | None,
    transport: JevTransport | None,
    recorder: TypedDecisionRecorder | None,
) -> GuardedTypedDecisionClassifier:
    variables = os.environ if environ is None else environ
    return GuardedTypedDecisionClassifier(
        _adapter_from_environment(settings, variables, transport),
        data_route=TypedDecisionDataRoute(settings.typed_decision_data_route),
        source=source,
        budget=LocalDailyModelBudget(
            data_directory,
            task_type=ModelTaskType.TYPED_DECISION,
            daily_input_tokens=settings.typed_decision_daily_input_tokens,
        ),
        workspace_id=_LOCAL_WORKSPACE,
        reservation=_RUNTIME_RESERVATION,
        deadline_seconds=RUNTIME_DEADLINE_SECONDS,
        recorder=recorder,
    )


def _adapter_from_environment(
    settings: PersonalSettings,
    environ: Mapping[str, str],
    transport: JevTransport | None,
) -> JevClassifier | None:
    """Return a Jev adapter, or ``None`` when the key is missing or malformed.

    A malformed key disables the feature quietly (the guard reports ``no_credential``) instead
    of failing composition; the key is never echoed.
    """

    api_key = environ.get("TYPESAFE_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        return JevClassifier(
            api_key, model_id=settings.typed_decision_model_id, transport=transport
        )
    except ValueError:
        return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_typed_decision_composition.py tests/architecture/test_typed_decision_boundaries.py -q`
Expected: PASS; the importer list is still exactly `[src/mnemo_memory/apps/cli/typed_decision_composition.py]`.

- [ ] **Step 5: Lint, type-check and commit**

Run: `uv run ruff format src/mnemo_memory/apps/cli tests/unit/test_typed_decision_composition.py && uv run ruff check src tests && uv run mypy && npm run -s architecture:check`
Expected: no errors.

```bash
git commit -m "feat(typed-decisions): 0.8 s runtime guard with local budget, recorder and synthetic builder

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- \
  src/mnemo_memory/apps/cli/typed_decision_composition.py \
  tests/unit/test_typed_decision_composition.py
```

---

### Task 4: Skill-pick axis and the current skill listing

**Files:**
- Modify: `src/mnemo_memory/packages/model_gateway/decision_axes.py:12-25` (imports), end of file (new constants and functions)
- Modify: `src/mnemo_memory/packages/skills_registry/registry.py:36-81` (new dataclass after `SkillDiscoveryCandidate`, new method after `list_current_skills`), `src/mnemo_memory/packages/skills_registry/__init__.py`
- Test: `tests/unit/test_decision_axes.py`, `tests/unit/test_skill_registry.py`

**Interfaces:**
- Consumes: `ClassifierAxis`, `ClassifierResult`, `CascadeRouterError` from `cascade_router`; `accepted_choice` (same module).
- Produces (in `mnemo_memory.packages.model_gateway.decision_axes`):
  - `SKILL_PICK_NAME = "skill_pick"`, `SKILL_PICK_NONE = "none"`, `SKILL_PICK_MAXIMUM_SKILLS = 32`.
  - `skill_pick_axis(skill_names: Sequence[str]) -> ClassifierAxis | None` — labels are the names plus `"none"`; `None` for 0 or >32 names, a name that is not a registry name (`^[a-z][a-z0-9_-]{0,63}$`), duplicates, or a skill named `none`.
  - `accepted_skill(result: ClassifierResult | None) -> str | None` — the accepted label (a skill name or `"none"`) at confidence ≥ 0.6, else `None`.
- Produces (in `mnemo_memory.packages.skills_registry`):
  - `@dataclass(frozen=True, slots=True) class CurrentSkillListing: skills: tuple[ProjectSkill, ...]; more_than_limit: bool` with property `names -> tuple[str, ...]`.
  - `KnowledgeDocumentSkillRegistry.current_skill_listing(self, scope: MemoryScope, client: str, maximum_skills: int = 32) -> CurrentSkillListing`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_decision_axes.py` (extend its `decision_axes` import with `SKILL_PICK_NAME`, `SKILL_PICK_NONE`, `accepted_skill`, `skill_pick_axis`):

```python
def test_skill_pick_axis_lists_skills_then_none() -> None:
    axis = skill_pick_axis(("release-notes", "test-plan"))
    assert axis is not None
    assert axis.name == SKILL_PICK_NAME == "skill_pick"
    assert axis.allowed_labels == ("release-notes", "test-plan", SKILL_PICK_NONE)
    assert axis.criteria == ()


@pytest.mark.parametrize(
    "names",
    [
        (),
        tuple(f"skill-{index:02d}" for index in range(33)),
        ("release-notes", "release-notes"),
        ("none",),
        ("Release Notes",),
        ("x" * 65,),
    ],
)
def test_skill_pick_axis_refuses_unusable_skill_lists(names: tuple[str, ...]) -> None:
    assert skill_pick_axis(names) is None


def test_thirty_two_skills_is_the_largest_axis() -> None:
    axis = skill_pick_axis(tuple(f"skill-{index:02d}" for index in range(32)))
    assert axis is not None and len(axis.allowed_labels) == 33


def test_accepted_skill_needs_the_bar_and_the_skill_axis() -> None:
    assert accepted_skill(_result("skill_pick", "test-plan", 0.0, 0.6)) == "test-plan"
    assert accepted_skill(_result("skill_pick", "none", 0.0, 0.9)) == "none"
    assert accepted_skill(_result("skill_pick", "test-plan", 0.0, 0.59)) is None
    assert accepted_skill(_result("memory_need", "nothing", 0.0, 0.9)) is None
    assert accepted_skill(None) is None
```

Append to `tests/unit/test_skill_registry.py` (extend the `skills_registry` import with `CurrentSkillListing`):

```python
def _skill_revision(name: str, clients: str = "codex, claude-code") -> KnowledgeDocumentRevision:
    return _revision(
        _scope(),
        f"skills/{name}.md",
        f"---\nmnemo_kind: skill\nmnemo_name: {name}\nmnemo_version: 1.0.0\n"
        f"mnemo_tags: replay\nmnemo_clients: {clients}\nmnemo_trust: checked_in\n---\n"
        f"# {name}\nSynthetic skill body.",
    )


def test_current_skill_listing_is_sorted_compatible_and_bounded() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    repository.apply_sync(
        _scope(),
        (
            _skill_revision("test-plan"),
            _skill_revision("release-notes"),
            _skill_revision("claude-only", clients="claude-code"),
        ),
        (),
    )
    listing = KnowledgeDocumentSkillRegistry(repository).current_skill_listing(_scope(), "codex")
    assert isinstance(listing, CurrentSkillListing)
    assert listing.names == ("release-notes", "test-plan")
    assert listing.more_than_limit is False


def test_current_skill_listing_reports_more_than_thirty_two() -> None:
    repository = ReferenceKnowledgeDocumentRepository()
    repository.apply_sync(
        _scope(), tuple(_skill_revision(f"skill-{index:02d}") for index in range(33)), ()
    )
    listing = KnowledgeDocumentSkillRegistry(repository).current_skill_listing(_scope(), "codex")
    assert len(listing.names) == 32
    assert listing.names[0] == "skill-00" and listing.names[-1] == "skill-31"
    assert listing.more_than_limit is True
    with pytest.raises(ValueError, match="limit"):
        KnowledgeDocumentSkillRegistry(repository).current_skill_listing(_scope(), "codex", 33)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_decision_axes.py tests/unit/test_skill_registry.py -q`
Expected: FAIL — `ImportError: cannot import name 'SKILL_PICK_NAME'`.

- [ ] **Step 3: Implement the axis**

In `src/mnemo_memory/packages/model_gateway/decision_axes.py`, change the imports to:

```python
from __future__ import annotations

import re
from collections.abc import Sequence
from enum import StrEnum

from mnemo_memory.packages.domain import EpisodicMemoryKind

from .cascade_router import (
    YES_NO_LABELS,
    AxisKind,
    CascadeCommittee,
    CascadeRouterError,
    ClassifierAxis,
    ClassifierResult,
)
from .rule_axes import RISK_AXIS
```

Append to the end of the file:

```python
SKILL_PICK_NAME = "skill_pick"
SKILL_PICK_NONE = "none"
SKILL_PICK_MAXIMUM_SKILLS = 32
_SKILL_LABEL = re.compile(r"[a-z][a-z0-9_-]{0,63}")


def skill_pick_axis(skill_names: Sequence[str]) -> ClassifierAxis | None:
    """Build the skill-pick choice: the current skill names plus ``none`` (spec 2026-10-02 §4.4).

    Returns ``None`` — keyword matching stays in charge — for no skills, more than 32, a name
    that is not a registry name, a duplicate, or a skill literally named ``none``.
    """

    names = tuple(skill_names)
    if not 1 <= len(names) <= SKILL_PICK_MAXIMUM_SKILLS:
        return None
    if any(not isinstance(name, str) or _SKILL_LABEL.fullmatch(name) is None for name in names):
        return None
    try:
        return ClassifierAxis(
            SKILL_PICK_NAME,
            "Which listed project skill, if any, fits this request",
            (*names, SKILL_PICK_NONE),
            0.0,
        )
    except CascadeRouterError:
        return None


def accepted_skill(result: ClassifierResult | None) -> str | None:
    """Return the accepted skill label (a skill name or ``none``), or ``None`` when unsure."""

    if result is None or result.axis_name != SKILL_PICK_NAME:
        return None
    return accepted_choice(result)
```

- [ ] **Step 4: Implement the registry listing**

In `src/mnemo_memory/packages/skills_registry/registry.py` (`ProjectSkill` is already imported), add after the `SkillDiscoveryCandidate` class:

```python
@dataclass(frozen=True, slots=True)
class CurrentSkillListing:
    """Current compatible skills (sorted, at most the limit) and whether more exist."""

    skills: tuple[ProjectSkill, ...]
    more_than_limit: bool

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(skill.name for skill in self.skills)
```

After `list_current_skills` add the method:

```python
    def current_skill_listing(
        self, scope: MemoryScope, client: str, maximum_skills: int = 32
    ) -> CurrentSkillListing:
        """List current compatible skills once, and say whether the limit cut some off."""

        project_scope = _require_project_scope(scope)
        compatible_client = _require_supported_client(client)
        _require_limit(maximum_skills)
        skills = _unique_skills(
            tuple(
                skill
                for skill in self._iter_current_skills(project_scope)
                if compatible_client in skill.compatible_clients
            )
        )
        return CurrentSkillListing(skills[:maximum_skills], len(skills) > maximum_skills)
```

In `src/mnemo_memory/packages/skills_registry/__init__.py`:

```python
"""Versioned, deterministic procedural-memory selection."""

from .procedures import KnowledgeDocumentProcedureRegistry
from .registry import CurrentSkillListing, KnowledgeDocumentSkillRegistry, SkillDiscoveryCandidate

__all__ = [
    "CurrentSkillListing",
    "KnowledgeDocumentProcedureRegistry",
    "KnowledgeDocumentSkillRegistry",
    "SkillDiscoveryCandidate",
]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_decision_axes.py tests/unit/test_skill_registry.py tests/architecture -q`
Expected: PASS (`decision_axes.py` imports no network module).

- [ ] **Step 6: Lint, type-check and commit**

Run: `uv run ruff format src/mnemo_memory/packages tests/unit && uv run ruff check src tests && uv run mypy`
Expected: no errors.

```bash
git commit -m "feat(typed-decisions): skill-pick axis and current skill listing

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- \
  src/mnemo_memory/packages/model_gateway/decision_axes.py \
  src/mnemo_memory/packages/skills_registry/registry.py \
  src/mnemo_memory/packages/skills_registry/__init__.py \
  tests/unit/test_decision_axes.py tests/unit/test_skill_registry.py
```

---

### Task 5: Planner typed needs, hard-rule flag and typed route decisions

**Files:**
- Modify: `src/mnemo_memory/packages/application/context_routing.py`: `AutomaticContextRouteReason` (`:197-214`), `AutomaticContextShadowPlan` (`:257-332`, reason set at `:308-315`), `plan_automatic_context_needs` (`:406-515`), new `typed_route_decision` after `_decision` (`:690-693`)
- Test: `tests/unit/test_context_routing.py`, `tests/unit/test_context_route_telemetry.py`

**Interfaces:**
- Consumes: nothing new.
- Produces (all in `mnemo_memory.packages.application.context_routing`):
  - `AutomaticContextRouteReason.TYPED_DECISION = "typed_decision"`.
  - `AutomaticContextShadowPlan.hard_rule: bool = False` (new last field) — true when the route rules (no skill candidates) return `none`, `direct_lookup`, `local_diagnostics` or `skill_discovery`, a learned phrase matched, or the current-session cue matched.
  - Plan reason set gains `"typed_decision"` (only with `hard_rule is False`).
  - `plan_automatic_context_needs(prompt: str, *, learned_phrases: tuple[LearnedRoutePhrase, ...] = (), semantic_router: CompactMemoryRouter | None = None, typed_needs: tuple[AutomaticContextNeed, AutomaticContextNeed] | None = None) -> AutomaticContextShadowPlan` — `typed_needs` is **`(long_term, structural)`**, the same order as `needs_from_memory_choice` and the spec §4.1 table. It replaces the keyword cues unless `hard_rule`; then the reason is `typed_decision` and the semantic router is not consulted.
  - `typed_route_decision(route: AutomaticContextRoute) -> AutomaticContextRouteDecision` — for `PRIOR_MEMORY`, `KNOWLEDGE`, `STRUCTURE` only (else `ValueError`), reason `TYPED_DECISION`, today's per-route token ceiling.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_context_routing.py` (extend its import with `typed_route_decision`):

```python
YES, NO, UNKNOWN = AutomaticContextNeed.YES, AutomaticContextNeed.NO, AutomaticContextNeed.UNKNOWN


def test_typed_needs_replace_the_keyword_cues_when_no_hard_rule_fired() -> None:
    rules = plan_automatic_context_needs("finance reconciliation variance")
    typed = plan_automatic_context_needs("finance reconciliation variance", typed_needs=(YES, NO))
    nothing = plan_automatic_context_needs("finance reconciliation variance", typed_needs=(NO, NO))
    both = plan_automatic_context_needs("finance reconciliation variance", typed_needs=(YES, YES))

    assert rules.action is AutomaticContextShadowAction.LAZY_PULL and rules.hard_rule is False
    assert typed.reason == "typed_decision"
    assert (typed.long_term_need, typed.structural_need) == (YES, NO)
    assert typed.action is AutomaticContextShadowAction.PUSH_LONG_TERM
    assert nothing.action is AutomaticContextShadowAction.NONE
    assert both.action is AutomaticContextShadowAction.PUSH_BOTH
    assert typed.hard_rule is False


@pytest.mark.parametrize(
    ("prompt", "reason"),
    [
        ("hello", "deterministic"),
        ("Where is parse_row defined?", "deterministic"),
        ("What mnemo version is installed?", "deterministic"),
        ("This is the output; what is your conclusion?", "current_session"),
    ],
)
def test_hard_rules_ignore_typed_needs(prompt: str, reason: str) -> None:
    plan = plan_automatic_context_needs(prompt, typed_needs=(YES, YES))
    assert plan.hard_rule is True
    assert plan.reason == reason
    assert plan == plan_automatic_context_needs(prompt)


def test_a_learned_phrase_is_a_hard_rule_for_typed_needs() -> None:
    phrase = LearnedRoutePhrase("reconcile the ledger", CompactMemoryRoute.STRUCTURE)
    plan = plan_automatic_context_needs(
        "Please reconcile the ledger for this request.",
        learned_phrases=(phrase,),
        typed_needs=(NO, NO),
    )
    assert plan.hard_rule is True
    assert plan.reason == "learned_phrase"
    assert plan.action is AutomaticContextShadowAction.PUSH_STRUCTURE


class _RecordingSemanticRouter:
    def __init__(self) -> None:
        self.calls = 0

    def classify(self, prompt: str) -> CompactMemoryRouteDecision:
        self.calls += 1
        return CompactMemoryRouteDecision(CompactMemoryRoute.STRUCTURE, 0.9, 0.8)


def test_typed_needs_skip_the_semantic_router() -> None:
    router = _RecordingSemanticRouter()
    plan = plan_automatic_context_needs(
        "finance reconciliation variance", semantic_router=router, typed_needs=(YES, NO)
    )
    assert router.calls == 0
    assert plan.semantic_invoked is False and plan.reason == "typed_decision"


def test_typed_needs_must_be_two_closed_needs() -> None:
    with pytest.raises(TypeError, match="typed needs"):
        plan_automatic_context_needs("finance reconciliation variance", typed_needs=("yes", "no"))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="typed needs"):
        plan_automatic_context_needs("finance reconciliation variance", typed_needs=(YES,))  # type: ignore[arg-type]


def test_typed_route_decision_uses_the_route_ceiling_and_a_typed_reason() -> None:
    prior = typed_route_decision(AutomaticContextRoute.PRIOR_MEMORY)
    knowledge = typed_route_decision(AutomaticContextRoute.KNOWLEDGE)
    assert prior.maximum_attachment_tokens == knowledge.maximum_attachment_tokens == 1_300
    structure = typed_route_decision(AutomaticContextRoute.STRUCTURE)
    assert structure.maximum_attachment_tokens == 1_000
    assert structure.reason is AutomaticContextRouteReason.TYPED_DECISION
    for route in (
        AutomaticContextRoute.NONE,
        AutomaticContextRoute.DIRECT_LOOKUP,
        AutomaticContextRoute.SKILL_DISCOVERY,
    ):
        with pytest.raises(ValueError, match="typed retrieval route"):
            typed_route_decision(route)
```

Append to `tests/unit/test_context_route_telemetry.py`:

```python
def test_shadow_reason_typed_decision_is_a_valid_closed_reason() -> None:
    event = replace(
        _event(1),
        shadow_structural_need="no",
        shadow_long_term_need="yes",
        shadow_reason="typed_decision",
        shadow_long_term_tokens=1_300,
        shadow_shared_maximum_tokens=1_300,
        shadow_action="push_long_term",
        shadow_estimated_tokens=1_300,
    )
    assert AutomaticRouteEvent.from_dict(event.to_dict()).shadow_reason == "typed_decision"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_context_routing.py tests/unit/test_context_route_telemetry.py -q`
Expected: FAIL — `ImportError: cannot import name 'typed_route_decision'`.

- [ ] **Step 3: Implement the reason and the plan field**

In `AutomaticContextRouteReason`, add after `ROUTER_UNCERTAIN = "router_uncertain"`:

```python
    TYPED_DECISION = "typed_decision"
```

Directly after the `_MAXIMUM_ROUTE_TOKENS = {...}` dict add:

```python
_HARD_RULE_ROUTES = frozenset(
    {
        AutomaticContextRoute.NONE,
        AutomaticContextRoute.DIRECT_LOOKUP,
        AutomaticContextRoute.LOCAL_DIAGNOSTICS,
        AutomaticContextRoute.SKILL_DISCOVERY,
    }
)
_TYPED_RETRIEVAL_ROUTES = frozenset(
    {
        AutomaticContextRoute.PRIOR_MEMORY,
        AutomaticContextRoute.KNOWLEDGE,
        AutomaticContextRoute.STRUCTURE,
    }
)
```

In `AutomaticContextShadowPlan`, add the last field after `semantic_route`:

```python
    hard_rule: bool = False
```

and in its `__post_init__` replace the reason check

```python
        if self.reason not in {
            "current_session",
            "deterministic",
            "learned_phrase",
            "potion_proposal",
            "uncertain",
        }:
            raise ValueError("shadow route reason is invalid")
```

with

```python
        if self.reason not in {
            "current_session",
            "deterministic",
            "learned_phrase",
            "potion_proposal",
            "typed_decision",
            "uncertain",
        }:
            raise ValueError("shadow route reason is invalid")
        if not isinstance(self.hard_rule, bool):
            raise TypeError("shadow route hard-rule flag is invalid")
        if self.reason == "typed_decision" and self.hard_rule:
            raise ValueError("a typed decision cannot override a hard rule")
```

- [ ] **Step 4: Implement `typed_needs` in the planner**

Replace the signature and the first lines of `plan_automatic_context_needs` with:

```python
def plan_automatic_context_needs(
    prompt: str,
    *,
    learned_phrases: tuple[LearnedRoutePhrase, ...] = (),
    semantic_router: CompactMemoryRouter | None = None,
    typed_needs: tuple[AutomaticContextNeed, AutomaticContextNeed] | None = None,
) -> AutomaticContextShadowPlan:
    """Plan independent needs without itself changing the live attachment route.

    ``typed_needs`` is ``(long_term, structural)`` from an accepted typed answer. It replaces the
    keyword cues unless a hard rule fired: the route rules returned a no-retrieval route, a
    learned phrase matched, or the current-session cue matched (spec 2026-10-02 §4.1).
    """

    bounded = bounded_automatic_context_prompt(prompt)
    if any(not isinstance(item, LearnedRoutePhrase) for item in learned_phrases):
        raise TypeError("learned route phrases are invalid")
    if typed_needs is not None and (
        not isinstance(typed_needs, tuple)
        or len(typed_needs) != 2
        or any(not isinstance(need, AutomaticContextNeed) for need in typed_needs)
    ):
        raise TypeError("typed needs are invalid")
    live = choose_automatic_context_route(bounded)
    terms = frozenset(_ROUTER_TERM.findall(bounded.casefold()))

    if live.route in _HARD_RULE_ROUTES:
```

(The last line replaces `if live.route in {AutomaticContextRoute.NONE, ... SKILL_DISCOVERY,}:`; the body below it is unchanged.)

Replace the block from `semantic_route: CompactMemoryRoute | None = None` down to the `return AutomaticContextShadowPlan(...)` call (inclusive) with:

```python
    hard_rule = live.route in _HARD_RULE_ROUTES or learned or current_session
    typed_applies = False
    if typed_needs is not None and not hard_rule:
        long_term, structural = typed_needs
        typed_applies = True

    semantic_route: CompactMemoryRoute | None = None
    if (
        not typed_applies
        and live.reason is AutomaticContextRouteReason.ROUTER_UNCERTAIN
        and not current_session
        and semantic_router is not None
    ):
        proposal = semantic_router.classify(bounded)
        if not isinstance(proposal, CompactMemoryRouteDecision):
            raise TypeError("semantic route proposal is invalid")
        semantic_route = proposal.route
        if proposal.route is CompactMemoryRoute.STRUCTURE:
            structural = AutomaticContextNeed.YES
        elif proposal.route in {CompactMemoryRoute.PRIOR_MEMORY, CompactMemoryRoute.KNOWLEDGE}:
            long_term = AutomaticContextNeed.YES

    if typed_applies:
        reason = "typed_decision"
    elif semantic_route is not None:
        reason = "potion_proposal"
    elif learned:
        reason = "learned_phrase"
    elif current_session:
        reason = "current_session"
    elif AutomaticContextNeed.UNKNOWN in {structural, long_term}:
        reason = "uncertain"
    else:
        reason = "deterministic"
    structural_tokens, long_term_tokens = _shadow_token_allocation(structural, long_term)
    action = _shadow_action(structural, long_term)
    estimated_attachment_tokens = (
        _LAZY_PULL_ESTIMATED_TOKENS
        if action is AutomaticContextShadowAction.LAZY_PULL
        else structural_tokens + long_term_tokens
    )
    return AutomaticContextShadowPlan(
        structural,
        long_term,
        structural_tokens,
        long_term_tokens,
        1_300,
        reason,
        action,
        estimated_attachment_tokens,
        semantic_route is not None,
        semantic_route,
        hard_rule,
    )
```

After `_decision(...)` add:

```python
def typed_route_decision(route: AutomaticContextRoute) -> AutomaticContextRouteDecision:
    """Return the retrieval decision for a route chosen from a typed answer (spec §4.1)."""

    if route not in _TYPED_RETRIEVAL_ROUTES:
        raise ValueError("typed retrieval route is invalid")
    return _decision(route, AutomaticContextRouteReason.TYPED_DECISION)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_context_routing.py tests/unit/test_context_route_telemetry.py tests/evals/test_automatic_context_routing.py tests/unit/test_automatic_memory.py -q`
Expected: PASS (existing planner and hook tests are unchanged by the new defaulted field).

- [ ] **Step 6: Lint, type-check and commit**

Run: `uv run ruff format src/mnemo_memory/packages/application tests/unit && uv run ruff check src tests && uv run mypy`
Expected: no errors.

```bash
git commit -m "feat(routing): typed needs replace keyword cues outside hard rules

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- \
  src/mnemo_memory/packages/application/context_routing.py \
  tests/unit/test_context_routing.py tests/unit/test_context_route_telemetry.py
```

---
### Task 6: `get_context item_ids` — exact item lookup (no packet schema change)

**Files:**
- Modify: `src/mnemo_memory/packages/application/checkpoints.py`: imports (`:13-48`), new public method after `get_approved_event_record` (`:763-778`), `_approved_event_items` loop (`:1355-1393`), new module function after the class
- Modify: `src/mnemo_memory/packages/application/unified_context.py`: imports (`:3-10`, `:74-120`), `GetUnifiedContext` (`:449-524`), `UnifiedContextService.get_context` (`:554`), new methods, new module helpers after `_with_omission` (`:2702-2724`)
- Modify: `src/mnemo_memory/packages/context_engine/engine.py:269` (`UnifiedContextEngine.get_context`)
- Modify: `src/mnemo_memory/packages/application/mcp_durable.py` (`get_context`, insert before the first `if (lineage is not None or test_coverage is not None ...` at about `:545`)
- Modify: `src/mnemo_memory/apps/mcp/server.py`: full `get_context` (`:321-525`), compact `get_context` (`:1070-1082`)
- Test: `tests/unit/test_context_packet.py`, `tests/unit/test_context_item_lookup.py` (new), `tests/unit/test_mcp_server.py:115-160` and `:379-410`

**Not modified, by maintainer decision (spec §5 as amended 2026-10-02):** `packages/domain/context_packet.py`, `resources/schemas/context-packet-v1.json` and `docs/context-packet-schema.md`. Schema 1.x allows no new fields or enum values. Dropped filler notes (Task 9) therefore use the **existing** `OmissionReason.LOWER_RANK`, one standard `OmissionNotice(item_id=<the note's own ID>, reason=LOWER_RANK, detail="judged filler; fetch with get_context item_ids")` per dropped note. `item_ids` exists only as a `get_context` tool input, never in a packet.

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `CheckpointApplicationService.approved_event_context_item(scope: MemoryScope, event_id: EventId) -> tuple[ContextItem, ProvenanceNotice] | OmissionReason`.
  - `GetUnifiedContext.item_ids: tuple[str, ...] = ()` — 1–16 unique IDs shaped `knowledge:<uuid>:revision:<uuid>:section:<n>` or `approved-episodic:<uuid>`; cannot be combined with any other retrieval field (the `include_*` flags and `skill_client` are ignored).
  - `MAXIMUM_REQUESTED_ITEM_IDS = 16` in `unified_context`.
  - MCP `get_context` (full and compact) gains `item_ids: list[str] | None` (1–16) and forwards `"item_ids"` to the port.
  - Omission reasons for an item that cannot be served: knowledge document gone from this scope → `expired`; knowledge revision changed → `superseded`; knowledge section index beyond the revision → `unauthorized_scope`; approved event corrected → `superseded`; retracted → `expired`; not in this scope → `unauthorized_scope`; sensitivity not `normal` → `prohibited_sensitivity`; over budget → `token_budget`. All are existing v1 reasons.

- [ ] **Step 1: Pin the no-schema-change decision**

Append to `tests/unit/test_context_packet.py`:

```python
FILLER_DETAIL = "judged filler; fetch with get_context item_ids"


def test_per_note_filler_omissions_fit_the_unchanged_v1_schema() -> None:
    schema = json.loads(
        resources.files("mnemo_memory")
        .joinpath("resources/schemas/context-packet-v1.json")
        .read_text()
    )
    definition = schema["$defs"]["omission"]
    notices = (
        OmissionNotice(
            "approved-episodic:00000000-0000-4000-8000-000000000001",
            OmissionReason.LOWER_RANK,
            FILLER_DETAIL,
        ),
        OmissionNotice(
            "knowledge:00000000-0000-4000-8000-000000000002:revision:"
            "00000000-0000-4000-8000-000000000003:section:0",
            OmissionReason.LOWER_RANK,
            FILLER_DETAIL,
        ),
    )
    result = packet(omissions=notices)
    serialized = result.to_dict()
    assert ContextPacket.from_dict(serialized) == result
    omissions = serialized["omissions"]
    assert isinstance(omissions, list) and len(omissions) == 2
    assert definition["additionalProperties"] is False
    for omission in omissions:
        assert set(omission) == set(definition["required"]) == set(definition["properties"])
        assert omission["reason"] == "lower_rank"
        assert omission["reason"] in definition["properties"]["reason"]["enum"]
        assert omission["detail"] == FILLER_DETAIL
    assert definition["properties"]["reason"]["enum"] == [reason.value for reason in OmissionReason]
```

This test passes before and after this task. It pins the decision: the omissions Task 9 writes are valid under the unchanged v1 schema, and the schema and `OmissionReason` stay exactly as they are.

Run: `uv run pytest tests/unit/test_context_packet.py -q && npm run -s schema:check`
Expected: PASS, then `Context packet JSON Schema and representative fixture validation passed.`

- [ ] **Step 2: Write the failing item-lookup tests**

Create `tests/unit/test_context_item_lookup.py`:

```python
"""Exact item-ID fetches recheck scope, currentness and sensitivity (spec 2026-10-02 §5)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from mnemo_memory.packages.application import (
    CorrectApprovedEpisodicEvent,
    RecordApprovedEpisodicEvent,
    RetractApprovedEpisodicEvent,
)
from mnemo_memory.packages.application import unified_context
from mnemo_memory.packages.application.checkpoints import CheckpointApplicationService
from mnemo_memory.packages.application.mcp_durable import DurableMcpContextPort
from mnemo_memory.packages.application.unified_context import (
    GetUnifiedContext,
    UnifiedContextService,
)
from mnemo_memory.packages.context_engine import UnifiedContextEngine
from mnemo_memory.packages.domain import (
    ApprovedEventKind,
    ContextBudget,
    ContextItem,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EventId,
    EvidenceSourceType,
    KnowledgeDocumentRevision,
    KnowledgeDocumentRevisionId,
    MemoryScope,
    OmissionNotice,
    OmissionReason,
    OwnerId,
    ProjectId,
    ProvenanceNotice,
    ScopeLevel,
    Sensitivity,
    SessionId,
    SourceId,
    SourceTrustClass,
    TaskId,
    VerificationStatus,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.knowledge import KnowledgeDocumentParser, KnowledgeDocumentParseRequest
from mnemo_memory.packages.storage import (
    ActiveEpisodicMemoryPage,
    ReferenceApprovedEpisodicEventRepository,
    ReferenceCheckpointRepository,
    ReferenceKnowledgeDocumentRepository,
)

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def _project_scope(seed: int = 1) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(f"00000000-0000-4000-8000-{seed:012d}"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string(f"00000000-0000-4000-8001-{seed:012d}"),
        ProjectId.from_string(f"00000000-0000-4000-8002-{seed:012d}"),
    )


def _task_scope(seed: int = 1) -> MemoryScope:
    project = _project_scope(seed)
    return MemoryScope(
        project.owner_id,
        ScopeLevel.TASK,
        project.visibility,
        project.workspace_id,
        project.project_id,
        SessionId.from_string(f"00000000-0000-4000-8003-{seed:012d}"),
        TaskId.from_string(f"00000000-0000-4000-8004-{seed:012d}"),
    )


def _evidence(seed: str) -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.TOOL_RESULT,
        SourceTrustClass.VERIFIED_TOOL_RESULT,
        f"fixture://item-lookup/{seed}",
        "sha256:" + "a" * 64,
        EvidenceLocation(f"fixture://item-lookup/{seed}"),
        NOW,
        VerificationStatus.VERIFIED,
    )


def _revision(
    path: str, body: str, predecessor: KnowledgeDocumentRevision | None = None
) -> KnowledgeDocumentRevision:
    document = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(_project_scope(), path), body
    )
    return KnowledgeDocumentRevision(
        KnowledgeDocumentRevisionId.new(),
        document,
        1 if predecessor is None else predecessor.revision_number + 1,
        None if predecessor is None else predecessor.revision_id,
        NOW,
    )


def _knowledge_id(revision: KnowledgeDocumentRevision, section: int = 0) -> str:
    return (
        f"knowledge:{revision.document.document_id}:revision:{revision.revision_id}"
        f":section:{section}"
    )


def _services() -> tuple[
    CheckpointApplicationService, UnifiedContextService, ReferenceKnowledgeDocumentRepository
]:
    knowledge = ReferenceKnowledgeDocumentRepository()
    checkpoints = CheckpointApplicationService(
        ReferenceCheckpointRepository(),
        clock=lambda: NOW,
        approved_event_repository=ReferenceApprovedEpisodicEventRepository(),
    )
    return checkpoints, UnifiedContextService(checkpoints, None, knowledge=knowledge), knowledge


def _record(
    checkpoints: CheckpointApplicationService, summary: str, key: str, seed: int = 1
) -> str:
    event = checkpoints.record_approved_event(
        RecordApprovedEpisodicEvent(
            _task_scope(seed), ApprovedEventKind.DECISION, summary, key, (_evidence(key),)
        )
    ).event
    return f"approved-episodic:{event.event_id}"


def _reasons(omissions: tuple[OmissionNotice, ...]) -> dict[str, OmissionReason]:
    return {notice.item_id: notice.reason for notice in omissions}


def test_item_ids_return_exactly_the_requested_current_items() -> None:
    checkpoints, service, knowledge = _services()
    note = _revision("notes/export.md", "# Invoice export\nKeep ledger sequence numbers exactly.")
    knowledge.apply_sync(_project_scope(), (note,), ())
    event_id = _record(checkpoints, "Keep invoice export retries idempotent.", "lookup:a")
    _record(checkpoints, "An unrelated fact that was not requested.", "lookup:b")

    packet = service.get_context(
        GetUnifiedContext(_task_scope(), item_ids=(_knowledge_id(note), event_id))
    )

    assert {item.item_id for item in packet.items} == {_knowledge_id(note), event_id}
    assert packet.active_task_checkpoint is None and packet.omissions == ()
    assert "Keep ledger sequence numbers exactly." in packet.knowledge_items[0].content
    assert "retries idempotent" in packet.episodic_memories[0].content
    assert packet.declared_total_tokens == packet.computed_total_tokens


def test_changed_or_missing_knowledge_comes_back_as_an_omission() -> None:
    _, service, knowledge = _services()
    first = _revision("notes/export.md", "# Invoice export\nFirst wording.")
    knowledge.apply_sync(_project_scope(), (first,), ())
    second = _revision("notes/export.md", "# Invoice export\nSecond wording.", first)
    knowledge.apply_sync(_project_scope(), (second,), ())
    missing = f"knowledge:{uuid4()}:revision:{uuid4()}:section:0"

    packet = service.get_context(
        GetUnifiedContext(
            _task_scope(),
            item_ids=(_knowledge_id(first), missing, _knowledge_id(second, section=7)),
        )
    )

    assert packet.items == ()
    assert _reasons(packet.omissions) == {
        _knowledge_id(first): OmissionReason.SUPERSEDED,
        missing: OmissionReason.EXPIRED,
        _knowledge_id(second, section=7): OmissionReason.UNAUTHORIZED_SCOPE,
    }


def test_corrected_retracted_and_foreign_events_come_back_as_omissions() -> None:
    checkpoints, service, _ = _services()
    corrected = _record(checkpoints, "Retain the first grain.", "lookup:corrected")
    checkpoints.correct_approved_event(
        CorrectApprovedEpisodicEvent(
            _task_scope(),
            _event_id(corrected),
            "Retain the corrected grain.",
            "lookup:replacement",
            "The user corrected the grain.",
            "lookup:correct",
            (_evidence("correct"),),
        )
    )
    retracted = _record(checkpoints, "A withdrawn fact.", "lookup:retracted")
    checkpoints.retract_approved_event(
        RetractApprovedEpisodicEvent(
            _task_scope(),
            _event_id(retracted),
            "The user withdrew the fact.",
            "lookup:retract",
            (_evidence("retract"),),
        )
    )
    foreign = _record(checkpoints, "Another project's fact.", "lookup:foreign", seed=2)

    packet = service.get_context(
        GetUnifiedContext(_task_scope(), item_ids=(corrected, retracted, foreign))
    )

    assert packet.items == ()
    assert _reasons(packet.omissions) == {
        corrected: OmissionReason.SUPERSEDED,
        retracted: OmissionReason.EXPIRED,
        foreign: OmissionReason.UNAUTHORIZED_SCOPE,
    }


def _event_id(item_id: str) -> EventId:
    return EventId.from_string(item_id.removeprefix("approved-episodic:"))


def test_non_normal_sensitivity_is_withheld(monkeypatch: pytest.MonkeyPatch) -> None:
    _, service, knowledge = _services()
    note = _revision("notes/export.md", "# Invoice export\nKeep ledger sequence numbers exactly.")
    knowledge.apply_sync(_project_scope(), (note,), ())
    original = unified_context._knowledge_context_item

    def sensitive(*args: Any, **kwargs: Any) -> tuple[ContextItem, ProvenanceNotice]:
        item, notice = original(*args, **kwargs)
        return replace(item, sensitivity=Sensitivity.CONFIDENTIAL), notice

    monkeypatch.setattr(unified_context, "_knowledge_context_item", sensitive)
    packet = service.get_context(GetUnifiedContext(_task_scope(), item_ids=(_knowledge_id(note),)))
    assert packet.items == ()
    assert _reasons(packet.omissions) == {
        _knowledge_id(note): OmissionReason.PROHIBITED_SENSITIVITY
    }


def test_requested_items_respect_the_packet_budget() -> None:
    _, service, knowledge = _services()
    note = _revision("notes/export.md", "# Invoice export\nKeep ledger sequence numbers exactly.")
    knowledge.apply_sync(_project_scope(), (note,), ())
    packet = service.get_context(
        GetUnifiedContext(
            _task_scope(),
            budget=replace(ContextBudget(), knowledge=1),
            item_ids=(_knowledge_id(note),),
        )
    )
    assert packet.items == ()
    assert _reasons(packet.omissions) == {_knowledge_id(note): OmissionReason.TOKEN_BUDGET}


VALID = f"approved-episodic:{uuid4()}"


@pytest.mark.parametrize(
    "changes",
    [
        {"item_ids": (VALID, VALID)},
        {"item_ids": tuple(f"approved-episodic:{uuid4()}" for _ in range(17))},
        {"item_ids": (VALID.upper(),)},
        {"item_ids": (" " + VALID,)},
        {"item_ids": (f"checkpoint:{uuid4()}",)},
        {"item_ids": (VALID,), "query": "invoice export"},
        {"item_ids": (VALID,), "knowledge_query": "invoice export"},
    ],
)
def test_item_id_requests_are_validated(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="item_ids"):
        GetUnifiedContext(_task_scope(), **changes)


class _ForbiddenEpisodicMemories:
    def list_active_episodic_memories(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 50
    ) -> ActiveEpisodicMemoryPage:
        raise AssertionError("an item-ID fetch must return exactly the requested items")


def test_engine_returns_item_id_requests_without_additions() -> None:
    checkpoints, service, _ = _services()
    event_id = _record(checkpoints, "Keep invoice export retries idempotent.", "lookup:engine")
    engine = UnifiedContextEngine(service, _ForbiddenEpisodicMemories())
    packet = engine.get_context(GetUnifiedContext(_task_scope(), item_ids=(event_id,)))
    assert [item.item_id for item in packet.items] == [event_id]


def test_mcp_port_fetches_item_ids_and_refuses_mixed_requests() -> None:
    checkpoints, service, _ = _services()
    event_id = _record(checkpoints, "Keep invoice export retries idempotent.", "lookup:port")
    port = DurableMcpContextPort(checkpoints, context_service=service, default_scope=_task_scope())

    fetched = port.get_context({"item_ids": [event_id], "include_approved_events": True})
    episodic = fetched["episodic_memories"]
    assert isinstance(episodic, list) and [item["item_id"] for item in episodic] == [event_id]
    with pytest.raises(ValueError, match="MNEMO_INVALID_INPUT"):
        port.get_context({"item_ids": [event_id], "query": "invoice"})
    with pytest.raises(ValueError, match="MNEMO_INVALID_INPUT"):
        port.get_context({"item_ids": [event_id], "dbt_lineage": {"unique_id": "model.x.y"}})
    with pytest.raises(ValueError, match="MNEMO_INVALID_INPUT"):
        port.get_context({"item_ids": [event_id, "source-structure"]})
```

In `tests/unit/test_mcp_server.py`:
- in `test_server_lists_exact_tools_with_safety_annotations`, after `assert "include_approved_events" in tools[0].inputSchema["properties"]` add `assert "item_ids" in tools[0].inputSchema["properties"]`;
- in `test_compact_profile_reduces_schema_and_keeps_only_bound_project_operations`, change the compact `get_context` set to `{"query", "recap_days", "total_tokens", "item_ids"}`.

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/unit/test_context_item_lookup.py tests/unit/test_mcp_server.py -q`
Expected: FAIL — `TypeError: GetUnifiedContext.__init__() got an unexpected keyword argument 'item_ids'`, and the two MCP tool-input-schema assertions fail.

- [ ] **Step 4: Implement the approved-event lookup**

In `checkpoints.py`, add `ApprovedEventLifecycleStatus,` to the `mnemo_memory.packages.domain` import list (after `ApprovedEventKind,`).

After `get_approved_event_record` add:

```python
    def approved_event_context_item(
        self, scope: MemoryScope, event_id: EventId
    ) -> tuple[ContextItem, ProvenanceNotice] | OmissionReason:
        """Rebuild one approved fact for an exact item-ID fetch, or say why it cannot be served.

        Not found in this scope is ``unauthorized_scope``; a corrected fact is ``superseded``;
        a retracted one is ``expired`` (spec 2026-10-02 §5). Storage failures still raise.
        """

        self._validate_scope(scope)
        try:
            record = self._approved_repository().get_approved_event_record(scope, event_id)
        except ApprovedEpisodicEventNotFound:
            return OmissionReason.UNAUTHORIZED_SCOPE
        except ApprovedEpisodicEventRepositoryError as error:
            raise CheckpointApplicationStorageFailure(
                "approved episodic event storage is unavailable"
            ) from error
        if record.status is ApprovedEventLifecycleStatus.CORRECTED:
            return OmissionReason.SUPERSEDED
        if record.status is not ApprovedEventLifecycleStatus.ACTIVE or record.event is None:
            return OmissionReason.EXPIRED
        return _approved_event_context_item(record.event)
```

In `_approved_event_items`, replace the loop body (from `content = json.dumps(` through `remaining -= tokens`) with:

```python
        for event in page.items[:maximum_events]:
            item, notice = _approved_event_context_item(event)
            if item.token_estimate > remaining:
                omitted = True
                continue
            items.append(item)
            notices.append(notice)
            remaining -= item.token_estimate
```

and add this module-level function after the `CheckpointApplicationService` class:

```python
def _approved_event_context_item(
    event: ApprovedEpisodicEvent,
) -> tuple[ContextItem, ProvenanceNotice]:
    """Render one approved fact exactly as task context shows it (also used by item-ID fetches)."""

    content = json.dumps(
        {
            "event_kind": event.kind.value,
            "occurred_at": event.occurred_at.isoformat(),
            "summary": event.summary,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    item = ContextItem(
        item_id=f"approved-episodic:{event.event_id}",
        item_type=ContextItemType.EPISODIC_MEMORY,
        source_scope=event.scope,
        content=content,
        content_representation=ContentRepresentation.UNTRUSTED_EVIDENCE,
        token_estimate=(len(content) + 3) // 4,
        evidence_references=event.evidence_references,
        source_trust=SourceTrustClass.USER_AUTHORED,
        sensitivity=Sensitivity.NORMAL,
        validity=ValidityState.UNKNOWN,
        ranking=None,
        conflict_state=ConflictState.NONE,
        observed_at=event.occurred_at,
    )
    return item, ProvenanceNotice(
        provenance_id=f"provenance:{item.item_id}",
        item_id=item.item_id,
        source_reference=f"mnemo:approved-episodic/{event.event_id}",
        source_digest=hashlib.sha256(content.encode()).hexdigest(),
        evidence_references=event.evidence_references,
    )
```

- [ ] **Step 5: Implement the item-ID request and lookup**

In `unified_context.py`: add `import re` to the standard-library imports; add `EventId`, `KnowledgeDocumentId` and `KnowledgeDocumentRevisionId` to the `mnemo_memory.packages.domain` import list (sorted position).

Directly before `class GetUnifiedContext` add:

```python
MAXIMUM_REQUESTED_ITEM_IDS = 16
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_KNOWLEDGE_ITEM_ID = re.compile(
    rf"knowledge:({_UUID}):revision:({_UUID}):section:(0|[1-9][0-9]{{0,3}})"
)
_APPROVED_ITEM_ID = re.compile(rf"approved-episodic:({_UUID})")
_ITEM_LOOKUP_DETAILS: dict[OmissionReason, str] = {
    OmissionReason.EXPIRED: "requested item no longer exists",
    OmissionReason.SUPERSEDED: "requested item has changed since it was attached",
    OmissionReason.UNAUTHORIZED_SCOPE: "requested item is not available in this scope",
    OmissionReason.PROHIBITED_SENSITIVITY: "requested item is not normal sensitivity",
}
```

Add the last field of `GetUnifiedContext`:

```python
    memory_handle: str | None = None
    item_ids: tuple[str, ...] = ()
```

and append to the end of its `__post_init__`:

```python
        item_ids = tuple(self.item_ids)
        object.__setattr__(self, "item_ids", item_ids)
        if item_ids:
            if (
                len(item_ids) > MAXIMUM_REQUESTED_ITEM_IDS
                or len(set(item_ids)) != len(item_ids)
                or any(
                    not isinstance(value, str)
                    or (
                        _KNOWLEDGE_ITEM_ID.fullmatch(value) is None
                        and _APPROVED_ITEM_ID.fullmatch(value) is None
                    )
                    for value in item_ids
                )
            ):
                raise ValueError(
                    "item_ids must be 1-16 unique knowledge or approved-event item ids"
                )
            if _requests_other_retrieval(self):
                raise ValueError("item_ids cannot be combined with other retrieval fields")
```

Directly after the `GetUnifiedContext` class add:

```python
def _requests_other_retrieval(request: GetUnifiedContext) -> bool:
    return (
        request.checkpoint_id is not None
        or request.lineage is not None
        or request.source_query is not None
        or request.source_impact is not None
        or request.source_changes is not None
        or request.source_overview is not None
        or request.checkpoint_source_impact is not None
        or request.checkpoint_recap is not None
        or request.knowledge_query is not None
        or request.semantic_knowledge_query is not None
        or request.include_checkpoint_file_knowledge
        or bool(request.procedure_tags)
        or request.procedure_profile is not None
        or bool(request.skill_tags)
        or request.skill_agent_name is not None
        or request.dbt_test_coverage is not None
        or request.dbt_selector is not None
        or request.dbt_freshness is not None
        or request.dbt_changes is not None
        or request.query is not None
        or request.memory_handle is not None
    )
```

At the top of `UnifiedContextService.get_context` insert:

```python
        if request.item_ids:
            return self._requested_items(request)
```

Add these methods to `UnifiedContextService` (after `get_context`):

```python
    def _requested_items(self, request: GetUnifiedContext) -> ContextPacket:
        """Return exactly the requested items, rechecked for scope, currentness and sensitivity.

        An explicit fetch is never filtered by a typed decision (spec 2026-10-02 §5).
        """

        base = self._checkpoints.get_context(
            GetCheckpointContext(request.scope, None, request.budget)
        )
        packet = replace(
            base,
            declared_total_tokens=0,
            active_task_checkpoint=None,
            episodic_memories=(),
            knowledge_items=(),
            structural_items=(),
            skills_and_procedures=(),
            provenance=(),
            conflicts=(),
            omissions=(),
        )
        for rank, item_id in enumerate(request.item_ids, start=1):
            found = self._item_by_id(packet, request.scope, item_id, rank)
            if isinstance(found, OmissionReason):
                packet = _with_omission(packet, item_id, found, _ITEM_LOOKUP_DETAILS[found])
                continue
            item, notice = found
            if item.sensitivity is not Sensitivity.NORMAL:
                reason = OmissionReason.PROHIBITED_SENSITIVITY
                packet = _with_omission(packet, item_id, reason, _ITEM_LOOKUP_DETAILS[reason])
                continue
            packet = _with_requested_item(packet, item, notice)
        return packet

    def _item_by_id(
        self, packet: ContextPacket, scope: MemoryScope, item_id: str, rank: int
    ) -> tuple[ContextItem, ProvenanceNotice] | OmissionReason:
        approved = _APPROVED_ITEM_ID.fullmatch(item_id)
        if approved is not None:
            return self._checkpoints.approved_event_context_item(
                scope, EventId.from_string(approved.group(1))
            )
        knowledge = _KNOWLEDGE_ITEM_ID.fullmatch(item_id)
        if knowledge is None or self._knowledge is None:
            return OmissionReason.EXPIRED
        try:
            current = self._knowledge.get_current_revision(
                _project_scope(scope), KnowledgeDocumentId.from_string(knowledge.group(1))
            )
        except KnowledgeDocumentNotFound:
            return OmissionReason.EXPIRED
        if current.revision_id != KnowledgeDocumentRevisionId.from_string(knowledge.group(2)):
            return OmissionReason.SUPERSEDED
        section_index = int(knowledge.group(3))
        sections = current.document.sections
        if section_index >= len(sections):
            return OmissionReason.UNAUTHORIZED_SCOPE
        match = KnowledgeDocumentSectionMatch(current, section_index, sections[section_index], 1)
        return _knowledge_context_item(packet, scope, match, rank, 1.0, "exact-item-id")
```

After the module function `_with_omission` add:

```python
def _with_requested_item(
    packet: ContextPacket, item: ContextItem, notice: ProvenanceNotice
) -> ContextPacket:
    knowledge = item.item_type is ContextItemType.KNOWLEDGE
    section = packet.knowledge_items if knowledge else packet.episodic_memories
    section_limit = packet.budget.knowledge if knowledge else packet.budget.episodic_memories
    cost = item.token_estimate + notice.token_estimate
    if (
        item.token_estimate > section_limit - sum(entry.token_estimate for entry in section)
        or cost > packet.budget.total_limit - packet.declared_total_tokens
    ):
        return _with_omission(
            packet,
            item.item_id,
            OmissionReason.TOKEN_BUDGET,
            "requested item exceeds the remaining context budget",
        )
    return replace(
        packet,
        declared_total_tokens=packet.declared_total_tokens + cost,
        knowledge_items=(*packet.knowledge_items, item) if knowledge else packet.knowledge_items,
        episodic_memories=(
            packet.episodic_memories if knowledge else (*packet.episodic_memories, item)
        ),
        provenance=(*packet.provenance, notice),
    )
```

In `packages/context_engine/engine.py`, at the top of `UnifiedContextEngine.get_context` insert:

```python
        if request.item_ids:
            return self._assembler.get_context(request)
```

- [ ] **Step 6: Implement the MCP surface**

In `mcp_durable.py` `DurableMcpContextPort.get_context`, directly before the first `if (` whose first condition is `lineage is not None` (just after the `overview = (...)` assignment), insert:

```python
            item_ids = request.get("item_ids")
            if item_ids is not None:
                if not isinstance(item_ids, list) or any(
                    not isinstance(value, str) for value in item_ids
                ):
                    raise ValueError("item_ids must be an array of strings")
                if any(
                    value is not None
                    for value in (lineage, test_coverage, dbt_selector, dbt_freshness, dbt_changes)
                ):
                    raise ValueError("item_ids cannot be combined with other retrieval fields")
                if self._context_service is None:
                    raise CheckpointApplicationStorageFailure("context service is unavailable")
                return self._context_service.get_context(
                    GetUnifiedContext(
                        scope=scope,
                        checkpoint_id=checkpoint,
                        query=query,
                        memory_handle=memory_handle,
                        checkpoint_recap=checkpoint_recap,
                        source_query=source_query,
                        budget=budget,
                        source_impact=impact,
                        source_changes=changes,
                        source_overview=overview,
                        knowledge_query=knowledge_query,
                        semantic_knowledge_query=semantic_knowledge_query,
                        procedure_tags=tuple(cast(list[str], procedure_tags)),
                        skill_tags=tuple(cast(list[str], skill_tags)),
                        skill_client=skill_client,
                        skill_agent_name=skill_agent_name,
                        item_ids=tuple(cast(list[str], item_ids)),
                    )
                ).to_dict()
```

In `apps/mcp/server.py`, full `get_context`: add this parameter directly after the `include_approved_events` parameter:

```python
        item_ids: Annotated[
            list[str] | None,
            Field(
                default=None,
                min_length=1,
                max_length=16,
                description=(
                    "Optional exact item IDs from an earlier MNEMO_OMISSION line (1-16). Returns "
                    "exactly those items after the usual scope, currentness and sensitivity "
                    "checks; cannot be combined with other retrieval fields."
                ),
            ),
        ] = None,
```

and add `"item_ids": item_ids,` to the dict passed to `port.get_context`, after `"include_approved_events": include_approved_events,`.

Compact `get_context`: add the parameter after `total_tokens`:

```python
        item_ids: Annotated[
            list[str] | None, Field(default=None, min_length=1, max_length=16)
        ] = None,
```

and add `"item_ids": item_ids,` to its dict after `"total_tokens": total_tokens,`.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_context_item_lookup.py tests/unit/test_context_packet.py tests/unit/test_mcp_server.py tests/unit/test_knowledge_context.py tests/unit/test_approved_event_pinning.py tests/integration/test_mcp_durability.py -q`
Expected: PASS.

- [ ] **Step 8: Lint, type-check, schema check and commit**

Run: `uv run ruff format src tests/unit && uv run ruff check src tests && uv run mypy && npm run -s schema:check && npm run -s architecture:check`
Expected: no errors; the schema check passes against the unchanged `context-packet-v1.json`.

```bash
git commit -m "feat(context): get_context item_ids exact item lookup

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- \
  src/mnemo_memory/packages/application/checkpoints.py \
  src/mnemo_memory/packages/application/unified_context.py \
  src/mnemo_memory/packages/context_engine/engine.py \
  src/mnemo_memory/packages/application/mcp_durable.py \
  src/mnemo_memory/apps/mcp/server.py \
  tests/unit/test_context_packet.py tests/unit/test_context_item_lookup.py \
  tests/unit/test_mcp_server.py
```

---
### Task 7: Pure combine functions in `typed_decision_hook.py`

**Files:**
- Create: `src/mnemo_memory/apps/cli/typed_decision_hook.py`
- Test: `tests/unit/test_typed_decision_hook.py` (new)

**Interfaces:**
- Consumes:
  - Task 1: `TypedDecisionKind.TIER_HINT`, `TypedDecisionKind.SKILL`; `PersonalSettings.typed_decision_mode(kind) -> TypedDecisionMode`.
  - Task 4: `SKILL_PICK_NAME`, `SKILL_PICK_NONE`, `skill_pick_axis(names) -> ClassifierAxis | None`, `accepted_skill(result) -> str | None`.
  - Task 5: `AutomaticContextShadowPlan.hard_rule`, `plan_automatic_context_needs(..., typed_needs=(long_term, structural))`.
  - Existing, unchanged: `OmissionReason.LOWER_RANK`, `OmissionNotice(item_id, reason, detail)` (no packet schema change, spec §5).
  - Existing: `MEMORY_NEED`, `NOTE_SUBSTANCE`, `COMPLEXITY`, `TOOL_NEED`, `HINT_TEXT`, `accepted_choice`, `hint_eligible`, `note_text`, `should_drop_note` (`decision_axes`); `TypedDecisionOutcome`, `TierDecision`, `GuardedTypedDecisionClassifier`, `TypedDecisionRecorder` (`typed_decisions`); `contains_high_confidence_secret` (`policy.content_safety`).
- Produces (module `mnemo_memory.apps.cli.typed_decision_hook`):
  - `HOOK_KINDS: tuple[TypedDecisionKind, ...]` = front_door, relevance, tier_hint, skill.
  - `@dataclass(frozen=True, slots=True) class TypedHookModes(front_door: TypedDecisionMode = OFF, relevance = OFF, tier_hint = OFF, skill = OFF)` with property `any_on: bool`.
  - `typed_hook_modes(settings: PersonalSettings) -> TypedHookModes` (all off when the master switch is off).
  - `@dataclass(frozen=True, slots=True) class FillerCandidate(item_id: str, text: str)`.
  - `@dataclass(frozen=True, slots=True) class TypedStepInput(prompt: str, modes: TypedHookModes, hard_rule: bool, rules_plan: AutomaticContextShadowPlan, rules_route: AutomaticContextRoute, learned_phrases: tuple[LearnedRoutePhrase, ...] = (), skill_names: tuple[str, ...] = (), skills_over_limit: bool = False, keyword_skill_names: tuple[str, ...] = (), filler_candidates: tuple[FillerCandidate, ...] = ())`.
  - `@dataclass(frozen=True, slots=True) class TypedAnswers(front_door: TypedDecisionOutcome | None, fillers: tuple[TypedDecisionOutcome, ...] = (), tier: TierDecision | None = None)`.
  - `@dataclass(frozen=True, slots=True) class MemoryNeedOutcome(label: str | None, needs: tuple[AutomaticContextNeed, AutomaticContextNeed] | None, route: AutomaticContextRoute | None)`.
  - `class SkillComparison(StrEnum)`: `agreed`, `differs`, `unsure`, `skipped`, `not_asked`.
  - `@dataclass(frozen=True, slots=True) class TypedTelemetryValues` with fields, in order: `front_door_mode: str`, `relevance_mode: str`, `tier_hint_mode: str`, `skill_mode: str`, `front_door_outcome: str`, `step_ms: int`, `model_version: str | None`, `memory_label: str | None`, `memory_confidence_bucket: str | None`, `action: str | None`, `agrees_with_rules: bool | None`, `notes_checked: int`, `notes_dropped: int`, `notes_unanswered: int`, `tier: str | None`, `hint: str`, `skill: str`.
  - `@dataclass(frozen=True, slots=True) class TypedPromptDecisions(typed_needs: tuple[AutomaticContextNeed, AutomaticContextNeed] | None, memory_route: AutomaticContextRoute | None, drop_item_ids: tuple[str, ...], skill: str | None, show_hint: bool, telemetry: TypedTelemetryValues)` — the live fields are set **only** for decisions whose mode is `live`; shadow answers live only in `telemetry`.
  - `GuardFactory = Callable[[TypedDecisionRecorder], GuardedTypedDecisionClassifier | None]`; `DecisionObserver = Callable[[TypedStepInput, TypedPromptDecisions], None]`.
  - `@dataclass(frozen=True, slots=True) class TypedHookOverrides(guard_factory: GuardFactory, modes: TypedHookModes, observer: DecisionObserver | None = None)`.
  - Functions: `confidence_bucket(confidence: float | None) -> str | None`; `memory_need_outcome(result: ClassifierResult | None, rules_route: AutomaticContextRoute) -> MemoryNeedOutcome`; `skill_axis_for(step: TypedStepInput) -> ClassifierAxis | None`; `front_door_axes(step: TypedStepInput) -> tuple[ClassifierAxis, ...]`; `filler_candidates(packet: ContextPacket, *, pinned_item_ids: frozenset[str]) -> tuple[FillerCandidate, ...]`; `notes_to_drop(candidates: Sequence[FillerCandidate], outcomes: Sequence[TypedDecisionOutcome]) -> tuple[str, ...]`; `filler_omission(item_id: str) -> OmissionNotice` (reason `LOWER_RANK`, detail `FILLER_OMISSION_DETAIL`); `without_filler_notes(packet: ContextPacket, item_ids: Sequence[str]) -> tuple[ContextPacket, tuple[OmissionNotice, ...]]` (one omission per removed note, packet order); `omission_line(notice: OmissionNotice) -> str`; `skill_comparison(mode: TypedDecisionMode, *, axis_built: bool, accepted: str | None, keyword_top: str) -> SkillComparison`; `effective_skill_names(keyword_names: Sequence[str], accepted: str | None) -> tuple[str, ...]`; `with_task_size_hint(rendered: str | None) -> str`; `combine_typed_decisions(step: TypedStepInput, answers: TypedAnswers, *, step_ms: int, model_version: str | None) -> TypedPromptDecisions`; `typed_step_error_decisions(modes: TypedHookModes, step_ms: int) -> TypedPromptDecisions`.
  - Constants: `MAXIMUM_FILLER_CHECKS = 16`, `FILLER_OMISSION_DETAIL = "judged filler; fetch with get_context item_ids"`, `APPROVED_EVENT_ITEM_PREFIX = "approved-episodic:"`, `UNSURE = "unsure"`.

Spec rules implemented here: §4.1 route table including "unsure or no answer means the rules' own needs"; §4.2 eligibility, exemptions and the 0.7 drop rule; §5 one standard `lower_rank` omission per dropped note; §4.3 hint only for light + `read_heavy` + no veto; §4.4 skill outcomes compared with the keyword top candidate. This module must not import `mnemo_memory.connectors.typesafe` or `apps/cli/main.py`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_typed_decision_hook.py`:

```python
"""Pure combine functions for the Jev hook decisions (spec 2026-10-02 §4)."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, replace
from datetime import UTC, datetime

import pytest

from mnemo_memory.apps.cli.typed_decision_hook import (
    APPROVED_EVENT_ITEM_PREFIX,
    FILLER_OMISSION_DETAIL,
    MAXIMUM_FILLER_CHECKS,
    FillerCandidate,
    SkillComparison,
    TypedAnswers,
    TypedHookModes,
    TypedStepInput,
    combine_typed_decisions,
    confidence_bucket,
    effective_skill_names,
    filler_candidates,
    filler_omission,
    front_door_axes,
    memory_need_outcome,
    notes_to_drop,
    omission_line,
    skill_comparison,
    typed_hook_modes,
    typed_step_error_decisions,
    with_task_size_hint,
    without_filler_notes,
)
from mnemo_memory.packages.application import PersonalSettings
from mnemo_memory.packages.application.context_routing import (
    AutomaticContextNeed,
    AutomaticContextRoute,
    plan_automatic_context_needs,
)
from mnemo_memory.packages.domain import (
    ConflictNotice,
    ConflictState,
    ContentRepresentation,
    ContextBudget,
    ContextItem,
    ContextItemType,
    ContextPacket,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryScope,
    OmissionReason,
    OwnerId,
    PacketSchemaVersion,
    ProjectId,
    ProvenanceNotice,
    RequestId,
    ScopeLevel,
    Sensitivity,
    SourceId,
    SourceTrustClass,
    TypedDecisionMode,
    TypedDecisionUnavailableReason,
    ValidityState,
    VerificationStatus,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.model_gateway.cascade_router import ClassifierResult
from mnemo_memory.packages.model_gateway.decision_axes import HINT_TEXT
from mnemo_memory.packages.model_gateway.typed_decisions import TierDecision, TypedDecisionOutcome

OFF, SHADOW, LIVE = TypedDecisionMode.OFF, TypedDecisionMode.SHADOW, TypedDecisionMode.LIVE
YES, NO = AutomaticContextNeed.YES, AutomaticContextNeed.NO
ALL_SHADOW = TypedHookModes(SHADOW, SHADOW, SHADOW, SHADOW)
ALL_LIVE = TypedHookModes(LIVE, LIVE, LIVE, LIVE)
LAZY_PROMPT = "finance reconciliation variance"
RULES_LAZY = plan_automatic_context_needs(LAZY_PROMPT)
NOW = datetime(2026, 10, 2, tzinfo=UTC)
SCOPE = MemoryScope(
    OwnerId.from_string("00000000-0000-4000-8000-000000000001"),
    ScopeLevel.PROJECT,
    Visibility.PROJECT,
    WorkspaceId.from_string("00000000-0000-4000-8001-000000000001"),
    ProjectId.from_string("00000000-0000-4000-8002-000000000001"),
)
EVIDENCE = EvidenceReference(
    EvidenceId.new(),
    SourceId.new(),
    EvidenceSourceType.TOOL_RESULT,
    SourceTrustClass.VERIFIED_TOOL_RESULT,
    "fixture://typed-hook",
    "sha256:" + "a" * 64,
    EvidenceLocation("fixture://typed-hook"),
    NOW,
    VerificationStatus.VERIFIED,
)
BASE_STEP = TypedStepInput(
    prompt=LAZY_PROMPT,
    modes=ALL_SHADOW,
    hard_rule=False,
    rules_plan=RULES_LAZY,
    rules_route=AutomaticContextRoute.KNOWLEDGE,
    skill_names=("release-notes", "test-plan"),
    keyword_skill_names=("release-notes",),
    filler_candidates=(
        FillerCandidate("knowledge:useful", "Invoice export keeps ledger order."),
        FillerCandidate("approved-episodic:filler", "FILLER chatter about lunch."),
    ),
)


def _choice(axis: str, label: str, confidence: float, score: float = 0.0) -> ClassifierResult:
    return ClassifierResult(axis, label, math.log(confidence), score, confidence=confidence)


def _answered(*results: ClassifierResult) -> TypedDecisionOutcome:
    return TypedDecisionOutcome(tuple(results), None, 120, "jev-1.13.0")


def _blocked(reason: TypedDecisionUnavailableReason) -> TypedDecisionOutcome:
    return TypedDecisionOutcome((), reason, 0, None)


FILLER = _answered(_choice("note_substance", "filler", 0.95, 0.95))
KEEP = _answered(_choice("note_substance", "task_information", 0.95, 0.05))
FRONT = _answered(
    _choice("memory_need", "nothing", 0.95),
    _choice("complexity", "light", 0.95, 0.05),
    _choice("tool_need", "read_heavy", 0.95, 0.2625),
    _choice("skill_pick", "test-plan", 0.9),
)
LIGHT = TierDecision("light", "light", None)


def _item(
    item_id: str,
    content: str,
    *,
    item_type: ContextItemType = ContextItemType.KNOWLEDGE,
    sensitivity: Sensitivity = Sensitivity.NORMAL,
    conflict_state: ConflictState = ConflictState.NONE,
) -> ContextItem:
    return ContextItem(
        item_id,
        item_type,
        SCOPE,
        content,
        ContentRepresentation.UNTRUSTED_EVIDENCE,
        (len(content) + 3) // 4,
        (EVIDENCE,),
        SourceTrustClass.USER_AUTHORED,
        sensitivity,
        ValidityState.UNKNOWN,
        None,
        conflict_state,
        NOW,
    )


def _note(
    item_id: str,
    heading: str,
    content: str,
    *,
    sensitivity: Sensitivity = Sensitivity.NORMAL,
) -> ContextItem:
    body = json.dumps({"content": content, "heading": heading}, sort_keys=True)
    return _item(item_id, body, sensitivity=sensitivity)


def _event(item_id: str, summary: str) -> ContextItem:
    body = json.dumps(
        {"event_kind": "decision", "occurred_at": NOW.isoformat(), "summary": summary},
        sort_keys=True,
    )
    return _item(item_id, body, item_type=ContextItemType.EPISODIC_MEMORY)


def _packet(
    *items: ContextItem,
    conflicts: tuple[ConflictNotice, ...] = (),
    budget: ContextBudget | None = None,
) -> ContextPacket:
    knowledge = tuple(item for item in items if item.item_type is ContextItemType.KNOWLEDGE)
    episodic = tuple(
        item for item in items if item.item_type is ContextItemType.EPISODIC_MEMORY
    )
    provenance = tuple(
        ProvenanceNotice(
            f"provenance:{item.item_id}", item.item_id, "fixture://typed-hook", "a" * 64, (EVIDENCE,)
        )
        for item in (*episodic, *knowledge)
    )
    return ContextPacket(
        PacketSchemaVersion.V1,
        RequestId.new(),
        SCOPE,
        "typed hook",
        None,
        NOW,
        None,
        sum(item.token_estimate for item in items),
        budget or ContextBudget(),
        "mnemo-test/1",
        episodic_memories=episodic,
        knowledge_items=knowledge,
        provenance=provenance,
        conflicts=conflicts,
    )


def test_typed_hook_modes_follow_the_master_switch() -> None:
    assert typed_hook_modes(PersonalSettings()) == TypedHookModes()
    assert TypedHookModes().any_on is False
    switched = PersonalSettings.from_dict(
        {
            **PersonalSettings().to_dict(),
            "experimental_typed_decisions_enabled": True,
            "typed_decision_modes": {"skill": "shadow", "tier_hint": "shadow"},
        }
    )
    modes = typed_hook_modes(switched)
    assert modes == TypedHookModes(OFF, OFF, SHADOW, SHADOW)
    assert modes.any_on is True


@pytest.mark.parametrize(
    ("label", "needs", "route"),
    [
        ("past_sessions", (YES, NO), AutomaticContextRoute.PRIOR_MEMORY),
        ("project_docs", (YES, NO), AutomaticContextRoute.KNOWLEDGE),
        ("code_structure", (NO, YES), AutomaticContextRoute.STRUCTURE),
        ("code_and_history", (YES, YES), AutomaticContextRoute.PRIOR_MEMORY),
        ("nothing", (NO, NO), None),
    ],
)
def test_memory_label_table_maps_needs_and_routes(
    label: str,
    needs: tuple[AutomaticContextNeed, AutomaticContextNeed],
    route: AutomaticContextRoute | None,
) -> None:
    outcome = memory_need_outcome(
        _choice("memory_need", label, 0.9), AutomaticContextRoute.PRIOR_MEMORY
    )
    assert (outcome.label, outcome.needs, outcome.route) == (label, needs, route)


@pytest.mark.parametrize(
    ("rules_route", "expected"),
    [
        (AutomaticContextRoute.STRUCTURE, AutomaticContextRoute.STRUCTURE),
        (AutomaticContextRoute.KNOWLEDGE, AutomaticContextRoute.KNOWLEDGE),
        (AutomaticContextRoute.SKILL_DISCOVERY, AutomaticContextRoute.KNOWLEDGE),
        (AutomaticContextRoute.NONE, AutomaticContextRoute.KNOWLEDGE),
    ],
)
def test_code_and_history_keeps_a_retrieving_rules_route_else_knowledge(
    rules_route: AutomaticContextRoute, expected: AutomaticContextRoute
) -> None:
    outcome = memory_need_outcome(_choice("memory_need", "code_and_history", 0.9), rules_route)
    assert outcome.route is expected


def test_unsure_or_missing_memory_answer_leaves_the_rules_needs() -> None:
    for result in (
        _choice("memory_need", "past_sessions", 0.59),
        _choice("skill_pick", "nothing", 0.9),
        None,
    ):
        outcome = memory_need_outcome(result, AutomaticContextRoute.KNOWLEDGE)
        assert (outcome.label, outcome.needs, outcome.route) == (None, None, None)


@pytest.mark.parametrize(
    ("confidence", "bucket"),
    [
        (0.0, "<0.5"),
        (0.49, "<0.5"),
        (0.5, "0.5-0.6"),
        (0.59, "0.5-0.6"),
        (0.6, "0.6-0.8"),
        (0.79, "0.6-0.8"),
        (0.8, "0.8-0.9"),
        (0.9, ">=0.9"),
        (1.0, ">=0.9"),
        (None, None),
    ],
)
def test_confidence_buckets_split_at_the_spec_boundaries(
    confidence: float | None, bucket: str | None
) -> None:
    assert confidence_bucket(confidence) == bucket


def names(step: TypedStepInput) -> list[str]:
    return [axis.name for axis in front_door_axes(step)]


def test_front_door_axes_follow_modes_and_hard_rules() -> None:
    assert names(BASE_STEP) == ["memory_need", "complexity", "tool_need", "skill_pick"]
    assert names(replace(BASE_STEP, hard_rule=True)) == ["skill_pick"]
    assert names(replace(BASE_STEP, modes=TypedHookModes(tier_hint=SHADOW))) == [
        "complexity",
        "tool_need",
    ]
    assert names(replace(BASE_STEP, skills_over_limit=True)) == [
        "memory_need",
        "complexity",
        "tool_need",
    ]
    assert names(replace(BASE_STEP, skill_names=())) == ["memory_need", "complexity", "tool_need"]
    assert names(replace(BASE_STEP, hard_rule=True, modes=TypedHookModes(SHADOW))) == []


def test_filler_candidates_keep_exempt_notes_out() -> None:
    conflict = ConflictNotice(
        "knowledge-conflict:a:b",
        ("knowledge:conflict-a", "knowledge:conflict-b"),
        (EVIDENCE,),
        ConflictState.UNRESOLVED,
    )
    packet = _packet(
        _note("knowledge:useful", "Invoice export", "Keep ledger sequence numbers exactly."),
        _note("knowledge:secret", "Keys", "Rotate AKIAABCDEFGHIJKLMNOP before Friday."),
        _note("knowledge:private", "Private", "Owner notes.", sensitivity=Sensitivity.CONFIDENTIAL),
        _note("knowledge:conflict-a", "Grain", "Daily grain."),
        _note("knowledge:conflict-b", "Grain", "Hourly grain."),
        _item("knowledge:broken", "not json at all"),
        _event(f"{APPROVED_EVENT_ITEM_PREFIX}pinned", "Keep retries idempotent."),
        _event(f"{APPROVED_EVENT_ITEM_PREFIX}open", "FILLER chatter about lunch."),
        _event("checkpoint-recap:one", "A saved handoff recap."),
        conflicts=(conflict,),
    )
    candidates = filler_candidates(
        packet, pinned_item_ids=frozenset({f"{APPROVED_EVENT_ITEM_PREFIX}pinned"})
    )
    assert [candidate.item_id for candidate in candidates] == [
        "knowledge:useful",
        f"{APPROVED_EVENT_ITEM_PREFIX}open",
    ]
    assert candidates[0].text == "Invoice export Keep ledger sequence numbers exactly."
    assert candidates[1].text == "FILLER chatter about lunch."


def test_filler_candidates_are_bounded_to_sixteen_notes_of_300_characters() -> None:
    packet = _packet(
        *(_note(f"knowledge:{index:02d}", "Long", "word " * 70) for index in range(20)),
        budget=ContextBudget(knowledge=8_000, total_limit=8_000),
    )
    candidates = filler_candidates(packet, pinned_item_ids=frozenset())
    assert len(candidates) == MAXIMUM_FILLER_CHECKS == 16
    assert all(len(candidate.text) <= 300 for candidate in candidates)


def test_notes_to_drop_only_drops_confident_filler() -> None:
    candidates = tuple(FillerCandidate(f"knowledge:{index}", "note") for index in range(4))
    outcomes = (
        FILLER,
        _answered(_choice("note_substance", "filler", 0.65, 0.65)),
        KEEP,
        _blocked(TypedDecisionUnavailableReason.TIMEOUT),
    )
    assert notes_to_drop(candidates, outcomes) == ("knowledge:0",)


def test_filler_notes_leave_one_lower_rank_omission_each() -> None:
    packet = _packet(
        _note("knowledge:useful", "Invoice export", "Keep ledger order."),
        _note("knowledge:filler", "Chatter", "FILLER talk."),
        _event(f"{APPROVED_EVENT_ITEM_PREFIX}filler", "FILLER lunch."),
    )
    reduced, notices = without_filler_notes(
        packet,
        (f"{APPROVED_EVENT_ITEM_PREFIX}filler", "knowledge:filler", "knowledge:not-present"),
    )
    assert notices == (
        filler_omission(f"{APPROVED_EVENT_ITEM_PREFIX}filler"),
        filler_omission("knowledge:filler"),
    )
    assert FILLER_OMISSION_DETAIL == "judged filler; fetch with get_context item_ids"
    assert notices[0].to_dict() == {
        "item_id": f"{APPROVED_EVENT_ITEM_PREFIX}filler",
        "reason": "lower_rank",
        "detail": FILLER_OMISSION_DETAIL,
    }
    assert all(notice.reason is OmissionReason.LOWER_RANK for notice in notices)
    assert [item.item_id for item in reduced.items] == ["knowledge:useful"]
    assert reduced.declared_total_tokens == reduced.computed_total_tokens
    assert reduced.omissions[-2:] == notices
    assert ContextPacket.from_dict(reduced.to_dict()) == reduced
    assert omission_line(notices[1]) == "MNEMO_OMISSION " + json.dumps(
        notices[1].to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    assert without_filler_notes(packet, ()) == (packet, ())


def test_conflict_participants_are_never_removed() -> None:
    conflict = ConflictNotice(
        "knowledge-conflict:a:b", ("knowledge:a", "knowledge:b"), (EVIDENCE,), ConflictState.UNRESOLVED
    )
    packet = _packet(
        _note("knowledge:a", "Grain", "FILLER daily grain."),
        _note("knowledge:b", "Grain", "Hourly grain."),
        conflicts=(conflict,),
    )
    assert without_filler_notes(packet, ("knowledge:a",)) == (packet, ())


@pytest.mark.parametrize(
    ("mode", "axis_built", "accepted", "keyword_top", "expected"),
    [
        (OFF, True, "test-plan", "test-plan", SkillComparison.NOT_ASKED),
        (SHADOW, False, None, "none", SkillComparison.SKIPPED),
        (SHADOW, True, None, "test-plan", SkillComparison.UNSURE),
        (LIVE, True, "test-plan", "test-plan", SkillComparison.AGREED),
        (LIVE, True, "none", "none", SkillComparison.AGREED),
        (SHADOW, True, "none", "release-notes", SkillComparison.DIFFERS),
    ],
)
def test_skill_comparison_states(
    mode: TypedDecisionMode,
    axis_built: bool,
    accepted: str | None,
    keyword_top: str,
    expected: SkillComparison,
) -> None:
    assert (
        skill_comparison(mode, axis_built=axis_built, accepted=accepted, keyword_top=keyword_top)
        is expected
    )


def test_effective_skill_names_follow_section_4_4() -> None:
    assert effective_skill_names(("release-notes",), None) == ("release-notes",)
    assert effective_skill_names(("release-notes",), "none") == ()
    assert effective_skill_names(("release-notes",), "test-plan") == ("test-plan",)


def test_task_size_hint_is_appended_or_attached_alone() -> None:
    assert with_task_size_hint(None) == HINT_TEXT
    assert with_task_size_hint("MNEMO_CONTEXT_END") == "MNEMO_CONTEXT_END\n" + HINT_TEXT
    assert (len(HINT_TEXT) + 3) // 4 <= 40


def test_shadow_records_every_answer_and_changes_nothing() -> None:
    decisions = combine_typed_decisions(
        BASE_STEP,
        TypedAnswers(FRONT, (KEEP, FILLER), LIGHT),
        step_ms=310,
        model_version="jev-1.13.0",
    )
    assert decisions.typed_needs is None and decisions.memory_route is None
    assert decisions.drop_item_ids == () and decisions.skill is None
    assert decisions.show_hint is False
    assert asdict(decisions.telemetry) == {
        "front_door_mode": "shadow",
        "relevance_mode": "shadow",
        "tier_hint_mode": "shadow",
        "skill_mode": "shadow",
        "front_door_outcome": "answered",
        "step_ms": 310,
        "model_version": "jev-1.13.0",
        "memory_label": "nothing",
        "memory_confidence_bucket": ">=0.9",
        "action": "none",
        "agrees_with_rules": False,
        "notes_checked": 2,
        "notes_dropped": 1,
        "notes_unanswered": 0,
        "tier": "light",
        "hint": "would_show",
        "skill": "differs",
    }


def test_live_returns_only_the_decisions_to_apply() -> None:
    decisions = combine_typed_decisions(
        replace(BASE_STEP, modes=ALL_LIVE),
        TypedAnswers(FRONT, (KEEP, FILLER), LIGHT),
        step_ms=310,
        model_version="jev-1.13.0",
    )
    assert decisions.typed_needs == (NO, NO)
    assert decisions.memory_route is None
    assert decisions.drop_item_ids == ("approved-episodic:filler",)
    assert decisions.skill == "test-plan"
    assert decisions.show_hint is True
    assert decisions.telemetry.hint == "shown"


def test_hard_rule_leaves_memory_need_to_the_rules() -> None:
    skill_only = _answered(_choice("skill_pick", "none", 0.9))
    decisions = combine_typed_decisions(
        replace(BASE_STEP, modes=ALL_LIVE, hard_rule=True, filler_candidates=()),
        TypedAnswers(skill_only, (), None),
        step_ms=5,
        model_version=None,
    )
    assert decisions.typed_needs is None
    assert decisions.telemetry.memory_label is None
    assert decisions.telemetry.action is None
    assert decisions.telemetry.tier is None
    assert decisions.skill == "none"


def test_an_unavailable_front_door_falls_back_everywhere() -> None:
    blocked = _blocked(TypedDecisionUnavailableReason.DATA_ROUTE_BLOCKED)
    decisions = combine_typed_decisions(
        replace(BASE_STEP, modes=ALL_LIVE),
        TypedAnswers(
            blocked,
            (blocked, blocked),
            TierDecision("heavy", "unavailable:data_route_blocked", None),
        ),
        step_ms=2,
        model_version=None,
    )
    assert decisions.typed_needs is None and decisions.drop_item_ids == ()
    assert decisions.skill is None and decisions.show_hint is False
    telemetry = decisions.telemetry
    assert telemetry.front_door_outcome == "data_route_blocked"
    assert telemetry.memory_label == "unsure" and telemetry.memory_confidence_bucket is None
    assert (telemetry.notes_checked, telemetry.notes_dropped, telemetry.notes_unanswered) == (
        2,
        0,
        2,
    )
    assert (telemetry.tier, telemetry.hint, telemetry.skill) == ("heavy", "none", "unsure")


def test_below_bar_memory_answer_is_unsure_and_keeps_the_rules_needs() -> None:
    unsure = _answered(_choice("memory_need", "past_sessions", 0.58))
    decisions = combine_typed_decisions(
        replace(BASE_STEP, modes=TypedHookModes(front_door=LIVE), filler_candidates=()),
        TypedAnswers(unsure, (), None),
        step_ms=5,
        model_version="jev-1.13.0",
    )
    assert decisions.typed_needs is None
    assert decisions.telemetry.memory_label == "unsure"
    assert decisions.telemetry.memory_confidence_bucket == "0.5-0.6"
    assert decisions.telemetry.action is None and decisions.telemetry.agrees_with_rules is None


def test_no_front_door_request_is_not_asked() -> None:
    decisions = combine_typed_decisions(
        replace(BASE_STEP, modes=TypedHookModes(relevance=SHADOW)),
        TypedAnswers(None, (KEEP, FILLER), None),
        step_ms=5,
        model_version=None,
    )
    assert decisions.telemetry.front_door_outcome == "not_asked"
    assert decisions.telemetry.skill == "not_asked"
    assert decisions.telemetry.hint == "none"


def test_typed_step_error_changes_nothing() -> None:
    decisions = typed_step_error_decisions(ALL_LIVE, 41)
    assert decisions.typed_needs is None and decisions.drop_item_ids == ()
    assert decisions.skill is None and decisions.show_hint is False
    assert decisions.telemetry.front_door_outcome == "typed_step_error"
    assert decisions.telemetry.step_ms == 41
    assert decisions.telemetry.skill == "unsure"
    assert typed_step_error_decisions(TypedHookModes(), 0).telemetry.skill == "not_asked"


def test_telemetry_values_never_carry_prompt_note_or_skill_text() -> None:
    step = replace(
        BASE_STEP,
        prompt="private-prompt-7f3a finance reconciliation variance",
        skill_names=("private-skill-9b1d", "test-plan"),
        keyword_skill_names=("private-skill-9b1d",),
        filler_candidates=(FillerCandidate("knowledge:x", "private-note-21c9 FILLER"),),
    )
    decisions = combine_typed_decisions(
        step,
        TypedAnswers(FRONT, (FILLER,), LIGHT),
        step_ms=1,
        model_version="jev-1.13.0",
    )
    encoded = json.dumps(asdict(decisions.telemetry))
    for marker in ("private-prompt-7f3a", "private-skill-9b1d", "private-note-21c9", "test-plan"):
        assert marker not in encoded
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/unit/test_typed_decision_hook.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mnemo_memory.apps.cli.typed_decision_hook'`.

- [ ] **Step 3: Create the module**

Create `src/mnemo_memory/apps/cli/typed_decision_hook.py`:

```python
"""Per-prompt typed-decision step for the automatic-memory hook (spec 2026-10-02 §3–§4).

The functions here turn guard outcomes into plain values and never read or write files.
``main.py`` prepares the local inputs, runs the step, and applies live answers on top of
today's rules result. Prompt text, note text and skill names never enter
``TypedTelemetryValues``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from mnemo_memory.packages.application import PersonalSettings
from mnemo_memory.packages.application.context_routing import (
    AutomaticContextNeed,
    AutomaticContextRoute,
    AutomaticContextShadowAction,
    AutomaticContextShadowPlan,
    LearnedRoutePhrase,
    plan_automatic_context_needs,
)
from mnemo_memory.packages.domain import (
    ConflictState,
    ContextItem,
    ContextItemType,
    ContextPacket,
    OmissionNotice,
    OmissionReason,
    Sensitivity,
    TypedDecisionKind,
    TypedDecisionMode,
)
from mnemo_memory.packages.model_gateway.cascade_router import ClassifierAxis, ClassifierResult
from mnemo_memory.packages.model_gateway.decision_axes import (
    COMPLEXITY,
    HINT_TEXT,
    MEMORY_NEED,
    NOTE_SUBSTANCE,
    SKILL_PICK_NAME,
    SKILL_PICK_NONE,
    TOOL_NEED,
    accepted_choice,
    accepted_skill,
    hint_eligible,
    note_text,
    should_drop_note,
    skill_pick_axis,
)
from mnemo_memory.packages.model_gateway.typed_decisions import (
    GuardedTypedDecisionClassifier,
    TierDecision,
    TypedDecisionOutcome,
    TypedDecisionRecorder,
)
from mnemo_memory.packages.policy.content_safety import contains_high_confidence_secret

OFF = TypedDecisionMode.OFF
SHADOW = TypedDecisionMode.SHADOW
LIVE = TypedDecisionMode.LIVE
HOOK_KINDS: tuple[TypedDecisionKind, ...] = (
    TypedDecisionKind.FRONT_DOOR,
    TypedDecisionKind.RELEVANCE,
    TypedDecisionKind.TIER_HINT,
    TypedDecisionKind.SKILL,
)
MAXIMUM_FILLER_CHECKS = 16
FILLER_OMISSION_DETAIL = "judged filler; fetch with get_context item_ids"
APPROVED_EVENT_ITEM_PREFIX = "approved-episodic:"
UNSURE = "unsure"
_YES = AutomaticContextNeed.YES
_NO = AutomaticContextNeed.NO
_RETRIEVAL_ROUTES = frozenset(
    {
        AutomaticContextRoute.PRIOR_MEMORY,
        AutomaticContextRoute.KNOWLEDGE,
        AutomaticContextRoute.STRUCTURE,
    }
)
# Spec §4.1: label -> (long_term, structural) and the retrieval route in live mode.
_NEEDS_BY_LABEL: dict[str, tuple[AutomaticContextNeed, AutomaticContextNeed]] = {
    "past_sessions": (_YES, _NO),
    "project_docs": (_YES, _NO),
    "code_structure": (_NO, _YES),
    "code_and_history": (_YES, _YES),
    "nothing": (_NO, _NO),
}
_ROUTE_BY_LABEL: dict[str, AutomaticContextRoute | None] = {
    "past_sessions": AutomaticContextRoute.PRIOR_MEMORY,
    "project_docs": AutomaticContextRoute.KNOWLEDGE,
    "code_structure": AutomaticContextRoute.STRUCTURE,
    "nothing": None,
}


@dataclass(frozen=True, slots=True)
class TypedHookModes:
    """The four hook decision modes; all ``off`` means today's path, byte for byte."""

    front_door: TypedDecisionMode = TypedDecisionMode.OFF
    relevance: TypedDecisionMode = TypedDecisionMode.OFF
    tier_hint: TypedDecisionMode = TypedDecisionMode.OFF
    skill: TypedDecisionMode = TypedDecisionMode.OFF

    def __post_init__(self) -> None:
        if any(not isinstance(mode, TypedDecisionMode) for mode in self._modes()):
            raise TypeError("typed hook modes are invalid")

    def _modes(self) -> tuple[TypedDecisionMode, ...]:
        return (self.front_door, self.relevance, self.tier_hint, self.skill)

    @property
    def any_on(self) -> bool:
        return any(mode is not TypedDecisionMode.OFF for mode in self._modes())


def typed_hook_modes(settings: PersonalSettings) -> TypedHookModes:
    """Read the four hook modes from settings; the master switch off means all off."""

    if not settings.experimental_typed_decisions_enabled:
        return TypedHookModes()
    return TypedHookModes(
        settings.typed_decision_mode(TypedDecisionKind.FRONT_DOOR),
        settings.typed_decision_mode(TypedDecisionKind.RELEVANCE),
        settings.typed_decision_mode(TypedDecisionKind.TIER_HINT),
        settings.typed_decision_mode(TypedDecisionKind.SKILL),
    )


@dataclass(frozen=True, slots=True)
class FillerCandidate:
    """One eligible pre-fetched note: its item ID and the bounded text Jev judges."""

    item_id: str
    text: str


@dataclass(frozen=True, slots=True)
class TypedStepInput:
    """Everything the step needs, already bounded and read locally (spec §3 step 2)."""

    prompt: str
    modes: TypedHookModes
    hard_rule: bool
    rules_plan: AutomaticContextShadowPlan
    rules_route: AutomaticContextRoute
    learned_phrases: tuple[LearnedRoutePhrase, ...] = ()
    skill_names: tuple[str, ...] = ()
    skills_over_limit: bool = False
    keyword_skill_names: tuple[str, ...] = ()
    filler_candidates: tuple[FillerCandidate, ...] = ()


@dataclass(frozen=True, slots=True)
class TypedAnswers:
    """Guard outcomes for one prompt: the front door (or not asked), each note, the tier."""

    front_door: TypedDecisionOutcome | None
    fillers: tuple[TypedDecisionOutcome, ...] = ()
    tier: TierDecision | None = None


@dataclass(frozen=True, slots=True)
class MemoryNeedOutcome:
    """An accepted memory-need label as ``(long_term, structural)`` needs and a route."""

    label: str | None
    needs: tuple[AutomaticContextNeed, AutomaticContextNeed] | None
    route: AutomaticContextRoute | None


class SkillComparison(StrEnum):
    """Skill pick compared with keyword matching; never a skill name (spec §4.4)."""

    AGREED = "agreed"
    DIFFERS = "differs"
    UNSURE = "unsure"
    SKIPPED = "skipped"
    NOT_ASKED = "not_asked"


@dataclass(frozen=True, slots=True)
class TypedTelemetryValues:
    """The spec §7 ``typed_v1`` values for one prompt: closed values, counts and booleans."""

    front_door_mode: str
    relevance_mode: str
    tier_hint_mode: str
    skill_mode: str
    front_door_outcome: str
    step_ms: int
    model_version: str | None
    memory_label: str | None
    memory_confidence_bucket: str | None
    action: str | None
    agrees_with_rules: bool | None
    notes_checked: int
    notes_dropped: int
    notes_unanswered: int
    tier: str | None
    hint: str
    skill: str


@dataclass(frozen=True, slots=True)
class TypedPromptDecisions:
    """What to apply live (only for live modes) plus the telemetry for every mode."""

    typed_needs: tuple[AutomaticContextNeed, AutomaticContextNeed] | None
    memory_route: AutomaticContextRoute | None
    drop_item_ids: tuple[str, ...]
    skill: str | None
    show_hint: bool
    telemetry: TypedTelemetryValues


GuardFactory = Callable[[TypedDecisionRecorder], GuardedTypedDecisionClassifier | None]
DecisionObserver = Callable[[TypedStepInput, TypedPromptDecisions], None]


@dataclass(frozen=True, slots=True)
class TypedHookOverrides:
    """Replay-only seam: a synthetic-source guard factory and fixed modes (spec §3).

    The ``automatic-memory-hook`` command never builds one, so the real hook always uses the
    runtime guard and the locked settings. ``observer`` lets the replay score decisions.
    """

    guard_factory: GuardFactory
    modes: TypedHookModes
    observer: DecisionObserver | None = None


_NO_MEMORY_ANSWER = MemoryNeedOutcome(None, None, None)


def confidence_bucket(confidence: float | None) -> str | None:
    if confidence is None:
        return None
    if confidence < 0.5:
        return "<0.5"
    if confidence < 0.6:
        return "0.5-0.6"
    if confidence < 0.8:
        return "0.6-0.8"
    if confidence < 0.9:
        return "0.8-0.9"
    return ">=0.9"


def memory_need_outcome(
    result: ClassifierResult | None, rules_route: AutomaticContextRoute
) -> MemoryNeedOutcome:
    """Map an accepted memory-need label to needs and a route (spec §4.1 table).

    A missing, foreign or below-bar answer returns no needs, so the rules' own needs stand.
    """

    if result is None or result.axis_name != MEMORY_NEED.name:
        return _NO_MEMORY_ANSWER
    label = accepted_choice(result)
    if label is None or label not in _NEEDS_BY_LABEL:
        return _NO_MEMORY_ANSWER
    route: AutomaticContextRoute | None
    if label == "code_and_history":
        # No single route fetches both today: keep a retrieving rules route, else knowledge.
        route = rules_route if rules_route in _RETRIEVAL_ROUTES else AutomaticContextRoute.KNOWLEDGE
    else:
        route = _ROUTE_BY_LABEL[label]
    return MemoryNeedOutcome(label, _NEEDS_BY_LABEL[label], route)


def skill_axis_for(step: TypedStepInput) -> ClassifierAxis | None:
    if step.modes.skill is OFF or step.skills_over_limit:
        return None
    return skill_pick_axis(step.skill_names)


def front_door_axes(step: TypedStepInput) -> tuple[ClassifierAxis, ...]:
    """Axes for the single front-door request; a hard rule leaves only the skill question."""

    axes: list[ClassifierAxis] = []
    if not step.hard_rule:
        if step.modes.front_door is not OFF:
            axes.append(MEMORY_NEED)
        if step.modes.tier_hint is not OFF:
            axes.extend((COMPLEXITY, TOOL_NEED))
    skill_axis = skill_axis_for(step)
    if skill_axis is not None:
        axes.append(skill_axis)
    return tuple(axes)


def filler_candidates(
    packet: ContextPacket, *, pinned_item_ids: frozenset[str]
) -> tuple[FillerCandidate, ...]:
    """Eligible pre-fetched notes: knowledge sections and approved events (spec §4.2).

    Never sent, always kept: pinned items, conflict participants, non-``normal`` sensitivity,
    text the secret scan flags, and notes whose stored text cannot be read. The active
    checkpoint, procedures and skills are not in these sections at all.
    """

    protected = {item_id for conflict in packet.conflicts for item_id in conflict.item_ids}
    notes = (
        *packet.knowledge_items,
        *(
            item
            for item in packet.episodic_memories
            if item.item_id.startswith(APPROVED_EVENT_ITEM_PREFIX)
        ),
    )
    candidates: list[FillerCandidate] = []
    for item in notes:
        if (
            item.item_id in pinned_item_ids
            or item.item_id in protected
            or item.conflict_state is not ConflictState.NONE
            or item.sensitivity is not Sensitivity.NORMAL
        ):
            continue
        source = _note_source_text(item)
        if source is None:
            continue
        try:
            judged = note_text(source)
        except ValueError:
            continue
        if contains_high_confidence_secret(source, judged):
            continue
        candidates.append(FillerCandidate(item.item_id, judged))
    return tuple(candidates[:MAXIMUM_FILLER_CHECKS])


def _note_source_text(item: ContextItem) -> str | None:
    try:
        value = json.loads(item.content)
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    if item.item_type is ContextItemType.KNOWLEDGE:
        heading, content = value.get("heading"), value.get("content")
        if not isinstance(content, str):
            return None
        return f"{heading}\n{content}" if isinstance(heading, str) and heading.strip() else content
    summary = value.get("summary")
    return summary if isinstance(summary, str) else None


def notes_to_drop(
    candidates: Sequence[FillerCandidate], outcomes: Sequence[TypedDecisionOutcome]
) -> tuple[str, ...]:
    """Item IDs judged confident filler (p(filler) >= 0.7); unanswered or unsure notes stay."""

    drops: list[str] = []
    for candidate, outcome in zip(candidates, outcomes, strict=True):
        result = next(
            (value for value in outcome.results if value.axis_name == NOTE_SUBSTANCE.name), None
        )
        if should_drop_note(result):
            drops.append(candidate.item_id)
    return tuple(drops)


def filler_omission(item_id: str) -> OmissionNotice:
    """One standard omission for one dropped note, valid under the unchanged v1 schema (§5)."""

    return OmissionNotice(item_id, OmissionReason.LOWER_RANK, FILLER_OMISSION_DETAIL)


def without_filler_notes(
    packet: ContextPacket, item_ids: Sequence[str]
) -> tuple[ContextPacket, tuple[OmissionNotice, ...]]:
    """Remove judged-filler notes and add one ``lower_rank`` omission per removed note.

    Only knowledge sections and approved events that are not conflict participants can be
    removed; anything else in ``item_ids`` is ignored. Freed space is not refilled.
    """

    requested = frozenset(item_ids)
    protected = {item_id for conflict in packet.conflicts for item_id in conflict.item_ids}
    removable = tuple(
        item.item_id
        for item in (*packet.episodic_memories, *packet.knowledge_items)
        if item.item_id in requested
        and item.item_id not in protected
        and (
            item.item_type is ContextItemType.KNOWLEDGE
            or item.item_id.startswith(APPROVED_EVENT_ITEM_PREFIX)
        )
    )
    if not removable:
        return packet, ()
    dropped = frozenset(removable)
    notices = tuple(filler_omission(item_id) for item_id in removable)
    removed_tokens = sum(
        item.token_estimate for item in packet.items if item.item_id in dropped
    ) + sum(notice.token_estimate for notice in packet.provenance if notice.item_id in dropped)
    reduced = replace(
        packet,
        declared_total_tokens=packet.declared_total_tokens - removed_tokens,
        knowledge_items=tuple(
            item for item in packet.knowledge_items if item.item_id not in dropped
        ),
        episodic_memories=tuple(
            item for item in packet.episodic_memories if item.item_id not in dropped
        ),
        provenance=tuple(item for item in packet.provenance if item.item_id not in dropped),
        omissions=(*packet.omissions, *notices),
    )
    return reduced, notices


def omission_line(notice: OmissionNotice) -> str:
    """The exact line ``render_automatic_context_packet`` writes for ``notice``."""

    return "MNEMO_OMISSION " + json.dumps(
        notice.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def skill_comparison(
    mode: TypedDecisionMode, *, axis_built: bool, accepted: str | None, keyword_top: str
) -> SkillComparison:
    if mode is OFF:
        return SkillComparison.NOT_ASKED
    if not axis_built:
        return SkillComparison.SKIPPED
    if accepted is None:
        return SkillComparison.UNSURE
    return SkillComparison.AGREED if accepted == keyword_top else SkillComparison.DIFFERS


def effective_skill_names(keyword_names: Sequence[str], accepted: str | None) -> tuple[str, ...]:
    """Candidates after a live answer: a skill, none, or (unsure) keyword matching (§4.4)."""

    if accepted is None:
        return tuple(keyword_names)
    if accepted == SKILL_PICK_NONE:
        return ()
    return (accepted,)


def with_task_size_hint(rendered: str | None) -> str:
    """Append the ~20-token hint to whatever is attached, or attach it alone (spec §4.3)."""

    return HINT_TEXT if rendered is None else f"{rendered}\n{HINT_TEXT}"


def combine_typed_decisions(
    step: TypedStepInput,
    answers: TypedAnswers,
    *,
    step_ms: int,
    model_version: str | None,
) -> TypedPromptDecisions:
    """Turn guard outcomes into live decisions and content-free telemetry (pure)."""

    modes = step.modes
    front = answers.front_door
    results = {} if front is None else {result.axis_name: result for result in front.results}

    memory_asked = front is not None and modes.front_door is not OFF and not step.hard_rule
    memory_result = results.get(MEMORY_NEED.name)
    memory = (
        memory_need_outcome(memory_result, step.rules_route) if memory_asked else _NO_MEMORY_ANSWER
    )
    typed_action: AutomaticContextShadowAction | None = None
    if memory.needs is not None:
        typed_action = plan_automatic_context_needs(
            step.prompt, learned_phrases=step.learned_phrases, typed_needs=memory.needs
        ).action

    drops = notes_to_drop(step.filler_candidates, answers.fillers) if modes.relevance is not OFF else ()
    unanswered = sum(outcome.unavailable_reason is not None for outcome in answers.fillers)

    tier = None if answers.tier is None else answers.tier.route
    eligible = tier is not None and hint_eligible(tier, results.get(TOOL_NEED.name))
    if eligible and modes.tier_hint is LIVE:
        hint = "shown"
    elif eligible and modes.tier_hint is SHADOW:
        hint = "would_show"
    else:
        hint = "none"

    axis_built = skill_axis_for(step) is not None
    accepted = accepted_skill(results.get(SKILL_PICK_NAME)) if axis_built else None
    keyword_top = step.keyword_skill_names[0] if step.keyword_skill_names else SKILL_PICK_NONE
    comparison = skill_comparison(
        modes.skill, axis_built=axis_built, accepted=accepted, keyword_top=keyword_top
    )

    if front is None:
        front_outcome = "not_asked"
    elif front.unavailable_reason is None:
        front_outcome = "answered"
    else:
        front_outcome = front.unavailable_reason.value

    telemetry = TypedTelemetryValues(
        front_door_mode=modes.front_door.value,
        relevance_mode=modes.relevance.value,
        tier_hint_mode=modes.tier_hint.value,
        skill_mode=modes.skill.value,
        front_door_outcome=front_outcome,
        step_ms=step_ms,
        model_version=model_version,
        memory_label=None if not memory_asked else (memory.label or UNSURE),
        memory_confidence_bucket=(
            None
            if not memory_asked or memory_result is None
            else confidence_bucket(memory_result.confidence)
        ),
        action=None if typed_action is None else typed_action.value,
        agrees_with_rules=(
            None if typed_action is None else typed_action is step.rules_plan.action
        ),
        notes_checked=len(answers.fillers),
        notes_dropped=len(drops),
        notes_unanswered=unanswered,
        tier=tier,
        hint=hint,
        skill=comparison.value,
    )
    live_needs = memory.needs if modes.front_door is LIVE else None
    return TypedPromptDecisions(
        typed_needs=live_needs,
        memory_route=memory.route if live_needs is not None else None,
        drop_item_ids=drops if modes.relevance is LIVE else (),
        skill=accepted if modes.skill is LIVE else None,
        show_hint=eligible and modes.tier_hint is LIVE,
        telemetry=telemetry,
    )


def typed_step_error_decisions(modes: TypedHookModes, step_ms: int) -> TypedPromptDecisions:
    """Whole-step fallback: no live change, telemetry says ``typed_step_error`` (spec §7)."""

    return TypedPromptDecisions(
        None,
        None,
        (),
        None,
        False,
        TypedTelemetryValues(
            front_door_mode=modes.front_door.value,
            relevance_mode=modes.relevance.value,
            tier_hint_mode=modes.tier_hint.value,
            skill_mode=modes.skill.value,
            front_door_outcome="typed_step_error",
            step_ms=step_ms,
            model_version=None,
            memory_label=None,
            memory_confidence_bucket=None,
            action=None,
            agrees_with_rules=None,
            notes_checked=0,
            notes_dropped=0,
            notes_unanswered=0,
            tier=None,
            hint="none",
            skill=(
                SkillComparison.NOT_ASKED.value
                if modes.skill is OFF
                else SkillComparison.UNSURE.value
            ),
        ),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_typed_decision_hook.py tests/architecture -q`
Expected: PASS; the Jev-connector importer list is unchanged.

- [ ] **Step 5: Lint, type-check and commit**

Run: `uv run ruff format src/mnemo_memory/apps/cli tests/unit/test_typed_decision_hook.py && uv run ruff check src tests && uv run mypy && npm run -s architecture:check`
Expected: no errors (ruff format wraps the two long expressions above).

```bash
git commit -m "feat(typed-decisions): pure combine functions for the hook decisions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- \
  src/mnemo_memory/apps/cli/typed_decision_hook.py tests/unit/test_typed_decision_hook.py
```

---

### Task 8: The async step — one `asyncio.run`, one `ask_each`, a runtime recorder

**Files:**
- Modify: `src/mnemo_memory/apps/cli/typed_decision_hook.py` (imports; append the step)
- Test: `tests/unit/test_typed_decision_hook.py` (append)

**Interfaces:**
- Consumes: Task 7 types (`TypedStepInput`, `TypedAnswers`, `TypedPromptDecisions`, `GuardFactory`, `front_door_axes`, `combine_typed_decisions`, `typed_step_error_decisions`); `GuardedTypedDecisionClassifier.ask_each(requests, *, total_deadline_seconds)`; `decide_tier`, `TierDecision`, `TypedDecisionRecord`; `AxisRoutedClassifier`, `PrecomputedClassifier`; `RISK_AXIS`, `RiskTermClassifier`; `tier_committee`, `FILLER_CHECK_BUDGET_SECONDS`.
- Produces:
  - `class RuntimeTypedDecisionRecorder` with `records: list[TypedDecisionRecord]` and `record(self, record: TypedDecisionRecord) -> None`.
  - `pinned_model_version(records: Sequence[TypedDecisionRecord]) -> str | None` — first answered `jev-X.Y.Z` version, else `None`.
  - `async ask_typed_questions(guard: GuardedTypedDecisionClassifier, step: TypedStepInput) -> TypedAnswers` — the front-door request (if any axes) and one `NOTE_SUBSTANCE` request per filler candidate, all in one `ask_each` with `total_deadline_seconds=FILLER_CHECK_BUDGET_SECONDS`; the tier is decided from the front-door results.
  - `decide_typed_prompt(guard_factory: GuardFactory, step: TypedStepInput, *, started: float, clock: Callable[[], float] = time.monotonic) -> TypedPromptDecisions` — exactly one `asyncio.run`; any exception returns `typed_step_error_decisions(...)`.

- [ ] **Step 1: Write the failing tests**

Add to the imports of `tests/unit/test_typed_decision_hook.py`:

```python
import asyncio
import threading
import time
from uuid import UUID

from mnemo_memory.apps.cli import typed_decision_hook
from mnemo_memory.apps.cli.typed_decision_hook import (
    GuardFactory,
    RuntimeTypedDecisionRecorder,
    TypedPromptDecisions,
    decide_typed_prompt,
    pinned_model_version,
)
from mnemo_memory.packages.domain import (
    ModelBudgetReservation,
    ModelTaskType,
    TypedDecisionDataRoute,
    TypedDecisionSource,
)
from mnemo_memory.packages.model_gateway.cascade_router import ClassifierAxis
from mnemo_memory.packages.model_gateway.decision_axes import NOTE_SUBSTANCE
from mnemo_memory.packages.model_gateway.typed_decisions import (
    AdapterAnswer,
    GuardedTypedDecisionClassifier,
    TypedDecisionRecord,
    TypedDecisionRecorder,
)
```

(`WorkspaceId` is already imported from `mnemo_memory.packages.domain`.) Append:

```python
class ScriptedAdapter:
    """Answer each axis from a script; notes containing FILLER are filler. Thread-safe."""

    provider_id = "fake"
    model_id = "jev-1.13.0"

    def __init__(
        self,
        answers: dict[str, tuple[str, float]] | None = None,
        *,
        delay: float = 0.0,
        version: str = "jev-1.13.0",
    ) -> None:
        self.answers = answers or {}
        self.delay = delay
        self.version = version
        self.requests: list[tuple[tuple[str, ...], str]] = []
        self._lock = threading.Lock()

    def answer(
        self, axes: tuple[ClassifierAxis, ...], text: str, *, timeout_seconds: float
    ) -> AdapterAnswer:
        with self._lock:
            self.requests.append((tuple(axis.name for axis in axes), text))
        if self.delay:
            time.sleep(self.delay)
        results: list[ClassifierResult] = []
        for axis in axes:
            if axis.name == NOTE_SUBSTANCE.name:
                label, confidence = (
                    ("filler", 0.95) if "FILLER" in text else ("task_information", 0.95)
                )
            else:
                label, confidence = self.answers.get(axis.name, (axis.allowed_labels[0], 0.55))
            rest = (1.0 - confidence) / (len(axis.allowed_labels) - 1)
            probabilities = {
                name: confidence if name == label else rest for name in axis.allowed_labels
            }
            results.append(
                ClassifierResult(
                    axis.name,
                    label,
                    math.log(confidence),
                    axis.escalation_score_for(probabilities),
                    confidence=confidence,
                )
            )
        return AdapterAnswer(tuple(results), self.version, 10)


class AllowBudget:
    def reserve(
        self,
        workspace_id: WorkspaceId,
        task_type: ModelTaskType,
        reservation: ModelBudgetReservation,
    ) -> None:
        return None


def _factory(
    adapter: ScriptedAdapter | None,
    *,
    source: TypedDecisionSource = TypedDecisionSource.SYNTHETIC_FIXTURE,
) -> GuardFactory:
    def build(recorder: TypedDecisionRecorder) -> GuardedTypedDecisionClassifier:
        return GuardedTypedDecisionClassifier(
            adapter,
            data_route=TypedDecisionDataRoute.SYNTHETIC_ONLY,
            source=source,
            budget=AllowBudget(),
            workspace_id=WorkspaceId(UUID(int=0)),
            reservation=ModelBudgetReservation(input_tokens=1_000, output_tokens=1, cost_microusd=0),
            deadline_seconds=0.8,
            recorder=recorder,
        )

    return build


SCRIPT = {
    "memory_need": ("project_docs", 0.9),
    "complexity": ("light", 0.95),
    "tool_need": ("read_heavy", 0.95),
    "skill_pick": ("test-plan", 0.9),
}
STEP = replace(
    BASE_STEP,
    prompt="Summarize the invoice export notes for me.",
    filler_candidates=tuple(
        FillerCandidate(f"knowledge:{index}", f"note {index} FILLER" if index % 2 else f"note {index}")
        for index in range(4)
    ),
)


def _decide(factory: GuardFactory, step: TypedStepInput = STEP) -> TypedPromptDecisions:
    return decide_typed_prompt(factory, step, started=time.monotonic())


def test_one_front_door_request_and_one_request_per_note_run_concurrently() -> None:
    adapter = ScriptedAdapter(SCRIPT, delay=0.3)
    started = time.monotonic()
    decisions = _decide(_factory(adapter))
    elapsed = time.monotonic() - started
    assert elapsed < 1.0  # five 0.3 s requests in series would take 1.5 s
    assert sorted(adapter.requests) == sorted(
        [
            (("memory_need", "complexity", "tool_need", "skill_pick"), STEP.prompt),
            *((("note_substance",), candidate.text) for candidate in STEP.filler_candidates),
        ]
    )
    telemetry = decisions.telemetry
    assert telemetry.front_door_outcome == "answered"
    assert (telemetry.notes_checked, telemetry.notes_dropped, telemetry.notes_unanswered) == (
        4,
        2,
        0,
    )
    assert telemetry.memory_label == "project_docs"
    assert telemetry.tier == "light" and telemetry.hint == "would_show"
    assert telemetry.model_version == "jev-1.13.0"


def test_requests_still_running_at_the_cap_count_as_timeouts() -> None:
    adapter = ScriptedAdapter(SCRIPT, delay=1.5)
    started = time.monotonic()
    decisions = _decide(_factory(adapter))
    assert time.monotonic() - started < 1.4
    telemetry = decisions.telemetry
    assert telemetry.front_door_outcome == "timeout"
    assert telemetry.notes_unanswered == 4 and telemetry.notes_dropped == 0
    assert telemetry.tier == "heavy" and telemetry.hint == "none"
    assert telemetry.model_version is None


def test_model_version_is_folded_from_the_records() -> None:
    records = [
        TypedDecisionRecord("synthetic_fixture", 1, "timeout", 800, None, 0),
        TypedDecisionRecord("synthetic_fixture", 1, "answered", 300, "jev-1.13.0", 10),
    ]
    assert pinned_model_version(records) == "jev-1.13.0"
    assert pinned_model_version(
        [TypedDecisionRecord("synthetic_fixture", 1, "answered", 300, "fake-1", 10)]
    ) is None
    recorder = RuntimeTypedDecisionRecorder()
    recorder.record(records[1])
    assert recorder.records == [records[1]]
    assert _decide(_factory(ScriptedAdapter(SCRIPT, version="fake-1"))).telemetry.model_version is None


def test_runtime_source_is_blocked_and_a_missing_guard_is_disabled() -> None:
    adapter = ScriptedAdapter(SCRIPT)
    blocked = _decide(_factory(adapter, source=TypedDecisionSource.RUNTIME))
    assert blocked.telemetry.front_door_outcome == "data_route_blocked"
    assert blocked.telemetry.notes_unanswered == 4
    assert adapter.requests == []
    disabled = _decide(lambda recorder: None)
    assert disabled.telemetry.front_door_outcome == "disabled"
    assert disabled.telemetry.tier == "heavy"


def test_exactly_one_asyncio_run_per_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    original = asyncio.run
    calls: list[int] = []

    def counting(main: object, **kwargs: object) -> object:
        calls.append(1)
        return original(main, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(typed_decision_hook.asyncio, "run", counting)
    _decide(_factory(ScriptedAdapter(SCRIPT)))
    assert calls == [1]


def test_an_exception_inside_the_step_is_a_step_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(recorder: TypedDecisionRecorder) -> GuardedTypedDecisionClassifier:
        raise RuntimeError("synthetic failure")

    assert _decide(broken).telemetry.front_door_outcome == "typed_step_error"

    def explode(*args: object, **kwargs: object) -> TypedPromptDecisions:
        raise ValueError("synthetic combine failure")

    monkeypatch.setattr(typed_decision_hook, "combine_typed_decisions", explode)
    decisions = _decide(_factory(ScriptedAdapter(SCRIPT)))
    assert decisions.telemetry.front_door_outcome == "typed_step_error"
    assert decisions.drop_item_ids == () and decisions.typed_needs is None


def test_running_event_loop_falls_back_to_a_step_error() -> None:
    async def inside() -> TypedPromptDecisions:
        return _decide(_factory(ScriptedAdapter(SCRIPT)))

    decisions = asyncio.run(inside())
    assert decisions.telemetry.front_door_outcome == "typed_step_error"


def test_long_prompt_reaches_the_adapter_only_as_the_bounded_view() -> None:
    adapter = ScriptedAdapter(SCRIPT)
    prompt = "Summarize the notes. " + ("padding " * 80) + "PRIVATE-MIDDLE-c41e" + (" tail" * 120)
    _decide(_factory(adapter), replace(STEP, prompt=prompt, filler_candidates=()))
    assert len(adapter.requests) == 1
    sent = adapter.requests[0][1]
    assert len(sent) <= 512
    assert "PRIVATE-MIDDLE-c41e" not in sent


def test_hard_rule_sends_only_the_skill_question() -> None:
    adapter = ScriptedAdapter(SCRIPT)
    decisions = _decide(_factory(adapter), replace(STEP, hard_rule=True, filler_candidates=()))
    assert adapter.requests == [(("skill_pick",), STEP.prompt)]
    assert decisions.telemetry.tier is None and decisions.telemetry.memory_label is None


def test_risk_terms_veto_the_hint() -> None:
    adapter = ScriptedAdapter(SCRIPT)
    risky = replace(STEP, prompt="Deploy the export fix to production.", filler_candidates=())
    decisions = _decide(_factory(adapter), risky)
    assert decisions.telemetry.tier == "heavy"
    assert decisions.telemetry.hint == "none"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/unit/test_typed_decision_hook.py -q`
Expected: FAIL — `ImportError: cannot import name 'RuntimeTypedDecisionRecorder'`.

- [ ] **Step 3: Implement the step**

In `typed_decision_hook.py`, extend the imports:

```python
import asyncio
import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
```

add `TypedDecisionUnavailableReason,` to the `mnemo_memory.packages.domain` import, and add:

```python
from mnemo_memory.packages.model_gateway.cascade_router import (
    AxisRoutedClassifier,
    ClassifierAxis,
    ClassifierResult,
    PrecomputedClassifier,
)
from mnemo_memory.packages.model_gateway.decision_axes import (
    COMPLEXITY,
    FILLER_CHECK_BUDGET_SECONDS,
    HINT_TEXT,
    MEMORY_NEED,
    NOTE_SUBSTANCE,
    SKILL_PICK_NAME,
    SKILL_PICK_NONE,
    TOOL_NEED,
    accepted_choice,
    accepted_skill,
    hint_eligible,
    note_text,
    should_drop_note,
    skill_pick_axis,
    tier_committee,
)
from mnemo_memory.packages.model_gateway.rule_axes import RISK_AXIS, RiskTermClassifier
from mnemo_memory.packages.model_gateway.typed_decisions import (
    GuardedTypedDecisionClassifier,
    TierDecision,
    TypedDecisionOutcome,
    TypedDecisionRecord,
    TypedDecisionRecorder,
    decide_tier,
)
```

(these replace the Task 7 `cascade_router`, `decision_axes` and `typed_decisions` imports). Add after the other module constants:

```python
_PINNED_MODEL_VERSION = re.compile(r"jev-[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}")
```

Append to the end of the module:

```python
class RuntimeTypedDecisionRecorder:
    """Collect one prompt's per-request guard records in memory (content-free)."""

    def __init__(self) -> None:
        self.records: list[TypedDecisionRecord] = []

    def record(self, record: TypedDecisionRecord) -> None:
        self.records.append(record)


def pinned_model_version(records: Sequence[TypedDecisionRecord]) -> str | None:
    """Fold per-request records into the one model version telemetry may hold."""

    for record in records:
        version = record.model_version
        if (
            record.outcome == "answered"
            and version is not None
            and _PINNED_MODEL_VERSION.fullmatch(version) is not None
        ):
            return version
    return None


async def ask_typed_questions(
    guard: GuardedTypedDecisionClassifier, step: TypedStepInput
) -> TypedAnswers:
    """Send the front-door request and every note request at once under one 0.8 s cap."""

    axes = front_door_axes(step)
    requests: list[tuple[Sequence[ClassifierAxis], str]] = []
    if axes:
        requests.append((axes, step.prompt))
    requests.extend(((NOTE_SUBSTANCE,), candidate.text) for candidate in step.filler_candidates)
    outcomes: tuple[TypedDecisionOutcome, ...] = ()
    if requests:
        outcomes = await guard.ask_each(
            requests, total_deadline_seconds=FILLER_CHECK_BUDGET_SECONDS
        )
    front = outcomes[0] if axes else None
    fillers = outcomes[1:] if axes else outcomes
    tier: TierDecision | None = None
    if front is not None and step.modes.tier_hint is not OFF and not step.hard_rule:
        tier = await _tier(front, step.prompt)
    return TypedAnswers(front, tuple(fillers), tier)


async def _tier(front: TypedDecisionOutcome, prompt: str) -> TierDecision:
    """The phase-1 committee over answers already in hand; unavailable resolves to heavy."""

    if front.unavailable_reason is not None:
        return TierDecision("heavy", f"unavailable:{front.unavailable_reason.value}", None)
    answered = PrecomputedClassifier(front.results)
    classifier = AxisRoutedClassifier(
        {
            COMPLEXITY.name: answered,
            TOOL_NEED.name: answered,
            RISK_AXIS.name: RiskTermClassifier(),
        }
    )
    return await decide_tier(tier_committee(), classifier, prompt)


def decide_typed_prompt(
    guard_factory: GuardFactory,
    step: TypedStepInput,
    *,
    started: float,
    clock: Callable[[], float] = time.monotonic,
) -> TypedPromptDecisions:
    """Run the step with exactly one ``asyncio.run``; any exception is ``typed_step_error``."""

    try:
        recorder = RuntimeTypedDecisionRecorder()
        guard = guard_factory(recorder)
        if guard is None:
            answers = _unavailable_answers(step, TypedDecisionUnavailableReason.DISABLED)
        else:
            coroutine = ask_typed_questions(guard, step)
            try:
                answers = asyncio.run(coroutine)
            finally:
                coroutine.close()  # no "never awaited" warning when a loop is already running
        return combine_typed_decisions(
            step,
            answers,
            step_ms=_elapsed_ms(started, clock),
            model_version=pinned_model_version(recorder.records),
        )
    except Exception:
        return typed_step_error_decisions(step.modes, _elapsed_ms(started, clock))


def _unavailable_answers(
    step: TypedStepInput, reason: TypedDecisionUnavailableReason
) -> TypedAnswers:
    unavailable = TypedDecisionOutcome((), reason, 0, None)
    front = unavailable if front_door_axes(step) else None
    tier: TierDecision | None = None
    if front is not None and step.modes.tier_hint is not OFF and not step.hard_rule:
        tier = TierDecision("heavy", f"unavailable:{reason.value}", None)
    return TypedAnswers(front, tuple(unavailable for _ in step.filler_candidates), tier)


def _elapsed_ms(started: float, clock: Callable[[], float]) -> int:
    return max(0, min(10_000_000, round((clock() - started) * 1_000)))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_typed_decision_hook.py tests/unit/test_typed_decision_guard.py -q`
Expected: PASS.

- [ ] **Step 5: Lint, type-check and commit**

Run: `uv run ruff format src/mnemo_memory/apps/cli tests/unit/test_typed_decision_hook.py && uv run ruff check src tests && uv run mypy && npm run -s architecture:check`
Expected: no errors.

```bash
git commit -m "feat(typed-decisions): one-shot async step under the 0.8 s cap

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- \
  src/mnemo_memory/apps/cli/typed_decision_hook.py tests/unit/test_typed_decision_hook.py
```

---
### Task 9: Hook integration in `main.py`

**Files:**
- Modify: `src/mnemo_memory/apps/cli/main.py`:
  - imports (`:3-226`)
  - `_automatic_prompt_context_result` (`:506-612`) — extract `_fetch_route_packet`, add `_automatic_prompt_context_for_route`
  - `_automatic_shadow_trace` (`:771-796`) — extract `_learned_route_phrases`
  - `_automatic_prompt_context_for_hook` (`:798-931`) — `replay_overrides`, rules render extraction, typed path
  - new private helpers placed directly after `_automatic_prompt_context_for_hook`
- Create: `scripts/typed_decision_test_support.py` (test-only helpers; same pattern as `scripts/sqlite_migration_test_support.py`)
- Test: `tests/unit/test_typed_hook_integration.py` (new)

**Interfaces:**
- Consumes:
  - Task 3: `build_runtime_typed_decision_classifier(settings, *, data_directory, recorder=...)`, `build_synthetic_typed_decision_classifier(settings, *, data_directory, environ, jev_transport, recorder)`.
  - Task 4: `KnowledgeDocumentSkillRegistry.current_skill_listing(scope, client) -> CurrentSkillListing`; `SKILL_PICK_NONE`.
  - Task 5: `AutomaticContextShadowPlan.hard_rule`; `plan_automatic_context_needs(..., typed_needs=...)`; `typed_route_decision(route)`.
  - Task 7/8: `TypedHookModes`, `TypedHookOverrides`, `TypedStepInput`, `TypedPromptDecisions`, `typed_hook_modes`, `filler_candidates`, `without_filler_notes`, `omission_line`, `FILLER_OMISSION_DETAIL`, `with_task_size_hint`, `decide_typed_prompt`.
- Produces:
  - `_automatic_prompt_context_for_hook(data_directory: Path, scope: MemoryScope, prompt: str, client: ClientName, *, replay_overrides: TypedHookOverrides | None = None) -> PromptContextAttachment`.
  - `@dataclass(frozen=True, slots=True) class _PromptRender(result: _AutomaticPromptContextResult, rendered: str | None, canonical_tokens: int, live_attachment: AutomaticContextLiveAttachment | None)`.
  - `_rules_prompt_render(data_directory, scope, prompt, client, trace, *, experimental_live_gate: bool) -> _PromptRender` — today's path, moved verbatim.
  - `_typed_prompt_render(data_directory, scope, prompt, client, settings, modes, trace, rules, overrides) -> tuple[_PromptRender, _AutomaticShadowTrace | None]` (Task 10 adds a third element).
  - `@dataclass(frozen=True, slots=True) class _TypedApplication(render: _PromptRender, trace: _AutomaticShadowTrace | None, notes_dropped: int)`.
  - `_typed_local_inputs(data_directory, scope, prompt, client, packet, *, list_skills: bool) -> _TypedLocalInputs`.
  - `scripts/typed_decision_test_support.py`: `FAKE_TYPESAFE_KEY`, `FILLER_MARKER`, prompt and note constants, `ScriptedJevTransport`, `choice_answer`, `HookFixture`, `seed_hook_fixture(root, *, semantic_gate, with_handoff=False) -> HookFixture`, `synthetic_overrides(fixture, transport, modes, observer=None) -> TypedHookOverrides`, `run_hook(fixture, prompt, overrides=None) -> PromptContextAttachment`.

Behaviour this task pins (spec §3, §4, §7):
- All modes off (the default): only today's code runs and `typed_decision_hook` is not even imported.
- Shadow: answers are computed and discarded; output is today's output.
- Memory-need live needs the semantic-memory gate (a trace). The typed plan replaces the rules plan for gating; a changed route is fetched after the answer, unfiltered.
- Filler drops apply only to the pre-fetched rules packet. Each dropped note leaves its own standard `lower_rank` omission (spec §5, no packet schema change). If a note's omission line does not fit the render, that note's drop is cancelled, the note is kept, and the rest are rendered again.
- Skill live: one named skill, none, or (unsure) keyword candidates; hard routes are never changed.
- The hint is appended last.
- Any exception in the typed step returns today's result.

- [ ] **Step 1: Create the test-support module**

Create `scripts/typed_decision_test_support.py`:

```python
"""Test-only helpers for the Jev hook wiring: a scripted Jev transport and a seeded project.

Nothing here opens a network connection. The transport answers from a script, and the key is a
fake literal, never the real ``TYPESAFE_API_KEY``.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mnemo_memory.apps.cli import main as cli
from mnemo_memory.apps.cli.typed_decision_composition import (
    build_synthetic_typed_decision_classifier,
)
from mnemo_memory.apps.cli.typed_decision_hook import (
    DecisionObserver,
    TypedHookModes,
    TypedHookOverrides,
)
from mnemo_memory.connectors.automatic_memory.hook import PromptContextAttachment
from mnemo_memory.packages.application import (
    LocalConfig,
    PersonalSettings,
    PersonalSettingsStore,
    RecordApprovedEpisodicEvent,
    SetApprovedEpisodicEventPin,
    build_checkpoint_runtime,
)
from mnemo_memory.packages.application.automatic_memory import (
    LocalMemoryProjectBindingStore,
    MemoryProjectBinding,
)
from mnemo_memory.packages.application.checkpoints import CreateCheckpoint
from mnemo_memory.packages.domain import (
    ApprovedEventKind,
    CheckpointContent,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    SourceId,
    SourceTrustClass,
    VerificationStatus,
)
from mnemo_memory.packages.model_gateway.typed_decisions import (
    GuardedTypedDecisionClassifier,
    TypedDecisionRecorder,
)
from mnemo_memory.packages.storage import SQLiteKnowledgeDocumentRepository
from mnemo_memory.packages.telemetry import (
    AutomaticRouteDiagnosticsMode,
    AutomaticRouteDiagnosticsSettings,
    LocalAutomaticRouteDiagnosticsSettingsStore,
)

FAKE_TYPESAFE_KEY = "test-key-not-real-0000"
FILLER_MARKER = "FILLER"
KNOWLEDGE_PROMPT = "What do the project notes say about the invoice export?"
PRIOR_PROMPT = "Use the decision from our previous session."
SKILL_PROMPT = "Create the changelog entry for version 2.4."
LAZY_PROMPT = "finance reconciliation variance"
GREETING_PROMPT = "hello"
USEFUL_NOTE = "The invoice export must keep the ledger sequence numbers exactly."
FILLER_NOTE = "FILLER: someone mentioned the invoice export over coffee and liked the weather."
PINNED_EVENT = "Keep invoice export retries idempotent."
FILLER_EVENT = "FILLER chatter about lunch plans with the team."
HANDOFF_OBJECTIVE = "Finish the invoice export retry fix."

Answer = tuple[str, float]


def choice_answer(labels: list[str], chosen: str, confidence: float) -> dict[str, object]:
    """One Jev ``choice`` answer whose chosen label carries the highest probability."""

    others = [label for label in labels if label != chosen]
    rest = (1.0 - confidence) / len(others)
    probabilities = {label: rest for label in others}
    probabilities[chosen] = confidence
    return {
        "type": "choice",
        "choice": chosen,
        "confidence": confidence,
        "probabilities": probabilities,
    }


class ScriptedJevTransport:
    """Answer Jev requests from a script; thread-safe and never opens a socket.

    ``answers`` maps a question name to ``(label, confidence)``. ``note_substance`` is answered
    ``filler`` when the judged text contains ``FILLER``, else ``task_information``. Any other
    unscripted question gets its first label at confidence 0.55, below the 0.6 bar.
    """

    def __init__(
        self, answers: Mapping[str, Answer] | None = None, *, model: str = "jev-1.13.0"
    ) -> None:
        self.answers = dict(answers or {})
        self.model = model
        self.calls = 0
        self.questions: list[tuple[str, ...]] = []
        self.states: list[str] = []
        self._lock = threading.Lock()

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        request = json.loads(body)
        state = str(request["state"])
        with self._lock:
            self.calls += 1
            self.questions.append(tuple(request["questions"]))
            self.states.append(state)
        answers: dict[str, object] = {}
        for name, question in request["questions"].items():
            labels = list(question["criteria"])
            if name == "note_substance":
                label, confidence = (
                    ("filler", 0.95) if FILLER_MARKER in state else ("task_information", 0.95)
                )
            else:
                label, confidence = self.answers.get(name, (labels[0], 0.55))
            answers[name] = choice_answer(labels, label, confidence)
        return json.dumps(
            {"model": self.model, "answers": answers, "usage": {"input_tokens": 10}}
        ).encode()


@dataclass(frozen=True, slots=True)
class HookFixture:
    data: Path
    project: Path
    binding: MemoryProjectBinding
    useful_note_prefix: str
    filler_note_prefix: str
    pinned_event_id: str
    filler_event_id: str


def _skill(name: str, tags: str, when: str) -> str:
    return (
        f"---\nmnemo_kind: skill\nmnemo_name: {name}\nmnemo_version: 1.0.0\n"
        f"mnemo_tags: {tags}\nmnemo_clients: codex, claude-code\nmnemo_trust: checked_in\n"
        f"mnemo_when: {when}\n---\n# {name}\nSynthetic skill body.\n"
    )


_SKILLS = {
    "release-notes": _skill(
        "release-notes",
        "release, changelog",
        "Use when drafting release notes or a changelog entry for a new version",
    ),
    "test-plan": _skill(
        "test-plan",
        "testing, coverage",
        "Use when designing a test plan or deciding which tests a change needs",
    ),
}


def _evidence(
    seed: str,
    *,
    source: EvidenceSourceType = EvidenceSourceType.TOOL_RESULT,
    trust: SourceTrustClass = SourceTrustClass.VERIFIED_TOOL_RESULT,
) -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        source,
        trust,
        f"fixture://typed-hook/{seed}",
        "sha256:" + "a" * 64,
        EvidenceLocation(f"fixture://typed-hook/{seed}"),
        datetime(2026, 10, 2, tzinfo=UTC),
        VerificationStatus.VERIFIED,
    )


def _handoff() -> CheckpointContent:
    return CheckpointContent(
        task_objective=HANDOFF_OBJECTIVE,
        completed_work=("Recorded bounded progress.",),
        current_state="The retry fix is half done.",
        remaining_work=("Finish the retry fix.",),
        decisions=("Keep retries idempotent.",),
        failures=(),
        blockers=(),
        relevant_files=("export.py",),
        relevant_artifacts=(),
        verification_performed=("Focused tests ran.",),
        token_estimate=70,
    )


def seed_hook_fixture(
    root: Path, *, semantic_gate: bool, with_handoff: bool = False
) -> HookFixture:
    """A bound project with two notes, two approved events (one pinned) and two skills."""

    project = root / "project"
    project.mkdir(parents=True)
    data = root / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    PersonalSettingsStore(data).save(
        PersonalSettings(experimental_semantic_memory_enabled=semantic_gate)
    )
    LocalAutomaticRouteDiagnosticsSettingsStore(data).save(
        AutomaticRouteDiagnosticsSettings(AutomaticRouteDiagnosticsMode.TRACE, 7)
    )
    (project / "notes").mkdir()
    (project / "notes" / "export.md").write_text(f"# Invoice export\n{USEFUL_NOTE}\n", "utf-8")
    (project / "notes" / "chatter.md").write_text(
        f"# Invoice export chatter\n{FILLER_NOTE}\n", "utf-8"
    )
    (project / "skills").mkdir()
    for name, body in _SKILLS.items():
        (project / "skills" / f"{name}.md").write_text(body, "utf-8")
    cli._refresh_project_knowledge(data, binding)
    with build_checkpoint_runtime(LocalConfig.defaults(data)) as runtime:
        service = runtime.checkpoint_service
        if with_handoff:
            service.create(
                CreateCheckpoint(
                    binding.checkpoint_scope,
                    _handoff(),
                    (
                        _evidence(
                            "handoff",
                            source=EvidenceSourceType.CHECKPOINT,
                            trust=SourceTrustClass.USER_AUTHORED,
                        ),
                    ),
                )
            )
        pinned = service.record_approved_event(
            RecordApprovedEpisodicEvent(
                binding.checkpoint_scope,
                ApprovedEventKind.DECISION,
                PINNED_EVENT,
                "typed-hook:pinned",
                (_evidence("pinned"),),
            )
        ).event
        filler = service.record_approved_event(
            RecordApprovedEpisodicEvent(
                binding.checkpoint_scope,
                ApprovedEventKind.TOOL_OUTCOME,
                FILLER_EVENT,
                "typed-hook:filler",
                (_evidence("filler"),),
            )
        ).event
        service.set_approved_event_pin(
            SetApprovedEpisodicEventPin(
                binding.checkpoint_scope,
                pinned.event_id,
                True,
                "typed-hook:pin",
                (
                    _evidence(
                        "pin",
                        source=EvidenceSourceType.USER_CORRECTION,
                        trust=SourceTrustClass.USER_CORRECTION,
                    ),
                ),
            )
        )
    repository = SQLiteKnowledgeDocumentRepository(data / "mnemo.sqlite3", base_directory=data)
    repository.migrate()
    prefixes = {
        known.relative_path: f"knowledge:{known.document_id}:"
        for known in repository.list_active_documents(binding.scope)
    }
    return HookFixture(
        data,
        project,
        binding,
        prefixes["notes/export.md"],
        prefixes["notes/chatter.md"],
        f"approved-episodic:{pinned.event_id}",
        f"approved-episodic:{filler.event_id}",
    )


def synthetic_overrides(
    fixture: HookFixture,
    transport: ScriptedJevTransport,
    modes: TypedHookModes,
    observer: DecisionObserver | None = None,
) -> TypedHookOverrides:
    """Replay overrides whose guard reaches only ``transport`` (synthetic-fixture source)."""

    def build(recorder: TypedDecisionRecorder) -> GuardedTypedDecisionClassifier:
        return build_synthetic_typed_decision_classifier(
            PersonalSettings(),
            data_directory=fixture.data,
            environ={"TYPESAFE_API_KEY": FAKE_TYPESAFE_KEY},
            jev_transport=transport,
            recorder=recorder,
        )

    return TypedHookOverrides(build, modes, observer)


def run_hook(
    fixture: HookFixture, prompt: str, overrides: TypedHookOverrides | None = None
) -> PromptContextAttachment:
    return cli._automatic_prompt_context_for_hook(
        fixture.data,
        fixture.binding.checkpoint_scope,
        prompt,
        "codex",
        replay_overrides=overrides,
    )
```

- [ ] **Step 2: Write the failing integration tests**

Create `tests/unit/test_typed_hook_integration.py`:

```python
"""The typed step inside the real prompt hook (spec 2026-10-02 §3, §4, §7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo_memory.apps.cli import main as cli
from mnemo_memory.apps.cli import typed_decision_hook
from mnemo_memory.apps.cli.typed_decision_hook import FILLER_OMISSION_DETAIL, TypedHookModes
from mnemo_memory.connectors.automatic_memory.hook import PromptContextAttachment
from mnemo_memory.packages.application import LocalConfig
from mnemo_memory.packages.application.context_routing import AUTOMATIC_CONTEXT_LAZY_PULL_HINT
from mnemo_memory.packages.domain import OmissionNotice, OmissionReason, TypedDecisionMode
from mnemo_memory.packages.model_gateway.decision_axes import HINT_TEXT
from scripts.typed_decision_test_support import (
    GREETING_PROMPT,
    HANDOFF_OBJECTIVE,
    KNOWLEDGE_PROMPT,
    LAZY_PROMPT,
    PINNED_EVENT,
    SKILL_PROMPT,
    ScriptedJevTransport,
    run_hook,
    seed_hook_fixture,
    synthetic_overrides,
)

SHADOW, LIVE = TypedDecisionMode.SHADOW, TypedDecisionMode.LIVE
EVERYTHING = {
    "memory_need": ("nothing", 0.95),
    "complexity": ("light", 0.95),
    "tool_need": ("read_heavy", 0.95),
    "skill_pick": ("test-plan", 0.95),
}


def _filler_omission_ids(context: str | None) -> list[str]:
    """Item IDs named by per-note ``lower_rank`` filler omissions in a rendered attachment."""

    assert context is not None
    ids: list[str] = []
    for line in context.split("\n"):
        if not line.startswith("MNEMO_OMISSION "):
            continue
        value = json.loads(line.removeprefix("MNEMO_OMISSION "))
        if value["reason"] == "lower_rank" and value["detail"] == FILLER_OMISSION_DETAIL:
            ids.append(value["item_id"])
    return ids


@pytest.mark.parametrize("semantic_gate", [False, True])
def test_shadow_answers_change_nothing(tmp_path: Path, semantic_gate: bool) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=semantic_gate)
    transport = ScriptedJevTransport(EVERYTHING)
    shadow = synthetic_overrides(
        fixture, transport, TypedHookModes(SHADOW, SHADOW, SHADOW, SHADOW)
    )
    for prompt in (KNOWLEDGE_PROMPT, SKILL_PROMPT, LAZY_PROMPT, GREETING_PROMPT):
        off = run_hook(fixture, prompt)
        seen = run_hook(fixture, prompt, shadow)
        assert (seen.context, seen.delivery_keys) == (off.context, off.delivery_keys), prompt
    assert transport.calls > 0


def test_each_live_filler_drop_leaves_its_own_lower_rank_omission(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    transport = ScriptedJevTransport()
    live = synthetic_overrides(fixture, transport, TypedHookModes(relevance=LIVE))

    off = run_hook(fixture, KNOWLEDGE_PROMPT)
    dropped = run_hook(fixture, KNOWLEDGE_PROMPT, live)

    assert _filler_omission_ids(off.context) == []
    ids = _filler_omission_ids(dropped.context)
    assert len(ids) == 2
    assert fixture.filler_event_id in ids
    assert any(item_id.startswith(fixture.filler_note_prefix) for item_id in ids)
    assert not any(item_id.startswith(fixture.useful_note_prefix) for item_id in ids)
    assert fixture.pinned_event_id not in ids
    assert all(PINNED_EVENT not in state for state in transport.states)  # pinned never sent


def test_a_drop_is_cancelled_when_its_omission_line_does_not_fit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    original = typed_decision_hook.filler_omission

    def oversized_for_the_note(item_id: str) -> OmissionNotice:
        if item_id.startswith(fixture.filler_note_prefix):
            return OmissionNotice(item_id, OmissionReason.LOWER_RANK, "x" * 6_000)
        return original(item_id)

    monkeypatch.setattr(typed_decision_hook, "filler_omission", oversized_for_the_note)
    live = synthetic_overrides(fixture, ScriptedJevTransport(), TypedHookModes(relevance=LIVE))
    partial = run_hook(fixture, KNOWLEDGE_PROMPT, live)
    # The note whose line cannot fit is kept; the event's drop still applies.
    assert _filler_omission_ids(partial.context) == [fixture.filler_event_id]

    def oversized(item_id: str) -> OmissionNotice:
        return OmissionNotice(item_id, OmissionReason.LOWER_RANK, "x" * 6_000)

    monkeypatch.setattr(typed_decision_hook, "filler_omission", oversized)
    assert run_hook(fixture, KNOWLEDGE_PROMPT, live).context == (
        run_hook(fixture, KNOWLEDGE_PROMPT).context
    )


def test_live_nothing_attaches_nothing(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    live = synthetic_overrides(
        fixture,
        ScriptedJevTransport({"memory_need": ("nothing", 0.95)}),
        TypedHookModes(front_door=LIVE),
    )
    assert run_hook(fixture, KNOWLEDGE_PROMPT).context is not None
    assert run_hook(fixture, KNOWLEDGE_PROMPT, live).context is None


def test_a_changed_route_is_fetched_after_the_answer_unfiltered(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True, with_handoff=True)
    live = synthetic_overrides(
        fixture,
        ScriptedJevTransport({"memory_need": ("past_sessions", 0.95)}),
        TypedHookModes(front_door=LIVE, relevance=LIVE),
    )
    off = run_hook(fixture, KNOWLEDGE_PROMPT)
    fetched = run_hook(fixture, KNOWLEDGE_PROMPT, live)
    assert fetched.context is not None and fetched.context != off.context
    assert HANDOFF_OBJECTIVE in fetched.context
    assert _filler_omission_ids(fetched.context) == []


def test_front_door_live_without_the_semantic_gate_is_not_applied(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    live = synthetic_overrides(
        fixture,
        ScriptedJevTransport({"memory_need": ("nothing", 0.95)}),
        TypedHookModes(front_door=LIVE),
    )
    assert run_hook(fixture, KNOWLEDGE_PROMPT, live).context == (
        run_hook(fixture, KNOWLEDGE_PROMPT).context
    )


def test_hard_rule_prompt_asks_only_the_skill_question(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    transport = ScriptedJevTransport(EVERYTHING)
    shadow = synthetic_overrides(
        fixture, transport, TypedHookModes(SHADOW, SHADOW, SHADOW, SHADOW)
    )
    run_hook(fixture, GREETING_PROMPT, shadow)
    assert transport.questions == [("skill_pick",)]


def _skill_names(context: str | None) -> list[str]:
    if context is None or not context.startswith("MNEMO_SKILL_DISCOVERY_V1 "):
        return []
    value = json.loads(context.removeprefix("MNEMO_SKILL_DISCOVERY_V1 "))
    return [candidate["name"] for candidate in value["candidates"]]


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (("test-plan", 0.95), ["test-plan"]),
        (("none", 0.95), []),
        (("test-plan", 0.55), ["release-notes"]),
    ],
)
def test_live_skill_pick_replaces_keyword_matching_only_when_sure(
    tmp_path: Path, answer: tuple[str, float], expected: list[str]
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    live = synthetic_overrides(
        fixture, ScriptedJevTransport({"skill_pick": answer}), TypedHookModes(skill=LIVE)
    )
    assert _skill_names(run_hook(fixture, SKILL_PROMPT).context) == ["release-notes"]
    assert _skill_names(run_hook(fixture, SKILL_PROMPT, live).context) == expected


def test_live_hint_is_appended_and_merged_with_lazy_pull(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    live = synthetic_overrides(
        fixture,
        ScriptedJevTransport({"complexity": ("light", 0.95), "tool_need": ("read_heavy", 0.95)}),
        TypedHookModes(tier_hint=LIVE),
    )
    pushed = run_hook(fixture, KNOWLEDGE_PROMPT, live).context
    lazy = run_hook(fixture, LAZY_PROMPT, live).context
    assert pushed is not None and pushed.endswith("\n" + HINT_TEXT)
    assert lazy == AUTOMATIC_CONTEXT_LAZY_PULL_HINT + "\n" + HINT_TEXT
    assert all((len(line) + 3) // 4 <= 40 for line in lazy.split("\n"))


def test_an_exception_inside_the_step_keeps_todays_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    off = run_hook(fixture, KNOWLEDGE_PROMPT)

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic local failure")

    monkeypatch.setattr(cli, "_typed_local_inputs", broken)
    live = synthetic_overrides(
        fixture, ScriptedJevTransport(EVERYTHING), TypedHookModes(LIVE, LIVE, LIVE, LIVE)
    )
    assert run_hook(fixture, KNOWLEDGE_PROMPT, live).context == off.context


def test_the_hook_command_never_passes_replay_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    calls: list[dict[str, object]] = []

    def spy(*args: object, **kwargs: object) -> PromptContextAttachment:
        calls.append(dict(kwargs))
        return PromptContextAttachment(None)

    monkeypatch.setattr(cli, "_automatic_prompt_context_for_hook", spy)
    hook = cli.build_automatic_memory_hook(LocalConfig.defaults(fixture.data), "codex")
    assert hook.prompt_context_loader is not None
    hook.prompt_context_loader(fixture.binding.checkpoint_scope, KNOWLEDGE_PROMPT)
    assert calls == [{}]


def test_typed_modes_off_never_run_the_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("the typed step must not run while every mode is off")

    monkeypatch.setattr(cli, "_typed_prompt_render", forbidden)
    assert run_hook(fixture, KNOWLEDGE_PROMPT).context is not None
    assert run_hook(
        fixture,
        KNOWLEDGE_PROMPT,
        synthetic_overrides(fixture, ScriptedJevTransport(), TypedHookModes()),
    ).context is not None
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/unit/test_typed_hook_integration.py -q`
Expected: FAIL — `TypeError: _automatic_prompt_context_for_hook() got an unexpected keyword argument 'replay_overrides'`.

- [ ] **Step 4: Update the imports in `main.py`**

- Add `from collections.abc import Callable` and `from contextlib import suppress` to the standard-library imports.
- Change `from typing import Literal, cast` to `from typing import TYPE_CHECKING, Literal, cast`.
- Add `LearnedRoutePhrase,` and `typed_route_decision,` to the `mnemo_memory.packages.application.context_routing` import list.
- Add `ProjectSkill,`, `TypedDecisionMode,` and `normalize_agent_client,` to the `mnemo_memory.packages.domain` import list.
- Change the skills-registry import to:

```python
from mnemo_memory.packages.skills_registry import (
    CurrentSkillListing,
    KnowledgeDocumentProcedureRegistry,
    KnowledgeDocumentSkillRegistry,
    SkillDiscoveryCandidate,
)
```

- After the last import block add:

```python
if TYPE_CHECKING:
    from mnemo_memory.apps.cli.typed_decision_hook import (
        TypedHookModes,
        TypedHookOverrides,
        TypedPromptDecisions,
        TypedStepInput,
    )
    from mnemo_memory.packages.model_gateway.typed_decisions import (
        GuardedTypedDecisionClassifier,
        TypedDecisionRecorder,
    )
```

`typed_decision_hook` and `typed_decision_composition` are imported lazily inside the typed path, so a hook process with every mode off never pays for `asyncio` or `urllib`.

- [ ] **Step 5: Extract the route fetch**

In `_automatic_prompt_context_result`, replace everything from the line `prompt_budget = _automatic_budget(data_directory, _AUTOMATIC_PROMPT_CONTEXT_BUDGET)` through the end of the `except LocalEmbeddingError:` fallback (the statement that assigns `packet`) with:

```python
            packet = _fetch_route_packet(
                runtime,
                data_directory,
                scope,
                prompt,
                decision,
                experimental_semantic_memory_enabled=experimental_semantic_memory_enabled,
            )
```

Directly after `_automatic_prompt_context_result` add (the body is the moved code, unchanged apart from `return`):

```python
def _fetch_route_packet(
    runtime: CheckpointRuntime,
    data_directory: Path,
    scope: MemoryScope,
    prompt: str,
    decision: AutomaticContextRouteDecision,
    *,
    experimental_semantic_memory_enabled: bool,
) -> ContextPacket:
    """Fetch one already-selected retrieval route inside an open runtime (rules retrieval)."""

    assert runtime.knowledge_document_repository is not None
    prompt_budget = _automatic_budget(data_directory, _AUTOMATIC_PROMPT_CONTEXT_BUDGET)
    if decision.route is AutomaticContextRoute.PRIOR_MEMORY:
        prompt_budget = ContextBudget(
            active_task_checkpoint=prompt_budget.active_task_checkpoint,
            episodic_memories=1_000,
            knowledge=0,
            structural=0,
            skills_and_procedures=0,
            provenance_and_conflicts=0,
            total_limit=prompt_budget.total_limit,
        )
    elif decision.route is AutomaticContextRoute.STRUCTURE:
        prompt_budget = ContextBudget(
            active_task_checkpoint=0,
            episodic_memories=0,
            knowledge=0,
            structural=1_000,
            skills_and_procedures=0,
            provenance_and_conflicts=300,
            total_limit=prompt_budget.total_limit,
        )

    query_prompt = _automatic_route_query(prompt, decision)
    semantic = None
    if decision.route is AutomaticContextRoute.KNOWLEDGE and not (
        contains_high_confidence_secret(prompt, query_prompt)
    ):
        semantic = LocalSemanticKnowledgeRetriever(
            runtime.knowledge_document_repository,
            FastEmbedLocalProvider(data_directory / "semantic-model-cache"),
        )
    service = _automatic_prompt_context_service(
        runtime,
        semantic,
        include_semantic_memory=experimental_semantic_memory_enabled,
    )
    request = _automatic_prompt_context_request(
        scope,
        query_prompt,
        prompt_budget,
        decision,
        include_semantic=semantic is not None,
    )
    try:
        return service.get_context(request)
    except LocalEmbeddingError:
        return _automatic_prompt_context_service(
            runtime,
            None,
            include_semantic_memory=experimental_semantic_memory_enabled,
        ).get_context(
            _automatic_prompt_context_request(
                scope,
                query_prompt,
                prompt_budget,
                decision,
                include_semantic=False,
            )
        )


def _automatic_prompt_context_for_route(
    data_directory: Path,
    scope: MemoryScope,
    prompt: str,
    decision: AutomaticContextRouteDecision,
    *,
    experimental_semantic_memory_enabled: bool = False,
) -> _AutomaticPromptContextResult:
    """Fetch one route chosen after a typed answer: unfiltered, under the same ceilings."""

    started = monotonic()
    prompt = bounded_automatic_context_prompt(prompt)
    try:
        with build_checkpoint_runtime(
            resolve_local_config(data_directory), dbt_parser=DbtManifestParser()
        ) as runtime:
            packet = _fetch_route_packet(
                runtime,
                data_directory,
                scope,
                prompt,
                decision,
                experimental_semantic_memory_enabled=experimental_semantic_memory_enabled,
            )
    except (CheckpointApplicationError, OSError, ValueError, RuntimeError):
        return _AutomaticPromptContextResult(
            decision, None, (), _elapsed_milliseconds(started), failed=True
        )
    if not _packet_has_automatic_context(packet):
        return _AutomaticPromptContextResult(decision, None, (), _elapsed_milliseconds(started))
    return _AutomaticPromptContextResult(decision, packet, (), _elapsed_milliseconds(started))
```

- [ ] **Step 6: Extract the learned phrases**

Replace `_automatic_shadow_trace` with:

```python
def _learned_route_phrases(
    data_directory: Path, scope: MemoryScope
) -> tuple[LearnedRoutePhrase, ...]:
    project_scope = MemoryScope(
        scope.owner_id,
        ScopeLevel.PROJECT,
        scope.visibility,
        scope.workspace_id,
        scope.project_id,
    )
    try:
        return tuple(
            record.routing_phrase()
            for record in LocalLearnedRouteStore(data_directory).records(project_scope)
        )
    except (LearnedRouteStoreError, OSError, TypeError, ValueError):
        return ()


def _automatic_shadow_trace(
    data_directory: Path, scope: MemoryScope, prompt: str
) -> _AutomaticShadowTrace:
    """Evaluate the deterministic shadow planner without loading a model in the hook path."""

    started = monotonic()
    learned = _learned_route_phrases(data_directory, scope)
    try:
        plan = plan_automatic_context_needs(prompt, learned_phrases=learned)
    except (OSError, RuntimeError, TypeError, ValueError):
        plan = plan_automatic_context_needs(prompt)
    return _AutomaticShadowTrace(plan, _elapsed_milliseconds(started))
```

- [ ] **Step 7: Restructure `_automatic_prompt_context_for_hook`**

Replace the function's signature and everything up to (not including) the line `delivery_keys = _automatic_prompt_delivery_keys(` with:

```python
def _automatic_prompt_context_for_hook(
    data_directory: Path,
    scope: MemoryScope,
    prompt: str,
    client: ClientName,
    *,
    replay_overrides: TypedHookOverrides | None = None,
) -> PromptContextAttachment:
    """Render one selected route and persist only content-free cost metadata.

    ``replay_overrides`` exists for the synthetic replay only. The ``automatic-memory-hook``
    command never passes it, so the real hook always uses the runtime guard and the locked
    settings (spec 2026-10-02 §3).
    """

    try:
        settings = PersonalSettingsStore(data_directory).load()
    except (OSError, TypeError, ValueError):
        settings = PersonalSettings()
    experimental_live_gate = settings.experimental_semantic_memory_enabled

    trace = (
        _automatic_shadow_trace(data_directory, scope, prompt) if experimental_live_gate else None
    )
    render = _rules_prompt_render(
        data_directory, scope, prompt, client, trace, experimental_live_gate=experimental_live_gate
    )
    modes = _typed_modes(settings, replay_overrides)
    if modes is not None:
        render, trace = _typed_prompt_render(
            data_directory, scope, prompt, client, settings, modes, trace, render, replay_overrides
        )
    result = render.result
    rendered = render.rendered
    canonical_tokens = render.canonical_tokens
    live_attachment = render.live_attachment

```

The rest of the function (from `delivery_keys = ...` to the final `return`) is unchanged.

- [ ] **Step 8: Add the rules render and the typed path**

Directly after `_automatic_prompt_context_for_hook` add:

```python
@dataclass(frozen=True, slots=True)
class _PromptRender:
    result: _AutomaticPromptContextResult
    rendered: str | None
    canonical_tokens: int
    live_attachment: AutomaticContextLiveAttachment | None


def _rules_prompt_render(
    data_directory: Path,
    scope: MemoryScope,
    prompt: str,
    client: ClientName,
    trace: _AutomaticShadowTrace | None,
    *,
    experimental_live_gate: bool,
) -> _PromptRender:
    """Today's route selection and render, with the ADR 0046 live gate when traced."""

    if trace is not None and trace.plan.action in {
        AutomaticContextShadowAction.NONE,
        AutomaticContextShadowAction.LAZY_PULL,
    }:
        return _suppressed_render(prompt, trace)
    result = _automatic_prompt_context_result(
        data_directory,
        scope,
        prompt,
        client,
        experimental_semantic_memory_enabled=experimental_live_gate,
    )
    return _render_selected_result(result, client, trace)


def _suppressed_render(prompt: str, trace: _AutomaticShadowTrace) -> _PromptRender:
    started = monotonic()
    decision = choose_automatic_context_route(bounded_automatic_context_prompt(prompt))
    result = _AutomaticPromptContextResult(decision, None, (), _elapsed_milliseconds(started))
    live_attachment = gate_automatic_context_injection(trace.plan, lambda: None)
    return _PromptRender(result, live_attachment.context, 0, live_attachment)


def _render_selected_result(
    result: _AutomaticPromptContextResult,
    client: ClientName,
    trace: _AutomaticShadowTrace | None,
) -> _PromptRender:
    maximum_tokens = result.decision.maximum_attachment_tokens
    if trace is not None:
        maximum_tokens = min(maximum_tokens, trace.plan.estimated_attachment_tokens)
    rendered, canonical_tokens = _render_automatic_prompt_result(result, client, maximum_tokens)
    live_attachment = (
        None if trace is None else gate_automatic_context_injection(trace.plan, lambda: rendered)
    )
    if live_attachment is not None:
        rendered = live_attachment.context
    return _PromptRender(result, rendered, canonical_tokens, live_attachment)


def _typed_modes(
    settings: PersonalSettings, overrides: TypedHookOverrides | None
) -> TypedHookModes | None:
    """Active hook modes, or ``None`` for today's path (nothing typed is imported then)."""

    if overrides is None and not settings.experimental_typed_decisions_enabled:
        return None
    from mnemo_memory.apps.cli.typed_decision_hook import typed_hook_modes

    modes = typed_hook_modes(settings) if overrides is None else overrides.modes
    return modes if modes.any_on else None


@dataclass(frozen=True, slots=True)
class _TypedLocalInputs:
    skills: tuple[ProjectSkill, ...]
    skills_over_limit: bool
    keyword_candidates: tuple[SkillDiscoveryCandidate, ...]
    pinned_item_ids: frozenset[str]


def _typed_local_inputs(
    data_directory: Path,
    scope: MemoryScope,
    prompt: str,
    client: ClientName,
    packet: ContextPacket | None,
    *,
    list_skills: bool,
) -> _TypedLocalInputs:
    """Local preparation for the typed step (spec §3 step 2): metadata only, nothing sent."""

    project_scope = MemoryScope(
        scope.owner_id,
        ScopeLevel.PROJECT,
        scope.visibility,
        scope.workspace_id,
        scope.project_id,
    )
    with build_checkpoint_runtime(resolve_local_config(data_directory)) as runtime:
        if runtime.knowledge_document_repository is None:
            raise RuntimeError("knowledge repository is unavailable")
        registry = KnowledgeDocumentSkillRegistry(runtime.knowledge_document_repository)
        keyword = registry.discover_current_skills(project_scope, prompt, client)
        listing = (
            registry.current_skill_listing(project_scope, client)
            if list_skills
            else CurrentSkillListing((), False)
        )
        pinned = _pinned_approved_item_ids(runtime, packet)
    return _TypedLocalInputs(listing.skills, listing.more_than_limit, keyword, pinned)


def _pinned_approved_item_ids(
    runtime: CheckpointRuntime, packet: ContextPacket | None
) -> frozenset[str]:
    """Pinned approved events in ``packet``; an unreadable pin state counts as pinned."""

    if packet is None:
        return frozenset()
    pinned: set[str] = set()
    for item in packet.episodic_memories:
        if not item.item_id.startswith("approved-episodic:"):
            continue
        try:
            record = runtime.repository.get_approved_event_record(
                item.source_scope,
                EventId.from_string(item.item_id.removeprefix("approved-episodic:")),
            )
        except Exception:
            pinned.add(item.item_id)  # keep is the safe side
            continue
        if record.pinned:
            pinned.add(item.item_id)
    return frozenset(pinned)


def _runtime_guard_factory(
    settings: PersonalSettings, data_directory: Path
) -> Callable[[TypedDecisionRecorder], GuardedTypedDecisionClassifier | None]:
    from mnemo_memory.apps.cli.typed_decision_composition import (
        build_runtime_typed_decision_classifier,
    )

    def build(recorder: TypedDecisionRecorder) -> GuardedTypedDecisionClassifier | None:
        return build_runtime_typed_decision_classifier(
            settings, data_directory=data_directory, recorder=recorder
        )

    return build


@dataclass(frozen=True, slots=True)
class _TypedApplication:
    render: _PromptRender
    trace: _AutomaticShadowTrace | None
    notes_dropped: int


def _typed_prompt_render(
    data_directory: Path,
    scope: MemoryScope,
    prompt: str,
    client: ClientName,
    settings: PersonalSettings,
    modes: TypedHookModes,
    trace: _AutomaticShadowTrace | None,
    rules: _PromptRender,
    overrides: TypedHookOverrides | None,
) -> tuple[_PromptRender, _AutomaticShadowTrace | None]:
    """Run the typed step on top of today's result; any exception keeps today's result."""

    from mnemo_memory.apps.cli import typed_decision_hook as typed

    started = monotonic()
    try:
        bounded = bounded_automatic_context_prompt(prompt)
        learned = _learned_route_phrases(data_directory, scope)
        rules_plan = (
            trace.plan
            if trace is not None
            else plan_automatic_context_needs(bounded, learned_phrases=learned)
        )
        local = _typed_local_inputs(
            data_directory,
            scope,
            bounded,
            client,
            rules.result.packet,
            list_skills=modes.skill is not TypedDecisionMode.OFF,
        )
        step = typed.TypedStepInput(
            prompt=bounded,
            modes=modes,
            hard_rule=rules_plan.hard_rule,
            rules_plan=rules_plan,
            rules_route=choose_automatic_context_route(
                bounded, skill_candidate_count=len(local.keyword_candidates)
            ).route,
            learned_phrases=learned,
            skill_names=tuple(skill.name for skill in local.skills),
            skills_over_limit=local.skills_over_limit,
            keyword_skill_names=tuple(
                candidate.skill.name for candidate in local.keyword_candidates
            ),
            filler_candidates=(
                typed.filler_candidates(
                    rules.result.packet, pinned_item_ids=local.pinned_item_ids
                )
                if modes.relevance is not TypedDecisionMode.OFF and rules.result.packet is not None
                else ()
            ),
        )
        guard_factory = (
            overrides.guard_factory
            if overrides is not None
            else _runtime_guard_factory(settings, data_directory)
        )
        decisions = typed.decide_typed_prompt(guard_factory, step, started=started)
        if overrides is not None and overrides.observer is not None:
            with suppress(Exception):
                overrides.observer(step, decisions)
        applied = _apply_typed_decisions(
            data_directory, scope, prompt, client, trace, rules, decisions, local, step
        )
    except Exception:
        return rules, trace
    return applied.render, applied.trace


def _apply_typed_decisions(
    data_directory: Path,
    scope: MemoryScope,
    prompt: str,
    client: ClientName,
    trace: _AutomaticShadowTrace | None,
    rules: _PromptRender,
    decisions: TypedPromptDecisions,
    local: _TypedLocalInputs,
    step: TypedStepInput,
) -> _TypedApplication:
    """Apply live answers on top of today's result (spec §4); shadow answers change nothing."""

    from mnemo_memory.apps.cli import typed_decision_hook as typed

    gate_trace = trace
    memory_live = False
    if decisions.typed_needs is not None and trace is not None:
        memory_live = True
        typed_plan = plan_automatic_context_needs(
            step.prompt, learned_phrases=step.learned_phrases, typed_needs=decisions.typed_needs
        )
        gate_trace = _AutomaticShadowTrace(typed_plan, trace.shadow_duration_ms)
    render = rules
    if memory_live or decisions.skill is not None:
        render = _typed_selected_render(
            data_directory,
            scope,
            prompt,
            client,
            gate_trace,
            rules,
            _effective_skill_candidates(local, decisions.skill, client),
            decisions.memory_route if memory_live else None,
        )
    dropped = 0
    if decisions.drop_item_ids and render.result is rules.result:
        render, dropped = _with_filler_drops(render, client, gate_trace, decisions.drop_item_ids)
    if decisions.show_hint:
        render = _with_rendered(render, typed.with_task_size_hint(render.rendered))
    return _TypedApplication(render, gate_trace, dropped)


def _typed_selected_render(
    data_directory: Path,
    scope: MemoryScope,
    prompt: str,
    client: ClientName,
    trace: _AutomaticShadowTrace | None,
    rules: _PromptRender,
    candidates: tuple[SkillDiscoveryCandidate, ...],
    memory_route: AutomaticContextRoute | None,
) -> _PromptRender:
    """Re-select the route after live answers; new retrieval runs unfiltered (§4.1, §4.4)."""

    bounded = bounded_automatic_context_prompt(prompt)
    if trace is not None and trace.plan.action in {
        AutomaticContextShadowAction.NONE,
        AutomaticContextShadowAction.LAZY_PULL,
    }:
        return _suppressed_render(prompt, trace)
    if choose_automatic_context_route(bounded).route in {
        AutomaticContextRoute.NONE,
        AutomaticContextRoute.DIRECT_LOOKUP,
        AutomaticContextRoute.LOCAL_DIAGNOSTICS,
    }:
        return rules  # Jev never overrides a hard route
    decision = choose_automatic_context_route(bounded, skill_candidate_count=len(candidates))
    if decision.route is AutomaticContextRoute.SKILL_DISCOVERY:
        return _render_selected_result(
            _AutomaticPromptContextResult(decision, None, candidates, 0), client, trace
        )
    if memory_route is not None:
        decision = typed_route_decision(memory_route)
    if decision.route is rules.result.decision.route and rules.result.packet is not None:
        return _render_selected_result(rules.result, client, trace)
    result = _automatic_prompt_context_for_route(
        data_directory,
        scope,
        prompt,
        decision,
        experimental_semantic_memory_enabled=trace is not None,
    )
    return _render_selected_result(result, client, trace)


def _effective_skill_candidates(
    local: _TypedLocalInputs, accepted: str | None, client: ClientName
) -> tuple[SkillDiscoveryCandidate, ...]:
    """Live skill pick: one named skill, none, or (unsure) today's keyword candidates."""

    from mnemo_memory.packages.model_gateway.decision_axes import SKILL_PICK_NONE

    if accepted is None:
        return local.keyword_candidates
    if accepted == SKILL_PICK_NONE:
        return ()
    selected = next((skill for skill in local.skills if skill.name == accepted), None)
    if selected is None:
        return local.keyword_candidates  # the skill vanished since listing: keep keywords
    return (SkillDiscoveryCandidate(selected, normalize_agent_client(client), 0),)


def _with_filler_drops(
    render: _PromptRender,
    client: ClientName,
    trace: _AutomaticShadowTrace | None,
    drop_item_ids: tuple[str, ...],
) -> tuple[_PromptRender, int]:
    """Drop judged filler, one ``lower_rank`` omission per note (spec §5).

    A note whose omission line does not fit the attachment budget is kept: its drop is
    cancelled and the remaining drops are rendered again, until every remaining line fits.
    The drop set shrinks every round, so this ends after at most 16 renders.
    """

    from mnemo_memory.apps.cli import typed_decision_hook as typed

    packet = render.result.packet
    if packet is None:
        return render, 0
    drops = drop_item_ids
    while drops:
        reduced, notices = typed.without_filler_notes(packet, drops)
        if not notices:
            return render, 0
        candidate = _render_selected_result(replace(render.result, packet=reduced), client, trace)
        lines = set((candidate.rendered or "").split("\n"))
        missing = {
            notice.item_id for notice in notices if typed.omission_line(notice) not in lines
        }
        if not missing:
            return candidate, len(notices)
        drops = tuple(notice.item_id for notice in notices if notice.item_id not in missing)
    return render, 0


def _with_rendered(render: _PromptRender, rendered: str) -> _PromptRender:
    attachment = render.live_attachment
    if attachment is not None:
        attachment = replace(
            attachment, context=rendered, injected_context_tokens=(len(rendered) + 3) // 4
        )
    return replace(render, rendered=rendered, live_attachment=attachment)
```

- [ ] **Step 9: Run the new and the existing hook tests**

Run: `uv run pytest tests/unit/test_typed_hook_integration.py tests/unit/test_automatic_memory.py tests/unit/test_context_route_telemetry.py -q`
Expected: PASS. The existing automatic-memory tests prove the extracted rules path is unchanged.

- [ ] **Step 10: Lint, type-check, architecture and commit**

Run: `uv run ruff format src scripts tests/unit && uv run ruff check src scripts tests && uv run mypy && npm run -s architecture:check && uv run pytest tests/architecture -q`
Expected: no errors; the Jev-connector importer list is still only the composition module.

```bash
git commit -m "feat(hook): wire typed decisions into the prompt hook with shadow and live modes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- \
  src/mnemo_memory/apps/cli/main.py scripts/typed_decision_test_support.py \
  tests/unit/test_typed_hook_integration.py
```

---
### Task 10: Telemetry `typed_v1`

**Files:**
- Modify: `src/mnemo_memory/packages/telemetry/automatic_routes.py`: imports (`:5-16`), constants (`:18-34`), new dataclass before `AutomaticRouteEvent` (`:145`), `AutomaticRouteEvent` field list (`:163-180`), `__post_init__` (`:182-302`), `to_dict` (`:365-412`), `from_dict` (`:415-522`), helpers at the end (`:891-906`)
- Modify: `src/mnemo_memory/packages/telemetry/__init__.py`
- Modify: `src/mnemo_memory/apps/cli/typed_decision_hook.py` (add `route_telemetry`)
- Modify: `src/mnemo_memory/apps/cli/main.py` (`_typed_prompt_render` return value, the event construction in `_automatic_prompt_context_for_hook`)
- Test: `tests/unit/test_context_route_telemetry.py`, `tests/unit/test_typed_hook_integration.py`

**Interfaces:**
- Consumes: Task 7 `TypedTelemetryValues`, `typed_step_error_decisions`; Task 9 `_typed_prompt_render`, `_TypedApplication.notes_dropped`.
- Produces:
  - `AUTOMATIC_ROUTE_TYPED_V1_FIELDS: tuple[str, ...]` — the 17 flat keys `typed_front_door_mode` … `typed_skill`.
  - `@dataclass(frozen=True, slots=True) class AutomaticRouteTypedDecisions` with exactly the field names and order of `TypedTelemetryValues`; `to_dict() -> dict[str, object]` (keys prefixed `typed_`); `from_dict(value: Mapping[str, object]) -> AutomaticRouteTypedDecisions`. Exported from `mnemo_memory.packages.telemetry`.
  - `AutomaticRouteEvent.typed: AutomaticRouteTypedDecisions | None = None` (new last field). `from_dict` accepts each existing key-set tier with or without the whole `typed_v1` key set; a partial set is rejected.
  - `typed_decision_hook.route_telemetry(values: TypedTelemetryValues) -> AutomaticRouteTypedDecisions`.
  - `_typed_prompt_render(...) -> tuple[_PromptRender, _AutomaticShadowTrace | None, AutomaticRouteTypedDecisions]`.

The record is written only when diagnostics are `summary` or `trace` (the existing gate at `main.py:876-884`), under today's retention (256 events, 1–90 days). `typed_notes_dropped` means "would drop" in shadow and "actually dropped" in live (a note whose omission line did not fit is kept and not counted). `typed_step_ms` is the whole step: local preparation, the Jev requests and the combine.

- [ ] **Step 1: Write the failing telemetry tests**

Append to `tests/unit/test_context_route_telemetry.py` (add `AUTOMATIC_ROUTE_TYPED_V1_FIELDS` and `AutomaticRouteTypedDecisions` to the `mnemo_memory.packages.telemetry` import):

```python
TYPED = AutomaticRouteTypedDecisions(
    front_door_mode="shadow",
    relevance_mode="shadow",
    tier_hint_mode="off",
    skill_mode="shadow",
    front_door_outcome="data_route_blocked",
    step_ms=4,
    model_version=None,
    memory_label="unsure",
    memory_confidence_bucket=None,
    action=None,
    agrees_with_rules=None,
    notes_checked=3,
    notes_dropped=0,
    notes_unanswered=3,
    tier=None,
    hint="none",
    skill="unsure",
)


def _lazy_shadow(seed: int) -> AutomaticRouteEvent:
    return replace(
        _event(seed),
        shadow_structural_need="unknown",
        shadow_long_term_need="unknown",
        shadow_reason="uncertain",
        shadow_shared_maximum_tokens=1_300,
        shadow_action="lazy_pull",
        shadow_estimated_tokens=29,
    )


def test_typed_v1_group_round_trips_with_every_existing_key_tier() -> None:
    plain = replace(_event(1), typed=TYPED)
    shadowed = replace(_lazy_shadow(2), typed=TYPED)
    live = replace(shadowed, live_gate_applied=True, injected_context_tokens=29)
    for event in (plain, shadowed, live):
        encoded = event.to_dict()
        assert set(AUTOMATIC_ROUTE_TYPED_V1_FIELDS) <= set(encoded)
        assert AutomaticRouteEvent.from_dict(encoded) == event
    older = _lazy_shadow(3)
    assert not set(AUTOMATIC_ROUTE_TYPED_V1_FIELDS) & set(older.to_dict())
    assert AutomaticRouteEvent.from_dict(older.to_dict()).typed is None


def test_typed_v1_keys_must_arrive_together() -> None:
    encoded = replace(_event(1), typed=TYPED).to_dict()
    del encoded["typed_skill"]
    with pytest.raises(ValueError, match="automatic route event is invalid"):
        AutomaticRouteEvent.from_dict(encoded)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("front_door_mode", "on"),
        ("front_door_outcome", "What did we decide last week?"),
        ("step_ms", -1),
        ("model_version", "jev-latest"),
        ("memory_label", "private prompt text"),
        ("memory_confidence_bucket", "0.73"),
        ("action", "push_everything"),
        ("agrees_with_rules", "yes"),
        ("notes_checked", 17),
        ("notes_dropped", 4),
        ("tier", "medium"),
        ("hint", "Mnemo: try a subagent"),
        ("skill", "release-notes"),
    ],
)
def test_typed_v1_accepts_only_closed_values(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        AutomaticRouteTypedDecisions.from_dict({**TYPED.to_dict(), f"typed_{field}": value})
```

Append to `tests/unit/test_typed_hook_integration.py` (add these imports: `from mnemo_memory.packages.telemetry import AutomaticRouteEvent, LocalAutomaticRouteTelemetryStore`, and `HookFixture` from `scripts.typed_decision_test_support`):

```python
def _latest_event(fixture: HookFixture) -> AutomaticRouteEvent:
    return LocalAutomaticRouteTelemetryStore(fixture.data).events(
        cli._automatic_route_scope(fixture.binding.checkpoint_scope), limit=1
    )[0]


def test_shadow_writes_the_typed_v1_group(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    shadow = synthetic_overrides(
        fixture, ScriptedJevTransport(EVERYTHING), TypedHookModes(SHADOW, SHADOW, SHADOW, SHADOW)
    )
    run_hook(fixture, KNOWLEDGE_PROMPT, shadow)
    typed = _latest_event(fixture).typed
    assert typed is not None
    assert typed.front_door_outcome == "answered"
    assert typed.model_version == "jev-1.13.0"
    assert (typed.memory_label, typed.memory_confidence_bucket, typed.action) == (
        "nothing",
        ">=0.9",
        "none",
    )
    assert typed.agrees_with_rules is False
    assert (typed.notes_checked, typed.notes_dropped, typed.notes_unanswered) == (3, 2, 0)
    assert (typed.tier, typed.hint, typed.skill) == ("light", "would_show", "differs")
    assert 0 <= typed.step_ms <= 10_000


def test_live_typed_plan_is_recorded_as_typed_decision(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    live = synthetic_overrides(
        fixture,
        ScriptedJevTransport({"memory_need": ("project_docs", 0.95)}),
        TypedHookModes(front_door=LIVE),
    )
    run_hook(fixture, LAZY_PROMPT, live)
    event = _latest_event(fixture)
    assert event.shadow_reason == "typed_decision"
    assert event.shadow_action == "push_long_term"
    assert event.typed is not None and event.typed.agrees_with_rules is False


def test_live_notes_dropped_counts_only_drops_that_happened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    live = synthetic_overrides(fixture, ScriptedJevTransport(), TypedHookModes(relevance=LIVE))
    run_hook(fixture, KNOWLEDGE_PROMPT, live)
    applied = _latest_event(fixture).typed
    assert applied is not None and applied.notes_dropped == 2

    def oversized(item_id: str) -> OmissionNotice:
        return OmissionNotice(item_id, OmissionReason.LOWER_RANK, "x" * 6_000)

    monkeypatch.setattr(typed_decision_hook, "filler_omission", oversized)
    run_hook(fixture, KNOWLEDGE_PROMPT, live)
    cancelled = _latest_event(fixture).typed
    assert cancelled is not None and cancelled.notes_dropped == 0


def test_a_step_error_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic local failure")

    monkeypatch.setattr(cli, "_typed_local_inputs", broken)
    run_hook(
        fixture,
        KNOWLEDGE_PROMPT,
        synthetic_overrides(
            fixture, ScriptedJevTransport(EVERYTHING), TypedHookModes(LIVE, LIVE, LIVE, LIVE)
        ),
    )
    typed = _latest_event(fixture).typed
    assert typed is not None and typed.front_door_outcome == "typed_step_error"


def test_hook_telemetry_never_holds_prompt_note_or_skill_text(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    shadow = synthetic_overrides(
        fixture, ScriptedJevTransport(EVERYTHING), TypedHookModes(SHADOW, SHADOW, SHADOW, SHADOW)
    )
    run_hook(fixture, KNOWLEDGE_PROMPT + " private-marker-5d2e", shadow)
    encoded = LocalAutomaticRouteTelemetryStore(fixture.data).path.read_text("utf-8")
    assert "typed_front_door_mode" in encoded
    for marker in ("private-marker-5d2e", "invoice", "ledger", "FILLER", "release-notes", "test-plan"):
        assert marker not in encoded
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/unit/test_context_route_telemetry.py tests/unit/test_typed_hook_integration.py -q`
Expected: FAIL — `ImportError: cannot import name 'AUTOMATIC_ROUTE_TYPED_V1_FIELDS'`.

- [ ] **Step 3: Implement the telemetry group**

In `automatic_routes.py` change `from collections.abc import Iterator` to `from collections.abc import Iterator, Mapping`. After `_SEMANTIC_ROUTES = ...` add:

```python
_TYPED_MODES = frozenset({"off", "shadow", "live"})
_TYPED_UNAVAILABLE_REASONS = frozenset(
    {
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
)
_TYPED_FRONT_DOOR_OUTCOMES = _TYPED_UNAVAILABLE_REASONS | {
    "answered",
    "not_asked",
    "typed_step_error",
}
_TYPED_MEMORY_LABELS = frozenset(
    {"past_sessions", "project_docs", "code_structure", "code_and_history", "nothing", "unsure"}
)
_TYPED_CONFIDENCE_BUCKETS = frozenset({"<0.5", "0.5-0.6", "0.6-0.8", "0.8-0.9", ">=0.9"})
_TYPED_TIERS = frozenset({"light", "heavy"})
_TYPED_HINTS = frozenset({"shown", "would_show", "none"})
_TYPED_SKILL_OUTCOMES = frozenset({"agreed", "differs", "unsure", "skipped", "not_asked"})
_TYPED_MODEL_VERSION = re.compile(r"jev-[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}\Z")
_MAXIMUM_TYPED_NOTES = 16
AUTOMATIC_ROUTE_TYPED_V1_FIELDS: tuple[str, ...] = (
    "typed_front_door_mode",
    "typed_relevance_mode",
    "typed_tier_hint_mode",
    "typed_skill_mode",
    "typed_front_door_outcome",
    "typed_step_ms",
    "typed_model_version",
    "typed_memory_label",
    "typed_memory_confidence_bucket",
    "typed_action",
    "typed_agrees_with_rules",
    "typed_notes_checked",
    "typed_notes_dropped",
    "typed_notes_unanswered",
    "typed_tier",
    "typed_hint",
    "typed_skill",
)
```

Directly before `class AutomaticRouteEvent` add:

```python
@dataclass(frozen=True, slots=True)
class AutomaticRouteTypedDecisions:
    """Content-free ``typed_v1`` group for one prompt (spec 2026-10-02 §7): closed values only."""

    front_door_mode: str
    relevance_mode: str
    tier_hint_mode: str
    skill_mode: str
    front_door_outcome: str
    step_ms: int
    model_version: str | None
    memory_label: str | None
    memory_confidence_bucket: str | None
    action: str | None
    agrees_with_rules: bool | None
    notes_checked: int
    notes_dropped: int
    notes_unanswered: int
    tier: str | None
    hint: str
    skill: str

    def __post_init__(self) -> None:
        modes = (self.front_door_mode, self.relevance_mode, self.tier_hint_mode, self.skill_mode)
        if any(mode not in _TYPED_MODES for mode in modes):
            raise ValueError("automatic route typed mode is invalid")
        if self.front_door_outcome not in _TYPED_FRONT_DOOR_OUTCOMES:
            raise ValueError("automatic route typed outcome is invalid")
        if not _bounded_integer(self.step_ms, 10_000_000):
            raise ValueError("automatic route typed duration is invalid")
        if self.model_version is not None and (
            not isinstance(self.model_version, str)
            or _TYPED_MODEL_VERSION.fullmatch(self.model_version) is None
        ):
            raise ValueError("automatic route typed model version is invalid")
        if self.memory_label is not None and self.memory_label not in _TYPED_MEMORY_LABELS:
            raise ValueError("automatic route typed memory label is invalid")
        if (
            self.memory_confidence_bucket is not None
            and self.memory_confidence_bucket not in _TYPED_CONFIDENCE_BUCKETS
        ):
            raise ValueError("automatic route typed confidence bucket is invalid")
        if self.action is not None and self.action not in _SHADOW_ACTIONS:
            raise ValueError("automatic route typed action is invalid")
        if self.agrees_with_rules is not None and not isinstance(self.agrees_with_rules, bool):
            raise ValueError("automatic route typed agreement is invalid")
        counts = (self.notes_checked, self.notes_dropped, self.notes_unanswered)
        if (
            not all(_bounded_integer(value, _MAXIMUM_TYPED_NOTES) for value in counts)
            or self.notes_dropped + self.notes_unanswered > self.notes_checked
        ):
            raise ValueError("automatic route typed note counts are invalid")
        if self.tier is not None and self.tier not in _TYPED_TIERS:
            raise ValueError("automatic route typed tier is invalid")
        if self.hint not in _TYPED_HINTS:
            raise ValueError("automatic route typed hint is invalid")
        if self.skill not in _TYPED_SKILL_OUTCOMES:
            raise ValueError("automatic route typed skill outcome is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "typed_front_door_mode": self.front_door_mode,
            "typed_relevance_mode": self.relevance_mode,
            "typed_tier_hint_mode": self.tier_hint_mode,
            "typed_skill_mode": self.skill_mode,
            "typed_front_door_outcome": self.front_door_outcome,
            "typed_step_ms": self.step_ms,
            "typed_model_version": self.model_version,
            "typed_memory_label": self.memory_label,
            "typed_memory_confidence_bucket": self.memory_confidence_bucket,
            "typed_action": self.action,
            "typed_agrees_with_rules": self.agrees_with_rules,
            "typed_notes_checked": self.notes_checked,
            "typed_notes_dropped": self.notes_dropped,
            "typed_notes_unanswered": self.notes_unanswered,
            "typed_tier": self.tier,
            "typed_hint": self.hint,
            "typed_skill": self.skill,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> AutomaticRouteTypedDecisions:
        return cls(
            front_door_mode=_string(value["typed_front_door_mode"]),
            relevance_mode=_string(value["typed_relevance_mode"]),
            tier_hint_mode=_string(value["typed_tier_hint_mode"]),
            skill_mode=_string(value["typed_skill_mode"]),
            front_door_outcome=_string(value["typed_front_door_outcome"]),
            step_ms=_integer(value["typed_step_ms"]),
            model_version=_optional_string(value["typed_model_version"]),
            memory_label=_optional_string(value["typed_memory_label"]),
            memory_confidence_bucket=_optional_string(value["typed_memory_confidence_bucket"]),
            action=_optional_string(value["typed_action"]),
            agrees_with_rules=_optional_boolean(value["typed_agrees_with_rules"]),
            notes_checked=_integer(value["typed_notes_checked"]),
            notes_dropped=_integer(value["typed_notes_dropped"]),
            notes_unanswered=_integer(value["typed_notes_unanswered"]),
            tier=_optional_string(value["typed_tier"]),
            hint=_string(value["typed_hint"]),
            skill=_string(value["typed_skill"]),
        )
```

In `AutomaticRouteEvent` add the last field:

```python
    injected_context_tokens: int = 0
    typed: AutomaticRouteTypedDecisions | None = None
```

At the end of `AutomaticRouteEvent.__post_init__` add:

```python
        if self.typed is not None and not isinstance(self.typed, AutomaticRouteTypedDecisions):
            raise TypeError("automatic route typed decisions are invalid")
```

At the end of `to_dict`, before `return value`, add:

```python
        if self.typed is not None:
            value.update(self.typed.to_dict())
```

In `from_dict`, replace

```python
        if not isinstance(value, dict) or frozenset(value) not in {
            frozenset(required),
            frozenset(required | shadow),
            frozenset(required | shadow_v2),
            frozenset(required | shadow_v2 | live_gate),
        }:
            raise ValueError("automatic route event is invalid")
```

with

```python
        typed_v1 = frozenset(AUTOMATIC_ROUTE_TYPED_V1_FIELDS)
        if not isinstance(value, dict):
            raise ValueError("automatic route event is invalid")
        keys = frozenset(value)
        has_typed = bool(keys & typed_v1)
        if (has_typed and not typed_v1 <= keys) or keys - typed_v1 not in {
            frozenset(required),
            frozenset(required | shadow),
            frozenset(required | shadow_v2),
            frozenset(required | shadow_v2 | live_gate),
        }:
            raise ValueError("automatic route event is invalid")
```

and add as the last argument of the final `cls(...)` call:

```python
            typed=AutomaticRouteTypedDecisions.from_dict(value) if has_typed else None,
```

Append to the helpers at the end of the module:

```python
def _optional_string(value: object) -> str | None:
    return None if value is None else _string(value)


def _optional_boolean(value: object) -> bool | None:
    return None if value is None else _boolean(value)


def _bounded_integer(value: object, maximum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum
```

In `packages/telemetry/__init__.py`, import and export `AUTOMATIC_ROUTE_TYPED_V1_FIELDS` and `AutomaticRouteTypedDecisions` from `.automatic_routes`; in `__all__` put `"AUTOMATIC_ROUTE_TYPED_V1_FIELDS"` first and `"AutomaticRouteTypedDecisions"` directly after `"AutomaticRouteToolCategory"`.

- [ ] **Step 4: Convert and write the values from the hook**

In `typed_decision_hook.py` change `from dataclasses import dataclass, replace` to `from dataclasses import asdict, dataclass, replace`, add `from mnemo_memory.packages.telemetry import AutomaticRouteTypedDecisions`, and append:

```python
def route_telemetry(values: TypedTelemetryValues) -> AutomaticRouteTypedDecisions:
    """Validate the plain values as the persisted, closed-value ``typed_v1`` group."""

    return AutomaticRouteTypedDecisions(**asdict(values))
```

In `main.py` add `AutomaticRouteTypedDecisions,` to the `mnemo_memory.packages.telemetry` import list. Replace the end of `_typed_prompt_render` — its return annotation, and everything from `applied = _apply_typed_decisions(` to the function's last line — with:

```python
) -> tuple[_PromptRender, _AutomaticShadowTrace | None, AutomaticRouteTypedDecisions]:
```

```python
        applied = _apply_typed_decisions(
            data_directory, scope, prompt, client, trace, rules, decisions, local, step
        )
        values = decisions.telemetry
        if modes.relevance is TypedDecisionMode.LIVE:
            values = replace(values, notes_dropped=applied.notes_dropped)
        telemetry = typed.route_telemetry(replace(values, step_ms=_elapsed_milliseconds(started)))
    except Exception:
        failed = typed.typed_step_error_decisions(modes, _elapsed_milliseconds(started))
        return rules, trace, typed.route_telemetry(failed.telemetry)
    return applied.render, applied.trace, telemetry
```

In `_automatic_prompt_context_for_hook` replace

```python
    modes = _typed_modes(settings, replay_overrides)
    if modes is not None:
        render, trace = _typed_prompt_render(
            data_directory, scope, prompt, client, settings, modes, trace, render, replay_overrides
        )
```

with

```python
    typed_telemetry: AutomaticRouteTypedDecisions | None = None
    modes = _typed_modes(settings, replay_overrides)
    if modes is not None:
        render, trace, typed_telemetry = _typed_prompt_render(
            data_directory, scope, prompt, client, settings, modes, trace, render, replay_overrides
        )
```

and in the `AutomaticRouteEvent(...)` construction add after the `injected_context_tokens=(...)` argument:

```python
        typed=typed_telemetry,
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_context_route_telemetry.py tests/unit/test_typed_hook_integration.py tests/unit/test_automatic_memory.py -q`
Expected: PASS.

- [ ] **Step 6: Lint, type-check, architecture and commit**

Run: `uv run ruff format src tests/unit && uv run ruff check src tests && uv run mypy && npm run -s architecture:check`
Expected: no errors (telemetry still imports nothing internal).

```bash
git commit -m "feat(telemetry): content-free typed_v1 group on automatic route events

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- \
  src/mnemo_memory/packages/telemetry/automatic_routes.py \
  src/mnemo_memory/packages/telemetry/__init__.py \
  src/mnemo_memory/apps/cli/typed_decision_hook.py src/mnemo_memory/apps/cli/main.py \
  tests/unit/test_context_route_telemetry.py tests/unit/test_typed_hook_integration.py
```

---

### Task 11: `typed-decisions status` and `typed-decisions set`

**Files:**
- Modify: `src/mnemo_memory/apps/cli/main.py`: imports; a new Typer sub-app next to `memory_route_diagnostics_app` (`:293-296`) and its `app.add_typer` call after `app.add_typer(memory_app, ...)` (`:301`); two commands placed after `memory_route_diagnostics_status` (`:3276-3285`)
- Test: `tests/unit/test_typed_decisions_cli.py` (new)

**Interfaces:**
- Consumes: Task 1 `active_typed_decision_locks`, `with_typed_decision_mode`, `TYPED_DECISION_LOCK_MESSAGES`, `TypedDecisionLock`, `PersonalSettings.typed_decision_daily_input_tokens`; Task 2 `LocalDailyModelBudget.reserved_today()`.
- Produces: CLI commands `mnemo-memory typed-decisions status [--data-dir]` and `mnemo-memory typed-decisions set <front_door|relevance|tier_hint|skill> <off|shadow|live> [--data-dir]`. Both print one JSON object (`_show`). A lock refusal prints `{"status": "refused", "kind", "mode", "reason"}` and exits 1; an unreadable settings file prints `{"status": "settings_invalid", "reason"}` and exits 1; a bad kind or mode is a Typer usage error (exit 2). The key is reported only as `credential_present: true|false`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_typed_decisions_cli.py`:

```python
"""The typed-decisions CLI shows switches, locks and budget, and refuses locked modes."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner, Result

from mnemo_memory.apps.cli.main import app
from mnemo_memory.packages.application import PersonalSettings, PersonalSettingsStore
from mnemo_memory.packages.application.settings import (
    TYPED_DECISION_LOCK_MESSAGES,
    TypedDecisionLock,
)

FAKE_KEY = "test-key-not-real-0000"
runner = CliRunner()


def _invoke(data: Path, *args: str, key: str | None = None) -> Result:
    return runner.invoke(
        app,
        ["typed-decisions", *args, "--data-dir", str(data)],
        env={"TYPESAFE_API_KEY": key},
    )


def test_status_reports_switches_locks_budget_and_key_presence_only(tmp_path: Path) -> None:
    result = _invoke(tmp_path, "status")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "status": "available",
        "master_switch": False,
        "data_route": "synthetic_only",
        "model_id": "jev-1.13.0",
        "modes": {"front_door": "off", "relevance": "off", "tier_hint": "off", "skill": "off"},
        "locks": ["master_switch", "semantic_memory_gate", "data_route"],
        "credential_present": False,
        "daily_input_tokens": {
            "counter": "available",
            "limit": 10_000_000,
            "reserved_today": 0,
        },
        "sends_real_prompts": False,
    }
    with_key = _invoke(tmp_path, "status", key=FAKE_KEY)
    assert json.loads(with_key.output)["credential_present"] is True
    assert FAKE_KEY not in with_key.output


def test_set_changes_one_mode_and_applies_the_locks(tmp_path: Path) -> None:
    refused = _invoke(tmp_path, "set", "skill", "shadow")
    assert refused.exit_code == 1
    assert json.loads(refused.output)["reason"] == TYPED_DECISION_LOCK_MESSAGES[
        TypedDecisionLock.MASTER_SWITCH
    ]
    PersonalSettingsStore(tmp_path).save(PersonalSettings(experimental_typed_decisions_enabled=True))

    updated = _invoke(tmp_path, "set", "skill", "shadow")
    assert updated.exit_code == 0, updated.output
    assert json.loads(updated.output) == {"status": "updated", "kind": "skill", "mode": "shadow"}
    assert json.loads(_invoke(tmp_path, "status").output)["modes"]["skill"] == "shadow"

    live = _invoke(tmp_path, "set", "relevance", "live")
    assert live.exit_code == 1
    assert json.loads(live.output)["reason"] == TYPED_DECISION_LOCK_MESSAGES[
        TypedDecisionLock.DATA_ROUTE
    ]
    front = _invoke(tmp_path, "set", "front_door", "live")
    assert json.loads(front.output)["reason"] == TYPED_DECISION_LOCK_MESSAGES[
        TypedDecisionLock.SEMANTIC_MEMORY_GATE
    ]
    assert PersonalSettingsStore(tmp_path).load().typed_decision_modes == (("skill", "shadow"),)

    assert _invoke(tmp_path, "set", "verify", "shadow").exit_code == 2
    assert _invoke(tmp_path, "set", "skill", "on").exit_code == 2


def test_status_reports_a_locked_settings_file_in_plain_words(tmp_path: Path) -> None:
    PersonalSettingsStore(tmp_path).save(PersonalSettings(experimental_typed_decisions_enabled=True))
    path = tmp_path / "settings.json"
    stored = json.loads(path.read_text("utf-8"))
    stored["typed_decision_modes"] = {"skill": "live"}
    path.write_text(json.dumps(stored), encoding="utf-8")
    result = _invoke(tmp_path, "status")
    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "status": "settings_invalid",
        "reason": TYPED_DECISION_LOCK_MESSAGES[TypedDecisionLock.DATA_ROUTE],
    }


def test_status_reports_an_unreadable_counter(tmp_path: Path) -> None:
    (tmp_path / "typed_decision-budget.json").write_text("{not json", encoding="utf-8")
    budget = json.loads(_invoke(tmp_path, "status").output)["daily_input_tokens"]
    assert budget == {"counter": "unavailable", "limit": 10_000_000, "reserved_today": None}


def test_help_lists_both_commands() -> None:
    result = runner.invoke(app, ["typed-decisions", "--help"])
    assert result.exit_code == 0
    assert "status" in result.output and "set" in result.output
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/unit/test_typed_decisions_cli.py -q`
Expected: FAIL — exit code 2 with `No such command 'typed-decisions'`.

- [ ] **Step 3: Implement the commands**

Imports in `main.py`: add `ModelTaskType,` and `TypedDecisionKind,` to the `mnemo_memory.packages.domain` list; add `LocalDailyModelBudget,` to the `mnemo_memory.packages.storage` list; add

```python
from mnemo_memory.packages.application.settings import (
    active_typed_decision_locks,
    with_typed_decision_mode,
)
```

After the `memory_route_diagnostics_app = typer.Typer(...)` definition add:

```python
typed_decisions_app = typer.Typer(
    no_args_is_help=True,
    help="Inspect and set Jev typed-decision modes; live stays locked to synthetic data.",
)
```

After `app.add_typer(memory_app, name="memory", ...)` add:

```python
app.add_typer(
    typed_decisions_app,
    name="typed-decisions",
    help="Inspect and set Jev typed-decision modes.",
)
```

After `memory_route_diagnostics_status` add:

```python
_TYPED_DECISION_HOOK_KINDS = ("front_door", "relevance", "tier_hint", "skill")


@typed_decisions_app.command(
    "status", help="Show switches, modes, active locks and today's typed-decision budget."
)
def typed_decisions_status(
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
) -> None:
    try:
        config = resolve_local_config(data_dir)
    except (OSError, ValueError) as error:
        raise typer.BadParameter("MNEMO_TYPED_DECISIONS_UNAVAILABLE") from error
    try:
        settings = PersonalSettingsStore(config.data_directory).load()
    except PersonalSettingsError as error:
        cause = error.__cause__
        reason = str(cause) if isinstance(cause, PersonalSettingsError) else str(error)
        _show({"status": "settings_invalid", "reason": reason})
        raise typer.Exit(1) from error
    reserved = LocalDailyModelBudget(
        config.data_directory,
        task_type=ModelTaskType.TYPED_DECISION,
        daily_input_tokens=settings.typed_decision_daily_input_tokens,
    ).reserved_today()
    _show(
        {
            "status": "available",
            "master_switch": settings.experimental_typed_decisions_enabled,
            "data_route": settings.typed_decision_data_route,
            "model_id": settings.typed_decision_model_id,
            "modes": {
                kind: settings.typed_decision_mode(TypedDecisionKind(kind)).value
                for kind in _TYPED_DECISION_HOOK_KINDS
            },
            "locks": [lock.value for lock in active_typed_decision_locks(settings)],
            # Presence only: the key value is never read into output, logs or settings.
            "credential_present": bool(os.environ.get("TYPESAFE_API_KEY", "").strip()),
            "daily_input_tokens": {
                "counter": "unavailable" if reserved is None else "available",
                "limit": settings.typed_decision_daily_input_tokens,
                "reserved_today": reserved,
            },
            "sends_real_prompts": False,
        }
    )


@typed_decisions_app.command("set", help="Change one hook decision mode; the locks still apply.")
def typed_decisions_set(
    kind: str = typer.Argument(..., help="front_door, relevance, tier_hint or skill"),
    mode: str = typer.Argument(..., help="off, shadow or live"),
    data_dir: Path | None = typer.Option(None, "--data-dir"),  # noqa: B008
) -> None:
    if kind not in _TYPED_DECISION_HOOK_KINDS:
        raise typer.BadParameter("kind must be one of front_door, relevance, tier_hint, skill")
    try:
        target = TypedDecisionMode(mode)
    except ValueError as error:
        raise typer.BadParameter("mode must be one of off, shadow, live") from error
    try:
        config = resolve_local_config(data_dir)
        store = PersonalSettingsStore(config.data_directory)
        current = store.load()
    except (OSError, ValueError) as error:
        raise typer.BadParameter("MNEMO_TYPED_DECISIONS_UNAVAILABLE") from error
    try:
        updated = with_typed_decision_mode(current, TypedDecisionKind(kind), target)
    except PersonalSettingsError as error:
        _show({"status": "refused", "kind": kind, "mode": target.value, "reason": str(error)})
        raise typer.Exit(1) from error
    try:
        store.save(updated)
    except PersonalSettingsError as error:
        raise typer.BadParameter("MNEMO_SETTINGS_WRITE_FAILED") from error
    _show({"status": "updated", "kind": kind, "mode": target.value})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_typed_decisions_cli.py tests/unit/test_potion_router.py -q`
Expected: PASS.

- [ ] **Step 5: Lint, type-check and commit**

Run: `uv run ruff format src tests/unit && uv run ruff check src tests && uv run mypy`
Expected: no errors.

```bash
git commit -m "feat(cli): typed-decisions status and set

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- \
  src/mnemo_memory/apps/cli/main.py tests/unit/test_typed_decisions_cli.py
```

---

### Task 12: Contract and security tests

**Files:**
- Create: `tests/contract/test_typed_hook_contract.py`
- Create: `tests/security/test_typed_hook_network_boundary.py`

**Interfaces:**
- Consumes: `scripts/typed_decision_test_support.py` (Task 9); `build_runtime_typed_decision_classifier` (Task 3); `with_typed_decision_mode` (Task 1); `TypedHookModes`, `TypedHookOverrides`, `HOOK_KINDS` (Task 7); `cli._typed_prompt_render`, `cli._automatic_prompt_context_attachment`; `FILLER_OMISSION_DETAIL` (Task 7); `jev_provider._urllib_transport` (`connectors/typesafe/jev_provider.py:60`).
- Produces: tests only.

Spec §8.1 contracts pinned here:
- **Off is unchanged.** With the master switch off, or on with every mode off, the typed step never runs, so the output is today's code path. The existing hook suite in `tests/unit/test_automatic_memory.py` is the byte-level regression net for that path. Canonical packets keep their exact wire shape.
- **No packet schema change.** The per-note `lower_rank` filler omissions a live hook writes validate against the unchanged `context-packet-v1.json` omission definition.
- **Shadow is unchanged.** Over the 60 routing prompts plus the 40 holdout prompts, with and without the semantic-memory gate, a fake Jev answers every question and the output stays byte-identical to off.
- **Nothing sends while blocked.** The real hook path (no overrides) makes zero transport calls in `off` and `shadow` for every hook kind; settings refuse `live`; and even forced `live` modes with the runtime guard send nothing.
- **The entry point is pinned to runtime.** Nothing under `src/` but the composition module mentions the synthetic builder; no call to `_automatic_prompt_context_for_hook` in `main.py` passes `replay_overrides`; the hook's guard is built with the runtime source.

- [ ] **Step 1: Write the contract tests**

Create `tests/contract/test_typed_hook_contract.py`:

```python
"""Shadow and off produce today's hook output byte for byte (spec 2026-10-02 §8.1)."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import pytest

from mnemo_memory.apps.cli import main as cli
from mnemo_memory.apps.cli.typed_decision_hook import FILLER_OMISSION_DETAIL, TypedHookModes
from mnemo_memory.packages.application import PersonalSettings, PersonalSettingsStore
from mnemo_memory.packages.domain import TypedDecisionMode
from mnemo_memory.packages.telemetry import LocalAutomaticRouteTelemetryStore
from scripts.typed_decision_test_support import (
    KNOWLEDGE_PROMPT,
    ScriptedJevTransport,
    run_hook,
    seed_hook_fixture,
    synthetic_overrides,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "evals"
ROUTING = json.loads((FIXTURES / "automatic-context-routing-v1.json").read_text("utf-8"))
HOLDOUT = json.loads((FIXTURES / "typed-decision-holdout-v1.json").read_text("utf-8"))
PROMPTS: tuple[str, ...] = tuple(case["prompt"] for case in ROUTING["cases"]) + tuple(
    case["prompt"] for case in HOLDOUT["front_door_cases"]
)
SHADOW = TypedDecisionMode.SHADOW
ANSWER_EVERYTHING = {
    "memory_need": ("nothing", 0.95),
    "complexity": ("light", 0.95),
    "tool_need": ("read_heavy", 0.95),
    "skill_pick": ("test-plan", 0.95),
}


@pytest.mark.parametrize("semantic_gate", [False, True])
def test_shadow_output_is_byte_identical_to_off(tmp_path: Path, semantic_gate: bool) -> None:
    assert len(PROMPTS) == 100
    fixture = seed_hook_fixture(tmp_path, semantic_gate=semantic_gate)
    transport = ScriptedJevTransport(ANSWER_EVERYTHING)
    shadow = synthetic_overrides(
        fixture, transport, TypedHookModes(SHADOW, SHADOW, SHADOW, SHADOW)
    )
    for prompt in PROMPTS:
        off = run_hook(fixture, prompt)
        seen = run_hook(fixture, prompt, shadow)
        assert (seen.context, seen.delivery_keys) == (off.context, off.delivery_keys), prompt
    # Every prompt asked at least the skill question, so shadow really received answers.
    assert transport.calls >= len(PROMPTS)
    events = LocalAutomaticRouteTelemetryStore(fixture.data).events(
        cli._automatic_route_scope(fixture.binding.checkpoint_scope), limit=100
    )
    typed = [event.typed for event in events if event.typed is not None]
    assert typed and all(value.front_door_outcome == "answered" for value in typed)


def test_off_never_enters_the_typed_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("the typed step must not run while every mode is off")

    monkeypatch.setattr(cli, "_typed_prompt_render", forbidden)
    for prompt in PROMPTS[::5]:
        run_hook(fixture, prompt)
    PersonalSettingsStore(fixture.data).save(
        PersonalSettings(
            experimental_semantic_memory_enabled=True, experimental_typed_decisions_enabled=True
        )
    )
    for prompt in PROMPTS[::5]:
        run_hook(fixture, prompt)


def test_canonical_packets_keep_their_wire_shape(tmp_path: Path) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    attached = cli._automatic_prompt_context_attachment(
        fixture.data, fixture.binding.checkpoint_scope, KNOWLEDGE_PROMPT
    )
    assert attached is not None
    omissions = json.loads(attached)["omissions"]
    assert omissions
    assert all(set(omission) == {"item_id", "reason", "detail"} for omission in omissions)


def test_live_filler_omission_lines_fit_the_unchanged_v1_schema(tmp_path: Path) -> None:
    schema = json.loads(
        resources.files("mnemo_memory")
        .joinpath("resources/schemas/context-packet-v1.json")
        .read_text(encoding="utf-8")
    )
    definition = schema["$defs"]["omission"]
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    live = synthetic_overrides(
        fixture, ScriptedJevTransport(), TypedHookModes(relevance=TypedDecisionMode.LIVE)
    )
    context = run_hook(fixture, KNOWLEDGE_PROMPT, live).context
    assert context is not None
    omissions = [
        json.loads(line.removeprefix("MNEMO_OMISSION "))
        for line in context.split("\n")
        if line.startswith("MNEMO_OMISSION ")
    ]
    filler = [omission for omission in omissions if omission["detail"] == FILLER_OMISSION_DETAIL]
    assert len(filler) == 2
    assert all(omission["reason"] == "lower_rank" for omission in filler)
    for omission in omissions:
        assert set(omission) == set(definition["required"]) == set(definition["properties"])
        assert omission["reason"] in definition["properties"]["reason"]["enum"]
```

- [ ] **Step 2: Write the security tests**

Create `tests/security/test_typed_hook_network_boundary.py`:

```python
"""Real prompts never reach the Jev transport while the route is synthetic_only (spec §8.1)."""

from __future__ import annotations

import ast
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from mnemo_memory.apps.cli import main as cli
from mnemo_memory.apps.cli import typed_decision_composition
from mnemo_memory.apps.cli.typed_decision_hook import HOOK_KINDS, TypedHookModes, TypedHookOverrides
from mnemo_memory.connectors.typesafe import jev_provider
from mnemo_memory.packages.application import (
    PersonalSettings,
    PersonalSettingsError,
    PersonalSettingsStore,
)
from mnemo_memory.packages.application.settings import with_typed_decision_mode
from mnemo_memory.packages.domain import TypedDecisionMode, TypedDecisionSource
from mnemo_memory.packages.model_gateway.typed_decisions import (
    GuardedTypedDecisionClassifier,
    TypedDecisionRecorder,
)
from scripts.typed_decision_test_support import FAKE_TYPESAFE_KEY, run_hook, seed_hook_fixture

FIXTURES = Path(__file__).parents[1] / "fixtures" / "evals"
ROUTING = json.loads((FIXTURES / "automatic-context-routing-v1.json").read_text("utf-8"))
SAMPLE: tuple[str, ...] = tuple(case["prompt"] for case in ROUTING["cases"][::6])
OFF, SHADOW, LIVE = TypedDecisionMode.OFF, TypedDecisionMode.SHADOW, TypedDecisionMode.LIVE
SOURCE = Path("src/mnemo_memory")


def test_real_hook_path_makes_zero_transport_calls_in_every_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def counting(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        calls.append(url)
        raise AssertionError("runtime text reached the Jev transport")

    monkeypatch.setattr(jev_provider, "_urllib_transport", counting)
    monkeypatch.setenv("TYPESAFE_API_KEY", FAKE_TYPESAFE_KEY)
    fixture = seed_hook_fixture(tmp_path, semantic_gate=True)
    store = PersonalSettingsStore(fixture.data)
    base = PersonalSettings(
        experimental_semantic_memory_enabled=True, experimental_typed_decisions_enabled=True
    )
    for kind in HOOK_KINDS:
        for mode in (OFF, SHADOW):
            store.save(with_typed_decision_mode(base, kind, mode))
            for prompt in SAMPLE:
                run_hook(fixture, prompt)
        with pytest.raises(PersonalSettingsError, match="synthetic_only"):
            with_typed_decision_mode(base, kind, LIVE)
    every_shadow = base
    for kind in HOOK_KINDS:
        every_shadow = with_typed_decision_mode(every_shadow, kind, SHADOW)
    store.save(every_shadow)
    for prompt in SAMPLE:
        run_hook(fixture, prompt)

    def runtime_guard(recorder: TypedDecisionRecorder) -> GuardedTypedDecisionClassifier | None:
        return typed_decision_composition.build_runtime_typed_decision_classifier(
            base, data_directory=fixture.data, recorder=recorder
        )

    forced_live = TypedHookOverrides(runtime_guard, TypedHookModes(LIVE, LIVE, LIVE, LIVE))
    for prompt in SAMPLE:
        run_hook(fixture, prompt, forced_live)
    assert calls == []
    assert not (fixture.data / "typed_decision-budget.json").exists()


def test_the_hook_builds_its_guard_with_the_runtime_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = seed_hook_fixture(tmp_path, semantic_gate=False)
    PersonalSettingsStore(fixture.data).save(
        with_typed_decision_mode(
            PersonalSettings(experimental_typed_decisions_enabled=True),
            HOOK_KINDS[3],
            SHADOW,
        )
    )
    built: list[GuardedTypedDecisionClassifier] = []
    original = typed_decision_composition.build_runtime_typed_decision_classifier

    def recording(*args: object, **kwargs: object) -> GuardedTypedDecisionClassifier | None:
        guard = original(*args, **kwargs)  # type: ignore[arg-type]
        if guard is not None:
            built.append(guard)
        return guard

    monkeypatch.setattr(
        typed_decision_composition, "build_runtime_typed_decision_classifier", recording
    )
    run_hook(fixture, "Create the changelog entry for version 2.4.")
    assert built and all(guard._source is TypedDecisionSource.RUNTIME for guard in built)


def test_only_the_composition_module_mentions_the_synthetic_builder() -> None:
    mentions = sorted(
        str(path)
        for path in SOURCE.rglob("*.py")
        if "build_synthetic_typed_decision_classifier" in path.read_text(encoding="utf-8")
    )
    assert mentions == ["src/mnemo_memory/apps/cli/typed_decision_composition.py"]


def test_no_production_call_passes_replay_overrides() -> None:
    tree = ast.parse((SOURCE / "apps/cli/main.py").read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_automatic_prompt_context_for_hook"
    ]
    assert calls
    for call in calls:
        assert all(keyword.arg != "replay_overrides" for keyword in call.keywords)
        assert len(call.args) <= 4
```

- [ ] **Step 3: Run them**

Run: `uv run pytest tests/contract/test_typed_hook_contract.py tests/security/test_typed_hook_network_boundary.py -q`
Expected: PASS (the shadow byte-identity test runs 400 in-process hook calls; expect roughly 20–40 s).

If a test fails, it is a real defect in Task 9 or 10: fix the implementation, not the test.

- [ ] **Step 4: Lint, type-check and commit**

Run: `uv run ruff format tests/contract tests/security && uv run ruff check tests && uv run mypy`
Expected: no errors.

```bash
git commit -m "test(typed-decisions): off/shadow byte identity and zero-send boundary

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- \
  tests/contract/test_typed_hook_contract.py tests/security/test_typed_hook_network_boundary.py
```

---
### Task 13: Synthetic skill fixtures

**Files:**
- Create: `tests/fixtures/evals/typed-decision-skills-v1.json`
- Create: `tests/fixtures/evals/typed-decision-skills-holdout-v1.json`
- Test: `tests/evals/test_typed_decision_skills_fixture.py` (new)

**Interfaces:**
- Consumes: `load_synthetic_fixture(path)` from `scripts/typed_decision_evaluation.py` (accepts the provenance object below).
- Produces (read by Task 14):
  - dev: `{"schema_version": 1, "fixture_kind": "synthetic-typed-decision-skills", "provenance": {...}, "skills": [{"name", "tags": [..], "when"}] × 12, "cases": [{"id", "expected_skill", "prompt"}] × 40}`; `expected_skill` is a skill name or `"none"`; 10 cases are `"none"`.
  - holdout: `{"schema_version": 1, "fixture_kind": "synthetic-typed-decision-skills-holdout", "provenance": {...}, "skills_fixture": "typed-decision-skills-v1.json", "cases": [...] × 24}`; 6 cases are `"none"`. The holdout reuses the dev skills and is written after the wording is fixed; it must not be used to tune the question.

Some prompts are deliberately hard for keyword matching (paraphrases with no tag word, or a tag word in a no-skill prompt such as "release the file lock") so "better than keyword matching" is a real comparison.

- [ ] **Step 1: Write the failing shape test**

Create `tests/evals/test_typed_decision_skills_fixture.py`:

```python
"""Shape checks for the synthetic skill-pick fixtures (spec 2026-10-02 §8.3)."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.typed_decision_evaluation import load_synthetic_fixture

FIXTURES = Path(__file__).parents[1] / "fixtures/evals"
SKILLS = FIXTURES / "typed-decision-skills-v1.json"
HOLDOUT = FIXTURES / "typed-decision-skills-holdout-v1.json"
OTHER_PROMPT_FIXTURES = (
    "automatic-context-routing-v1.json",
    "typed-decision-v1.json",
    "typed-decision-holdout-v1.json",
)
_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_TAG = re.compile(r"[a-z][a-z0-9_-]{0,31}")


def _prompts(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "prompt" and isinstance(item, str):
                found.add(item)
            else:
                found |= _prompts(item)
    elif isinstance(value, list):
        for item in value:
            found |= _prompts(item)
    return found


def test_both_fixtures_declare_synthetic_provenance() -> None:
    assert load_synthetic_fixture(SKILLS)["fixture_kind"] == "synthetic-typed-decision-skills"
    holdout = load_synthetic_fixture(HOLDOUT)
    assert holdout["fixture_kind"] == "synthetic-typed-decision-skills-holdout"
    assert holdout["skills_fixture"] == SKILLS.name


def test_dev_set_has_twelve_skills_and_forty_prompts_with_ten_needing_none() -> None:
    value = load_synthetic_fixture(SKILLS)
    skills = value["skills"]
    names = [skill["name"] for skill in skills]
    assert len(names) == len(set(names)) == 12
    assert all(_NAME.fullmatch(name) and name != "none" for name in names)
    assert all(1 <= len(skill["tags"]) <= 8 for skill in skills)
    assert all(_TAG.fullmatch(tag) for skill in skills for tag in skill["tags"])
    assert all(0 < len(skill["when"]) <= 500 for skill in skills)
    cases = value["cases"]
    assert len(cases) == 40
    assert len({case["id"] for case in cases}) == len({case["prompt"] for case in cases}) == 40
    counts = Counter(case["expected_skill"] for case in cases)
    assert counts["none"] >= 10
    assert set(counts) - {"none"} == set(names)


def test_holdout_has_twenty_four_new_prompts_with_six_needing_none() -> None:
    names = {skill["name"] for skill in load_synthetic_fixture(SKILLS)["skills"]}
    cases = load_synthetic_fixture(HOLDOUT)["cases"]
    assert len(cases) == 24
    assert len({case["id"] for case in cases}) == len({case["prompt"] for case in cases}) == 24
    counts = Counter(case["expected_skill"] for case in cases)
    assert counts["none"] >= 6
    assert set(counts) - {"none"} <= names


def test_skill_prompts_are_new_text() -> None:
    dev = _prompts(load_synthetic_fixture(SKILLS))
    holdout = _prompts(load_synthetic_fixture(HOLDOUT))
    assert not dev & holdout
    for name in OTHER_PROMPT_FIXTURES:
        other = _prompts(json.loads((FIXTURES / name).read_text(encoding="utf-8")))
        assert not (dev | holdout) & other, name
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/evals/test_typed_decision_skills_fixture.py -q`
Expected: FAIL — `FileNotFoundError: ... typed-decision-skills-v1.json`.

- [ ] **Step 3: Write the dev fixture**

Create `tests/fixtures/evals/typed-decision-skills-v1.json`:

```json
{
  "schema_version": 1,
  "fixture_kind": "synthetic-typed-decision-skills",
  "provenance": {
    "origin": "Mnemo-owned original synthetic prompts",
    "competing_product_artifacts_used": false
  },
  "skills": [
    {"name": "release-notes", "tags": ["release", "changelog"], "when": "Use when drafting release notes or a changelog entry for a new version"},
    {"name": "dbt-model-review", "tags": ["dbt", "review"], "when": "Use when reviewing a dbt model change for grain, tests and lineage impact"},
    {"name": "incident-postmortem", "tags": ["incident", "postmortem"], "when": "Use when writing a blameless postmortem after an outage or failed deploy"},
    {"name": "api-endpoint", "tags": ["api", "endpoint"], "when": "Use when adding or changing an HTTP endpoint, its request schema and handler"},
    {"name": "schema-migration", "tags": ["migration", "schema"], "when": "Use when planning a database schema migration with a rollback path"},
    {"name": "test-plan", "tags": ["testing", "coverage"], "when": "Use when designing a test plan or deciding which tests a change needs"},
    {"name": "dependency-upgrade", "tags": ["dependency", "upgrade"], "when": "Use when upgrading a library version and checking for breaking changes"},
    {"name": "performance-profiling", "tags": ["performance", "profiling"], "when": "Use when a slow request or batch job needs profiling to find the bottleneck"},
    {"name": "security-review", "tags": ["security", "threat"], "when": "Use when reviewing a change for injection, leaked secrets or authorization flaws"},
    {"name": "contributor-guide", "tags": ["onboarding", "contributing"], "when": "Use when writing or updating a getting-started guide for new contributors"},
    {"name": "flag-rollout", "tags": ["rollout", "flag"], "when": "Use when planning a staged rollout behind a feature flag with a kill switch"},
    {"name": "data-backfill", "tags": ["backfill", "history"], "when": "Use when planning a historical data backfill for an existing table"}
  ],
  "cases": [
    {"id": "skill-01", "expected_skill": "release-notes", "prompt": "Draft the changelog entry for version 2.4 of the CLI."},
    {"id": "skill-02", "expected_skill": "release-notes", "prompt": "Write up what shipped in this version for users reading the update page."},
    {"id": "skill-03", "expected_skill": "release-notes", "prompt": "Summarize the new features and fixes going out in Friday's release."},
    {"id": "skill-04", "expected_skill": "dbt-model-review", "prompt": "Review the dbt change to the orders model before I merge it."},
    {"id": "skill-05", "expected_skill": "dbt-model-review", "prompt": "Check whether my edit to the revenue model changes its grain or breaks its tests."},
    {"id": "skill-06", "expected_skill": "incident-postmortem", "prompt": "Write the postmortem for yesterday's checkout outage."},
    {"id": "skill-07", "expected_skill": "incident-postmortem", "prompt": "We had a two-hour payments incident; help me document what went wrong without blaming anyone."},
    {"id": "skill-08", "expected_skill": "api-endpoint", "prompt": "Add a new endpoint that returns an invoice as a PDF."},
    {"id": "skill-09", "expected_skill": "api-endpoint", "prompt": "Expose the export job status over HTTP so the dashboard can poll it."},
    {"id": "skill-10", "expected_skill": "schema-migration", "prompt": "Plan the schema migration that splits the users table in two."},
    {"id": "skill-11", "expected_skill": "schema-migration", "prompt": "We need to rename a column in Postgres safely and be able to undo it."},
    {"id": "skill-12", "expected_skill": "test-plan", "prompt": "Design a test plan for the new retry logic."},
    {"id": "skill-13", "expected_skill": "test-plan", "prompt": "Which cases should I cover before shipping the rate limiter?"},
    {"id": "skill-14", "expected_skill": "dependency-upgrade", "prompt": "Upgrade the requests library to its latest major version."},
    {"id": "skill-15", "expected_skill": "dependency-upgrade", "prompt": "Bump pydantic from v1 to v2 and find what breaks."},
    {"id": "skill-16", "expected_skill": "performance-profiling", "prompt": "Profile the nightly import job; it got three times slower."},
    {"id": "skill-17", "expected_skill": "performance-profiling", "prompt": "Find the performance bottleneck in the search endpoint."},
    {"id": "skill-18", "expected_skill": "security-review", "prompt": "Do a security review of the new file upload handler."},
    {"id": "skill-19", "expected_skill": "security-review", "prompt": "Check this login change for SQL injection or leaked tokens."},
    {"id": "skill-20", "expected_skill": "contributor-guide", "prompt": "Update the getting-started guide for new contributors."},
    {"id": "skill-21", "expected_skill": "contributor-guide", "prompt": "Explain to a first-week engineer how to run the project locally, as a doc in the repo."},
    {"id": "skill-22", "expected_skill": "flag-rollout", "prompt": "Roll out the new pricing page to ten percent of users first, behind a flag."},
    {"id": "skill-23", "expected_skill": "flag-rollout", "prompt": "Plan a staged release of dark mode with a way to turn it off instantly."},
    {"id": "skill-24", "expected_skill": "data-backfill", "prompt": "Backfill the last two years of order history into the new table."},
    {"id": "skill-25", "expected_skill": "data-backfill", "prompt": "Recompute daily revenue for every day since 2023 in the summary table."},
    {"id": "skill-26", "expected_skill": "release-notes", "prompt": "Draft release notes for the 3.0 launch."},
    {"id": "skill-27", "expected_skill": "incident-postmortem", "prompt": "Write a blameless review of the failed deploy on Monday."},
    {"id": "skill-28", "expected_skill": "dbt-model-review", "prompt": "Review the dbt lineage impact of dropping the legacy customers source."},
    {"id": "skill-29", "expected_skill": "api-endpoint", "prompt": "Add request validation to the create-user API."},
    {"id": "skill-30", "expected_skill": "test-plan", "prompt": "Help me decide which tests the payment refactor needs."},
    {"id": "no-skill-01", "expected_skill": "none", "prompt": "Explain how Python's GIL affects threads."},
    {"id": "no-skill-02", "expected_skill": "none", "prompt": "Write a regular expression that matches ISO dates."},
    {"id": "no-skill-03", "expected_skill": "none", "prompt": "Rename the variable tmp to buffer in this function."},
    {"id": "no-skill-04", "expected_skill": "none", "prompt": "What is the difference between a list and a tuple?"},
    {"id": "no-skill-05", "expected_skill": "none", "prompt": "Translate this error message into plain English."},
    {"id": "no-skill-06", "expected_skill": "none", "prompt": "Release the file lock before returning from the function."},
    {"id": "no-skill-07", "expected_skill": "none", "prompt": "Add a docstring to the parse_row helper."},
    {"id": "no-skill-08", "expected_skill": "none", "prompt": "Why does my for loop skip the last element?"},
    {"id": "no-skill-09", "expected_skill": "none", "prompt": "Format this JSON so it is easier to read."},
    {"id": "no-skill-10", "expected_skill": "none", "prompt": "Flag any typos in this paragraph."}
  ]
}
```

- [ ] **Step 4: Write the holdout fixture**

Create `tests/fixtures/evals/typed-decision-skills-holdout-v1.json`:

```json
{
  "schema_version": 1,
  "fixture_kind": "synthetic-typed-decision-skills-holdout",
  "provenance": {
    "origin": "Mnemo-owned original synthetic prompts",
    "competing_product_artifacts_used": false
  },
  "skills_fixture": "typed-decision-skills-v1.json",
  "cases": [
    {"id": "h-skill-01", "expected_skill": "release-notes", "prompt": "Put together the notes users will see for the 1.8 update."},
    {"id": "h-skill-02", "expected_skill": "release-notes", "prompt": "List the user-facing changes in this sprint's version bump for the changelog."},
    {"id": "h-skill-03", "expected_skill": "dbt-model-review", "prompt": "Before merging, look over my change to the fct_sessions model for duplicate rows."},
    {"id": "h-skill-04", "expected_skill": "incident-postmortem", "prompt": "The queue backed up for an hour last night; write up the timeline and root cause."},
    {"id": "h-skill-05", "expected_skill": "api-endpoint", "prompt": "Add a route that lets clients delete a saved search."},
    {"id": "h-skill-06", "expected_skill": "schema-migration", "prompt": "Change the type of the amount column from float to decimal without downtime."},
    {"id": "h-skill-07", "expected_skill": "test-plan", "prompt": "Work out what we need to test before turning on the new billing calculator."},
    {"id": "h-skill-08", "expected_skill": "dependency-upgrade", "prompt": "Move the project from Django 4.2 to 5.1."},
    {"id": "h-skill-09", "expected_skill": "performance-profiling", "prompt": "The dashboard query takes nine seconds; figure out where the time goes."},
    {"id": "h-skill-10", "expected_skill": "security-review", "prompt": "Look over this webhook handler for ways an attacker could abuse it."},
    {"id": "h-skill-11", "expected_skill": "contributor-guide", "prompt": "Write the doc a new teammate reads on day one to set up their machine."},
    {"id": "h-skill-12", "expected_skill": "flag-rollout", "prompt": "Ship the redesigned checkout to internal users first, then everyone, with an off switch."},
    {"id": "h-skill-13", "expected_skill": "data-backfill", "prompt": "Fill in the missing region values for all historical shipments."},
    {"id": "h-skill-14", "expected_skill": "security-review", "prompt": "Make sure the new admin export cannot be reached by regular users."},
    {"id": "h-skill-15", "expected_skill": "dependency-upgrade", "prompt": "Update numpy and fix anything the new version deprecates."},
    {"id": "h-skill-16", "expected_skill": "schema-migration", "prompt": "Add a non-null tenant_id column to every table with a safe way back."},
    {"id": "h-skill-17", "expected_skill": "performance-profiling", "prompt": "Memory use doubles during the nightly report; find out why."},
    {"id": "h-skill-18", "expected_skill": "api-endpoint", "prompt": "Version the public orders API so v1 clients keep working."},
    {"id": "h-no-skill-01", "expected_skill": "none", "prompt": "What does the walrus operator do in Python?"},
    {"id": "h-no-skill-02", "expected_skill": "none", "prompt": "Sort these names alphabetically."},
    {"id": "h-no-skill-03", "expected_skill": "none", "prompt": "Write a haiku about debugging."},
    {"id": "h-no-skill-04", "expected_skill": "none", "prompt": "Convert 72 degrees Fahrenheit to Celsius."},
    {"id": "h-no-skill-05", "expected_skill": "none", "prompt": "Is a hash map lookup always constant time?"},
    {"id": "h-no-skill-06", "expected_skill": "none", "prompt": "Shorten this commit message to one line."}
  ]
}
```

- [ ] **Step 5: Run the shape test and the existing fixture tests**

Run: `uv run pytest tests/evals/test_typed_decision_skills_fixture.py tests/evals/test_typed_decision_holdout_fixture.py tests/evals/test_typed_decision_fixture.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git commit -m "test(fixtures): synthetic skill-pick dev and holdout sets

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- \
  tests/fixtures/evals/typed-decision-skills-v1.json \
  tests/fixtures/evals/typed-decision-skills-holdout-v1.json \
  tests/evals/test_typed_decision_skills_fixture.py
```

---

### Task 14: Synthetic fresh-process replay

**Files:**
- Create: `scripts/typed_decision_replay.py` (library)
- Create: `scripts/run_typed_decision_replay.py` (CLI and child entry point)
- Test: `tests/evals/test_typed_decision_replay.py` (new)

**Interfaces:**
- Consumes:
  - Task 3: `build_synthetic_typed_decision_classifier(settings, *, data_directory, environ, jev_transport, recorder)`.
  - Task 7/8: `TypedHookModes`, `TypedHookOverrides(guard_factory, modes, observer)`, `TypedStepInput`, `TypedPromptDecisions`, `FILLER_OMISSION_DETAIL`.
  - Task 9: `cli._automatic_prompt_context_for_hook(..., replay_overrides=...)`, `cli._refresh_project_knowledge`, `cli._automatic_route_scope`; `scripts/typed_decision_test_support.choice_answer` (tests only).
  - Task 10: `AutomaticRouteEvent.typed`.
  - Task 13: the two skill fixtures.
  - `scripts/typed_decision_evaluation.py`: `load_synthetic_fixture`, `FrontDoorRow`, `score_front_door`, `ROUTING_FIXTURE`, `HOLDOUT_FIXTURE`, `TYPED_DECISION_FIXTURE`, `VIABILITY_FIXTURE`, `REPOSITORY_ROOT`.
- Produces (`scripts/typed_decision_replay.py`):
  - Constants `SKILLS_FIXTURE`, `SKILLS_HOLDOUT_FIXTURE`, `SETS = ("dev", "holdout")`, `ARMS = ("rules", "typed")`, `NOTE_BATCH = 5`, `MAXIMUM_ADDED_HOOK_MS = 850`, `MAXIMUM_CAP_SHARE = 0.05`.
  - `@dataclass(frozen=True, slots=True) class ReplayCase(set_name, group, case_id, prompt, expected_route=None, expected_skill=None, expected_tier=None)`; `group` is `memory`, `skill`, `tier` or `notes`.
  - `@dataclass(frozen=True, slots=True) class ReplayNote(note_id, summary, category)`.
  - `@dataclass(frozen=True, slots=True) class ReplaySeed(set_name, data_directory: Path, project_directory: Path, categories: Mapping[str, str])`.
  - `@dataclass(frozen=True, slots=True) class ReplayRequest(set_name, group, case_id, arm, data_directory: str, project_directory: str)`.
  - `@dataclass(frozen=True, slots=True) class ReplayResult(set_name, group, case_id, arm, attached_tokens: int, hook_ms: int, step_ms: int | None, cap_hit: bool, structural_need: str | None, long_term_need: str | None, tier: str | None, skill_pick: str, checked_item_ids: tuple[str, ...], dropped_item_ids: tuple[str, ...], applied_drop_item_ids: tuple[str, ...])` with `to_json()` / `from_json(text)`. `dropped_item_ids` are Jev's would-drop decisions; `applied_drop_item_ids` are the notes the hook really dropped, read from the per-note `lower_rank` filler omissions in the rendered attachment.
  - `replay_notes(set_name) -> tuple[ReplayNote, ...]`, `replay_cases() -> tuple[ReplayCase, ...]`, `find_case(set_name, group, case_id) -> ReplayCase`, `seed_replay_set(root: Path, set_name: str) -> ReplaySeed`, `run_replay_case(request, *, environ, jev_transport=None) -> ReplayResult`, `run_case_in_fresh_process(request, *, live_calls_authorized, timeout_seconds=120.0) -> ReplayResult`, `run_replay(seeds, cases, runner) -> list[ReplayResult]`, `score_replay(cases, results, seeds) -> dict[str, Any]`, `class ReplayChildError(RuntimeError)`.
- Produces (`scripts/run_typed_decision_replay.py`): `main(argv=None, *, environ=None, runner=None, stdin=None, stdout=None) -> int`; report at `evaluation-results/typed-decisions/replay/<run-id>/report.json`.

How it runs (spec §8.2): each set gets its own temporary data directory, seeded with synthetic notes (one Markdown note per fixture note, grouped five to a "batch" heading), four approved events (two relevant, two noise; the first pinned) and the twelve fixture skills; settings turn the semantic-memory gate on and diagnostics to `trace`. Every case runs twice: `rules` (no overrides — today's hook) and `typed` (all four decisions `live` through replay overrides with a synthetic-source guard). The production runner starts one `python -m scripts.run_typed_decision_replay --child` per prompt and arm, so cold start counts as in the real hook. The child never accepts prompt text: it receives a case ID and re-reads the prompt from the provenance-checked fixtures. A typed child refuses to run without `--live-calls-authorized` and a key. Tests use an in-process runner with a fake transport; no live replay is part of this plan.

Scoring (spec §8.3), per decision:
- memory need: `score_front_door` on the final plan's needs (from the route event), typed arm, dev and holdout;
- filler: Jev's would-drop decisions mapped to seeded categories — zero relevant drops and ≥ 90% of noise dropped, per set; the report also counts the drops actually applied (per-note `lower_rank` omissions in the output) and the judged drops not applied (an omission line did not fit, or the route changed after the answer);
- tier hint: heavy recall ≥ 0.95 on the phase-1 tier set (a hard-rule prompt that was not asked counts as heavy, the safe side);
- skill pick: ≥ 85% correct skill, ≥ 95% correct `none`, and accuracy strictly above keyword matching, per set;
- all: ≤ 5% of typed prompts hit the 0.8 s cap; the largest added hook time (typed minus rules, per prompt) ≤ 850 ms;
- tokens: typed-live total attached tokens below rules-only, reported per group.

- [ ] **Step 1: Write the failing tests**

Create `tests/evals/test_typed_decision_replay.py`:

```python
"""The synthetic hook replay: seeding, the child boundary and the §8.3 gates (no network)."""

from __future__ import annotations

import io
import json
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

import scripts.run_typed_decision_replay as replay_cli
from scripts.typed_decision_replay import (
    ReplayCase,
    ReplayRequest,
    ReplayResult,
    ReplaySeed,
    find_case,
    replay_cases,
    replay_notes,
    run_case_in_fresh_process,
    run_replay,
    run_replay_case,
    score_replay,
    seed_replay_set,
)
from scripts.typed_decision_test_support import FAKE_TYPESAFE_KEY, choice_answer

MEMORY_LABEL = {
    "prior_memory": "past_sessions",
    "knowledge": "project_docs",
    "structure": "code_structure",
    "none": "nothing",
}


class ReplayOracle:
    """Answer from fixture labels: expected memory route, expected skill, noise is filler."""

    def __init__(self) -> None:
        self.calls = 0
        cases = replay_cases()
        self.routes = {case.prompt: case.expected_route or "knowledge" for case in cases}
        self.skills = {case.prompt: case.expected_skill or "none" for case in cases}
        self.noise = tuple(
            note.summary
            for set_name in ("dev", "holdout")
            for note in replay_notes(set_name)
            if note.category == "noise"
        )

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        self.calls += 1
        request = json.loads(body)
        state = request["state"]
        answers: dict[str, object] = {}
        for name, question in request["questions"].items():
            labels = list(question["criteria"])
            if name == "memory_need":
                label = MEMORY_LABEL[self.routes.get(state, "none")]
            elif name == "note_substance":
                noise = any(summary in state for summary in self.noise)
                label = "filler" if noise else "task_information"
            elif name == "skill_pick":
                label = self.skills.get(state, "none")
            elif name == "complexity":
                label = "heavy"
            else:
                label = "edit"
            answers[name] = choice_answer(labels, label, 0.9)
        return json.dumps(
            {"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 10}}
        ).encode()


def test_replay_cases_come_only_from_fixtures_and_are_unique() -> None:
    cases = replay_cases()
    groups = {(case.set_name, case.group) for case in cases}
    assert groups == {
        ("dev", "memory"),
        ("dev", "skill"),
        ("dev", "tier"),
        ("dev", "notes"),
        ("holdout", "memory"),
        ("holdout", "skill"),
        ("holdout", "notes"),
    }
    counts = {group: sum((case.set_name, case.group) == group for case in cases) for group in groups}
    assert counts[("dev", "memory")] == 60 and counts[("holdout", "memory")] == 40
    assert counts[("dev", "skill")] == 40 and counts[("holdout", "skill")] == 24
    assert counts[("dev", "tier")] == 40
    assert counts[("dev", "notes")] == 14 and counts[("holdout", "notes")] == 5
    keys = [(case.set_name, case.group, case.case_id) for case in cases]
    assert len(keys) == len(set(keys))
    with pytest.raises(ValueError, match="synthetic fixture"):
        find_case("dev", "memory", "not-a-fixture-case")


def test_seed_maps_every_note_and_event_to_its_category(tmp_path: Path) -> None:
    seed = seed_replay_set(tmp_path, "holdout")
    knowledge = [key for key in seed.categories if key.startswith("knowledge:")]
    events = [key for key in seed.categories if key.startswith("approved-episodic:")]
    assert len(knowledge) == len(replay_notes("holdout")) == 24
    assert len(events) == 4
    assert set(seed.categories.values()) == {"relevant", "noise"}
    assert (seed.data_directory / "settings.json").exists()


def _in_process(oracle: ReplayOracle) -> Callable[[ReplayRequest], ReplayResult]:
    def run(request: ReplayRequest) -> ReplayResult:
        return run_replay_case(
            request, environ={"TYPESAFE_API_KEY": FAKE_TYPESAFE_KEY}, jev_transport=oracle
        )

    return run


def test_in_process_replay_runs_both_arms_and_scores_them(tmp_path: Path) -> None:
    seed = seed_replay_set(tmp_path, "holdout")
    all_cases = [case for case in replay_cases() if case.set_name == "holdout"]
    subset = (
        [case for case in all_cases if case.group == "memory"][:4]
        + [case for case in all_cases if case.group == "skill"][:3]
        + [case for case in all_cases if case.group == "notes"][:1]
    )
    oracle = ReplayOracle()
    results = run_replay({"holdout": seed}, subset, _in_process(oracle))
    assert len(results) == 2 * len(subset)
    by_key = {(result.case_id, result.arm): result for result in results}

    for case in subset:
        rules = by_key[(case.case_id, "rules")]
        assert rules.checked_item_ids == () and rules.cap_hit is False
    for case in (case for case in subset if case.group == "skill"):
        assert by_key[(case.case_id, "typed")].skill_pick == case.expected_skill
    notes = by_key[(subset[-1].case_id, "typed")]
    assert notes.checked_item_ids
    for item_id in notes.checked_item_ids:
        category = next(
            (value for prefix, value in seed.categories.items() if item_id.startswith(prefix)),
            None,
        )
        if category is None:
            continue  # a skill document matched the probe; it is not a scored note
        assert (item_id in notes.dropped_item_ids) == (category == "noise")
    assert set(notes.applied_drop_item_ids) <= set(notes.dropped_item_ids)

    report = score_replay(subset, results, {"holdout": seed})
    assert set(report["sets"]) == {"holdout"}
    assert report["sets"]["holdout"]["filler"]["relevant_dropped"] == 0
    assert isinstance(report["complete"], bool)
    assert oracle.calls > 0


def _result(
    case: ReplayCase,
    arm: str,
    *,
    tokens: int = 100,
    hook_ms: int = 100,
    cap_hit: bool = False,
    skill_pick: str = "none",
    checked: tuple[str, ...] = (),
    dropped: tuple[str, ...] = (),
    applied: tuple[str, ...] = (),
) -> ReplayResult:
    return ReplayResult(
        case.set_name,
        case.group,
        case.case_id,
        arm,
        tokens,
        hook_ms,
        None,
        cap_hit,
        "no",
        "yes",
        "heavy",
        skill_pick,
        checked,
        dropped,
        applied,
    )


def test_score_replay_applies_the_section_8_3_gates() -> None:
    skill = ReplayCase("dev", "skill", "s1", "p1", expected_skill="test-plan")
    none = ReplayCase("dev", "skill", "s2", "p2", expected_skill="none")
    notes = ReplayCase("dev", "notes", "n1", "p3")
    seed = ReplaySeed(
        "dev", Path("."), Path("."), {"knowledge:good:": "relevant", "knowledge:noise:": "noise"}
    )
    cases = [skill, none, notes]
    passing = [
        _result(skill, "rules", tokens=200, skill_pick="none"),
        _result(skill, "typed", tokens=100, hook_ms=500, skill_pick="test-plan"),
        _result(none, "rules", tokens=200, skill_pick="release-notes"),
        _result(none, "typed", tokens=100, hook_ms=500, skill_pick="none"),
        _result(notes, "rules", tokens=200),
        _result(
            notes,
            "typed",
            tokens=100,
            hook_ms=500,
            checked=("knowledge:good:r:section:0", "knowledge:noise:r:section:0"),
            dropped=("knowledge:noise:r:section:0",),
            applied=("knowledge:noise:r:section:0",),
        ),
    ]
    report = score_replay(cases, passing, {"dev": seed})
    assert report["gates"] == {
        "filler_dev": True,
        "skill_dev": True,
        "latency": True,
        "tokens": True,
    }
    assert report["complete"] is True
    filler = report["sets"]["dev"]["filler"]
    assert (filler["drops_judged"], filler["drops_applied"], filler["drops_not_applied"]) == (
        1,
        1,
        0,
    )

    slow = [*passing[:-1], replace_result(passing[-1], hook_ms=1_200, cap_hit=True)]
    failing = score_replay(cases, slow, {"dev": seed})
    assert failing["gates"]["latency"] is False
    assert failing["latency"]["added_hook_ms"]["max"] == 1_100

    leaky = [
        *passing[:-1],
        replace_result(passing[-1], dropped_item_ids=("knowledge:good:r:section:0",)),
    ]
    assert score_replay(cases, leaky, {"dev": seed})["gates"]["filler_dev"] is False

    costly = [
        replace_result(result, attached_tokens=300) if result.arm == "typed" else result
        for result in passing
    ]
    assert score_replay(cases, costly, {"dev": seed})["gates"]["tokens"] is False


def replace_result(result: ReplayResult, **changes: object) -> ReplayResult:
    values = {**json.loads(result.to_json()), **changes}
    return ReplayResult.from_json(json.dumps(values))


def test_cli_refuses_without_authorization_or_key(tmp_path: Path) -> None:
    calls: list[ReplayRequest] = []

    def runner(request: ReplayRequest) -> ReplayResult:
        calls.append(request)
        raise AssertionError("no case may run without authorization")

    args = ["--run-id", "test-run", "--results-root", str(tmp_path)]
    assert replay_cli.main(args, environ={"TYPESAFE_API_KEY": FAKE_TYPESAFE_KEY}, runner=runner) == 2
    assert replay_cli.main([*args, "--live-calls-authorized"], environ={}, runner=runner) == 2
    assert replay_cli.main(["--run-id", "bad id", "--live-calls-authorized"], environ={}) == 2
    assert calls == [] and not (tmp_path / "test-run").exists()


def test_child_refuses_unknown_cases_and_unauthorized_typed_runs(tmp_path: Path) -> None:
    request = ReplayRequest("dev", "memory", "prior-01", "typed", str(tmp_path), str(tmp_path))
    unauthorized = replay_cli.main(
        ["--child"],
        environ={"TYPESAFE_API_KEY": FAKE_TYPESAFE_KEY},
        stdin=io.StringIO(json.dumps(_as_dict(request))),
        stdout=io.StringIO(),
    )
    assert unauthorized == 2
    unknown = _as_dict(request) | {"case_id": "not-a-fixture-case", "arm": "rules"}
    assert (
        replay_cli.main(
            ["--child"],
            environ={},
            stdin=io.StringIO(json.dumps(unknown)),
            stdout=io.StringIO(),
        )
        == 2
    )


def _as_dict(request: ReplayRequest) -> dict[str, str]:
    return {
        "set_name": request.set_name,
        "group": request.group,
        "case_id": request.case_id,
        "arm": request.arm,
        "data_directory": request.data_directory,
        "project_directory": request.project_directory,
    }


def test_fresh_process_runner_runs_a_rules_case(tmp_path: Path) -> None:
    seed = seed_replay_set(tmp_path, "holdout")
    case = next(case for case in replay_cases() if case.set_name == "holdout")
    result = run_case_in_fresh_process(
        ReplayRequest(
            "holdout",
            case.group,
            case.case_id,
            "rules",
            str(seed.data_directory),
            str(seed.project_directory),
        ),
        live_calls_authorized=False,
    )
    assert (result.case_id, result.arm) == (case.case_id, "rules")
    assert result.hook_ms >= 0 and result.checked_item_ids == ()


def test_cli_writes_a_report_with_an_in_process_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    small = tuple(
        case
        for case in replay_cases()
        if case.case_id in {"prior-01", "skill-01", "notes-dev-b00", "h-prior-01", "light-01"}
    )
    monkeypatch.setattr(replay_cli, "replay_cases", lambda: small)
    oracle = ReplayOracle()
    code = replay_cli.main(
        ["--run-id", "test-run", "--results-root", str(tmp_path), "--live-calls-authorized"],
        environ={"TYPESAFE_API_KEY": FAKE_TYPESAFE_KEY},
        runner=_in_process(oracle),
    )
    report = json.loads((tmp_path / "test-run" / "report.json").read_text("utf-8"))
    assert code in {0, 1}
    assert report["run"] == {"run_id": "test-run", "prompts": 5, "requests": 10}
    assert set(report["gates"]) >= {"latency", "tokens", "tier"}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/evals/test_typed_decision_replay.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.run_typed_decision_replay'`.

- [ ] **Step 3: Write the replay library**

Create `scripts/typed_decision_replay.py`:

```python
"""Synthetic replay of the Jev hook wiring through the real prompt hook (spec 2026-10-02 §8).

``seed_replay_set`` builds a data directory from fixtures that declare synthetic provenance.
``run_replay_case`` runs one fixture prompt through ``_automatic_prompt_context_for_hook``;
``run_case_in_fresh_process`` does the same in a new interpreter, as the real hook does.
``score_replay`` turns the per-prompt results into the §8.3 gates. Nothing here opens a network
connection itself: a typed case reaches Jev only through the synthetic-source guard, and only
with prompt text the child re-reads from a provenance-checked fixture by case ID.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from mnemo_memory.apps.cli import main as cli
from mnemo_memory.apps.cli.typed_decision_composition import (
    build_synthetic_typed_decision_classifier,
)
from mnemo_memory.apps.cli.typed_decision_hook import (
    FILLER_OMISSION_DETAIL,
    TypedHookModes,
    TypedHookOverrides,
    TypedPromptDecisions,
    TypedStepInput,
)
from mnemo_memory.connectors.typesafe import JevTransport
from mnemo_memory.packages.application import (
    LocalConfig,
    PersonalSettings,
    PersonalSettingsStore,
    RecordApprovedEpisodicEvent,
    SetApprovedEpisodicEventPin,
    build_checkpoint_runtime,
)
from mnemo_memory.packages.application.automatic_memory import (
    LocalMemoryProjectBindingStore,
    MemoryProjectBinding,
)
from mnemo_memory.packages.application.context_routing import bounded_automatic_context_prompt
from mnemo_memory.packages.domain import (
    ApprovedEventKind,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    SourceId,
    SourceTrustClass,
    TypedDecisionMode,
    VerificationStatus,
)
from mnemo_memory.packages.model_gateway.decision_axes import (
    SKILL_PICK_NONE,
    NeedAnswer,
)
from mnemo_memory.packages.model_gateway.typed_decisions import (
    GuardedTypedDecisionClassifier,
    TypedDecisionRecord,
    TypedDecisionRecorder,
)
from mnemo_memory.packages.skills_registry import KnowledgeDocumentSkillRegistry
from mnemo_memory.packages.storage import SQLiteKnowledgeDocumentRepository
from mnemo_memory.packages.telemetry import (
    AutomaticRouteDiagnosticsMode,
    AutomaticRouteDiagnosticsSettings,
    AutomaticRouteEvent,
    LocalAutomaticRouteDiagnosticsSettingsStore,
    LocalAutomaticRouteTelemetryStore,
)
from scripts.typed_decision_evaluation import (
    HOLDOUT_FIXTURE,
    REPOSITORY_ROOT,
    ROUTING_FIXTURE,
    TYPED_DECISION_FIXTURE,
    VIABILITY_FIXTURE,
    FrontDoorRow,
    load_synthetic_fixture,
    score_front_door,
)

_FIXTURES = REPOSITORY_ROOT / "tests/fixtures/evals"
SKILLS_FIXTURE = _FIXTURES / "typed-decision-skills-v1.json"
SKILLS_HOLDOUT_FIXTURE = _FIXTURES / "typed-decision-skills-holdout-v1.json"
SETS = ("dev", "holdout")
ARMS = ("rules", "typed")
NOTE_BATCH = 5
MAXIMUM_ADDED_HOOK_MS = 850
MAXIMUM_CAP_SHARE = 0.05
CLIENT = "claude-code"
_LIVE = TypedHookModes(
    TypedDecisionMode.LIVE, TypedDecisionMode.LIVE, TypedDecisionMode.LIVE, TypedDecisionMode.LIVE
)
_NOISE_SUMMARY = (
    "Background conversation {index:04d} for synthetic workflow {template}; it is unrelated "
    "and must not displace active task state."
)


class ReplayChildError(RuntimeError):
    """A replay child failed; its output is not echoed back."""


@dataclass(frozen=True, slots=True)
class ReplayCase:
    set_name: str
    group: str
    case_id: str
    prompt: str
    expected_route: str | None = None
    expected_skill: str | None = None
    expected_tier: str | None = None


@dataclass(frozen=True, slots=True)
class ReplayNote:
    note_id: str
    summary: str
    category: str


@dataclass(frozen=True, slots=True)
class ReplaySeed:
    set_name: str
    data_directory: Path
    project_directory: Path
    categories: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ReplayRequest:
    set_name: str
    group: str
    case_id: str
    arm: str
    data_directory: str
    project_directory: str


@dataclass(frozen=True, slots=True)
class ReplayResult:
    set_name: str
    group: str
    case_id: str
    arm: str
    attached_tokens: int
    hook_ms: int
    step_ms: int | None
    cap_hit: bool
    structural_need: str | None
    long_term_need: str | None
    tier: str | None
    skill_pick: str
    checked_item_ids: tuple[str, ...]
    dropped_item_ids: tuple[str, ...]
    applied_drop_item_ids: tuple[str, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> ReplayResult:
        value = json.loads(text)
        value["checked_item_ids"] = tuple(value["checked_item_ids"])
        value["dropped_item_ids"] = tuple(value["dropped_item_ids"])
        value["applied_drop_item_ids"] = tuple(value["applied_drop_item_ids"])
        return cls(**value)


Runner = Callable[[ReplayRequest], ReplayResult]


def replay_notes(set_name: str) -> tuple[ReplayNote, ...]:
    """Notes seeded for one set: the viability events plus noise (dev) or the holdout notes."""

    if set_name == "holdout":
        return tuple(
            ReplayNote(note["id"], note["summary"], note["category"])
            for note in load_synthetic_fixture(HOLDOUT_FIXTURE)["notes"]
        )
    notes: list[ReplayNote] = []
    for template in load_synthetic_fixture(VIABILITY_FIXTURE)["templates"]:
        relevant = set(template["ground_truth"]["relevant_evidence"])
        notes.extend(
            ReplayNote(
                event["event_key"],
                event["summary"],
                "relevant" if event["event_key"] in relevant else "superseded",
            )
            for event in template["events"]
        )
        notes.extend(
            ReplayNote(
                f"noise-{template['template_id']}-{index}",
                _NOISE_SUMMARY.format(index=index, template=template["template_id"]),
                "noise",
            )
            for index in (1, 2)
        )
    return tuple(notes)


def _notes_prompt(batch: int) -> str:
    return f"What do the project notes say about replay batch b{batch:02d}?"


def replay_cases() -> tuple[ReplayCase, ...]:
    """Every replay prompt, read from synthetic fixtures (plus generated note probes)."""

    routing = load_synthetic_fixture(ROUTING_FIXTURE)["cases"]
    holdout = load_synthetic_fixture(HOLDOUT_FIXTURE)["front_door_cases"]
    tiers = load_synthetic_fixture(TYPED_DECISION_FIXTURE)["tier_cases"]
    skills = load_synthetic_fixture(SKILLS_FIXTURE)["cases"]
    skills_holdout = load_synthetic_fixture(SKILLS_HOLDOUT_FIXTURE)["cases"]
    cases: list[ReplayCase] = [
        *(
            ReplayCase("dev", "memory", case["id"], case["prompt"], case["expected_route"])
            for case in routing
        ),
        *(
            ReplayCase("dev", "skill", case["id"], case["prompt"], None, case["expected_skill"])
            for case in skills
        ),
        *(
            ReplayCase("dev", "tier", case["id"], case["prompt"], None, None, case["expected_tier"])
            for case in tiers
        ),
        *(
            ReplayCase("holdout", "memory", case["id"], case["prompt"], case["expected_route"])
            for case in holdout
        ),
        *(
            ReplayCase(
                "holdout", "skill", case["id"], case["prompt"], None, case["expected_skill"]
            )
            for case in skills_holdout
        ),
    ]
    for set_name in SETS:
        batches = math.ceil(len(replay_notes(set_name)) / NOTE_BATCH)
        cases.extend(
            ReplayCase(set_name, "notes", f"notes-{set_name}-b{batch:02d}", _notes_prompt(batch))
            for batch in range(batches)
        )
    return tuple(cases)


def find_case(set_name: str, group: str, case_id: str) -> ReplayCase:
    for case in replay_cases():
        if (case.set_name, case.group, case.case_id) == (set_name, group, case_id):
            return case
    raise ValueError("replay case is not in a synthetic fixture")


def _evidence(seed: str, *, user: bool = False) -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.USER_CORRECTION if user else EvidenceSourceType.TOOL_RESULT,
        SourceTrustClass.USER_CORRECTION if user else SourceTrustClass.VERIFIED_TOOL_RESULT,
        f"fixture://typed-replay/{seed}",
        "sha256:" + ("b" if user else "a") * 64,
        EvidenceLocation(f"fixture://typed-replay/{seed}"),
        datetime.now(UTC),
        VerificationStatus.VERIFIED,
    )


def _skill_markdown(skill: Mapping[str, Any]) -> str:
    tags = ", ".join(skill["tags"])
    return (
        f"---\nmnemo_kind: skill\nmnemo_name: {skill['name']}\nmnemo_version: 1.0.0\n"
        f"mnemo_tags: {tags}\nmnemo_clients: codex, claude-code\nmnemo_trust: checked_in\n"
        f"mnemo_when: {skill['when']}\n---\n# {skill['name']}\nSynthetic skill body.\n"
    )


def seed_replay_set(root: Path, set_name: str) -> ReplaySeed:
    """A bound project seeded only from synthetic fixtures (spec §8.2 step 1)."""

    project = root / set_name / "project"
    data = root / set_name / "data"
    project.mkdir(parents=True)
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    PersonalSettingsStore(data).save(PersonalSettings(experimental_semantic_memory_enabled=True))
    LocalAutomaticRouteDiagnosticsSettingsStore(data).save(
        AutomaticRouteDiagnosticsSettings(AutomaticRouteDiagnosticsMode.TRACE, 7)
    )
    notes = replay_notes(set_name)
    by_path: dict[str, str] = {}
    for index, note in enumerate(notes):
        batch = index // NOTE_BATCH
        relative = f"notes/b{batch:02d}/{note.note_id}.md"
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# Replay batch b{batch:02d} note\n{note.summary}\n", encoding="utf-8")
        by_path[relative] = note.category
    (project / "skills").mkdir()
    for skill in load_synthetic_fixture(SKILLS_FIXTURE)["skills"]:
        (project / "skills" / f"{skill['name']}.md").write_text(
            _skill_markdown(skill), encoding="utf-8"
        )
    cli._refresh_project_knowledge(data, binding)
    repository = SQLiteKnowledgeDocumentRepository(data / "mnemo.sqlite3", base_directory=data)
    repository.migrate()
    categories = {
        f"knowledge:{known.document_id}:": by_path[known.relative_path]
        for known in repository.list_active_documents(binding.scope)
        if known.relative_path in by_path
    }
    categories.update(_seed_approved_events(data, binding, notes))
    return ReplaySeed(set_name, data, project, categories)


def _seed_approved_events(
    data: Path, binding: MemoryProjectBinding, notes: Sequence[ReplayNote]
) -> dict[str, str]:
    """Two relevant and two noise notes also become approved events; the first is pinned."""

    chosen = [note for note in notes if note.category == "relevant"][:2] + [
        note for note in notes if note.category == "noise"
    ][:2]
    categories: dict[str, str] = {}
    with build_checkpoint_runtime(LocalConfig.defaults(data)) as runtime:
        service = runtime.checkpoint_service
        for index, note in enumerate(chosen):
            event = service.record_approved_event(
                RecordApprovedEpisodicEvent(
                    binding.checkpoint_scope,
                    ApprovedEventKind.DECISION,
                    note.summary,
                    f"replay:{note.note_id}",
                    (_evidence(note.note_id),),
                )
            ).event
            categories[f"approved-episodic:{event.event_id}"] = note.category
            if index == 0:
                service.set_approved_event_pin(
                    SetApprovedEpisodicEventPin(
                        binding.checkpoint_scope,
                        event.event_id,
                        True,
                        f"replay-pin:{note.note_id}",
                        (_evidence(f"pin-{note.note_id}", user=True),),
                    )
                )
    return categories


class _TeeRecorder:
    """Pass each guard record to the hook's recorder and keep a copy for cap scoring."""

    def __init__(self, first: TypedDecisionRecorder, records: list[TypedDecisionRecord]) -> None:
        self._first = first
        self._records = records

    def record(self, record: TypedDecisionRecord) -> None:
        self._first.record(record)
        self._records.append(record)


def run_replay_case(
    request: ReplayRequest,
    *,
    environ: Mapping[str, str],
    jev_transport: JevTransport | None = None,
) -> ReplayResult:
    """Run one fixture prompt, one arm, through the real hook function."""

    if request.arm not in ARMS:
        raise ValueError("replay arm is invalid")
    case = find_case(request.set_name, request.group, request.case_id)
    data = Path(request.data_directory)
    binding = LocalMemoryProjectBindingStore(data).get(Path(request.project_directory))
    if binding is None:
        raise ValueError("replay project is not bound")
    records: list[TypedDecisionRecord] = []
    observed: list[tuple[TypedStepInput, TypedPromptDecisions]] = []
    overrides: TypedHookOverrides | None = None
    if request.arm == "typed":
        settings = PersonalSettingsStore(data).load()

        def guard(recorder: TypedDecisionRecorder) -> GuardedTypedDecisionClassifier:
            return build_synthetic_typed_decision_classifier(
                settings,
                data_directory=data,
                environ=environ,
                jev_transport=jev_transport,
                recorder=_TeeRecorder(recorder, records),
            )

        def observe(step: TypedStepInput, decisions: TypedPromptDecisions) -> None:
            observed.append((step, decisions))

        overrides = TypedHookOverrides(guard, _LIVE, observe)
    started = time.perf_counter()
    attachment = cli._automatic_prompt_context_for_hook(
        data, binding.checkpoint_scope, case.prompt, CLIENT, replay_overrides=overrides
    )
    hook_ms = round((time.perf_counter() - started) * 1_000)
    event = _event(data, binding, attachment.telemetry_event_id)
    context = attachment.context or ""
    step, decisions = observed[-1] if observed else (None, None)
    typed = None if event is None else event.typed
    return ReplayResult(
        set_name=request.set_name,
        group=request.group,
        case_id=request.case_id,
        arm=request.arm,
        attached_tokens=(len(context) + 3) // 4,
        hook_ms=hook_ms,
        step_ms=None if typed is None else typed.step_ms,
        cap_hit=any(record.outcome == "timeout" for record in records),
        structural_need=None if event is None else event.shadow_structural_need,
        long_term_need=None if event is None else event.shadow_long_term_need,
        tier=None if typed is None else typed.tier,
        skill_pick=_skill_pick(data, binding, case.prompt, step, decisions),
        checked_item_ids=(
            () if step is None else tuple(item.item_id for item in step.filler_candidates)
        ),
        dropped_item_ids=() if decisions is None else decisions.drop_item_ids,
        applied_drop_item_ids=_applied_drops(context),
    )


def _applied_drops(context: str) -> tuple[str, ...]:
    """Notes the hook really dropped: its per-note ``lower_rank`` filler omission lines."""

    applied: list[str] = []
    for line in context.split("\n"):
        if not line.startswith("MNEMO_OMISSION "):
            continue
        value = json.loads(line.removeprefix("MNEMO_OMISSION "))
        if value.get("reason") == "lower_rank" and value.get("detail") == FILLER_OMISSION_DETAIL:
            applied.append(str(value["item_id"]))
    return tuple(applied)


def _event(
    data: Path, binding: MemoryProjectBinding, event_id: UUID | None
) -> AutomaticRouteEvent | None:
    if event_id is None:
        return None
    events = LocalAutomaticRouteTelemetryStore(data).events(
        cli._automatic_route_scope(binding.checkpoint_scope), limit=1
    )
    return events[0] if events and events[0].event_id == event_id else None


def _skill_pick(
    data: Path,
    binding: MemoryProjectBinding,
    prompt: str,
    step: TypedStepInput | None,
    decisions: TypedPromptDecisions | None,
) -> str:
    """The skill the hook would offer: Jev's accepted pick, else the keyword top candidate."""

    if decisions is not None and decisions.skill is not None:
        return decisions.skill
    if step is not None:
        return step.keyword_skill_names[0] if step.keyword_skill_names else SKILL_PICK_NONE
    with build_checkpoint_runtime(LocalConfig.defaults(data)) as runtime:
        if runtime.knowledge_document_repository is None:
            return SKILL_PICK_NONE
        candidates = KnowledgeDocumentSkillRegistry(
            runtime.knowledge_document_repository
        ).discover_current_skills(binding.scope, bounded_automatic_context_prompt(prompt), CLIENT)
    return candidates[0].skill.name if candidates else SKILL_PICK_NONE


def run_case_in_fresh_process(
    request: ReplayRequest, *, live_calls_authorized: bool, timeout_seconds: float = 120.0
) -> ReplayResult:
    """Run one case in a new interpreter so cold start counts, as in the real hook."""

    command = [sys.executable, "-m", "scripts.run_typed_decision_replay", "--child"]
    if live_calls_authorized:
        command.append("--live-calls-authorized")
    completed = subprocess.run(
        command,
        input=json.dumps(asdict(request)),
        capture_output=True,
        text=True,
        cwd=REPOSITORY_ROOT,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        raise ReplayChildError(f"replay child failed for {request.case_id}")
    return ReplayResult.from_json(completed.stdout.strip().splitlines()[-1])


def run_replay(
    seeds: Mapping[str, ReplaySeed], cases: Sequence[ReplayCase], runner: Runner
) -> list[ReplayResult]:
    """Run every case twice, rules-only then typed-live, in its own set's data directory."""

    results: list[ReplayResult] = []
    for case in cases:
        seed = seeds[case.set_name]
        for arm in ARMS:
            results.append(
                runner(
                    ReplayRequest(
                        case.set_name,
                        case.group,
                        case.case_id,
                        arm,
                        str(seed.data_directory),
                        str(seed.project_directory),
                    )
                )
            )
    return results


def score_replay(
    cases: Sequence[ReplayCase],
    results: Sequence[ReplayResult],
    seeds: Mapping[str, ReplaySeed],
) -> dict[str, Any]:
    """Score the spec §8.3 gates per decision; only sets and groups present are scored."""

    by_key = {(r.set_name, r.group, r.case_id, r.arm): r for r in results}

    def result(case: ReplayCase, arm: str) -> ReplayResult:
        return by_key[(case.set_name, case.group, case.case_id, arm)]

    report: dict[str, Any] = {"sets": {}}
    gates: dict[str, bool] = {}
    for set_name in [name for name in SETS if any(case.set_name == name for case in cases)]:
        set_cases = [case for case in cases if case.set_name == set_name]
        section: dict[str, Any] = {}
        memory = [case for case in set_cases if case.group == "memory"]
        if memory:
            for arm in ARMS:
                section[f"memory_{arm}"] = score_front_door(
                    [_front_door_row(case, result(case, arm)) for case in memory]
                )
            gates[f"memory_{set_name}"] = all(section["memory_typed"]["gates"].values())
        typed_results = [result(case, "typed") for case in set_cases]
        if any(item.checked_item_ids for item in typed_results):
            section["filler"] = _score_filler(typed_results, seeds[set_name])
            gates[f"filler_{set_name}"] = all(section["filler"]["gates"].values())
        skill = [case for case in set_cases if case.group == "skill"]
        if skill:
            section["skill"] = _score_skill(skill, result)
            gates[f"skill_{set_name}"] = all(section["skill"]["gates"].values())
        report["sets"][set_name] = section
    tier = [case for case in cases if case.group == "tier"]
    if tier:
        report["tier"] = _score_tier(tier, result)
        gates["tier"] = all(report["tier"]["gates"].values())
    report["latency"] = _score_latency(cases, result)
    gates["latency"] = all(report["latency"]["gates"].values())
    report["tokens"] = _score_tokens(cases, result)
    gates["tokens"] = report["tokens"]["typed_lower"]
    report["gates"] = gates
    report["complete"] = bool(gates) and all(gates.values())
    return report


def _front_door_row(case: ReplayCase, result: ReplayResult) -> FrontDoorRow:
    return FrontDoorRow(
        case.case_id,
        case.expected_route or "none",
        NeedAnswer(result.structural_need or "unknown"),
        NeedAnswer(result.long_term_need or "unknown"),
        result.hook_ms,
        None,
    )


def _category(seed: ReplaySeed, item_id: str) -> str | None:
    exact = seed.categories.get(item_id)
    if exact is not None:
        return exact
    return next(
        (value for prefix, value in seed.categories.items() if item_id.startswith(prefix)), None
    )


def _score_filler(results: Sequence[ReplayResult], seed: ReplaySeed) -> dict[str, Any]:
    rows: list[tuple[str, bool]] = []
    uncategorized = 0
    for result in results:
        dropped = set(result.dropped_item_ids)
        for item_id in result.checked_item_ids:
            category = _category(seed, item_id)
            if category is None:
                uncategorized += 1
                continue
            rows.append((category, item_id in dropped))
    relevant = [dropped for category, dropped in rows if category == "relevant"]
    noise = [dropped for category, dropped in rows if category == "noise"]
    noise_rate = _share(sum(noise), len(noise))
    judged = sum(len(result.dropped_item_ids) for result in results)
    applied = sum(len(result.applied_drop_item_ids) for result in results)
    return {
        "checks": len(rows),
        "drops_judged": judged,
        "drops_applied": applied,
        "drops_not_applied": judged - applied,
        "uncategorized_checks": uncategorized,
        "relevant_checks": len(relevant),
        "relevant_dropped": sum(relevant),
        "noise_checks": len(noise),
        "noise_drop_rate": noise_rate,
        "superseded_dropped": sum(dropped for category, dropped in rows if category == "superseded"),
        "gates": {
            "relevant_dropped": bool(relevant) and sum(relevant) == 0,
            "filler_removed": bool(noise) and noise_rate >= 0.9,
        },
    }


def _score_skill(
    cases: Sequence[ReplayCase], result: Callable[[ReplayCase, str], ReplayResult]
) -> dict[str, Any]:
    def correct(case: ReplayCase, arm: str) -> bool:
        return result(case, arm).skill_pick == case.expected_skill

    with_skill = [case for case in cases if case.expected_skill != SKILL_PICK_NONE]
    without = [case for case in cases if case.expected_skill == SKILL_PICK_NONE]
    typed_skill = _share(sum(correct(case, "typed") for case in with_skill), len(with_skill))
    typed_none = _share(sum(correct(case, "typed") for case in without), len(without))
    typed_all = _share(sum(correct(case, "typed") for case in cases), len(cases))
    keyword_all = _share(sum(correct(case, "rules") for case in cases), len(cases))
    return {
        "skill_prompts": len(with_skill),
        "no_skill_prompts": len(without),
        "typed_correct_skill": typed_skill,
        "typed_correct_none": typed_none,
        "typed_accuracy": typed_all,
        "keyword_accuracy": keyword_all,
        "gates": {
            "correct_skill": not with_skill or typed_skill >= 0.85,
            "correct_none": not without or typed_none >= 0.95,
            "better_than_keyword": typed_all > keyword_all,
        },
    }


def _score_tier(
    cases: Sequence[ReplayCase], result: Callable[[ReplayCase, str], ReplayResult]
) -> dict[str, Any]:
    heavy = [case for case in cases if case.expected_tier == "heavy"]
    recalled = sum(result(case, "typed").tier != "light" for case in heavy)
    recall = _share(recalled, len(heavy))
    return {
        "heavy_cases": len(heavy),
        "heavy_recall": recall,
        "gates": {"heavy_recall": bool(heavy) and recall >= 0.95},
    }


def _score_latency(
    cases: Sequence[ReplayCase], result: Callable[[ReplayCase, str], ReplayResult]
) -> dict[str, Any]:
    typed = [result(case, "typed") for case in cases]
    rules = [result(case, "rules") for case in cases]
    added = sorted(
        max(0, result(case, "typed").hook_ms - result(case, "rules").hook_ms) for case in cases
    )
    steps = sorted(item.step_ms for item in typed if item.step_ms is not None)
    cap_share = _share(sum(item.cap_hit for item in typed), len(typed))
    return {
        "prompts": len(typed),
        "cap_hit_share": cap_share,
        "added_hook_ms": _percentiles(added),
        "step_ms": _percentiles(steps),
        "hook_ms": {
            "rules": _percentiles(sorted(item.hook_ms for item in rules)),
            "typed": _percentiles(sorted(item.hook_ms for item in typed)),
        },
        "gates": {
            "cap_hit_share": bool(typed) and cap_share <= MAXIMUM_CAP_SHARE,
            "added_hook_max": bool(added) and added[-1] <= MAXIMUM_ADDED_HOOK_MS,
        },
    }


def _score_tokens(
    cases: Sequence[ReplayCase], result: Callable[[ReplayCase, str], ReplayResult]
) -> dict[str, Any]:
    def total(arm: str, group: str | None = None) -> int:
        return sum(
            result(case, arm).attached_tokens
            for case in cases
            if group is None or case.group == group
        )

    groups = sorted({case.group for case in cases})
    return {
        "rules": total("rules"),
        "typed": total("typed"),
        "by_group": {
            group: {"rules": total("rules", group), "typed": total("typed", group)}
            for group in groups
        },
        "typed_lower": total("typed") < total("rules"),
    }


def _percentiles(ordered: Sequence[int]) -> dict[str, int | None]:
    return {
        "p50": _nearest_rank(ordered, 0.50),
        "p95": _nearest_rank(ordered, 0.95),
        "max": ordered[-1] if ordered else None,
    }


def _nearest_rank(ordered: Sequence[int], quantile: float) -> int | None:
    if not ordered:
        return None
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def _share(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0
```

- [ ] **Step 4: Write the CLI and child entry point**

Create `scripts/run_typed_decision_replay.py`:

```python
"""Run the Jev hook-wiring replay on synthetic fixtures (spec 2026-10-02 §8.2).

Every prompt runs in a fresh process, twice (rules-only and typed-live). A run sends fixture
text to Jev, so it needs --live-calls-authorized (the maintainer's go-ahead, each time) and
TYPESAFE_API_KEY in the environment. Example:

    uv run python -m scripts.run_typed_decision_replay --run-id 2026-10-03-replay-a \
        --live-calls-authorized
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TextIO

from scripts.typed_decision_evaluation import REPOSITORY_ROOT
from scripts.typed_decision_replay import (
    SETS,
    ReplayRequest,
    ReplayResult,
    Runner,
    replay_cases,
    run_case_in_fresh_process,
    run_replay,
    run_replay_case,
    score_replay,
    seed_replay_set,
)

DEFAULT_RESULTS_ROOT = REPOSITORY_ROOT / "evaluation-results" / "typed-decisions" / "replay"
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    runner: Runner | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id")
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--live-calls-authorized", action="store_true")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    variables = os.environ if environ is None else environ
    if args.child:
        return _child(
            args.live_calls_authorized, variables, stdin or sys.stdin, stdout or sys.stdout
        )
    if args.run_id is None or not _RUN_ID.fullmatch(args.run_id):
        return _refuse("--run-id must be 1-64 letters, digits, '.', '_' or '-'")
    if not args.live_calls_authorized:
        return _refuse("live Jev calls need --live-calls-authorized (maintainer go-ahead)")
    if not variables.get("TYPESAFE_API_KEY", "").strip():
        return _refuse("TYPESAFE_API_KEY is not set")
    output = args.results_root / args.run_id / "report.json"
    if output.exists():
        return _refuse(f"{output} already exists")

    def fresh(request: ReplayRequest) -> ReplayResult:
        return run_case_in_fresh_process(request, live_calls_authorized=True)

    run: Runner = runner or fresh
    cases = replay_cases()
    with TemporaryDirectory(prefix="mnemo-typed-replay-") as root:
        seeds = {name: seed_replay_set(Path(root), name) for name in SETS}
        results = run_replay(seeds, cases, run)
        report = score_replay(cases, results, seeds)
    report["run"] = {"run_id": args.run_id, "prompts": len(cases), "requests": len(results)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    status = "complete" if report["complete"] else "NOT complete"
    print(f"typed-decision hook replay {status}: {output}")
    return 0 if report["complete"] else 1


def _child(
    live_calls_authorized: bool, environ: Mapping[str, str], stdin: TextIO, stdout: TextIO
) -> int:
    """One replay case in this fresh process; the prompt is re-read from the fixtures."""

    try:
        request = ReplayRequest(**json.loads(stdin.read()))
    except (TypeError, ValueError):
        return _refuse("replay child request is invalid")
    if request.arm == "typed" and not live_calls_authorized:
        return _refuse("a typed replay child needs --live-calls-authorized")
    if request.arm == "typed" and not environ.get("TYPESAFE_API_KEY", "").strip():
        return _refuse("TYPESAFE_API_KEY is not set")
    try:
        result = run_replay_case(request, environ=environ)
    except ValueError:
        return _refuse("replay case is not in a synthetic fixture")
    stdout.write(result.to_json() + "\n")
    return 0


def _refuse(message: str) -> int:
    print(f"refusing: {message}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/evals/test_typed_decision_replay.py tests/evals/test_typed_decision_evaluation.py -q`
Expected: PASS. `test_fresh_process_runner_runs_a_rules_case` starts one real child interpreter (a few seconds); no test reaches the network.

- [ ] **Step 6: Lint, type-check and commit**

Run: `uv run ruff format scripts tests/evals && uv run ruff check scripts tests && uv run mypy`
Expected: no errors.

```bash
git commit -m "feat(eval): synthetic fresh-process replay for the Jev hook wiring

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- \
  scripts/typed_decision_replay.py scripts/run_typed_decision_replay.py \
  tests/evals/test_typed_decision_replay.py
```

Do **not** run the replay with `--live-calls-authorized`. A live replay needs the maintainer's explicit go-ahead.

---
### Task 15: Paperwork

**Files:**
- Modify: `docs/adr/0049-hosted-typed-classifier-and-cheap-model-tier.md` (`## Consequences`, after the paragraph ending "would otherwise cut every check off at 0.6 s.", about `:122`)
- Modify: `docs/threat-model.md` (new entry directly after the "Hosted typed-decision classifier data exposure" entry, before `## Security gates and ownership`, about `:1746`)
- Modify: `docs/implementation-status.md` (about 500 KB — locate with `grep -n "Phase-2 hardening (2026-10-02" docs/implementation-status.md`; insert after that paragraph, which ends "signed zero-data-retention terms and new runtime wiring."; do not read the whole file)
- Modify: `docs/user-guide.md` (new subsection directly before `### Optional: add one Obsidian vault`, about `:860`)
- Outside the repository, not committed: `~/.claude/projects/-Users-keithfajardo-Desktop-local-personal-mnemo-memory/memory/jev-phase2-prerequisites.md`

**Interfaces:**
- Consumes: the names shipped in Tasks 1–14.
- Produces: documentation only.

- [ ] **Step 1: ADR 0049 Consequences**

Insert after the paragraph that ends "would otherwise cut every check off at 0.6 s.":

```markdown
- Phase 2, part 1 — hook wiring (spec `docs/superpowers/specs/2026-10-02-jev-hook-wiring-design.md`):
  the prompt hook carries four typed decisions, each `off`, `shadow` or `live`: memory need
  (`front_door`), the per-note filler check (`relevance`), the task-size hint (`tier_hint`) and
  skill pick (`skill`). Shadow records content-free `typed_v1` route telemetry and changes nothing.
  Live is locked three ways: no mode may be `live` while the data route is `synthetic_only`;
  `front_door: live` needs `experimental_semantic_memory_enabled`; any mode other than `off` needs
  the master switch. The runtime guard's per-request deadline is now 0.8 s
  (`FILLER_CHECK_BUDGET_SECONDS`), and the front-door request plus up to 16 filler requests go out
  together under one 0.8 s `ask_each` cap, in one `asyncio.run` per prompt.
  `DenyAllModelBudget` is replaced by a file-locked local daily counter
  (`typed_decision_daily_input_tokens`, default 10,000,000 input tokens, about $0.42 a day at
  $0.042 per million). Each dropped note stays reachable: it leaves one standard `lower_rank`
  omission naming its own item ID (no packet schema change), and `get_context item_ids` fetches
  it again; a note whose omission line does not fit the attachment budget is kept. A synthetic fresh-process replay (`scripts/run_typed_decision_replay.py`)
  scores the gates per decision; a decision that fails stays at most in shadow. Real-traffic
  promotion still needs a ZDR route, then shadow on real traffic, then live.
```

- [ ] **Step 2: Threat model entry**

Insert before `## Security gates and ownership`:

```markdown
### Hook-path typed-decision traffic

**Scenario:** With a typed-decision mode on, the automatic-memory prompt hook runs a typed step
on every prompt. A real prompt, a stored note or a skill name could reach TypeSafe. A hand-edited
setting could turn on live behaviour. The replay's synthetic-source helper could be reused on
real prompts. A slow or failing step could delay the hook or blank its output.

**Required controls:** `automatic-memory-hook` builds its guard only through
`build_runtime_typed_decision_classifier`, bound to the `runtime` source, and never passes replay
overrides; under `synthetic_only` every runtime request returns `data_route_blocked` before
credential, budget or network work. Settings refuse `live` while the route is `synthetic_only`,
refuse `front_door: live` without the semantic-memory gate, and refuse any mode without the master
switch, on load and on every change. Only the composition module defines the synthetic-source
builder, and only the replay scripts and tests call it; the replay child re-reads its prompt from
a provenance-checked fixture by case ID and refuses typed runs without `--live-calls-authorized`.
Pinned, mandatory, conflict-participating, non-`normal` and secret-flagged notes are never sent.
The step runs in one `asyncio.run` under a 0.8 s cap and is wrapped as a whole: any exception
keeps today's output and records `typed_step_error`. `typed_v1` telemetry holds closed values,
bounded counts and booleans only — no prompt, note or skill name. The local daily input-token
counter is file-locked and fails closed on a corrupt or unsafe file. An explicit `get_context`
fetch is never filtered, and `item_ids` lookups recheck scope, currentness and sensitivity.

**Verification:** `tests/security/test_typed_hook_network_boundary.py` (zero transport calls in
every mode, runtime source, synthetic builder confined, no `replay_overrides` in production
calls), `tests/contract/test_typed_hook_contract.py` (off and shadow byte-identical),
`tests/unit/test_typed_hook_integration.py` (content-free telemetry, step-error fallback),
`tests/unit/test_local_model_budget.py`, `tests/unit/test_context_item_lookup.py`.
```

- [ ] **Step 3: Implementation status**

Find the anchor: `grep -n "Phase-2 hardening (2026-10-02" docs/implementation-status.md`. After that paragraph (it ends "signed zero-data-retention terms and new runtime wiring.") insert a blank line and:

```markdown
Phase-2 hook wiring (2026-10-02, spec `docs/superpowers/specs/2026-10-02-jev-hook-wiring-design.md`,
plan `docs/superpowers/plans/2026-10-02-jev-hook-wiring.md`): the prompt hook can run memory need,
the filler check, the task-size hint and skill pick in `off`, `shadow` or `live` mode
(`mnemo-memory typed-decisions status|set`). Real data still never leaves the machine: the route
stays `synthetic_only`, every runtime request is `data_route_blocked`, and settings refuse `live`.
Shadow output is byte-identical to off over the 100 routing and holdout prompts. Also new: a
file-locked local daily typed-decision budget, per-note `lower_rank` omissions for dropped notes
(no packet schema change) with `get_context item_ids` to fetch them again, content-free `typed_v1` route telemetry, a skill-pick axis with two
synthetic skill fixtures, and a fresh-process synthetic replay with per-decision gates. The filler
guard deadline (phase-2 open item 2) is closed: the runtime guard now uses 0.8 s. No live replay
has run; it needs the maintainer's go-ahead. Still open: the worker-thread cap for the long-lived
MCP server.
```

- [ ] **Step 4: User guide**

Insert before `### Optional: add one Obsidian vault`:

````markdown
#### Optional: Jev typed decisions (shadow only for now)

Mnemo can ask TypeSafe's Jev four small questions about each prompt: whether earlier memory is
needed, whether a pre-fetched note is filler, whether the task is light and reading-heavy (then a
one-line hint suggests a Haiku subagent), and which project skill fits. Each question has its own
mode: `off` (today's behaviour), `shadow` (Jev answers are recorded beside the rules and nothing
changes) or `live` (Jev's answers change what is attached).

Live is locked. While the data route is `synthetic_only` — the only route today — Mnemo refuses
`live` for every decision and blocks every real prompt before any network call, so shadow mode
only proves the wiring is harmless. Check and change the modes with:

```bash
mnemo-memory typed-decisions status
mnemo-memory typed-decisions set skill shadow
```

`status` shows the master switch (`experimental_typed_decisions_enabled`), the route, each mode,
the locks that are active, whether `TYPESAFE_API_KEY` is present (yes or no only, never the
value), and today's reserved input tokens against `typed_decision_daily_input_tokens` (default
10,000,000 per UTC day). `set` refuses a locked change with a plain reason: any mode needs the
master switch, `front_door: live` needs `experimental_semantic_memory_enabled`, and no mode may be
`live` on `synthetic_only`. If the budget counter file (`typed_decision-budget.json` in the data
directory) is ever damaged, every request is denied and `status` shows the counter as
`unavailable`; delete the file to reset it.

When the filler check is live and drops a note, the attached context keeps one standard
`MNEMO_OMISSION` line for that note: its `item_id` is the note's own ID, the reason is
`lower_rank` and the detail is `judged filler; fetch with get_context item_ids`. If that line would
not fit the attachment budget, the note is kept instead. An agent can fetch dropped notes again
with `get_context` and `item_ids` (1–16 IDs, on both the full and compact MCP profiles). The fetch
rechecks scope, currentness and sensitivity: a note that changed comes back as `superseded`, a
deleted or retracted one as `expired`, one outside this project as `unauthorized_scope`. Explicit
`get_context` calls are never filtered.
````

- [ ] **Step 5: Close open item 2 in the phase-2 memory (not committed)**

In `~/.claude/projects/-Users-keithfajardo-Desktop-local-personal-mnemo-memory/memory/jev-phase2-prerequisites.md`, replace the line

```markdown
2. **Filler guard deadline.** The runtime guard's per-request deadline is 0.6 s. Filler checks need a guard with a deadline of at least 0.8 s, or every check is cut short.
```

with

```markdown
2. **Filler guard deadline — DONE (hook wiring, 2026-10-02).** The runtime guard's per-request deadline is now 0.8 s (`FILLER_CHECK_BUDGET_SECONDS`), and the hook sends everything under one 0.8 s `ask_each` cap.
```

and append to item 3: ` Still open for the long-lived MCP server (extraction sub-project); the hook process sends at most 17 requests and exits.`

- [ ] **Step 6: Commit the docs**

```bash
git commit -m "docs: record the Jev hook wiring (ADR 0049, threat model, status, user guide)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- \
  docs/adr/0049-hosted-typed-classifier-and-cheap-model-tier.md docs/threat-model.md \
  docs/implementation-status.md docs/user-guide.md
```

---

### Final step: full check

- [ ] **Step 1: Run the full check**

Run: `npm run check`
Expected: every stage passes — format check, lint, mypy, pytest (including the new contract, security, unit and eval tests), the PostgreSQL team suite, the context-packet schema check, the dependency check (no new dependency), the architecture check (Jev-connector importer list unchanged) and the installed-package check.

If a stage fails, fix the cause in the task that owns the code, re-run that task's focused tests, commit the fix with `git commit -- <paths>`, and run `npm run check` again.
