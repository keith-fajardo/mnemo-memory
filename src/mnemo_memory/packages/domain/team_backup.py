"""Content-free integrity manifest for one complete team PostgreSQL backup."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Self
from uuid import UUID, uuid5

from .identifiers import EventId

TEAM_BACKUP_FORMAT_V1 = "mnemo.team-backup.v1"
TEAM_BACKUP_FORMAT = "mnemo.team-backup.v2"
_BACKUP_NAMESPACE = UUID("713eef93-161a-4482-a25c-f8db5ffbd220")
_TABLE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True, order=True)
class TeamBackupTableCount:
    table_name: str
    row_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.table_name, str) or _TABLE_NAME.fullmatch(self.table_name) is None:
            raise ValueError("team backup table name is invalid")
        if (
            not isinstance(self.row_count, int)
            or isinstance(self.row_count, bool)
            or self.row_count < 0
        ):
            raise ValueError("team backup row count is invalid")

    def to_dict(self) -> dict[str, object]:
        return {"table_name": self.table_name, "row_count": self.row_count}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        if set(value) != {"table_name", "row_count"}:
            raise ValueError("team backup table count fields are invalid")
        return cls(str(value["table_name"]), value["row_count"])  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class TeamBackupManifest:
    format_version: str
    backup_id: EventId
    created_at: datetime
    schema_version: int
    artifact_name: str
    artifact_digest: str
    size_bytes: int
    table_counts: tuple[TeamBackupTableCount, ...]
    erasure_counts: tuple[TeamBackupTableCount, ...] = ()

    def __post_init__(self) -> None:
        if self.format_version not in {TEAM_BACKUP_FORMAT_V1, TEAM_BACKUP_FORMAT}:
            raise ValueError("team backup format is unsupported")
        if not isinstance(self.backup_id, EventId):
            raise TypeError("team backup identity is invalid")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("team backup timestamp must be timezone-aware")
        if (
            not isinstance(self.schema_version, int)
            or isinstance(self.schema_version, bool)
            or self.schema_version < 1
        ):
            raise ValueError("team backup schema version is invalid")
        if (
            not isinstance(self.artifact_name, str)
            or "/" in self.artifact_name
            or "\\" in self.artifact_name
            or not self.artifact_name.endswith(".dump")
            or len(self.artifact_name) > 255
        ):
            raise ValueError("team backup artifact name is invalid")
        if (
            not isinstance(self.artifact_digest, str)
            or _DIGEST.fullmatch(self.artifact_digest) is None
        ):
            raise ValueError("team backup artifact digest is invalid")
        if (
            not isinstance(self.size_bytes, int)
            or isinstance(self.size_bytes, bool)
            or self.size_bytes < 1
        ):
            raise ValueError("team backup artifact size is invalid")
        counts = tuple(self.table_counts)
        if (
            not counts
            or len(counts) > 128
            or counts != tuple(sorted(counts))
            or len({item.table_name for item in counts}) != len(counts)
            or "schema_migrations" not in {item.table_name for item in counts}
        ):
            raise ValueError("team backup table inventory is invalid")
        object.__setattr__(self, "table_counts", counts)
        erasures = tuple(self.erasure_counts)
        if (
            erasures != tuple(sorted(erasures))
            or len({item.table_name for item in erasures}) != len(erasures)
            or (self.format_version == TEAM_BACKUP_FORMAT and not erasures)
            or (self.format_version == TEAM_BACKUP_FORMAT_V1 and erasures)
            or any(
                item.table_name not in {count.table_name for count in counts} for item in erasures
            )
        ):
            raise ValueError("team backup erasure inventory is invalid")
        object.__setattr__(self, "erasure_counts", erasures)
        expected_identity = (
            self.identity(self.created_at, self.artifact_digest, self.size_bytes)
            if self.format_version == TEAM_BACKUP_FORMAT_V1
            else self.version_two_identity(
                self.created_at,
                self.schema_version,
                self.artifact_name,
                self.artifact_digest,
                self.size_bytes,
                self.table_counts,
                self.erasure_counts,
            )
        )
        if self.backup_id != expected_identity:
            raise ValueError("team backup identity is invalid")

    @staticmethod
    def identity(created_at: datetime, artifact_digest: str, size_bytes: int) -> EventId:
        return EventId(
            uuid5(_BACKUP_NAMESPACE, f"{created_at.isoformat()}:{artifact_digest}:{size_bytes}")
        )

    @staticmethod
    def version_two_identity(
        created_at: datetime,
        schema_version: int,
        artifact_name: str,
        artifact_digest: str,
        size_bytes: int,
        table_counts: tuple[TeamBackupTableCount, ...],
        erasure_counts: tuple[TeamBackupTableCount, ...],
    ) -> EventId:
        content = json.dumps(
            {
                "created_at": created_at.isoformat(),
                "schema_version": schema_version,
                "artifact_name": artifact_name,
                "artifact_digest": artifact_digest,
                "size_bytes": size_bytes,
                "table_counts": [item.to_dict() for item in table_counts],
                "erasure_counts": [item.to_dict() for item in erasure_counts],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return EventId(uuid5(_BACKUP_NAMESPACE, content))

    @classmethod
    def create(
        cls,
        *,
        created_at: datetime,
        schema_version: int,
        artifact_name: str,
        artifact_digest: str,
        size_bytes: int,
        table_counts: tuple[TeamBackupTableCount, ...],
        erasure_counts: tuple[TeamBackupTableCount, ...],
    ) -> Self:
        return cls(
            TEAM_BACKUP_FORMAT,
            cls.version_two_identity(
                created_at,
                schema_version,
                artifact_name,
                artifact_digest,
                size_bytes,
                table_counts,
                erasure_counts,
            ),
            created_at,
            schema_version,
            artifact_name,
            artifact_digest,
            size_bytes,
            table_counts,
            erasure_counts,
        )

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "format_version": self.format_version,
            "backup_id": str(self.backup_id),
            "created_at": self.created_at.isoformat(),
            "schema_version": self.schema_version,
            "artifact_name": self.artifact_name,
            "artifact_digest": self.artifact_digest,
            "size_bytes": self.size_bytes,
            "table_counts": [item.to_dict() for item in self.table_counts],
        }
        if self.format_version == TEAM_BACKUP_FORMAT:
            value["erasure_counts"] = [item.to_dict() for item in self.erasure_counts]
        return value

    def canonical_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        expected = {
            "format_version",
            "backup_id",
            "created_at",
            "schema_version",
            "artifact_name",
            "artifact_digest",
            "size_bytes",
            "table_counts",
        }
        format_version = str(value.get("format_version"))
        if format_version == TEAM_BACKUP_FORMAT:
            expected.add("erasure_counts")
        if set(value) != expected or not isinstance(value["table_counts"], list):
            raise ValueError("team backup manifest fields are invalid")
        counts = value["table_counts"]
        if any(not isinstance(item, Mapping) for item in counts):
            raise TypeError("team backup table counts are invalid")
        raw_erasures = value.get("erasure_counts", [])
        if not isinstance(raw_erasures, list) or any(
            not isinstance(item, Mapping) for item in raw_erasures
        ):
            raise TypeError("team backup erasure counts are invalid")
        return cls(
            format_version,
            EventId.from_string(str(value["backup_id"])),
            datetime.fromisoformat(str(value["created_at"])),
            value["schema_version"],  # type: ignore[arg-type]
            str(value["artifact_name"]),
            str(value["artifact_digest"]),
            value["size_bytes"],  # type: ignore[arg-type]
            tuple(TeamBackupTableCount.from_dict(item) for item in counts),
            tuple(TeamBackupTableCount.from_dict(item) for item in raw_erasures),
        )

    @classmethod
    def from_json(cls, value: str) -> Self:
        parsed = json.loads(value)
        if not isinstance(parsed, Mapping):
            raise TypeError("team backup manifest must be an object")
        return cls.from_dict(parsed)
