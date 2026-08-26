"""Tests for DbtStructureService: lineage/impact/test_coverage/freshness/changes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from mnemo_memory.connectors.dbt.artifacts import DbtSourceFreshnessParser
from mnemo_memory.connectors.dbt.manifest import DbtManifestParser
from mnemo_memory.packages.application.dbt import (
    DbtManifestApplicationService,
    IngestManifest,
    IngestSourceFreshness,
)
from mnemo_memory.packages.application.dbt_structure import DbtStructureService
from mnemo_memory.packages.domain import (
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.storage.reference import ReferenceProjectIndexRepository

FIXTURE = Path(__file__).parents[1] / "fixtures" / "dbt" / "manifest-v12.json"
SOURCES_FIXTURE = Path(__file__).parents[1] / "fixtures" / "dbt" / "sources-v3.json"
STAMP = datetime(2026, 8, 2, tzinfo=UTC)


def project_scope() -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )


def dbt_service_with_manifest() -> DbtManifestApplicationService:
    repo = ReferenceProjectIndexRepository()
    writer = DbtManifestApplicationService(repo, DbtManifestParser())
    writer.ingest(
        IngestManifest(
            scope=project_scope(),
            raw_manifest=FIXTURE.read_text(),
            source_identity="fixtures/dbt/manifest-v12.json",
            ingested_at=STAMP,
        )
    )
    return DbtManifestApplicationService(repo)  # reader-only handle, mirrors production wiring


def dbt_service_with_manifest_and_freshness() -> DbtManifestApplicationService:
    repo = ReferenceProjectIndexRepository()
    writer = DbtManifestApplicationService(
        repo, DbtManifestParser(), source_freshness_parser=DbtSourceFreshnessParser()
    )
    snapshot = writer.ingest(
        IngestManifest(
            scope=project_scope(),
            raw_manifest=FIXTURE.read_text(),
            source_identity="fixtures/dbt/manifest-v12.json",
            ingested_at=STAMP,
        )
    ).snapshot
    writer.ingest_source_freshness(
        IngestSourceFreshness(
            scope=project_scope(),
            snapshot_id=snapshot.snapshot_id,
            raw_sources=SOURCES_FIXTURE.read_bytes(),
            source_identity="sources.json",
            ingested_at=STAMP,
        )
    )
    return DbtManifestApplicationService(repo)


def _manifest_with_added_modified_and_removed_nodes() -> str:
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


def dbt_service_with_two_manifest_snapshots() -> DbtManifestApplicationService:
    repo = ReferenceProjectIndexRepository()
    writer = DbtManifestApplicationService(repo, DbtManifestParser())
    first = writer.ingest(
        IngestManifest(
            scope=project_scope(),
            raw_manifest=FIXTURE.read_text(),
            source_identity="fixtures/dbt/manifest-v12.json",
            ingested_at=STAMP,
        )
    ).snapshot
    writer.ingest(
        IngestManifest(
            scope=project_scope(),
            raw_manifest=_manifest_with_added_modified_and_removed_nodes(),
            source_identity="fixtures/dbt/manifest-v12.json",
            ingested_at=STAMP,
            expected_active_snapshot_id=first.snapshot_id,
        )
    )
    return DbtManifestApplicationService(repo)


# --- A1: lineage kinds + node resolution + fail-open -----------------------


def test_downstream_from_unique_id() -> None:
    service = DbtStructureService(dbt_service_with_manifest())
    r = service.lookup(
        project_scope(), kind="downstream", target="model.mnemo_analytics.fct_orders"
    )
    assert r.kind == "downstream"
    assert r.resolved_unique_id == "model.mnemo_analytics.fct_orders"
    assert r.currentness in ("current", "stale", "unknown")
    assert isinstance(r.items, tuple)


def test_impact_kind_uses_downstream_direction() -> None:
    service = DbtStructureService(dbt_service_with_manifest())
    downstream = service.lookup(
        project_scope(), kind="downstream", target="model.mnemo_analytics.fct_orders"
    )
    impact = service.lookup(
        project_scope(), kind="impact", target="model.mnemo_analytics.fct_orders"
    )
    assert impact.kind == "impact"
    assert impact.resolved_unique_id == downstream.resolved_unique_id
    assert impact.items == downstream.items


def test_target_resolves_by_relative_path() -> None:
    service = DbtStructureService(dbt_service_with_manifest())
    r = service.lookup(project_scope(), kind="upstream", target="models/marts/fct_orders.sql")
    assert r.resolved_unique_id == "model.mnemo_analytics.fct_orders"


def test_bare_name_target_is_unsupported_fails_open() -> None:
    # DbtManifestApplicationService exposes neither a public active-snapshot
    # accessor nor iter_nodes (both live only on the storage-layer
    # repository); per the plan's explicit fallback, bare-name resolution is
    # therefore unsupported rather than reaching into a private attribute.
    service = DbtStructureService(dbt_service_with_manifest())
    r = service.lookup(project_scope(), kind="upstream", target="fct_orders")
    assert r.items == ()
    assert r.resolved_unique_id is None
    assert r.currentness == "unknown"


def test_unknown_node_fails_open() -> None:
    service = DbtStructureService(dbt_service_with_manifest())
    r = service.lookup(project_scope(), kind="upstream", target="does_not_exist_xyz")
    assert r.items == () and r.resolved_unique_id is None
    assert r.currentness == "unknown"


def test_unsupported_kind_fails_open() -> None:
    service = DbtStructureService(dbt_service_with_manifest())
    r = service.lookup(project_scope(), kind="bogus_kind", target="fct_orders")  # type: ignore[arg-type]
    assert r.items == () and r.currentness == "unknown"


def test_no_active_manifest_fails_open() -> None:
    service = DbtStructureService(DbtManifestApplicationService(ReferenceProjectIndexRepository()))
    r = service.lookup(project_scope(), kind="changes", target="")
    assert r.items == () and r.currentness == "unknown"


# --- A2: test_coverage / freshness / changes --------------------------------


def test_test_coverage_returns_attached_test() -> None:
    service = DbtStructureService(dbt_service_with_manifest())
    r = service.lookup(
        project_scope(), kind="test_coverage", target="model.mnemo_analytics.fct_orders"
    )
    assert r.kind == "test_coverage"
    assert r.resolved_unique_id == "model.mnemo_analytics.fct_orders"
    assert r.currentness in ("current", "stale", "unknown")
    assert r.items == (
        {
            "test_unique_id": "test.mnemo_analytics.unique_fct_orders",
            "subject_node": "model.mnemo_analytics.fct_orders",
            "resource_type": "test",
            "relative_path": "tests/unique_fct_orders.sql",
        },
    )


def test_freshness_returns_observed_status() -> None:
    service = DbtStructureService(dbt_service_with_manifest_and_freshness())
    r = service.lookup(
        project_scope(), kind="freshness", target="source.mnemo_analytics.raw_orders"
    )
    assert r.kind == "freshness"
    assert r.resolved_unique_id == "source.mnemo_analytics.raw_orders"
    assert r.currentness in ("current", "stale", "unknown")
    assert len(r.items) == 1
    item = r.items[0]
    assert item["source_unique_id"] == "source.mnemo_analytics.raw_orders"
    assert item["status"] == "warn"
    assert item["age_seconds"] == 5400.0


def test_freshness_without_observation_is_empty_but_valid() -> None:
    service = DbtStructureService(dbt_service_with_manifest())
    r = service.lookup(
        project_scope(), kind="freshness", target="source.mnemo_analytics.raw_orders"
    )
    assert r.kind == "freshness"
    assert r.resolved_unique_id == "source.mnemo_analytics.raw_orders"
    assert r.items == ()


def test_changes_with_single_snapshot_fails_open_but_valid() -> None:
    service = DbtStructureService(dbt_service_with_manifest())
    r = service.lookup(project_scope(), kind="changes", target="")
    assert r.kind == "changes"
    assert r.items == ()
    assert r.currentness == "unknown"


def test_changes_across_two_snapshots_maps_added_modified_removed() -> None:
    service = DbtStructureService(dbt_service_with_two_manifest_snapshots())
    r = service.lookup(project_scope(), kind="changes", target="")
    assert r.kind == "changes"
    assert r.currentness in ("current", "stale", "unknown")
    kinds_by_id = {item["unique_id"]: item["kind"] for item in r.items}
    assert kinds_by_id["model.mnemo_analytics.fct_orders"] == "modified"
    assert kinds_by_id["model.mnemo_analytics.new_rollup"] == "added"
    assert kinds_by_id["test.mnemo_analytics.unique_fct_orders"] == "removed"
