"""Bounded evidence contracts for supplemental dbt artifacts.

These values deliberately exclude warehouse comments, statistics, adapter messages,
compiled code, environment values, and arbitrary invocation arguments.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256

from .dbt_manifest import DbtNodeId
from .models import EvidenceReference, MemoryScope, _require_aware


class DbtSupplementalArtifactError(ValueError):
    """Safe base error for catalog and run-results parsing."""


class UnsupportedDbtSupplementalSchema(DbtSupplementalArtifactError):
    pass


class DbtSupplementalArtifactValidationError(DbtSupplementalArtifactError):
    pass


class DbtSupplementalArtifactLimitError(DbtSupplementalArtifactError):
    pass


class DbtCatalogCollection(StrEnum):
    NODE = "node"
    SOURCE = "source"


class DbtRunStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    SKIPPED = "skipped"
    PARTIAL_SUCCESS = "partial_success"
    NO_OP = "no_op"
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    RUNTIME_ERROR = "runtime_error"


class DbtFreshnessStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    ERROR = "error"
    RUNTIME_ERROR = "runtime_error"


class DbtFreshnessPeriod(StrEnum):
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"


@dataclass(frozen=True, slots=True)
class DbtSupplementalArtifactMetadata:
    schema_version: str
    dbt_version: str | None
    generated_at: datetime | None
    invocation_id: str | None
    content_digest: str
    normalized_digest: str
    source_identity: str
    ingested_at: datetime

    def __post_init__(self) -> None:
        if not self.schema_version.startswith("https://schemas.getdbt.com/dbt/"):
            raise ValueError("supplemental dbt schema version must be an official schema URL")
        for field_name in ("dbt_version", "invocation_id"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{field_name} must be non-empty when present")
        for field_name in ("content_digest", "normalized_digest"):
            value = getattr(self, field_name)
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
        if not self.source_identity or self.source_identity.startswith("/"):
            raise ValueError("source_identity must be non-empty and non-absolute")
        if self.generated_at is not None:
            _require_aware(self.generated_at, "generated_at")
        _require_aware(self.ingested_at, "ingested_at")


@dataclass(frozen=True, slots=True, order=True)
class DbtCatalogColumn:
    index: int
    name: str
    data_type: str

    def __post_init__(self) -> None:
        if not isinstance(self.index, int) or isinstance(self.index, bool) or self.index < 0:
            raise ValueError("catalog column index must be a non-negative integer")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("catalog column name must be non-empty")
        if not isinstance(self.data_type, str) or not self.data_type.strip():
            raise ValueError("catalog column type must be non-empty")


@dataclass(frozen=True, slots=True)
class DbtCatalogRelation:
    unique_id: DbtNodeId
    collection: DbtCatalogCollection
    relation_type: str
    database: str | None
    schema_name: str
    name: str
    columns: tuple[DbtCatalogColumn, ...]
    evidence: EvidenceReference

    def __post_init__(self) -> None:
        if not isinstance(self.unique_id, DbtNodeId):
            raise TypeError("catalog relation requires a dbt node identity")
        if not isinstance(self.collection, DbtCatalogCollection):
            raise TypeError("catalog relation collection is invalid")
        for field_name in ("relation_type", "schema_name", "name"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"catalog relation {field_name} must be non-empty")
        if self.database is not None and (
            not isinstance(self.database, str) or not self.database.strip()
        ):
            raise ValueError("catalog relation database must be non-empty when present")
        columns = tuple(sorted(self.columns))
        if len({column.index for column in columns}) != len(columns):
            raise ValueError("catalog relation column indexes must be unique")
        if len({column.name for column in columns}) != len(columns):
            raise ValueError("catalog relation column names must be unique")
        object.__setattr__(self, "columns", columns)


@dataclass(frozen=True, slots=True)
class DbtCatalogArtifact:
    metadata: DbtSupplementalArtifactMetadata
    scope: MemoryScope
    relations: tuple[DbtCatalogRelation, ...]
    error_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, DbtSupplementalArtifactMetadata) or not isinstance(
            self.scope, MemoryScope
        ):
            raise TypeError("catalog artifact requires metadata and explicit scope")
        relations = tuple(sorted(self.relations, key=lambda item: str(item.unique_id)))
        if len({relation.unique_id for relation in relations}) != len(relations):
            raise ValueError("catalog artifact contains duplicate relation identities")
        if not isinstance(self.error_count, int) or self.error_count < 0:
            raise ValueError("catalog error count must be non-negative")
        object.__setattr__(self, "relations", relations)

    def normalized_json(self) -> str:
        value = {
            "schema_version": self.metadata.schema_version,
            "relations": [
                {
                    "unique_id": str(relation.unique_id),
                    "collection": relation.collection.value,
                    "relation_type": relation.relation_type,
                    "database": relation.database,
                    "schema_name": relation.schema_name,
                    "name": relation.name,
                    "columns": [
                        {
                            "index": column.index,
                            "name": column.name,
                            "data_type": column.data_type,
                        }
                        for column in relation.columns
                    ],
                }
                for relation in self.relations
            ],
            "error_count": self.error_count,
        }
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True, slots=True, order=True)
class DbtRunTiming:
    name: str
    started_at: datetime | None
    completed_at: datetime | None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("run timing name must be non-empty")
        if self.started_at is not None:
            _require_aware(self.started_at, "started_at")
        if self.completed_at is not None:
            _require_aware(self.completed_at, "completed_at")
        if (
            self.started_at is not None
            and self.completed_at is not None
            and self.completed_at < self.started_at
        ):
            raise ValueError("run timing completion cannot precede its start")


@dataclass(frozen=True, slots=True)
class DbtNodeRunResult:
    unique_id: DbtNodeId
    status: DbtRunStatus
    execution_time_seconds: float
    failures: int | None
    timing: tuple[DbtRunTiming, ...]
    evidence: EvidenceReference

    def __post_init__(self) -> None:
        if not isinstance(self.unique_id, DbtNodeId) or not isinstance(self.status, DbtRunStatus):
            raise TypeError("run result identity or status is invalid")
        if (
            isinstance(self.execution_time_seconds, bool)
            or not isinstance(self.execution_time_seconds, (int, float))
            or not math.isfinite(self.execution_time_seconds)
            or self.execution_time_seconds < 0
        ):
            raise ValueError("run result execution time must be finite and non-negative")
        if self.failures is not None and (
            not isinstance(self.failures, int)
            or isinstance(self.failures, bool)
            or self.failures < 0
        ):
            raise ValueError("run result failures must be a non-negative integer when present")
        timing = tuple(self.timing)
        if len({item.name for item in timing}) != len(timing):
            raise ValueError("run result timing names must be unique")
        object.__setattr__(self, "execution_time_seconds", float(self.execution_time_seconds))
        object.__setattr__(self, "timing", timing)


@dataclass(frozen=True, slots=True)
class DbtRunResultsArtifact:
    metadata: DbtSupplementalArtifactMetadata
    scope: MemoryScope
    elapsed_time_seconds: float
    command: str | None
    results: tuple[DbtNodeRunResult, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, DbtSupplementalArtifactMetadata) or not isinstance(
            self.scope, MemoryScope
        ):
            raise TypeError("run-results artifact requires metadata and explicit scope")
        if (
            isinstance(self.elapsed_time_seconds, bool)
            or not isinstance(self.elapsed_time_seconds, (int, float))
            or not math.isfinite(self.elapsed_time_seconds)
            or self.elapsed_time_seconds < 0
        ):
            raise ValueError("run-results elapsed time must be finite and non-negative")
        if self.command is not None and (
            not isinstance(self.command, str) or not self.command.strip()
        ):
            raise ValueError("run-results command must be non-empty when present")
        results = tuple(sorted(self.results, key=lambda item: str(item.unique_id)))
        if len({result.unique_id for result in results}) != len(results):
            raise ValueError("run-results artifact contains duplicate node identities")
        object.__setattr__(self, "elapsed_time_seconds", float(self.elapsed_time_seconds))
        object.__setattr__(self, "results", results)

    def normalized_json(self) -> str:
        value = {
            "schema_version": self.metadata.schema_version,
            "elapsed_time_seconds": self.elapsed_time_seconds,
            "command": self.command,
            "results": [
                {
                    "unique_id": str(result.unique_id),
                    "status": result.status.value,
                    "execution_time_seconds": result.execution_time_seconds,
                    "failures": result.failures,
                    "timing": [
                        {
                            "name": timing.name,
                            "started_at": None
                            if timing.started_at is None
                            else timing.started_at.isoformat(),
                            "completed_at": None
                            if timing.completed_at is None
                            else timing.completed_at.isoformat(),
                        }
                        for timing in result.timing
                    ],
                }
                for result in self.results
            ],
        }
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True, slots=True)
class DbtFreshnessThreshold:
    count: int
    period: DbtFreshnessPeriod

    def __post_init__(self) -> None:
        if not isinstance(self.count, int) or isinstance(self.count, bool) or self.count < 0:
            raise ValueError("freshness threshold count must be a non-negative integer")
        if not isinstance(self.period, DbtFreshnessPeriod):
            raise TypeError("freshness threshold period is invalid")


@dataclass(frozen=True, slots=True)
class DbtSourceFreshnessResult:
    unique_id: DbtNodeId
    status: DbtFreshnessStatus
    max_loaded_at: datetime | None
    snapshotted_at: datetime | None
    age_seconds: float | None
    warn_after: DbtFreshnessThreshold | None
    error_after: DbtFreshnessThreshold | None
    execution_time_seconds: float | None
    evidence: EvidenceReference

    def __post_init__(self) -> None:
        if not isinstance(self.unique_id, DbtNodeId) or not isinstance(
            self.status, DbtFreshnessStatus
        ):
            raise TypeError("freshness result identity or status is invalid")
        for field_name in ("max_loaded_at", "snapshotted_at"):
            value = getattr(self, field_name)
            if value is not None:
                _require_aware(value, field_name)
        for field_name in ("age_seconds", "execution_time_seconds"):
            value = getattr(self, field_name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"freshness {field_name} must be finite and non-negative")
            if value is not None:
                object.__setattr__(self, field_name, float(value))
        if self.status is DbtFreshnessStatus.RUNTIME_ERROR:
            if any(
                value is not None
                for value in (
                    self.max_loaded_at,
                    self.snapshotted_at,
                    self.age_seconds,
                    self.warn_after,
                    self.error_after,
                    self.execution_time_seconds,
                )
            ):
                raise ValueError("runtime freshness errors cannot carry observed warehouse data")
        elif any(
            value is None
            for value in (
                self.max_loaded_at,
                self.snapshotted_at,
                self.age_seconds,
                self.execution_time_seconds,
            )
        ):
            raise ValueError("successful freshness observations require timestamps and durations")
        if not isinstance(self.evidence, EvidenceReference):
            raise TypeError("freshness result evidence is invalid")


@dataclass(frozen=True, slots=True)
class DbtSourceFreshnessArtifact:
    metadata: DbtSupplementalArtifactMetadata
    scope: MemoryScope
    elapsed_time_seconds: float
    results: tuple[DbtSourceFreshnessResult, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, DbtSupplementalArtifactMetadata) or not isinstance(
            self.scope, MemoryScope
        ):
            raise TypeError("source-freshness artifact requires metadata and explicit scope")
        if (
            isinstance(self.elapsed_time_seconds, bool)
            or not isinstance(self.elapsed_time_seconds, (int, float))
            or not math.isfinite(self.elapsed_time_seconds)
            or self.elapsed_time_seconds < 0
        ):
            raise ValueError("source-freshness elapsed time must be finite and non-negative")
        results = tuple(sorted(self.results, key=lambda item: str(item.unique_id)))
        if len({result.unique_id for result in results}) != len(results):
            raise ValueError("source-freshness artifact contains duplicate source identities")
        object.__setattr__(self, "elapsed_time_seconds", float(self.elapsed_time_seconds))
        object.__setattr__(self, "results", results)

    def normalized_json(self) -> str:
        def threshold(value: DbtFreshnessThreshold | None) -> dict[str, object] | None:
            return None if value is None else {"count": value.count, "period": value.period.value}

        value = {
            "schema_version": self.metadata.schema_version,
            "elapsed_time_seconds": self.elapsed_time_seconds,
            "results": [
                {
                    "unique_id": str(result.unique_id),
                    "status": result.status.value,
                    "max_loaded_at": None
                    if result.max_loaded_at is None
                    else result.max_loaded_at.isoformat(),
                    "snapshotted_at": None
                    if result.snapshotted_at is None
                    else result.snapshotted_at.isoformat(),
                    "age_seconds": result.age_seconds,
                    "warn_after": threshold(result.warn_after),
                    "error_after": threshold(result.error_after),
                    "execution_time_seconds": result.execution_time_seconds,
                }
                for result in self.results
            ],
        }
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def normalized_digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()
