"""Offline bounded parsers for dbt catalog v1 and run-results v6 artifacts."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
from typing import cast
from uuid import UUID, uuid5

from mnemo_memory.packages.domain import (
    DbtCatalogArtifact,
    DbtCatalogCollection,
    DbtCatalogColumn,
    DbtCatalogRelation,
    DbtNodeId,
    DbtNodeRunResult,
    DbtRunResultsArtifact,
    DbtRunStatus,
    DbtRunTiming,
    DbtSupplementalArtifactLimitError,
    DbtSupplementalArtifactMetadata,
    DbtSupplementalArtifactValidationError,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryScope,
    SourceId,
    SourceTrustClass,
    UnsupportedDbtSupplementalSchema,
    VerificationStatus,
)
from mnemo_memory.packages.domain.dbt_artifacts import normalized_digest

SUPPORTED_CATALOG_SCHEMA = "https://schemas.getdbt.com/dbt/catalog/v1.json"
SUPPORTED_RUN_RESULTS_SCHEMA = "https://schemas.getdbt.com/dbt/run-results/v6.json"
_EVIDENCE_NAMESPACE = UUID("e47c5cbe-0464-476d-9f45-4ea5383d186e")
_KNOWN_RUN_STATUSES = {
    "success": DbtRunStatus.SUCCESS,
    "error": DbtRunStatus.ERROR,
    "skipped": DbtRunStatus.SKIPPED,
    "partial success": DbtRunStatus.PARTIAL_SUCCESS,
    "no-op": DbtRunStatus.NO_OP,
    "pass": DbtRunStatus.PASS,
    "fail": DbtRunStatus.FAIL,
    "warn": DbtRunStatus.WARN,
    "runtime error": DbtRunStatus.RUNTIME_ERROR,
}


@dataclass(frozen=True, slots=True)
class DbtSupplementalArtifactLimits:
    max_artifact_bytes: int = 20_000_000
    max_relations: int = 50_000
    max_columns: int = 500_000
    max_results: int = 50_000
    max_timings_per_result: int = 32
    max_errors: int = 10_000
    max_string_length: int = 100_000

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 1
            for value in (
                self.max_artifact_bytes,
                self.max_relations,
                self.max_columns,
                self.max_results,
                self.max_timings_per_result,
                self.max_errors,
                self.max_string_length,
            )
        ):
            raise ValueError("dbt supplemental artifact limits must be positive integers")


@dataclass(frozen=True, slots=True)
class DbtSupplementalParseRequest:
    scope: MemoryScope
    source_identity: str
    ingested_at: datetime
    limits: DbtSupplementalArtifactLimits = DbtSupplementalArtifactLimits()

    def __post_init__(self) -> None:
        if not isinstance(self.scope, MemoryScope):
            raise TypeError("supplemental dbt parsing requires explicit scope")
        if not isinstance(self.source_identity, str) or not self.source_identity.strip():
            raise ValueError("source_identity must be non-empty")
        if self.source_identity.startswith("/"):
            raise ValueError("source_identity must be non-absolute")
        if self.ingested_at.tzinfo is None or self.ingested_at.utcoffset() is None:
            raise ValueError("ingested_at must be timezone-aware")


class DbtCatalogParser:
    """Parse only relation identities, relation types, and ordered column names/types."""

    def parse(self, raw: bytes | str, request: DbtSupplementalParseRequest) -> DbtCatalogArtifact:
        value, encoded = _decode(raw, request, "catalog")
        _allowed(value, {"metadata", "nodes", "sources", "errors", "_compile_results"}, "catalog")
        _required_fields(value, {"metadata", "nodes", "sources"}, "catalog")
        metadata_value = _mapping(value.get("metadata"), "catalog metadata")
        metadata = _metadata(
            metadata_value,
            SUPPORTED_CATALOG_SCHEMA,
            encoded,
            request,
        )
        nodes = _object_map(value.get("nodes"), "catalog nodes")
        sources = _object_map(value.get("sources"), "catalog sources")
        if len(nodes) + len(sources) > request.limits.max_relations:
            raise DbtSupplementalArtifactLimitError(
                "dbt catalog exceeds the configured relation limit"
            )
        relations: list[DbtCatalogRelation] = []
        column_count = 0
        for collection, entries in (
            (DbtCatalogCollection.NODE, nodes),
            (DbtCatalogCollection.SOURCE, sources),
        ):
            for unique_id in sorted(entries):
                raw_relation = _mapping(entries[unique_id], f"catalog relation {unique_id}")
                _allowed(
                    raw_relation,
                    {"metadata", "columns", "stats", "unique_id"},
                    f"catalog relation {unique_id}",
                )
                _required_fields(
                    raw_relation,
                    {"metadata", "columns", "stats"},
                    f"catalog relation {unique_id}",
                )
                embedded_id = raw_relation.get("unique_id")
                if embedded_id is not None and embedded_id != unique_id:
                    raise DbtSupplementalArtifactValidationError(
                        "dbt catalog map key must match its contained unique_id"
                    )
                relation_metadata = _mapping(
                    raw_relation.get("metadata"), f"catalog relation metadata {unique_id}"
                )
                _allowed(
                    relation_metadata,
                    {"type", "schema", "name", "database", "comment", "owner"},
                    f"catalog relation metadata {unique_id}",
                )
                _required_fields(
                    relation_metadata,
                    {"type", "schema", "name"},
                    f"catalog relation metadata {unique_id}",
                )
                _nullable_string(relation_metadata.get("comment"), "comment")
                _nullable_string(relation_metadata.get("owner"), "owner")
                columns_value = _object_map(
                    raw_relation.get("columns"), f"catalog columns {unique_id}"
                )
                column_count += len(columns_value)
                if column_count > request.limits.max_columns:
                    raise DbtSupplementalArtifactLimitError(
                        "dbt catalog exceeds the configured column limit"
                    )
                columns = tuple(
                    self._column(column_key, column_value, unique_id)
                    for column_key, column_value in sorted(columns_value.items())
                )
                if not isinstance(raw_relation.get("stats"), Mapping):
                    raise DbtSupplementalArtifactValidationError(
                        "dbt catalog relation stats must be an object"
                    )
                relations.append(
                    DbtCatalogRelation(
                        unique_id=DbtNodeId(unique_id),
                        collection=collection,
                        relation_type=_required_string(relation_metadata, "type"),
                        database=_optional_string(relation_metadata.get("database"), "database"),
                        schema_name=_required_string(relation_metadata, "schema"),
                        name=_required_string(relation_metadata, "name"),
                        columns=columns,
                        evidence=_evidence(
                            metadata.content_digest, request, f"catalog:{unique_id}"
                        ),
                    )
                )
        errors = value.get("errors")
        if errors is None:
            error_count = 0
        elif isinstance(errors, list) and all(isinstance(item, str) for item in errors):
            error_count = len(errors)
        else:
            raise DbtSupplementalArtifactValidationError(
                "dbt catalog errors must be an array of strings or null"
            )
        if error_count > request.limits.max_errors:
            raise DbtSupplementalArtifactLimitError(
                "dbt catalog exceeds the configured error-count limit"
            )
        preliminary = DbtCatalogArtifact(metadata, request.scope, tuple(relations), error_count)
        return replace(
            preliminary,
            metadata=replace(
                metadata, normalized_digest=normalized_digest(preliminary.normalized_json())
            ),
        )

    @staticmethod
    def _column(column_key: str, value: object, unique_id: str) -> DbtCatalogColumn:
        if not column_key.strip():
            raise DbtSupplementalArtifactValidationError(
                "dbt catalog column map keys must be non-empty"
            )
        raw_column = _mapping(value, f"catalog column {unique_id}.{column_key}")
        _allowed(
            raw_column,
            {"type", "index", "name", "comment"},
            f"catalog column {unique_id}.{column_key}",
        )
        _required_fields(
            raw_column,
            {"type", "index", "name"},
            f"catalog column {unique_id}.{column_key}",
        )
        _nullable_string(raw_column.get("comment"), "comment")
        index = raw_column.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            raise DbtSupplementalArtifactValidationError(
                "dbt catalog column index must be an integer"
            )
        return DbtCatalogColumn(
            index=index,
            name=_required_string(raw_column, "name"),
            data_type=_required_string(raw_column, "type"),
        )


class DbtRunResultsParser:
    """Parse bounded execution status while discarding code, messages, and adapter payloads."""

    def parse(
        self, raw: bytes | str, request: DbtSupplementalParseRequest
    ) -> DbtRunResultsArtifact:
        value, encoded = _decode(raw, request, "run-results")
        _allowed(value, {"metadata", "results", "elapsed_time", "args"}, "run-results")
        _required_fields(value, {"metadata", "results", "elapsed_time"}, "run-results")
        metadata = _metadata(
            _mapping(value.get("metadata"), "run-results metadata"),
            SUPPORTED_RUN_RESULTS_SCHEMA,
            encoded,
            request,
        )
        raw_results = value.get("results")
        if not isinstance(raw_results, list):
            raise DbtSupplementalArtifactValidationError("dbt run-results results must be an array")
        if len(raw_results) > request.limits.max_results:
            raise DbtSupplementalArtifactLimitError(
                "dbt run-results exceeds the configured result limit"
            )
        results = tuple(
            self._result(item, metadata.content_digest, request) for item in raw_results
        )
        args = value.get("args", {})
        if not isinstance(args, Mapping):
            raise DbtSupplementalArtifactValidationError("dbt run-results args must be an object")
        command_value = args.get("which", args.get("rpc_method"))
        command = _optional_string(command_value, "command")
        preliminary = DbtRunResultsArtifact(
            metadata=metadata,
            scope=request.scope,
            elapsed_time_seconds=_finite_non_negative(value.get("elapsed_time"), "elapsed_time"),
            command=command,
            results=results,
        )
        return replace(
            preliminary,
            metadata=replace(
                metadata, normalized_digest=normalized_digest(preliminary.normalized_json())
            ),
        )

    @staticmethod
    def _result(
        value: object, content_digest: str, request: DbtSupplementalParseRequest
    ) -> DbtNodeRunResult:
        raw = _mapping(value, "run result")
        _allowed(
            raw,
            {
                "status",
                "timing",
                "thread_id",
                "execution_time",
                "adapter_response",
                "message",
                "failures",
                "unique_id",
                "compiled",
                "compiled_code",
                "relation_name",
                "batch_results",
            },
            "run result",
        )
        _required_fields(
            raw,
            {
                "status",
                "timing",
                "thread_id",
                "execution_time",
                "adapter_response",
                "message",
                "failures",
                "unique_id",
                "compiled",
                "compiled_code",
                "relation_name",
            },
            "run result",
        )
        unique_id = _required_string(raw, "unique_id")
        _required_string(raw, "thread_id")
        _mapping(raw.get("adapter_response"), "run result adapter_response")
        _nullable_string(raw.get("message"), "message")
        _nullable_string(raw.get("compiled_code"), "compiled_code")
        _nullable_string(raw.get("relation_name"), "relation_name")
        compiled = raw.get("compiled")
        if compiled is not None and not isinstance(compiled, bool):
            raise DbtSupplementalArtifactValidationError(
                "dbt run result compiled must be a boolean or null"
            )
        batch_results = raw.get("batch_results")
        if batch_results is not None and not isinstance(batch_results, Mapping):
            raise DbtSupplementalArtifactValidationError(
                "dbt run result batch_results must be an object or null"
            )
        status_value = _required_string(raw, "status")
        try:
            status = _KNOWN_RUN_STATUSES[status_value]
        except KeyError as error:
            raise DbtSupplementalArtifactValidationError(
                "dbt run result status is unsupported"
            ) from error
        timing_value = raw.get("timing")
        if not isinstance(timing_value, list):
            raise DbtSupplementalArtifactValidationError("dbt run result timing must be an array")
        if len(timing_value) > request.limits.max_timings_per_result:
            raise DbtSupplementalArtifactLimitError(
                "dbt run result exceeds the configured timing limit"
            )
        timings = tuple(DbtRunResultsParser._timing(item) for item in timing_value)
        failures = raw.get("failures")
        if failures is not None and (not isinstance(failures, int) or isinstance(failures, bool)):
            raise DbtSupplementalArtifactValidationError(
                "dbt run result failures must be an integer or null"
            )
        return DbtNodeRunResult(
            unique_id=DbtNodeId(unique_id),
            status=status,
            execution_time_seconds=_finite_non_negative(
                raw.get("execution_time"), "execution_time"
            ),
            failures=failures,
            timing=timings,
            evidence=_evidence(content_digest, request, f"run-result:{unique_id}"),
        )

    @staticmethod
    def _timing(value: object) -> DbtRunTiming:
        raw = _mapping(value, "run timing")
        _allowed(raw, {"name", "started_at", "completed_at"}, "run timing")
        return DbtRunTiming(
            name=_required_string(raw, "name"),
            started_at=_optional_timestamp(raw.get("started_at"), "started_at"),
            completed_at=_optional_timestamp(raw.get("completed_at"), "completed_at"),
        )


def _decode(
    raw: bytes | str, request: DbtSupplementalParseRequest, artifact_name: str
) -> tuple[dict[str, object], bytes]:
    encoded = raw if isinstance(raw, bytes) else raw.encode("utf-8")
    if len(encoded) > request.limits.max_artifact_bytes:
        raise DbtSupplementalArtifactLimitError(
            f"dbt {artifact_name} exceeds the configured byte limit"
        )
    try:
        decoded = encoded.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DbtSupplementalArtifactValidationError(
            f"dbt {artifact_name} must be UTF-8 JSON"
        ) from error
    try:
        payload = json.loads(decoded, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as error:
        raise DbtSupplementalArtifactValidationError(
            f"dbt {artifact_name} is not valid JSON"
        ) from error
    if not isinstance(payload, dict):
        raise DbtSupplementalArtifactValidationError(
            f"dbt {artifact_name} top level must be an object"
        )
    value = cast(dict[str, object], payload)
    _check_strings(value, request.limits.max_string_length)
    return value, encoded


def _metadata(
    value: Mapping[str, object],
    supported_schema: str,
    encoded: bytes,
    request: DbtSupplementalParseRequest,
) -> DbtSupplementalArtifactMetadata:
    _allowed(
        value,
        {
            "dbt_schema_version",
            "dbt_version",
            "generated_at",
            "invocation_id",
            "invocation_started_at",
            "env",
        },
        "supplemental dbt metadata",
    )
    schema = _required_string(value, "dbt_schema_version")
    if schema != supported_schema:
        raise UnsupportedDbtSupplementalSchema(
            f"unsupported supplemental dbt schema version: {schema!r}"
        )
    _optional_timestamp(value.get("invocation_started_at"), "invocation_started_at")
    environment = value.get("env")
    if environment is not None:
        environment_map = _mapping(environment, "supplemental dbt metadata env")
        if not all(isinstance(item, str) for item in environment_map.values()):
            raise DbtSupplementalArtifactValidationError(
                "supplemental dbt metadata env values must be strings"
            )
    return DbtSupplementalArtifactMetadata(
        schema_version=schema,
        dbt_version=_optional_string(value.get("dbt_version"), "dbt_version"),
        generated_at=_optional_timestamp(value.get("generated_at"), "generated_at"),
        invocation_id=_optional_string(value.get("invocation_id"), "invocation_id"),
        content_digest=sha256(encoded).hexdigest(),
        normalized_digest="0" * 64,
        source_identity=request.source_identity,
        ingested_at=request.ingested_at,
    )


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise DbtSupplementalArtifactValidationError(f"{name} must be an object")
    return cast(Mapping[str, object], value)


def _object_map(value: object, name: str) -> Mapping[str, object]:
    return _mapping(value, name)


def _allowed(value: Mapping[str, object], fields: set[str], name: str) -> None:
    if not set(value).issubset(fields):
        raise DbtSupplementalArtifactValidationError(f"{name} contains unsupported fields")


def _required_fields(value: Mapping[str, object], fields: set[str], name: str) -> None:
    if not fields.issubset(value):
        raise DbtSupplementalArtifactValidationError(f"{name} is missing required fields")


def _required_string(value: Mapping[str, object], name: str) -> str:
    item = value.get(name)
    if not isinstance(item, str) or not item.strip():
        raise DbtSupplementalArtifactValidationError(f"{name} must be a non-empty string")
    return item


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise DbtSupplementalArtifactValidationError(
            f"{name} must be a non-empty string when present"
        )
    return value


def _nullable_string(value: object, name: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise DbtSupplementalArtifactValidationError(f"{name} must be a string or null")
    return value


def _optional_timestamp(value: object, name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DbtSupplementalArtifactValidationError(f"{name} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DbtSupplementalArtifactValidationError(f"{name} must be a valid timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DbtSupplementalArtifactValidationError(f"{name} must be timezone-aware")
    return parsed


def _finite_non_negative(value: object, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise DbtSupplementalArtifactValidationError(f"{name} must be finite and non-negative")
    return float(value)


def _check_strings(value: object, maximum: int) -> None:
    if isinstance(value, str):
        if len(value) > maximum:
            raise DbtSupplementalArtifactLimitError(
                "dbt supplemental artifact contains a string above the configured limit"
            )
    elif isinstance(value, Mapping):
        for key, nested in value.items():
            _check_strings(key, maximum)
            _check_strings(nested, maximum)
    elif isinstance(value, list):
        for nested in value:
            _check_strings(nested, maximum)


def _evidence(
    content_digest: str, request: DbtSupplementalParseRequest, fragment: str
) -> EvidenceReference:
    source_id = SourceId(uuid5(_EVIDENCE_NAMESPACE, request.source_identity))
    evidence_id = EvidenceId(
        uuid5(_EVIDENCE_NAMESPACE, f"{request.scope.to_dict()}:{content_digest}:{fragment}")
    )
    return EvidenceReference(
        evidence_id=evidence_id,
        source_id=source_id,
        source_type=EvidenceSourceType.DBT_ARTIFACT,
        trust_class=SourceTrustClass.CURRENT_STRUCTURAL,
        immutable_source_ref=f"{request.source_identity}@sha256:{content_digest}#{fragment}",
        content_hash=f"sha256:{content_digest}",
        location=EvidenceLocation(f"dbt://{request.source_identity}#{fragment}"),
        observed_at=request.ingested_at,
        verification_status=VerificationStatus.VERIFIED,
    )


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"invalid JSON numeric constant: {value}")
