"""Real prompts never reach the Jev transport while the route is synthetic_only (spec §8.1)."""

from __future__ import annotations

import ast
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

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

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "evals"
ROUTING = json.loads((FIXTURES / "automatic-context-routing-v1.json").read_text("utf-8"))
SAMPLE: tuple[str, ...] = tuple(case["prompt"] for case in ROUTING["cases"][::6])
OFF, SHADOW, LIVE = TypedDecisionMode.OFF, TypedDecisionMode.SHADOW, TypedDecisionMode.LIVE
SOURCE = ROOT / "src" / "mnemo_memory"


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
        path.relative_to(ROOT).as_posix()
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
