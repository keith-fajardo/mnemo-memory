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
        self.total_requests = 0

    def record(self, record: TypedDecisionRecord) -> None:
        if record.model_version is not None:
            self.versions.add(record.model_version)
        self.input_tokens += record.input_tokens
        self.answered += int(record.outcome == "answered")
        self.total_requests += 1


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
        "total_requests": recorder.total_requests,
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
