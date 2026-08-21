"""Bounded executor/advisor shadow-loop evaluation contracts."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pytest import MonkeyPatch, approx, raises

from mnemo_memory.packages.domain import EventId, SemanticAtomKind, SemanticMemoryAtom
from scripts import run_long_horizon_evaluation as evaluation
from scripts.run_long_horizon_evaluation import (
    DEFAULT_CODEX_CLI_SUPERVISION_CORPUS,
    DEFAULT_GATED_SUPERVISION_CORPUS,
    DEFAULT_SUPERVISION_CORPUS,
    FrontierCallResult,
    _build_codex_cli_frontier_call,
    _build_frontier_call,
    _build_openai_frontier_call,
    _codex_cli_output_text,
    _expected,
    _generate_supervised_candidate,
    _load_corpus,
    _memory_content,
    _scope,
    _trajectory,
    _variant,
    analyze_supervision,
)

FINAL_LOCAL_FIRST_30_PAIR_CORPUS = (
    evaluation.ROOT
    / "tests"
    / "fixtures"
    / "evals"
    / "supervised-small-model-shadow-local-first-30-pair.json"
)
FINAL_TAKEOVER_30_PAIR_CORPUS = (
    evaluation.ROOT
    / "tests"
    / "fixtures"
    / "evals"
    / "supervised-small-model-shadow-takeover-30-pair-v6.json"
)


def _candidate(changes: dict[str, object]) -> dict[str, object]:
    return {
        "changes": changes,
        "analysis_summary": "checked task evidence",
        "hypothesis": "apply supported policy",
        "evidence_used": ["ticket"],
        "uncertainty": "none",
        "next_action": "validate candidate",
        "confidence": 0.9,
    }


def _full_frontier_changes(corpus: dict[str, object], **updates: object) -> dict[str, object]:
    changes = dict(cast(dict[str, object], corpus["initial_config"]))
    changes.update(updates)
    return changes


def _model_result(value: dict[str, object], *, prompt_tokens: int = 10) -> dict[str, object]:
    return {
        "response": json.dumps(value),
        "prompt_eval_count": prompt_tokens,
        "eval_count": 5,
        "total_duration": 100,
        "load_duration": 10,
        "prompt_eval_duration": 40,
        "eval_duration": 50,
    }


def _constraint_atom(field: str, value: str) -> SemanticMemoryAtom:
    return SemanticMemoryAtom.create(
        scope=_scope("telehealth-01", "SD"),
        kind=SemanticAtomKind.CONSTRAINT,
        subject="user",
        predicate="requires",
        object_value=f"{field}={value}",
        source_event_ids=(EventId.from_string("60000000-0000-4000-8000-000000000011"),),
        created_at=datetime(2026, 8, 18, tzinfo=UTC),
    )


def test_supervision_protocol_freezes_roles_bounds_and_thresholds() -> None:
    corpus = _load_corpus(DEFAULT_SUPERVISION_CORPUS)

    assert corpus["analysis_protocol"] == "supervised_small_model_shadow"
    assert corpus["conditions"] == ["SD", "SS", "TD"]
    assert corpus["variant_count"] == 30
    assert corpus["model"]["identifier"] == "ministral-3:8b-instruct-2512-q4_K_M"
    assert corpus["model"]["license"] == "Apache-2.0"
    assert corpus["frontier_advisor"] == {
        "provider": "openai",
        "identifier": "gpt-5.6-sol",
        "source_url": "https://developers.openai.com/api/docs/models/gpt-5.6-sol",
        "reasoning_effort": "medium",
        "maximum_output_tokens": 800,
        "maximum_calls": 9,
        "maximum_cost_usd": 2.0,
        "input_cost_per_million_tokens_usd": 5.0,
        "output_cost_per_million_tokens_usd": 30.0,
        "request_timeout_seconds": 60,
        "live_calls_authorized": False,
    }
    assert corpus["loop"] == {
        "maximum_executor_repairs": 1,
        "maximum_advisor_reviews": 2,
        "persist_verified_field_names_only": True,
    }
    assert corpus["preregistered_supervision_thresholds"] == {
        "minimum_pairs": 30,
        "bootstrap_iterations": 10000,
        "supervised_accuracy_margin": 0.1,
        "frontier_quality_floor": -0.02,
        "frontier_token_savings_margin": 0.3,
        "critical_false_memories_allowed": 0,
    }


def test_gated_v2_changes_only_the_initial_frontier_review_policy() -> None:
    original = _load_corpus(DEFAULT_SUPERVISION_CORPUS)
    gated = _load_corpus(DEFAULT_GATED_SUPERVISION_CORPUS)

    assert gated["model"] == original["model"]
    assert gated["frontier_advisor"] == original["frontier_advisor"]
    assert (
        gated["preregistered_supervision_thresholds"]
        == original["preregistered_supervision_thresholds"]
    )
    assert original["loop"] == {
        "maximum_executor_repairs": 1,
        "maximum_advisor_reviews": 2,
        "persist_verified_field_names_only": True,
    }
    assert gated["loop"] == {
        **original["loop"],
        "frontier_review_gate": "deterministic_failure_only",
    }


def test_codex_cli_v3_changes_only_frontier_transport_and_subscription_accounting() -> None:
    gated = _load_corpus(DEFAULT_GATED_SUPERVISION_CORPUS)
    codex = _load_corpus(DEFAULT_CODEX_CLI_SUPERVISION_CORPUS)

    assert codex["model"] == gated["model"]
    assert codex["loop"] == gated["loop"]
    assert (
        codex["preregistered_supervision_thresholds"]
        == gated["preregistered_supervision_thresholds"]
    )
    assert codex["frontier_advisor"] == {
        "provider": "codex_cli",
        "identifier": "gpt-5.6-sol",
        "source_url": "https://learn.chatgpt.com/docs/non-interactive-mode",
        "authentication_mode": "chatgpt_subscription",
        "executable": "codex",
        "required_cli_version": "0.147.0",
        "reasoning_effort": "medium",
        "maximum_response_bytes": 32768,
        "maximum_calls": 9,
        "maximum_frontier_tokens": 50000,
        "request_timeout_seconds": 120,
        "live_calls_authorized": False,
    }


def test_hybrid_v4_freezes_user_routing_and_bounded_plan_first_policy() -> None:
    codex = _load_corpus(DEFAULT_CODEX_CLI_SUPERVISION_CORPUS)
    hybrid = _load_corpus(evaluation.DEFAULT_HYBRID_SUPERVISION_CORPUS)

    assert hybrid["conditions"] == codex["conditions"]
    assert hybrid["model"] == codex["model"]
    assert hybrid["loop"] == codex["loop"]
    assert (
        hybrid["preregistered_supervision_thresholds"]
        == codex["preregistered_supervision_thresholds"]
    )
    assert hybrid["frontier_advisor"] == {
        **codex["frontier_advisor"],
        "maximum_calls": 12,
        "maximum_frontier_tokens": 75000,
    }
    assert hybrid["hybrid_routing"] == {
        "mode": "hybrid",
        "plan_first_tags": ["authorization", "migration"],
        "frontier_takeover": False,
        "maximum_plan_steps": 8,
        "session_risk_tags": {
            "1": ["authorization"],
            "2": ["bounded_mechanical"],
            "3": ["migration"],
        },
    }


def test_hybrid_router_obeys_user_mode_and_frozen_risk_tags() -> None:
    corpus = _load_corpus(evaluation.DEFAULT_HYBRID_SUPERVISION_CORPUS)

    assert evaluation._hybrid_routing_decision(corpus, session=1) == (
        "frontier_plan_first",
        ("authorization",),
    )
    assert evaluation._hybrid_routing_decision(corpus, session=2) == (
        "local_first",
        ("bounded_mechanical",),
    )
    assert evaluation._hybrid_routing_decision(corpus, session=3) == (
        "frontier_plan_first",
        ("migration",),
    )

    local = {**corpus, "hybrid_routing": {**corpus["hybrid_routing"], "mode": "local_first"}}
    assert evaluation._hybrid_routing_decision(local, session=1)[0] == "local_first"
    frontier = {
        **corpus,
        "hybrid_routing": {**corpus["hybrid_routing"], "mode": "frontier_plan_first"},
    }
    assert evaluation._hybrid_routing_decision(frontier, session=2)[0] == ("frontier_plan_first")


def test_local_first_v5_freezes_subscription_savings_as_the_primary_comparison() -> None:
    hybrid = _load_corpus(evaluation.DEFAULT_HYBRID_SUPERVISION_CORPUS)
    local_first = _load_corpus(evaluation.DEFAULT_LOCAL_FIRST_SAVINGS_CORPUS)

    assert local_first["conditions"] == hybrid["conditions"]
    assert local_first["model"] == hybrid["model"]
    assert local_first["loop"] == hybrid["loop"]
    assert (
        local_first["preregistered_supervision_thresholds"]
        == hybrid["preregistered_supervision_thresholds"]
    )
    assert local_first["frontier_advisor"] == {
        **hybrid["frontier_advisor"],
        "maximum_calls": 9,
        "maximum_frontier_tokens": 180000,
    }
    assert local_first["hybrid_routing"] == {
        **hybrid["hybrid_routing"],
        "mode": "local_first",
    }
    assert [
        evaluation._hybrid_routing_decision(local_first, session=session)[0]
        for session in range(1, 4)
    ] == ["local_first", "local_first", "local_first"]


def test_local_first_30_pair_final_changes_only_global_live_run_bounds() -> None:
    engineering = _load_corpus(evaluation.DEFAULT_LOCAL_FIRST_SAVINGS_CORPUS)
    final = _load_corpus(FINAL_LOCAL_FIRST_30_PAIR_CORPUS)

    assert final["variant_count"] == 30
    assert final["conditions"] == engineering["conditions"]
    assert final["model"] == engineering["model"]
    assert final["loop"] == engineering["loop"]
    assert final["hybrid_routing"] == engineering["hybrid_routing"]
    assert (
        final["preregistered_supervision_thresholds"]
        == engineering["preregistered_supervision_thresholds"]
    )
    assert final["frontier_advisor"] == {
        **engineering["frontier_advisor"],
        "maximum_calls": 150,
        "maximum_frontier_tokens": 3_000_000,
    }
    assert final["frontier_advisor"]["live_calls_authorized"] is False


def test_takeover_v6_enables_only_bounded_direct_frontier_fallback() -> None:
    local_first = _load_corpus(evaluation.DEFAULT_LOCAL_FIRST_SAVINGS_CORPUS)
    takeover = _load_corpus(evaluation.DEFAULT_TAKEOVER_SUPERVISION_CORPUS)

    assert takeover["conditions"] == local_first["conditions"]
    assert takeover["model"] == local_first["model"]
    assert takeover["loop"] == local_first["loop"]
    assert (
        takeover["preregistered_supervision_thresholds"]
        == local_first["preregistered_supervision_thresholds"]
    )
    assert takeover["frontier_advisor"] == {
        **local_first["frontier_advisor"],
        "maximum_calls": 6,
    }
    assert takeover["hybrid_routing"] == {
        **local_first["hybrid_routing"],
        "frontier_takeover": True,
    }
    assert takeover["frontier_advisor"]["live_calls_authorized"] is False


def test_takeover_v6_30_pair_final_changes_only_global_live_run_bounds() -> None:
    engineering = _load_corpus(evaluation.DEFAULT_TAKEOVER_SUPERVISION_CORPUS)
    final = _load_corpus(FINAL_TAKEOVER_30_PAIR_CORPUS)

    assert final["variant_count"] == 30
    assert final["conditions"] == engineering["conditions"]
    assert final["model"] == engineering["model"]
    assert final["loop"] == engineering["loop"]
    assert final["hybrid_routing"] == engineering["hybrid_routing"]
    assert (
        final["preregistered_supervision_thresholds"]
        == engineering["preregistered_supervision_thresholds"]
    )
    assert final["frontier_advisor"] == {
        **engineering["frontier_advisor"],
        "maximum_calls": 180,
        "maximum_frontier_tokens": 3_000_000,
    }
    assert final["frontier_advisor"]["live_calls_authorized"] is False


def _codex_jsonl(response: dict[str, object], *, input_tokens: int = 123) -> str:
    events = [
        {"type": "thread.started", "thread_id": "transient-thread"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {"id": "item_0", "type": "reasoning", "text": "transient"},
        },
        {
            "type": "item.completed",
            "item": {
                "id": "item_1",
                "type": "agent_message",
                "text": json.dumps(response),
            },
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": input_tokens,
                "cached_input_tokens": 23,
                "output_tokens": 17,
                "reasoning_output_tokens": 7,
            },
        },
    ]
    return "\n".join(json.dumps(event) for event in events) + "\n"


def _codex_jsonl_with_usage(usage: object, *, include_usage: bool = True) -> str:
    events = [json.loads(line) for line in _codex_jsonl({}).splitlines()]
    completed = cast(dict[str, object], events[-1])
    if include_usage:
        completed["usage"] = usage
    else:
        completed.pop("usage")
    return "\n".join(json.dumps(event) for event in events) + "\n"


def _authorized_codex_corpus(**overrides: object) -> dict[str, object]:
    corpus = _load_corpus(DEFAULT_CODEX_CLI_SUPERVISION_CORPUS)
    return {
        **corpus,
        "frontier_advisor": {
            **cast(dict[str, object], corpus["frontier_advisor"]),
            "live_calls_authorized": True,
            **overrides,
        },
    }


def test_codex_cli_adapter_uses_subscription_auth_and_isolated_fail_closed_command() -> None:
    commands: list[tuple[tuple[str, ...], str | None, dict[str, str], Path, int]] = []
    response: dict[str, object] = {
        "status": "pass",
        "failed_fields": [],
        "repair_steps": [],
        "uncertainty": "",
    }

    def fake_transport(
        command: tuple[str, ...],
        stdin: str | None,
        environment: dict[str, str],
        cwd: Path,
        timeout_seconds: int,
    ) -> subprocess.CompletedProcess[str]:
        commands.append((command, stdin, environment, cwd, timeout_seconds))
        if command[1:] == ("--version",):
            return subprocess.CompletedProcess(command, 0, "codex-cli 0.147.0\n", "")
        if command[1:] == ("login", "status"):
            return subprocess.CompletedProcess(
                command,
                0,
                "",
                (
                    "WARNING: proceeding, even though we could not create PATH aliases: "
                    "Operation not permitted (os error 1)\n"
                    "Logged in using ChatGPT\n"
                ),
            )
        schema_path = Path(command[command.index("--output-schema") + 1])
        assert schema_path.parent == cwd
        assert json.loads(schema_path.read_text(encoding="utf-8"))["additionalProperties"] is False
        return subprocess.CompletedProcess(command, 0, _codex_jsonl(response), "")

    frontier_call = _build_codex_cli_frontier_call(
        _authorized_codex_corpus(),
        environment={
            "PATH": "/opt/homebrew/bin:/usr/bin",
            "HOME": "/Users/tester",
            "CODEX_HOME": "/Users/tester/.codex",
            "HTTPS_PROXY": "http://secret@proxy.invalid",
            "MNEMO_SECRET": "must-not-cross",
        },
        transport=fake_transport,
    )
    result = frontier_call("review", "review this parsed candidate")

    command, stdin, environment, cwd, timeout = commands[-1]
    assert command[0] == "codex"
    assert command[1] == "exec"
    for required in (
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--skip-git-repo-check",
        "--json",
        "--output-schema",
    ):
        assert required in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[command.index("--model") + 1] == "gpt-5.6-sol"
    assert 'model_reasoning_effort="medium"' in command
    assert command[-1] == "-"
    assert "review this parsed candidate" not in command
    assert stdin == "review this parsed candidate"
    assert cwd != evaluation.ROOT
    assert timeout == 120
    assert environment == {
        "CODEX_HOME": "/Users/tester/.codex",
        "HOME": "/Users/tester",
        "PATH": "/opt/homebrew/bin:/usr/bin",
    }
    assert result.provider_id == "codex_cli"
    assert result.model_id == "gpt-5.6-sol"
    assert result.input_tokens == 123
    assert result.cached_input_tokens == 23
    assert result.output_tokens == 17
    assert result.reasoning_output_tokens == 7


def test_codex_cli_adapter_uses_the_bounded_v4_plan_schema() -> None:
    corpus = _load_corpus(evaluation.DEFAULT_HYBRID_SUPERVISION_CORPUS)
    corpus = {
        **corpus,
        "frontier_advisor": {
            **cast(dict[str, object], corpus["frontier_advisor"]),
            "live_calls_authorized": True,
        },
    }
    plan: dict[str, object] = {
        "steps": ["Apply supported changes."],
        "acceptance_checks": ["Deterministic checks pass."],
        "uncertainty": "",
    }

    def fake_transport(
        command: tuple[str, ...],
        _stdin: str | None,
        _environment: dict[str, str],
        _cwd: Path,
        _timeout_seconds: int,
    ) -> subprocess.CompletedProcess[str]:
        if command[1:] == ("--version",):
            return subprocess.CompletedProcess(command, 0, "codex-cli 0.147.0\n", "")
        if command[1:] == ("login", "status"):
            return subprocess.CompletedProcess(command, 0, "Logged in using ChatGPT\n", "")
        schema_path = Path(command[command.index("--output-schema") + 1])
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        assert schema["required"] == ["steps", "acceptance_checks", "uncertainty"]
        assert schema["properties"]["steps"]["maxItems"] == 8
        assert schema["properties"]["acceptance_checks"]["maxItems"] == 8
        return subprocess.CompletedProcess(command, 0, _codex_jsonl(plan), "")

    frontier_call = _build_codex_cli_frontier_call(
        cast(dict[str, object], corpus),
        environment={"PATH": "/bin"},
        transport=fake_transport,
    )
    result = frontier_call("plan", "create a bounded plan")

    assert result.purpose == "plan"
    assert json.loads(result.response_text) == plan


def test_codex_cli_adapter_rejects_unauthorized_or_api_key_environment_before_transport() -> None:
    calls = 0

    def forbidden_transport(*_args: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not be called")

    corpus = _load_corpus(DEFAULT_CODEX_CLI_SUPERVISION_CORPUS)
    with raises(evaluation.LongHorizonError, match="live calls are not authorized"):
        _build_codex_cli_frontier_call(corpus, environment={}, transport=forbidden_transport)

    with raises(evaluation.LongHorizonError, match="API-key environment"):
        _build_codex_cli_frontier_call(
            _authorized_codex_corpus(),
            environment={"OPENAI_API_KEY": "sk-must-not-use"},
            transport=forbidden_transport,
        )

    assert calls == 0


def test_codex_cli_adapter_requires_pinned_version_and_chatgpt_login() -> None:
    observed: list[tuple[str, ...]] = []

    def wrong_version(
        command: tuple[str, ...],
        _stdin: str | None,
        _environment: dict[str, str],
        _cwd: Path,
        _timeout_seconds: int,
    ) -> subprocess.CompletedProcess[str]:
        observed.append(command)
        return subprocess.CompletedProcess(command, 0, "codex-cli 0.148.0\n", "")

    with raises(evaluation.LongHorizonError, match="version does not match"):
        _build_codex_cli_frontier_call(
            _authorized_codex_corpus(),
            environment={"PATH": "/bin"},
            transport=wrong_version,
        )
    assert len(observed) == 1

    def api_login(
        command: tuple[str, ...],
        _stdin: str | None,
        _environment: dict[str, str],
        _cwd: Path,
        _timeout_seconds: int,
    ) -> subprocess.CompletedProcess[str]:
        if command[1:] == ("--version",):
            return subprocess.CompletedProcess(command, 0, "codex-cli 0.147.0\n", "")
        return subprocess.CompletedProcess(command, 0, "", "Logged in using an API key\n")

    with raises(evaluation.LongHorizonError, match="ChatGPT subscription login"):
        _build_codex_cli_frontier_call(
            _authorized_codex_corpus(),
            environment={"PATH": "/bin"},
            transport=api_login,
        )

    def ambiguous_login(
        command: tuple[str, ...],
        _stdin: str | None,
        _environment: dict[str, str],
        _cwd: Path,
        _timeout_seconds: int,
    ) -> subprocess.CompletedProcess[str]:
        if command[1:] == ("--version",):
            return subprocess.CompletedProcess(command, 0, "codex-cli 0.147.0\n", "")
        return subprocess.CompletedProcess(
            command,
            0,
            "Logged in using ChatGPT\n",
            "Logged in using an API key\n",
        )

    with raises(evaluation.LongHorizonError, match="missing or ambiguous"):
        _build_codex_cli_frontier_call(
            _authorized_codex_corpus(),
            environment={"PATH": "/bin"},
            transport=ambiguous_login,
        )


def test_codex_cli_usage_shape_diagnostics_are_bounded_and_value_free() -> None:
    valid_usage: dict[str, object] = {
        "input_tokens": 123,
        "cached_input_tokens": 23,
        "output_tokens": 17,
        "reasoning_output_tokens": 7,
    }
    cases = (
        (
            _codex_jsonl_with_usage(None, include_usage=False),
            "usage member is missing",
            (),
        ),
        (_codex_jsonl_with_usage(None), "usage object is null", ()),
        (
            _codex_jsonl_with_usage("private-usage-payload"),
            "usage object has invalid type",
            ("private-usage-payload",),
        ),
        (
            _codex_jsonl_with_usage(
                {name: value for name, value in valid_usage.items() if name != "input_tokens"}
            ),
            "missing required fields: input_tokens",
            (),
        ),
        (
            _codex_jsonl_with_usage({**valid_usage, "total_tokens": 140}),
            "recognized additional fields: total_tokens",
            ("140",),
        ),
        (
            _codex_jsonl_with_usage({**valid_usage, "private_usage_marker": 999}),
            "unrecognized field count: 1",
            ("private_usage_marker", "999"),
        ),
        (
            _codex_jsonl_with_usage({**valid_usage, "input_tokens": "private-token-value"}),
            "invalid required counters: input_tokens",
            ("private-token-value",),
        ),
        (
            _codex_jsonl_with_usage({**valid_usage, "input_tokens": -999}),
            "invalid required counters: input_tokens",
            ("-999",),
        ),
    )
    for stdout, expected, prohibited in cases:
        with raises(evaluation.LongHorizonError) as caught:
            _codex_cli_output_text(stdout, maximum_response_bytes=32_768)
        message = str(caught.value)
        assert expected in message
        assert all(value not in message for value in prohibited)

    with raises(evaluation.LongHorizonError, match="token breakdown exceeds its total"):
        _codex_cli_output_text(
            _codex_jsonl_with_usage({**valid_usage, "cached_input_tokens": 124}),
            maximum_response_bytes=32_768,
        )


def test_codex_cli_usage_accepts_only_zero_cache_write_counter() -> None:
    valid_usage: dict[str, object] = {
        "input_tokens": 123,
        "cached_input_tokens": 23,
        "output_tokens": 17,
        "reasoning_output_tokens": 7,
    }

    assert _codex_cli_output_text(
        _codex_jsonl_with_usage({**valid_usage, "cache_write_input_tokens": 0}),
        maximum_response_bytes=32_768,
    ) == ("{}", 123, 23, 17, 7)

    cases = (
        (1, "nonzero cache-write usage is unsupported"),
        (-1, "invalid recognized counters: cache_write_input_tokens"),
        (True, "invalid recognized counters: cache_write_input_tokens"),
        ("private-token-value", "invalid recognized counters: cache_write_input_tokens"),
    )
    for value, expected in cases:
        with raises(evaluation.LongHorizonError) as caught:
            _codex_cli_output_text(
                _codex_jsonl_with_usage({**valid_usage, "cache_write_input_tokens": value}),
                maximum_response_bytes=32_768,
            )
        message = str(caught.value)
        assert expected in message
        assert str(value) not in message


def test_codex_cli_adapter_rejects_tool_events_and_token_limit_crossing() -> None:
    call_count = 0

    def tool_transport(
        command: tuple[str, ...],
        _stdin: str | None,
        _environment: dict[str, str],
        _cwd: Path,
        _timeout_seconds: int,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal call_count
        if command[1:] == ("--version",):
            return subprocess.CompletedProcess(command, 0, "codex-cli 0.147.0\n", "")
        if command[1:] == ("login", "status"):
            return subprocess.CompletedProcess(command, 0, "Logged in using ChatGPT\n", "")
        call_count += 1
        stdout = "\n".join(
            (
                json.dumps({"type": "thread.started", "thread_id": "transient"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"id": "item_0", "type": "command_execution"},
                    }
                ),
            )
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    tool_call = _build_codex_cli_frontier_call(
        _authorized_codex_corpus(maximum_calls=1),
        environment={"PATH": "/bin"},
        transport=tool_transport,
    )
    with raises(evaluation.LongHorizonError, match="tool item is not allowed"):
        tool_call("review", "review")
    with raises(evaluation.LongHorizonError, match="call limit reached"):
        tool_call("review", "do not retry a failed call")

    def token_transport(
        command: tuple[str, ...],
        _stdin: str | None,
        _environment: dict[str, str],
        _cwd: Path,
        _timeout_seconds: int,
    ) -> subprocess.CompletedProcess[str]:
        if command[1:] == ("--version",):
            return subprocess.CompletedProcess(command, 0, "codex-cli 0.147.0\n", "")
        if command[1:] == ("login", "status"):
            return subprocess.CompletedProcess(command, 0, "Logged in using ChatGPT\n", "")
        return subprocess.CompletedProcess(command, 0, _codex_jsonl({}, input_tokens=30), "")

    token_call = _build_codex_cli_frontier_call(
        _authorized_codex_corpus(maximum_frontier_tokens=10),
        environment={"PATH": "/bin"},
        transport=token_transport,
    )
    with raises(evaluation.LongHorizonError, match="reported usage above the token limit"):
        token_call("review", "review")
    with raises(evaluation.LongHorizonError, match="token limit reached"):
        token_call("review", "review again")
    assert call_count == 1


def test_frontier_dispatch_selects_codex_cli_adapter(monkeypatch: MonkeyPatch) -> None:
    corpus = _authorized_codex_corpus()
    sentinel = cast(evaluation.FrontierCall, lambda _purpose, _prompt: None)
    observed: list[dict[str, object]] = []

    def fake_builder(value: dict[str, object]) -> evaluation.FrontierCall:
        observed.append(value)
        return sentinel

    monkeypatch.setattr(evaluation, "_build_codex_cli_frontier_call", fake_builder)
    assert _build_frontier_call(corpus) is sentinel
    assert observed == [corpus]


def test_subscription_analysis_reports_tokens_without_api_dollar_estimate() -> None:
    corpus = _load_corpus(DEFAULT_CODEX_CLI_SUPERVISION_CORPUS)
    analysis = analyze_supervision([], corpus, expected_variant_count=1)
    analysis.update({"run_id": "subscription", "run_role": "engineering_dry_run"})

    assert analysis["frontier_billing_mode"] == "chatgpt_subscription"
    assert analysis["conservative_frontier_cost_usd_at_configured_rates"] is None
    report = evaluation._supervision_report(analysis)
    assert "Frontier dollar cost: `not applicable (ChatGPT subscription)`" in report


def test_gated_v2_skips_frontier_for_a_deterministically_supported_candidate(
    monkeypatch: MonkeyPatch,
) -> None:
    corpus = _load_corpus(DEFAULT_GATED_SUPERVISION_CORPUS)

    def fake_post(_base_url: str, _path: str, _payload: dict[str, object]) -> dict[str, object]:
        return _model_result(_candidate({"timezone_mode": "iana"}))

    def forbidden_frontier(_purpose: str, _prompt: str) -> FrontierCallResult:
        raise AssertionError(
            "a deterministically supported candidate must not spend frontier tokens"
        )

    monkeypatch.setattr(evaluation, "_post", fake_post)
    generated = _generate_supervised_candidate(
        model_url="http://127.0.0.1:11434",
        executor_payload={
            "model": "ministral-3:8b-instruct-2512-q4_K_M",
            "prompt": "BASE",
            "format": "json",
        },
        base_prompt="BASE",
        corpus=corpus,
        frontier_call=forbidden_frontier,
        verification_atoms=(_constraint_atom("timezone_mode", "iana"),),
        verification_candidate_base={},
    )

    assert generated.changes == {"timezone_mode": "iana"}
    assert generated.executor_model_call_count == 1
    assert generated.frontier_advisor_call_count == 0
    assert generated.frontier_usage["prompt_eval_count"] == 0
    assert generated.review_statuses == ()
    assert generated.escalation_required is False


def test_v1_still_reviews_a_deterministically_supported_candidate(
    monkeypatch: MonkeyPatch,
) -> None:
    corpus = _load_corpus(DEFAULT_SUPERVISION_CORPUS)
    reviews = 0

    def fake_post(_base_url: str, _path: str, _payload: dict[str, object]) -> dict[str, object]:
        return _model_result(_candidate({"timezone_mode": "iana"}))

    def fake_frontier(purpose: str, _prompt: str) -> FrontierCallResult:
        nonlocal reviews
        reviews += 1
        return FrontierCallResult(
            purpose=purpose,
            response_text=json.dumps(
                {"status": "pass", "failed_fields": [], "repair_steps": [], "uncertainty": ""}
            ),
            input_tokens=10,
            output_tokens=5,
            latency_ns=100,
            provider_id="openai",
            model_id="gpt-5.6-sol",
        )

    monkeypatch.setattr(evaluation, "_post", fake_post)
    generated = _generate_supervised_candidate(
        model_url="http://127.0.0.1:11434",
        executor_payload={
            "model": "ministral-3:8b-instruct-2512-q4_K_M",
            "prompt": "BASE",
            "format": "json",
        },
        base_prompt="BASE",
        corpus=corpus,
        frontier_call=fake_frontier,
        verification_atoms=(_constraint_atom("timezone_mode", "iana"),),
        verification_candidate_base={},
    )

    assert generated.changes == {"timezone_mode": "iana"}
    assert reviews == 1
    assert generated.frontier_advisor_call_count == 1


def test_gated_v2_sends_unsupported_changes_to_frontier_and_fails_closed(
    monkeypatch: MonkeyPatch,
) -> None:
    corpus = _load_corpus(DEFAULT_GATED_SUPERVISION_CORPUS)
    reviews = 0

    def fake_post(_base_url: str, _path: str, _payload: dict[str, object]) -> dict[str, object]:
        return _model_result(_candidate({"timezone_mode": "iana", "timezone": "Asia/Manila"}))

    def fake_frontier(purpose: str, _prompt: str) -> FrontierCallResult:
        nonlocal reviews
        reviews += 1
        return FrontierCallResult(
            purpose=purpose,
            response_text=json.dumps(
                {"status": "pass", "failed_fields": [], "repair_steps": [], "uncertainty": ""}
            ),
            input_tokens=10,
            output_tokens=5,
            latency_ns=100,
            provider_id="openai",
            model_id="gpt-5.6-sol",
        )

    monkeypatch.setattr(evaluation, "_post", fake_post)
    generated = _generate_supervised_candidate(
        model_url="http://127.0.0.1:11434",
        executor_payload={
            "model": "ministral-3:8b-instruct-2512-q4_K_M",
            "prompt": "BASE",
            "format": "json",
        },
        base_prompt="BASE",
        corpus=corpus,
        frontier_call=fake_frontier,
        verification_atoms=(_constraint_atom("timezone_mode", "iana"),),
        verification_candidate_base={},
    )

    assert reviews == 1
    assert generated.response is None
    assert generated.changes == {}
    assert generated.escalation_required is True


def test_gated_v2_promotes_an_invalid_draft_and_keeps_the_final_review(
    monkeypatch: MonkeyPatch,
) -> None:
    corpus = _load_corpus(DEFAULT_GATED_SUPERVISION_CORPUS)
    calls: list[str] = []

    def fake_post(_base_url: str, _path: str, payload: dict[str, object]) -> dict[str, object]:
        prompt = str(payload["prompt"])
        calls.append("executor")
        if "FRONTIER ADVISOR REPAIR PACKET" not in prompt:
            return _model_result(_candidate({"not_a_config_field": True}))
        return _model_result(_candidate({"timezone_mode": "iana"}))

    def fake_frontier(purpose: str, prompt: str) -> FrontierCallResult:
        calls.append("frontier")
        value = (
            {"status": "pass", "failed_fields": [], "repair_steps": [], "uncertainty": ""}
            if "REVIEW_ROUND: final" in prompt
            else {
                "status": "repair",
                "failed_fields": ["timezone_mode"],
                "repair_steps": ["Return the supported timezone mode."],
                "uncertainty": "",
            }
        )
        return FrontierCallResult(
            purpose=purpose,
            response_text=json.dumps(value),
            input_tokens=10,
            output_tokens=5,
            latency_ns=100,
            provider_id="openai",
            model_id="gpt-5.6-sol",
        )

    monkeypatch.setattr(evaluation, "_post", fake_post)
    generated = _generate_supervised_candidate(
        model_url="http://127.0.0.1:11434",
        executor_payload={
            "model": "ministral-3:8b-instruct-2512-q4_K_M",
            "prompt": "BASE",
            "format": "json",
        },
        base_prompt="BASE",
        corpus=corpus,
        frontier_call=fake_frontier,
        verification_atoms=(_constraint_atom("timezone_mode", "iana"),),
        verification_candidate_base={},
    )

    assert calls == ["executor", "frontier", "executor", "frontier"]
    assert generated.changes == {"timezone_mode": "iana"}
    assert generated.invalid_changes == 1
    assert generated.review_statuses == ("repair", "pass")
    assert generated.frontier_advisor_call_count == 2
    assert generated.escalation_required is False


def test_hybrid_plan_first_runs_plan_executor_review_repair_review(
    monkeypatch: MonkeyPatch,
) -> None:
    corpus = _load_corpus(evaluation.DEFAULT_HYBRID_SUPERVISION_CORPUS)
    calls: list[str] = []

    def fake_post(_base_url: str, _path: str, payload: dict[str, object]) -> dict[str, object]:
        prompt = str(payload["prompt"])
        calls.append("executor")
        assert "Verify authorization boundary first." in prompt
        if "FRONTIER ADVISOR REPAIR PACKET" in prompt:
            return _model_result(_candidate({"timezone_mode": "iana"}))
        return _model_result(_candidate({"timezone_mode": "offset"}))

    def fake_frontier(purpose: str, prompt: str) -> FrontierCallResult:
        calls.append(purpose)
        if purpose == "plan":
            value = {
                "steps": [
                    "Verify authorization boundary first.",
                    "Apply only supported changes.",
                ],
                "acceptance_checks": ["Memory constraints match.", "Output schema is valid."],
                "uncertainty": "",
            }
        elif "REVIEW_ROUND: final" in prompt:
            value = {
                "status": "pass",
                "failed_fields": [],
                "repair_steps": [],
                "uncertainty": "",
            }
        else:
            value = {
                "status": "repair",
                "failed_fields": ["timezone_mode"],
                "repair_steps": ["Use the remembered IANA mode."],
                "uncertainty": "",
            }
        return FrontierCallResult(
            purpose=purpose,
            response_text=json.dumps(value),
            input_tokens=10,
            output_tokens=5,
            latency_ns=100,
            provider_id="codex_cli",
            model_id="gpt-5.6-sol",
        )

    monkeypatch.setattr(evaluation, "_post", fake_post)
    generated = _generate_supervised_candidate(
        model_url="http://127.0.0.1:11434",
        executor_payload={
            "model": "ministral-3:8b-instruct-2512-q4_K_M",
            "prompt": "BASE",
            "format": "json",
        },
        base_prompt="BASE",
        corpus=corpus,
        frontier_call=fake_frontier,
        verification_atoms=(_constraint_atom("timezone_mode", "iana"),),
        verification_candidate_base={},
        frontier_plan_first=True,
        frontier_risk_tags=("authorization",),
    )

    assert calls == ["plan", "executor", "review", "executor", "review"]
    assert generated.changes == {"timezone_mode": "iana"}
    assert generated.frontier_plan_call_count == 1
    assert generated.executor_model_call_count == 2
    assert generated.frontier_advisor_call_count == 3
    assert generated.routing_decision == "frontier_plan_first"
    assert generated.review_statuses == ("repair", "pass")
    assert generated.escalation_required is False


def test_hybrid_plan_first_rejects_an_invalid_plan_before_local_execution(
    monkeypatch: MonkeyPatch,
) -> None:
    corpus = _load_corpus(evaluation.DEFAULT_HYBRID_SUPERVISION_CORPUS)

    def forbidden_post(
        _base_url: str, _path: str, _payload: dict[str, object]
    ) -> dict[str, object]:
        raise AssertionError("an invalid frontier plan must fail before local execution")

    def invalid_frontier(purpose: str, _prompt: str) -> FrontierCallResult:
        assert purpose == "plan"
        return FrontierCallResult(
            purpose=purpose,
            response_text=json.dumps(
                {"steps": [], "acceptance_checks": [], "uncertainty": "private detail"}
            ),
            input_tokens=10,
            output_tokens=5,
            latency_ns=100,
            provider_id="codex_cli",
            model_id="gpt-5.6-sol",
        )

    monkeypatch.setattr(evaluation, "_post", forbidden_post)
    with raises(evaluation.LongHorizonError, match="frontier plan is invalid") as caught:
        _generate_supervised_candidate(
            model_url="http://127.0.0.1:11434",
            executor_payload={
                "model": "ministral-3:8b-instruct-2512-q4_K_M",
                "prompt": "BASE",
                "format": "json",
            },
            base_prompt="BASE",
            corpus=corpus,
            frontier_call=invalid_frontier,
            verification_atoms=(),
            frontier_plan_first=True,
            frontier_risk_tags=("authorization",),
        )
    assert "private detail" not in str(caught.value)


def test_hybrid_trajectory_selects_plan_first_and_keeps_plan_text_transient(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    corpus = _load_corpus(evaluation.DEFAULT_HYBRID_SUPERVISION_CORPUS)
    variant = _variant(corpus, 0)
    secret_plan = "PRIVATE_PLAN_TEXT_DO_NOT_PERSIST"

    def fake_post(_base_url: str, _path: str, payload: dict[str, object]) -> dict[str, object]:
        prompt = str(payload["prompt"])
        session = next(number for number in (1, 2, 3) if f"SESSION: {number} of 3" in prompt)
        if session in {1, 3}:
            assert secret_plan in prompt
        else:
            assert secret_plan not in prompt
        return _model_result(_candidate(_expected(variant, session=session)))

    def fake_frontier(purpose: str, _prompt: str) -> FrontierCallResult:
        value = (
            {
                "steps": [secret_plan],
                "acceptance_checks": ["All deterministic checks pass."],
                "uncertainty": "",
            }
            if purpose == "plan"
            else {
                "status": "pass",
                "failed_fields": [],
                "repair_steps": [],
                "uncertainty": "",
            }
        )
        return FrontierCallResult(
            purpose=purpose,
            response_text=json.dumps(value),
            input_tokens=10,
            output_tokens=5,
            latency_ns=100,
            provider_id="codex_cli",
            model_id="gpt-5.6-sol",
        )

    monkeypatch.setattr(evaluation, "_post", fake_post)
    raw_sessions = tmp_path / "sessions.jsonl"
    trajectory = _trajectory(
        corpus=corpus,
        variant=variant,
        condition="SS",
        model_url="http://127.0.0.1:11434",
        raw_sessions=raw_sessions,
        attempt=1,
        frontier_call=fake_frontier,
    )

    assert trajectory["end_to_end_success"] is True
    assert trajectory["executor_model_call_count"] == 3
    assert trajectory["frontier_advisor_call_count"] == 4
    assert trajectory["frontier_plan_call_count"] == 2
    assert trajectory["frontier_plan_first_session_count"] == 2
    assert trajectory["local_first_session_count"] == 1
    persisted = raw_sessions.read_text(encoding="utf-8")
    assert secret_plan not in persisted
    session_rows = [json.loads(line) for line in persisted.splitlines()]
    assert [row["routing_decision"] for row in session_rows] == [
        "frontier_plan_first",
        "local_first",
        "frontier_plan_first",
    ]
    assert [row["routing_reason_codes"] for row in session_rows] == [
        ["authorization"],
        ["bounded_mechanical"],
        ["migration"],
    ]


def test_gated_v2_trajectory_can_complete_without_frontier_reviews(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    corpus = _load_corpus(DEFAULT_GATED_SUPERVISION_CORPUS)
    variant = _variant(corpus, 0)

    def fake_post(_base_url: str, _path: str, payload: dict[str, object]) -> dict[str, object]:
        prompt = str(payload["prompt"])
        session = next(number for number in (1, 2, 3) if f"SESSION: {number} of 3" in prompt)
        return _model_result(_candidate(_expected(variant, session=session)))

    def forbidden_frontier(_purpose: str, _prompt: str) -> FrontierCallResult:
        raise AssertionError("all three local candidates have complete structured support")

    monkeypatch.setattr(evaluation, "_post", fake_post)
    trajectory = _trajectory(
        corpus=corpus,
        variant=variant,
        condition="SS",
        model_url="http://127.0.0.1:11434",
        raw_sessions=tmp_path / "sessions.jsonl",
        attempt=1,
        frontier_call=forbidden_frontier,
    )

    assert trajectory["end_to_end_success"] is True
    assert trajectory["executor_model_call_count"] == 3
    assert trajectory["frontier_advisor_call_count"] == 0
    assert trajectory["frontier_advisor_input_tokens"] == 0
    assert trajectory["frontier_advisor_output_tokens"] == 0
    assert trajectory["frontier_escalation_count"] == 0


def test_local_first_v5_trajectory_uses_zero_frontier_on_deterministic_passes(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    corpus = _load_corpus(evaluation.DEFAULT_LOCAL_FIRST_SAVINGS_CORPUS)
    variant = _variant(corpus, 0)

    def fake_post(_base_url: str, _path: str, payload: dict[str, object]) -> dict[str, object]:
        prompt = str(payload["prompt"])
        session = next(number for number in (1, 2, 3) if f"SESSION: {number} of 3" in prompt)
        return _model_result(_candidate(_expected(variant, session=session)))

    def forbidden_frontier(_purpose: str, _prompt: str) -> FrontierCallResult:
        raise AssertionError("a deterministic local pass must preserve subscription capacity")

    monkeypatch.setattr(evaluation, "_post", fake_post)
    raw_sessions = tmp_path / "sessions.jsonl"
    trajectory = _trajectory(
        corpus=corpus,
        variant=variant,
        condition="SS",
        model_url="http://127.0.0.1:11434",
        raw_sessions=raw_sessions,
        attempt=1,
        frontier_call=forbidden_frontier,
    )

    assert trajectory["end_to_end_success"] is True
    assert trajectory["executor_model_call_count"] == 3
    assert trajectory["frontier_advisor_call_count"] == 0
    assert trajectory["frontier_plan_call_count"] == 0
    assert trajectory["frontier_plan_first_session_count"] == 0
    assert trajectory["local_first_session_count"] == 3
    assert [
        json.loads(line)["routing_decision"]
        for line in raw_sessions.read_text(encoding="utf-8").splitlines()
    ] == ["local_first", "local_first", "local_first"]


def test_takeover_v6_trajectory_continues_after_verified_direct_execution(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    corpus = _load_corpus(evaluation.DEFAULT_TAKEOVER_SUPERVISION_CORPUS)
    variant = _variant(corpus, 0)
    local_sessions: list[int] = []
    frontier_purposes: list[str] = []

    def fake_post(_base_url: str, _path: str, payload: dict[str, object]) -> dict[str, object]:
        prompt = str(payload["prompt"])
        session = next(number for number in (1, 2, 3) if f"SESSION: {number} of 3" in prompt)
        local_sessions.append(session)
        changes: dict[str, object] = (
            {"authorization_role": "unsupported-role"}
            if session == 1
            else _expected(variant, session=session)
        )
        return _model_result(_candidate(changes))

    def fake_frontier(purpose: str, prompt: str) -> FrontierCallResult:
        frontier_purposes.append(purpose)
        session = next(number for number in (1, 2, 3) if f"SESSION: {number} of 3" in prompt)
        full_changes = _full_frontier_changes(corpus)
        full_changes.update(_expected(variant, session=session))
        return FrontierCallResult(
            purpose=purpose,
            response_text=json.dumps(_candidate(full_changes)),
            input_tokens=20,
            output_tokens=7,
            latency_ns=200,
            provider_id="codex_cli",
            model_id="gpt-5.6-sol",
        )

    monkeypatch.setattr(evaluation, "_post", fake_post)
    raw_sessions = tmp_path / "sessions.jsonl"
    trajectory = _trajectory(
        corpus=corpus,
        variant=variant,
        condition="SS",
        model_url="http://127.0.0.1:11434",
        raw_sessions=raw_sessions,
        attempt=1,
        frontier_call=fake_frontier,
    )

    assert trajectory["end_to_end_success"] is True
    assert local_sessions == [1, 2, 3]
    assert frontier_purposes == ["direct"]
    assert trajectory["executor_model_call_count"] == 3
    assert trajectory["frontier_advisor_call_count"] == 1
    assert trajectory["frontier_takeover_call_count"] == 1
    session_rows = [
        json.loads(line) for line in raw_sessions.read_text(encoding="utf-8").splitlines()
    ]
    assert session_rows[0]["accepted_changes"] == {
        name: value
        for name, value in _expected(variant, session=1).items()
        if value != cast(dict[str, object], corpus["initial_config"])[name]
    }
    assert [row["frontier_takeover_call_count"] for row in session_rows] == [1, 0, 0]
    assert [row["frontier_review_statuses"] for row in session_rows] == [[], [], []]


def test_local_first_v5_trajectory_stops_after_unresolved_final_review(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    corpus = _load_corpus(evaluation.DEFAULT_LOCAL_FIRST_SAVINGS_CORPUS)
    variant = _variant(corpus, 0)
    local_sessions: list[int] = []

    def fake_post(_base_url: str, _path: str, payload: dict[str, object]) -> dict[str, object]:
        prompt = str(payload["prompt"])
        session = next(number for number in (1, 2, 3) if f"SESSION: {number} of 3" in prompt)
        local_sessions.append(session)
        changes: dict[str, object] = (
            {"authorization_role": "clinician"}
            if session == 1
            else _expected(variant, session=session)
        )
        return _model_result(_candidate(changes))

    def unresolved_frontier(purpose: str, _prompt: str) -> FrontierCallResult:
        return FrontierCallResult(
            purpose=purpose,
            response_text=json.dumps(
                {
                    "status": "repair",
                    "failed_fields": ["authorization_role"],
                    "repair_steps": ["Use the remembered authorization role."],
                    "uncertainty": "",
                }
            ),
            input_tokens=10,
            output_tokens=5,
            latency_ns=100,
            provider_id="codex_cli",
            model_id="gpt-5.6-sol",
        )

    monkeypatch.setattr(evaluation, "_post", fake_post)
    raw_sessions = tmp_path / "sessions.jsonl"
    with raises(evaluation.LongHorizonError, match="supervised session remained unresolved"):
        _trajectory(
            corpus=corpus,
            variant=variant,
            condition="SS",
            model_url="http://127.0.0.1:11434",
            raw_sessions=raw_sessions,
            attempt=1,
            frontier_call=unresolved_frontier,
        )

    assert local_sessions == [1, 1]
    session_rows = [
        json.loads(line) for line in raw_sessions.read_text(encoding="utf-8").splitlines()
    ]
    assert len(session_rows) == 1
    assert session_rows[0]["accepted_changes"] == {}
    assert session_rows[0]["frontier_review_statuses"] == ["repair", "repair"]
    assert session_rows[0]["frontier_escalation_required"] is True


def test_takeover_v6_uses_one_direct_frontier_execution_after_local_gate_failure(
    monkeypatch: MonkeyPatch,
) -> None:
    corpus = _load_corpus(evaluation.DEFAULT_TAKEOVER_SUPERVISION_CORPUS)
    calls: list[tuple[str, str]] = []
    candidate_base = _full_frontier_changes(corpus, authorization_role="scheduler")

    def fake_post(_base_url: str, _path: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append(("local", str(payload["prompt"])))
        return _model_result(_candidate({"timezone_mode": "offset"}))

    def fake_frontier(purpose: str, prompt: str) -> FrontierCallResult:
        calls.append((purpose, prompt))
        return FrontierCallResult(
            purpose=purpose,
            response_text=json.dumps(
                _candidate(
                    _full_frontier_changes(
                        corpus,
                        authorization_role="scheduler",
                        timezone_mode="iana",
                    )
                )
            ),
            input_tokens=20,
            output_tokens=7,
            latency_ns=200,
            provider_id="codex_cli",
            model_id="gpt-5.6-sol",
        )

    monkeypatch.setattr(evaluation, "_post", fake_post)
    generated = _generate_supervised_candidate(
        model_url="http://127.0.0.1:11434",
        executor_payload={
            "model": "ministral-3:8b-instruct-2512-q4_K_M",
            "prompt": "BASE",
            "format": "json",
        },
        base_prompt="BASE",
        corpus=corpus,
        frontier_call=fake_frontier,
        verification_atoms=(_constraint_atom("timezone_mode", "iana"),),
        verification_candidate_base=candidate_base,
    )

    assert calls == [("local", "BASE"), ("direct", "BASE")]
    assert generated.changes == {"timezone_mode": "iana"}
    assert generated.response is not None
    assert generated.response["changes"] == {"timezone_mode": "iana"}
    assert generated.executor_model_call_count == 1
    assert generated.frontier_advisor_call_count == 1
    assert generated.frontier_takeover_call_count == 1
    assert generated.review_statuses == ()
    assert generated.escalation_required is False
    assert generated.executor_usage["prompt_eval_count"] == 10
    assert generated.frontier_usage["prompt_eval_count"] == 20


def test_takeover_v6_cannot_override_a_deterministic_mismatch(
    monkeypatch: MonkeyPatch,
) -> None:
    corpus = _load_corpus(evaluation.DEFAULT_TAKEOVER_SUPERVISION_CORPUS)
    candidate_base = _full_frontier_changes(corpus, authorization_role="scheduler")

    def fake_post(_base_url: str, _path: str, _payload: dict[str, object]) -> dict[str, object]:
        return _model_result(_candidate({"timezone_mode": "offset"}))

    def fake_frontier(purpose: str, _prompt: str) -> FrontierCallResult:
        return FrontierCallResult(
            purpose=purpose,
            response_text=json.dumps(_candidate(candidate_base)),
            input_tokens=20,
            output_tokens=7,
            latency_ns=200,
            provider_id="codex_cli",
            model_id="gpt-5.6-sol",
        )

    monkeypatch.setattr(evaluation, "_post", fake_post)
    generated = _generate_supervised_candidate(
        model_url="http://127.0.0.1:11434",
        executor_payload={
            "model": "ministral-3:8b-instruct-2512-q4_K_M",
            "prompt": "BASE",
            "format": "json",
        },
        base_prompt="BASE",
        corpus=corpus,
        frontier_call=fake_frontier,
        verification_atoms=(_constraint_atom("timezone_mode", "iana"),),
        verification_candidate_base=candidate_base,
    )

    assert generated.response is None
    assert generated.changes == {}
    assert generated.executor_model_call_count == 1
    assert generated.frontier_advisor_call_count == 1
    assert generated.frontier_takeover_call_count == 1
    assert generated.review_statuses == ()
    assert generated.escalation_required is True


def test_takeover_v6_rejects_an_unsupported_effective_change(
    monkeypatch: MonkeyPatch,
) -> None:
    corpus = _load_corpus(evaluation.DEFAULT_TAKEOVER_SUPERVISION_CORPUS)
    candidate_base = _full_frontier_changes(corpus, authorization_role="scheduler")

    def fake_post(_base_url: str, _path: str, _payload: dict[str, object]) -> dict[str, object]:
        return _model_result(_candidate({"timezone_mode": "offset"}))

    def fake_frontier(purpose: str, _prompt: str) -> FrontierCallResult:
        return FrontierCallResult(
            purpose=purpose,
            response_text=json.dumps(
                _candidate(
                    _full_frontier_changes(
                        corpus,
                        authorization_role="scheduler",
                        timezone_mode="iana",
                        atomic_reservation=True,
                    )
                )
            ),
            input_tokens=20,
            output_tokens=7,
            latency_ns=200,
            provider_id="codex_cli",
            model_id="gpt-5.6-sol",
        )

    monkeypatch.setattr(evaluation, "_post", fake_post)
    generated = _generate_supervised_candidate(
        model_url="http://127.0.0.1:11434",
        executor_payload={
            "model": "ministral-3:8b-instruct-2512-q4_K_M",
            "prompt": "BASE",
            "format": "json",
        },
        base_prompt="BASE",
        corpus=corpus,
        frontier_call=fake_frontier,
        verification_atoms=(_constraint_atom("timezone_mode", "iana"),),
        verification_candidate_base=candidate_base,
    )

    assert generated.response is None
    assert generated.changes == {}
    assert generated.frontier_takeover_call_count == 1
    assert generated.escalation_required is True
    assert "atomic_reservation" in cast(
        list[str], generated.verification_reports[-1]["unverifiable_fields"]
    )


def test_supervised_candidate_is_one_repair_between_two_reviews(
    monkeypatch: MonkeyPatch,
) -> None:
    corpus = _load_corpus(DEFAULT_SUPERVISION_CORPUS)
    calls: list[tuple[str, str]] = []

    def fake_post(_base_url: str, _path: str, payload: dict[str, object]) -> dict[str, object]:
        model = str(payload["model"])
        prompt = str(payload["prompt"])
        calls.append((model, prompt))
        if "FRONTIER ADVISOR REPAIR PACKET" not in prompt:
            return _model_result(_candidate({"timezone_mode": "offset"}))
        return _model_result(_candidate({"timezone_mode": "iana"}))

    def fake_frontier(purpose: str, prompt: str) -> FrontierCallResult:
        calls.append(("frontier-fixture", prompt))
        if "REVIEW_ROUND: final" in prompt:
            value = {
                "status": "pass",
                "failed_fields": [],
                "repair_steps": [],
                "uncertainty": "",
            }
        else:
            value = {
                "status": "repair",
                "failed_fields": ["timezone_mode"],
                "repair_steps": ["Use the supported IANA mode."],
                "uncertainty": "",
            }
        return FrontierCallResult(
            purpose=purpose,
            response_text=json.dumps(value),
            input_tokens=10,
            output_tokens=5,
            latency_ns=100,
            provider_id="openai",
            model_id="gpt-5.6-sol",
        )

    monkeypatch.setattr(evaluation, "_post", fake_post)
    generated = _generate_supervised_candidate(
        model_url="http://127.0.0.1:11434",
        executor_payload={
            "model": "ministral-3:8b-instruct-2512-q4_K_M",
            "prompt": "BASE",
            "format": "json",
        },
        base_prompt="BASE",
        corpus=corpus,
        frontier_call=fake_frontier,
        verification_atoms=(_constraint_atom("timezone_mode", "iana"),),
        verification_candidate_base={},
    )

    assert [model for model, _ in calls] == [
        "ministral-3:8b-instruct-2512-q4_K_M",
        "frontier-fixture",
        "ministral-3:8b-instruct-2512-q4_K_M",
        "frontier-fixture",
    ]
    assert generated.changes == {"timezone_mode": "iana"}
    assert generated.model_call_count == 4
    assert generated.executor_model_call_count == 2
    assert generated.frontier_advisor_call_count == 2
    assert generated.review_statuses == ("repair", "pass")
    assert generated.escalation_required is False
    assert generated.verified_lesson_fields == ("timezone_mode",)
    assert generated.executor_usage["prompt_eval_count"] == 20
    assert generated.frontier_usage["prompt_eval_count"] == 20
    assert generated.actual_usage["prompt_eval_count"] == 40


def test_advisor_pass_cannot_override_a_deterministic_mismatch(
    monkeypatch: MonkeyPatch,
) -> None:
    corpus = _load_corpus(DEFAULT_SUPERVISION_CORPUS)

    def fake_post(_base_url: str, _path: str, payload: dict[str, object]) -> dict[str, object]:
        return _model_result(_candidate({"timezone_mode": "offset"}))

    def fake_frontier(purpose: str, _prompt: str) -> FrontierCallResult:
        return FrontierCallResult(
            purpose=purpose,
            response_text=json.dumps(
                {"status": "pass", "failed_fields": [], "repair_steps": [], "uncertainty": ""}
            ),
            input_tokens=10,
            output_tokens=5,
            latency_ns=100,
            provider_id="openai",
            model_id="gpt-5.6-sol",
        )

    monkeypatch.setattr(evaluation, "_post", fake_post)
    generated = _generate_supervised_candidate(
        model_url="http://127.0.0.1:11434",
        executor_payload={
            "model": "ministral-3:8b-instruct-2512-q4_K_M",
            "prompt": "BASE",
            "format": "json",
        },
        base_prompt="BASE",
        corpus=corpus,
        frontier_call=fake_frontier,
        verification_atoms=(_constraint_atom("timezone_mode", "iana"),),
        verification_candidate_base={},
    )

    assert generated.changes == {}
    assert generated.response is None
    assert generated.escalation_required is True
    assert generated.review_statuses == ("pass",)
    assert generated.verified_lesson_fields == ()
    assert generated.verification_reports[-1]["status"] == "mismatch"


def test_malformed_frontier_review_escalates_without_a_repair(
    monkeypatch: MonkeyPatch,
) -> None:
    corpus = _load_corpus(DEFAULT_SUPERVISION_CORPUS)

    def fake_post(_base_url: str, _path: str, _payload: dict[str, object]) -> dict[str, object]:
        return _model_result(_candidate({"timezone_mode": "iana"}))

    def fake_frontier(purpose: str, _prompt: str) -> FrontierCallResult:
        return FrontierCallResult(
            purpose=purpose,
            response_text='{"status":"pass","unexpected":true}',
            input_tokens=10,
            output_tokens=5,
            latency_ns=100,
            provider_id="openai",
            model_id="gpt-5.6-sol",
        )

    monkeypatch.setattr(evaluation, "_post", fake_post)
    generated = _generate_supervised_candidate(
        model_url="http://127.0.0.1:11434",
        executor_payload={
            "model": "ministral-3:8b-instruct-2512-q4_K_M",
            "prompt": "BASE",
            "format": "json",
        },
        base_prompt="BASE",
        corpus=corpus,
        frontier_call=fake_frontier,
        verification_atoms=(_constraint_atom("timezone_mode", "iana"),),
        verification_candidate_base={},
    )

    assert generated.changes == {}
    assert generated.review_statuses == ("invalid",)
    assert generated.executor_model_call_count == 1
    assert generated.frontier_advisor_call_count == 1
    assert generated.escalation_required is True


def test_verified_repair_memory_contains_only_bounded_field_names() -> None:
    corpus = _load_corpus(DEFAULT_SUPERVISION_CORPUS)
    content = _memory_content(
        condition="SD",
        variant=_variant(corpus, 0),
        session=1,
        config={},
        public_history=[],
        response=None,
        verified_lesson_fields=("authorization_role", "idempotency_key"),
        verified_lesson_evidence_ids=(evaluation._evidence("telehealth-01", "SD", 1).evidence_id,),
    )

    assert content.failures == ()
    assert content.lessons[0].trigger == (
        "executor mismatch fields=authorization_role,idempotency_key"
    )
    assert "repair_steps" not in json.dumps(content.to_dict())


def test_shadow_trajectory_never_persists_review_prose_and_retrieves_verified_lessons(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    corpus = _load_corpus(DEFAULT_SUPERVISION_CORPUS)
    variant = _variant(corpus, 0)
    secret_review = "PRIVATE_REPAIR_PROSE_88b65d"
    rendered_memories: list[str] = []
    paired_control_memory = evaluation._paired_control_memory

    def capture_paired_control_memory(memory: str) -> str:
        rendered_memories.append(memory)
        return paired_control_memory(memory)

    def fake_post(_base_url: str, _path: str, payload: dict[str, object]) -> dict[str, object]:
        prompt = str(payload["prompt"])
        session = next(number for number in (1, 2, 3) if f"SESSION: {number} of 3" in prompt)
        changes = _expected(variant, session=session)
        if "FRONTIER ADVISOR REPAIR PACKET" not in prompt:
            changes["authorization_role"] = next(
                role
                for role in cast(list[str], corpus["authorization_roles"])
                if role != variant["authorization_role"]
            )
        return _model_result(_candidate(changes))

    def fake_frontier(purpose: str, prompt: str) -> FrontierCallResult:
        if "REVIEW_ROUND: final" in prompt:
            value = {"status": "pass", "failed_fields": [], "repair_steps": [], "uncertainty": ""}
        else:
            value = {
                "status": "repair",
                "failed_fields": ["authorization_role"],
                "repair_steps": [secret_review],
                "uncertainty": "",
            }
        return FrontierCallResult(
            purpose=purpose,
            response_text=json.dumps(value),
            input_tokens=10,
            output_tokens=5,
            latency_ns=100,
            provider_id="openai",
            model_id="gpt-5.6-sol",
        )

    monkeypatch.setattr(evaluation, "_post", fake_post)
    monkeypatch.setattr(evaluation, "_paired_control_memory", capture_paired_control_memory)
    raw_sessions = tmp_path / "sessions.jsonl"
    trajectory = _trajectory(
        corpus=corpus,
        variant=variant,
        condition="SS",
        model_url="http://127.0.0.1:11434",
        raw_sessions=raw_sessions,
        attempt=1,
        frontier_call=fake_frontier,
    )

    persisted = raw_sessions.read_text(encoding="utf-8")
    assert secret_review not in persisted
    assert '"repair_steps"' not in persisted
    assert trajectory["verified_lesson_count"] == 3
    assert any(
        "Correction lesson:" in memory and "authorization_role" in memory
        for memory in rendered_memories
    ), [
        line
        for memory in rendered_memories
        for line in memory.splitlines()
        if "Correction" in line or line.startswith("KNOW")
    ]
    assert cast(int, trajectory["verified_lesson_retrieval_count"]) >= 1
    assert trajectory["frontier_escalation_count"] == 0
    assert trajectory["executor_model_call_count"] == 6
    assert trajectory["frontier_advisor_call_count"] == 6


def test_direct_frontier_condition_never_calls_the_local_executor(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    corpus = _load_corpus(DEFAULT_SUPERVISION_CORPUS)
    variant = _variant(corpus, 0)

    def forbidden_local_call(
        _base_url: str, _path: str, _payload: dict[str, object]
    ) -> dict[str, object]:
        raise AssertionError("TD must not call the local executor")

    def fake_frontier(purpose: str, prompt: str) -> FrontierCallResult:
        session = next(number for number in (1, 2, 3) if f"SESSION: {number} of 3" in prompt)
        return FrontierCallResult(
            purpose=purpose,
            response_text=json.dumps(_candidate(_expected(variant, session=session))),
            input_tokens=10,
            output_tokens=5,
            latency_ns=100,
            provider_id="openai",
            model_id="gpt-5.6-sol",
        )

    monkeypatch.setattr(evaluation, "_post", forbidden_local_call)
    trajectory = _trajectory(
        corpus=corpus,
        variant=variant,
        condition="TD",
        model_url="http://127.0.0.1:11434",
        raw_sessions=tmp_path / "sessions.jsonl",
        attempt=1,
        frontier_call=fake_frontier,
    )

    assert trajectory["end_to_end_success"] is True
    assert trajectory["executor_model_call_count"] == 0
    assert trajectory["frontier_advisor_call_count"] == 3


def test_supervision_analysis_distinguishes_promising_from_quality_only() -> None:
    corpus = _load_corpus(DEFAULT_SUPERVISION_CORPUS)
    rows: list[dict[str, object]] = []
    for index in range(30):
        variant_id = f"telehealth-{index + 1:02d}"
        common = {
            "variant_id": variant_id,
            "available": True,
            "end_to_end_success": True,
            "critical_false_memory_count": 0,
            "exact_value_integrity_rate": 1.0,
            "repeated_error_count": 0,
            "frontier_escalation_count": 0,
            "verified_lesson_count": 1,
            "verified_lesson_retrieval_count": 1,
            "executor_model_call_count": 3,
            "frontier_advisor_call_count": 0,
            "executor_model_input_tokens": 100,
            "executor_model_output_tokens": 20,
            "frontier_advisor_input_tokens": 0,
            "frontier_advisor_output_tokens": 0,
            "actual_latency_ns": 100,
        }
        rows.append({**common, "condition": "SD", "hidden_test_accuracy": 0.6})
        rows.append(
            {
                **common,
                "condition": "SS",
                "hidden_test_accuracy": 0.8,
                "frontier_advisor_call_count": 3,
                "frontier_advisor_input_tokens": 60,
                "frontier_advisor_output_tokens": 10,
            }
        )
        rows.append(
            {
                **common,
                "condition": "TD",
                "hidden_test_accuracy": 0.8,
                "executor_model_call_count": 0,
                "executor_model_input_tokens": 0,
                "executor_model_output_tokens": 0,
                "frontier_advisor_call_count": 3,
                "frontier_advisor_input_tokens": 100,
                "frontier_advisor_output_tokens": 20,
            }
        )

    promising = analyze_supervision(rows, corpus, expected_variant_count=30)
    assert promising["verdict"] == "PROMISING"
    supervised_vs_executor = cast(dict[str, object], promising["supervised_vs_executor"])
    supervised_vs_frontier = cast(dict[str, object], promising["supervised_vs_frontier"])
    assert supervised_vs_executor["mean_difference"] == approx(0.2)
    assert supervised_vs_frontier["mean_difference"] == approx(0.0)
    assert promising["frontier_token_savings_vs_direct"] == approx(1 - 70 / 120)
    assert promising["conservative_frontier_cost_usd_at_configured_rates"] == approx(0.051)
    router_goal = cast(dict[str, object], promising["router_goal"])
    assert router_goal == {
        "verdict": "ACHIEVED",
        "paired_count": 30,
        "required_pair_count": 30,
        "completion_pass": True,
        "critical_safety_pass": True,
        "frontier_quality_difference": approx(0.0),
        "frontier_quality_pass": True,
        "frontier_token_savings": approx(1 - 70 / 120),
        "frontier_token_savings_pass": True,
    }

    incomplete_rows = [dict(row) for row in rows]
    next(
        row
        for row in incomplete_rows
        if row["condition"] == "SS" and row["variant_id"] == "telehealth-01"
    )["end_to_end_success"] = False
    incomplete_goal = cast(
        dict[str, object],
        analyze_supervision(incomplete_rows, corpus, expected_variant_count=30)["router_goal"],
    )
    assert incomplete_goal["verdict"] == "NOT_ACHIEVED"
    assert incomplete_goal["completion_pass"] is False

    unsafe_rows = [dict(row) for row in rows]
    next(row for row in unsafe_rows if row["condition"] == "SS")["critical_false_memory_count"] = 1
    unsafe_goal = cast(
        dict[str, object],
        analyze_supervision(unsafe_rows, corpus, expected_variant_count=30)["router_goal"],
    )
    assert unsafe_goal["verdict"] == "NOT_ACHIEVED"
    assert unsafe_goal["critical_safety_pass"] is False

    lower_quality_rows = [dict(row) for row in rows]
    for row in lower_quality_rows:
        if row["condition"] == "SS":
            row["hidden_test_accuracy"] = 0.77
    lower_quality_goal = cast(
        dict[str, object],
        analyze_supervision(lower_quality_rows, corpus, expected_variant_count=30)["router_goal"],
    )
    assert lower_quality_goal["verdict"] == "NOT_ACHIEVED"
    assert lower_quality_goal["frontier_quality_difference"] == approx(-0.03)
    assert lower_quality_goal["frontier_quality_pass"] is False

    for row in rows:
        if row["condition"] == "SS":
            row["frontier_advisor_input_tokens"] = 130
            row["frontier_advisor_output_tokens"] = 20
    quality_only = analyze_supervision(rows, corpus, expected_variant_count=30)
    assert quality_only["verdict"] == "QUALITY_ONLY"
    quality_only_goal = cast(dict[str, object], quality_only["router_goal"])
    assert quality_only_goal["verdict"] == "NOT_ACHIEVED"
    assert quality_only_goal["frontier_token_savings_pass"] is False


def test_local_first_v5_savings_metric_excludes_local_executor_tokens() -> None:
    corpus = _load_corpus(evaluation.DEFAULT_LOCAL_FIRST_SAVINGS_CORPUS)
    corpus = {
        **corpus,
        "preregistered_supervision_thresholds": {
            **cast(dict[str, object], corpus["preregistered_supervision_thresholds"]),
            "minimum_pairs": 1,
        },
    }
    common: dict[str, object] = {
        "variant_id": "telehealth-01",
        "available": True,
        "end_to_end_success": True,
        "critical_false_memory_count": 0,
        "exact_value_integrity_rate": 1.0,
        "repeated_error_count": 0,
        "frontier_escalation_count": 0,
        "verified_lesson_count": 0,
        "verified_lesson_retrieval_count": 0,
        "frontier_advisor_cached_input_tokens": 0,
        "frontier_advisor_reasoning_output_tokens": 0,
        "actual_latency_ns": 100,
    }
    rows = [
        {
            **common,
            "condition": "SD",
            "hidden_test_accuracy": 0.8,
            "executor_model_call_count": 3,
            "frontier_advisor_call_count": 0,
            "executor_model_input_tokens": 100,
            "executor_model_output_tokens": 20,
            "frontier_advisor_input_tokens": 0,
            "frontier_advisor_output_tokens": 0,
        },
        {
            **common,
            "condition": "SS",
            "hidden_test_accuracy": 1.0,
            "executor_model_call_count": 3,
            "frontier_advisor_call_count": 0,
            "executor_model_input_tokens": 1_000_000,
            "executor_model_output_tokens": 100_000,
            "frontier_advisor_input_tokens": 0,
            "frontier_advisor_output_tokens": 0,
        },
        {
            **common,
            "condition": "TD",
            "hidden_test_accuracy": 1.0,
            "executor_model_call_count": 0,
            "frontier_advisor_call_count": 3,
            "executor_model_input_tokens": 0,
            "executor_model_output_tokens": 0,
            "frontier_advisor_input_tokens": 1_000,
            "frontier_advisor_output_tokens": 100,
        },
    ]

    analysis = analyze_supervision(rows, corpus, expected_variant_count=1)

    assert analysis["frontier_token_savings_vs_direct"] == 1.0
    assert cast(float, analysis["total_token_savings_vs_direct"]) < 0
    assert analysis["frontier_billing_mode"] == "chatgpt_subscription"


def test_incomplete_paired_population_cannot_claim_token_savings() -> None:
    corpus = _load_corpus(evaluation.DEFAULT_LOCAL_FIRST_SAVINGS_CORPUS)
    common: dict[str, object] = {
        "available": True,
        "end_to_end_success": True,
        "critical_false_memory_count": 0,
        "exact_value_integrity_rate": 1.0,
        "repeated_error_count": 0,
        "frontier_escalation_count": 0,
        "verified_lesson_count": 0,
        "verified_lesson_retrieval_count": 0,
        "frontier_advisor_cached_input_tokens": 0,
        "frontier_advisor_reasoning_output_tokens": 0,
        "actual_latency_ns": 100,
    }
    rows = [
        {
            **common,
            "variant_id": "telehealth-01",
            "condition": "SD",
            "hidden_test_accuracy": 0.9,
            "executor_model_call_count": 3,
            "frontier_advisor_call_count": 0,
            "executor_model_input_tokens": 100,
            "executor_model_output_tokens": 20,
            "frontier_advisor_input_tokens": 0,
            "frontier_advisor_output_tokens": 0,
        },
        {
            **common,
            "variant_id": "telehealth-01",
            "condition": "SS",
            "hidden_test_accuracy": 1.0,
            "executor_model_call_count": 3,
            "frontier_advisor_call_count": 0,
            "executor_model_input_tokens": 100,
            "executor_model_output_tokens": 20,
            "frontier_advisor_input_tokens": 0,
            "frontier_advisor_output_tokens": 0,
        },
        {
            **common,
            "variant_id": "telehealth-01",
            "condition": "TD",
            "hidden_test_accuracy": 1.0,
            "executor_model_call_count": 0,
            "frontier_advisor_call_count": 3,
            "executor_model_input_tokens": 0,
            "executor_model_output_tokens": 0,
            "frontier_advisor_input_tokens": 1_000,
            "frontier_advisor_output_tokens": 100,
        },
        {
            **common,
            "variant_id": "telehealth-02",
            "condition": "TD",
            "hidden_test_accuracy": 1.0,
            "executor_model_call_count": 0,
            "frontier_advisor_call_count": 3,
            "executor_model_input_tokens": 0,
            "executor_model_output_tokens": 0,
            "frontier_advisor_input_tokens": 1_000,
            "frontier_advisor_output_tokens": 100,
        },
    ]

    analysis = analyze_supervision(rows, corpus, expected_variant_count=2)

    assert analysis["paired_count"] == 1
    assert analysis["verdict"] == "NOT_EVALUATED"
    assert analysis["frontier_token_savings_vs_direct"] is None
    assert analysis["total_token_savings_vs_direct"] is None
    assert analysis["token_gate_pass"] is False
    router_goal = cast(dict[str, object], analysis["router_goal"])
    assert router_goal["verdict"] == "NOT_EVALUATED"
    assert router_goal["paired_count"] == 1
    assert router_goal["required_pair_count"] == 30


def test_unauthorized_frontier_stops_before_local_model_or_artifact_creation(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    def forbidden_model_identity(_base_url: str, _name: str) -> dict[str, object]:
        raise AssertionError("unconfigured frontier must fail before contacting Ollama")

    monkeypatch.setattr(evaluation, "_model_identity", forbidden_model_identity)
    with raises(evaluation.LongHorizonError, match="live calls are not authorized"):
        evaluation.run(
            run_id="blocked-dry-run",
            run_role="engineering_dry_run",
            variant_count=1,
            corpus_path=DEFAULT_SUPERVISION_CORPUS,
            results_root=tmp_path,
        )

    assert not (tmp_path / "blocked-dry-run").exists()


def test_supervised_run_cannot_resume_with_reset_call_and_cost_counters(tmp_path: Path) -> None:
    with raises(evaluation.LongHorizonError, match="not resumable"):
        evaluation.run(
            run_id="interrupted-dry-run",
            run_role="engineering_dry_run",
            variant_count=1,
            corpus_path=DEFAULT_SUPERVISION_CORPUS,
            results_root=tmp_path,
            resume=True,
        )

    assert not (tmp_path / "interrupted-dry-run").exists()


def test_openai_adapter_uses_user_selected_model_and_strict_stateless_schema() -> None:
    corpus = _load_corpus(DEFAULT_SUPERVISION_CORPUS)
    corpus = {
        **corpus,
        "frontier_advisor": {
            **cast(dict[str, object], corpus["frontier_advisor"]),
            "identifier": "gpt-user-selected",
            "live_calls_authorized": True,
        },
    }
    requests: list[tuple[str, str, dict[str, object], int]] = []

    def fake_transport(
        endpoint: str,
        api_key: str,
        payload: dict[str, object],
        timeout_seconds: int,
    ) -> dict[str, object]:
        requests.append((endpoint, api_key, payload, timeout_seconds))
        return {
            "status": "completed",
            "model": "gpt-user-selected",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "status": "pass",
                                    "failed_fields": [],
                                    "repair_steps": [],
                                    "uncertainty": "",
                                }
                            ),
                            "annotations": [],
                        }
                    ],
                }
            ],
            "usage": {"input_tokens": 123, "output_tokens": 17},
        }

    frontier_call = _build_openai_frontier_call(
        corpus,
        environment={"OPENAI_API_KEY": "sk-test-only"},
        transport=fake_transport,
    )
    result = frontier_call("review", "review this parsed candidate")

    endpoint, api_key, payload, timeout = requests[0]
    assert endpoint == "https://api.openai.com/v1/responses"
    assert api_key == "sk-test-only"
    assert timeout == 60
    assert payload["model"] == "gpt-user-selected"
    assert payload["input"] == "review this parsed candidate"
    assert payload["store"] is False
    assert payload["tools"] == []
    assert payload["reasoning"] == {"effort": "medium", "context": "current_turn"}
    text = cast(dict[str, object], payload["text"])
    output_format = cast(dict[str, object], text["format"])
    assert text["verbosity"] == "low"
    assert output_format["type"] == "json_schema"
    assert output_format["strict"] is True
    assert "sk-test-only" not in json.dumps(payload)
    assert result.provider_id == "openai"
    assert result.model_id == "gpt-user-selected"
    assert result.input_tokens == 123
    assert result.output_tokens == 17


def test_openai_adapter_requires_authorization_and_environment_only_key() -> None:
    corpus = _load_corpus(DEFAULT_SUPERVISION_CORPUS)

    with raises(evaluation.LongHorizonError, match="live calls are not authorized"):
        _build_openai_frontier_call(corpus, environment={})

    authorized = {
        **corpus,
        "frontier_advisor": {
            **cast(dict[str, object], corpus["frontier_advisor"]),
            "live_calls_authorized": True,
        },
    }
    with raises(evaluation.LongHorizonError, match="API key is unavailable"):
        _build_openai_frontier_call(authorized, environment={})


def test_openai_adapter_rejects_untrusted_response_identity_and_refusal() -> None:
    corpus = _load_corpus(DEFAULT_SUPERVISION_CORPUS)
    authorized = {
        **corpus,
        "frontier_advisor": {
            **cast(dict[str, object], corpus["frontier_advisor"]),
            "live_calls_authorized": True,
        },
    }

    def response_with(*, model: str, part: dict[str, object]) -> dict[str, object]:
        return {
            "status": "completed",
            "model": model,
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [part],
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }

    wrong_model = _build_openai_frontier_call(
        authorized,
        environment={"OPENAI_API_KEY": "sk-test-only"},
        transport=lambda *_: response_with(
            model="gpt-unexpected",
            part={"type": "output_text", "text": "{}"},
        ),
    )
    with raises(evaluation.LongHorizonError, match="identity or status"):
        wrong_model("review", "review")

    refusal = _build_openai_frontier_call(
        authorized,
        environment={"OPENAI_API_KEY": "sk-test-only"},
        transport=lambda *_: response_with(
            model="gpt-5.6-sol",
            part={"type": "refusal", "refusal": "cannot comply"},
        ),
    )
    with raises(evaluation.LongHorizonError, match="refused"):
        refusal("review", "review")


def test_openai_adapter_enforces_call_and_cost_limits_before_transport() -> None:
    corpus = _load_corpus(DEFAULT_SUPERVISION_CORPUS)
    advisor = cast(dict[str, object], corpus["frontier_advisor"])
    calls = 0

    def fake_transport(
        _endpoint: str,
        _api_key: str,
        _payload: dict[str, object],
        _timeout_seconds: int,
    ) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {
            "status": "completed",
            "model": "gpt-5.6-sol",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "{}", "annotations": []}],
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }

    one_call = {
        **corpus,
        "frontier_advisor": {
            **advisor,
            "live_calls_authorized": True,
            "maximum_calls": 1,
        },
    }
    frontier_call = _build_openai_frontier_call(
        one_call,
        environment={"OPENAI_API_KEY": "sk-test-only"},
        transport=fake_transport,
    )
    frontier_call("review", "first")
    with raises(evaluation.LongHorizonError, match="call limit"):
        frontier_call("review", "second")

    no_budget = {
        **corpus,
        "frontier_advisor": {
            **advisor,
            "live_calls_authorized": True,
            "maximum_cost_usd": 0.000001,
        },
    }
    blocked = _build_openai_frontier_call(
        no_budget,
        environment={"OPENAI_API_KEY": "sk-test-only"},
        transport=fake_transport,
    )
    with raises(evaluation.LongHorizonError, match="cost limit"):
        blocked("review", "blocked before transport")

    assert calls == 1


def test_incomplete_supervision_report_is_explicitly_not_evaluated() -> None:
    report = evaluation._supervision_report(
        {
            "run_id": "incomplete",
            "run_role": "engineering_dry_run",
            "paired_count": 0,
            "verdict": "NOT_EVALUATED",
            "reason": "no complete SD/SS/TD variant triplet",
            "conservative_frontier_cost_usd_at_configured_rates": 0.001,
        }
    )

    assert "**NOT_EVALUATED**" in report
    assert "no quality or token comparison was made" in report
    assert "Conservative frontier cost at configured rates: `$0.001000`" in report
