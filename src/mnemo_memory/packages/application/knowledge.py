"""Storage-independent synchronization of explicitly scoped local knowledge documents."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from mnemo_memory.packages.domain import (
    KnowledgeDocument,
    KnowledgeDocumentRevision,
    KnowledgeDocumentRevisionId,
    KnowledgeDocumentTombstone,
    KnowledgeSyncActionKind,
    KnowledgeSyncPlan,
    KnowledgeSyncPlanner,
    MemoryScope,
)
from mnemo_memory.packages.storage import (
    InvalidKnowledgeDocumentScope,
    KnowledgeDocumentConflict,
    KnowledgeDocumentRepository,
    KnowledgeDocumentSecretRejected,
    KnowledgeDocumentStorageFailure,
    KnowledgeDocumentSyncStoreResult,
)


class KnowledgeApplicationError(Exception):
    """Safe application outcome for local knowledge synchronization."""


class KnowledgeApplicationInvalidScope(KnowledgeApplicationError):
    pass


class KnowledgeApplicationRejected(KnowledgeApplicationError):
    pass


class KnowledgeApplicationConflict(KnowledgeApplicationError):
    pass


class KnowledgeApplicationStorageFailure(KnowledgeApplicationError):
    pass


@dataclass(frozen=True, slots=True)
class SynchronizeKnowledgeDocuments:
    """A complete parsed view of one local source root for one explicit project scope."""

    scope: MemoryScope
    documents: tuple[KnowledgeDocument, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.scope, MemoryScope):
            raise TypeError("knowledge synchronization requires an explicit scope")
        if any(document.scope != self.scope for document in self.documents):
            raise ValueError("knowledge synchronization document scope mismatch")


@dataclass(frozen=True, slots=True)
class KnowledgeSynchronizationResult:
    """Safe reconciliation result; payloads remain in the repository, not this response."""

    plan: KnowledgeSyncPlan
    store_result: KnowledgeDocumentSyncStoreResult


class KnowledgeDocumentApplicationService:
    """Coordinates planning, secret policy, immutable revisions, and atomic repository writes.

    Filesystem discovery and parsing deliberately happen outside this service.  That keeps local
    path traversal in connectors and lets this application boundary remain deterministic in tests.
    """

    def __init__(
        self,
        repository: KnowledgeDocumentRepository,
        *,
        clock: Callable[[], datetime],
        revision_id_factory: Callable[
            [], KnowledgeDocumentRevisionId
        ] = KnowledgeDocumentRevisionId.new,
        planner: KnowledgeSyncPlanner | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._revision_id_factory = revision_id_factory
        self._planner = planner or KnowledgeSyncPlanner()

    def synchronize(self, request: SynchronizeKnowledgeDocuments) -> KnowledgeSynchronizationResult:
        """Plan and atomically persist accepted changes for exactly one project scope."""
        try:
            known = self._repository.list_active_documents(request.scope)
            plan = self._planner.plan(request.scope, known, request.documents)
            known_by_id = {item.document_id: item for item in known}
            revisions: list[KnowledgeDocumentRevision] = []
            tombstones: list[KnowledgeDocumentTombstone] = []
            now = self._clock()
            for action in plan.actions:
                if action.kind is KnowledgeSyncActionKind.UNCHANGED:
                    continue
                if action.kind is KnowledgeSyncActionKind.TOMBSTONED:
                    previous = known_by_id[action.document_id]
                    tombstones.append(
                        KnowledgeDocumentTombstone(
                            previous.document_id,
                            request.scope,
                            previous.relative_path,
                            previous.content_digest,
                            previous.current_revision_id,
                            now,
                        )
                    )
                    continue
                if action.document is None:  # Defensive: the plan value object also enforces this.
                    raise KnowledgeApplicationConflict(
                        "knowledge synchronization action is invalid"
                    )
                prior_document = known_by_id.get(action.document_id)
                document = replace(action.document, document_id=action.document_id)
                revisions.append(
                    KnowledgeDocumentRevision(
                        self._revision_id_factory(),
                        document,
                        1 if prior_document is None else prior_document.revision_number + 1,
                        None if prior_document is None else prior_document.current_revision_id,
                        now,
                    )
                )
            store_result = self._repository.apply_sync(
                request.scope, tuple(revisions), tuple(tombstones)
            )
            return KnowledgeSynchronizationResult(plan, store_result)
        except InvalidKnowledgeDocumentScope as error:
            raise KnowledgeApplicationInvalidScope("knowledge scope is invalid") from error
        except KnowledgeDocumentSecretRejected as error:
            raise KnowledgeApplicationRejected(
                "knowledge document was rejected by safety policy"
            ) from error
        except KnowledgeDocumentConflict as error:
            raise KnowledgeApplicationConflict("knowledge synchronization conflicts") from error
        except KnowledgeDocumentStorageFailure as error:
            raise KnowledgeApplicationStorageFailure("knowledge storage is unavailable") from error
