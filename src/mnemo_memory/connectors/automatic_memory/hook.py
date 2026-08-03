"""Client-hook handler for opt-in automatic checkpoint reminders.

This module deliberately does not read a transcript, environment values, or tool payloads. On a
trusted enabled project boundary it may refresh Mnemo's bounded static source-structure projection;
it never stores source text and only receives lifecycle metadata on stdin.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from mnemo_memory.packages.application.automatic_memory import (
    AutomaticMemoryBindingError,
    LocalMemoryProjectBindingStore,
    MemoryProjectBinding,
    exclusive_local_file_lock,
)
from mnemo_memory.packages.domain import CodeSymbol
from mnemo_memory.packages.project_index import (
    SourceImpactService,
    SourceSnapshotDiff,
    SourceStructureParser,
    SourceStructureParseRequest,
)
from mnemo_memory.packages.storage import SQLiteSourceStructureRepository

ClientName = Literal["codex", "claude-code"]

_SAVE_TOOL_NAMES = {
    "mcp__mnemo-memory__save_checkpoint",
    "mcp__mnemo_memory__save_checkpoint",
}
_MUTATING_TOOLS = {"Bash", "apply_patch", "Edit", "Write"}
_MAX_CHANGE_SYMBOLS = 12
_MAX_CHANGE_SYMBOL_LABEL_LENGTH = 256


@dataclass(frozen=True, slots=True)
class AutomaticMemoryHook:
    """Make a small, client-neutral decision from trusted lifecycle metadata."""

    data_directory: Path
    client: ClientName

    def handle(self, event: object) -> dict[str, object]:
        if not isinstance(event, dict):
            return self._safe_output("MNEMO_MEMORY_HOOK_INPUT_INVALID")
        event_name = event.get("hook_event_name")
        session_id = event.get("session_id")
        cwd = event.get("cwd")
        if not isinstance(event_name, str) or not isinstance(session_id, str) or not session_id:
            return self._safe_output("MNEMO_MEMORY_HOOK_INPUT_INVALID")
        if not isinstance(cwd, str):
            return self._safe_output("MNEMO_MEMORY_HOOK_INPUT_INVALID")
        try:
            binding = LocalMemoryProjectBindingStore(self.data_directory).get(Path(cwd))
        except (AutomaticMemoryBindingError, OSError):
            return self._safe_output("MNEMO_MEMORY_PROJECT_UNAVAILABLE")
        if binding is None:
            return self._safe_output("MNEMO_MEMORY_PROJECT_UNENABLED")

        state = _SessionStateStore(self.data_directory).get(session_id)
        tool_name = event.get("tool_name")
        if event_name == "PostToolUse" and isinstance(tool_name, str):
            if tool_name in _SAVE_TOOL_NAMES:
                _SessionStateStore(self.data_directory).save(session_id, dirty=False, saved=True)
            elif tool_name in _MUTATING_TOOLS:
                _SessionStateStore(self.data_directory).save(session_id, dirty=True, saved=False)
            return {}
        if event_name == "SessionStart":
            refreshed = self._refresh_source_structure(binding)
            return self._context_output(
                _resume_instruction(binding.checkpoint_scope.to_dict(), refreshed)
            )
        if event_name == "UserPromptSubmit" and state.dirty and not state.saved:
            # Do not inspect ``prompt``. The cue is driven only by trusted lifecycle state.
            return self._context_output(_dirty_session_instruction(), event_name="UserPromptSubmit")
        if event_name in {"Stop", "PreCompact"} and state.dirty and not state.saved:
            if event.get("stop_hook_active") is True:
                return {}
            refreshed = self._refresh_source_structure(binding)
            return self._checkpoint_output(
                _checkpoint_instruction(binding.checkpoint_scope.to_dict(), refreshed)
            )
        return {}

    def _context_output(
        self, instruction: str, *, event_name: str = "SessionStart"
    ) -> dict[str, object]:
        if self.client == "codex":
            return {
                "hookSpecificOutput": {
                    "hookEventName": event_name,
                    "additionalContext": instruction,
                }
            }
        return {
            "hookSpecificOutput": {
                "hookEventName": event_name,
                "additionalContext": instruction,
            }
        }

    def _checkpoint_output(self, instruction: str) -> dict[str, object]:
        if self.client == "claude-code":
            return {"decision": "block", "reason": instruction}
        return {"decision": "block", "reason": instruction}

    def _safe_output(self, code: str) -> dict[str, object]:
        # Lifecycle hooks must fail open.  The stable code contains no local path or event payload.
        return {"systemMessage": code}

    def _refresh_source_structure(self, binding: MemoryProjectBinding) -> _SourceRefresh:
        """Best-effort local refresh; failure never blocks a coding client session."""
        try:
            repository = SQLiteSourceStructureRepository(self.data_directory / "mnemo.sqlite3")
            repository.migrate()
            previous = repository.get_active_snapshot(binding.scope)
            stored = repository.store_and_activate(
                SourceStructureParser().parse(
                    SourceStructureParseRequest(binding.scope, binding.project_root)
                )
            )
            if previous is None or previous.snapshot_id == stored.snapshot.snapshot_id:
                return _SourceRefresh(stored.snapshot.source_digest)
            diff = SourceImpactService(repository).diff(
                binding.scope, previous.snapshot_id, stored.snapshot.snapshot_id
            )
            return _SourceRefresh(
                stored.snapshot.source_digest,
                _SourceChangeSummary.from_diff(diff),
            )
        except (OSError, ValueError, RuntimeError):
            return _SourceRefresh(None)


@dataclass(frozen=True, slots=True)
class _SourceChangeSummary:
    """Bounded metadata-only summary; it deliberately contains no source text."""

    added_symbol_count: int
    removed_symbol_count: int
    added_edge_count: int
    removed_edge_count: int
    added_symbols: tuple[str, ...]
    removed_symbols: tuple[str, ...]

    @classmethod
    def from_diff(cls, diff: SourceSnapshotDiff) -> _SourceChangeSummary:
        # ``SourceSnapshotDiff`` is intentionally structural: safe relative paths and qualified
        # names only. Keep the hook payload bounded even for a large repository rewrite.
        return cls(
            len(diff.added_symbols),
            len(diff.removed_symbols),
            len(diff.added_edges),
            len(diff.removed_edges),
            _summary_symbols(diff.added_symbols),
            _summary_symbols(diff.removed_symbols),
        )

    @property
    def changed(self) -> bool:
        return any(
            (
                self.added_symbol_count,
                self.removed_symbol_count,
                self.added_edge_count,
                self.removed_edge_count,
            )
        )


@dataclass(frozen=True, slots=True)
class _SourceRefresh:
    digest: str | None
    changes: _SourceChangeSummary | None = None


def _summary_symbols(symbols: tuple[CodeSymbol, ...]) -> tuple[str, ...]:
    """Return whole safe structural identities; never truncate a symbol into a false fact."""
    labels = tuple(
        f"{symbol.relative_path}:{symbol.qualified_name}"
        for symbol in symbols
        if len(symbol.relative_path) + len(symbol.qualified_name) + 1
        <= _MAX_CHANGE_SYMBOL_LABEL_LENGTH
    )
    return labels[:_MAX_CHANGE_SYMBOLS]


@dataclass(frozen=True, slots=True)
class _SessionState:
    dirty: bool = False
    saved: bool = False


class _SessionStateStore:
    """Small private marker set; never a transcript, prompt, result, or checkpoint payload."""

    _name = "automatic-memory-session-state.json"

    def __init__(self, data_directory: Path) -> None:
        self._directory = data_directory.expanduser().resolve()
        self._path = self._directory / self._name

    def get(self, session_id: str) -> _SessionState:
        values = self._read()
        value = values.get(session_id)
        if not isinstance(value, dict):
            return _SessionState()
        return _SessionState(value.get("dirty") is True, value.get("saved") is True)

    def save(self, session_id: str, *, dirty: bool, saved: bool) -> None:
        try:
            with exclusive_local_file_lock(self._directory, ".automatic-memory-state.lock"):
                values = self._read()
                values[session_id] = {"dirty": dirty, "saved": saved}
                # Bounded state avoids making lifecycle metadata a long-term activity log.
                if len(values) > 128:
                    values = {session_id: values[session_id]}
                self._write(values)
        except AutomaticMemoryBindingError:
            return

    def _read(self) -> dict[str, object]:
        if not self._path.exists():
            return {}
        if self._path.is_symlink():
            return {}
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, values: dict[str, object]) -> None:
        try:
            self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self._path.exists() and self._path.is_symlink():
                return
            with NamedTemporaryFile(
                "w", encoding="utf-8", dir=self._directory, delete=False
            ) as handle:
                temporary = Path(handle.name)
                os.chmod(temporary, 0o600)
                json.dump(values, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
            os.replace(temporary, self._path)
            os.chmod(self._path, 0o600)
        except OSError:
            return
        finally:
            if "temporary" in locals():
                temporary.unlink(missing_ok=True)


def _resume_instruction(scope: Mapping[str, object], refreshed: _SourceRefresh) -> str:
    instruction = (
        "Mnemo automatic task memory is enabled. Before continuing, call get_context using this "
        f"stored task scope: {json.dumps(scope, sort_keys=True, separators=(',', ':'))}. "
        "Do not claim that you know prior changes, decisions, verification, or impact until you "
        "have checked that context. When the task names a supported-language symbol or relative "
        "path, include it as source_query to retrieve the matching saved structure. Treat "
        "retrieved facts as bounded context, not a transcript."
    )
    if refreshed.digest is not None:
        instruction += (
            " For a static dependency or impact request, include this exact "
            "current_source_digest to prove the refreshed source snapshot is current: "
            f"{refreshed.digest}."
        )
    if refreshed.changes is not None:
        instruction += _source_change_instruction(refreshed.changes)
    return instruction


def _checkpoint_instruction(scope: Mapping[str, object], refreshed: _SourceRefresh) -> str:
    instruction = (
        "Before finishing or compacting this task, call Mnemo save_checkpoint with this project "
        f"scope: {json.dumps(scope, sort_keys=True, separators=(',', ':'))}. "
        "Create or revise the active checkpoint with a concise objective, current state, "
        "decisions, verification, evidence, and next action. When a reasoning or analysis "
        "mistake was corrected, include a lesson: trigger, mistaken assumption, correction, "
        "prevention, and the IDs of its supporting evidence. Do not include a full transcript."
    )
    if refreshed.changes is not None:
        instruction += _source_change_instruction(refreshed.changes)
    return instruction


def _source_change_instruction(changes: _SourceChangeSummary) -> str:
    """Tell the agent only what Mnemo can prove about a structural refresh."""
    if not changes.changed:
        return (
            " The source digest changed, but no supported declaration or resolved relationship "
            "changed in Mnemo's bounded structural projection. Do not infer a reason from that."
        )
    instruction = (
        " Mnemo observed a structural change since its prior snapshot: "
        f"{changes.added_symbol_count} declaration(s) added, "
        f"{changes.removed_symbol_count} removed, "
        f"{changes.added_edge_count} resolved relationship(s) added, and "
        f"{changes.removed_edge_count} removed."
    )
    if changes.added_symbols:
        instruction += f" Added declarations: {', '.join(changes.added_symbols)}."
    if changes.removed_symbols:
        instruction += f" Removed declarations: {', '.join(changes.removed_symbols)}."
    return instruction


def _dirty_session_instruction() -> str:
    """One short prompt-boundary cue; no submitted prompt content is read or retained."""
    return (
        "Mnemo observed a project mutation in this session. Before analyzing prior changes, "
        "decisions, verification, or impact, check the stored Mnemo context; save a concise "
        "checkpoint before the task ends. Record a structured lesson when a mistaken assumption "
        "was corrected."
    )
