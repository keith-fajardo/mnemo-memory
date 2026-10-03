"""Run the Jev hook-wiring replay on synthetic fixtures (spec 2026-10-02 §8.2).

Every prompt runs in a fresh process, twice (rules-only and typed-live). A run sends fixture
text to Jev, so it needs --live-calls-authorized (the maintainer's go-ahead, each time) and
TYPESAFE_API_KEY in the environment. A case whose child fails is recorded by case ID and error
type, the run goes on, and the report is still written (and is not complete). Before the cases,
each seed's note-verdict cache is warmed by a priming pass that judges every seeded note (spec
2026-10-03 §7); a priming failure is recorded and the run goes on. Example:

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

from scripts.typed_decision_fixtures import REPOSITORY_ROOT
from scripts.typed_decision_replay import (
    ARMS,
    SETS,
    Primer,
    PrimingResult,
    ReplayRefusedError,
    ReplayRequest,
    ReplayResult,
    ReplaySeed,
    Runner,
    find_case,
    prime_replay_seed,
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
    primer: Primer | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Run the replay, or with ``--child`` one case.

    A full run seeds both sets, warms each seed's verdict cache with ``primer`` (default: the
    live priming pass), then runs every case. Tests pass both ``runner`` and ``primer``.
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id")
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--live-calls-authorized", action="store_true")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    variables = os.environ if environ is None else environ
    if args.child:
        return _child(
            args.live_calls_authorized,
            variables,
            stdin or sys.stdin,
            stdout or sys.stdout,
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
        return run_case_in_fresh_process(request, live_calls_authorized=True, environ=variables)

    def prime(seed: ReplaySeed) -> PrimingResult:
        return prime_replay_seed(seed, environ=variables, live_calls_authorized=True)

    run: Runner = runner or fresh
    warm: Primer = primer or prime
    cases = replay_cases()
    with TemporaryDirectory(prefix="mnemo-typed-replay-") as root:
        seeds = {name: seed_replay_set(Path(root), name) for name in SETS}
        priming = {name: _primed(warm, seed) for name, seed in seeds.items()}
        results = run_replay(seeds, cases, run)
        report = score_replay(cases, results, seeds, priming=priming)
    report["priming"] = {name: result.to_dict() for name, result in priming.items()}
    report["run"] = {"run_id": args.run_id, "prompts": len(cases), "requests": len(results)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    status = "complete" if report["complete"] else "NOT complete"
    print(f"typed-decision hook replay {status}: {output}")
    return 0 if report["complete"] else 1


def _child(
    live_calls_authorized: bool,
    environ: Mapping[str, str],
    stdin: TextIO,
    stdout: TextIO,
) -> int:
    """One replay case in this fresh process; the prompt is re-read from the fixtures.

    The reported hook time is the timed hook call alone. Nothing of the typed step was imported
    before it, so a typed case pays the cold typed import inside that call, as a real hook does.

    The request carries a case ID, never prompt text. No refusal echoes prompt text or note
    content: an unknown case and a refused seed have their own fixed messages, and every other
    failure is reported by its exception type only.
    """

    try:
        value = json.loads(stdin.read())
        if not isinstance(value, dict) or not all(isinstance(item, str) for item in value.values()):
            raise TypeError("replay child request is invalid")
        request = ReplayRequest(**value)
    except (TypeError, ValueError):
        return _refuse("replay child request is invalid")
    if request.arm not in ARMS:
        return _refuse("replay child request is invalid")
    if request.arm == "typed" and not live_calls_authorized:
        return _refuse("a typed replay child needs --live-calls-authorized")
    if request.arm == "typed" and not environ.get("TYPESAFE_API_KEY", "").strip():
        return _refuse("TYPESAFE_API_KEY is not set")
    try:
        find_case(request.set_name, request.group, request.case_id)
    except ValueError:
        return _refuse("replay case is not in a synthetic fixture")
    try:
        result = run_replay_case(
            request, environ=environ, live_calls_authorized=live_calls_authorized
        )
    except ReplayRefusedError as error:
        return _refuse(str(error))
    except Exception as error:
        return _refuse(f"replay case failed ({type(error).__name__})")
    stdout.write(result.to_json() + "\n")
    return 0


def _primed(primer: Primer, seed: ReplaySeed) -> PrimingResult:
    """One set's priming pass; a failure is recorded as a blocked, empty pass, never fatal.

    Only the exception's class name is kept: its message could carry note text.
    """

    try:
        return primer(seed)
    except Exception as error:
        return PrimingResult.failed(seed.set_name, type(error).__name__)


def _refuse(message: str) -> int:
    print(f"refusing: {message}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
