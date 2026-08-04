"""Original contract tests for bounded supplemental dbt artifact parsing."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from mnemo_memory.connectors.dbt import (
    DbtCatalogParser,
    DbtRunResultsParser,
    DbtSupplementalArtifactLimits,
    DbtSupplementalParseRequest,
)
from mnemo_memory.packages.domain import (
    DbtRunStatus,
    DbtSupplementalArtifactLimitError,
    DbtSupplementalArtifactValidationError,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    SessionId,
    TaskId,
    UnsupportedDbtSupplementalSchema,
    Visibility,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "dbt"
CATALOG = FIXTURES / "catalog-v1.json"
RUN_RESULTS = FIXTURES / "run-results-v6.json"


def scope() -> MemoryScope:
    return MemoryScope(
        owner_id=OwnerId.from_string("00000000-0000-4000-8000-000000000011"),
        level=ScopeLevel.TASK,
        visibility=Visibility.PROJECT,
        project_id=ProjectId.from_string("00000000-0000-4000-8000-000000000012"),
        session_id=SessionId.from_string("00000000-0000-4000-8000-000000000013"),
        task_id=TaskId.from_string("00000000-0000-4000-8000-000000000014"),
    )


def request(*, limits: DbtSupplementalArtifactLimits | None = None) -> DbtSupplementalParseRequest:
    return DbtSupplementalParseRequest(
        scope=scope(),
        source_identity="target/artifact.json",
        ingested_at=datetime(2026, 8, 5, 1, 2, tzinfo=UTC),
        limits=limits or DbtSupplementalArtifactLimits(),
    )


def payload(path: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads(path.read_text()))


def test_catalog_v1_retains_only_bounded_structural_relation_and_column_facts() -> None:
    parser = DbtCatalogParser()
    artifact = parser.parse(CATALOG.read_bytes(), request())
    assert artifact.scope == scope()
    assert artifact.metadata.schema_version.endswith("/catalog/v1.json")
    assert (
        artifact.metadata.content_digest
        == parser.parse(CATALOG.read_bytes(), request()).metadata.content_digest
    )
    assert (
        artifact.metadata.normalized_digest
        == parser.parse(CATALOG.read_bytes(), request()).metadata.normalized_digest
    )
    assert artifact.error_count == 0
    assert [str(item.unique_id) for item in artifact.relations] == [
        "model.mnemo_analytics.fct_orders",
        "source.mnemo_analytics.raw_orders",
    ]
    model = artifact.relations[0]
    assert [(item.index, item.name, item.data_type) for item in model.columns] == [
        (1, "order_id", "INTEGER"),
        (2, "amount", "DECIMAL"),
    ]
    assert model.evidence.immutable_source_ref.endswith("#catalog:model.mnemo_analytics.fct_orders")
    serialized = artifact.normalized_json()
    assert "secret-that-must-not-be-retained" not in serialized
    assert "warehouse-owner" not in serialized
    assert "row_count" not in serialized


def test_run_results_v6_retains_status_timing_and_failure_count_without_payloads() -> None:
    parser = DbtRunResultsParser()
    artifact = parser.parse(RUN_RESULTS.read_bytes(), request())
    assert artifact.scope == scope()
    assert artifact.metadata.schema_version.endswith("/run-results/v6.json")
    assert artifact.command == "build"
    assert artifact.elapsed_time_seconds == 4.5
    assert [(str(item.unique_id), item.status, item.failures) for item in artifact.results] == [
        ("model.mnemo_analytics.fct_orders", DbtRunStatus.SUCCESS, None),
        ("test.mnemo_analytics.unique_fct_orders", DbtRunStatus.FAIL, 1),
    ]
    assert [item.name for item in artifact.results[0].timing] == ["compile", "execute"]
    assert artifact.results[0].evidence.immutable_source_ref.endswith(
        "#run-result:model.mnemo_analytics.fct_orders"
    )
    serialized = artifact.normalized_json()
    assert "secret-that-must-not-be-retained" not in serialized
    assert "private/path" not in serialized
    assert "private_table" not in serialized
    assert "Thread-1" not in serialized


@pytest.mark.parametrize(
    ("path", "parser", "schema"),
    [
        (CATALOG, DbtCatalogParser(), "https://schemas.getdbt.com/dbt/catalog/v99.json"),
        (
            RUN_RESULTS,
            DbtRunResultsParser(),
            "https://schemas.getdbt.com/dbt/run-results/v99.json",
        ),
    ],
)
def test_supplemental_parsers_reject_unsupported_schemas(
    path: Path, parser: DbtCatalogParser | DbtRunResultsParser, schema: str
) -> None:
    value = payload(path)
    cast(dict[str, object], value["metadata"])["dbt_schema_version"] = schema
    with pytest.raises(UnsupportedDbtSupplementalSchema):
        parser.parse(json.dumps(value), request())


def test_catalog_rejects_mismatched_identity_duplicate_columns_and_limits() -> None:
    value = payload(CATALOG)
    model = cast(
        dict[str, object],
        cast(dict[str, object], value["nodes"])["model.mnemo_analytics.fct_orders"],
    )
    model["unique_id"] = "model.other"
    with pytest.raises(DbtSupplementalArtifactValidationError):
        DbtCatalogParser().parse(json.dumps(value), request())

    value = payload(CATALOG)
    model = cast(
        dict[str, object],
        cast(dict[str, object], value["nodes"])["model.mnemo_analytics.fct_orders"],
    )
    columns = cast(dict[str, object], model["columns"])
    cast(dict[str, object], columns["amount"])["index"] = 1
    with pytest.raises(ValueError, match="indexes must be unique"):
        DbtCatalogParser().parse(json.dumps(value), request())

    with pytest.raises(DbtSupplementalArtifactLimitError):
        DbtCatalogParser().parse(
            CATALOG.read_bytes(),
            request(limits=DbtSupplementalArtifactLimits(max_columns=1)),
        )


def test_run_results_rejects_duplicates_invalid_time_status_and_hostile_json() -> None:
    value = payload(RUN_RESULTS)
    results = cast(list[object], value["results"])
    results.append(results[0])
    with pytest.raises(ValueError, match="duplicate node identities"):
        DbtRunResultsParser().parse(json.dumps(value), request())

    value = payload(RUN_RESULTS)
    first = cast(dict[str, object], cast(list[object], value["results"])[0])
    first["status"] = "invented"
    with pytest.raises(DbtSupplementalArtifactValidationError, match="status is unsupported"):
        DbtRunResultsParser().parse(json.dumps(value), request())

    first["status"] = "success"
    first["execution_time"] = -1
    with pytest.raises(DbtSupplementalArtifactValidationError, match="finite and non-negative"):
        DbtRunResultsParser().parse(json.dumps(value), request())

    with pytest.raises(DbtSupplementalArtifactValidationError, match="not valid JSON"):
        DbtRunResultsParser().parse('{"elapsed_time": NaN}', request())


def test_supplemental_request_and_string_limits_fail_before_retention() -> None:
    with pytest.raises(ValueError, match="non-absolute"):
        DbtSupplementalParseRequest(
            scope=scope(),
            source_identity="/private/catalog.json",
            ingested_at=datetime(2026, 8, 5, tzinfo=UTC),
        )
    with pytest.raises(DbtSupplementalArtifactLimitError, match="string above"):
        DbtCatalogParser().parse(
            CATALOG.read_bytes(),
            request(limits=DbtSupplementalArtifactLimits(max_string_length=8)),
        )
