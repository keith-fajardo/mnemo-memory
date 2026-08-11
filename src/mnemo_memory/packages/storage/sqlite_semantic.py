"""Personal SQLite persistence for the semantic checkpoint ledger."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from mnemo_memory.packages.domain import (
    CheckpointId,
    EventId,
    MaterializedSemanticCheckpoint,
    MemoryScope,
    ScopeLevel,
    SemanticCheckpoint,
    SemanticCheckpointAtom,
    SemanticCheckpointPatch,
    SemanticMemoryAtom,
    apply_semantic_checkpoint_patch,
)

from .contracts import (
    SemanticCheckpointConflict,
    SemanticCheckpointNotFound,
    SemanticCheckpointStorageFailure,
)
from .sqlite import BUSY_TIMEOUT_MS, resolve_database_path


class SQLiteSemanticCheckpointRepository:
    """Exact-scope, transactionally validated semantic checkpoints for personal mode."""

    def __init__(self, path: Path, *, base_directory: Path | None = None) -> None:
        self.path = resolve_database_path(path, base_directory)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path, isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def get_current_semantic_checkpoint(self, scope: MemoryScope) -> SemanticCheckpoint | None:
        self._require_scope(scope)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT payload_json FROM semantic_checkpoints WHERE scope_key = ? "
                    "ORDER BY generation DESC LIMIT 1",
                    (self._scope_key(scope),),
                ).fetchone()
        except sqlite3.Error as error:
            raise SemanticCheckpointStorageFailure(
                "semantic checkpoint storage operation failed"
            ) from error
        if row is None:
            return None
        checkpoint = self._checkpoint_from_row(row)
        self._require_matching_scope(checkpoint.scope, scope)
        return checkpoint

    def get_semantic_checkpoint(
        self, scope: MemoryScope, checkpoint_id: CheckpointId
    ) -> SemanticCheckpoint:
        self._require_scope(scope)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT payload_json FROM semantic_checkpoints "
                    "WHERE checkpoint_id = ? AND scope_key = ?",
                    (str(checkpoint_id), self._scope_key(scope)),
                ).fetchone()
        except sqlite3.Error as error:
            raise SemanticCheckpointStorageFailure(
                "semantic checkpoint storage operation failed"
            ) from error
        if row is None:
            raise SemanticCheckpointNotFound("semantic checkpoint was not found")
        checkpoint = self._checkpoint_from_row(row)
        self._require_matching_scope(checkpoint.scope, scope)
        return checkpoint

    def list_semantic_atoms(self, scope: MemoryScope) -> tuple[SemanticMemoryAtom, ...]:
        self._require_scope(scope)
        try:
            with self._connect() as connection:
                atoms = self._list_atoms(connection, scope)
        except sqlite3.Error as error:
            raise SemanticCheckpointStorageFailure(
                "semantic checkpoint storage operation failed"
            ) from error
        return atoms

    def list_compiled_semantic_event_ids(self, scope: MemoryScope) -> frozenset[EventId]:
        self._require_scope(scope)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT marker.event_id, checkpoint.payload_json "
                    "FROM semantic_compiled_events AS marker "
                    "JOIN semantic_checkpoints AS checkpoint "
                    "ON checkpoint.checkpoint_id = marker.checkpoint_id "
                    "WHERE marker.scope_key = ?",
                    (self._scope_key(scope),),
                ).fetchall()
        except sqlite3.Error as error:
            raise SemanticCheckpointStorageFailure(
                "semantic checkpoint storage operation failed"
            ) from error
        for row in rows:
            self._require_matching_scope(self._checkpoint_from_row(row).scope, scope)
        return frozenset(EventId.from_string(str(row["event_id"])) for row in rows)

    def materialize_semantic_checkpoint(
        self, scope: MemoryScope, checkpoint_id: CheckpointId
    ) -> MaterializedSemanticCheckpoint:
        self._require_scope(scope)
        try:
            with self._connect() as connection:
                return self._materialize(connection, scope, checkpoint_id)
        except SemanticCheckpointNotFound:
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise SemanticCheckpointStorageFailure(
                "semantic checkpoint storage operation failed"
            ) from error

    def store_semantic_checkpoint(
        self,
        patch: SemanticCheckpointPatch,
        materialized: MaterializedSemanticCheckpoint,
    ) -> bool:
        checkpoint = materialized.checkpoint
        scope = checkpoint.scope
        self._require_scope(scope)
        if patch.digest != checkpoint.patch_digest:
            raise SemanticCheckpointConflict("semantic checkpoint patch digest does not match")
        try:
            with self._transaction() as connection:
                existing = connection.execute(
                    "SELECT payload_json FROM semantic_checkpoints WHERE checkpoint_id = ?",
                    (str(checkpoint.checkpoint_id),),
                ).fetchone()
                if existing is not None:
                    stored = self._materialize(connection, scope, checkpoint.checkpoint_id)
                    if stored == materialized:
                        return True
                    raise SemanticCheckpointConflict("semantic checkpoint identity conflicts")
                current_row = connection.execute(
                    "SELECT payload_json FROM semantic_checkpoints WHERE scope_key = ? "
                    "ORDER BY generation DESC LIMIT 1",
                    (self._scope_key(scope),),
                ).fetchone()
                current = None if current_row is None else self._checkpoint_from_row(current_row)
                current_id = None if current is None else current.checkpoint_id
                if (
                    patch.base_checkpoint_id != current_id
                    or checkpoint.parent_checkpoint_id != current_id
                ):
                    raise SemanticCheckpointConflict("semantic checkpoint parent changed")
                expected_generation = 1 if current is None else current.generation + 1
                if checkpoint.generation != expected_generation:
                    raise SemanticCheckpointConflict("semantic checkpoint generation is invalid")
                ledger = self._list_atoms(connection, scope)
                active_references: tuple[SemanticCheckpointAtom, ...] = ()
                if current is not None:
                    active_references = self._materialize(
                        connection, scope, current.checkpoint_id
                    ).references
                event_ids = frozenset(
                    event_id
                    for operation in patch.operations
                    if operation.atom is not None
                    for event_id in operation.atom.source_event_ids
                )
                event_ids |= frozenset(patch.processed_event_ids)
                self._verify_events(connection, scope, event_ids)
                if patch.processed_event_ids:
                    placeholders = ",".join("?" for _ in patch.processed_event_ids)
                    duplicate = connection.execute(
                        "SELECT 1 FROM semantic_compiled_events WHERE scope_key = ? "
                        f"AND event_id IN ({placeholders}) LIMIT 1",
                        (
                            self._scope_key(scope),
                            *(str(item) for item in patch.processed_event_ids),
                        ),
                    ).fetchone()
                    if duplicate is not None:
                        raise SemanticCheckpointConflict("semantic event was already compiled")
                updated_ledger, references = apply_semantic_checkpoint_patch(
                    scope=scope,
                    ledger_atoms=ledger,
                    active_references=active_references,
                    patch=patch,
                    available_event_ids=event_ids,
                    applied_at=checkpoint.created_at,
                )
                atoms_by_id = {atom.atom_id: atom for atom in updated_ledger}
                expected_atoms = tuple(atoms_by_id[item.atom_id] for item in references)
                if references != materialized.references or expected_atoms != materialized.atoms:
                    raise SemanticCheckpointConflict(
                        "materialized semantic checkpoint does not match patch"
                    )
                for atom in updated_ledger:
                    self._upsert_atom(connection, atom)
                self._insert_checkpoint(connection, checkpoint)
                connection.executemany(
                    "INSERT INTO semantic_compiled_events VALUES (?, ?, ?)",
                    tuple(
                        (
                            self._scope_key(scope),
                            str(event_id),
                            str(checkpoint.checkpoint_id),
                        )
                        for event_id in patch.processed_event_ids
                    ),
                )
                connection.executemany(
                    "INSERT INTO semantic_checkpoint_patch_operations VALUES (?, ?, ?, ?, ?)",
                    tuple(
                        (
                            str(checkpoint.checkpoint_id),
                            index,
                            operation.kind.value,
                            None if operation.atom is None else str(operation.atom.atom_id),
                            None
                            if operation.target_atom_id is None
                            else str(operation.target_atom_id),
                        )
                        for index, operation in enumerate(patch.operations)
                    ),
                )
                connection.executemany(
                    "INSERT INTO semantic_checkpoint_atoms VALUES (?, ?, ?, ?, ?)",
                    tuple(
                        (
                            str(checkpoint.checkpoint_id),
                            str(reference.atom_id),
                            reference.inclusion_reason,
                            reference.checkpoint_priority,
                            self._json(atoms_by_id[reference.atom_id].to_dict()),
                        )
                        for reference in references
                    ),
                )
                return False
        except SemanticCheckpointConflict:
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise SemanticCheckpointStorageFailure(
                "semantic checkpoint storage operation failed"
            ) from error

    def _materialize(
        self,
        connection: sqlite3.Connection,
        scope: MemoryScope,
        checkpoint_id: CheckpointId,
    ) -> MaterializedSemanticCheckpoint:
        row = connection.execute(
            "SELECT payload_json FROM semantic_checkpoints "
            "WHERE checkpoint_id = ? AND scope_key = ?",
            (str(checkpoint_id), self._scope_key(scope)),
        ).fetchone()
        if row is None:
            raise SemanticCheckpointNotFound("semantic checkpoint was not found")
        checkpoint = self._checkpoint_from_row(row)
        self._require_matching_scope(checkpoint.scope, scope)
        rows = connection.execute(
            "SELECT reference.atom_payload_json AS payload_json, reference.inclusion_reason, "
            "reference.checkpoint_priority FROM semantic_checkpoint_atoms AS reference "
            "JOIN semantic_memory_atoms AS atom ON atom.atom_id = reference.atom_id "
            "WHERE reference.checkpoint_id = ? "
            "ORDER BY reference.checkpoint_priority DESC, reference.atom_id",
            (str(checkpoint_id),),
        ).fetchall()
        atoms = tuple(self._atom_from_row(item) for item in rows)
        references = tuple(
            SemanticCheckpointAtom(
                atom.atom_id,
                str(item["inclusion_reason"]),
                int(item["checkpoint_priority"]),
            )
            for atom, item in zip(atoms, rows, strict=True)
        )
        return MaterializedSemanticCheckpoint(checkpoint, atoms, references)

    def _list_atoms(
        self, connection: sqlite3.Connection, scope: MemoryScope
    ) -> tuple[SemanticMemoryAtom, ...]:
        rows = connection.execute(
            "SELECT payload_json FROM semantic_memory_atoms WHERE scope_key = ? ORDER BY atom_id",
            (self._scope_key(scope),),
        ).fetchall()
        atoms = tuple(self._atom_from_row(row) for row in rows)
        if any(atom.scope != scope for atom in atoms):
            raise ValueError("semantic atom scope key does not match canonical scope")
        return atoms

    def _verify_events(
        self,
        connection: sqlite3.Connection,
        scope: MemoryScope,
        event_ids: frozenset[object],
    ) -> None:
        values = self._scope_values(scope)
        for event_id in event_ids:
            row = connection.execute(
                "SELECT 1 FROM task_activity_events AS event WHERE event.event_id = ? "
                "AND event.owner_id = ? AND event.visibility = ? AND event.workspace_id IS ? "
                "AND event.project_id = ? AND event.session_id = ? AND event.task_id = ? "
                "AND NOT EXISTS (SELECT 1 FROM task_activity_event_expirations AS expiration "
                "WHERE expiration.event_id = event.event_id) "
                "AND NOT EXISTS (SELECT 1 FROM task_activity_event_deletions AS deletion "
                "WHERE deletion.event_id = event.event_id)",
                (str(event_id), *values),
            ).fetchone()
            if row is None:
                raise SemanticCheckpointConflict(
                    "semantic atom evidence event is unavailable in scope"
                )

    def _upsert_atom(self, connection: sqlite3.Connection, atom: SemanticMemoryAtom) -> None:
        scope_key = self._scope_key(atom.scope)
        values = (
            str(atom.atom_id),
            scope_key,
            *self._scope_values(atom.scope),
            atom.kind.value,
            atom.status.value,
            atom.subject,
            atom.predicate,
            atom.object_value,
            atom.priority,
            atom.updated_at.isoformat(),
            self._json(atom.to_dict()),
        )
        connection.execute(
            "INSERT INTO semantic_memory_atoms("
            "atom_id, scope_key, owner_id, visibility, workspace_id, project_id, session_id, "
            "task_id, atom_kind, atom_status, subject, predicate, object_value, priority, "
            "updated_at, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(atom_id) DO UPDATE SET atom_status = excluded.atom_status, "
            "priority = excluded.priority, updated_at = excluded.updated_at, "
            "payload_json = excluded.payload_json",
            values,
        )
        connection.executemany(
            "INSERT OR IGNORE INTO semantic_atom_source_events VALUES (?, ?)",
            tuple((str(atom.atom_id), str(event_id)) for event_id in atom.source_event_ids),
        )

    def _insert_checkpoint(
        self, connection: sqlite3.Connection, checkpoint: SemanticCheckpoint
    ) -> None:
        connection.execute(
            "INSERT INTO semantic_checkpoints("
            "checkpoint_id, scope_key, owner_id, visibility, workspace_id, project_id, session_id, "
            "task_id, parent_checkpoint_id, generation, schema_version, checkpoint_type, "
            "head_event_id, created_at, renderer_profile, target_tokenizer, measured_tokens, "
            "compression_ratio, patch_digest, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(checkpoint.checkpoint_id),
                self._scope_key(checkpoint.scope),
                *self._scope_values(checkpoint.scope),
                None
                if checkpoint.parent_checkpoint_id is None
                else str(checkpoint.parent_checkpoint_id),
                checkpoint.generation,
                checkpoint.schema_version,
                checkpoint.checkpoint_type.value,
                str(checkpoint.head_event_id),
                checkpoint.created_at.isoformat(),
                checkpoint.renderer_profile.value,
                checkpoint.target_tokenizer,
                checkpoint.measured_tokens,
                checkpoint.compression_ratio,
                checkpoint.patch_digest,
                self._json(checkpoint.to_dict()),
            ),
        )

    @staticmethod
    def _checkpoint_from_row(row: sqlite3.Row) -> SemanticCheckpoint:
        value = json.loads(str(row["payload_json"]))
        if not isinstance(value, dict):
            raise TypeError("semantic checkpoint payload must be an object")
        return SemanticCheckpoint.from_dict(value)

    @staticmethod
    def _atom_from_row(row: sqlite3.Row) -> SemanticMemoryAtom:
        value = json.loads(str(row["payload_json"]))
        if not isinstance(value, dict):
            raise TypeError("semantic atom payload must be an object")
        return SemanticMemoryAtom.from_dict(value)

    @staticmethod
    def _scope_values(scope: MemoryScope) -> tuple[object, ...]:
        return (
            str(scope.owner_id),
            scope.visibility.value,
            None if scope.workspace_id is None else str(scope.workspace_id),
            str(scope.project_id),
            str(scope.session_id),
            str(scope.task_id),
        )

    @classmethod
    def _scope_key(cls, scope: MemoryScope) -> str:
        return hashlib.sha256(cls._json(scope.to_dict()).encode("utf-8")).hexdigest()

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    @staticmethod
    def _require_scope(scope: MemoryScope) -> None:
        if not isinstance(scope, MemoryScope) or scope.level is not ScopeLevel.TASK:
            raise ValueError("semantic checkpoints require exact task scope")

    @staticmethod
    def _require_matching_scope(actual: MemoryScope, requested: MemoryScope) -> None:
        if actual != requested:
            raise SemanticCheckpointNotFound("semantic checkpoint was not found")
