from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class Identifier:
    """A nominal UUID value that prevents accidental cross-domain ID use."""

    value: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise TypeError("identifier value must be a UUID")

    @classmethod
    def new(cls) -> Self:
        return cls(uuid4())

    @classmethod
    def from_string(cls, value: str) -> Self:
        if not isinstance(value, str):
            raise TypeError("identifier must be serialized as a string")
        try:
            parsed = UUID(value)
        except ValueError as error:
            raise ValueError("identifier must be a canonical UUID") from error
        if str(parsed) != value:
            raise ValueError("identifier must use lowercase canonical UUID form")
        return cls(parsed)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        if set(value) != {"value"}:
            raise ValueError("identifier serialization must contain only 'value'")
        raw_value = value["value"]
        if not isinstance(raw_value, str):
            raise TypeError("identifier value must be a string")
        return cls.from_string(raw_value)

    def to_dict(self) -> dict[str, str]:
        return {"value": str(self.value)}

    def __str__(self) -> str:
        return str(self.value)


class MemoryId(Identifier):
    __slots__ = ()


class EventId(Identifier):
    __slots__ = ()


class EvidenceId(Identifier):
    __slots__ = ()


class CheckpointId(Identifier):
    __slots__ = ()


class CheckpointRevisionId(Identifier):
    __slots__ = ()


class DbtSnapshotId(Identifier):
    __slots__ = ()


class OwnerId(Identifier):
    __slots__ = ()


class WorkspaceId(Identifier):
    __slots__ = ()


class ProjectId(Identifier):
    __slots__ = ()


class SessionId(Identifier):
    __slots__ = ()


class TaskId(Identifier):
    __slots__ = ()


class AgentId(Identifier):
    __slots__ = ()


class SourceId(Identifier):
    __slots__ = ()


class RetentionPolicyId(Identifier):
    __slots__ = ()


class RequestId(Identifier):
    __slots__ = ()
