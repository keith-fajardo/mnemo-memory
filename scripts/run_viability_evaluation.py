"""Run the dependency-free paired lifecycle viability evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mnemo_memory.packages.application.evaluation import (
    EvaluationRunner,
    aggregate_runs,
    load_corpus,
    load_evaluation_config,
)
from mnemo_memory.packages.application.evaluation.reporting import write_evaluation_artifacts

ROOT = Path(__file__).parents[1]
DEFAULT_CORPUS = ROOT / "tests/fixtures/evals/viability-corpus-v1.json"
DEFAULT_CONFIG = ROOT / "tests/fixtures/evals/viability-config-v1.json"
DEFAULT_RESULTS = ROOT / "evaluation-results/viability-v1"


def run(
    *,
    evaluation_run_id: str,
    corpus_path: Path = DEFAULT_CORPUS,
    config_path: Path = DEFAULT_CONFIG,
    results_root: Path = DEFAULT_RESULTS,
) -> tuple[Path, dict[str, Any]]:
    corpus = load_corpus(corpus_path)
    config = load_evaluation_config(config_path)
    runner = EvaluationRunner.offline(config, corpus)
    runs = runner.run(evaluation_run_id)
    aggregate = aggregate_runs(runs, config)
    environment = environment_metadata(
        runner.fairness_control_digest, config.live_evaluation_enabled
    )
    destination = write_evaluation_artifacts(
        results_root=results_root,
        evaluation_run_id=evaluation_run_id,
        runs=runs,
        aggregate=aggregate,
        config=config,
        corpus=corpus,
        environment=environment,
        corpus_path=corpus_path.relative_to(ROOT),
        config_path=config_path.relative_to(ROOT),
    )
    return destination, aggregate


def environment_metadata(fairness_digest: str, live_enabled: bool) -> dict[str, object]:
    revision = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain=v1")
    worktree_digest = hashlib.sha256(status.encode("utf-8")).hexdigest()
    source_tree = _source_tree_metadata()
    return {
        "schema_version": "mnemo-viability-environment/1.0",
        "evaluated_at": datetime.now(UTC).isoformat(),
        "evaluated_revision": revision,
        "worktree_state": "dirty" if status else "clean",
        "worktree_status_digest": f"sha256:{worktree_digest}",
        "evaluated_source_tree": source_tree,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "fairness_control_digest": fairness_digest,
        "provider_detection": {
            "codex_cli_available": shutil.which("codex") is not None,
            "claude_cli_available": shutil.which("claude") is not None,
            "openai_credential_name_present": "OPENAI_API_KEY" in os.environ,
            "anthropic_credential_name_present": "ANTHROPIC_API_KEY" in os.environ,
            "credential_values_recorded": False,
        },
        "external_authorization": {
            "live_evaluation_enabled": live_enabled,
            "authorized_external_calls": 0,
            "authorized_maximum_cost_usd": 0.0,
            "executed_external_calls": 0,
            "external_cost_incurred_usd": 0.0,
        },
        "unexecuted_live_study_estimate": {
            "major_conditions": ["B0", "B2", "M3"],
            "model_families": 2,
            "paired_runs_per_condition_family": 30,
            "minimum_generation_calls": 180,
            "maximum_cost_usd": None,
            "cost_reason": "no approved provider/model price and per-call cap is configured",
        },
    }


def _source_tree_metadata() -> dict[str, object]:
    paths = [
        path
        for path in (ROOT / "src").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    ]
    paths.extend(
        path
        for path in (
            ROOT / "package.json",
            ROOT / "package-lock.json",
            ROOT / "pyproject.toml",
            ROOT / "uv.lock",
            ROOT / "scripts/run_viability_evaluation.py",
            DEFAULT_CORPUS,
            DEFAULT_CONFIG,
        )
        if path.is_file()
    )
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        relative = path.relative_to(ROOT).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
        digest.update(b"\0")
    return {
        "sha256": f"sha256:{digest.hexdigest()}",
        "file_count": len(set(paths)),
        "scope": "all non-cache src files, manifests/lockfiles, runner, corpus, and configuration",
    }


def _git(*arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS)
    arguments = parser.parse_args(argv)
    destination, aggregate = run(
        evaluation_run_id=arguments.run_id,
        corpus_path=arguments.corpus.resolve(),
        config_path=arguments.config.resolve(),
        results_root=arguments.results_root.resolve(),
    )
    print(
        json.dumps(
            {
                "result_directory": destination.relative_to(ROOT).as_posix()
                if destination.is_relative_to(ROOT)
                else destination.as_posix(),
                "verdict": aggregate["verdict"],
                "run_count": aggregate["run_count"],
                "available_run_count": aggregate["available_run_count"],
                "external_cost_incurred_usd": 0.0,
            },
            sort_keys=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
