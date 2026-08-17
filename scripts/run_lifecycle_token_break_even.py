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
from collections.abc import Iterator
from contextlib import contextmanager
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
from mnemo_memory.packages.application.evaluation.analysis import descriptive_statistics
from mnemo_memory.packages.application.evaluation.conditions import (
    build_domain_events,
    render_event,
    rolling_natural_language_summary,
)
from mnemo_memory.packages.application.evaluation.graders import DeterministicContinuationGrader
from mnemo_memory.packages.application.evaluation.models import (
    EventSpec,
    GroundTruth,
    Horizon,
    Scenario,
    load_corpus,
)
from mnemo_memory.packages.application.semantic_rendering import (
    ConservativeTokenCounter,
    detect_protected_spans,
)
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
_CROSS_SCOPE_CANARY = "CROSS-SCOPE-CANARY"


class LifecycleTokenBreakEvenError(RuntimeError):
    """Safe evaluation failure that contains no transient payload."""


@contextmanager
def isolated_evaluation_work_directory() -> Iterator[Path]:
    """Yield system-temporary work that cannot inherit an enclosing evaluated Git root."""

    with TemporaryDirectory(prefix="mnemo-lifecycle-work-") as temporary:
        work_directory = Path(temporary).resolve() / "work"
        if any((parent / ".git").exists() for parent in work_directory.parents):
            raise LifecycleTokenBreakEvenError("evaluation work directory is not isolated")
        yield work_directory


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


def _probe_ground_truth(required_meaning: str, evidence_key: str) -> GroundTruth:
    """Build a condition-blind one-fact probe from the referenced source event."""

    return GroundTruth(
        required_active_facts=(required_meaning,),
        required_constraints=(),
        current_goals=(),
        current_decisions=(),
        superseded_decisions=(),
        failed_approaches=(),
        unresolved_questions=(),
        next_actions=(),
        protected_spans=tuple(span.value for span in detect_protected_spans(required_meaning)),
        relevant_evidence=(evidence_key,),
        critical_evidence=(evidence_key,),
        irrelevant_or_obsolete=(),
        expected_continuation=(),
        critical_facts=(required_meaning,),
        forbidden_facts=(_CROSS_SCOPE_CANARY,),
        retrieval_required=(required_meaning,),
    )


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
    probe_truth = _probe_ground_truth(required_meaning, seed_event_key)
    grader = DeterministicContinuationGrader()

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
        "duplicate_tokens_avoided": 0,
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
        prior_delivery_tokens = 0

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
            if purpose == "prior_memory_need" and emitted is not None:
                prior_delivery_tokens = emitted_tokens
            elif purpose in {"repeat_prior_memory_need", "changed_memory_need"} and emitted is None:
                lifecycle["duplicate_tokens_avoided"] += prior_delivery_tokens
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
            evidence_keys = required_keys
            full_history_prompt = _assemble_model_prompt(
                current_input,
                _full_history(available_events),
            )
            grade = grader.grade(
                context=model_prompt,
                evidence_event_keys=evidence_keys,
                ground_truth=probe_truth,
                context_tokens=measured,
                full_history_tokens=_TOKEN_COUNTER.count(full_history_prompt),
                deterministic=True,
                drift_resistance=1.0,
                omission_notice_valid=True,
            )
            cross_scope_disclosure_count = int(_CROSS_SCOPE_CANARY in model_prompt)
            false_memory_count = sum(
                int(value in model_prompt) for value in probe_truth.forbidden_facts
            )
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
                "cumulative_downstream_output_estimated_tokens": 0,
                "downstream_model_call_count": prompt_counts[condition],
                "mnemo_model_tokens": {"input": 0, "output": 0},
                "measurement_source": "tokenizer_estimate",
                "tokenizer_id": _TOKEN_COUNTER.tokenizer_id,
                "memory_necessity_valid": valid,
                "required_prior_fact_available": required_available,
                "required_prior_event_keys": required_keys,
                "required_knowledge_retention": (
                    grade.required_knowledge_retention if scored else None
                ),
                "protected_literal_fidelity": grade.protected_span_fidelity if scored else None,
                "evidence_attribution_fidelity": (
                    grade.evidence_attribution_fidelity if scored else None
                ),
                "temporal_supersession_accuracy": (
                    grade.temporal_supersession_accuracy if scored else None
                ),
                "critical_false_memory_count": false_memory_count,
                "cross_scope_disclosure_count": cross_scope_disclosure_count,
                "critical_violation_count": len(grade.critical_violations),
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


def _write_text(path: Path, value: str) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(value)
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


def _nonnegative_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{label} must be a non-negative number")
    return float(value)


def model_token_savings(
    baseline_input: int | float,
    baseline_output: int | float,
    candidate_input: int | float,
    candidate_output: int | float,
) -> dict[str, float | None]:
    """Return input-only and total savings without inventing a zero denominator."""

    baseline_input_value = _nonnegative_number(baseline_input, "baseline input")
    baseline_output_value = _nonnegative_number(baseline_output, "baseline output")
    candidate_input_value = _nonnegative_number(candidate_input, "candidate input")
    candidate_output_value = _nonnegative_number(candidate_output, "candidate output")
    baseline_total = baseline_input_value + baseline_output_value
    candidate_total = candidate_input_value + candidate_output_value
    return {
        "model_input_savings": (
            None
            if baseline_input_value == 0
            else 1.0 - candidate_input_value / baseline_input_value
        ),
        "total_model_token_savings": (
            None if baseline_total == 0 else 1.0 - candidate_total / baseline_total
        ),
    }


def tokens_per_success(total_tokens: int | float, successful_tasks: int) -> float | None:
    total = _nonnegative_number(total_tokens, "total tokens")
    if isinstance(successful_tasks, bool) or not isinstance(successful_tasks, int):
        raise TypeError("successful task count must be an integer")
    if successful_tasks < 0:
        raise ValueError("successful task count cannot be negative")
    return None if successful_tasks == 0 else total / successful_tasks


def provider_call_accounting(
    expected_keys: tuple[tuple[str, str, int, int], ...],
    calls: tuple[dict[str, object], ...],
) -> dict[str, int]:
    """Count included, failed, and unexpected provider calls without prompt payloads."""

    expected = set(expected_keys)
    included = 0
    failed = 0
    orphaned = 0
    for call in calls:
        raw_key = call.get("key")
        key = tuple(raw_key) if isinstance(raw_key, (list, tuple)) else ()
        if key not in expected:
            orphaned += 1
        elif call.get("status") == "included":
            included += 1
        else:
            failed += 1
    return {
        "expected": len(expected),
        "included": included,
        "failed": failed,
        "orphaned": orphaned,
    }


def _paired_token_totals(
    rows: tuple[dict[str, Any], ...],
    *,
    horizon: int,
    source: str,
) -> dict[str, dict[str, int]] | None:
    selected = tuple(
        row
        for row in rows
        if int(row.get("horizon", -1)) == horizon and row.get("condition") in {"FH", "MR"}
    )
    by_condition: dict[str, dict[tuple[str, int], dict[str, Any]]] = {"FH": {}, "MR": {}}
    for row in selected:
        condition = str(row["condition"])
        key = (str(row["scenario_family"]), int(row.get("attempt", 1)))
        if key in by_condition[condition]:
            return None
        by_condition[condition][key] = row
    if not by_condition["FH"] or set(by_condition["FH"]) != set(by_condition["MR"]):
        return None
    input_field = f"downstream_prompt_{source}_tokens"
    output_field = f"downstream_output_{source}_tokens"
    if source == "estimated" and all(
        "cumulative_downstream_prompt_estimated_tokens" in row
        and "cumulative_downstream_output_estimated_tokens" in row
        for condition_rows in by_condition.values()
        for row in condition_rows.values()
    ):
        input_field = "cumulative_downstream_prompt_estimated_tokens"
        output_field = "cumulative_downstream_output_estimated_tokens"
    if source == "actual" and any(
        row.get("provider_call_status") != "included"
        or input_field not in row
        or output_field not in row
        for condition_rows in by_condition.values()
        for row in condition_rows.values()
    ):
        return None
    if any(
        input_field not in row or output_field not in row
        for condition_rows in by_condition.values()
        for row in condition_rows.values()
    ):
        return None
    totals: dict[str, dict[str, int]] = {}
    for condition, condition_rows in by_condition.items():
        input_tokens = sum(int(row[input_field]) for row in condition_rows.values())
        output_tokens = sum(int(row[output_field]) for row in condition_rows.values())
        totals[condition] = {
            "input": input_tokens,
            "output": output_tokens,
            "total": input_tokens + output_tokens,
        }
    return totals


def _token_source_analysis(rows: tuple[dict[str, Any], ...], source: str) -> dict[str, object]:
    horizons = sorted(
        {
            int(row["horizon"])
            for row in rows
            if row.get("condition") in {"FH", "MR"} and "horizon" in row
        }
    )
    by_horizon: dict[str, dict[str, object]] = {}
    break_even: int | None = None
    for horizon in horizons:
        totals = _paired_token_totals(rows, horizon=horizon, source=source)
        if totals is None:
            continue
        savings = model_token_savings(
            totals["FH"]["input"],
            totals["FH"]["output"],
            totals["MR"]["input"],
            totals["MR"]["output"],
        )
        total_savings = savings["total_model_token_savings"]
        if break_even is None and total_savings is not None and total_savings >= 0:
            break_even = horizon
        by_horizon[str(horizon)] = {
            "FH": totals["FH"],
            "MR": totals["MR"],
            **savings,
            "paired_lifecycle_tes": total_savings,
        }
    long_horizon = by_horizon.get(str(max(horizons))) if horizons else None
    return {
        "measurement_source": source,
        "by_horizon": by_horizon,
        "observed_break_even_horizon": break_even,
        "long_horizon": long_horizon,
    }


def _family_sensitivity(rows: tuple[dict[str, Any], ...]) -> dict[str, object]:
    if not rows:
        return descriptive_statistics((), bootstrap_samples=2_000, seed=20260818)
    horizon = max(int(row["horizon"]) for row in rows)
    selected = tuple(
        row
        for row in rows
        if int(row["horizon"]) == horizon and row.get("condition") in {"FH", "MR"}
    )
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for row in selected:
        grouped.setdefault(str(row["scenario_family"]), {})[str(row["condition"])] = row
    values: list[float] = []
    families: list[str] = []
    for family in sorted(grouped):
        conditions = grouped[family]
        if set(conditions) != {"FH", "MR"}:
            continue
        fh = conditions["FH"]
        mr = conditions["MR"]
        savings = model_token_savings(
            int(
                fh.get(
                    "cumulative_downstream_prompt_estimated_tokens",
                    fh["downstream_prompt_estimated_tokens"],
                )
            ),
            int(
                fh.get(
                    "cumulative_downstream_output_estimated_tokens",
                    fh["downstream_output_estimated_tokens"],
                )
            ),
            int(
                mr.get(
                    "cumulative_downstream_prompt_estimated_tokens",
                    mr["downstream_prompt_estimated_tokens"],
                )
            ),
            int(
                mr.get(
                    "cumulative_downstream_output_estimated_tokens",
                    mr["downstream_output_estimated_tokens"],
                )
            ),
        )["total_model_token_savings"]
        if savings is None:
            continue
        values.append(savings)
        families.append(family)
    result = descriptive_statistics(
        tuple(values),
        bootstrap_samples=2_000,
        seed=20260818,
        cluster_ids=tuple(families),
    )
    result["interpretation"] = (
        "descriptive sensitivity over scenario families; no population inference"
    )
    return result


def _availability_control_valid(rows: tuple[dict[str, Any], ...]) -> tuple[bool, bool]:
    scored = tuple(row for row in rows if row.get("required_prior_fact_available") is not None)
    grouped: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    for row in scored:
        grouped.setdefault((str(row["scenario_family"]), int(row["horizon"])), {})[
            str(row["condition"])
        ] = row
    complete_groups = tuple(
        conditions for conditions in grouped.values() if {"FH", "MR", "NM"} <= set(conditions)
    )
    if not complete_groups:
        return False, False
    valid = all(
        conditions["FH"]["required_prior_fact_available"] is True
        and conditions["MR"]["required_prior_fact_available"] is True
        and conditions["NM"]["required_prior_fact_available"] is False
        for conditions in complete_groups
    )
    return True, valid


def decide_lifecycle_verdict(
    rows: tuple[dict[str, Any], ...],
    *,
    task_quality: dict[str, float] | None = None,
) -> dict[str, object]:
    """Apply preregistered gates without upgrading estimated evidence to provider evidence."""

    if not rows:
        return {
            "verdict": "NOT EVALUATED",
            "task_quality": "NOT EVALUATED",
            "provider_token_counts_complete": False,
        }
    if any(row.get("memory_necessity_valid") is not True for row in rows):
        return {
            "verdict": "INVALID",
            "task_quality": "NOT EVALUATED" if task_quality is None else "EVALUATED",
            "provider_token_counts_complete": False,
        }
    control_complete, control_valid = _availability_control_valid(rows)
    if control_complete and not control_valid:
        return {
            "verdict": "INVALID",
            "task_quality": "NOT EVALUATED" if task_quality is None else "EVALUATED",
            "provider_token_counts_complete": False,
        }
    if not control_complete:
        return {
            "verdict": "NOT EVALUATED",
            "task_quality": "NOT EVALUATED" if task_quality is None else "EVALUATED",
            "provider_token_counts_complete": False,
        }

    scored_memory = tuple(
        row
        for row in rows
        if row.get("condition") in {"FH", "MR"}
        and row.get("required_prior_fact_available") is not None
    )
    fidelity_gate = bool(scored_memory) and all(
        row.get("required_knowledge_retention") == 1.0
        and row.get("protected_literal_fidelity") == 1.0
        and row.get("evidence_attribution_fidelity") == 1.0
        for row in scored_memory
    )
    safety_gate = all(
        int(row.get("critical_false_memory_count", 0)) == 0
        and int(row.get("cross_scope_disclosure_count", 0)) == 0
        for row in rows
    )
    quality_status = "NOT EVALUATED"
    quality_gate = True
    if task_quality is not None:
        if set(task_quality) != {"FH", "MR"} or any(
            not 0.0 <= value <= 1.0 for value in task_quality.values()
        ):
            raise ValueError("task quality must contain normalized FH and MR accuracy")
        quality_status = "PASS" if task_quality["MR"] >= task_quality["FH"] - 0.02 else "FAIL"
        quality_gate = quality_status == "PASS"

    provider_rows = tuple(row for row in rows if row.get("condition") in {"FH", "MR"})
    provider_complete = bool(provider_rows) and all(
        row.get("provider_call_status") == "included"
        and "downstream_prompt_actual_tokens" in row
        and "downstream_output_actual_tokens" in row
        for row in provider_rows
    )
    source = "actual" if provider_complete else "estimated"
    token_analysis = _token_source_analysis(rows, source)
    long_horizon = cast(dict[str, object] | None, token_analysis["long_horizon"])
    savings = None if long_horizon is None else long_horizon.get("total_model_token_savings")
    savings_gate = isinstance(savings, (int, float)) and savings >= 0.30
    gates_pass = fidelity_gate and safety_gate and quality_gate and savings_gate
    verdict = (
        ("PASS" if gates_pass else "FAIL")
        if provider_complete
        else ("PROVISIONAL" if gates_pass else "FAIL")
    )
    return {
        "verdict": verdict,
        "task_quality": quality_status,
        "provider_token_counts_complete": provider_complete,
        "savings_gate": savings_gate,
        "fidelity_gate": fidelity_gate,
        "safety_gate": safety_gate,
        "quality_gate": quality_gate,
    }


def analyze_lifecycle_rows(rows: tuple[dict[str, Any], ...]) -> dict[str, object]:
    """Aggregate paired lifecycle observations at the scenario-family independence unit."""

    horizons = sorted({int(row["horizon"]) for row in rows if "horizon" in row})
    long_horizon = max(horizons) if horizons else None
    long_rows = tuple(row for row in rows if row.get("horizon") == long_horizon)
    mr_long = tuple(row for row in long_rows if row.get("condition") == "MR")
    mnemo_model_tokens = {
        "input": sum(
            int(cast(dict[str, Any], row["mnemo_model_tokens"])["input"]) for row in long_rows
        ),
        "output": sum(
            int(cast(dict[str, Any], row["mnemo_model_tokens"])["output"]) for row in long_rows
        ),
    }
    local_work = {
        "tokenizer_equivalent_calls": sum(
            int(cast(dict[str, Any], row["local_deterministic_work"])["tokenizer_equivalent_calls"])
            for row in long_rows
        ),
        "model_calls": sum(
            int(cast(dict[str, Any], row["local_deterministic_work"])["model_calls"])
            for row in long_rows
        ),
    }
    result: dict[str, object] = {
        "row_count": len(rows),
        "scenario_family_count": len({str(row["scenario_family"]) for row in rows}),
        "conditions": sorted({str(row["condition"]) for row in rows}),
        "horizons": horizons,
        "estimated": _token_source_analysis(rows, "estimated"),
        "actual": _token_source_analysis(rows, "actual"),
        "delivered_context_tokens": sum(
            int(cast(dict[str, Any], row["lifecycle"]).get("delivered_context_tokens", 0))
            for row in mr_long
        ),
        "duplicate_tokens_avoided": sum(
            int(cast(dict[str, Any], row["lifecycle"]).get("duplicate_tokens_avoided", 0))
            for row in mr_long
        ),
        "mnemo_model_tokens": mnemo_model_tokens,
        "local_deterministic_work": local_work,
        "paired_family_sensitivity": _family_sensitivity(rows),
        "provider_call_accounting": provider_call_accounting(
            tuple(
                (
                    str(row["scenario_family"]),
                    str(row["condition"]),
                    int(row["horizon"]),
                    int(row.get("attempt", 1)),
                )
                for row in rows
                if row.get("condition") in {"FH", "MR"}
            ),
            tuple(
                {
                    "key": (
                        str(row["scenario_family"]),
                        str(row["condition"]),
                        int(row["horizon"]),
                        int(row.get("attempt", 1)),
                    ),
                    "status": str(row["provider_call_status"]),
                }
                for row in rows
                if row.get("condition") in {"FH", "MR"} and "provider_call_status" in row
            ),
        ),
    }
    result.update(decide_lifecycle_verdict(rows))
    return result


def render_lifecycle_report(analysis: dict[str, object]) -> str:
    """Render one concise report whose labels preserve the evidence boundary."""

    estimated = cast(dict[str, object], analysis["estimated"])
    actual = cast(dict[str, object], analysis["actual"])
    estimated_long = estimated.get("long_horizon")
    actual_long = actual.get("long_horizon")
    sensitivity = cast(dict[str, object], analysis["paired_family_sensitivity"])
    return (
        "# Lifecycle token break-even\n\n"
        f"Verdict: **{analysis['verdict']}**\n\n"
        "| Measurement | Result |\n"
        "|---|---|\n"
        f"| Delivered context tokens | {analysis['delivered_context_tokens']} |\n"
        f"| Downstream estimated model tokens | {_canonical_json(estimated_long)} |\n"
        f"| Downstream actual provider tokens | "
        f"{'NOT EVALUATED' if actual_long is None else _canonical_json(actual_long)} |\n"
        f"| Mnemo model tokens | {_canonical_json(analysis['mnemo_model_tokens'])} |\n"
        f"| Local deterministic work | {_canonical_json(analysis['local_deterministic_work'])} |\n"
        f"| Provider call accounting | {_canonical_json(analysis['provider_call_accounting'])} |\n"
        f"| Deterministic quality proxy | required-fact and fidelity gates: "
        f"{analysis.get('fidelity_gate', 'NOT EVALUATED')} |\n"
        f"| Model-generated task quality | {analysis['task_quality']} |\n\n"
        "The paired family interval is a descriptive sensitivity summary over the six "
        "preregistered scenario families, not a population inference. "
        f"Interval: {_canonical_json(sensitivity.get('confidence_interval_95'))}.\n"
    )


def _aggregate(rows: tuple[dict[str, Any], ...]) -> dict[str, object]:
    return {
        "schema_version": "mnemo-lifecycle-token-break-even-aggregate/1.0",
        **analyze_lifecycle_rows(rows),
    }


def _write_manifest(repository_root: Path, run_directory: Path) -> None:
    artifact_names = (
        "aggregate.json",
        "evaluation-config.json",
        "failures.jsonl",
        "raw-sessions.jsonl",
        "report.md",
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
            with isolated_evaluation_work_directory() as work_directory:
                rows = build_offline_rows(
                    repository_root,
                    work_directory,
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
    aggregate = _aggregate(persisted)
    _write_json(run_directory / "aggregate.json", aggregate)
    _write_text(run_directory / "report.md", render_lifecycle_report(aggregate))
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
