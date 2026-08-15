"""Measure actual semantic lifecycle stages separately from token counterfactuals."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import statistics
import subprocess
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from mnemo_memory.packages.application import (
    CreateCheckpoint,
    LocalConfig,
    ReviseCheckpoint,
    SemanticLifecycleObservation,
    build_checkpoint_runtime,
)
from mnemo_memory.packages.domain import (
    CheckpointContent,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    SessionId,
    SourceId,
    SourceTrustClass,
    TaskId,
    VerificationStatus,
    Visibility,
    WorkspaceId,
)

ROOT = Path(__file__).parents[1]
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "evals" / "live-semantic-gate-v1.json"
DEFAULT_RESULTS = ROOT / "evaluation-results" / "semantic-lifecycle-v1"
DEFAULT_GATE1_RUN = (
    ROOT / "evaluation-results" / "live-semantic-v1" / "live-20260812-57ec69f-gate1-002"
)
DEFAULT_OFFLINE_RUN = (
    ROOT / "evaluation-results" / "viability-v1" / "offline-20260812-57ec69f-integrity-001"
)


def aggregate_records(records: list[dict[str, object]]) -> dict[str, object]:
    """Aggregate independent samples without treating nested operations as extra samples."""

    by_operation: dict[str, list[dict[str, object]]] = {}
    for record in records:
        operation = cast(str, record["operation"])
        by_operation.setdefault(operation, []).append(record)
    operations: dict[str, object] = {}
    for operation, values in sorted(by_operation.items()):
        stages = sorted(
            {name for value in values for name in cast(dict[str, int], value["stage_durations_ns"])}
        )
        operations[operation] = {
            "observation_count": len(values),
            "wall_duration_ns": _distribution(
                [cast(int, value["wall_duration_ns"]) for value in values]
            ),
            "deterministic_cpu_ns": _distribution(
                [cast(int, value["deterministic_cpu_ns"]) for value in values]
            ),
            "stages": {
                name: _distribution(
                    [
                        cast(dict[str, int], value["stage_durations_ns"]).get(name, 0)
                        for value in values
                    ]
                )
                for name in stages
            },
            "source_event_count": _distribution(
                [cast(int, value["source_event_count"]) for value in values]
            ),
            "changed_event_count": _distribution(
                [cast(int, value["changed_event_count"]) for value in values]
            ),
            "rendered_tokens": _distribution(
                [cast(int, value["rendered_tokens"]) for value in values]
            ),
            "rendered_bytes": _distribution(
                [cast(int, value["rendered_bytes"]) for value in values]
            ),
        }
    return {"operations": operations}


def _distribution(values: list[int]) -> dict[str, float]:
    ordered = sorted(values)
    p95_index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return {
        "mean": statistics.fmean(ordered),
        "median": statistics.median(ordered),
        "p95": float(ordered[p95_index]),
        "minimum": float(ordered[0]),
        "maximum": float(ordered[-1]),
    }


def _scope(sample: int) -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string(f"11000000-0000-4000-8000-{sample:012d}"),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.from_string(f"22000000-0000-4000-8000-{sample:012d}"),
        ProjectId.from_string(f"33000000-0000-4000-8000-{sample:012d}"),
        SessionId.from_string(f"44000000-0000-4000-8000-{sample:012d}"),
        TaskId.from_string(f"55000000-0000-4000-8000-{sample:012d}"),
    )


def _evidence(sample: int) -> EvidenceReference:
    source = f"fixture://semantic-lifecycle/{sample}"
    return EvidenceReference(
        EvidenceId.from_string(f"66000000-0000-4000-8000-{sample:012d}"),
        SourceId.from_string(f"77000000-0000-4000-8000-{sample:012d}"),
        EvidenceSourceType.AGENT_EVENT,
        SourceTrustClass.APPROVED_CHECKPOINT,
        source,
        "sha256:" + f"{sample:064x}",
        EvidenceLocation(source),
        datetime(2026, 8, 12, tzinfo=UTC),
        VerificationStatus.VERIFIED,
    )


def _content(value: dict[str, Any]) -> CheckpointContent:
    return CheckpointContent(
        task_objective=value["task_objective"],
        completed_work=tuple(value.get("completed_work", ())),
        current_state=value["current_state"],
        remaining_work=tuple(value.get("remaining_work", ())),
        decisions=tuple(value.get("decisions", ())),
        failures=tuple(value.get("failures", ())),
        blockers=tuple(value.get("blockers", ())),
        relevant_files=("scheduler/service.py",),
        relevant_artifacts=(),
        verification_performed=tuple(value.get("verification_performed", ())),
        token_estimate=600,
    )


def _append(path: Path, value: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _write_exclusive(path: Path, value: object) -> None:
    text = value if isinstance(value, str) else json.dumps(value, indent=2, sort_keys=True) + "\n"
    with path.open("x", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*arguments: str) -> str:
    return subprocess.run(
        ("git", *arguments), cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def _actual_model_lifecycle(gate1_run: Path) -> dict[str, object]:
    raw = gate1_run / "raw-events.jsonl"
    record = next(
        value
        for value in (json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines())
        if value["kind"] == "fresh_agent_continuation"
    )
    usage = cast(dict[str, int], record["actual_usage"])
    return {
        "classification": "actually_observed",
        "model_input_tokens": usage["prompt_eval_count"],
        "model_output_tokens": usage["eval_count"],
        "continuation_duration_ns": record["request_duration_ns"],
        "local_inference_duration_ns": usage["prompt_eval_duration"] + usage["eval_duration"],
        "load_duration_ns": usage["load_duration"],
        "human_intervention_count": 0,
        "external_spend_usd": 0.0,
        "source_run": gate1_run.name,
        "source_sha256": _sha256(raw),
    }


def _counterfactual_token_estimates(offline_run: Path) -> dict[str, object]:
    selected = []
    for line in (offline_run / "raw-runs.jsonl").read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if value["condition"] == "M3_mnemo_adaptive_retrieval" and value["available"]:
            selected.append(cast(dict[str, int], value["token_account"]))
    fields = (
        "checkpoint_save_input",
        "checkpoint_save_output",
        "validation",
        "checkpoint_recall",
        "retrieval_query",
        "retrieved_evidence",
        "total",
    )
    return {
        "classification": "estimated",
        "warning": (
            "deterministic serialization and validation text were converted to hypothetical "
            "tokens; these are not observed model usage, billing, or lifecycle work"
        ),
        "condition": "M3_mnemo_adaptive_retrieval",
        "row_count": len(selected),
        "mean_tokens": {name: statistics.fmean(item[name] for item in selected) for name in fields},
        "source_run": offline_run.name,
        "source_sha256": _sha256(offline_run / "raw-runs.jsonl"),
    }


def run(
    *,
    run_id: str,
    repetitions: int,
    fixture_path: Path = DEFAULT_FIXTURE,
    results_root: Path = DEFAULT_RESULTS,
    gate1_run: Path = DEFAULT_GATE1_RUN,
    offline_run: Path = DEFAULT_OFFLINE_RUN,
) -> tuple[Path, dict[str, object]]:
    if not 1 <= repetitions <= 1_000:
        raise ValueError("lifecycle repetitions must be between 1 and 1000")
    fixture = cast(dict[str, Any], json.loads(fixture_path.read_text(encoding="utf-8")))
    checkpoint = cast(dict[str, dict[str, Any]], fixture["checkpoint"])
    destination = results_root / run_id
    destination.mkdir(parents=True, exist_ok=False)
    raw = destination / "raw-lifecycle.jsonl"
    failures = destination / "failures.jsonl"
    raw.touch(exist_ok=False)
    failures.touch(exist_ok=False)
    records: list[dict[str, object]] = []
    try:
        for sample in range(1, repetitions + 1):
            observations: list[SemanticLifecycleObservation] = []
            with (
                tempfile.TemporaryDirectory(prefix=f"mnemo-lifecycle-{sample}-") as temporary,
                build_checkpoint_runtime(
                    LocalConfig.defaults(Path(temporary)),
                    semantic_lifecycle_observer=observations.append,
                ) as runtime,
            ):
                assert runtime.semantic_memory_service is not None
                scope = _scope(sample)
                evidence = (_evidence(sample),)
                initial = runtime.checkpoint_service.create(
                    CreateCheckpoint(scope, _content(checkpoint["create"]), evidence)
                )
                runtime.semantic_memory_service.save_checkpoint_view(initial, retention_days=180)
                revised = runtime.checkpoint_service.revise(
                    ReviseCheckpoint(
                        scope,
                        initial.aggregate.checkpoint_id,
                        initial.revision.revision_id,
                        replace(
                            _content(checkpoint["revise"]),
                            token_estimate=initial.revision.content.token_estimate,
                        ),
                        evidence,
                    )
                )
                runtime.semantic_memory_service.save_checkpoint_view(revised, retention_days=180)
                first, _ = runtime.semantic_memory_service.automatic_context_item(
                    scope, preferred_token_target=400, maximum_token_ceiling=600
                )
                second, _ = runtime.semantic_memory_service.automatic_context_item(
                    scope, preferred_token_target=400, maximum_token_ceiling=600
                )
                if first.content != second.content:
                    raise RuntimeError("semantic lifecycle retrieval drifted")
            for index, observation in enumerate(observations, start=1):
                record = {
                    "schema_version": "mnemo-semantic-lifecycle-record/1.0",
                    "run_id": run_id,
                    "sample": sample,
                    "operation_index": index,
                    "classification": "deterministically_measured",
                    **observation.to_dict(),
                }
                records.append(record)
                _append(raw, record)

        aggregate = aggregate_records(records)
        summary: dict[str, object] = {
            "schema_version": "mnemo-semantic-lifecycle-summary/1.0",
            "run_id": run_id,
            "sample_count": repetitions,
            "independence_unit": "fresh SQLite profile and exact-scope checkpoint lifecycle",
            "aggregate": aggregate,
            "actual_model_lifecycle": _actual_model_lifecycle(gate1_run),
            "counterfactual_token_estimates": _counterfactual_token_estimates(offline_run),
            "interpretation": (
                "deterministic stage timings are actual local elapsed/CPU measurements; only the "
                "Gate 1 runtime counts are actual model tokens; prior token-account rows remain "
                "counterfactual estimates"
            ),
        }
        _write_exclusive(destination / "summary.json", summary)
        _write_exclusive(destination / "report.md", _render_report(summary))
        artifacts = sorted(
            path.relative_to(destination).as_posix()
            for path in destination.rglob("*")
            if path.is_file() and path.name != "reproducibility-manifest.json"
        )
        manifest = {
            "schema_version": "mnemo-semantic-lifecycle-reproducibility/1.0",
            "run_id": run_id,
            "git_revision": _git("rev-parse", "HEAD"),
            "worktree_status_sha256": "sha256:"
            + hashlib.sha256(_git("status", "--porcelain=v1").encode()).hexdigest(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "fixture": {
                "path": fixture_path.relative_to(ROOT).as_posix(),
                "sha256": _sha256(fixture_path),
            },
            "runner": {
                "path": Path(__file__).relative_to(ROOT).as_posix(),
                "sha256": _sha256(Path(__file__)),
            },
            "sample_count": repetitions,
            "source_runs": {
                "gate1": gate1_run.name,
                "offline_counterfactual": offline_run.name,
            },
            "commands": [
                "uv run python -m scripts.run_semantic_lifecycle_benchmark "
                f"--run-id {run_id} --repetitions {repetitions}",
                "uv run pytest -q tests/evals/test_semantic_lifecycle_benchmark.py "
                "tests/unit/test_semantic_checkpoints.py",
                "npm run check",
            ],
            "artifact_hashes": {name: _sha256(destination / name) for name in artifacts},
            "raw_log_policy": (
                "exclusive-create run directory; per-operation records append and fsync"
            ),
        }
        _write_exclusive(destination / "reproducibility-manifest.json", manifest)
        return destination, summary
    except Exception as error:
        _append(
            failures,
            {
                "schema_version": "mnemo-semantic-lifecycle-failure/1.0",
                "observed_at": datetime.now(UTC).isoformat(),
                "exception_type": type(error).__name__,
                "message": str(error),
                "completed_record_count": len(records),
            },
        )
        raise


def _render_report(summary: dict[str, object]) -> str:
    actual = cast(dict[str, object], summary["actual_model_lifecycle"])
    estimated = cast(dict[str, object], summary["counterfactual_token_estimates"])
    return f"""# Semantic lifecycle measurement

- Run: `{summary["run_id"]}`
- Independent fresh profiles: `{summary["sample_count"]}`
- Deterministic measurements: actual monotonic elapsed time and process CPU
- Actual model input/output: `{actual["model_input_tokens"]}` /
  `{actual["model_output_tokens"]}` tokens
- Local inference time: `{actual["local_inference_duration_ns"]}` ns
- Human interventions: `{actual["human_intervention_count"]}`
- External spend: `${actual["external_spend_usd"]}`

The per-operation breakdown is in `summary.json`; every underlying observation is append-only in
`raw-lifecycle.jsonl`. Memory creation, validation, serialization, retrieval, context assembly,
repair, and persistence are separate stages. Model input, output, continuation, and inference come
only from the real local Gate 1 model call and are not attributed to deterministic Mnemo CPU work.

The earlier M3 lifecycle token account is retained only as **{estimated["classification"]}**
counterfactual cost. Its deterministic JSON/token estimates are not actual model tokens or spend.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repetitions", type=int, default=30)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--gate1-run", type=Path, default=DEFAULT_GATE1_RUN)
    parser.add_argument("--offline-run", type=Path, default=DEFAULT_OFFLINE_RUN)
    arguments = parser.parse_args(argv)
    destination, summary = run(
        run_id=arguments.run_id,
        repetitions=arguments.repetitions,
        fixture_path=arguments.fixture,
        results_root=arguments.results_root,
        gate1_run=arguments.gate1_run,
        offline_run=arguments.offline_run,
    )
    print(json.dumps({"destination": str(destination), "summary": summary}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
