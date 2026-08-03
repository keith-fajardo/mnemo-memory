from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from mnemo_memory.packages.application.command_wrapper import (
    COMMAND_HOOK_ENTRY_POINT_GROUP,
    COMMAND_NOT_EXECUTABLE_EXIT_CODE,
    COMMAND_NOT_FOUND_EXIT_CODE,
    STRICT_HOOK_FAILURE_EXIT_CODE,
    AfterHook,
    CommandContext,
    CommandFailure,
    CommandFailureCode,
    CommandInvocation,
    CommandResult,
    CommandWrapper,
    HookOutcome,
    HookRegistration,
    HookStatus,
    HookWarning,
    discover_command_hooks,
)

NOW = datetime(2026, 8, 3, tzinfo=UTC)


class StaticResolver:
    def __init__(self, executable: Path | CommandFailure) -> None:
        self.executable = executable
        self.calls: list[tuple[str | Path, Path | None]] = []

    def resolve(self, executable: str | Path, wrapper_executable: Path | None = None) -> Path:
        self.calls.append((executable, wrapper_executable))
        if isinstance(self.executable, CommandFailure):
            raise self.executable
        return self.executable


class FakeExecutor:
    def __init__(self, result: CommandResult | CommandFailure) -> None:
        self.result = result
        self.contexts: list[CommandContext] = []

    def execute(self, context: CommandContext) -> CommandResult:
        self.contexts.append(context)
        if isinstance(self.result, CommandFailure):
            raise self.result
        return self.result


def executable(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic executable"
    path.write_text("synthetic")
    path.chmod(0o755)
    return path


def invocation(tmp_path: Path, *, integration: str = "dbt") -> CommandInvocation:
    return CommandInvocation("synthetic", ("one", "two words"), tmp_path, integration)


def result(*, exit_code: int = 0, started: bool = True) -> CommandResult:
    return CommandResult(started, exit_code, False, NOW, NOW)


def wrapper(
    tmp_path: Path,
    *,
    hooks: tuple[HookRegistration, ...] = (),
    process_result: CommandResult | CommandFailure | None = None,
) -> tuple[CommandWrapper, FakeExecutor]:
    executor = FakeExecutor(process_result or result())
    return (
        CommandWrapper(
            StaticResolver(executable(tmp_path)), executor, lambda: NOW, lambda: "call-1", hooks
        ),
        executor,
    )


def test_immutable_values_validate_public_contract(tmp_path: Path) -> None:
    valid = invocation(tmp_path)
    assert valid.arguments == ("one", "two words")
    with pytest.raises(ValueError, match="INVALID_EXECUTABLE"):
        CommandInvocation("", (), tmp_path, "dbt")
    with pytest.raises(ValueError, match="INVALID_ARGUMENTS"):
        CommandInvocation("tool", ("valid", 1), tmp_path, "dbt")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="INVALID_WORKING_DIRECTORY"):
        CommandInvocation("tool", (), Path("relative"), "dbt")
    with pytest.raises(ValueError, match="INVALID_INTEGRATION"):
        CommandInvocation("tool", (), tmp_path, "DBT")
    with pytest.raises(ValueError, match="OUTCOME_INVALID"):
        HookOutcome(HookStatus.ACTIVATED, metadata=(("unsafe key", "value"),))
    with pytest.raises(ValueError, match="REGISTRATION_INVALID"):
        HookRegistration(
            "bad name", "dbt", lambda _: None, lambda *_: HookOutcome(HookStatus.ACTIVATED)
        )


def test_hooks_run_in_order_and_unwind_in_reverse_with_matched_state(tmp_path: Path) -> None:
    events: list[tuple[str, object]] = []

    def hook(name: str) -> HookRegistration:
        def before(_: CommandContext) -> object:
            state = {"hook": name}
            events.append((f"before:{name}", state))
            return state

        def after(_: CommandContext, state: object, command: CommandResult) -> HookOutcome:
            assert command.exit_code == 0
            events.append((f"after:{name}", state))
            return HookOutcome(HookStatus.ACTIVATED, metadata=(("hook", name),))

        return HookRegistration(name, "dbt", before, after)

    command_wrapper, executor = wrapper(tmp_path, hooks=(hook("first"), hook("second")))
    outcome = command_wrapper.run(invocation(tmp_path))

    assert [event[0] for event in events] == [
        "before:first",
        "before:second",
        "after:second",
        "after:first",
    ]
    assert events[0][1] is events[3][1]
    assert events[1][1] is events[2][1]
    assert [item.registration for item in outcome.outcomes] == ["second", "first"]
    assert outcome.outcomes[0].outcome.metadata == (("hook", "second"),)
    assert len(executor.contexts) == 1


def test_fail_open_before_failure_skips_its_after_only(tmp_path: Path) -> None:
    calls: list[str] = []

    def failed_before(_: CommandContext) -> object:
        calls.append("failed-before")
        raise RuntimeError("private exception text")

    bad = HookRegistration("bad", "dbt", failed_before, lambda *_: pytest.fail("must not run"))

    def good_before(_: CommandContext) -> object:
        calls.append("good-before")
        return None

    def good_after(_: CommandContext, __: object, ___: CommandResult) -> HookOutcome:
        calls.append("good-after")
        return HookOutcome(HookStatus.UNCHANGED)

    good = HookRegistration("good", "dbt", good_before, good_after)
    command_wrapper, executor = wrapper(tmp_path, hooks=(bad, good))
    wrapped = command_wrapper.run(invocation(tmp_path))

    assert calls == ["failed-before", "good-before", "good-after"]
    assert len(executor.contexts) == 1
    assert [item.registration for item in wrapped.outcomes] == ["good"]
    assert wrapped.warnings[0].code == "MNEMO_HOOK_PRE_FAILED"
    assert "private" not in str(wrapped.warnings)


def test_strict_before_failure_does_not_start_child_and_unwinds_entered_hooks(
    tmp_path: Path,
) -> None:
    received: list[CommandResult] = []

    def entered_after(_: CommandContext, __: object, command: CommandResult) -> HookOutcome:
        received.append(command)
        return HookOutcome(HookStatus.SKIPPED)

    entered = HookRegistration("entered", "dbt", lambda _: "state", entered_after)
    failing = HookRegistration(
        "failing",
        "dbt",
        lambda _: (_ for _ in ()).throw(RuntimeError("secret")),
        lambda *_: pytest.fail("must not run"),
    )
    command_wrapper, executor = wrapper(tmp_path, hooks=(entered, failing))
    wrapped = command_wrapper.run(invocation(tmp_path), strict_memory=True)

    assert executor.contexts == []
    assert wrapped.result.not_executed is True
    assert wrapped.result.exit_code == STRICT_HOOK_FAILURE_EXIT_CODE
    assert wrapped.result.failure_code is CommandFailureCode.STRICT_HOOK_FAILURE
    assert received == [wrapped.result]
    assert [item.registration for item in wrapped.outcomes] == ["entered"]


def test_after_hooks_run_for_nonzero_child_and_strict_cannot_replace_child_failure(
    tmp_path: Path,
) -> None:
    calls: list[int] = []

    def broken_after(_: CommandContext, __: object, command: CommandResult) -> HookOutcome:
        calls.append(command.exit_code)
        raise RuntimeError()

    hook = HookRegistration("memory", "dbt", lambda _: None, broken_after)
    command_wrapper, _ = wrapper(tmp_path, hooks=(hook,), process_result=result(exit_code=23))
    wrapped = command_wrapper.run(invocation(tmp_path), strict_memory=True)

    assert calls == [23]
    assert wrapped.result.exit_code == 23
    assert wrapped.result.failure_code is None
    assert wrapped.outcomes[0].outcome.status is HookStatus.FAILED


def test_strict_post_failure_changes_only_successful_child_exit_and_preserves_finish_time(
    tmp_path: Path,
) -> None:
    completed = datetime(2026, 8, 3, 12, tzinfo=UTC)
    child = CommandResult(True, 0, False, NOW, completed)
    hook = HookRegistration(
        "memory",
        "dbt",
        lambda _: None,
        lambda *_: (_ for _ in ()).throw(RuntimeError("not exposed")),
    )
    command_wrapper, _ = wrapper(tmp_path, hooks=(hook,), process_result=child)
    wrapped = command_wrapper.run(invocation(tmp_path), strict_memory=True)

    assert wrapped.result.exit_code == STRICT_HOOK_FAILURE_EXIT_CODE
    assert wrapped.result.failure_code is CommandFailureCode.STRICT_HOOK_FAILURE
    assert wrapped.result.finished_at == completed
    assert "not exposed" not in str(wrapped.warnings)


@pytest.mark.parametrize(
    ("failure", "exit_code"),
    [
        (CommandFailureCode.COMMAND_NOT_FOUND, COMMAND_NOT_FOUND_EXIT_CODE),
        (CommandFailureCode.COMMAND_NOT_EXECUTABLE, COMMAND_NOT_EXECUTABLE_EXIT_CODE),
        (CommandFailureCode.COMMAND_RECURSION, 125),
        (CommandFailureCode.COMMAND_LAUNCH_FAILED, 125),
    ],
)
def test_typed_resolution_failures_are_distinct_and_do_not_start_process(
    tmp_path: Path, failure: CommandFailureCode, exit_code: int
) -> None:
    executor = FakeExecutor(result())
    command_wrapper = CommandWrapper(
        StaticResolver(CommandFailure(failure)), executor, lambda: NOW, lambda: "call-1"
    )
    wrapped = command_wrapper.run(invocation(tmp_path))

    assert wrapped.result.started is False
    assert wrapped.result.exit_code == exit_code
    assert wrapped.result.failure_code is failure
    assert wrapped.warnings == (HookWarning("command", "resolve", failure.value),)
    assert executor.contexts == []


def test_launch_failure_unwinds_entered_hooks_and_does_not_expose_exception(tmp_path: Path) -> None:
    unwound: list[CommandResult] = []

    def unwind_after(_: CommandContext, __: object, command: CommandResult) -> HookOutcome:
        unwound.append(command)
        return HookOutcome(HookStatus.FAILED)

    hook = HookRegistration("memory", "dbt", lambda _: "state", unwind_after)
    command_wrapper, _ = wrapper(
        tmp_path,
        hooks=(hook,),
        process_result=CommandFailure(CommandFailureCode.COMMAND_LAUNCH_FAILED),
    )
    wrapped = command_wrapper.run(invocation(tmp_path))

    assert unwound == [wrapped.result]
    assert wrapped.result.failure_code is CommandFailureCode.COMMAND_LAUNCH_FAILED
    assert wrapped.outcomes[0].registration == "memory"
    assert "Traceback" not in str(wrapped)


def test_duplicate_registration_and_malformed_outcome_are_sanitized(tmp_path: Path) -> None:
    valid = HookRegistration(
        "memory", "dbt", lambda _: None, lambda *_: HookOutcome(HookStatus.ACTIVATED)
    )
    with pytest.raises(ValueError, match="DUPLICATE"):
        CommandWrapper(
            StaticResolver(executable(tmp_path)),
            FakeExecutor(result()),
            lambda: NOW,
            lambda: "id",
            (valid, valid),
        )
    malformed_after = cast(AfterHook, lambda *_: object())
    malformed = HookRegistration("malformed", "dbt", lambda _: None, malformed_after)
    command_wrapper, _ = wrapper(tmp_path, hooks=(malformed,))
    wrapped = command_wrapper.run(invocation(tmp_path))
    assert wrapped.outcomes[0].outcome.code == "MNEMO_HOOK_POST_FAILED"
    assert all("object at" not in warning.code for warning in wrapped.warnings)


def test_nonmatching_integration_does_not_receive_state(tmp_path: Path) -> None:
    hook = HookRegistration(
        "other",
        "other",
        lambda _: pytest.fail("must not run"),
        lambda *_: pytest.fail("must not run"),
    )
    command_wrapper, executor = wrapper(tmp_path, hooks=(hook,))
    wrapped = command_wrapper.run(invocation(tmp_path))
    assert wrapped.outcomes == ()
    assert len(executor.contexts) == 1


def test_wrapper_always_resolves_before_process_execution(tmp_path: Path) -> None:
    resolver = StaticResolver(executable(tmp_path))
    executor = FakeExecutor(result())
    command_wrapper = CommandWrapper(resolver, executor, lambda: NOW, lambda: "call-1")

    command_wrapper.run(invocation(tmp_path), wrapper_executable=tmp_path / "mnemo-memory")

    assert resolver.calls == [("synthetic", tmp_path / "mnemo-memory")]
    assert len(executor.contexts) == 1


def test_generic_application_kernel_has_no_connector_or_dbt_integration_imports() -> None:
    source = Path("src/mnemo_memory/packages/application/command_wrapper.py").read_text()

    assert "mnemo_memory.connectors" not in source
    assert "connectors.dbt" not in source


class FakeEntryPoint:
    def __init__(self, name: str, value: str, loaded: object | BaseException) -> None:
        self.name = name
        self.value = value
        self.group = COMMAND_HOOK_ENTRY_POINT_GROUP
        self._loaded = loaded

    def load(self) -> object:
        if isinstance(self._loaded, BaseException):
            raise self._loaded
        return self._loaded


def test_installed_entry_point_discovery_is_deterministic_filtered_and_sanitized() -> None:
    def registration(name: str, integration: str = "dbt") -> HookRegistration:
        return HookRegistration(
            name,
            integration,
            lambda _: None,
            lambda *_: HookOutcome(HookStatus.UNCHANGED),
        )

    result = discover_command_hooks(
        "dbt",
        installed_entry_points=(
            cast(object, FakeEntryPoint("zeta", "package:zeta", registration("zeta"))),
            cast(object, FakeEntryPoint("broken", "package:broken", RuntimeError("secret"))),
            cast(object, FakeEntryPoint("invalid", "package:invalid", object())),
            cast(object, FakeEntryPoint("other", "package:other", registration("other", "other"))),
            cast(object, FakeEntryPoint("duplicate", "package:duplicate", registration("zeta"))),
            cast(object, FakeEntryPoint("alpha", "package:alpha", registration("alpha"))),
        ),  # type: ignore[arg-type]
    )

    assert [item.name for item in result.registrations] == ["alpha", "zeta"]
    assert [item.code for item in result.warnings] == [
        "MNEMO_HOOK_DISCOVERY_LOAD_FAILED",
        "MNEMO_HOOK_DISCOVERY_INVALID",
        "MNEMO_HOOK_DISCOVERY_DUPLICATE",
    ]
    assert "secret" not in str(result.warnings)
