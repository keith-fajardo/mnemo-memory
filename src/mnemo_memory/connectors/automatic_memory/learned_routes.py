"""Private project-scoped storage for explicitly taught deterministic route phrases."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID, uuid4

from mnemo_memory.packages.application.automatic_memory import (
    AutomaticMemoryBindingError,
    exclusive_local_file_lock,
)
from mnemo_memory.packages.application.context_routing import (
    CompactMemoryRoute,
    LearnedRoutePhrase,
    normalize_learned_route_phrase,
)
from mnemo_memory.packages.domain import MemoryScope, ScopeLevel, Visibility
from mnemo_memory.packages.policy.knowledge import contains_high_confidence_secret

_FORMAT_VERSION = 1
_MAXIMUM_FILE_BYTES = 65_536
_MAXIMUM_PHRASES = 64
_MAXIMUM_PHRASE_CHARACTERS = 160


class LearnedRouteStoreError(ValueError):
    """Stable, payload-free learned-route storage failure."""


@dataclass(frozen=True, slots=True)
class LearnedRouteRecord:
    record_id: UUID
    scope: MemoryScope
    phrase: str
    normalized_phrase: str
    route: CompactMemoryRoute
    created_at: datetime
    source: str = "user_cli"
    sensitivity: str = "normal"

    def __post_init__(self) -> None:
        _validate_project_scope(self.scope)
        if not isinstance(self.record_id, UUID) or self.record_id.version != 4:
            raise ValueError("learned route identity is invalid")
        if (
            not isinstance(self.phrase, str)
            or not self.phrase.strip()
            or len(self.phrase) > _MAXIMUM_PHRASE_CHARACTERS
            or self.normalized_phrase != normalize_learned_route_phrase(self.phrase)
            or not self.normalized_phrase
            or len(self.normalized_phrase) > _MAXIMUM_PHRASE_CHARACTERS
        ):
            raise ValueError("learned route phrase is invalid")
        if self.route is CompactMemoryRoute.NONE:
            raise ValueError("learned route cannot suppress memory")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("learned route timestamp is invalid")
        if self.source != "user_cli" or self.sensitivity != "normal":
            raise ValueError("learned route provenance is invalid")

    def routing_phrase(self) -> LearnedRoutePhrase:
        return LearnedRoutePhrase(self.normalized_phrase, self.route)

    def to_dict(self) -> dict[str, object]:
        return {
            "record_id": str(self.record_id),
            "scope": self.scope.to_dict(),
            "phrase": self.phrase,
            "normalized_phrase": self.normalized_phrase,
            "route": self.route.value,
            "created_at": self.created_at.astimezone(UTC).isoformat(),
            "source": self.source,
            "sensitivity": self.sensitivity,
        }

    @classmethod
    def from_dict(cls, value: object) -> LearnedRouteRecord:
        expected = {
            "record_id",
            "scope",
            "phrase",
            "normalized_phrase",
            "route",
            "created_at",
            "source",
            "sensitivity",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("learned route record is invalid")
        return cls(
            UUID(_string(value["record_id"])),
            MemoryScope.from_dict(value["scope"]),
            _string(value["phrase"]),
            _string(value["normalized_phrase"]),
            CompactMemoryRoute(_string(value["route"])),
            datetime.fromisoformat(_string(value["created_at"])),
            _string(value["source"]),
            _string(value["sensitivity"]),
        )


@dataclass(frozen=True, slots=True)
class LearnedRouteMutation:
    record: LearnedRouteRecord | None
    changed: bool


class LocalLearnedRouteStore:
    """Bounded, symlink-safe local document for explicit project phrase mutations."""

    _name = "learned-route-phrases.json"
    _lock_name = ".learned-route-phrases.lock"

    def __init__(self, data_directory: Path) -> None:
        self._directory = data_directory.expanduser().resolve()
        self.path = self._directory / self._name

    def records(self, scope: MemoryScope) -> tuple[LearnedRouteRecord, ...]:
        _validate_project_scope(scope)
        records = self._read()
        return tuple(record for record in records if _same_project(record.scope, scope))

    def learn(
        self, scope: MemoryScope, phrase: str, route: CompactMemoryRoute
    ) -> LearnedRouteMutation:
        _validate_project_scope(scope)
        normalized = _validate_phrase(phrase)
        if not isinstance(route, CompactMemoryRoute) or route is CompactMemoryRoute.NONE:
            raise LearnedRouteStoreError("MNEMO_LEARNED_ROUTE_INVALID")
        if contains_high_confidence_secret(phrase):
            raise LearnedRouteStoreError("MNEMO_LEARNED_ROUTE_SECRET_REJECTED")
        with self._locked():
            records = self._read()
            existing = next(
                (
                    item
                    for item in records
                    if _same_project(item.scope, scope) and item.normalized_phrase == normalized
                ),
                None,
            )
            if existing is not None and existing.route is route:
                return LearnedRouteMutation(existing, False)
            record = LearnedRouteRecord(
                uuid4(), scope, phrase.strip(), normalized, route, datetime.now(UTC)
            )
            records = [
                item
                for item in records
                if not (_same_project(item.scope, scope) and item.normalized_phrase == normalized)
            ]
            if len(records) >= _MAXIMUM_PHRASES:
                raise LearnedRouteStoreError("MNEMO_LEARNED_ROUTE_LIMIT_REACHED")
            records.append(record)
            self._write(records)
            return LearnedRouteMutation(record, True)

    def forget(self, scope: MemoryScope, phrase: str) -> LearnedRouteMutation:
        _validate_project_scope(scope)
        normalized = _validate_phrase(phrase)
        with self._locked():
            records = self._read()
            removed = next(
                (
                    item
                    for item in records
                    if _same_project(item.scope, scope) and item.normalized_phrase == normalized
                ),
                None,
            )
            if removed is None:
                return LearnedRouteMutation(None, False)
            self._write(
                [
                    item
                    for item in records
                    if not (
                        _same_project(item.scope, scope) and item.normalized_phrase == normalized
                    )
                ]
            )
            return LearnedRouteMutation(removed, True)

    def _read(self) -> list[LearnedRouteRecord]:
        if not self.path.exists():
            return []
        if self.path.is_symlink() or not self.path.is_file():
            raise LearnedRouteStoreError("MNEMO_LEARNED_ROUTE_STATE_INVALID")
        try:
            if self.path.stat().st_size > _MAXIMUM_FILE_BYTES:
                raise ValueError
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(value, dict)
                or set(value) != {"version", "records"}
                or value["version"] != _FORMAT_VERSION
                or not isinstance(value["records"], list)
                or len(value["records"]) > _MAXIMUM_PHRASES
            ):
                raise ValueError
            records = [LearnedRouteRecord.from_dict(item) for item in value["records"]]
            if len({record.record_id for record in records}) != len(records):
                raise ValueError
            return records
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise LearnedRouteStoreError("MNEMO_LEARNED_ROUTE_STATE_INVALID") from error

    def _write(self, records: list[LearnedRouteRecord]) -> None:
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = json.dumps(
            {"version": _FORMAT_VERSION, "records": [item.to_dict() for item in records]},
            sort_keys=True,
            separators=(",", ":"),
        )
        temporary_path: Path | None = None
        try:
            if self.path.is_symlink():
                raise OSError
            with NamedTemporaryFile(
                "w", encoding="utf-8", dir=self._directory, delete=False
            ) as temporary:
                temporary.write(payload)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self.path)
            os.chmod(self.path, 0o600)
        except OSError as error:
            raise LearnedRouteStoreError("MNEMO_LEARNED_ROUTE_WRITE_FAILED") from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        try:
            with exclusive_local_file_lock(self._directory, self._lock_name):
                yield
        except AutomaticMemoryBindingError as error:
            raise LearnedRouteStoreError("MNEMO_LEARNED_ROUTE_WRITE_FAILED") from error


def _validate_phrase(phrase: str) -> str:
    if (
        not isinstance(phrase, str)
        or not phrase.strip()
        or len(phrase) > _MAXIMUM_PHRASE_CHARACTERS
    ):
        raise LearnedRouteStoreError("MNEMO_LEARNED_ROUTE_PHRASE_INVALID")
    normalized = normalize_learned_route_phrase(phrase)
    if not normalized or len(normalized) > _MAXIMUM_PHRASE_CHARACTERS:
        raise LearnedRouteStoreError("MNEMO_LEARNED_ROUTE_PHRASE_INVALID")
    return normalized


def _validate_project_scope(scope: MemoryScope) -> None:
    if (
        not isinstance(scope, MemoryScope)
        or scope.level is not ScopeLevel.PROJECT
        or scope.visibility is not Visibility.PROJECT
        or scope.workspace_id is None
        or scope.project_id is None
        or scope.session_id is not None
        or scope.task_id is not None
    ):
        raise LearnedRouteStoreError("MNEMO_LEARNED_ROUTE_SCOPE_INVALID")


def _same_project(left: MemoryScope, right: MemoryScope) -> bool:
    return (
        left.owner_id == right.owner_id
        and left.workspace_id == right.workspace_id
        and left.project_id == right.project_id
        and left.level is right.level
        and left.visibility is right.visibility
    )


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("learned route string is invalid")
    return value
