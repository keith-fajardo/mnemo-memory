"""Storage-independent lifecycle contract for safely wrapping a command.

The application layer owns validation, hook ordering, and sanitized results.  Process
creation belongs to a connector that implements :class:`ProcessExecutor`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Protocol

STRICT_HOOK_FAILURE_EXIT_CODE = 70
"""Exit status used only when strict-memory hooks fail after/before a command."""

COMMAND_NOT_FOUND_EXIT_CODE = 127
COMMAND_NOT_EXECUTABLE_EXIT_CODE = 126
COMMAND_WRAPPER_FAILURE_EXIT_CODE = 125

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_CODE = re.compile(r"MNEMO_[A-Z0-9_]{1,64}")
_MAX_METADATA_ITEMS = 8
_MAX_METADATA_KEY_LENGTH = 64
_MAX_METADATA_VALUE_LENGTH = 256
_MAX_WARNING_FIELD_LENGTH = 64
COMMAND_HOOK_ENTRY_POINT_GROUP = "mnemo.command_hooks"


class CommandFailureCode(str, Enum):
    """Machine-readable, sanitized command wrapper failures."""

    COMMAND_NOT_FOUND = "MNEMO_COMMAND_NOT_FOUND"
    COMMAND_NOT_EXECUTABLE = "MNEMO_COMMAND_NOT_EXECUTABLE"
    COMMAND_RECURSION = "MNEMO_COMMAND_RECURSION"
    INVALID_WORKING_DIRECTORY = "MNEMO_COMMAND_INVALID_WORKING_DIRECTORY"
    COMMAND_LAUNCH_FAILED = "MNEMO_COMMAND_LAUNCH_FAILED"
    STRICT_HOOK_FAILURE = "MNEMO_COMMAND_STRICT_HOOK_FAILURE"

    @property
    def exit_code(self) -> int:
        if self is CommandFailureCode.COMMAND_NOT_FOUND:
            return COMMAND_NOT_FOUND_EXIT_CODE
        if self is CommandFailureCode.COMMAND_NOT_EXECUTABLE:
            return COMMAND_NOT_EXECUTABLE_EXIT_CODE
        if self is CommandFailureCode.STRICT_HOOK_FAILURE:
            return STRICT_HOOK_FAILURE_EXIT_CODE
        return COMMAND_WRAPPER_FAILURE_EXIT_CODE


class CommandFailure(Exception):
    """Internal typed failure deliberately safe to expose as an error code only."""

    def __init__(self, code: CommandFailureCode) -> None:
        super().__init__(code.value)
        self.code = code


class HookStatus(str, Enum):
    ACTIVATED = "activated"
    UNCHANGED = "unchanged"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


def _validate_identifier(value: str, *, code: str) -> None:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(code)


def _validate_code(value: str, *, code: str) -> None:
    if not _CODE.fullmatch(value):
        raise ValueError(code)


@dataclass(frozen=True, slots=True)
class CommandInvocation:
    """Unresolved external command supplied by a caller."""

    executable: str | Path
    arguments: tuple[str, ...]
    working_directory: Path
    integration: str

    def __post_init__(self) -> None:
        if not isinstance(self.executable, (str, Path)) or not str(self.executable).strip():
            raise ValueError("MNEMO_COMMAND_INVALID_EXECUTABLE")
        if not isinstance(self.arguments, tuple) or not all(
            isinstance(argument, str) for argument in self.arguments
        ):
            raise ValueError("MNEMO_COMMAND_INVALID_ARGUMENTS")
        if not isinstance(self.working_directory, Path) or not self.working_directory.is_absolute():
            raise ValueError(CommandFailureCode.INVALID_WORKING_DIRECTORY.value)
        if not self.working_directory.is_dir():
            raise ValueError(CommandFailureCode.INVALID_WORKING_DIRECTORY.value)
        _validate_identifier(self.integration, code="MNEMO_COMMAND_INVALID_INTEGRATION")


@dataclass(frozen=True, slots=True)
class CommandContext:
    """Validated command details intentionally excluding process environment values."""

    executable: Path
    arguments: tuple[str, ...]
    working_directory: Path
    integration: str
    invocation_id: str
    started_at: datetime

    def __post_init__(self) -> None:
        if not self.executable.is_absolute() or not self.executable.is_file():
            raise ValueError("MNEMO_COMMAND_CONTEXT_INVALID_EXECUTABLE")
        if not self.working_directory.is_absolute() or not self.working_directory.is_dir():
            raise ValueError(CommandFailureCode.INVALID_WORKING_DIRECTORY.value)
        if not all(isinstance(argument, str) for argument in self.arguments):
            raise ValueError("MNEMO_COMMAND_INVALID_ARGUMENTS")
        _validate_identifier(self.integration, code="MNEMO_COMMAND_INVALID_INTEGRATION")
        if (
            not isinstance(self.invocation_id, str)
            or not self.invocation_id.strip()
            or len(self.invocation_id) > 128
        ):
            raise ValueError("MNEMO_COMMAND_INVALID_INVOCATION_ID")


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Process result; ``started`` distinguishes resolution/launch from child failures."""

    started: bool
    exit_code: int
    interrupted: bool
    started_at: datetime
    finished_at: datetime
    failure_code: CommandFailureCode | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.started, bool) or not isinstance(self.interrupted, bool):
            raise ValueError("MNEMO_COMMAND_INVALID_RESULT")
        if not isinstance(self.exit_code, int) or not 0 <= self.exit_code <= 255:
            raise ValueError("MNEMO_COMMAND_INVALID_RESULT")
        if self.finished_at < self.started_at:
            raise ValueError("MNEMO_COMMAND_INVALID_RESULT")

    @property
    def not_executed(self) -> bool:
        """Whether no child process was started (including strict pre-hook rejection)."""

        return not self.started


@dataclass(frozen=True, slots=True)
class HookWarning:
    hook_name: str
    phase: str
    code: str

    def __post_init__(self) -> None:
        _validate_identifier(self.hook_name, code="MNEMO_HOOK_WARNING_INVALID")
        if self.phase not in {"before", "after", "resolve", "execute"}:
            raise ValueError("MNEMO_HOOK_WARNING_INVALID")
        _validate_code(self.code, code="MNEMO_HOOK_WARNING_INVALID")
        if any(
            len(value) > _MAX_WARNING_FIELD_LENGTH
            for value in (self.hook_name, self.phase, self.code)
        ):
            raise ValueError("MNEMO_HOOK_WARNING_INVALID")


@dataclass(frozen=True, slots=True)
class HookOutcome:
    """Small structured hook result; arbitrary payloads are deliberately unsupported."""

    status: HookStatus
    code: str = "MNEMO_HOOK_OK"
    metadata: tuple[tuple[str, str], ...] = ()
    warnings: tuple[HookWarning, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, HookStatus):
            raise ValueError("MNEMO_HOOK_OUTCOME_INVALID")
        _validate_code(self.code, code="MNEMO_HOOK_OUTCOME_INVALID")
        if not isinstance(self.metadata, tuple) or len(self.metadata) > _MAX_METADATA_ITEMS:
            raise ValueError("MNEMO_HOOK_OUTCOME_INVALID")
        keys: list[str] = []
        for item in self.metadata:
            if not isinstance(item, tuple) or len(item) != 2:
                raise ValueError("MNEMO_HOOK_OUTCOME_INVALID")
            key, value = item
            if (
                not isinstance(key, str)
                or not isinstance(value, str)
                or not _IDENTIFIER.fullmatch(key)
                or len(key) > _MAX_METADATA_KEY_LENGTH
                or len(value) > _MAX_METADATA_VALUE_LENGTH
            ):
                raise ValueError("MNEMO_HOOK_OUTCOME_INVALID")
            keys.append(key)
        if len(set(keys)) != len(keys) or not isinstance(self.warnings, tuple):
            raise ValueError("MNEMO_HOOK_OUTCOME_INVALID")
        if not all(isinstance(warning, HookWarning) for warning in self.warnings):
            raise ValueError("MNEMO_HOOK_OUTCOME_INVALID")


BeforeHook = Callable[[CommandContext], object]
AfterHook = Callable[[CommandContext, object, CommandResult], HookOutcome]


@dataclass(frozen=True, slots=True)
class HookRegistration:
    name: str
    integration: str
    before: BeforeHook
    after: AfterHook

    def __post_init__(self) -> None:
        _validate_identifier(self.name, code="MNEMO_HOOK_REGISTRATION_INVALID")
        _validate_identifier(self.integration, code="MNEMO_HOOK_REGISTRATION_INVALID")
        if not callable(self.before) or not callable(self.after):
            raise ValueError("MNEMO_HOOK_REGISTRATION_INVALID")


@dataclass(frozen=True, slots=True)
class RegisteredHookOutcome:
    registration: str
    outcome: HookOutcome


@dataclass(frozen=True, slots=True)
class HookTiming:
    """Bounded timing evidence for one successfully entered hook.

    The wrapper exposes phase boundaries rather than process output or arbitrary hook payloads.
    Callers can therefore measure wrapper overhead without inspecting private hook state.
    """

    registration: str
    before_started_at: datetime
    before_finished_at: datetime
    after_started_at: datetime | None = None
    after_finished_at: datetime | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.registration, code="MNEMO_HOOK_TIMING_INVALID")
        if self.before_finished_at < self.before_started_at:
            raise ValueError("MNEMO_HOOK_TIMING_INVALID")
        if (self.after_started_at is None) != (self.after_finished_at is None):
            raise ValueError("MNEMO_HOOK_TIMING_INVALID")
        if (
            self.after_started_at is not None
            and self.after_finished_at is not None
            and self.after_finished_at < self.after_started_at
        ):
            raise ValueError("MNEMO_HOOK_TIMING_INVALID")


@dataclass(frozen=True, slots=True)
class HookDiscoveryResult:
    """Validated registrations discovered only from installed distributions."""

    registrations: tuple[HookRegistration, ...]
    warnings: tuple[HookWarning, ...]


def discover_command_hooks(
    integration: str,
    *,
    installed_entry_points: Iterable[EntryPoint] | None = None,
) -> HookDiscoveryResult:
    """Load deterministic, applicable registrations from installed entry points.

    An entry point in ``mnemo.command_hooks`` must load to one
    :class:`HookRegistration`. Working-directory files and project-provided
    Python are never scanned or imported. A malformed or duplicate external
    registration is skipped with a sanitized discovery warning.
    """
    _validate_identifier(integration, code="MNEMO_HOOK_DISCOVERY_INVALID_INTEGRATION")
    candidates = (
        tuple(installed_entry_points)
        if installed_entry_points is not None
        else tuple(entry_points(group=COMMAND_HOOK_ENTRY_POINT_GROUP))
    )
    registrations: list[HookRegistration] = []
    warnings: list[HookWarning] = []
    names: set[str] = set()
    for candidate in sorted(
        (item for item in candidates if item.group == COMMAND_HOOK_ENTRY_POINT_GROUP),
        key=lambda item: (item.name, item.value),
    ):
        warning_name = candidate.name if _IDENTIFIER.fullmatch(candidate.name) else "discovery"
        try:
            loaded = candidate.load()
        except Exception:
            warnings.append(
                HookWarning(warning_name, "resolve", "MNEMO_HOOK_DISCOVERY_LOAD_FAILED")
            )
            continue
        if not isinstance(loaded, HookRegistration):
            warnings.append(HookWarning(warning_name, "resolve", "MNEMO_HOOK_DISCOVERY_INVALID"))
            continue
        if loaded.integration != integration:
            continue
        if loaded.name in names:
            warnings.append(HookWarning(loaded.name, "resolve", "MNEMO_HOOK_DISCOVERY_DUPLICATE"))
            continue
        names.add(loaded.name)
        registrations.append(loaded)
    return HookDiscoveryResult(tuple(registrations), tuple(warnings))


def merge_command_hooks(
    built_in: Iterable[HookRegistration], discovered: HookDiscoveryResult
) -> HookDiscoveryResult:
    """Combine trusted built-ins with discovered hooks without letting a plugin replace one.

    Built-ins keep their declared order. Discovery already supplies deterministic order for
    external distributions, so accepted extensions follow them in that same order. A plugin
    cannot shadow a built-in (or another accepted plugin): it is skipped with the same bounded
    duplicate warning used by discovery instead of making command execution ambiguous.
    """
    registrations = list(built_in)
    names: set[str] = set()
    for registration in registrations:
        if registration.name in names:
            raise ValueError("MNEMO_HOOK_DUPLICATE_NAME")
        names.add(registration.name)
    warnings = list(discovered.warnings)
    for registration in discovered.registrations:
        if registration.name in names:
            warnings.append(
                HookWarning(registration.name, "resolve", "MNEMO_HOOK_DISCOVERY_DUPLICATE")
            )
            continue
        names.add(registration.name)
        registrations.append(registration)
    return HookDiscoveryResult(tuple(registrations), tuple(warnings))


@dataclass(frozen=True, slots=True)
class CommandWrapperResult:
    result: CommandResult
    outcomes: tuple[RegisteredHookOutcome, ...]
    warnings: tuple[HookWarning, ...]
    hook_timings: tuple[HookTiming, ...] = ()


class ExecutableResolver(Protocol):
    def resolve(self, executable: str | Path, wrapper_executable: Path | None = None) -> Path: ...


class ProcessExecutor(Protocol):
    def execute(self, context: CommandContext) -> CommandResult: ...


Clock = Callable[[], datetime]
InvocationIdFactory = Callable[[], str]


class CommandWrapper:
    """Coordinates validated execution and function-level hook unwinding."""

    def __init__(
        self,
        resolver: ExecutableResolver,
        executor: ProcessExecutor,
        clock: Clock,
        invocation_ids: InvocationIdFactory,
        hooks: Iterable[HookRegistration] = (),
    ) -> None:
        registrations = tuple(hooks)
        names = tuple(registration.name for registration in registrations)
        if len(set(names)) != len(names):
            raise ValueError("MNEMO_HOOK_DUPLICATE_NAME")
        self._resolver = resolver
        self._executor = executor
        self._clock = clock
        self._invocation_ids = invocation_ids
        self._hooks = registrations

    def run(
        self,
        invocation: CommandInvocation,
        *,
        strict_memory: bool = False,
        wrapper_executable: Path | None = None,
    ) -> CommandWrapperResult:
        started_at = self._clock()
        try:
            executable = self._resolver.resolve(invocation.executable, wrapper_executable)
        except CommandFailure as failure:
            return self._failure_result(started_at, failure.code, phase="resolve")
        except Exception:
            return self._failure_result(
                started_at, CommandFailureCode.COMMAND_LAUNCH_FAILED, phase="resolve"
            )

        context = CommandContext(
            executable=executable,
            arguments=invocation.arguments,
            working_directory=invocation.working_directory,
            integration=invocation.integration,
            invocation_id=self._new_invocation_id(),
            started_at=started_at,
        )
        entered: list[tuple[HookRegistration, object, datetime, datetime]] = []
        warnings: list[HookWarning] = []
        for registration in self._hooks:
            if registration.integration != context.integration:
                continue
            before_started_at = self._clock()
            try:
                state = registration.before(context)
            except Exception:
                warnings.append(HookWarning(registration.name, "before", "MNEMO_HOOK_PRE_FAILED"))
                if strict_memory:
                    result = self._result(started_at, CommandFailureCode.STRICT_HOOK_FAILURE)
                    return self._unwind(context, entered, result, warnings, strict_memory=False)
                continue
            entered.append((registration, state, before_started_at, self._clock()))

        try:
            result = self._executor.execute(context)
        except CommandFailure as failure:
            result = self._result(started_at, failure.code)
            warnings.append(HookWarning("command", "execute", failure.code.value))
        except Exception:
            result = self._result(started_at, CommandFailureCode.COMMAND_LAUNCH_FAILED)
            warnings.append(
                HookWarning("command", "execute", CommandFailureCode.COMMAND_LAUNCH_FAILED.value)
            )
        return self._unwind(context, entered, result, warnings, strict_memory=strict_memory)

    def _new_invocation_id(self) -> str:
        invocation_id = self._invocation_ids()
        if (
            not isinstance(invocation_id, str)
            or not invocation_id.strip()
            or len(invocation_id) > 128
        ):
            raise ValueError("MNEMO_COMMAND_INVALID_INVOCATION_ID")
        return invocation_id

    def _result(self, started_at: datetime, failure: CommandFailureCode) -> CommandResult:
        return CommandResult(
            started=False,
            exit_code=failure.exit_code,
            interrupted=False,
            started_at=started_at,
            finished_at=self._clock(),
            failure_code=failure,
        )

    def _failure_result(
        self, started_at: datetime, failure: CommandFailureCode, *, phase: str
    ) -> CommandWrapperResult:
        result = self._result(started_at, failure)
        return CommandWrapperResult(
            result=result,
            outcomes=(),
            warnings=(HookWarning("command", phase, failure.value),),
        )

    def _unwind(
        self,
        context: CommandContext,
        entered: list[tuple[HookRegistration, object, datetime, datetime]],
        result: CommandResult,
        warnings: list[HookWarning],
        *,
        strict_memory: bool,
    ) -> CommandWrapperResult:
        outcomes: list[RegisteredHookOutcome] = []
        timings: list[HookTiming] = []
        final_result = result
        for registration, state, before_started_at, before_finished_at in reversed(entered):
            after_started_at = self._clock()
            try:
                outcome = registration.after(context, state, final_result)
                if not isinstance(outcome, HookOutcome):
                    raise ValueError("MNEMO_HOOK_INVALID_OUTCOME")
            except Exception:
                outcome = HookOutcome(HookStatus.FAILED, "MNEMO_HOOK_POST_FAILED")
                warnings.append(HookWarning(registration.name, "after", "MNEMO_HOOK_POST_FAILED"))
                if strict_memory and final_result.started and final_result.exit_code == 0:
                    final_result = replace(
                        final_result,
                        exit_code=STRICT_HOOK_FAILURE_EXIT_CODE,
                        failure_code=CommandFailureCode.STRICT_HOOK_FAILURE,
                    )
            after_finished_at = self._clock()
            outcomes.append(RegisteredHookOutcome(registration.name, outcome))
            timings.append(
                HookTiming(
                    registration.name,
                    before_started_at,
                    before_finished_at,
                    after_started_at,
                    after_finished_at,
                )
            )
            warnings.extend(outcome.warnings)
            if (
                strict_memory
                and outcome.status is HookStatus.FAILED
                and final_result.started
                and final_result.exit_code == 0
            ):
                final_result = replace(
                    final_result,
                    exit_code=STRICT_HOOK_FAILURE_EXIT_CODE,
                    failure_code=CommandFailureCode.STRICT_HOOK_FAILURE,
                )
        return CommandWrapperResult(final_result, tuple(outcomes), tuple(warnings), tuple(timings))


__all__ = [
    "COMMAND_HOOK_ENTRY_POINT_GROUP",
    "COMMAND_NOT_EXECUTABLE_EXIT_CODE",
    "COMMAND_NOT_FOUND_EXIT_CODE",
    "COMMAND_WRAPPER_FAILURE_EXIT_CODE",
    "STRICT_HOOK_FAILURE_EXIT_CODE",
    "AfterHook",
    "BeforeHook",
    "Clock",
    "CommandContext",
    "CommandFailure",
    "CommandFailureCode",
    "CommandInvocation",
    "CommandResult",
    "CommandWrapper",
    "CommandWrapperResult",
    "ExecutableResolver",
    "HookDiscoveryResult",
    "HookOutcome",
    "HookRegistration",
    "HookStatus",
    "HookTiming",
    "HookWarning",
    "InvocationIdFactory",
    "ProcessExecutor",
    "RegisteredHookOutcome",
    "discover_command_hooks",
    "merge_command_hooks",
]
