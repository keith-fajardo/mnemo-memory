"""Compare two append-only semantic lifecycle runs with paired profile bootstrap intervals."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import statistics
import subprocess
from pathlib import Path
from typing import cast

ROOT = Path(__file__).parents[1]
DEFAULT_RESULTS = ROOT / "evaluation-results" / "semantic-lifecycle-v1"
TOP_LEVEL_OPERATIONS = frozenset({"checkpoint_view_save", "automatic_context_assembly"})


def paired_comparison(
    before: list[dict[str, object]],
    after: list[dict[str, object]],
    *,
    seed: int,
    bootstrap_iterations: int,
) -> dict[str, object]:
    if bootstrap_iterations < 1:
        raise ValueError("bootstrap iterations must be positive")
    before_profiles = _profile_metrics(before)
    after_profiles = _profile_metrics(after)
    profile_ids = sorted(set(before_profiles) & set(after_profiles))
    if not profile_ids or set(before_profiles) != set(after_profiles):
        raise ValueError("lifecycle runs do not have identical paired profiles")
    metric_names = sorted(
        set.intersection(
            *(set(before_profiles[item]) & set(after_profiles[item]) for item in profile_ids)
        )
    )
    rng = random.Random(seed)
    comparisons: dict[str, object] = {}
    for name in metric_names:
        before_values = [before_profiles[item][name] for item in profile_ids]
        after_values = [after_profiles[item][name] for item in profile_ids]
        differences = [
            after - prior for prior, after in zip(before_values, after_values, strict=True)
        ]
        relative = [
            0.0 if prior == 0 else (after - prior) / prior
            for prior, after in zip(before_values, after_values, strict=True)
        ]
        boot_absolute: list[float] = []
        boot_relative: list[float] = []
        for _ in range(bootstrap_iterations):
            indices = [rng.randrange(len(profile_ids)) for _ in profile_ids]
            boot_absolute.append(statistics.fmean(differences[index] for index in indices))
            boot_relative.append(statistics.fmean(relative[index] for index in indices))
        comparisons[name] = {
            "before_mean": statistics.fmean(before_values),
            "before_median": statistics.median(before_values),
            "after_mean": statistics.fmean(after_values),
            "after_median": statistics.median(after_values),
            "paired_mean_difference": statistics.fmean(differences),
            "paired_mean_difference_ci95": _percentile_interval(boot_absolute),
            "paired_mean_relative_change": statistics.fmean(relative),
            "paired_mean_relative_change_ci95": _percentile_interval(boot_relative),
            "paired_cohens_dz": _cohens_dz(differences),
            "lower_is_better": True,
        }
    return {
        "independence_unit": "fresh SQLite profile",
        "paired_profile_count": len(profile_ids),
        "bootstrap_iterations": bootstrap_iterations,
        "bootstrap_seed": seed,
        "metrics": comparisons,
    }


def _profile_metrics(records: list[dict[str, object]]) -> dict[int, dict[str, float]]:
    profiles: dict[int, dict[str, float]] = {}
    for record in records:
        sample = cast(int, record["sample"])
        operation = cast(str, record["operation"])
        values = profiles.setdefault(sample, {})
        prefix = operation
        values[f"{prefix}.wall_duration_ns"] = values.get(f"{prefix}.wall_duration_ns", 0.0) + cast(
            int, record["wall_duration_ns"]
        )
        values[f"{prefix}.deterministic_cpu_ns"] = values.get(
            f"{prefix}.deterministic_cpu_ns", 0.0
        ) + cast(int, record["deterministic_cpu_ns"])
        for stage, duration in cast(dict[str, int], record["stage_durations_ns"]).items():
            name = f"{prefix}.stage.{stage}_ns"
            values[name] = values.get(name, 0.0) + duration
        if operation in TOP_LEVEL_OPERATIONS:
            values["top_level.wall_duration_ns"] = values.get(
                "top_level.wall_duration_ns", 0.0
            ) + cast(int, record["wall_duration_ns"])
            values["top_level.deterministic_cpu_ns"] = values.get(
                "top_level.deterministic_cpu_ns", 0.0
            ) + cast(int, record["deterministic_cpu_ns"])
    return profiles


def _percentile_interval(values: list[float]) -> list[float]:
    ordered = sorted(values)
    lower = max(0, math.floor(0.025 * (len(ordered) - 1)))
    upper = min(len(ordered) - 1, math.ceil(0.975 * (len(ordered) - 1)))
    return [ordered[lower], ordered[upper]]


def _cohens_dz(differences: list[float]) -> float | None:
    if len(differences) < 2:
        return None
    deviation = statistics.stdev(differences)
    return None if deviation == 0 else statistics.fmean(differences) / deviation


def _load(path: Path) -> list[dict[str, object]]:
    return [
        cast(dict[str, object], json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def _write_exclusive(path: Path, value: object) -> None:
    text = value if isinstance(value, str) else json.dumps(value, indent=2, sort_keys=True) + "\n"
    with path.open("x", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def run(
    *,
    comparison_id: str,
    before_run: Path,
    after_run: Path,
    results_root: Path = DEFAULT_RESULTS,
    seed: int = 8122026,
    bootstrap_iterations: int = 10_000,
) -> tuple[Path, dict[str, object]]:
    before_raw = before_run / "raw-lifecycle.jsonl"
    after_raw = after_run / "raw-lifecycle.jsonl"
    comparison = paired_comparison(
        _load(before_raw),
        _load(after_raw),
        seed=seed,
        bootstrap_iterations=bootstrap_iterations,
    )
    destination = results_root / comparison_id
    destination.mkdir(parents=True, exist_ok=False)
    result = {
        "schema_version": "mnemo-semantic-lifecycle-comparison/1.0",
        "comparison_id": comparison_id,
        "before_run": before_run.name,
        "after_run": after_run.name,
        **comparison,
    }
    _write_exclusive(destination / "comparison.json", result)
    _write_exclusive(destination / "report.md", _report(result))
    manifest = {
        "schema_version": "mnemo-semantic-lifecycle-comparison-reproducibility/1.0",
        "comparison_id": comparison_id,
        "git_revision": subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "before_raw": {"path": str(before_raw), "sha256": _sha256(before_raw)},
        "after_raw": {"path": str(after_raw), "sha256": _sha256(after_raw)},
        "runner_sha256": _sha256(Path(__file__)),
        "artifact_hashes": {
            "comparison.json": _sha256(destination / "comparison.json"),
            "report.md": _sha256(destination / "report.md"),
        },
        "command": (
            "uv run python -m scripts.compare_semantic_lifecycle_runs "
            f"--comparison-id {comparison_id} --before-run {before_run} "
            f"--after-run {after_run}"
        ),
    }
    _write_exclusive(destination / "reproducibility-manifest.json", manifest)
    return destination, result


def _report(result: dict[str, object]) -> str:
    metrics = cast(dict[str, dict[str, object]], result["metrics"])
    total = metrics["top_level.wall_duration_ns"]
    save = metrics["checkpoint_view_save.wall_duration_ns"]
    automatic = metrics["automatic_context_assembly.wall_duration_ns"]
    rows = "\n".join(
        _report_row(label, metric)
        for label, metric in (
            ("Top-level semantic lifecycle", total),
            ("Checkpoint saves", save),
            ("Automatic context", automatic),
        )
    )
    return f"""# Semantic lifecycle before/after comparison

- Before: `{result["before_run"]}`
- After: `{result["after_run"]}`
- Paired independent profiles: `{result["paired_profile_count"]}`
- Bootstrap: `{result["bootstrap_iterations"]}` profile-level resamples,
  seed `{result["bootstrap_seed"]}`

| Actual elapsed metric | Before median (ms/profile) | After median (ms/profile) |
|---|---:|---:|
{rows}

Lower is better. These are actual local elapsed measurements. They are not model tokens, billing,
or evidence of behavioral task value. Rendered token counts were unchanged.
"""


def _report_row(label: str, metric: dict[str, object]) -> str:
    before = cast(float, metric["before_median"]) / 1e6
    after = cast(float, metric["after_median"]) / 1e6
    change = cast(float, metric["paired_mean_relative_change"])
    return f"| {label} | {before:.2f} | {after:.2f} ({change:.1%}; CI {_ci(metric)}) |"


def _ci(metric: dict[str, object]) -> str:
    interval = cast(list[float], metric["paired_mean_relative_change_ci95"])
    return f"[{interval[0]:.1%}, {interval[1]:.1%}]"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison-id", required=True)
    parser.add_argument("--before-run", type=Path, required=True)
    parser.add_argument("--after-run", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--seed", type=int, default=8122026)
    parser.add_argument("--bootstrap-iterations", type=int, default=10_000)
    arguments = parser.parse_args(argv)
    destination, result = run(
        comparison_id=arguments.comparison_id,
        before_run=arguments.before_run,
        after_run=arguments.after_run,
        results_root=arguments.results_root,
        seed=arguments.seed,
        bootstrap_iterations=arguments.bootstrap_iterations,
    )
    print(json.dumps({"destination": str(destination), "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
