from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mnemo_memory.packages.application import (
    CheckpointApplicationNotFound,
    CreateCheckpoint,
    GetCheckpoint,
    GetCheckpointContext,
    LocalConfig,
    LocalConfigurationError,
    LocalRuntimeError,
    ReviseCheckpoint,
    SynchronizeKnowledgeDocuments,
    build_checkpoint_runtime,
    resolve_local_config,
)
from mnemo_memory.packages.domain import (
    CheckpointContent,
    CheckpointLesson,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    SessionId,
    SourceId,
    SourceTrustClass,
    TaskId,
    VerificationStatus,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.knowledge import KnowledgeDocumentParser, KnowledgeDocumentParseRequest

NOW = datetime(2026, 8, 2, 13, 0, tzinfo=UTC)
HASH = "sha256:" + "c" * 64


def scope() -> MemoryScope:
    return MemoryScope(
        OwnerId.new(),
        ScopeLevel.TASK,
        Visibility.PROJECT,
        workspace_id=WorkspaceId.new(),
        project_id=ProjectId.new(),
        session_id=SessionId.new(),
        task_id=TaskId.new(),
    )


def evidence() -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.CHECKPOINT,
        SourceTrustClass.USER_AUTHORED,
        "synthetic://runtime",
        HASH,
        EvidenceLocation("fixture://runtime"),
        NOW,
        VerificationStatus.VERIFIED,
    )


def content() -> CheckpointContent:
    return CheckpointContent(
        "Persist a checkpoint across local runtimes",
        ("created through runtime A",),
        "active",
        ("open runtime B",),
        ("use the canonical data directory",),
        (),
        (),
        ("tests/unit/test_checkpoint_runtime.py",),
        (),
        ("pytest",),
        40,
    )


def test_data_directory_precedence_and_normalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = tmp_path / "explicit space" / "Δ"
    environment_directory = tmp_path / "environment"
    config = resolve_local_config(
        explicit,
        environment={"MNEMO_DATA_DIR": str(environment_directory)},
        default_directory=tmp_path / "default",
    )
    assert config.data_directory == explicit.resolve()
    from_environment = resolve_local_config(
        environment={"MNEMO_DATA_DIR": str(environment_directory)},
        default_directory=tmp_path / "default",
    )
    assert from_environment.data_directory == environment_directory.resolve()
    persisted = tmp_path / "default"
    persisted.mkdir()
    expected = LocalConfig.defaults(persisted)
    expected.config_path.write_text(json.dumps(expected.to_dict()))
    assert resolve_local_config(default_directory=persisted) == expected
    monkeypatch.chdir(tmp_path)
    assert resolve_local_config(default_directory=persisted).data_directory == persisted.resolve()
    with pytest.raises(LocalConfigurationError, match="absolute"):
        resolve_local_config(Path("relative"), default_directory=persisted)


def test_resolution_rejects_file_and_mismatched_persisted_directory(tmp_path: Path) -> None:
    occupied = tmp_path / "occupied"
    occupied.write_text("not a directory")
    with pytest.raises(LocalConfigurationError, match="occupied"):
        resolve_local_config(occupied)
    configured = tmp_path / "configured"
    configured.mkdir()
    wrong = LocalConfig.defaults(tmp_path / "other")
    configured.joinpath("config.json").write_text(json.dumps(wrong.to_dict()))
    with pytest.raises(LocalConfigurationError, match="does not match"):
        resolve_local_config(configured)


def test_runtime_persists_checkpoint_across_instances_and_isolates_directories(
    tmp_path: Path,
) -> None:
    data_a = tmp_path / "data with spaces"
    configuration_a = LocalConfig.defaults(data_a)
    scope_value = scope()
    with build_checkpoint_runtime(configuration_a) as runtime_a:
        created = runtime_a.checkpoint_service.create(
            CreateCheckpoint(scope_value, content(), (evidence(),))
        )
        assert runtime_a.repository.schema_version() == 24
        assert runtime_a.repository.connection_settings()["foreign_keys"] == 1
    with build_checkpoint_runtime(configuration_a) as runtime_b:
        restored = runtime_b.checkpoint_service.get(
            GetCheckpoint(scope_value, created.aggregate.checkpoint_id)
        )
        assert restored.revision == created.revision
        assert restored.revision.evidence_references == created.revision.evidence_references
    with (
        build_checkpoint_runtime(LocalConfig.defaults(tmp_path / "isolated")) as runtime_c,
        pytest.raises(CheckpointApplicationNotFound),
    ):
        runtime_c.checkpoint_service.get(
            GetCheckpoint(scope_value, created.aggregate.checkpoint_id)
        )


def test_runtime_composes_durable_scoped_knowledge_service(tmp_path: Path) -> None:
    configuration = LocalConfig.defaults(tmp_path / "knowledge runtime")
    knowledge_scope = MemoryScope(
        OwnerId.new(),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.new(),
        ProjectId.new(),
    )
    document = KnowledgeDocumentParser().parse(
        KnowledgeDocumentParseRequest(knowledge_scope, "notes/decision.md"),
        "# Decision\nKeep user-owned documents scoped and explicit.",
    )
    with build_checkpoint_runtime(configuration) as runtime:
        assert runtime.knowledge_document_service is not None
        result = runtime.knowledge_document_service.synchronize(
            SynchronizeKnowledgeDocuments(knowledge_scope, (document,))
        )
        document_id = result.store_result.active_documents[0].document_id
    with build_checkpoint_runtime(configuration) as runtime:
        assert runtime.knowledge_document_repository is not None
        restored = runtime.knowledge_document_repository.get_current_revision(
            knowledge_scope, document_id
        )
    assert restored.document == document


def test_runtime_preserves_reasoning_lesson_across_reopen(tmp_path: Path) -> None:
    configuration = LocalConfig.defaults(tmp_path / "lesson runtime")
    scope_value = scope()
    reference = evidence()
    lesson = CheckpointLesson(
        "A test showed the expected and actual values had different grains.",
        "A timestamp join was assumed to represent the Finance business day.",
        "Use the documented business-date grain for reconciliation.",
        "Verify both input grains before proposing a join change.",
        (reference.evidence_id,),
    )
    with build_checkpoint_runtime(configuration) as runtime:
        created = runtime.checkpoint_service.create(
            CreateCheckpoint(scope_value, replace(content(), lessons=(lesson,)), (reference,))
        )
    with build_checkpoint_runtime(configuration) as runtime:
        restored = runtime.checkpoint_service.get(
            GetCheckpoint(scope_value, created.aggregate.checkpoint_id)
        )
    assert restored.revision.content.lessons == (lesson,)


def test_runtime_returns_a_prior_lesson_when_a_newer_revision_omits_it(tmp_path: Path) -> None:
    configuration = LocalConfig.defaults(tmp_path / "historical lesson runtime")
    scope_value = scope()
    reference = evidence()
    lesson = CheckpointLesson(
        "A test contradicted the timestamp-join reconciliation result.",
        "The join was assumed to preserve the Finance business-date grain.",
        "Use the documented business-date comparison grain.",
        "Check input grain before changing a reconciliation join.",
        (reference.evidence_id,),
    )
    with build_checkpoint_runtime(configuration) as runtime:
        initial = runtime.checkpoint_service.create(
            CreateCheckpoint(scope_value, replace(content(), lessons=(lesson,)), (reference,))
        )
        runtime.checkpoint_service.revise(
            ReviseCheckpoint(
                scope_value,
                initial.aggregate.checkpoint_id,
                initial.revision.revision_id,
                content(),
                (evidence(),),
            )
        )
    with build_checkpoint_runtime(configuration) as runtime:
        packet = runtime.checkpoint_service.get_context(GetCheckpointContext(scope_value))
    assert len(packet.episodic_memories) == 1
    assert lesson.prevention in packet.episodic_memories[0].content


def test_runtime_rejects_corrupt_and_newer_databases_without_fallback(tmp_path: Path) -> None:
    corrupt_directory = tmp_path / "corrupt"
    corrupt_directory.mkdir()
    corrupt_path = corrupt_directory / "mnemo.sqlite3"
    corrupt_path.write_text("not sqlite")
    with pytest.raises(LocalRuntimeError, match="unavailable") as corrupt:
        build_checkpoint_runtime(LocalConfig.defaults(corrupt_directory))
    assert corrupt.value.__cause__ is not None

    newer_directory = tmp_path / "newer"
    with (
        build_checkpoint_runtime(LocalConfig.defaults(newer_directory)) as runtime,
        sqlite3.connect(runtime.repository.path) as connection,
    ):
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (25, ?)",
            (NOW.isoformat(),),
        )
    with pytest.raises(LocalRuntimeError, match="unavailable"):
        build_checkpoint_runtime(LocalConfig.defaults(newer_directory))


def test_runtime_closes_after_exception(tmp_path: Path) -> None:
    runtime = build_checkpoint_runtime(LocalConfig.defaults(tmp_path / "exception"))
    with pytest.raises(RuntimeError, match="fixture"), runtime:
        raise RuntimeError("fixture")
    with pytest.raises(RuntimeError, match="closed"):
        runtime.__enter__()
