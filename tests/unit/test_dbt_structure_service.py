"""Tests for DbtStructureService: lineage/impact/test_coverage/freshness/changes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from mnemo_memory.connectors.dbt.manifest import DbtManifestParser
from mnemo_memory.packages.application.dbt import DbtManifestApplicationService, IngestManifest
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
