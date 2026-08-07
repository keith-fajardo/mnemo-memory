"""Storage-independent application coverage for persisted dbt lineage."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mnemo_memory.connectors.dbt.artifacts import (
    DbtCatalogParser,
    DbtRunResultsParser,
    DbtSourceFreshnessParser,
)
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
    IngestSourceFreshness,
    LineageDirection,
    QueryLineage,
    QueryManifestChanges,
    QueryManifestSelector,
    QuerySourceFreshness,
    QueryTestCoverage,
    ResolveManifestFile,
)
from mnemo_memory.packages.application.unified_context import (
    ContextDbtChangesQuery,
    ContextDbtFreshnessQuery,
    ContextDbtTestCoverageQuery,
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
SOURCES_FIXTURE = Path(__file__).parents[1] / "fixtures" / "dbt" / "sources-v3.json"
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
        DbtSourceFreshnessParser(),
    )


def manifest_with_added_modified_and_removed_nodes() -> str:
    value = json.loads(FIXTURE.read_text())
    nodes = value["nodes"]
    parent_map = value["parent_map"]
    child_map = value["child_map"]
    nodes["model.mnemo_analytics.fct_orders"]["checksum"]["checksum"] = "fact-orders-v2"
    removed = "test.mnemo_analytics.unique_fct_orders"
    del nodes[removed]
    del parent_map[removed]
    del child_map[removed]
    child_map["model.mnemo_analytics.fct_orders"].remove(removed)
    added = "model.mnemo_analytics.new_rollup"
    new_node = dict(nodes["model.date_utils.dim_calendar"])
    new_node.update(
        {
            "unique_id": added,
            "package_name": "mnemo_analytics",
            "name": "new_rollup",
            "alias": "new_rollup",
            "original_file_path": "models/marts/new_rollup.sql",
            "checksum": {"checksum": "new-rollup"},
        }
    )
    nodes[added] = new_node
    parent_map[added] = []
    child_map[added] = []
    return json.dumps(value)


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


def test_manifest_changes_return_modified_node_and_authoritative_downstream_scope() -> None:
    item, value = service(), scope()
    state = SourceStateFingerprint(
        git_commit="a" * 40,
        working_tree_fingerprint="sha256:" + "b" * 64,
        dirty=False,
    )
    first = item.ingest(command(value, source_state=state)).snapshot
    changed_raw = manifest_with_added_modified_and_removed_nodes()
    second = item.ingest(
        command(
            value,
            changed_raw,
            expected_active_snapshot_id=first.snapshot_id,
            source_state=state,
        )
    ).snapshot

    result = item.query_changes(QueryManifestChanges(value, current_source_state=state))

    assert result.before_snapshot.snapshot_id == first.snapshot_id
    assert result.after_snapshot.snapshot_id == second.snapshot_id
    assert [(change.kind.value, str(change.unique_id)) for change in result.changes] == [
        ("modified", "model.mnemo_analytics.fct_orders"),
        ("added", "model.mnemo_analytics.new_rollup"),
        ("removed", "test.mnemo_analytics.unique_fct_orders"),
    ]
    assert {str(node.unique_id) for node in result.affected_nodes} == {
        "exposure.mnemo_analytics.order_dashboard",
        "metric.mnemo_analytics.customer_value",
        "model.mnemo_analytics.fct_orders",
        "model.mnemo_analytics.mart_customer_value",
        "model.mnemo_analytics.new_rollup",
        "semantic_model.mnemo_analytics.customer_value",
    }
    assert result.currentness is ArtifactCurrentness.CURRENT

    checkpoints = CheckpointApplicationService(ReferenceCheckpointRepository(), clock=lambda: STAMP)
    task_value = MemoryScope(
        value.owner_id,
        ScopeLevel.TASK,
        value.visibility,
        value.workspace_id,
        value.project_id,
        SessionId.new(),
        TaskId.new(),
    )
    packet = UnifiedContextService(checkpoints, item).get_context(
        GetUnifiedContext(
            task_value,
            dbt_changes=ContextDbtChangesQuery(current_source_state=state),
        )
    )
    content = json.loads(packet.structural_items[0].content)
    assert content["query_kind"] == "changes"
    assert content["currentness"] == "current"
    assert content["changes"] == [
        {
            "kind": "modified",
            "relative_file": "models/marts/fct_orders.sql",
            "resource_type": "model",
            "unique_id": "model.mnemo_analytics.fct_orders",
        },
        {
            "kind": "added",
            "relative_file": "models/marts/new_rollup.sql",
            "resource_type": "model",
            "unique_id": "model.mnemo_analytics.new_rollup",
        },
        {
            "kind": "removed",
            "relative_file": "tests/unique_fct_orders.sql",
            "resource_type": "test",
            "unique_id": "test.mnemo_analytics.unique_fct_orders",
        },
    ]

    stale = UnifiedContextService(checkpoints, item).get_context(
        GetUnifiedContext(
            task_value,
            dbt_changes=ContextDbtChangesQuery(
                current_source_state=SourceStateFingerprint(
                    working_tree_fingerprint="sha256:" + "c" * 64,
                    dirty=True,
                ),
                require_current=True,
            ),
        )
    )
    assert stale.structural_items == ()
    assert stale.omissions[0].reason.value == "stale"
    with pytest.raises(DbtApplicationNotFound):
        item.query_changes(
            QueryManifestChanges(
                scope(2),
                before_snapshot_id=first.snapshot_id,
                after_snapshot_id=second.snapshot_id,
            )
        )

    no_history, isolated_scope = service(), scope(3)
    no_history.ingest(command(isolated_scope))
    with pytest.raises(DbtApplicationNotFound):
        no_history.query_changes(QueryManifestChanges(isolated_scope))


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


def test_directed_path_query_returns_one_stable_shortest_evidenced_path() -> None:
    item, value = service(), scope()
    snapshot = item.ingest(command(value)).snapshot
    query = QueryLineage(
        value,
        DbtNodeId("source.mnemo_analytics.raw_orders"),
        LineageDirection.DOWNSTREAM,
        snapshot_id=snapshot.snapshot_id,
        destination_unique_id=DbtNodeId("model.mnemo_analytics.mart_customer_value"),
    )

    result = item.query(query)

    assert result.path_found is True
    assert result.destination_node is not None
    assert [str(node.node.unique_id) for node in result.nodes] == [
        "model.mnemo_analytics.stg_orders",
        "model.mnemo_analytics.int_customer_orders",
        "model.mnemo_analytics.fct_orders",
        "model.mnemo_analytics.mart_customer_value",
    ]
    assert [node.depth for node in result.nodes] == [1, 2, 3, 4]
    assert len(result.edges) == 4
    assert all(edge.evidence for edge in result.edges)
    assert item.query(query).nodes == result.nodes

    absent = item.query(
        QueryLineage(
            value,
            DbtNodeId("model.mnemo_analytics.mart_customer_value"),
            LineageDirection.DOWNSTREAM,
            destination_unique_id=DbtNodeId("source.mnemo_analytics.raw_orders"),
        )
    )
    assert absent.path_found is False
    assert absent.nodes == ()
    assert absent.truncation_reason == "no directed path"

    bounded = item.query(
        QueryLineage(
            value,
            DbtNodeId("source.mnemo_analytics.raw_orders"),
            LineageDirection.DOWNSTREAM,
            maximum_depth=2,
            destination_unique_id=DbtNodeId("model.mnemo_analytics.mart_customer_value"),
        )
    )
    assert bounded.path_found is False and bounded.truncated
    with pytest.raises(DbtApplicationNotFound):
        item.query(
            QueryLineage(
                scope(2),
                DbtNodeId("source.mnemo_analytics.raw_orders"),
                LineageDirection.DOWNSTREAM,
                snapshot_id=snapshot.snapshot_id,
                destination_unique_id=DbtNodeId("model.mnemo_analytics.mart_customer_value"),
            )
        )


def test_direct_test_coverage_is_bounded_evidenced_and_scope_safe() -> None:
    item, value = service(), scope()
    snapshot = item.ingest(command(value)).snapshot

    result = item.query_test_coverage(
        QueryTestCoverage(
            value,
            DbtNodeId("model.mnemo_analytics.fct_orders"),
            snapshot_id=snapshot.snapshot_id,
        )
    )

    assert [str(node.unique_id) for node in result.test_nodes] == [
        "test.mnemo_analytics.unique_fct_orders"
    ]
    assert len(result.edges) == 1
    assert result.edges[0].evidence
    assert result.truncated is False

    expanded = json.loads(FIXTURE.read_text())
    second_test = json.loads(
        json.dumps(expanded["nodes"]["test.mnemo_analytics.unique_fct_orders"])
    )
    second_test["unique_id"] = "test.mnemo_analytics.z_fct_orders"
    second_test["name"] = "z_fct_orders"
    second_test["alias"] = "z_fct_orders"
    expanded["nodes"][second_test["unique_id"]] = second_test
    expanded["parent_map"][second_test["unique_id"]] = ["model.mnemo_analytics.fct_orders"]
    expanded["child_map"][second_test["unique_id"]] = []
    expanded["child_map"]["model.mnemo_analytics.fct_orders"].append(second_test["unique_id"])
    bounded_item = service()
    bounded_item.ingest(command(value, json.dumps(expanded)))
    bounded = bounded_item.query_test_coverage(
        QueryTestCoverage(
            value,
            DbtNodeId("model.mnemo_analytics.fct_orders"),
            maximum_tests=1,
        )
    )
    assert len(bounded.test_nodes) == 1 and bounded.truncated

    uncovered = item.query_test_coverage(
        QueryTestCoverage(value, DbtNodeId("model.mnemo_analytics.dim_customers"))
    )
    assert uncovered.test_nodes == ()
    with pytest.raises(DbtApplicationNotFound):
        item.query_test_coverage(
            QueryTestCoverage(
                scope(2),
                DbtNodeId("model.mnemo_analytics.fct_orders"),
                snapshot_id=snapshot.snapshot_id,
            )
        )


def test_task_context_returns_direct_test_coverage_with_latest_run_evidence() -> None:
    item, project_scope = service(), scope()
    snapshot = item.ingest(command(project_scope)).snapshot
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
            dbt_test_coverage=ContextDbtTestCoverageQuery(
                None, relative_path="models/marts/fct_orders.sql"
            ),
        )
    )

    assert len(packet.structural_items) == 1
    fact = json.loads(packet.structural_items[0].content)
    assert fact["query_kind"] == "test_coverage"
    assert fact["subject_node"] == "model.mnemo_analytics.fct_orders"
    assert fact["test_unique_id"] == "test.mnemo_analytics.unique_fct_orders"
    assert fact["latest_run"] == {
        "status": "fail",
        "execution_time_seconds": 0.25,
        "failures": 1,
    }
    assert len(packet.structural_items[0].evidence_references) == 3


def test_manifest_selector_intersects_exact_filters_with_stable_bounds_and_scope() -> None:
    item, value = service(), scope()
    snapshot = item.ingest(command(value)).snapshot

    result = item.query_selector(
        QueryManifestSelector(
            value,
            resource_type="model",
            package_name="mnemo_analytics",
            tag="mart",
            maximum_nodes=2,
            snapshot_id=snapshot.snapshot_id,
        )
    )

    assert [str(node.unique_id) for node in result.nodes] == [
        "model.mnemo_analytics.dim_customers",
        "model.mnemo_analytics.fct_orders",
    ]
    assert result.matched_node_count == 3
    assert result.truncated is True
    assert all(node.evidence for node in result.nodes)
    assert item.query_selector(QueryManifestSelector(value, package_name="missing")).nodes == ()
    with pytest.raises(DbtApplicationNotFound):
        item.query_selector(
            QueryManifestSelector(
                scope(2),
                tag="mart",
                snapshot_id=snapshot.snapshot_id,
            )
        )


def test_source_freshness_is_observed_evidenced_and_scope_safe() -> None:
    item, project_scope = service(), scope()
    snapshot = item.ingest(command(project_scope)).snapshot
    item.ingest_source_freshness(
        IngestSourceFreshness(
            project_scope,
            snapshot.snapshot_id,
            SOURCES_FIXTURE.read_bytes(),
            "sources.json",
            STAMP,
        )
    )

    result = item.query_source_freshness(
        QuerySourceFreshness(
            project_scope,
            DbtNodeId("source.mnemo_analytics.raw_orders"),
            snapshot.snapshot_id,
        )
    )

    assert result.observation is not None
    assert result.observation.status.value == "warn"
    assert result.observation.age_seconds == 5400.0
    assert result.observation.evidence
    assert result.artifact is not None
    with pytest.raises(DbtApplicationNotFound):
        item.query_source_freshness(
            QuerySourceFreshness(
                scope(2),
                DbtNodeId("source.mnemo_analytics.raw_orders"),
                snapshot.snapshot_id,
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
            dbt_freshness=ContextDbtFreshnessQuery(DbtNodeId("source.mnemo_analytics.raw_orders")),
        )
    )
    assert len(packet.structural_items) == 1
    fact = json.loads(packet.structural_items[0].content)
    assert fact["query_kind"] == "source_freshness"
    assert fact["status"] == "warn"
    assert fact["warn_after"] == {"count": 1, "period": "hour"}
    assert len(packet.structural_items[0].evidence_references) == 2
    assert "private" not in packet.structural_items[0].content


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

    macro_impact = UnifiedContextService(
        CheckpointApplicationService(ReferenceCheckpointRepository(), clock=lambda: STAMP), item
    ).get_context(
        GetUnifiedContext(
            task_scope,
            lineage=ContextLineageQuery(
                DbtNodeId("macro.date_utils.safe_divide"),
                LineageDirection.DOWNSTREAM,
                maximum_depth=2,
            ),
        )
    )
    model = next(
        json.loads(structural_item.content)
        for structural_item in macro_impact.structural_items
        if '"node_unique_id":"model.mnemo_analytics.mart_customer_value"' in structural_item.content
    )
    assert model["lineage_edge_types"] == ["dbt_macro_dependency"]

    path = UnifiedContextService(
        CheckpointApplicationService(ReferenceCheckpointRepository(), clock=lambda: STAMP), item
    ).get_context(
        GetUnifiedContext(
            task_scope,
            lineage=ContextLineageQuery(
                DbtNodeId("source.mnemo_analytics.raw_orders"),
                LineageDirection.DOWNSTREAM,
                destination_unique_id=DbtNodeId("model.mnemo_analytics.mart_customer_value"),
            ),
        )
    )
    assert len(path.structural_items) == 4
    assert all('"query_kind":"path"' in item.content for item in path.structural_items)
    assert all(
        '"path_destination":"model.mnemo_analytics.mart_customer_value"' in item.content
        for item in path.structural_items
    )

    no_path = UnifiedContextService(
        CheckpointApplicationService(ReferenceCheckpointRepository(), clock=lambda: STAMP), item
    ).get_context(
        GetUnifiedContext(
            task_scope,
            lineage=ContextLineageQuery(
                DbtNodeId("model.mnemo_analytics.mart_customer_value"),
                LineageDirection.DOWNSTREAM,
                destination_unique_id=DbtNodeId("source.mnemo_analytics.raw_orders"),
            ),
        )
    )
    assert no_path.structural_items == ()
    assert any(omission.item_id == "dbt-path" for omission in no_path.omissions)


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
    assert len(fact.evidence_references) == 4
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
    targeted, targeted_scope = service(), scope(2)
    targeted.ingest(
        command(
            targeted_scope,
            source_state=SourceStateFingerprint(
                git_commit="abc", dirty=False, target_name="production"
            ),
        )
    )
    target_mismatch = targeted.get_active_status(
        GetActiveManifestStatus(
            targeted_scope,
            current_source_state=SourceStateFingerprint(
                git_commit="abc", dirty=False, target_name="development"
            ),
        )
    )
    assert target_mismatch.currentness is ArtifactCurrentness.STALE


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
