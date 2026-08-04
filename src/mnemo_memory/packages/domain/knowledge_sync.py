"""Pure, deterministic reconciliation types for scoped local knowledge sources."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .identifiers import KnowledgeDocumentId
from .knowledge import KnowledgeDocument, KnownKnowledgeDocument
from .models import MemoryScope


class KnowledgeSyncActionKind(StrEnum):
    ADDED = "added"
    UNCHANGED = "unchanged"
    REVISED = "revised"
    RENAMED = "renamed"
    TOMBSTONED = "tombstoned"


@dataclass(frozen=True, slots=True)
class KnowledgeSyncAction:
    """One deterministic source transition; a tombstone has no document payload."""

    kind: KnowledgeSyncActionKind
    document_id: KnowledgeDocumentId
    relative_path: str
    previous_relative_path: str | None = None
    document: KnowledgeDocument | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.document_id, KnowledgeDocumentId) or not self.relative_path:
            raise ValueError("knowledge sync action identity is invalid")
        if self.kind is KnowledgeSyncActionKind.TOMBSTONED:
            if self.document is not None or self.previous_relative_path is not None:
                raise ValueError("knowledge tombstones cannot retain a document payload")
        elif self.document is None:
            raise ValueError("active knowledge sync action requires its parsed document")
        elif self.kind is KnowledgeSyncActionKind.RENAMED and not self.previous_relative_path:
            raise ValueError("knowledge rename requires a prior path")
        elif (
            self.kind is not KnowledgeSyncActionKind.RENAMED
            and self.previous_relative_path is not None
        ):
            raise ValueError("only a knowledge rename carries a prior path")


@dataclass(frozen=True, slots=True)
class KnowledgeSyncPlan:
    """A deterministic full source reconciliation for exactly one explicit project scope."""

    scope: MemoryScope
    actions: tuple[KnowledgeSyncAction, ...]

    @property
    def changed(self) -> bool:
        return any(action.kind is not KnowledgeSyncActionKind.UNCHANGED for action in self.actions)


class KnowledgeSyncPlanner:
    """Compare active metadata with explicit discovery without persistence side effects."""

    def plan(
        self,
        scope: MemoryScope,
        known: tuple[KnownKnowledgeDocument, ...],
        discovered: tuple[KnowledgeDocument, ...],
    ) -> KnowledgeSyncPlan:
        if not isinstance(scope, MemoryScope):
            raise TypeError("knowledge synchronization requires an explicit scope")
        if any(item.scope != scope for item in known) or any(
            item.scope != scope for item in discovered
        ):
            raise ValueError("knowledge synchronization scope mismatch")
        known_paths = {item.relative_path: item for item in known}
        discovered_paths = {item.relative_path: item for item in discovered}
        if len(known_paths) != len(known) or len(discovered_paths) != len(discovered):
            raise ValueError("knowledge synchronization paths must be unique")

        actions: list[KnowledgeSyncAction] = []
        for path in sorted(known_paths.keys() & discovered_paths.keys()):
            previous, current = known_paths[path], discovered_paths[path]
            kind = (
                KnowledgeSyncActionKind.UNCHANGED
                if previous.content_digest == current.content_digest
                else KnowledgeSyncActionKind.REVISED
            )
            actions.append(KnowledgeSyncAction(kind, previous.document_id, path, document=current))

        added = [
            discovered_paths[path] for path in sorted(discovered_paths.keys() - known_paths.keys())
        ]
        removed = [
            known_paths[path] for path in sorted(known_paths.keys() - discovered_paths.keys())
        ]
        added_by_digest: dict[str, list[KnowledgeDocument]] = {}
        removed_by_digest: dict[str, list[KnownKnowledgeDocument]] = {}
        for added_item in added:
            added_by_digest.setdefault(added_item.content_digest, []).append(added_item)
        for removed_item in removed:
            removed_by_digest.setdefault(removed_item.content_digest, []).append(removed_item)
        renamed_added: set[str] = set()
        renamed_removed: set[str] = set()
        for digest in sorted(added_by_digest.keys() & removed_by_digest.keys()):
            new_values, old_values = added_by_digest[digest], removed_by_digest[digest]
            if len(new_values) != 1 or len(old_values) != 1:
                continue
            current, previous = new_values[0], old_values[0]
            renamed_added.add(current.relative_path)
            renamed_removed.add(previous.relative_path)
            actions.append(
                KnowledgeSyncAction(
                    KnowledgeSyncActionKind.RENAMED,
                    previous.document_id,
                    current.relative_path,
                    previous.relative_path,
                    current,
                )
            )
        actions.extend(
            KnowledgeSyncAction(
                KnowledgeSyncActionKind.ADDED, item.document_id, item.relative_path, document=item
            )
            for item in added
            if item.relative_path not in renamed_added
        )
        actions.extend(
            KnowledgeSyncAction(
                KnowledgeSyncActionKind.TOMBSTONED, item.document_id, item.relative_path
            )
            for item in removed
            if item.relative_path not in renamed_removed
        )
        return KnowledgeSyncPlan(
            scope,
            tuple(
                sorted(
                    actions,
                    key=lambda item: (item.relative_path, item.kind.value, str(item.document_id)),
                )
            ),
        )
