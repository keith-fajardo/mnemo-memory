"""Regression coverage for ADR 0002's canonical revision-content boundary."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path

import pytest

from mnemo_memory.packages.domain import (
    Checkpoint,
    CheckpointContent,
    CheckpointId,
    CheckpointLesson,
    CheckpointRevision,
    CheckpointRevisionId,
    CheckpointStatus,
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
from mnemo_memory.packages.storage import SQLiteCheckpointRepository, SQLiteMigrationError

NOW = datetime(2026, 8, 2, 9, 0, tzinfo=UTC)
HASH = "sha256:" + "d" * 64
ROOT = Path(__file__).parents[2]


def task_scope() -> MemoryScope:
    return MemoryScope(
        owner_id=OwnerId.new(),
        level=ScopeLevel.TASK,
        visibility=Visibility.PROJECT,
        workspace_id=WorkspaceId.new(),
        project_id=ProjectId.new(),
        session_id=SessionId.new(),
        task_id=TaskId.new(),
    )


def evidence() -> EvidenceReference:
    return EvidenceReference(
        evidence_id=EvidenceId.new(),
        source_id=SourceId.new(),
        source_type=EvidenceSourceType.REPOSITORY,
        trust_class=SourceTrustClass.CURRENT_STRUCTURAL,
        immutable_source_ref="git:fixture:checkpoint.py",
        content_hash=HASH,
        location=EvidenceLocation("repo://checkpoint.py"),
        observed_at=NOW,
        verification_status=VerificationStatus.VERIFIED,
    )


def checkpoint(scope: MemoryScope, reference: EvidenceReference) -> Checkpoint:
    return Checkpoint(
        checkpoint_id=CheckpointId.new(),
        scope=scope,
        task_objective="Persist canonical checkpoint content",
        completed_work=("created test fixture",),
        current_state="migration ready",
        remaining_work=("upgrade database",),
        decisions=("separate aggregate identity",),
        failures=(),
        blockers=(),
        relevant_files=("packages/storage/sqlite.py",),
        relevant_artifacts=("migrations/0002_checkpoint_aggregate_revisions.sql",),
        verification_performed=("pytest",),
        evidence_references=(reference,),
        status=CheckpointStatus.DRAFT,
        revision=1,
        supersedes_checkpoint_id=None,
        superseded_by_checkpoint_id=None,
        token_estimate=17,
        created_at=NOW,
        updated_at=NOW,
    )


def legacy_repository(tmp_path: Path, name: str = "legacy.sqlite3") -> SQLiteCheckpointRepository:
    repository = SQLiteCheckpointRepository(tmp_path / name, base_directory=tmp_path)
    with sqlite3.connect(repository.path) as connection:
        for statement in (
            resources.files("mnemo_memory")
            .joinpath("resources", "migrations", "0001_initial.sql")
            .read_text(encoding="utf-8")
            .split(";")
        ):
            if statement.strip():
                connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)",
            (NOW.isoformat(),),
        )
    return repository


def seed_legacy_checkpoint(repository: SQLiteCheckpointRepository, value: Checkpoint) -> None:
    """Test-only v1 fixture seeding; production code never writes replacement chains."""
    scope = value.scope
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO principals(owner_id) VALUES (?)", (str(scope.owner_id),)
        )
        connection.execute(
            "INSERT OR IGNORE INTO workspaces(workspace_id, owner_id) VALUES (?, ?)",
            (str(scope.workspace_id), str(scope.owner_id)),
        )
        connection.execute(
            "INSERT OR IGNORE INTO projects(project_id, workspace_id, owner_id) VALUES (?, ?, ?)",
            (str(scope.project_id), str(scope.workspace_id), str(scope.owner_id)),
        )
        connection.execute(
            "INSERT OR IGNORE INTO sessions(session_id, project_id) VALUES (?, ?)",
            (str(scope.session_id), str(scope.project_id)),
        )
        connection.execute(
            "INSERT OR IGNORE INTO tasks(task_id, session_id) VALUES (?, ?)",
            (str(scope.task_id), str(scope.session_id)),
        )
        for item in value.evidence_references:
            connection.execute(
                "INSERT OR IGNORE INTO evidence(evidence_id, source_id, payload_json) VALUES (?, ?, ?)",  # noqa: E501
                (
                    str(item.evidence_id),
                    str(item.source_id),
                    json.dumps(item.to_dict(), sort_keys=True),
                ),
            )
        connection.execute(
            "INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(value.checkpoint_id),
                str(scope.owner_id),
                str(scope.workspace_id),
                str(scope.project_id),
                str(scope.session_id),
                str(scope.task_id),
                value.revision,
            ),
        )
        connection.execute(
            "INSERT INTO checkpoint_revisions VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(value.checkpoint_id),
                value.revision,
                value.status.value,
                None
                if value.supersedes_checkpoint_id is None
                else str(value.supersedes_checkpoint_id),
                json.dumps(value.to_dict(), sort_keys=True),
                value.created_at.isoformat(),
            ),
        )
        connection.executemany(
            "INSERT INTO checkpoint_evidence VALUES (?, ?, ?)",
            [
                (str(value.checkpoint_id), value.revision, str(item.evidence_id))
                for item in value.evidence_references
            ],
        )


def read_v2_rows(path: Path) -> tuple[sqlite3.Row, list[sqlite3.Row]]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        aggregate = connection.execute("SELECT * FROM checkpoint_aggregates").fetchone()
        revisions = connection.execute(
            "SELECT * FROM checkpoint_revision_records ORDER BY revision_number"
        ).fetchall()
        assert aggregate is not None
        return aggregate, revisions
    finally:
        connection.close()


def test_canonical_content_round_trip_is_identity_free() -> None:
    legacy = checkpoint(task_scope(), evidence())
    content = CheckpointContent.from_legacy(legacy)

    encoded = content.to_dict()
    assert CheckpointContent.from_dict(encoded) == content
    forbidden = {
        "checkpoint_id",
        "checkpoint_revision_id",
        "revision_number",
        "predecessor_revision_id",
        "supersedes_checkpoint_id",
        "scope",
        "status",
        "created_at",
    }
    assert not forbidden.intersection(encoded)
    with pytest.raises(ValueError, match="fields are invalid"):
        CheckpointContent.from_dict({**encoded, "supersedes_checkpoint_id": None})


def test_checkpoint_lesson_round_trip_is_identity_free_and_evidence_backed() -> None:
    reference = evidence()
    legacy = checkpoint(task_scope(), reference)
    lesson = CheckpointLesson(
        trigger="The reconciliation total diverged after the timestamp join.",
        mistaken_assumption="The finance seed and model timestamps used the same grain.",
        correction="Compare values at the documented business-date grain.",
        prevention="Verify grain and null behavior before changing a reconciliation join.",
        evidence_ids=(reference.evidence_id,),
    )
    content = replace(CheckpointContent.from_legacy(legacy), lessons=(lesson,))

    encoded = content.to_dict()
    assert CheckpointContent.from_dict(encoded) == content
    assert encoded["lessons"] == [lesson.to_dict()]
    assert "checkpoint_id" not in encoded["lessons"][0]
    assert "checkpoint_revision_id" not in encoded["lessons"][0]

    revision = CheckpointRevision(
        revision_id=CheckpointRevisionId.new(),
        checkpoint_id=legacy.checkpoint_id,
        revision_number=1,
        predecessor_revision_id=None,
        scope=legacy.scope,
        content=content,
        status=CheckpointStatus.ACTIVE,
        evidence_references=(reference,),
        created_at=NOW,
    )
    assert CheckpointRevision.from_dict(revision.to_dict()) == revision


def test_checkpoint_lesson_rejects_missing_or_unrelated_evidence() -> None:
    reference = evidence()
    with pytest.raises(ValueError, match="requires evidence identifiers"):
        CheckpointLesson("trigger", "assumption", "correction", "prevention", ())

    legacy = checkpoint(task_scope(), reference)
    lesson = CheckpointLesson(
        "trigger",
        "assumption",
        "correction",
        "prevention",
        (EvidenceId.new(),),
    )
    content = replace(CheckpointContent.from_legacy(legacy), lessons=(lesson,))
    with pytest.raises(ValueError, match="must belong to its revision"):
        CheckpointRevision(
            CheckpointRevisionId.new(),
            legacy.checkpoint_id,
            1,
            None,
            legacy.scope,
            content,
            CheckpointStatus.ACTIVE,
            (reference,),
            NOW,
        )


def test_old_canonical_content_without_lessons_remains_readable() -> None:
    encoded = CheckpointContent.from_legacy(checkpoint(task_scope(), evidence())).to_dict()
    encoded.pop("lessons")
    assert CheckpointContent.from_dict(encoded).lessons == ()


def test_canonical_revision_round_trip_keeps_ids_distinct() -> None:
    legacy = checkpoint(task_scope(), evidence())
    first_id = CheckpointRevisionId.new()
    first = CheckpointRevision(
        revision_id=first_id,
        checkpoint_id=legacy.checkpoint_id,
        revision_number=1,
        predecessor_revision_id=None,
        scope=legacy.scope,
        content=CheckpointContent.from_legacy(legacy),
        status=legacy.status,
        evidence_references=legacy.evidence_references,
        created_at=legacy.created_at,
    )
    second = CheckpointRevision(
        revision_id=CheckpointRevisionId.new(),
        checkpoint_id=legacy.checkpoint_id,
        revision_number=2,
        predecessor_revision_id=first_id,
        scope=legacy.scope,
        content=first.content,
        status=CheckpointStatus.ACTIVE,
        evidence_references=legacy.evidence_references,
        created_at=NOW + timedelta(minutes=1),
    )

    assert CheckpointRevision.from_dict(second.to_dict()) == second
    assert second.checkpoint_id == first.checkpoint_id
    assert second.revision_id != first.revision_id
    assert second.predecessor_revision_id == first.revision_id
    with pytest.raises(ValueError, match="fields are invalid"):
        CheckpointRevision.from_dict({**second.to_dict(), "legacy_checkpoint": {}})


def test_singleton_legacy_checkpoint_migrates_to_canonical_payload(tmp_path: Path) -> None:
    repository = legacy_repository(tmp_path)
    legacy = checkpoint(task_scope(), evidence())
    seed_legacy_checkpoint(repository, legacy)

    repository.migrate()
    aggregate, revisions = read_v2_rows(repository.path)

    assert aggregate["checkpoint_id"] == str(legacy.checkpoint_id)
    assert aggregate["visibility"] == legacy.scope.visibility.value
    assert aggregate["current_revision_number"] == 1
    assert aggregate["lifecycle_status"] == legacy.status.value
    assert aggregate["created_at"] == legacy.created_at.isoformat()
    assert revisions[0]["predecessor_revision_id"] is None
    assert revisions[0]["status"] == legacy.status.value
    payload = json.loads(revisions[0]["payload_json"])
    assert payload == CheckpointContent.from_legacy(legacy).to_dict()
    assert "supersedes_checkpoint_id" not in payload
    assert "checkpoint_id" not in payload

    with sqlite3.connect(repository.path) as connection:
        links = connection.execute(
            "SELECT evidence_id FROM checkpoint_revision_evidence"
        ).fetchall()
    assert links == [(str(legacy.evidence_references[0].evidence_id),)]
    assert repository.schema_version() == 9


def test_empty_and_independent_legacy_chains_preserve_each_scope(tmp_path: Path) -> None:
    empty = legacy_repository(tmp_path, "empty legacy.sqlite3")
    empty.migrate()
    with sqlite3.connect(empty.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM checkpoint_aggregates").fetchone()[0] == 0

    repository = legacy_repository(tmp_path, "independent chains.sqlite3")
    first = checkpoint(task_scope(), evidence())
    second = checkpoint(task_scope(), evidence())
    superseded, replacement = first.revise(CheckpointId.new(), NOW + timedelta(minutes=1))
    for legacy in (superseded, replacement, second):
        seed_legacy_checkpoint(repository, legacy)

    repository.migrate()
    with sqlite3.connect(repository.path) as connection:
        connection.row_factory = sqlite3.Row
        aggregates = connection.execute(
            "SELECT * FROM checkpoint_aggregates ORDER BY checkpoint_id"
        ).fetchall()
        revisions = connection.execute(
            "SELECT * FROM checkpoint_revision_records ORDER BY checkpoint_id, revision_number"
        ).fetchall()
    assert {row["checkpoint_id"] for row in aggregates} == {
        str(first.checkpoint_id),
        str(second.checkpoint_id),
    }
    first_header = next(
        row for row in aggregates if row["checkpoint_id"] == str(first.checkpoint_id)
    )
    second_header = next(
        row for row in aggregates if row["checkpoint_id"] == str(second.checkpoint_id)
    )
    assert first_header["owner_id"] == str(first.scope.owner_id)
    assert first_header["visibility"] == first.scope.visibility.value
    assert first_header["workspace_id"] == str(first.scope.workspace_id)
    assert first_header["project_id"] == str(first.scope.project_id)
    assert first_header["created_at"] == first.created_at.isoformat()
    assert first_header["updated_at"] == replacement.created_at.isoformat()
    assert second_header["lifecycle_status"] == second.status.value
    assert {(row["checkpoint_id"], row["revision_number"]) for row in revisions} == {
        (str(first.checkpoint_id), 1),
        (str(first.checkpoint_id), 2),
        (str(second.checkpoint_id), 1),
    }


def test_legacy_replacement_chain_preserves_canonical_content_and_history(tmp_path: Path) -> None:
    repository = legacy_repository(tmp_path)
    original = checkpoint(task_scope(), evidence())
    superseded, replacement = original.revise(CheckpointId.new(), NOW + timedelta(minutes=1))
    seed_legacy_checkpoint(repository, superseded)
    seed_legacy_checkpoint(repository, replacement)

    repository.migrate()
    aggregate, revisions = read_v2_rows(repository.path)

    assert aggregate["checkpoint_id"] == str(original.checkpoint_id)
    assert aggregate["current_revision_number"] == 2
    assert aggregate["current_revision_id"] == revisions[1]["checkpoint_revision_id"]
    assert revisions[0]["predecessor_revision_id"] is None
    assert revisions[1]["predecessor_revision_id"] == revisions[0]["checkpoint_revision_id"]
    assert [row["revision_number"] for row in revisions] == [1, 2]
    assert [json.loads(row["payload_json"]) for row in revisions] == [
        CheckpointContent.from_legacy(superseded).to_dict(),
        CheckpointContent.from_legacy(replacement).to_dict(),
    ]
    assert all(
        "supersedes_checkpoint_id" not in json.loads(row["payload_json"]) for row in revisions
    )

    reopened = SQLiteCheckpointRepository(repository.path, base_directory=tmp_path)
    assert reopened.schema_version() == 9
    _, reopened_revisions = read_v2_rows(reopened.path)
    assert [json.loads(row["payload_json"]) for row in reopened_revisions] == [
        json.loads(row["payload_json"]) for row in revisions
    ]


def test_v2_constraints_preserve_reference_integrity(tmp_path: Path) -> None:
    repository = legacy_repository(tmp_path)
    legacy = checkpoint(task_scope(), evidence())
    seed_legacy_checkpoint(repository, legacy)
    repository.migrate()
    aggregate, revisions = read_v2_rows(repository.path)

    with sqlite3.connect(repository.path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO checkpoint_revision_records VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(CheckpointRevisionId.new()),
                    aggregate["checkpoint_id"],
                    revisions[0]["revision_number"],
                    None,
                    CheckpointStatus.DRAFT.value,
                    json.dumps(CheckpointContent.from_legacy(legacy).to_dict()),
                    NOW.isoformat(),
                ),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO checkpoint_revision_records VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(CheckpointRevisionId.new()),
                    str(CheckpointId.new()),
                    1,
                    None,
                    CheckpointStatus.DRAFT.value,
                    json.dumps(CheckpointContent.from_legacy(legacy).to_dict()),
                    NOW.isoformat(),
                ),
            )


@pytest.mark.parametrize("failure", ["fork", "broken_predecessor", "cross_scope", "cycle"])
def test_invalid_legacy_chains_rollback_entire_v2_upgrade(tmp_path: Path, failure: str) -> None:
    repository = legacy_repository(tmp_path, f"{failure}.sqlite3")
    original = checkpoint(task_scope(), evidence())
    superseded, replacement = original.revise(CheckpointId.new(), NOW + timedelta(minutes=1))
    seed_legacy_checkpoint(repository, superseded)
    seed_legacy_checkpoint(repository, replacement)

    if failure == "fork":
        _, fork = original.revise(CheckpointId.new(), NOW + timedelta(minutes=2))
        seed_legacy_checkpoint(repository, fork)
    elif failure == "broken_predecessor":
        with sqlite3.connect(repository.path) as connection:
            connection.execute(
                "UPDATE checkpoint_revisions SET supersedes_checkpoint_id = ? "
                "WHERE checkpoint_id = ?",
                (str(CheckpointId.new()), str(replacement.checkpoint_id)),
            )
    elif failure == "cross_scope":
        other_scope = task_scope()
        with sqlite3.connect(repository.path) as connection:
            connection.execute(
                "UPDATE checkpoints SET owner_id = ?, workspace_id = ?, project_id = ?, "
                "session_id = ?, task_id = ? WHERE checkpoint_id = ?",
                (
                    str(other_scope.owner_id),
                    str(other_scope.workspace_id),
                    str(other_scope.project_id),
                    str(other_scope.session_id),
                    str(other_scope.task_id),
                    str(replacement.checkpoint_id),
                ),
            )
    else:
        with sqlite3.connect(repository.path) as connection:
            connection.execute(
                "UPDATE checkpoint_revisions SET supersedes_checkpoint_id = ? "
                "WHERE checkpoint_id = ?",
                (str(replacement.checkpoint_id), str(superseded.checkpoint_id)),
            )

    with pytest.raises(SQLiteMigrationError):
        repository.migrate()
    assert repository.schema_version() == 1
    with sqlite3.connect(repository.path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
                "AND name = 'checkpoint_aggregates'"
            ).fetchone()[0]
            == 0
        )


def test_injected_v2_migration_failure_keeps_legacy_data_and_schema(tmp_path: Path) -> None:
    repository = legacy_repository(tmp_path)
    legacy = checkpoint(task_scope(), evidence())
    seed_legacy_checkpoint(repository, legacy)

    with pytest.raises(SQLiteMigrationError, match="injected"):
        repository.migrate(fail_after_version=2)

    assert repository.schema_version() == 1
    with sqlite3.connect(repository.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
                "AND name = 'checkpoint_aggregates'"
            ).fetchone()[0]
            == 0
        )
