from __future__ import annotations

from pathlib import Path

import pytest

from mnemo_memory.connectors.automatic_memory.checkpoint_evidence import (
    CheckpointFileEvidenceError,
    CheckpointFileEvidenceResolver,
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


def scope() -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
        SessionId.from_string("44444444-4444-4444-8444-444444444444"),
        TaskId.from_string("55555555-5555-4555-8555-555555555555"),
    )


def test_checkpoint_file_evidence_rejects_escape_symlink_and_oversize(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "private.txt"
    outside.write_text("private", encoding="utf-8")
    (project / "linked.txt").symlink_to(outside)
    (project / "large.bin").write_bytes(b"x" * (5 * 1024 * 1024 + 1))
    resolver = CheckpointFileEvidenceResolver(project)

    for unsafe in ("../private.txt", "linked.txt", "large.bin"):
        with pytest.raises(CheckpointFileEvidenceError) as captured:
            resolver(scope(), (unsafe,))
        assert unsafe not in str(captured.value)
        assert str(project) not in str(captured.value)


def test_checkpoint_file_evidence_uses_full_hash_and_stable_local_ids(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "safe.py").write_text("value = 1\n", encoding="utf-8")
    resolver = CheckpointFileEvidenceResolver(project)

    first = resolver(scope(), ("safe.py",))[0]
    second = resolver(scope(), ("safe.py",))[0]

    assert first.evidence_id == second.evidence_id
    assert first.source_id == second.source_id
    assert len(first.content_hash) == 71
    assert first.location.to_dict() == {"uri": "repo://safe.py"}
    assert first.immutable_source_ref.startswith("working-tree:")
