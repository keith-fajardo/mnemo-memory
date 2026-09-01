"""Durable MCP translation for the bounded dbt structural lookup."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from mnemo_memory.connectors.dbt.manifest import DbtManifestParser
from mnemo_memory.packages.application.checkpoints import CheckpointApplicationService
from mnemo_memory.packages.application.dbt import DbtManifestApplicationService, IngestManifest
from mnemo_memory.packages.application.dbt_structure import DbtStructureService
from mnemo_memory.packages.application.mcp_durable import DurableMcpContextPort
from mnemo_memory.packages.domain import (
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    SourceStateFingerprint,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.storage import ReferenceProjectIndexRepository

FIXTURE = Path(__file__).parents[1] / "fixtures" / "dbt" / "manifest-v12.json"
NOW = datetime(2026, 8, 26, tzinfo=UTC)
SOURCE_STATE = SourceStateFingerprint(working_tree_fingerprint="sha256:" + "a" * 64)


def project_scope() -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )


DEFAULT_DBT_SCOPE = project_scope()


def dbt_structure_service() -> DbtStructureService:
    repository = ReferenceProjectIndexRepository()
    dbt = DbtManifestApplicationService(repository, DbtManifestParser())
    dbt.ingest(
        IngestManifest(
            project_scope(),
            FIXTURE.read_bytes(),
            "fixtures/dbt/manifest-v12.json",
            NOW,
            source_state=SOURCE_STATE,
        )
    )
    return DbtStructureService(dbt, repository, current_source_state=lambda _: SOURCE_STATE)


def port(
    service: DbtStructureService | None,
    *,
    default_dbt_scope: MemoryScope | None = DEFAULT_DBT_SCOPE,
) -> DurableMcpContextPort:
    return DurableMcpContextPort(
        cast(CheckpointApplicationService, object()),
        dbt_structure_service=service,
        default_dbt_scope=default_dbt_scope,
    )


def test_dbt_structure_returns_bounded_lineage_from_explicit_dbt_scope() -> None:
    result = port(dbt_structure_service()).dbt_structure(
        {"kind": "downstream", "target": "fct_orders", "depth": 1}
    )

    assert result["kind"] == "downstream"
    assert result["resolved_unique_id"] == "model.mnemo_analytics.fct_orders"
    assert result["currentness"] == "current"
    assert result["freshness_hint"] == ""
    assert result["nodes"]
    assert result["edges"]


def test_dbt_structure_rejects_unknown_kind_and_boolean_depth() -> None:
    service = dbt_structure_service()

    unknown = port(service).dbt_structure({"kind": "tests", "target": "fct_orders"})
    boolean_depth = port(service).dbt_structure(
        {"kind": "upstream", "target": "fct_orders", "depth": True}
    )

    assert unknown["nodes"] == []
    assert unknown["currentness"] == "unknown"
    assert boolean_depth["nodes"] == []


def test_dbt_structure_without_service_or_dbt_binding_fails_open() -> None:
    no_service = port(None).dbt_structure({"kind": "upstream", "target": "fct_orders"})
    no_binding = port(dbt_structure_service(), default_dbt_scope=None).dbt_structure(
        {"kind": "upstream", "target": "fct_orders"}
    )

    assert no_service["nodes"] == []
    assert no_binding["nodes"] == []
    assert no_service["snapshot_id"] is None
    assert no_binding["snapshot_id"] is None
