from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.domain import (
    CheckpointAggregate,
    CheckpointContent,
    CheckpointId,
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
from packages.storage import SQLiteCheckpointRepository
from packages.storage.contracts import (
    CheckpointNotFound,
    RepositoryStorageFailure,
    RevisionConflict,
)

NOW = datetime(2026, 8, 2, 11, 0, tzinfo=UTC)
HASH = "sha256:" + "f" * 64


def scope() -> MemoryScope:
    return MemoryScope(
        owner_id=OwnerId.new(),
        level=ScopeLevel.TASK,
        visibility=Visibility.PROJECT,
        workspace_id=WorkspaceId.new(),
        project_id=ProjectId.new(),
        session_id=SessionId.new(),
        task_id=TaskId.new(),
    )


def content(*, suffix: str = "one") -> CheckpointContent:
    return CheckpointContent(
        task_objective="exercise SQLite lifecycle",
        completed_work=(f"completed-{suffix}",),
        current_state="active",
        remaining_work=(f"next-{suffix}",),
        decisions=(f"decision-{suffix}",),
        failures=(),
        blockers=(),
        relevant_files=("packages/storage/sqlite.py",),
        relevant_artifacts=(),
        verification_performed=("pytest",),
        token_estimate=9,
    )


def evidence() -> EvidenceReference:
    return EvidenceReference(
        evidence_id=EvidenceId.new(),
        source_id=SourceId.new(),
        source_type=EvidenceSourceType.CHECKPOINT,
        trust_class=SourceTrustClass.USER_AUTHORED,
        immutable_source_ref="synthetic://sqlite-lifecycle",
        content_hash=HASH,
        location=EvidenceLocation("fixture://sqlite-lifecycle"),
        observed_at=NOW,
        verification_status=VerificationStatus.VERIFIED,
    )


def initial(scope_value: MemoryScope) -> tuple[CheckpointAggregate, CheckpointRevision]:
    checkpoint_id = CheckpointId.new()
    revision = CheckpointRevision(
        revision_id=CheckpointRevisionId.new(),
        checkpoint_id=checkpoint_id,
        revision_number=1,
        predecessor_revision_id=None,
        scope=scope_value,
        content=content(),
        status=CheckpointStatus.ACTIVE,
        evidence_references=(evidence(),),
        created_at=NOW,
    )
    return (
        CheckpointAggregate(
            checkpoint_id=checkpoint_id,
            scope=scope_value,
            current_revision_id=revision.revision_id,
            current_revision_number=1,
            lifecycle_status=CheckpointStatus.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        ),
        revision,
    )


def repository(tmp_path: Path, name: str = "lifecycle.sqlite3") -> SQLiteCheckpointRepository:
    result = SQLiteCheckpointRepository(tmp_path / name, base_directory=tmp_path)
    result.migrate()
    return result


def stored(
    tmp_path: Path,
) -> tuple[SQLiteCheckpointRepository, MemoryScope, CheckpointAggregate, CheckpointRevision]:
    result = repository(tmp_path)
    scope_value = scope()
    aggregate, revision = initial(scope_value)
    result.create_checkpoint_aggregate(aggregate, revision)
    return result, scope_value, aggregate, revision


def test_sqlite_canonical_payload_and_restart_persistence(tmp_path: Path) -> None:
    result, scope_value, aggregate, initial_revision = stored(tmp_path)
    revised = result.append_revision(
        scope_value,
        aggregate.checkpoint_id,
        initial_revision.revision_id,
        content(suffix="two"),
        (evidence(),),
        NOW + timedelta(minutes=1),
    )
    with sqlite3.connect(result.path) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM checkpoint_revision_records "
                "WHERE checkpoint_revision_id = ?",
                (str(revised.revision_id),),
            ).fetchone()[0]
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert payload == revised.content.to_dict()
    assert not {
        "checkpoint_id",
        "checkpoint_revision_id",
        "revision_number",
        "predecessor_revision_id",
        "supersedes_checkpoint_id",
        "scope",
    }.intersection(payload)
    reopened = SQLiteCheckpointRepository(result.path, base_directory=tmp_path)
    assert reopened.get_current_revision(scope_value, aggregate.checkpoint_id) == revised
    assert (
        reopened.get_revision(scope_value, aggregate.checkpoint_id, revision_number=1)
        == initial_revision
    )


def test_sqlite_terminal_state_and_scope_remain_after_reopen(tmp_path: Path) -> None:
    result, scope_value, aggregate, initial_revision = stored(tmp_path)
    completed_content = replace(
        content(suffix="complete"), current_state="complete", remaining_work=()
    )
    completed = result.complete_checkpoint(
        scope_value,
        aggregate.checkpoint_id,
        initial_revision.revision_id,
        completed_content,
        (evidence(),),
        NOW + timedelta(minutes=1),
    )
    reopened = SQLiteCheckpointRepository(result.path, base_directory=tmp_path)
    assert reopened.get_current_revision(scope_value, aggregate.checkpoint_id) == completed
    assert (
        reopened.get_aggregate(scope_value, aggregate.checkpoint_id).lifecycle_status
        is CheckpointStatus.COMPLETED
    )
    with pytest.raises(CheckpointNotFound):
        reopened.get_current_revision(
            replace(scope_value, visibility=Visibility.OWNER), aggregate.checkpoint_id
        )


class FailingRevisionInsertRepository(SQLiteCheckpointRepository):
    def _insert_canonical_revision(
        self, connection: sqlite3.Connection, revision: CheckpointRevision
    ) -> None:
        if revision.revision_number > 1:
            raise sqlite3.OperationalError("injected revision insertion failure")
        super()._insert_canonical_revision(connection, revision)


class FailingCreateRepository(SQLiteCheckpointRepository):
    def _insert_canonical_revision(
        self, connection: sqlite3.Connection, revision: CheckpointRevision
    ) -> None:
        raise sqlite3.OperationalError("injected initial revision failure")


class FailingEvidenceRepository(SQLiteCheckpointRepository):
    def _insert_canonical_revision(
        self, connection: sqlite3.Connection, revision: CheckpointRevision
    ) -> None:
        super()._insert_canonical_revision(connection, revision)
        if revision.revision_number > 1:
            raise sqlite3.OperationalError("injected evidence insertion failure")


class FailingPointerRepository(SQLiteCheckpointRepository):
    def _advance_current_pointer(
        self,
        connection: sqlite3.Connection,
        scope_value: MemoryScope,
        checkpoint_id: CheckpointId,
        expected_revision_id: CheckpointRevisionId,
        revision: CheckpointRevision,
    ) -> int:
        return 0


def test_sqlite_initial_creation_rolls_back_aggregate_on_revision_failure(tmp_path: Path) -> None:
    path = tmp_path / "failed-create.sqlite3"
    result = FailingCreateRepository(path, base_directory=tmp_path)
    result.migrate()
    aggregate, initial_revision = initial(scope())
    with pytest.raises(RepositoryStorageFailure):
        result.create_checkpoint_aggregate(aggregate, initial_revision)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM checkpoint_aggregates").fetchone()[0] == 0
        assert (
            connection.execute("SELECT COUNT(*) FROM checkpoint_revision_records").fetchone()[0]
            == 0
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM checkpoint_revision_evidence").fetchone()[0]
            == 0
        )


@pytest.mark.parametrize(
    "repository_type, expected_error",
    [
        (FailingRevisionInsertRepository, RepositoryStorageFailure),
        (FailingEvidenceRepository, RepositoryStorageFailure),
        (FailingPointerRepository, RevisionConflict),
    ],
)
def test_sqlite_failed_mutations_rollback_all_partial_rows(
    tmp_path: Path,
    repository_type: type[SQLiteCheckpointRepository],
    expected_error: type[Exception],
) -> None:
    path = tmp_path / f"{repository_type.__name__}.sqlite3"
    result = repository_type(path, base_directory=tmp_path)
    result.migrate()
    scope_value = scope()
    aggregate, initial_revision = initial(scope_value)
    result.create_checkpoint_aggregate(aggregate, initial_revision)
    with pytest.raises(expected_error):
        result.append_revision(
            scope_value,
            aggregate.checkpoint_id,
            initial_revision.revision_id,
            content(suffix="failed"),
            (evidence(),),
            NOW + timedelta(minutes=1),
        )
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM checkpoint_revision_records").fetchone()[0]
            == 1
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM checkpoint_revision_evidence").fetchone()[0]
            == 1
        )
        assert connection.execute(
            "SELECT current_revision_id FROM checkpoint_aggregates WHERE checkpoint_id = ?",
            (str(aggregate.checkpoint_id),),
        ).fetchone()[0] == str(initial_revision.revision_id)


def test_two_independent_sqlite_writers_leave_one_contiguous_current_revision(
    tmp_path: Path,
) -> None:
    first, scope_value, aggregate, initial_revision = stored(tmp_path)
    second = SQLiteCheckpointRepository(first.path, base_directory=tmp_path)
    start = threading.Barrier(2)

    def append(
        repository_instance: SQLiteCheckpointRepository, suffix: str
    ) -> CheckpointRevision | Exception:
        start.wait(timeout=2)
        try:
            return repository_instance.append_revision(
                scope_value,
                aggregate.checkpoint_id,
                initial_revision.revision_id,
                content(suffix=suffix),
                (evidence(),),
                NOW + timedelta(minutes=1),
            )
        except Exception as error:  # Typed outcome asserted after synchronization.
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda item: append(*item),
                ((first, "writer-one"), (second, "writer-two")),
                timeout=5,
            )
        )
    successes = [item for item in outcomes if isinstance(item, CheckpointRevision)]
    failures = [item for item in outcomes if isinstance(item, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], RevisionConflict)
    reopened = SQLiteCheckpointRepository(first.path, base_directory=tmp_path)
    current = reopened.get_current_revision(scope_value, aggregate.checkpoint_id)
    assert current == successes[0]
    assert [
        reopened.get_revision(
            scope_value, aggregate.checkpoint_id, revision_number=number
        ).revision_number
        for number in (1, 2)
    ] == [1, 2]
    with sqlite3.connect(first.path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM checkpoint_revision_records WHERE checkpoint_id = ?",
                (str(aggregate.checkpoint_id),),
            ).fetchone()[0]
            == 2
        )
