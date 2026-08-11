from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Self, cast

from .identifiers import (
    AgentId,
    CheckpointId,
    CheckpointRevisionId,
    EvidenceId,
    Identifier,
    MemoryId,
    OwnerId,
    ProjectId,
    RetentionPolicyId,
    SessionId,
    SourceId,
    TaskId,
    WorkspaceId,
)


def _require_aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _parse_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field_name} must be a valid ISO-8601 timestamp") from error
    _require_aware(parsed, field_name)
    return parsed


def _strict_fields(value: Mapping[str, object], expected: set[str], name: str) -> None:
    actual = set(value)
    if actual != expected:
        unknown = sorted(actual - expected)
        missing = sorted(expected - actual)
        raise ValueError(f"{name} fields are invalid; unknown={unknown}, missing={missing}")


def _text_items(value: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    normalized = tuple(value)
    if any(not isinstance(item, str) or not item.strip() for item in normalized):
        raise ValueError(f"{field_name} must contain non-empty strings")
    return normalized


class ScopeLevel(str, Enum):
    PERSONAL = "personal"
    WORKSPACE = "workspace"
    PROJECT = "project"
    SESSION = "session"
    TASK = "task"
    AGENT = "agent"


class Visibility(str, Enum):
    OWNER = "owner"
    WORKSPACE = "workspace"
    PROJECT = "project"


@dataclass(frozen=True, slots=True)
class MemoryScope:
    owner_id: OwnerId
    level: ScopeLevel
    visibility: Visibility
    workspace_id: WorkspaceId | None = None
    project_id: ProjectId | None = None
    session_id: SessionId | None = None
    task_id: TaskId | None = None
    agent_id: AgentId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.owner_id, OwnerId):
            raise TypeError("owner_id must be an OwnerId")
        identifiers = {
            "workspace_id": (self.workspace_id, WorkspaceId),
            "project_id": (self.project_id, ProjectId),
            "session_id": (self.session_id, SessionId),
            "task_id": (self.task_id, TaskId),
            "agent_id": (self.agent_id, AgentId),
        }
        for name, (value, identifier_type) in identifiers.items():
            if value is not None and not isinstance(value, identifier_type):
                raise TypeError(f"{name} must be a {identifier_type.__name__}")

        has = {name: value is not None for name, (value, _) in identifiers.items()}
        valid_shape = {
            ScopeLevel.PERSONAL: not any(has.values()),
            ScopeLevel.WORKSPACE: has["workspace_id"]
            and not any(has[name] for name in ("project_id", "session_id", "task_id", "agent_id")),
            ScopeLevel.PROJECT: has["project_id"]
            and not any(has[name] for name in ("session_id", "task_id", "agent_id")),
            ScopeLevel.SESSION: has["project_id"]
            and has["session_id"]
            and not has["task_id"]
            and not has["agent_id"],
            ScopeLevel.TASK: has["project_id"]
            and has["session_id"]
            and has["task_id"]
            and not has["agent_id"],
            ScopeLevel.AGENT: has["project_id"]
            and has["agent_id"]
            and not has["session_id"]
            and not has["task_id"],
        }
        if not valid_shape[self.level]:
            raise ValueError(f"invalid identifier combination for {self.level.value} scope")
        if self.level is ScopeLevel.PERSONAL and self.visibility is not Visibility.OWNER:
            raise ValueError("personal scope must have owner visibility")
        if self.visibility is Visibility.WORKSPACE and self.workspace_id is None:
            raise ValueError("workspace visibility requires workspace_id")
        if self.visibility is Visibility.PROJECT and self.project_id is None:
            raise ValueError("project visibility requires project_id")

    def to_dict(self) -> dict[str, str | None]:
        return {
            "owner_id": str(self.owner_id),
            "level": self.level.value,
            "visibility": self.visibility.value,
            "workspace_id": _optional_identifier(self.workspace_id),
            "project_id": _optional_identifier(self.project_id),
            "session_id": _optional_identifier(self.session_id),
            "task_id": _optional_identifier(self.task_id),
            "agent_id": _optional_identifier(self.agent_id),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        fields = {
            "owner_id",
            "level",
            "visibility",
            "workspace_id",
            "project_id",
            "session_id",
            "task_id",
            "agent_id",
        }
        _strict_fields(value, fields, "memory scope")
        return cls(
            owner_id=_identifier_from_value(value["owner_id"], OwnerId, "owner_id"),
            level=ScopeLevel(_string_value(value["level"], "level")),
            visibility=Visibility(_string_value(value["visibility"], "visibility")),
            workspace_id=_optional_identifier_from_value(
                value["workspace_id"], WorkspaceId, "workspace_id"
            ),
            project_id=_optional_identifier_from_value(
                value["project_id"], ProjectId, "project_id"
            ),
            session_id=_optional_identifier_from_value(
                value["session_id"], SessionId, "session_id"
            ),
            task_id=_optional_identifier_from_value(value["task_id"], TaskId, "task_id"),
            agent_id=_optional_identifier_from_value(value["agent_id"], AgentId, "agent_id"),
        )


class Sensitivity(str, Enum):
    NORMAL = "normal"
    PERSONAL = "personal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"
    PROHIBITED = "prohibited"


class MemoryStatus(str, Enum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"
    EXPIRED = "expired"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class MemoryClassification:
    sensitivity: Sensitivity
    status: MemoryStatus

    def __post_init__(self) -> None:
        if self.sensitivity is Sensitivity.PROHIBITED and self.status is not MemoryStatus.REJECTED:
            raise ValueError("prohibited content must be rejected and cannot become memory")

    @property
    def can_be_embedded(self) -> bool:
        return self.sensitivity is not Sensitivity.PROHIBITED and self.status is MemoryStatus.ACTIVE

    @property
    def can_enter_context(self) -> bool:
        return self.can_be_embedded

    def activate(self) -> Self:
        if self.sensitivity is Sensitivity.PROHIBITED:
            raise ValueError("prohibited content cannot be activated")
        if self.status is not MemoryStatus.CANDIDATE:
            raise ValueError("only candidate memory can be activated")
        return replace(self, status=MemoryStatus.ACTIVE)

    def to_dict(self) -> dict[str, str]:
        return {"sensitivity": self.sensitivity.value, "status": self.status.value}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        _strict_fields(value, {"sensitivity", "status"}, "memory classification")
        return cls(
            sensitivity=Sensitivity(_string_value(value["sensitivity"], "sensitivity")),
            status=MemoryStatus(_string_value(value["status"], "status")),
        )


@dataclass(frozen=True, slots=True)
class RetentionSchedule:
    policy_id: RetentionPolicyId
    permanent: bool
    created_at: datetime
    observed_at: datetime
    valid_from: datetime
    valid_to: datetime | None
    expires_at: datetime | None
    expired_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, RetentionPolicyId):
            raise TypeError("policy_id must be a RetentionPolicyId")
        for field_name in ("created_at", "observed_at", "valid_from"):
            _require_aware(getattr(self, field_name), field_name)
        for field_name in ("valid_to", "expires_at", "expired_at"):
            value = getattr(self, field_name)
            if value is not None:
                _require_aware(value, field_name)
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to cannot precede valid_from")
        if self.permanent:
            if self.expires_at is not None or self.expired_at is not None:
                raise ValueError("permanent retention cannot include expiry timestamps")
        elif self.expires_at is None:
            raise ValueError("non-permanent retention requires an explicit expires_at")
        elif self.expires_at < self.created_at:
            raise ValueError("expires_at cannot precede created_at")
        if (
            self.expired_at is not None
            and self.expires_at is not None
            and self.expired_at < self.expires_at
        ):
            raise ValueError("expired_at cannot precede expires_at")

    def is_expired(self, at: datetime) -> bool:
        _require_aware(at, "at")
        return self.expired_at is not None or (
            self.expires_at is not None and at >= self.expires_at
        )

    def expire(self, at: datetime) -> Self:
        _require_aware(at, "at")
        if self.permanent:
            raise ValueError("permanent retention cannot expire without an explicit policy change")
        if self.expires_at is None or at < self.expires_at:
            raise ValueError("retention cannot expire before expires_at")
        return replace(self, expired_at=at)

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_id": str(self.policy_id),
            "permanent": self.permanent,
            "created_at": self.created_at.isoformat(),
            "observed_at": self.observed_at.isoformat(),
            "valid_from": self.valid_from.isoformat(),
            "valid_to": _optional_datetime(self.valid_to),
            "expires_at": _optional_datetime(self.expires_at),
            "expired_at": _optional_datetime(self.expired_at),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        fields = {
            "policy_id",
            "permanent",
            "created_at",
            "observed_at",
            "valid_from",
            "valid_to",
            "expires_at",
            "expired_at",
        }
        _strict_fields(value, fields, "retention schedule")
        permanent = value["permanent"]
        if not isinstance(permanent, bool):
            raise TypeError("permanent must be a boolean")
        return cls(
            policy_id=_identifier_from_value(value["policy_id"], RetentionPolicyId, "policy_id"),
            permanent=permanent,
            created_at=_parse_datetime(value["created_at"], "created_at"),
            observed_at=_parse_datetime(value["observed_at"], "observed_at"),
            valid_from=_parse_datetime(value["valid_from"], "valid_from"),
            valid_to=_optional_datetime_from_value(value["valid_to"], "valid_to"),
            expires_at=_optional_datetime_from_value(value["expires_at"], "expires_at"),
            expired_at=_optional_datetime_from_value(value["expired_at"], "expired_at"),
        )


class EvidenceSourceType(str, Enum):
    USER_DOCUMENT = "user_document"
    USER_CORRECTION = "user_correction"
    REPOSITORY = "repository"
    DBT_ARTIFACT = "dbt_artifact"
    TOOL_RESULT = "tool_result"
    AGENT_EVENT = "agent_event"
    CHECKPOINT = "checkpoint"
    EXTERNAL_DOCUMENT = "external_document"
    ASSISTANT_INFERENCE = "assistant_inference"


class SourceTrustClass(str, Enum):
    CURRENT_STRUCTURAL = "current_structural"
    USER_CORRECTION = "user_correction"
    USER_AUTHORED = "user_authored"
    APPROVED_CHECKPOINT = "approved_checkpoint"
    VERIFIED_TOOL_RESULT = "verified_tool_result"
    EXTERNAL = "external"
    ASSISTANT_INFERENCE = "assistant_inference"


class VerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class EvidenceLocation:
    uri: str
    start_line: int | None = None
    start_column: int | None = None
    end_line: int | None = None
    end_column: int | None = None

    def __post_init__(self) -> None:
        if not self.uri.strip():
            raise ValueError("evidence location uri must not be blank")
        span = (self.start_line, self.start_column, self.end_line, self.end_column)
        if any(value is None for value in span):
            if any(value is not None for value in span):
                raise ValueError("evidence span must provide all start and end positions")
            return
        assert self.start_line is not None
        assert self.start_column is not None
        assert self.end_line is not None
        assert self.end_column is not None
        if self.start_line < 1 or self.end_line < 1 or self.start_column < 0 or self.end_column < 0:
            raise ValueError("evidence span positions are invalid")
        if (self.end_line, self.end_column) < (self.start_line, self.start_column):
            raise ValueError("evidence span end cannot precede start")

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {"uri": self.uri}
        if self.start_line is not None:
            value.update(
                {
                    "start_line": self.start_line,
                    "start_column": self.start_column,
                    "end_line": self.end_line,
                    "end_column": self.end_column,
                }
            )
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        fields = {"uri", "start_line", "start_column", "end_line", "end_column"}
        if set(value) not in ({"uri"}, fields):
            _strict_fields(value, fields, "evidence location")
        uri = _string_value(value["uri"], "uri")
        positions = {
            name: _optional_int_value(value.get(name), name)
            for name in ("start_line", "start_column", "end_line", "end_column")
        }
        return cls(uri=uri, **positions)


_CONTENT_HASH = re.compile(r"sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    evidence_id: EvidenceId
    source_id: SourceId
    source_type: EvidenceSourceType
    trust_class: SourceTrustClass
    immutable_source_ref: str
    content_hash: str
    location: EvidenceLocation
    observed_at: datetime
    verification_status: VerificationStatus

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_id, EvidenceId) or not isinstance(self.source_id, SourceId):
            raise TypeError("evidence and source identifiers must use their nominal ID types")
        if not self.immutable_source_ref.strip():
            raise ValueError("immutable_source_ref must not be blank")
        if not _CONTENT_HASH.fullmatch(self.content_hash):
            raise ValueError("content_hash must be a lowercase sha256 digest")
        if not isinstance(self.location, EvidenceLocation):
            raise TypeError("location must be an EvidenceLocation")
        _require_aware(self.observed_at, "observed_at")

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_id": str(self.evidence_id),
            "source_id": str(self.source_id),
            "source_type": self.source_type.value,
            "trust_class": self.trust_class.value,
            "immutable_source_ref": self.immutable_source_ref,
            "content_hash": self.content_hash,
            "location": self.location.to_dict(),
            "observed_at": self.observed_at.isoformat(),
            "verification_status": self.verification_status.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        fields = {
            "evidence_id",
            "source_id",
            "source_type",
            "trust_class",
            "immutable_source_ref",
            "content_hash",
            "location",
            "observed_at",
            "verification_status",
        }
        _strict_fields(value, fields, "evidence reference")
        location = value["location"]
        if not isinstance(location, Mapping):
            raise TypeError("location must be an object")
        return cls(
            evidence_id=_identifier_from_value(value["evidence_id"], EvidenceId, "evidence_id"),
            source_id=_identifier_from_value(value["source_id"], SourceId, "source_id"),
            source_type=EvidenceSourceType(_string_value(value["source_type"], "source_type")),
            trust_class=SourceTrustClass(_string_value(value["trust_class"], "trust_class")),
            immutable_source_ref=_string_value(
                value["immutable_source_ref"], "immutable_source_ref"
            ),
            content_hash=_string_value(value["content_hash"], "content_hash"),
            location=EvidenceLocation.from_dict(location),
            observed_at=_parse_datetime(value["observed_at"], "observed_at"),
            verification_status=VerificationStatus(
                _string_value(value["verification_status"], "verification_status")
            ),
        )


@dataclass(frozen=True, slots=True)
class DurableClaim:
    memory_id: MemoryId
    scope: MemoryScope
    classification: MemoryClassification
    retention: RetentionSchedule
    claim: str
    evidence_references: tuple[EvidenceReference, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.memory_id, MemoryId):
            raise TypeError("memory_id must be a MemoryId")
        if not isinstance(self.scope, MemoryScope):
            raise TypeError("scope must be a MemoryScope")
        if not isinstance(self.classification, MemoryClassification):
            raise TypeError("classification must be a MemoryClassification")
        if not isinstance(self.retention, RetentionSchedule):
            raise TypeError("retention must be a RetentionSchedule")
        if not self.claim.strip():
            raise ValueError("durable claim must not be blank")
        evidence = tuple(self.evidence_references)
        if not evidence or any(not isinstance(item, EvidenceReference) for item in evidence):
            raise ValueError("every durable claim requires structurally valid evidence references")
        object.__setattr__(self, "evidence_references", evidence)

    def activate(self) -> Self:
        return replace(self, classification=self.classification.activate())

    def to_dict(self) -> dict[str, object]:
        return {
            "memory_id": str(self.memory_id),
            "scope": self.scope.to_dict(),
            "classification": self.classification.to_dict(),
            "retention": self.retention.to_dict(),
            "claim": self.claim,
            "evidence_references": [item.to_dict() for item in self.evidence_references],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        fields = {
            "memory_id",
            "scope",
            "classification",
            "retention",
            "claim",
            "evidence_references",
        }
        _strict_fields(value, fields, "durable claim")
        scope = value["scope"]
        classification = value["classification"]
        retention = value["retention"]
        evidence = value["evidence_references"]
        if not all(isinstance(item, Mapping) for item in (scope, classification, retention)):
            raise TypeError("durable claim nested objects must be mappings")
        if not isinstance(evidence, list) or not all(
            isinstance(item, Mapping) for item in evidence
        ):
            raise TypeError("evidence_references must be an array of objects")
        scope_mapping = cast(Mapping[str, object], scope)
        classification_mapping = cast(Mapping[str, object], classification)
        retention_mapping = cast(Mapping[str, object], retention)
        return cls(
            memory_id=_identifier_from_value(value["memory_id"], MemoryId, "memory_id"),
            scope=MemoryScope.from_dict(scope_mapping),
            classification=MemoryClassification.from_dict(classification_mapping),
            retention=RetentionSchedule.from_dict(retention_mapping),
            claim=_string_value(value["claim"], "claim"),
            evidence_references=tuple(EvidenceReference.from_dict(item) for item in evidence),
        )


class CheckpointStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"


_CHECKPOINT_LESSON_TEXT_LIMIT = 1_000
_CHECKPOINT_LESSON_METADATA_LIMIT = 16


@dataclass(frozen=True, slots=True)
class CheckpointLesson:
    """An evidence-backed correction that helps a later task avoid repeating a mistake.

    A lesson deliberately records the *reasoning* boundary, rather than merely a
    failed command: what triggered the error, the assumption that was wrong, the
    correction, and the concrete prevention step.  It is identity-free content
    belonging to the revision that records it.
    """

    trigger: str
    mistaken_assumption: str
    correction: str
    prevention: str
    evidence_ids: tuple[EvidenceId, ...]

    def __post_init__(self) -> None:
        for name in ("trigger", "mistaken_assumption", "correction", "prevention"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"checkpoint lesson {name} must not be blank")
            if len(value) > _CHECKPOINT_LESSON_TEXT_LIMIT:
                raise ValueError(
                    f"checkpoint lesson {name} exceeds {_CHECKPOINT_LESSON_TEXT_LIMIT} characters"
                )

        evidence_ids = tuple(self.evidence_ids)
        if not evidence_ids or any(not isinstance(item, EvidenceId) for item in evidence_ids):
            raise ValueError("checkpoint lesson requires evidence identifiers")
        if len(evidence_ids) > _CHECKPOINT_LESSON_METADATA_LIMIT:
            raise ValueError("checkpoint lesson has too many evidence identifiers")
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("checkpoint lesson evidence identifiers must be unique")
        object.__setattr__(self, "evidence_ids", evidence_ids)

    def to_dict(self) -> dict[str, object]:
        return {
            "trigger": self.trigger,
            "mistaken_assumption": self.mistaken_assumption,
            "correction": self.correction,
            "prevention": self.prevention,
            "evidence_ids": [str(item) for item in self.evidence_ids],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        fields = {"trigger", "mistaken_assumption", "correction", "prevention", "evidence_ids"}
        _strict_fields(value, fields, "checkpoint lesson")
        evidence_ids = value["evidence_ids"]
        if not isinstance(evidence_ids, list):
            raise TypeError("checkpoint lesson evidence_ids must be an array")
        return cls(
            trigger=_string_value(value["trigger"], "checkpoint lesson trigger"),
            mistaken_assumption=_string_value(
                value["mistaken_assumption"], "checkpoint lesson mistaken_assumption"
            ),
            correction=_string_value(value["correction"], "checkpoint lesson correction"),
            prevention=_string_value(value["prevention"], "checkpoint lesson prevention"),
            evidence_ids=tuple(
                _identifier_from_value(item, EvidenceId, "checkpoint lesson evidence_id")
                for item in evidence_ids
            ),
        )


@dataclass(frozen=True, slots=True)
class CheckpointContent:
    """Identity-free, immutable payload of one checkpoint revision."""

    task_objective: str
    completed_work: tuple[str, ...]
    current_state: str
    remaining_work: tuple[str, ...]
    decisions: tuple[str, ...]
    failures: tuple[str, ...]
    blockers: tuple[str, ...]
    relevant_files: tuple[str, ...]
    relevant_artifacts: tuple[str, ...]
    verification_performed: tuple[str, ...]
    token_estimate: int
    lessons: tuple[CheckpointLesson, ...] = ()

    def __post_init__(self) -> None:
        if not self.task_objective.strip() or not self.current_state.strip():
            raise ValueError("checkpoint objective and current_state must not be blank")
        if self.token_estimate < 0:
            raise ValueError("token_estimate cannot be negative")
        lessons = tuple(self.lessons)
        if len(lessons) > _CHECKPOINT_LESSON_METADATA_LIMIT:
            raise ValueError("checkpoint content has too many lessons")
        if any(not isinstance(item, CheckpointLesson) for item in lessons):
            raise TypeError("checkpoint lessons must be CheckpointLesson values")
        if len(set(lessons)) != len(lessons):
            raise ValueError("checkpoint lessons must be unique")
        object.__setattr__(self, "lessons", lessons)
        for name in (
            "completed_work",
            "remaining_work",
            "decisions",
            "failures",
            "blockers",
            "relevant_files",
            "relevant_artifacts",
            "verification_performed",
        ):
            object.__setattr__(self, name, _text_items(getattr(self, name), name))

    @classmethod
    def from_legacy(cls, checkpoint: Checkpoint) -> Self:
        return cls(
            checkpoint.task_objective,
            checkpoint.completed_work,
            checkpoint.current_state,
            checkpoint.remaining_work,
            checkpoint.decisions,
            checkpoint.failures,
            checkpoint.blockers,
            checkpoint.relevant_files,
            checkpoint.relevant_artifacts,
            checkpoint.verification_performed,
            checkpoint.token_estimate,
        )

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "task_objective": self.task_objective,
            "current_state": self.current_state,
            "token_estimate": self.token_estimate,
        }
        for name in (
            "completed_work",
            "remaining_work",
            "decisions",
            "failures",
            "blockers",
            "relevant_files",
            "relevant_artifacts",
            "verification_performed",
        ):
            items = getattr(self, name)
            if items:
                value[name] = list(items)
        if self.lessons:
            value["lessons"] = [item.to_dict() for item in self.lessons]
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        fields = set(cls.__dataclass_fields__)
        required = {"task_objective", "current_state", "token_estimate"}
        actual = set(value)
        if not required.issubset(actual) or not actual.issubset(fields):
            unknown = sorted(actual - fields)
            missing = sorted(required - actual)
            raise ValueError(
                f"checkpoint content fields are invalid; unknown={unknown}, missing={missing}"
            )
        lessons = value.get("lessons", [])
        if not isinstance(lessons, list) or not all(isinstance(item, Mapping) for item in lessons):
            raise TypeError("checkpoint content lessons must be an array of objects")
        return cls(
            _string_value(value["task_objective"], "task_objective"),
            _string_tuple(value.get("completed_work", []), "completed_work"),
            _string_value(value["current_state"], "current_state"),
            _string_tuple(value.get("remaining_work", []), "remaining_work"),
            _string_tuple(value.get("decisions", []), "decisions"),
            _string_tuple(value.get("failures", []), "failures"),
            _string_tuple(value.get("blockers", []), "blockers"),
            _string_tuple(value.get("relevant_files", []), "relevant_files"),
            _string_tuple(value.get("relevant_artifacts", []), "relevant_artifacts"),
            _string_tuple(value.get("verification_performed", []), "verification_performed"),
            _int_value(value["token_estimate"], "token_estimate"),
            tuple(CheckpointLesson.from_dict(item) for item in lessons),
        )


@dataclass(frozen=True, slots=True)
class CheckpointRevision:
    """Immutable content revision under a stable logical checkpoint identity."""

    revision_id: CheckpointRevisionId
    checkpoint_id: CheckpointId
    revision_number: int
    predecessor_revision_id: CheckpointRevisionId | None
    scope: MemoryScope
    content: CheckpointContent
    status: CheckpointStatus
    evidence_references: tuple[EvidenceReference, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.revision_id, CheckpointRevisionId):
            raise TypeError("revision_id must be a CheckpointRevisionId")
        if not isinstance(self.checkpoint_id, CheckpointId):
            raise TypeError("checkpoint_id must be a CheckpointId")
        if self.revision_number < 1:
            raise ValueError("revision_number must be positive")
        if self.revision_number == 1 and self.predecessor_revision_id is not None:
            raise ValueError("initial revision cannot have a predecessor")
        if self.revision_number > 1 and self.predecessor_revision_id is None:
            raise ValueError("later revision requires a predecessor")
        if self.predecessor_revision_id is not None and not isinstance(
            self.predecessor_revision_id, CheckpointRevisionId
        ):
            raise TypeError("predecessor_revision_id must be a CheckpointRevisionId")
        if not isinstance(self.scope, MemoryScope):
            raise TypeError("scope must be a MemoryScope")
        if not isinstance(self.content, CheckpointContent):
            raise TypeError("content must be a CheckpointContent")
        if not isinstance(self.status, CheckpointStatus):
            raise TypeError("status must be a CheckpointStatus")
        if not self.evidence_references or any(
            not isinstance(item, EvidenceReference) for item in self.evidence_references
        ):
            raise ValueError("revision requires structurally valid evidence references")
        evidence_references = tuple(self.evidence_references)
        evidence_ids = {item.evidence_id for item in evidence_references}
        if any(
            not set(lesson.evidence_ids).issubset(evidence_ids) for lesson in self.content.lessons
        ):
            raise ValueError("checkpoint lesson evidence must belong to its revision")
        object.__setattr__(self, "evidence_references", evidence_references)
        _require_aware(self.created_at, "created_at")

    def to_dict(self) -> dict[str, object]:
        return {
            "checkpoint_revision_id": str(self.revision_id),
            "checkpoint_id": str(self.checkpoint_id),
            "revision_number": self.revision_number,
            "predecessor_revision_id": _optional_identifier(self.predecessor_revision_id),
            "scope": self.scope.to_dict(),
            "content": self.content.to_dict(),
            "status": self.status.value,
            "evidence_references": [item.to_dict() for item in self.evidence_references],
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        fields = {
            "checkpoint_revision_id",
            "checkpoint_id",
            "revision_number",
            "predecessor_revision_id",
            "scope",
            "content",
            "status",
            "evidence_references",
            "created_at",
        }
        _strict_fields(value, fields, "checkpoint revision")
        scope = value["scope"]
        content = value["content"]
        evidence = value["evidence_references"]
        if not isinstance(scope, Mapping) or not isinstance(content, Mapping):
            raise TypeError("checkpoint revision scope and content must be objects")
        if not isinstance(evidence, list) or not all(
            isinstance(item, Mapping) for item in evidence
        ):
            raise TypeError("checkpoint revision evidence_references must be an array of objects")
        return cls(
            revision_id=_identifier_from_value(
                value["checkpoint_revision_id"], CheckpointRevisionId, "checkpoint_revision_id"
            ),
            checkpoint_id=_identifier_from_value(
                value["checkpoint_id"], CheckpointId, "checkpoint_id"
            ),
            revision_number=_int_value(value["revision_number"], "revision_number"),
            predecessor_revision_id=_optional_identifier_from_value(
                value["predecessor_revision_id"],
                CheckpointRevisionId,
                "predecessor_revision_id",
            ),
            scope=MemoryScope.from_dict(scope),
            content=CheckpointContent.from_dict(content),
            status=CheckpointStatus(_string_value(value["status"], "status")),
            evidence_references=tuple(EvidenceReference.from_dict(item) for item in evidence),
            created_at=_parse_datetime(value["created_at"], "created_at"),
        )


@dataclass(frozen=True, slots=True)
class CheckpointAggregate:
    checkpoint_id: CheckpointId
    scope: MemoryScope
    current_revision_id: CheckpointRevisionId
    current_revision_number: int
    lifecycle_status: CheckpointStatus
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoint_id, CheckpointId):
            raise TypeError("checkpoint_id must be a CheckpointId")
        if not isinstance(self.scope, MemoryScope):
            raise TypeError("scope must be a MemoryScope")
        if not isinstance(self.current_revision_id, CheckpointRevisionId):
            raise TypeError("current_revision_id must be a CheckpointRevisionId")
        if self.current_revision_number < 1:
            raise ValueError("current_revision_number must be positive")
        if not isinstance(self.lifecycle_status, CheckpointStatus):
            raise TypeError("lifecycle_status must be a CheckpointStatus")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")


@dataclass(frozen=True, slots=True)
class Checkpoint:
    checkpoint_id: CheckpointId
    scope: MemoryScope
    task_objective: str
    completed_work: tuple[str, ...]
    current_state: str
    remaining_work: tuple[str, ...]
    decisions: tuple[str, ...]
    failures: tuple[str, ...]
    blockers: tuple[str, ...]
    relevant_files: tuple[str, ...]
    relevant_artifacts: tuple[str, ...]
    verification_performed: tuple[str, ...]
    evidence_references: tuple[EvidenceReference, ...]
    status: CheckpointStatus
    revision: int
    supersedes_checkpoint_id: CheckpointId | None
    superseded_by_checkpoint_id: CheckpointId | None
    token_estimate: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    abandoned_at: datetime | None = None
    superseded_at: datetime | None = None
    expired_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoint_id, CheckpointId):
            raise TypeError("checkpoint_id must be a CheckpointId")
        if self.scope.level is not ScopeLevel.TASK:
            raise ValueError("checkpoint scope must be an explicit task scope")
        if not self.task_objective.strip() or not self.current_state.strip():
            raise ValueError("checkpoint objective and current_state must not be blank")
        for field_name in (
            "completed_work",
            "remaining_work",
            "decisions",
            "failures",
            "blockers",
            "relevant_files",
            "relevant_artifacts",
            "verification_performed",
        ):
            object.__setattr__(self, field_name, _text_items(getattr(self, field_name), field_name))
        evidence = tuple(self.evidence_references)
        if not evidence or any(not isinstance(item, EvidenceReference) for item in evidence):
            raise ValueError("checkpoint requires structurally valid evidence references")
        object.__setattr__(self, "evidence_references", evidence)
        if self.revision < 1:
            raise ValueError("checkpoint revision must be positive")
        if self.revision == 1 and self.supersedes_checkpoint_id is not None:
            raise ValueError("first checkpoint revision cannot supersede another checkpoint")
        if self.revision > 1 and self.supersedes_checkpoint_id is None:
            raise ValueError("revised checkpoint must identify the replaced revision")
        if self.supersedes_checkpoint_id == self.checkpoint_id:
            raise ValueError("checkpoint cannot supersede itself")
        if self.superseded_by_checkpoint_id == self.checkpoint_id:
            raise ValueError("checkpoint cannot be superseded by itself")
        if self.token_estimate < 0:
            raise ValueError("token_estimate cannot be negative")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        terminal_timestamps = {
            CheckpointStatus.COMPLETED: self.completed_at,
            CheckpointStatus.ABANDONED: self.abandoned_at,
            CheckpointStatus.SUPERSEDED: self.superseded_at,
            CheckpointStatus.EXPIRED: self.expired_at,
        }
        for name, value in (
            ("completed_at", self.completed_at),
            ("abandoned_at", self.abandoned_at),
            ("superseded_at", self.superseded_at),
            ("expired_at", self.expired_at),
        ):
            if value is not None:
                _require_aware(value, name)
                if value < self.created_at or value > self.updated_at:
                    raise ValueError(f"{name} must be within checkpoint lifetime")
        if self.status in {CheckpointStatus.DRAFT, CheckpointStatus.ACTIVE}:
            if any(value is not None for value in terminal_timestamps.values()):
                raise ValueError("non-terminal checkpoint cannot have terminal timestamps")
            if self.superseded_by_checkpoint_id is not None:
                raise ValueError("non-superseded checkpoint cannot identify a replacement")
        else:
            required_timestamp = terminal_timestamps[self.status]
            if required_timestamp is None:
                raise ValueError(f"{self.status.value} checkpoint requires its terminal timestamp")
            for terminal_status, value in terminal_timestamps.items():
                if terminal_status is not self.status and value is not None:
                    raise ValueError("checkpoint can have only one terminal timestamp")
        if self.status is CheckpointStatus.COMPLETED:
            if self.blockers:
                raise ValueError("completed checkpoint cannot contain an active blocker")
            if self.remaining_work:
                raise ValueError("completed checkpoint cannot contain remaining work")
        if self.status is CheckpointStatus.SUPERSEDED and self.superseded_by_checkpoint_id is None:
            raise ValueError("superseded checkpoint must identify its replacement")

    def activate(self, at: datetime) -> Self:
        _require_aware(at, "at")
        if self.status is not CheckpointStatus.DRAFT:
            raise ValueError("only draft checkpoints can be activated")
        return replace(self, status=CheckpointStatus.ACTIVE, updated_at=at)

    def complete(self, at: datetime) -> Self:
        _require_aware(at, "at")
        if self.status is not CheckpointStatus.ACTIVE:
            raise ValueError("only active checkpoints can be completed")
        return replace(
            self,
            status=CheckpointStatus.COMPLETED,
            updated_at=at,
            completed_at=at,
        )

    def abandon(self, at: datetime) -> Self:
        _require_aware(at, "at")
        if self.status not in {CheckpointStatus.DRAFT, CheckpointStatus.ACTIVE}:
            raise ValueError("only draft or active checkpoints can be abandoned")
        return replace(
            self,
            status=CheckpointStatus.ABANDONED,
            updated_at=at,
            abandoned_at=at,
        )

    def expire(self, at: datetime) -> Self:
        _require_aware(at, "at")
        if self.status not in {CheckpointStatus.DRAFT, CheckpointStatus.ACTIVE}:
            raise ValueError("only draft or active checkpoints can expire")
        return replace(self, status=CheckpointStatus.EXPIRED, updated_at=at, expired_at=at)

    def supersede(self, replacement_id: CheckpointId, at: datetime) -> Self:
        _require_aware(at, "at")
        if not isinstance(replacement_id, CheckpointId):
            raise TypeError("replacement_id must be a CheckpointId")
        if replacement_id == self.checkpoint_id:
            raise ValueError("checkpoint cannot supersede itself")
        if self.status in {CheckpointStatus.SUPERSEDED, CheckpointStatus.EXPIRED}:
            raise ValueError("checkpoint is already immutable after supersession or expiry")
        return replace(
            self,
            status=CheckpointStatus.SUPERSEDED,
            updated_at=at,
            superseded_at=at,
            superseded_by_checkpoint_id=replacement_id,
            completed_at=None,
            abandoned_at=None,
            expired_at=None,
        )

    def revise(self, replacement_id: CheckpointId, at: datetime) -> tuple[Self, Self]:
        superseded = self.supersede(replacement_id, at)
        replacement = replace(
            self,
            checkpoint_id=replacement_id,
            status=CheckpointStatus.DRAFT,
            revision=self.revision + 1,
            supersedes_checkpoint_id=self.checkpoint_id,
            superseded_by_checkpoint_id=None,
            created_at=at,
            updated_at=at,
            completed_at=None,
            abandoned_at=None,
            superseded_at=None,
            expired_at=None,
        )
        return superseded, replacement

    def to_dict(self) -> dict[str, object]:
        return {
            "checkpoint_id": str(self.checkpoint_id),
            "scope": self.scope.to_dict(),
            "task_objective": self.task_objective,
            "completed_work": list(self.completed_work),
            "current_state": self.current_state,
            "remaining_work": list(self.remaining_work),
            "decisions": list(self.decisions),
            "failures": list(self.failures),
            "blockers": list(self.blockers),
            "relevant_files": list(self.relevant_files),
            "relevant_artifacts": list(self.relevant_artifacts),
            "verification_performed": list(self.verification_performed),
            "evidence_references": [item.to_dict() for item in self.evidence_references],
            "status": self.status.value,
            "revision": self.revision,
            "supersedes_checkpoint_id": _optional_identifier(self.supersedes_checkpoint_id),
            "superseded_by_checkpoint_id": _optional_identifier(self.superseded_by_checkpoint_id),
            "token_estimate": self.token_estimate,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "completed_at": _optional_datetime(self.completed_at),
            "abandoned_at": _optional_datetime(self.abandoned_at),
            "superseded_at": _optional_datetime(self.superseded_at),
            "expired_at": _optional_datetime(self.expired_at),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        fields = {
            "checkpoint_id",
            "scope",
            "task_objective",
            "completed_work",
            "current_state",
            "remaining_work",
            "decisions",
            "failures",
            "blockers",
            "relevant_files",
            "relevant_artifacts",
            "verification_performed",
            "evidence_references",
            "status",
            "revision",
            "supersedes_checkpoint_id",
            "superseded_by_checkpoint_id",
            "token_estimate",
            "created_at",
            "updated_at",
            "completed_at",
            "abandoned_at",
            "superseded_at",
            "expired_at",
        }
        _strict_fields(value, fields, "checkpoint")
        scope = value["scope"]
        evidence = value["evidence_references"]
        if not isinstance(scope, Mapping):
            raise TypeError("checkpoint scope must be an object")
        if not isinstance(evidence, list) or not all(
            isinstance(item, Mapping) for item in evidence
        ):
            raise TypeError("checkpoint evidence_references must be an array of objects")
        return cls(
            checkpoint_id=_identifier_from_value(
                value["checkpoint_id"], CheckpointId, "checkpoint_id"
            ),
            scope=MemoryScope.from_dict(scope),
            task_objective=_string_value(value["task_objective"], "task_objective"),
            completed_work=_string_tuple(value["completed_work"], "completed_work"),
            current_state=_string_value(value["current_state"], "current_state"),
            remaining_work=_string_tuple(value["remaining_work"], "remaining_work"),
            decisions=_string_tuple(value["decisions"], "decisions"),
            failures=_string_tuple(value["failures"], "failures"),
            blockers=_string_tuple(value["blockers"], "blockers"),
            relevant_files=_string_tuple(value["relevant_files"], "relevant_files"),
            relevant_artifacts=_string_tuple(value["relevant_artifacts"], "relevant_artifacts"),
            verification_performed=_string_tuple(
                value["verification_performed"], "verification_performed"
            ),
            evidence_references=tuple(EvidenceReference.from_dict(item) for item in evidence),
            status=CheckpointStatus(_string_value(value["status"], "status")),
            revision=_int_value(value["revision"], "revision"),
            supersedes_checkpoint_id=_optional_identifier_from_value(
                value["supersedes_checkpoint_id"], CheckpointId, "supersedes_checkpoint_id"
            ),
            superseded_by_checkpoint_id=_optional_identifier_from_value(
                value["superseded_by_checkpoint_id"], CheckpointId, "superseded_by_checkpoint_id"
            ),
            token_estimate=_int_value(value["token_estimate"], "token_estimate"),
            created_at=_parse_datetime(value["created_at"], "created_at"),
            updated_at=_parse_datetime(value["updated_at"], "updated_at"),
            completed_at=_optional_datetime_from_value(value["completed_at"], "completed_at"),
            abandoned_at=_optional_datetime_from_value(value["abandoned_at"], "abandoned_at"),
            superseded_at=_optional_datetime_from_value(value["superseded_at"], "superseded_at"),
            expired_at=_optional_datetime_from_value(value["expired_at"], "expired_at"),
        )


def _identifier_from_value[IdentifierType: Identifier](
    value: object,
    identifier_type: type[IdentifierType],
    field_name: str,
) -> IdentifierType:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    return identifier_type.from_string(value)


def _optional_identifier_from_value[IdentifierType: Identifier](
    value: object,
    identifier_type: type[IdentifierType],
    field_name: str,
) -> IdentifierType | None:
    if value is None:
        return None
    return _identifier_from_value(value, identifier_type, field_name)


def _optional_identifier(value: object) -> str | None:
    return None if value is None else str(value)


def _string_value(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    return value


def _int_value(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{field_name} must be an integer")
    return value


def _optional_int_value(value: object, field_name: str) -> int | None:
    return None if value is None else _int_value(value, field_name)


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"{field_name} must be an array of strings")
    return tuple(value)


def _optional_datetime(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _optional_datetime_from_value(value: object, field_name: str) -> datetime | None:
    return None if value is None else _parse_datetime(value, field_name)
