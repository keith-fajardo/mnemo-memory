"""Storage-independent application coverage for persisted dbt lineage."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mnemo_memory.connectors.dbt.artifacts import DbtCatalogParser, DbtRunResultsParser
from mnemo_memory.connectors.dbt.manifest import DbtManifestParser
from mnemo_memory.packages.application.checkpoints import CheckpointApplicationService
from mnemo_memory.packages.application.dbt import (
    DbtApplicationAmbiguous,
    DbtApplicationConflict,
    DbtApplicationInvalidManifest,
    DbtApplicationNotFound,
    DbtManifestApplicationService,
    GetActiveManifestStatus,
    GetDbtSupplementalArtifacts,
    IngestCatalog,
    IngestManifest,
    IngestRunResults,
    LineageDirection,
    QueryLineage,
    ResolveManifestFile,
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
CATALOG_FIXTURE = Path(__file__).parents[1] / "fixtures" / "dbt" / "catalog-v1.json"
RUN_RESULTS_FIXTURE = Path(__file__).parents[1] / "fixtures" / "dbt" / "run-results-v6.json"
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
    return DbtManifestApplicationService(
        ReferenceProjectIndexRepository(),
        DbtManifestParser(),
        DbtCatalogParser(),
        DbtRunResultsParser(),
    )


def test_supplemental_ingestion_is_exact_scoped_and_idempotent() -> None:
    item, value = service(), scope()
    snapshot = item.ingest(command(value)).snapshot
    catalog = IngestCatalog(
        value,
        snapshot.snapshot_id,
        CATALOG_FIXTURE.read_bytes(),
        "catalog.json",
        STAMP,
    )
    runs = IngestRunResults(
        value,
        snapshot.snapshot_id,
        RUN_RESULTS_FIXTURE.read_bytes(),
        "run_results.json",
        STAMP,
    )

    assert item.ingest_catalog(catalog).idempotent is False
    assert item.ingest_catalog(catalog).idempotent is True
    assert item.ingest_run_results(runs).idempotent is False
    saved = item.get_supplemental(GetDbtSupplementalArtifacts(value, snapshot.snapshot_id))
    assert saved.catalog is not None and len(saved.catalog.relations) == 2
    assert saved.run_results is not None and len(saved.run_results.results) == 2
    with pytest.raises(DbtApplicationNotFound):
        item.get_supplemental(GetDbtSupplementalArtifacts(scope(2), snapshot.snapshot_id))


def test_supplemental_ingestion_rejects_invalid_or_mismatched_artifacts() -> None:
    item, value = service(), scope()
    snapshot = item.ingest(command(value)).snapshot
    with pytest.raises(DbtApplicationInvalidManifest):
        item.ingest_catalog(
            IngestCatalog(value, snapshot.snapshot_id, b"not json", "catalog.json", STAMP)
        )
    unknown = CATALOG_FIXTURE.read_text().replace(
        "model.mnemo_analytics.fct_orders", "model.mnemo_analytics.unknown_model"
    )
    with pytest.raises(DbtApplicationConflict):
        item.ingest_catalog(
            IngestCatalog(value, snapshot.snapshot_id, unknown, "catalog.json", STAMP)
        )


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


def test_read_only_lineage_service_does_not_need_an_ingestion_parser() -> None:
    repository = ReferenceProjectIndexRepository()
    writer = DbtManifestApplicationService(repository, DbtManifestParser())
    value = scope()
    writer.ingest(command(value))
    reader = DbtManifestApplicationService(repository)

    result = reader.query(
        QueryLineage(
            value,
            DbtNodeId("model.mnemo_analytics.fct_orders"),
            LineageDirection.DOWNSTREAM,
        )
    )

    assert result.start_node.unique_id == DbtNodeId("model.mnemo_analytics.fct_orders")
    with pytest.raises(DbtApplicationInvalidManifest):
        reader.ingest(command(value))


def test_manifest_file_resolution_is_exact_scoped_and_refuses_ambiguity() -> None:
    item, value = service(), scope()
    stored = item.ingest(command(value))

    resolved = item.resolve_file(
        ResolveManifestFile(value, "models/marts/fct_orders.sql", stored.snapshot.snapshot_id)
    )
    assert resolved.snapshot.snapshot_id == stored.snapshot.snapshot_id
    assert str(resolved.node.unique_id) == "model.mnemo_analytics.fct_orders"
    with pytest.raises(DbtApplicationAmbiguous):
        item.resolve_file(ResolveManifestFile(value, "models/sources.yml"))
    with pytest.raises(DbtApplicationNotFound):
        item.resolve_file(ResolveManifestFile(scope(2), "models/marts/fct_orders.sql"))
    with pytest.raises(ValueError, match="canonical relative path"):
        ResolveManifestFile(value, "../models/marts/fct_orders.sql")


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
    consumers = item.query(QueryLineage(value, start, LineageDirection.DOWNSTREAM))
    assert {str(node.node.unique_id) for node in consumers.nodes} == {
        "exposure.mnemo_analytics.order_dashboard",
        "metric.mnemo_analytics.customer_value",
        "semantic_model.mnemo_analytics.customer_value",
    }
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

    consumers = UnifiedContextService(
        CheckpointApplicationService(ReferenceCheckpointRepository(), clock=lambda: STAMP), item
    ).get_context(
        GetUnifiedContext(
            task_scope,
            lineage=ContextLineageQuery(
                DbtNodeId("model.mnemo_analytics.mart_customer_value"),
                LineageDirection.DOWNSTREAM,
            ),
        )
    )
    assert {
        json.loads(structural_item.content)["resource_type"]
        for structural_item in consumers.structural_items
    } == {"exposure", "metric", "semantic_model"}
    assert all(
        structural_item.evidence_references for structural_item in consumers.structural_items
    )


def test_task_context_includes_bounded_matching_supplemental_dbt_evidence() -> None:
    item, project_scope = service(), scope()
    snapshot = item.ingest(command(project_scope)).snapshot
    catalog_value = json.loads(CATALOG_FIXTURE.read_text())
    catalog_value["nodes"]["model.mnemo_analytics.fct_orders"]["columns"] = {
        f"column_{index}": {
            "type": "TEXT",
            "index": index,
            "name": f"column_{index}",
            "comment": "secret-that-must-not-be-retained",
        }
        for index in range(15)
    }
    item.ingest_catalog(
        IngestCatalog(
            project_scope,
            snapshot.snapshot_id,
            json.dumps(catalog_value),
            "catalog.json",
            STAMP,
        )
    )
    item.ingest_run_results(
        IngestRunResults(
            project_scope,
            snapshot.snapshot_id,
            RUN_RESULTS_FIXTURE.read_bytes(),
            "run_results.json",
            STAMP,
        )
    )
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
        CheckpointApplicationService(ReferenceCheckpointRepository(), clock=lambda: STAMP), item
    ).get_context(
        GetUnifiedContext(
            task_scope,
            lineage=ContextLineageQuery(
                DbtNodeId("model.mnemo_analytics.mart_customer_value"),
                LineageDirection.UPSTREAM,
            ),
        )
    )

    fact = next(
        fact
        for fact in packet.structural_items
        if '"node_unique_id":"model.mnemo_analytics.fct_orders"' in fact.content
    )
    content = json.loads(fact.content)
    assert content["catalog"]["column_count"] == 15
    assert len(content["catalog"]["columns"]) == 12
    assert content["catalog"]["columns_omitted"] == 3
    assert '"status":"success"' in fact.content
    assert len(fact.evidence_references) == 3
    assert "secret-that-must-not-be-retained" not in fact.content
    assert "compiled_code" not in fact.content
    notice = next(notice for notice in packet.provenance if notice.item_id == fact.item_id)
    assert notice.evidence_references == fact.evidence_references


def test_task_context_can_resolve_an_unambiguous_dbt_file_to_authoritative_lineage() -> None:
    item, project_scope = service(), scope()
    stored = item.ingest(command(project_scope)).snapshot
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
                None,
                LineageDirection.DOWNSTREAM,
                relative_path="models/marts/fct_orders.sql",
                current_content_digest=stored.metadata.content_digest,
            ),
        )
    )

    assert packet.structural_items
    assert all(
        '"start_node":"model.mnemo_analytics.fct_orders"' in item.content
        for item in packet.structural_items
    )
    assert all(item.validity.value == "current" for item in packet.structural_items)


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
