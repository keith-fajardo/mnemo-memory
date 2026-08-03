"""Storage-independent application coverage for persisted dbt lineage."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from mnemo_memory.connectors.dbt.manifest import DbtManifestParser
from mnemo_memory.packages.application.checkpoints import CheckpointApplicationService
from mnemo_memory.packages.application.dbt import (
    DbtApplicationConflict,
    DbtApplicationInvalidManifest,
    DbtApplicationNotFound,
    DbtManifestApplicationService,
    GetActiveManifestStatus,
    IngestManifest,
    LineageDirection,
    QueryLineage,
)
from mnemo_memory.packages.application.unified_context import (
    ContextLineageQuery,
    GetUnifiedContext,
    UnifiedContextService,
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
from mnemo_memory.packages.domain.dbt_manifest import (
    ArtifactCurrentness,
    DbtNodeId,
    SourceStateFingerprint,
)
from mnemo_memory.packages.domain.identifiers import DbtSnapshotId
from mnemo_memory.packages.storage import (
    ReferenceCheckpointRepository,
    ReferenceProjectIndexRepository,
)
from mnemo_memory.packages.storage.sqlite import SQLiteCheckpointRepository

FIXTURE = Path(__file__).parents[1] / "fixtures" / "dbt" / "manifest-v12.json"
STAMP = datetime(2026, 8, 2, tzinfo=UTC)


def scope(seed: int = 1) -> MemoryScope:
    return MemoryScope(
        owner_id=OwnerId.from_string(f"00000000-0000-4000-8000-{seed:012d}"),
        level=ScopeLevel.PROJECT,
        visibility=Visibility.PROJECT,
        workspace_id=WorkspaceId.from_string(f"00000000-0000-4000-8001-{seed:012d}"),
        project_id=ProjectId.from_string(f"00000000-0000-4000-8002-{seed:012d}"),
    )


def command(
    value: MemoryScope,
    raw: str | None = None,
    *,
    expected_active_snapshot_id: DbtSnapshotId | None = None,
    source_state: SourceStateFingerprint | None = None,
) -> IngestManifest:
    return IngestManifest(
        scope=value,
        raw_manifest=FIXTURE.read_text() if raw is None else raw,
        source_identity="fixtures/dbt/manifest-v12.json",
        ingested_at=STAMP,
        expected_active_snapshot_id=expected_active_snapshot_id,
        source_state=source_state,
    )


def service() -> DbtManifestApplicationService:
    return DbtManifestApplicationService(ReferenceProjectIndexRepository(), DbtManifestParser())


def test_ingestion_is_idempotent_replaces_active_and_preserves_prior_snapshot() -> None:
    item, value = service(), scope()
    first = item.ingest(command(value))
    same = item.ingest(command(value, expected_active_snapshot_id=first.snapshot.snapshot_id))
    assert same.idempotent and same.snapshot.snapshot_id == first.snapshot.snapshot_id
    changed = item.ingest(
        command(
            value,
            FIXTURE.read_text().replace("customer-stage", "customer-stage-v2"),
            expected_active_snapshot_id=first.snapshot.snapshot_id,
        )
    )
    assert not changed.idempotent
    assert changed.snapshot.snapshot_id != first.snapshot.snapshot_id
    with pytest.raises(DbtApplicationConflict):
        item.ingest(command(value, expected_active_snapshot_id=first.snapshot.snapshot_id))


def test_parser_failure_never_creates_snapshot() -> None:
    item = service()
    with pytest.raises(DbtApplicationInvalidManifest):
        item.ingest(command(scope(), "not JSON"))
    assert item.get_active_status(GetActiveManifestStatus(scope())).snapshot is None


def test_queries_are_bounded_deterministic_and_scope_safe() -> None:
    item, value = service(), scope()
    stored = item.ingest(command(value)).snapshot
    start = DbtNodeId("model.mnemo_analytics.mart_customer_value")
    upstream = item.query(QueryLineage(value, start, LineageDirection.UPSTREAM))
    assert [node.depth for node in upstream.nodes] == sorted(node.depth for node in upstream.nodes)
    assert {node.depth for node in upstream.nodes} >= {1, 2}
    assert all(node.node.evidence for node in upstream.nodes)
    assert all(edge.evidence for edge in upstream.edges)
    direct = item.query(QueryLineage(value, start, LineageDirection.UPSTREAM, transitive=False))
    assert {node.depth for node in direct.nodes} == {1}
    zero = item.query(QueryLineage(value, start, LineageDirection.UPSTREAM, maximum_depth=0))
    assert zero.nodes == () and zero.truncated
    bounded = item.query(QueryLineage(value, start, LineageDirection.UPSTREAM, maximum_nodes=1))
    assert bounded.truncated and len(bounded.nodes) == 1
    assert upstream.snapshot.snapshot_id == stored.snapshot_id
    with pytest.raises(DbtApplicationNotFound):
        item.query(
            QueryLineage(scope(2), start, LineageDirection.UPSTREAM, snapshot_id=stored.snapshot_id)
        )


def test_task_context_uses_its_project_scope_for_dbt_lineage() -> None:
    """One task request can combine task memory with project-scoped structural evidence."""
    item, project_scope = service(), scope()
    item.ingest(command(project_scope))
    task_scope = MemoryScope(
        project_scope.owner_id,
        ScopeLevel.TASK,
        project_scope.visibility,
        project_scope.workspace_id,
        project_scope.project_id,
        session_id=SessionId.new(),
        task_id=TaskId.new(),
    )
    packet = UnifiedContextService(
        CheckpointApplicationService(
            ReferenceCheckpointRepository(), clock=lambda: datetime(2026, 8, 2, tzinfo=UTC)
        ),
        item,
    ).get_context(
        GetUnifiedContext(
            task_scope,
            lineage=ContextLineageQuery(
                DbtNodeId("model.mnemo_analytics.mart_customer_value"),
                LineageDirection.UPSTREAM,
            ),
        )
    )

    assert packet.structural_items
    assert all(
        structural_item.source_scope == task_scope for structural_item in packet.structural_items
    )


def test_currentness_uses_exact_digest_or_comparable_source_state() -> None:
    item, value = service(), scope()
    stored = item.ingest(
        command(
            value,
            source_state=SourceStateFingerprint(git_commit="abc", dirty=False),
        )
    ).snapshot
    start = DbtNodeId("model.mnemo_analytics.fct_orders")
    matching = item.query(
        QueryLineage(
            value,
            start,
            LineageDirection.UPSTREAM,
            current_content_digest=stored.metadata.content_digest,
        )
    )
    assert matching.currentness is ArtifactCurrentness.CURRENT
    stale = item.query(
        QueryLineage(value, start, LineageDirection.UPSTREAM, current_content_digest="0" * 64)
    )
    assert stale.currentness is ArtifactCurrentness.STALE
    git_match = item.get_active_status(
        GetActiveManifestStatus(
            value, current_source_state=SourceStateFingerprint(git_commit="abc", dirty=False)
        )
    )
    assert git_match.currentness is ArtifactCurrentness.CURRENT
    assert (
        item.get_active_status(GetActiveManifestStatus(value)).currentness
        is ArtifactCurrentness.UNKNOWN
    )


def test_reference_and_sqlite_return_the_same_normalized_lineage(tmp_path: Path) -> None:
    value = scope()
    reference = service()
    sqlite = SQLiteCheckpointRepository(tmp_path / "index.sqlite3", base_directory=tmp_path)
    sqlite.migrate()
    persisted = DbtManifestApplicationService(sqlite, DbtManifestParser())
    reference.ingest(command(value))
    persisted.ingest(command(value))
    request = QueryLineage(
        value,
        DbtNodeId("model.mnemo_analytics.mart_customer_value"),
        LineageDirection.UPSTREAM,
    )
    assert [(str(item.node.unique_id), item.depth) for item in reference.query(request).nodes] == [
        (str(item.node.unique_id), item.depth) for item in persisted.query(request).nodes
    ]
