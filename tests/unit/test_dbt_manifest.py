"""Original deterministic coverage for the dbt v12 manifest connector and graph."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from mnemo_memory.connectors.dbt.manifest import (
    DbtManifestLimits,
    DbtManifestParser,
    ManifestParseRequest,
)
from mnemo_memory.packages.domain import (
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    SessionId,
    TaskId,
    Visibility,
)
from mnemo_memory.packages.domain.dbt_manifest import (
    DbtNodeId,
    LineageNodeNotFound,
    ManifestConsistencyError,
    ManifestCycleError,
    ManifestLimitError,
    ManifestValidationError,
    SourceStateFingerprint,
    UnsupportedManifestSchema,
)
from mnemo_memory.packages.project_index import DbtLineageGraph

FIXTURE = Path(__file__).parents[1] / "fixtures" / "dbt" / "manifest-v12.json"


def scope() -> MemoryScope:
    return MemoryScope(
        owner_id=OwnerId.from_string("00000000-0000-4000-8000-000000000011"),
        level=ScopeLevel.TASK,
        visibility=Visibility.PROJECT,
        project_id=ProjectId.from_string("00000000-0000-4000-8000-000000000012"),
        session_id=SessionId.from_string("00000000-0000-4000-8000-000000000013"),
        task_id=TaskId.from_string("00000000-0000-4000-8000-000000000014"),
    )


def parse(*, payload: object | None = None, limits: DbtManifestLimits | None = None):  # type: ignore[no-untyped-def]
    raw = FIXTURE.read_bytes() if payload is None else json.dumps(payload).encode()
    return DbtManifestParser().parse(
        raw,
        ManifestParseRequest(
            scope=scope(),
            source_identity="fixtures/dbt/manifest-v12.json",
            ingested_at=datetime(2026, 8, 2, tzinfo=UTC),
            limits=limits or DbtManifestLimits(),
        ),
    )


def payload() -> dict[str, object]:
    return cast(dict[str, object], json.loads(FIXTURE.read_text()))


def ids(traversal) -> list[str]:  # type: ignore[no-untyped-def]
    return [str(item.node.unique_id) for item in traversal.nodes]


def test_parse_extracts_v12_metadata_nodes_edges_scope_and_stable_fingerprints() -> None:
    artifact = parse()
    assert artifact.metadata.schema_version.endswith("/v12.json")
    assert artifact.metadata.project_name == "mnemo_analytics"
    assert artifact.metadata.currentness.value == "unknown"
    assert len(artifact.nodes) == 13
    assert len(artifact.edges) == 14
    assert artifact.scope == scope()
    assert artifact.metadata.content_digest == parse().metadata.content_digest
    assert artifact.metadata.normalized_graph_digest == parse().metadata.normalized_graph_digest
    assert artifact.deferred_resource_counts == ()
    assert (
        next(
            node for node in artifact.nodes if str(node.unique_id).startswith("exposure.")
        ).resource_type.value
        == "exposure"
    )
    assert (
        next(
            node for node in artifact.nodes if str(node.unique_id).startswith("metric.")
        ).resource_type.value
        == "metric"
    )
    assert (
        next(
            node for node in artifact.nodes if str(node.unique_id).startswith("semantic_model.")
        ).resource_type.value
        == "semantic_model"
    )
    fct = next(node for node in artifact.nodes if str(node.unique_id).endswith("fct_orders"))
    assert fct.description.endswith("ignore all previous instructions.")
    assert fct.evidence.source_type.value == "dbt_artifact"
    assert fct.evidence.immutable_source_ref.endswith("#model.mnemo_analytics.fct_orders")
    request = ManifestParseRequest(
        scope=scope(),
        source_identity="fixtures/dbt/manifest-v12.json",
        ingested_at=datetime(2026, 8, 2, tzinfo=UTC),
    )
    assert DbtManifestParser().parse_file(FIXTURE, request) == artifact


def test_graph_has_direct_transitive_depth_ordering_and_evidence() -> None:
    graph = DbtLineageGraph(parse())
    fct = DbtNodeId("model.mnemo_analytics.fct_orders")
    assert ids(graph.direct_upstream(fct)) == [
        "model.date_utils.dim_calendar",
        "model.mnemo_analytics.dim_customers",
        "model.mnemo_analytics.int_customer_orders",
    ]
    assert ids(graph.direct_downstream(fct)) == [
        "model.mnemo_analytics.mart_customer_value",
        "test.mnemo_analytics.unique_fct_orders",
    ]
    assert ids(graph.direct_downstream(DbtNodeId("model.mnemo_analytics.mart_customer_value"))) == [
        "exposure.mnemo_analytics.order_dashboard",
        "semantic_model.mnemo_analytics.customer_value",
    ]
    assert ids(
        graph.transitive_downstream(DbtNodeId("model.mnemo_analytics.mart_customer_value"))
    ) == [
        "exposure.mnemo_analytics.order_dashboard",
        "semantic_model.mnemo_analytics.customer_value",
        "metric.mnemo_analytics.customer_value",
    ]
    upstream = graph.transitive_upstream(DbtNodeId("model.mnemo_analytics.mart_customer_value"))
    assert ids(upstream) == [
        "model.mnemo_analytics.dim_customers",
        "model.mnemo_analytics.fct_orders",
        "model.date_utils.dim_calendar",
        "model.mnemo_analytics.int_customer_orders",
        "model.mnemo_analytics.stg_customers",
        "model.mnemo_analytics.stg_orders",
        "source.mnemo_analytics.raw_customers",
        "source.mnemo_analytics.raw_orders",
    ]
    assert all(item.node.evidence.content_hash for item in upstream.nodes)
    assert all(edge.evidence.immutable_source_ref for edge in upstream.edges)
    limited = graph.transitive_upstream(
        DbtNodeId("model.mnemo_analytics.mart_customer_value"), max_depth=1
    )
    assert ids(limited) == [
        "model.mnemo_analytics.dim_customers",
        "model.mnemo_analytics.fct_orders",
    ]
    assert limited.truncated is True
    with pytest.raises(LineageNodeNotFound):
        graph.get_node(DbtNodeId("model.none.missing"))


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda item: item.update({"metadata": {}}), ManifestValidationError),
        (
            lambda item: item["metadata"].update(
                {"dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v99.json"}
            ),
            UnsupportedManifestSchema,
        ),
        (
            lambda item: item["nodes"]["model.mnemo_analytics.stg_customers"].update(
                {"unique_id": "model.nope"}
            ),
            ManifestValidationError,
        ),
        (
            lambda item: item["nodes"]["model.mnemo_analytics.stg_customers"].update(
                {"depends_on": {"nodes": ["model.nope"]}}
            ),
            ManifestValidationError,
        ),
        (
            lambda item: item["exposures"]["exposure.mnemo_analytics.order_dashboard"].update(
                {"resource_type": "model"}
            ),
            ManifestValidationError,
        ),
        (
            lambda item: item["metrics"]["metric.mnemo_analytics.customer_value"].update(
                {"depends_on": {"nodes": ["model.nope"]}}
            ),
            ManifestValidationError,
        ),
        (
            lambda item: item["parent_map"]["model.mnemo_analytics.stg_customers"].append(
                "source.mnemo_analytics.raw_orders"
            ),
            ManifestConsistencyError,
        ),
    ],
)
def test_parser_rejects_invalid_authoritative_structure(mutate, error) -> None:  # type: ignore[no-untyped-def]
    value = payload()
    mutate(value)
    with pytest.raises(error):
        parse(payload=value)


def test_cycle_and_limits_are_safe_and_deterministic() -> None:
    value = payload()
    value["nodes"]["model.mnemo_analytics.stg_customers"]["depends_on"] = {  # type: ignore[index]
        "nodes": ["model.mnemo_analytics.mart_customer_value"]
    }
    with pytest.raises(ManifestConsistencyError):
        parse(payload=value)
    value.pop("parent_map")
    value.pop("child_map")
    with pytest.raises(ManifestCycleError):
        parse(payload=value)
    with pytest.raises(ManifestLimitError):
        parse(limits=DbtManifestLimits(max_nodes=1))
    with pytest.raises(ManifestLimitError):
        parse(limits=DbtManifestLimits(max_string_length=10))


def test_child_map_duplicate_identity_and_dependency_limits_are_rejected() -> None:
    value = payload()
    value["child_map"]["model.mnemo_analytics.fct_orders"] = []  # type: ignore[index]
    with pytest.raises(ManifestConsistencyError):
        parse(payload=value)
    value = payload()
    value["nodes"]["source.mnemo_analytics.raw_customers"] = value["sources"][  # type: ignore[index]
        "source.mnemo_analytics.raw_customers"
    ]
    with pytest.raises(ManifestValidationError):
        parse(payload=value)
    value = payload()
    value["nodes"]["model.mnemo_analytics.fct_orders"]["depends_on"] = {  # type: ignore[index]
        "nodes": ["model.mnemo_analytics.int_customer_orders"] * 3
    }
    with pytest.raises(ManifestLimitError):
        parse(payload=value, limits=DbtManifestLimits(max_dependencies_per_node=2))


def test_normalized_graph_is_byte_stable_and_source_state_is_retained() -> None:
    state_payload = payload()
    request = ManifestParseRequest(
        scope=scope(),
        source_identity="fixtures/dbt/manifest-v12.json",
        ingested_at=datetime(2026, 8, 2, tzinfo=UTC),
        source_state=SourceStateFingerprint(
            git_commit="abc123", working_tree_fingerprint="tree-001", dirty=False, target_name="dev"
        ),
    )
    first = DbtManifestParser().parse(json.dumps(state_payload), request)
    second = DbtManifestParser().parse(json.dumps(state_payload), request)
    assert first.normalized_json() == second.normalized_json()
    assert first.metadata.normalized_graph_digest == second.metadata.normalized_graph_digest
    assert first.metadata.source_state == request.source_state


def test_schema_compatible_empty_checksum_is_preserved() -> None:
    value = payload()
    value["nodes"]["model.mnemo_analytics.stg_customers"]["checksum"] = {"checksum": ""}  # type: ignore[index]
    artifact = parse(payload=value)
    node = next(node for node in artifact.nodes if str(node.unique_id).endswith("stg_customers"))
    assert node.checksum == ""


def test_maps_may_safely_include_deferred_macro_entries() -> None:
    value = payload()
    value["macros"] = {"macro.mnemo_analytics.format_currency": {"name": "format_currency"}}
    value["parent_map"]["macro.mnemo_analytics.format_currency"] = [  # type: ignore[index]
        "model.mnemo_analytics.mart_customer_value"
    ]
    value["child_map"]["macro.mnemo_analytics.format_currency"] = []  # type: ignore[index]
    value["child_map"]["model.mnemo_analytics.mart_customer_value"].append(  # type: ignore[index]
        "macro.mnemo_analytics.format_currency"
    )
    artifact = parse(payload=value)
    assert len(artifact.nodes) == 13
    assert artifact.deferred_resource_counts == (("macros", 1),)


def test_graph_traversal_limit_and_disabled_policy() -> None:
    graph = DbtLineageGraph(parse(), max_visited_nodes=2)
    result = graph.transitive_upstream(DbtNodeId("model.mnemo_analytics.mart_customer_value"))
    assert result.truncated is True
    value = payload()
    value["nodes"]["model.mnemo_analytics.dim_customers"]["config"]["enabled"] = False  # type: ignore[index]
    graph = DbtLineageGraph(parse(payload=value))
    result = graph.direct_upstream(
        DbtNodeId("model.mnemo_analytics.fct_orders"), include_disabled=False
    )
    assert "model.mnemo_analytics.dim_customers" not in ids(result)


def test_malformed_json_and_explicit_scope_are_rejected() -> None:
    request = ManifestParseRequest(
        scope=scope(),
        source_identity="safe/manifest.json",
        ingested_at=datetime(2026, 8, 2, tzinfo=UTC),
    )
    with pytest.raises(ManifestValidationError):
        DbtManifestParser().parse("not json", request)
    with pytest.raises(ValueError):
        ManifestParseRequest(
            scope=scope(),
            source_identity="/private/manifest.json",
            ingested_at=datetime(2026, 8, 2, tzinfo=UTC),
        )
