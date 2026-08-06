"""Payload-free team knowledge ownership and source-approval contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Self
from uuid import UUID, uuid5

from .identifiers import (
    EventId,
    KnowledgeDocumentId,
    KnowledgeDocumentRevisionId,
    OwnerId,
)
from .models import MemoryScope, ScopeLevel

_SOURCE_APPROVAL_NAMESPACE = UUID("fa3989f9-f4e8-49f4-a4ad-9c73cc4a7db3")


def _aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class TeamKnowledgeSourceApproval:
    """One immutable approval of a stable team source at an exact observed revision."""

    approval_id: EventId
    scope: MemoryScope
    document_id: KnowledgeDocumentId
    expected_revision_id: KnowledgeDocumentRevisionId
    approved_by_id: OwnerId
    source_action_key: str
    approved_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.approval_id, EventId):
            raise TypeError("knowledge source approval identity is invalid")
        if not isinstance(self.scope, MemoryScope) or self.scope.level is not ScopeLevel.PROJECT:
            raise ValueError("knowledge source approval requires an exact project scope")
        if not isinstance(self.document_id, KnowledgeDocumentId) or not isinstance(
            self.expected_revision_id, KnowledgeDocumentRevisionId
        ):
            raise TypeError("knowledge source approval target is invalid")
        if not isinstance(self.approved_by_id, OwnerId):
            raise TypeError("knowledge source approver is invalid")
        if (
            not isinstance(self.source_action_key, str)
            or not self.source_action_key.strip()
            or len(self.source_action_key) > 256
        ):
            raise ValueError("knowledge source approval action key is invalid")
        _aware(self.approved_at, "approved_at")
        if self.approval_id != self.identity(self.scope, self.document_id, self.source_action_key):
            raise ValueError("knowledge source approval identity is invalid")

    @staticmethod
    def identity(
        scope: MemoryScope, document_id: KnowledgeDocumentId, source_action_key: str
    ) -> EventId:
        return EventId(
            uuid5(
                _SOURCE_APPROVAL_NAMESPACE,
                f"{scope.to_dict()}:{document_id}:{source_action_key}",
            )
        )

    @classmethod
    def create(
        cls,
        *,
        scope: MemoryScope,
        document_id: KnowledgeDocumentId,
        expected_revision_id: KnowledgeDocumentRevisionId,
        approved_by_id: OwnerId,
        source_action_key: str,
        approved_at: datetime,
    ) -> Self:
        return cls(
            cls.identity(scope, document_id, source_action_key),
            scope,
            document_id,
            expected_revision_id,
            approved_by_id,
            source_action_key,
            approved_at,
        )

    def same_intent(self, other: object) -> bool:
        return isinstance(other, TeamKnowledgeSourceApproval) and (
            self.approval_id,
            self.scope,
            self.document_id,
            self.expected_revision_id,
            self.approved_by_id,
            self.source_action_key,
        ) == (
            other.approval_id,
            other.scope,
            other.document_id,
            other.expected_revision_id,
            other.approved_by_id,
            other.source_action_key,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "approval_id": str(self.approval_id),
            "scope": self.scope.to_dict(),
            "document_id": str(self.document_id),
            "expected_revision_id": str(self.expected_revision_id),
            "approved_by_id": str(self.approved_by_id),
            "source_action_key": self.source_action_key,
            "approved_at": self.approved_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        expected = {
            "approval_id",
            "scope",
            "document_id",
            "expected_revision_id",
            "approved_by_id",
            "source_action_key",
            "approved_at",
        }
        if set(value) != expected or not isinstance(value["scope"], Mapping):
            raise ValueError("knowledge source approval fields are invalid")
        if not all(isinstance(value[name], str) for name in expected - {"scope"}):
            raise TypeError("knowledge source approval fields are invalid")
        return cls(
            EventId.from_string(str(value["approval_id"])),
            MemoryScope.from_dict(value["scope"]),
            KnowledgeDocumentId.from_string(str(value["document_id"])),
            KnowledgeDocumentRevisionId.from_string(str(value["expected_revision_id"])),
            OwnerId.from_string(str(value["approved_by_id"])),
            str(value["source_action_key"]),
            datetime.fromisoformat(str(value["approved_at"])),
        )


@dataclass(frozen=True, slots=True)
class TeamKnowledgeSourceStatus:
    """Content-free current source state for review and ownership inspection."""

    scope: MemoryScope
    document_id: KnowledgeDocumentId
    relative_path: str
    current_revision_id: KnowledgeDocumentRevisionId
    revision_number: int
    source_owner_id: OwnerId
    source_owner_authenticated: bool
    current_author_id: OwnerId
    current_author_authenticated: bool
    approval: TeamKnowledgeSourceApproval | None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, MemoryScope) or self.scope.level is not ScopeLevel.PROJECT:
            raise ValueError("team knowledge source status requires an exact project scope")
        if not isinstance(self.document_id, KnowledgeDocumentId) or not isinstance(
            self.current_revision_id, KnowledgeDocumentRevisionId
        ):
            raise TypeError("team knowledge source status identity is invalid")
        if (
            not isinstance(self.relative_path, str)
            or not self.relative_path
            or self.relative_path.startswith("/")
            or ".." in self.relative_path.split("/")
        ):
            raise ValueError("team knowledge source path is invalid")
        if (
            not isinstance(self.revision_number, int)
            or isinstance(self.revision_number, bool)
            or self.revision_number < 1
        ):
            raise ValueError("team knowledge source revision number is invalid")
        if not isinstance(self.source_owner_id, OwnerId) or not isinstance(
            self.current_author_id, OwnerId
        ):
            raise TypeError("team knowledge source ownership is invalid")
        if not isinstance(self.source_owner_authenticated, bool) or not isinstance(
            self.current_author_authenticated, bool
        ):
            raise TypeError("team knowledge source attribution state is invalid")
        if self.approval is not None and (
            not isinstance(self.approval, TeamKnowledgeSourceApproval)
            or self.approval.scope != self.scope
            or self.approval.document_id != self.document_id
        ):
            raise ValueError("team knowledge source approval does not match its source")

    @property
    def approved(self) -> bool:
        return self.approval is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "scope": self.scope.to_dict(),
            "document_id": str(self.document_id),
            "relative_path": self.relative_path,
            "current_revision_id": str(self.current_revision_id),
            "revision_number": self.revision_number,
            "source_owner_id": str(self.source_owner_id),
            "source_owner_authenticated": self.source_owner_authenticated,
            "current_author_id": str(self.current_author_id),
            "current_author_authenticated": self.current_author_authenticated,
            "approved": self.approved,
            "approval": None if self.approval is None else self.approval.to_dict(),
        }
