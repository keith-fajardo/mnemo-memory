"""JSON-file store for pending host-agent-takeover markers."""

from __future__ import annotations

from pathlib import Path

from mnemo_memory.connectors.automatic_memory.pending_takeover import (
    LocalPendingTakeoverStore,
)
from mnemo_memory.packages.domain import (
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    SessionId,
    TaskId,
    Visibility,
    WorkspaceId,
)


def _task_scope() -> MemoryScope:
    """Construct a TASK-level MemoryScope inline."""
    return MemoryScope(
        OwnerId.new(),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.new(),
        ProjectId.new(),
        session_id=SessionId.new(),
        task_id=TaskId.new(),
    )


def test_mark_pending_clear_roundtrip(tmp_path: Path) -> None:
    """Test mark → pending → clear roundtrip for pending-takeover store."""
    store = LocalPendingTakeoverStore(tmp_path)
    scope = _task_scope()

    # Initially, no pending takeover
    assert store.pending(scope) is None

    # Mark with a source event key
    store.mark(scope, "evt-1")
    assert store.pending(scope) == "evt-1"

    # Clear the pending marker
    store.clear(scope)
    assert store.pending(scope) is None
