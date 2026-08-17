#!/usr/bin/env python3
"""Run the offline, payload-free lifecycle token break-even construction.

The runner invokes Mnemo's production hook composition but never invokes a model. Prompts and
rendered memory exist only in process memory; persisted rows contain hashes, counts, and grades.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from mnemo_memory.apps.cli.main import build_automatic_memory_hook
from mnemo_memory.packages.application import (
    CreateCheckpoint,
    LocalConfig,
    PersonalSettings,
    PersonalSettingsStore,
    build_checkpoint_runtime,
    resolve_local_config,
)
from mnemo_memory.packages.application.automatic_memory import LocalMemoryProjectBindingStore
from mnemo_memory.packages.application.evaluation.conditions import (
    build_domain_events,
    render_event,
    rolling_natural_language_summary,
)
from mnemo_memory.packages.application.evaluation.models import (
    EventSpec,
    Horizon,
    Scenario,
    load_corpus,
)
from mnemo_memory.packages.application.semantic_rendering import ConservativeTokenCounter
from mnemo_memory.packages.domain import CheckpointContent, MemoryScope
from mnemo_memory.packages.telemetry import (
    AutomaticRouteScope,
    LocalAutomaticRouteTelemetryStore,
)

_FIXTURE = Path("tests/fixtures/evals/lifecycle-token-break-even-v1.json")
_SOURCE_CORPUS = Path("tests/fixtures/evals/viability-corpus-v1.json")
_CONDITIONS = ("FH", "RS", "NM", "MR")
_STARTING_STATE = "No prior-session fact is carried in the current implementation state."
_RUN_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_TOKEN_COUNTER = ConservativeTokenCounter()


class LifecycleTokenBreakEvenError(RuntimeError):
    """Safe evaluation failure that contains no transient payload."""


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise LifecycleTokenBreakEvenError("evaluation input is invalid")
    return cast(dict[str, Any], value)


def _additional_context(output: dict[str, object]) -> str | None:
    hook_output = output.get("hookSpecificOutput")
    if not isinstance(hook_output, dict):
        return None
    value = hook_output.get("additionalContext")
    return value if isinstance(value, str) and value else None


def _automatic_route_scope(scope: MemoryScope) -> AutomaticRouteScope:
    if scope.project_id is None or scope.session_id is None or scope.task_id is None:
        raise LifecycleTokenBreakEvenError("evaluation scope is invalid")
    return AutomaticRouteScope(
        str(scope.owner_id),
        str(scope.workspace_id),
        str(scope.project_id),
        str(scope.session_id),
        str(scope.task_id),
        scope.visibility.value,
    )


def _session_for_event(
    event: EventSpec,
    index: int,
    total: int,
    *,
    seed_event_key: str,
    changed_event_key: str,
) -> int:
    if event.event_key == seed_event_key:
        return 1
    if event.event_key == changed_event_key:
        return 10
    return min(30, 1 + (index * 29 // max(1, total - 1)))


def _purpose_prompt(
    purpose: str,
    *,
    prompt_marker: str,
) -> str:
    if purpose == "self_contained":
        prompt = "Start a new implementation from this complete specification."
    elif purpose in {"prior_memory_need", "repeat_prior_memory_need"}:
        prompt = (
            "Use the prior handoff to resume this task and retrieve its required verified fact."
        )
    elif purpose == "changed_memory_need":
        prompt = "Use the latest saved handoff and explain what changed before continuing."
    else:
        raise LifecycleTokenBreakEvenError("evaluation lifecycle purpose is invalid")
    return prompt if not prompt_marker else f"{prompt}\n{prompt_marker}"


def _assemble_model_prompt(current_input: str, history_transport: str) -> str:
    prompt = f"{current_input}\n\nCurrent implementation state:\n{_STARTING_STATE}"
    if history_transport:
        prompt += f"\n\nPrior-history transport:\n{history_transport}"
    return prompt


def _full_history(events: list[EventSpec]) -> str:
    return "\n".join(render_event(item.event_key, item.actor, item.summary) for item in events)


def _rolling_summary(scenario: Scenario, events: list[EventSpec]) -> str:
    if not events:
        return ""
    partial = replace(scenario, events=tuple(events), target_event_count=len(events))
    summary, _ = rolling_natural_language_summary(partial, 200, _TOKEN_COUNTER)
    return summary


def _checkpoint_content() -> CheckpointContent:
    return CheckpointContent(
        task_objective="Evaluate a synthetic long-horizon memory lifecycle.",
        completed_work=(),
        current_state="The local deterministic evaluation lifecycle is initialized.",
        remaining_work=("Continue the preregistered synthetic lifecycle.",),
        decisions=(),
        failures=(),
        blockers=(),
        relevant_files=(),
        relevant_artifacts=(),
        verification_performed=(),
        token_estimate=24,
    )


def _condition_transport(
    condition: str,
    scenario: Scenario,
    available_events: list[EventSpec],
    mr_context: list[str],
) -> str:
    if condition == "FH":
        return _full_history(available_events)
    if condition == "RS":
        return _rolling_summary(scenario, available_events)
    if condition == "NM":
        return ""
    if condition == "MR":
        return "\n\n".join(mr_context)
    raise LifecycleTokenBreakEvenError("evaluation condition is invalid")


def _delivery_keys_cleared(data_directory: Path, session_id: str) -> bool:
    path = data_directory / "automatic-memory-session-state.json"
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(values, dict):
        return False
    state = values.get(session_id)
    return isinstance(state, dict) and not state.get("delivered_context_keys")


def _memory_necessity_valid(
    required_meaning: str,
    current_input: str,
    lifecycle_schedule: object,
) -> bool:
    carried = "\n".join(
        (
            current_input,
            _STARTING_STATE,
            _canonical_json(lifecycle_schedule),
            _canonical_json({"conditions": _CONDITIONS, "horizons": (1, 10, 30)}),
        )
    )
    return required_meaning.casefold() not in carried.casefold()


def _family_rows(
    work_directory: Path,
    fixture: dict[str, Any],
    family: dict[str, Any],
    scenario: Scenario,
    *,
    prompt_marker: str,
) -> tuple[dict[str, Any], ...]:
    template_id = str(family["template_id"])
    seed_event_key = str(family["seed_event_key"])
    changed_event_key = str(family["changed_event_key"])
    family_directory = work_directory / template_id
    project_directory = family_directory / "project"
    data_directory = family_directory / "data"
    project_directory.mkdir(mode=0o700, parents=True)
    data_directory.mkdir(mode=0o700, parents=True)

    config: LocalConfig = resolve_local_config(data_directory)
    binding = LocalMemoryProjectBindingStore(data_directory).enable(project_directory)
    PersonalSettingsStore(data_directory).save(
        PersonalSettings(experimental_semantic_memory_enabled=True)
    )
    domain_events, _ = build_domain_events(scenario, binding.checkpoint_scope)
    domain_by_key = {
        spec.event_key: event for spec, event in zip(scenario.events, domain_events, strict=True)
    }
    spec_by_key = {event.event_key: event for event in scenario.events}
    if seed_event_key not in domain_by_key or changed_event_key not in domain_by_key:
        raise LifecycleTokenBreakEvenError("evaluation event reference is invalid")
    required_summary = spec_by_key[seed_event_key].summary
    _, separator, required_meaning = required_summary.partition(":")
    required_meaning = required_meaning.strip() if separator else required_summary

    with build_checkpoint_runtime(config) as runtime:
        runtime.checkpoint_service.create(
            CreateCheckpoint(
                binding.checkpoint_scope,
                _checkpoint_content(),
                domain_by_key[seed_event_key].evidence_references,
            )
        )

    events_by_session: dict[int, list[EventSpec]] = {number: [] for number in range(1, 31)}
    for index, event in enumerate(scenario.events):
        session_number = _session_for_event(
            event,
            index,
            len(scenario.events),
            seed_event_key=seed_event_key,
            changed_event_key=changed_event_key,
        )
        events_by_session[session_number].append(event)

    hook = build_automatic_memory_hook(config, "codex")
    cumulative_tokens = {condition: 0 for condition in _CONDITIONS}
    prompt_counts = {condition: 0 for condition in _CONDITIONS}
    available_events: list[EventSpec] = []
    lifecycle: dict[str, int] = {
        "session_start_count": 0,
        "user_prompt_submit_count": 0,
        "self_contained_attachment_tokens": 0,
        "detail_delivery_count": 0,
        "precompact_count": 0,
        "precompact_reset_count": 0,
        "delivered_context_tokens": 0,
        "mnemo_model_call_count": 0,
    }
    captured: dict[int, dict[str, dict[str, object]]] = {}
    primary_inputs: dict[str, tuple[str, str, int, bool]] = {}
    sessions = cast(list[dict[str, Any]], fixture["sessions"])
    horizons = {int(value) for value in cast(list[int], fixture["horizons"])}

    for session in sessions:
        session_number = int(session["session_number"])
        session_id = f"{template_id}-{session['client_session_id']}"
        pending = list(events_by_session[session_number])
        changed_spec = spec_by_key[changed_event_key]
        history_before_boundary = [item for item in pending if item.event_key != changed_event_key]
        available_events.extend(history_before_boundary)
        durable_before_boundary = [
            item for item in history_before_boundary if item.event_key == seed_event_key
        ]
        if durable_before_boundary:
            with build_checkpoint_runtime(config) as runtime:
                assert runtime.semantic_memory_service is not None
                runtime.semantic_memory_service.save_checkpoint(
                    binding.checkpoint_scope,
                    events=tuple(domain_by_key[item.event_key] for item in durable_before_boundary),
                )

        start_output = hook.handle(
            {
                "hook_event_name": "SessionStart",
                "session_id": session_id,
                "cwd": str(project_directory),
            }
        )
        lifecycle["session_start_count"] += 1
        mr_context: list[str] = []
        start_context = _additional_context(start_output)
        if start_context is not None:
            mr_context.append(start_context)
            lifecycle["delivered_context_tokens"] += _TOKEN_COUNTER.count(start_context)

        for hook_event in cast(list[dict[str, Any]], session["events"]):
            if hook_event["hook_event_name"] != "UserPromptSubmit":
                continue
            purpose = str(hook_event["purpose"])
            if purpose == "changed_memory_need":
                with build_checkpoint_runtime(config) as runtime:
                    assert runtime.semantic_memory_service is not None
                    runtime.semantic_memory_service.save_checkpoint(
                        binding.checkpoint_scope,
                        events=(domain_by_key[changed_event_key],),
                    )
                if changed_spec not in available_events:
                    available_events.append(changed_spec)
            current_input = _purpose_prompt(
                purpose,
                prompt_marker=prompt_marker,
            )
            prompt_output = hook.handle(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": session_id,
                    "cwd": str(project_directory),
                    "prompt": current_input,
                }
            )
            lifecycle["user_prompt_submit_count"] += 1
            emitted = _additional_context(prompt_output)
            emitted_tokens = 0 if emitted is None else _TOKEN_COUNTER.count(emitted)
            if purpose == "self_contained":
                lifecycle["self_contained_attachment_tokens"] += emitted_tokens
            elif emitted is not None:
                lifecycle["detail_delivery_count"] += 1
            if emitted is not None:
                mr_context.append(emitted)
                lifecycle["delivered_context_tokens"] += emitted_tokens

            for condition in _CONDITIONS:
                transport = _condition_transport(
                    condition,
                    scenario,
                    available_events,
                    mr_context,
                )
                model_prompt = _assemble_model_prompt(current_input, transport)
                measured = _TOKEN_COUNTER.count(model_prompt)
                cumulative_tokens[condition] += measured
                prompt_counts[condition] += 1
                if purpose == "prior_memory_need":
                    primary_inputs[condition] = (
                        model_prompt,
                        current_input,
                        measured,
                        required_meaning in model_prompt,
                    )

        if session["events"][-1]["hook_event_name"] == "PreCompact":
            hook.handle(
                {
                    "hook_event_name": "PreCompact",
                    "session_id": session_id,
                    "cwd": str(project_directory),
                }
            )
            lifecycle["precompact_count"] += 1
            if _delivery_keys_cleared(data_directory, session_id):
                lifecycle["precompact_reset_count"] += 1

        if session_number not in horizons:
            continue
        telemetry = LocalAutomaticRouteTelemetryStore(data_directory).events(
            _automatic_route_scope(binding.checkpoint_scope), limit=100
        )
        duplicate_count = sum(int(item.duplicate_render) for item in telemetry)
        schedule = sessions[:session_number]
        schedule_digest = _sha256_text(_canonical_json(schedule))
        primary_current = primary_inputs["NM"][1]
        valid = _memory_necessity_valid(required_meaning, primary_current, schedule)
        captured[session_number] = {}
        for condition in _CONDITIONS:
            model_prompt, current_input, measured, available = primary_inputs[condition]
            scored = bool(session["scored_reuse"])
            required_available: bool | None = available if scored else None
            required_keys: tuple[str, ...] = ()
            if scored and available and condition in {"FH", "RS", "MR"}:
                required_keys = (seed_event_key,)
            condition_lifecycle = {
                **lifecycle,
                "duplicate_suppression_count": duplicate_count,
            }
            if condition != "MR":
                condition_lifecycle = {name: 0 for name in condition_lifecycle}
            captured[session_number][condition] = {
                "schema_version": "mnemo-lifecycle-token-break-even-row/1.0",
                "condition": condition,
                "scenario_family": template_id,
                "horizon": session_number,
                "attempt": 1,
                "scored_reuse": scored,
                "current_input_sha256": _sha256_text(_assemble_model_prompt(current_input, "")),
                "starting_state_sha256": _sha256_text(_STARTING_STATE),
                "lifecycle_schedule_sha256": schedule_digest,
                "prompt_sha256": _sha256_text(model_prompt),
                "downstream_prompt_estimated_tokens": measured,
                "downstream_output_estimated_tokens": 0,
                "cumulative_downstream_prompt_estimated_tokens": cumulative_tokens[condition],
                "downstream_model_call_count": prompt_counts[condition],
                "mnemo_model_tokens": {"input": 0, "output": 0},
                "measurement_source": "tokenizer_estimate",
                "tokenizer_id": _TOKEN_COUNTER.tokenizer_id,
                "memory_necessity_valid": valid,
                "required_prior_fact_available": required_available,
                "required_prior_event_keys": required_keys,
                "lifecycle": condition_lifecycle,
                "local_deterministic_work": {
                    "tokenizer_equivalent_calls": prompt_counts[condition],
                    "model_calls": 0,
                },
                "stored_payload_fields": (),
            }

    return tuple(
        cast(dict[str, Any], captured[horizon][condition])
        for horizon in sorted(captured)
        for condition in _CONDITIONS
    )


def build_offline_rows(
    repository_root: Path,
    work_directory: Path,
    *,
    prompt_marker: str = "",
    response_marker: str = "",
    reasoning_marker: str = "",
) -> tuple[dict[str, Any], ...]:
    """Construct paired rows using real hooks and zero model calls."""

    repository_root = repository_root.resolve()
    fixture_path = repository_root / _FIXTURE
    source_path = repository_root / _SOURCE_CORPUS
    fixture = _load_object(fixture_path)
    corpus = load_corpus(source_path)
    long_scenarios = {
        scenario.template_id: scenario
        for scenario in corpus.scenarios
        if scenario.horizon is Horizon.LONG
    }
    work_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    transient_downstream_values = (response_marker, reasoning_marker)
    if any(not isinstance(value, str) for value in transient_downstream_values):
        raise LifecycleTokenBreakEvenError("transient evaluation marker is invalid")

    rows: list[dict[str, Any]] = []
    for value in cast(list[dict[str, Any]], fixture["scenario_families"]):
        template_id = str(value["template_id"])
        scenario = long_scenarios.get(template_id)
        if scenario is None:
            raise LifecycleTokenBreakEvenError("evaluation scenario is unavailable")
        rows.extend(
            _family_rows(
                work_directory,
                fixture,
                value,
                scenario,
                prompt_marker=prompt_marker,
            )
        )
    return tuple(rows)


def _run_configuration(repository_root: Path, run_id: str) -> dict[str, object]:
    runner = Path(__file__).resolve()
    return {
        "schema_version": "mnemo-lifecycle-token-break-even-config/1.0",
        "run_id": run_id,
        "fixture": _FIXTURE.as_posix(),
        "fixture_sha256": _sha256_file(repository_root / _FIXTURE),
        "source_corpus": _SOURCE_CORPUS.as_posix(),
        "source_corpus_sha256": _sha256_file(repository_root / _SOURCE_CORPUS),
        "runner_sha256": _sha256_file(runner),
        "tokenizer_id": _TOKEN_COUNTER.tokenizer_id,
        "model_calls_authorized": False,
        "model_calls_made": 0,
    }


def _write_json(path: Path, value: object) -> None:
    payload = json.dumps(value, sort_keys=True, indent=2) + "\n"
    with path.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _append_row(path: Path, row: dict[str, Any]) -> None:
    payload = _canonical_json(row) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _read_rows(path: Path) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return ()
    for line in path.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if not isinstance(value, dict):
            raise LifecycleTokenBreakEvenError("raw evaluation row is invalid")
        rows.append(cast(dict[str, Any], value))
    return tuple(rows)


def _row_key(row: dict[str, Any]) -> tuple[str, str, int, int]:
    return (
        str(row["condition"]),
        str(row["scenario_family"]),
        int(row["horizon"]),
        int(row["attempt"]),
    )


def _aggregate(rows: tuple[dict[str, Any], ...]) -> dict[str, object]:
    condition_totals = {
        condition: sum(
            int(row["cumulative_downstream_prompt_estimated_tokens"])
            for row in rows
            if row["condition"] == condition
        )
        for condition in _CONDITIONS
    }
    return {
        "schema_version": "mnemo-lifecycle-token-break-even-aggregate/1.0",
        "evidence_status": "offline_tokenizer_estimate_only",
        "row_count": len(rows),
        "scenario_family_count": len({str(row["scenario_family"]) for row in rows}),
        "conditions": list(_CONDITIONS),
        "horizons": sorted({int(row["horizon"]) for row in rows}),
        "cumulative_prompt_estimated_token_observation_sum": condition_totals,
        "mnemo_model_tokens": {"input": 0, "output": 0},
        "task_quality": "NOT EVALUATED",
    }


def _write_manifest(repository_root: Path, run_directory: Path) -> None:
    artifact_names = (
        "aggregate.json",
        "evaluation-config.json",
        "failures.jsonl",
        "raw-sessions.jsonl",
    )
    manifest = {
        "schema_version": "mnemo-lifecycle-token-break-even-manifest/1.0",
        "artifact_sha256": {name: _sha256_file(run_directory / name) for name in artifact_names},
        "input_sha256": {
            _FIXTURE.as_posix(): _sha256_file(repository_root / _FIXTURE),
            _SOURCE_CORPUS.as_posix(): _sha256_file(repository_root / _SOURCE_CORPUS),
            "scripts/run_lifecycle_token_break_even.py": _sha256_file(Path(__file__).resolve()),
        },
        "excluded_provider_calls": 0,
        "failed_provider_calls": 0,
        "orphaned_provider_calls": 0,
    }
    _write_json(run_directory / "reproducibility-manifest.json", manifest)


def run_offline_evaluation(
    repository_root: Path,
    output_directory: Path,
    *,
    run_id: str,
    resume: bool = False,
    prompt_marker: str = "",
    response_marker: str = "",
    reasoning_marker: str = "",
) -> Path:
    """Create or resume one immutable, append-only offline evaluation run."""

    if not _RUN_ID.fullmatch(run_id):
        raise LifecycleTokenBreakEvenError("run id is invalid")
    repository_root = repository_root.resolve()
    output_directory = output_directory.resolve()
    output_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    run_directory = output_directory / run_id
    configuration = _run_configuration(repository_root, run_id)
    if run_directory.exists():
        if not resume:
            raise LifecycleTokenBreakEvenError("evaluation run already exists")
        existing = _load_object(run_directory / "evaluation-config.json")
        if existing != configuration:
            raise LifecycleTokenBreakEvenError("evaluation resume configuration changed")
    else:
        try:
            run_directory.mkdir(mode=0o700)
        except FileExistsError as error:
            raise LifecycleTokenBreakEvenError("evaluation run already exists") from error
        _write_json(run_directory / "evaluation-config.json", configuration)
        with (run_directory / "raw-sessions.jsonl").open("x", encoding="utf-8"):
            pass
        with (run_directory / "failures.jsonl").open("x", encoding="utf-8"):
            pass

    raw_path = run_directory / "raw-sessions.jsonl"
    completed = {_row_key(row) for row in _read_rows(raw_path)}
    expected_count = 6 * 3 * 4
    if len(completed) < expected_count:
        try:
            with TemporaryDirectory(prefix=".lifecycle-work-", dir=output_directory) as temporary:
                rows = build_offline_rows(
                    repository_root,
                    Path(temporary) / "work",
                    prompt_marker=prompt_marker,
                    response_marker=response_marker,
                    reasoning_marker=reasoning_marker,
                )
        except Exception as error:
            _append_row(
                run_directory / "failures.jsonl",
                {
                    "schema_version": "mnemo-lifecycle-token-break-even-failure/1.0",
                    "error_code": "OFFLINE_CONSTRUCTION_FAILED",
                    "error_type": type(error).__name__,
                },
            )
            raise LifecycleTokenBreakEvenError("offline evaluation construction failed") from None
        for row in rows:
            key = _row_key(row)
            if key in completed:
                continue
            _append_row(raw_path, {**row, "run_id": run_id})
            completed.add(key)

    persisted = _read_rows(raw_path)
    if len(persisted) != expected_count:
        raise LifecycleTokenBreakEvenError("offline evaluation row set is incomplete")
    _write_json(run_directory / "aggregate.json", _aggregate(persisted))
    _write_manifest(repository_root, run_directory)
    return run_directory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-directory", type=Path, default=Path("evaluation-results"))
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    run_directory = run_offline_evaluation(
        args.repository_root,
        args.output_directory,
        run_id=args.run_id,
        resume=args.resume,
    )
    print(run_directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
