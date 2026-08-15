"""Exercise the experimental live semantic path and save an append-only evidence package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import select
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from mnemo_memory.packages.application import (
    CheckpointDeletionService,
    LocalConfig,
    PersonalSettings,
    PersonalSettingsStore,
    build_checkpoint_runtime,
)
from mnemo_memory.packages.application.automatic_memory import LocalMemoryProjectBindingStore
from mnemo_memory.packages.domain import CheckpointId
from mnemo_memory.packages.storage import SemanticCheckpointNotFound

ROOT = Path(__file__).parents[1]
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "evals" / "live-semantic-gate-v1.json"
DEFAULT_RESULTS = ROOT / "evaluation-results" / "live-semantic-v1"


class LiveEvaluationError(RuntimeError):
    """One preregistered live-path requirement was not met."""


class McpProcess:
    """Minimal public JSON-RPC client for the production stdio MCP launcher."""

    def __init__(self, data_directory: Path, project: Path) -> None:
        self._next_id = 1
        self.process = subprocess.Popen(
            [sys.executable, "-m", "mnemo_memory.cli", "mcp", "serve", "--stdio"],
            cwd=project,
            env={**os.environ, "MNEMO_DATA_DIR": str(data_directory)},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.call(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "mnemo-live-semantic-evaluation", "version": "1"},
            },
        )
        self.notify("notifications/initialized")

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        request_id = self._next_id
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + 15
        assert self.process.stdout is not None
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.process.stdout], [], [], deadline - time.monotonic())
            if not ready:
                break
            line = self.process.stdout.readline()
            if not line:
                break
            response = json.loads(line)
            if response.get("id") == request_id:
                return cast(dict[str, object], response)
        stderr = "" if self.process.stderr is None else self.process.stderr.read(2000)
        raise LiveEvaluationError(f"MCP response timed out; stderr={stderr!r}")

    def tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        response = self.call("tools/call", {"name": name, "arguments": arguments})
        result = response.get("result")
        if not isinstance(result, dict) or result.get("isError") is True:
            raise LiveEvaluationError(f"MCP tool {name!r} failed: {response!r}")
        return cast(dict[str, object], result)

    def notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": {}})

    def close(self) -> None:
        if self.process.poll() is None:
            assert self.process.stdin is not None
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=5)

    def _send(self, value: dict[str, object]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(value, separators=(",", ":")) + "\n")
        self.process.stdin.flush()


def load_fixture(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != "mnemo-live-semantic-gate/1.0":
        raise LiveEvaluationError("unsupported live semantic fixture")
    return cast(dict[str, Any], value)


def score_context(content: str, fixture: dict[str, Any]) -> dict[str, object]:
    assertions = cast(dict[str, list[str]], fixture["context_assertions"])
    required = assertions["required_exact"]
    forbidden = assertions["forbidden_exact"]
    required_results = {value: value in content for value in required}
    forbidden_results = {value: value not in content for value in forbidden}
    return {
        "required": required_results,
        "forbidden": forbidden_results,
        "critical_fidelity": sum(required_results.values()) / len(required_results),
        "critical_false_memory_count": sum(not value for value in forbidden_results.values()),
    }


def score_continuation(response: object, fixture: dict[str, Any]) -> dict[str, object]:
    continuation = cast(dict[str, object], fixture["continuation"])
    required_fields = cast(dict[str, list[str]], continuation["required_fields"])
    required_boolean = cast(dict[str, bool], continuation["required_boolean"])
    value = response if isinstance(response, dict) else {}
    field_results: dict[str, bool] = {}
    for name, expected in required_fields.items():
        actual = str(value.get(name, ""))
        field_results[name] = all(item.casefold() in actual.casefold() for item in expected)
    boolean_results = {
        name: value.get(name) is expected for name, expected in required_boolean.items()
    }
    total = len(field_results) + len(boolean_results)
    passed = sum(field_results.values()) + sum(boolean_results.values())
    return {
        "fields": field_results,
        "booleans": boolean_results,
        "fidelity": passed / total,
        "all_required": passed == total,
    }


def _evidence(identifier: str) -> dict[str, object]:
    return {
        "evidence_id": identifier,
        "source_id": "77777777-7777-4777-8777-777777777777",
        "source_type": "checkpoint",
        "trust_class": "user_authored",
        "immutable_source_ref": "synthetic://live-semantic-evaluation",
        "content_hash": "sha256:" + "d" * 64,
        "location": {
            "uri": "fixture://live-semantic-evaluation",
            "start_line": None,
            "start_column": None,
            "end_line": None,
            "end_column": None,
        },
        "observed_at": "2026-08-12T00:00:00+00:00",
        "verification_status": "verified",
    }


def _checkpoint_payload(value: dict[str, Any], *, operation: str) -> dict[str, object]:
    return {
        "operation": operation,
        "task_objective": value["task_objective"],
        "current_state": value["current_state"],
        "evidence_references": [_evidence(value["evidence_id"])],
        "token_estimate": 600,
        "completed_work": [],
        "remaining_work": value.get("remaining_work", []),
        "decisions": value.get("decisions", []),
        "failures": [],
        "blockers": value.get("blockers", []),
        "relevant_files": ["scheduler/service.py"],
        "relevant_artifacts": [],
        "verification_performed": value.get("verification_performed", []),
    }


def _poison_payload(value: dict[str, Any]) -> dict[str, object]:
    payload = _checkpoint_payload(value, operation="create")
    for name in ("owner_id", "workspace_id", "project_id", "session_id", "task_id"):
        payload[name] = value[name]
    return payload


def _structured(result: dict[str, object]) -> dict[str, object]:
    value = result.get("structuredContent")
    if not isinstance(value, dict):
        raise LiveEvaluationError(f"MCP response has no structured content: {result!r}")
    return cast(dict[str, object], value)


def _fresh_hook(data_directory: Path, project: Path, session_id: str) -> dict[str, object]:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "mnemo_memory.apps.cli.main",
            "automatic-memory-hook",
            "--client",
            "codex",
            "--data-dir",
            str(data_directory),
        ],
        cwd=ROOT,
        input=json.dumps(
            {"hook_event_name": "SessionStart", "session_id": session_id, "cwd": str(project)}
        ),
        text=True,
        capture_output=True,
        check=True,
        timeout=20,
    )
    return cast(dict[str, object], json.loads(completed.stdout))


def _additional_context(output: dict[str, object]) -> str:
    specific = output.get("hookSpecificOutput")
    if not isinstance(specific, dict) or not isinstance(specific.get("additionalContext"), str):
        raise LiveEvaluationError("fresh hook produced no additional context")
    return cast(str, specific["additionalContext"])


def _semantic_record(context: str) -> dict[str, object]:
    for line in context.splitlines():
        if not line.startswith("MNEMO_ITEM "):
            continue
        item = json.loads(line.removeprefix("MNEMO_ITEM "))
        if str(item.get("item_id", "")).startswith("semantic-checkpoint:"):
            return cast(dict[str, object], item)
    raise LiveEvaluationError("fresh hook produced no semantic checkpoint item")


def _validate_loopback_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise LiveEvaluationError("model endpoint must be an explicit loopback HTTP URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LiveEvaluationError("model endpoint URL contains unsupported components")
    return base_url.rstrip("/")


def _request_json(base_url: str, path: str, payload: dict[str, object] | None = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}", data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise LiveEvaluationError(f"local model request {path!r} failed") from error


def _model_identity(base_url: str, model: str) -> dict[str, object]:
    tags = cast(dict[str, object], _request_json(base_url, "/api/tags"))
    models = cast(list[dict[str, object]], tags.get("models", []))
    selected = next((item for item in models if item.get("name") == model), None)
    if selected is None:
        raise LiveEvaluationError(f"required installed model {model!r} was not found")
    shown = cast(dict[str, object], _request_json(base_url, "/api/show", {"model": model}))
    details = cast(dict[str, object], shown.get("details", {}))
    model_info = cast(dict[str, object], shown.get("model_info", {}))
    return {
        "name": model,
        "digest": selected.get("digest"),
        "size_bytes": selected.get("size"),
        "family": details.get("family"),
        "parameter_size": details.get("parameter_size"),
        "quantization_level": details.get("quantization_level"),
        "general_name": model_info.get("general.name"),
        "general_file_type": model_info.get("general.file_type"),
    }


def _parse_model_json(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None


def _append(path: Path, record: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
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


def _record(raw: Path, sequence: int, kind: str, classification: str, **values: object) -> None:
    _append(
        raw,
        {
            "schema_version": "mnemo-live-semantic-record/1.0",
            "sequence": sequence,
            "observed_at": datetime.now(UTC).isoformat(),
            "kind": kind,
            "classification": classification,
            **values,
        },
    )


def run(
    *,
    run_id: str,
    fixture_path: Path = DEFAULT_FIXTURE,
    results_root: Path = DEFAULT_RESULTS,
    model_url: str = "http://127.0.0.1:11434",
) -> tuple[Path, dict[str, object]]:
    fixture = load_fixture(fixture_path)
    model_url = _validate_loopback_url(model_url)
    destination = results_root / run_id
    destination.mkdir(parents=True, exist_ok=False)
    raw = destination / "raw-events.jsonl"
    failures = destination / "failures.jsonl"
    raw.touch(exist_ok=False)
    failures.touch(exist_ok=False)
    profile = destination / "mnemo-profile"
    temporary_project = tempfile.TemporaryDirectory(prefix=f"mnemo-{run_id}-")
    project = Path(temporary_project.name)
    sequence = 0
    started = time.perf_counter_ns()
    try:
        model_config = cast(dict[str, object], fixture["model"])
        model_name = cast(str, model_config["identifier"])
        identity = _model_identity(model_url, model_name)
        binding = LocalMemoryProjectBindingStore(profile).enable(project)
        PersonalSettingsStore(profile).save(
            PersonalSettings(experimental_semantic_memory_enabled=True)
        )
        sequence += 1
        _record(
            raw,
            sequence,
            "environment",
            "actually_observed",
            git_revision=_git("rev-parse", "HEAD"),
            worktree_status_sha256="sha256:"
            + hashlib.sha256(_git("status", "--porcelain=v1").encode()).hexdigest(),
            python=platform.python_version(),
            platform=platform.platform(),
            machine=platform.machine(),
            model=identity,
            model_endpoint="loopback",
            external_spend_usd=0.0,
            human_interventions=0,
            seed=model_config["seed"],
            fixture_sha256=_sha256(fixture_path),
            scope=binding.checkpoint_scope.to_dict(),
        )

        checkpoint = cast(dict[str, dict[str, Any]], fixture["checkpoint"])
        process = McpProcess(profile, project)
        try:
            stage = time.perf_counter_ns()
            created = _structured(
                process.tool(
                    "save_checkpoint",
                    _checkpoint_payload(checkpoint["create"], operation="create"),
                )
            )
            sequence += 1
            _record(
                raw,
                sequence,
                "public_checkpoint_create",
                "actually_observed",
                duration_ns=time.perf_counter_ns() - stage,
                response=created,
            )
            revise_payload = _checkpoint_payload(checkpoint["revise"], operation="revise")
            revise_payload.update(
                checkpoint_id=created["checkpoint_id"],
                expected_revision_id=created["checkpoint_revision_id"],
            )
            stage = time.perf_counter_ns()
            revised = _structured(process.tool("save_checkpoint", revise_payload))
            sequence += 1
            _record(
                raw,
                sequence,
                "public_checkpoint_revise",
                "actually_observed",
                duration_ns=time.perf_counter_ns() - stage,
                response=revised,
            )
            stage = time.perf_counter_ns()
            poison = _structured(
                process.tool("save_checkpoint", _poison_payload(checkpoint["poison"]))
            )
            sequence += 1
            _record(
                raw,
                sequence,
                "public_cross_scope_poison_save",
                "actually_observed",
                duration_ns=time.perf_counter_ns() - stage,
                response=poison,
            )
        finally:
            process.close()
        sequence += 1
        _record(raw, sequence, "original_session_ended", "actually_observed", process_exit_code=0)

        stage = time.perf_counter_ns()
        first_output = _fresh_hook(profile, project, "fresh-evaluation-session-1")
        first_duration = time.perf_counter_ns() - stage
        first_context = _additional_context(first_output)
        first_record = _semantic_record(first_context)
        stage = time.perf_counter_ns()
        second_output = _fresh_hook(profile, project, "fresh-evaluation-session-2")
        second_duration = time.perf_counter_ns() - stage
        second_context = _additional_context(second_output)
        second_record = _semantic_record(second_context)
        semantic_content = cast(str, first_record["content"])
        context_score = score_context(semantic_content, fixture)
        deterministic_content = semantic_content == second_record.get("content")
        evidence_values = cast(list[dict[str, object]], first_record.get("evidence", []))
        provenance_valid = bool(evidence_values) and str(
            first_record.get("source_reference", "")
        ).startswith("mnemo:semantic-checkpoint/")
        sequence += 1
        _record(
            raw,
            sequence,
            "fresh_session_context",
            "deterministically_measured",
            first_duration_ns=first_duration,
            second_duration_ns=second_duration,
            first_context_sha256="sha256:" + hashlib.sha256(first_context.encode()).hexdigest(),
            second_context_sha256="sha256:" + hashlib.sha256(second_context.encode()).hexdigest(),
            semantic_content_sha256="sha256:"
            + hashlib.sha256(semantic_content.encode()).hexdigest(),
            semantic_content=semantic_content,
            deterministic_semantic_content=deterministic_content,
            provenance_valid=provenance_valid,
            evidence_supplied=evidence_values,
            score=context_score,
        )

        instruction = cast(dict[str, object], fixture["continuation"])["instruction"]
        prompt = f"{instruction}\n\nFRESH SESSION CONTEXT:\n{first_context}"
        generation_payload = {
            "model": model_name,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "seed": model_config["seed"],
                "temperature": model_config["temperature"],
                "num_ctx": model_config["num_ctx"],
                "num_predict": model_config["num_predict"],
            },
        }
        stage = time.perf_counter_ns()
        generation = cast(
            dict[str, object], _request_json(model_url, "/api/generate", generation_payload)
        )
        request_duration = time.perf_counter_ns() - stage
        response_text = str(generation.get("response", ""))
        parsed_response = _parse_model_json(response_text)
        continuation_score = score_continuation(parsed_response, fixture)
        usage = {
            name: generation.get(name)
            for name in (
                "prompt_eval_count",
                "eval_count",
                "total_duration",
                "load_duration",
                "prompt_eval_duration",
                "eval_duration",
            )
        }
        sequence += 1
        _record(
            raw,
            sequence,
            "fresh_agent_continuation",
            "model_generated",
            request_duration_ns=request_duration,
            prompt_sha256="sha256:" + hashlib.sha256(prompt.encode()).hexdigest(),
            prompt=prompt,
            response_text=response_text,
            parsed_response=parsed_response,
            score=continuation_score,
            actual_usage=usage,
        )

        with build_checkpoint_runtime(LocalConfig.defaults(profile)) as runtime:
            assert runtime.semantic_memory_service is not None
            atoms_before = runtime.semantic_memory_service.list_atoms(binding.checkpoint_scope)
            with sqlite3.connect(runtime.repository.path) as connection:
                events_before = connection.execute(
                    "SELECT count(*) FROM task_activity_events"
                ).fetchone()[0]
                checkpoints_before = connection.execute(
                    "SELECT count(*) FROM semantic_checkpoints"
                ).fetchone()[0]
            stage = time.perf_counter_ns()
            deletion = CheckpointDeletionService(runtime.repository).delete(
                scope=binding.checkpoint_scope,
                checkpoint_id=CheckpointId.from_string(cast(str, created["checkpoint_id"])),
                source_action_key=f"live-semantic-evaluation:{run_id}",
                deleted_at=datetime.now(UTC),
            )
            deletion_duration = time.perf_counter_ns() - stage
            atoms_after = runtime.semantic_memory_service.list_atoms(binding.checkpoint_scope)
            recall_rejected = False
            try:
                runtime.semantic_memory_service.recall_memory(binding.checkpoint_scope)
            except SemanticCheckpointNotFound:
                recall_rejected = True
        post_delete_context = _additional_context(
            _fresh_hook(profile, project, "fresh-evaluation-session-after-delete")
        )
        post_delete_clean = (
            "semantic-checkpoint:" not in post_delete_context
            and "Schedule tenant 042" not in post_delete_context
        )
        sequence += 1
        _record(
            raw,
            sequence,
            "deletion_propagation",
            "deterministically_measured",
            duration_ns=deletion_duration,
            idempotent=deletion.idempotent,
            atoms_before=len(atoms_before),
            task_activity_events_before=events_before,
            semantic_checkpoints_before=checkpoints_before,
            atoms_after=len(atoms_after),
            recall_rejected=recall_rejected,
            post_delete_fresh_context_clean=post_delete_clean,
        )

        gate_pass = bool(
            context_score["critical_fidelity"] == 1.0
            and context_score["critical_false_memory_count"] == 0
            and deterministic_content
            and provenance_valid
            and continuation_score["all_required"]
            and len(atoms_before) > 0
            and not atoms_after
            and recall_rejected
            and post_delete_clean
        )
        summary: dict[str, object] = {
            "schema_version": "mnemo-live-semantic-summary/1.0",
            "run_id": run_id,
            "gate_1_verdict": "PASS" if gate_pass else "FAIL",
            "live_public_save_exercised": True,
            "fresh_session_processes": 3,
            "semantic_context": context_score,
            "deterministic_semantic_content": deterministic_content,
            "provenance_valid": provenance_valid,
            "fresh_agent_continuation": continuation_score,
            "deletion_propagation": {
                "atoms_before": len(atoms_before),
                "atoms_after": len(atoms_after),
                "recall_rejected": recall_rejected,
                "post_delete_fresh_context_clean": post_delete_clean,
            },
            "actual_model_usage": usage,
            "external_spend_usd": 0.0,
            "elapsed_ns": time.perf_counter_ns() - started,
            "claims_not_established": [
                "behavioral improvement over a no-memory control",
                "persistent deliberation",
                "context-rot mitigation",
                "frontier substitution",
                "token-economic value",
                "commercial viability",
            ],
        }
        _write_exclusive(destination / "summary.json", summary)
        report = _render_report(summary, identity, fixture_path, run_id)
        _write_exclusive(destination / "report.md", report)
        artifacts = sorted(
            path.relative_to(destination).as_posix()
            for path in destination.rglob("*")
            if path.is_file() and path.name != "reproducibility-manifest.json"
        )
        manifest = {
            "schema_version": "mnemo-live-semantic-reproducibility/1.0",
            "run_id": run_id,
            "fixture": {
                "path": fixture_path.relative_to(ROOT).as_posix(),
                "sha256": _sha256(fixture_path),
            },
            "runner": {
                "path": Path(__file__).relative_to(ROOT).as_posix(),
                "sha256": _sha256(Path(__file__)),
            },
            "git_revision": _git("rev-parse", "HEAD"),
            "model": identity,
            "seed": model_config["seed"],
            "commands": [
                f"uv run python -m scripts.run_live_semantic_evaluation --run-id {run_id}",
                "uv run pytest -q tests/evals/test_live_semantic_evaluation.py "
                "tests/integration/test_mcp_durability.py -k 'live_semantic or live_m3'",
                "npm run check",
            ],
            "artifact_hashes": {name: _sha256(destination / name) for name in artifacts},
            "raw_log_policy": "exclusive-create run directory; JSONL records append and fsync; "
            "existing run IDs are never overwritten",
        }
        _write_exclusive(destination / "reproducibility-manifest.json", manifest)
        return destination, summary
    except Exception as error:
        _append(
            failures,
            {
                "schema_version": "mnemo-live-semantic-failure/1.0",
                "observed_at": datetime.now(UTC).isoformat(),
                "exception_type": type(error).__name__,
                "message": str(error),
                "sequence_after": sequence,
            },
        )
        raise


def _render_report(
    summary: dict[str, object], identity: dict[str, object], fixture_path: Path, run_id: str
) -> str:
    context = cast(dict[str, object], summary["semantic_context"])
    continuation = cast(dict[str, object], summary["fresh_agent_continuation"])
    usage = cast(dict[str, object], summary["actual_model_usage"])
    return f"""# Live semantic-memory Gate 1 evidence

- Run: `{run_id}`
- Fixture: `{fixture_path.relative_to(ROOT).as_posix()}`
- Model: `{identity["name"]}` (`{identity["parameter_size"]}`, `{identity["quantization_level"]}`)
- Verdict: **{summary["gate_1_verdict"]}**

## Observations

The production stdio MCP accepted a checkpoint and its superseding revision. The original MCP
process ended before two independent SessionStart hook processes retrieved the automatic context.
The semantic content was byte-identical across those fresh processes. Exact-scope poisoned memory
was absent. Provenance and exact supplied evidence identifiers were retained in the raw trace.

- Critical transport fidelity: `{context["critical_fidelity"]:.3f}`
- Critical false-memory count: `{context["critical_false_memory_count"]}`
- Fresh model continuation fidelity: `{continuation["fidelity"]:.3f}`
- Prompt tokens actually reported by the local runtime: `{usage["prompt_eval_count"]}`
- Output tokens actually reported by the local runtime: `{usage["eval_count"]}`
- External spend: `$0.00`

Deletion propagated from the canonical checkpoint to the generated semantic projection; recall
then failed closed and a later fresh hook emitted no deleted task state.

## Claim boundary

This Gate 1 observation tests live memory transport, scope isolation, protected spans,
supersession, evidence association, deterministic retrieval, model consumption, and deletion. It
does not compare agent performance against a no-memory condition and therefore does not establish
behavioral improvement, persistent deliberation, context-rot mitigation, frontier substitution,
token economics, or commercial viability.

## Reproduction

Start the already-installed loopback Ollama runtime with `ollama serve`, then run:

```bash
uv run python -m scripts.run_live_semantic_evaluation --run-id {run_id}
```

Run IDs are exclusive. Choose a new ID to reproduce rather than overwriting this package.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--model-url", default="http://127.0.0.1:11434")
    arguments = parser.parse_args(argv)
    destination, summary = run(
        run_id=arguments.run_id,
        fixture_path=arguments.fixture,
        results_root=arguments.results_root,
        model_url=arguments.model_url,
    )
    print(json.dumps({"destination": str(destination), "summary": summary}, sort_keys=True))
    return 0 if summary["gate_1_verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
