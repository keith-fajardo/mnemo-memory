"""Backend-neutral contracts for immutable dbt manifest snapshots."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from connectors.dbt.manifest import DbtManifestParser, ManifestParseRequest
from packages.domain import (
    DbtSnapshotId,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)
from packages.domain.dbt_manifest import DbtManifestArtifact
from packages.storage import (
    ActiveSnapshotConflict,
    ManifestNodeNotFound,
    ManifestSnapshotNotFound,
    ProjectIndexRepository,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "dbt" / "manifest-v12.json"


def project_scope(seed: int = 1) -> MemoryScope:
    return MemoryScope(
        owner_id=OwnerId.from_string(f"00000000-0000-4000-8000-{seed:012d}"),
        level=ScopeLevel.PROJECT,
        visibility=Visibility.PROJECT,
        workspace_id=WorkspaceId.from_string(f"00000000-0000-4000-8001-{seed:012d}"),
        project_id=ProjectId.from_string(f"00000000-0000-4000-8002-{seed:012d}"),
    )


def artifact(scope: MemoryScope, *, stamp: int = 0) -> DbtManifestArtifact:
    raw = FIXTURE.read_text()
    if stamp:
        raw = raw.replace("customer-stage", f"customer-stage-{stamp}")
    return DbtManifestParser().parse(
        raw,
        ManifestParseRequest(
            scope=scope,
            source_identity="fixtures/dbt/manifest-v12.json",
            ingested_at=datetime(2026, 8, 2, tzinfo=UTC) + timedelta(seconds=stamp),
        ),
    )


def test_snapshot_creation_retrieval_and_adjacency(
    project_index_repository_factory: Callable[[], ProjectIndexRepository],
) -> None:
    repository: ProjectIndexRepository = project_index_repository_factory()
    scope = project_scope()
    graph = artifact(scope)
    result = repository.store_and_activate(graph, DbtSnapshotId.new())
    assert result.idempotent is False
    assert result.snapshot.is_active is True
    assert result.snapshot.node_count == len(graph.nodes)
    assert result.snapshot.edge_count == len(graph.edges)
    assert repository.get_active_snapshot(scope) == result.snapshot
    node = repository.get_node(scope, result.snapshot.snapshot_id, graph.nodes[-1].unique_id)
    assert node.unique_id == graph.nodes[-1].unique_id
    assert len(repository.iter_nodes(scope, result.snapshot.snapshot_id)) == len(graph.nodes)
    assert len(repository.iter_edges(scope, result.snapshot.snapshot_id)) == len(graph.edges)
    child = next(item for item in graph.nodes if item.dependency_ids)
    assert repository.direct_upstream(scope, result.snapshot.snapshot_id, child.unique_id)


def test_digest_idempotency_replacement_scope_and_conflict(
    project_index_repository_factory: Callable[[], ProjectIndexRepository],
) -> None:
    repository: ProjectIndexRepository = project_index_repository_factory()
    scope = project_scope()
    first = repository.store_and_activate(artifact(scope), DbtSnapshotId.new())
    same = repository.store_and_activate(
        artifact(scope), DbtSnapshotId.new(), expected_active_snapshot_id=first.snapshot.snapshot_id
    )
    assert same.idempotent is True
    assert same.snapshot.snapshot_id == first.snapshot.snapshot_id
    changed = repository.store_and_activate(
        artifact(scope, stamp=1),
        DbtSnapshotId.new(),
        expected_active_snapshot_id=first.snapshot.snapshot_id,
    )
    assert changed.snapshot.snapshot_id != first.snapshot.snapshot_id
    assert repository.get_snapshot(scope, first.snapshot.snapshot_id).is_active is False
    with pytest.raises(ActiveSnapshotConflict):
        repository.store_and_activate(
            artifact(scope, stamp=2),
            DbtSnapshotId.new(),
            expected_active_snapshot_id=first.snapshot.snapshot_id,
        )
    isolated = project_scope(2)
    assert (
        repository.store_and_activate(artifact(isolated), DbtSnapshotId.new()).idempotent is False
    )
    with pytest.raises(ManifestSnapshotNotFound):
        repository.get_snapshot(isolated, changed.snapshot.snapshot_id)
    with pytest.raises(ManifestNodeNotFound):
        repository.get_node(
            isolated, first.snapshot.snapshot_id, artifact(scope).nodes[0].unique_id
        )


def test_snapshot_listing_is_deterministic_and_paginated(
    project_index_repository_factory: Callable[[], ProjectIndexRepository],
) -> None:
    repository: ProjectIndexRepository = project_index_repository_factory()
    scope = project_scope()
    first = repository.store_and_activate(artifact(scope), DbtSnapshotId.new())
    second = repository.store_and_activate(
        artifact(scope, stamp=1),
        DbtSnapshotId.new(),
        expected_active_snapshot_id=first.snapshot.snapshot_id,
    )
    page = repository.list_snapshots(scope, limit=1)
    assert page.items == (second.snapshot,)
    assert page.next_offset == 1
    assert (
        repository.list_snapshots(scope, offset=1, limit=1).items[0].snapshot_id
        == first.snapshot.snapshot_id
    )
