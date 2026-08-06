"""Verified, resumable import of live canonical episodic state."""

from __future__ import annotations

from dataclasses import dataclass

from mnemo_memory.packages.domain import (
    ActiveEpisodicMemory,
    EpisodicCandidateReviewAction,
    EpisodicCandidateReviewDecision,
    EpisodicExportBundle,
    EpisodicExtractionProposal,
    EpisodicMemoryCandidate,
    EpisodicMemoryGovernanceAction,
    EpisodicMemoryGovernanceKind,
    EpisodicMemoryRevision,
    EventId,
    MemoryId,
    MemoryScope,
    ScopeLevel,
    TaskActivityEvent,
    replay_episodic_memory_revisions,
)
from mnemo_memory.packages.storage.contracts import (
    EpisodicExportRepository,
    EpisodicExportRepositoryError,
    EpisodicMemoryCandidateRepository,
    EpisodicMemoryCandidateRepositoryError,
    EpisodicMemoryGovernanceRepository,
    EpisodicMemoryGovernanceRepositoryError,
    EpisodicMemoryReviewRepository,
    EpisodicMemoryReviewRepositoryError,
    TaskActivityEventRepository,
    TaskActivityEventRepositoryError,
)

_LIVE_GROUPS = ("task_events", "candidates", "reviews", "governance_actions")
_LIFECYCLE_GROUPS = (
    "memory_expirations",
    "memory_purges",
    "task_expirations",
    "task_purges",
    "memory_deletions",
    "task_deletions",
)
_COUNT_GROUPS = (*_LIVE_GROUPS, "revisions", *_LIFECYCLE_GROUPS)


class EpisodicImportError(Exception):
    """Safe application outcome for an episodic import."""


class EpisodicImportConflict(EpisodicImportError):
    """The target contains state outside the expected imported projection."""


class EpisodicImportUnsupportedLifecycle(EpisodicImportError):
    """The bundle contains lifecycle state not supported by this import path."""


class EpisodicImportStorageFailure(EpisodicImportError):
    """A repository operation failed without exposing adapter detail."""


@dataclass(frozen=True, slots=True)
class EpisodicImportResult:
    source_content_digest: str
    target_content_digest: str
    counts: tuple[tuple[str, int], ...]
    idempotent: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "source_content_digest": self.source_content_digest,
            "target_content_digest": self.target_content_digest,
            "counts": dict(self.counts),
            "idempotent": self.idempotent,
        }


class EpisodicImportService:
    """Rebase and replay one validated live bundle, then verify its exact projection."""

    def __init__(
        self,
        task_events: TaskActivityEventRepository,
        candidates: EpisodicMemoryCandidateRepository,
        reviews: EpisodicMemoryReviewRepository,
        governance: EpisodicMemoryGovernanceRepository,
        exports: EpisodicExportRepository,
    ) -> None:
        self._task_events = task_events
        self._candidates = candidates
        self._reviews = reviews
        self._governance = governance
        self._exports = exports

    def import_bundle(
        self, bundle: EpisodicExportBundle, *, target_scope: MemoryScope
    ) -> EpisodicImportResult:
        if not isinstance(bundle, EpisodicExportBundle):
            raise TypeError("episodic import requires a validated export bundle")
        if not isinstance(target_scope, MemoryScope) or target_scope.level is not ScopeLevel.TASK:
            raise ValueError("episodic import requires exact target task scope")
        if any(getattr(bundle, name) for name in _LIFECYCLE_GROUPS):
            raise EpisodicImportUnsupportedLifecycle(
                "episodic import bundle contains lifecycle tombstones"
            )

        expected = _rebase_live_bundle(bundle, target_scope)
        try:
            before = self._exports.export_episodic_state(
                target_scope, exported_at=bundle.exported_at
            )
            _require_resumable_subset(before, expected)
            if _semantically_equal(before, expected):
                return _result(bundle, before, idempotent=True)

            for event in expected.task_events:
                self._task_events.append_task_activity_event(event)
            for candidate_batch in _candidate_batches(expected.candidates):
                self._candidates.store_episodic_memory_candidates(candidate_batch)
            for review in expected.reviews:
                self._reviews.review_episodic_memory_candidate(review)
            for action in expected.governance_actions:
                self._governance.govern_episodic_memory(action)

            after = self._exports.export_episodic_state(
                target_scope, exported_at=bundle.exported_at
            )
        except EpisodicImportError:
            raise
        except (
            TaskActivityEventRepositoryError,
            EpisodicMemoryCandidateRepositoryError,
            EpisodicMemoryReviewRepositoryError,
            EpisodicMemoryGovernanceRepositoryError,
            EpisodicExportRepositoryError,
        ) as error:
            raise EpisodicImportStorageFailure(
                "episodic import storage operation failed"
            ) from error

        if not _semantically_equal(after, expected):
            mismatched = tuple(
                name for name in _COUNT_GROUPS if getattr(after, name) != getattr(expected, name)
            )
            detail = ",".join(mismatched) if mismatched else "metadata"
            raise EpisodicImportConflict(
                f"episodic import target digest or counts do not match: {detail}"
            )
        return _result(bundle, after, idempotent=False)


def _rebase_live_bundle(
    bundle: EpisodicExportBundle, target_scope: MemoryScope
) -> EpisodicExportBundle:
    events: list[TaskActivityEvent] = []
    event_by_source: dict[EventId, TaskActivityEvent] = {}
    for source_event in bundle.task_events:
        target_event = TaskActivityEvent.create(
            scope=target_scope,
            kind=source_event.kind,
            actor=source_event.actor,
            summary=source_event.summary,
            source_event_key=source_event.source_event_key,
            sensitivity=source_event.sensitivity,
            retention=source_event.retention,
            occurred_at=source_event.occurred_at,
            evidence_references=source_event.evidence_references,
        )
        events.append(target_event)
        event_by_source[source_event.event_id] = target_event

    candidates: list[EpisodicMemoryCandidate] = []
    candidate_by_source: dict[MemoryId, EpisodicMemoryCandidate] = {}
    for source_candidate in bundle.candidates:
        target_event = event_by_source[source_candidate.source_event_id]
        target_candidate = EpisodicMemoryCandidate.create(
            source_event=target_event,
            proposal=EpisodicExtractionProposal(
                source_candidate.kind,
                source_candidate.memory.claim,
                source_candidate.confidence,
                source_candidate.memory.classification.sensitivity,
            ),
            proposal_index=source_candidate.proposal_index,
            sensitivity=source_candidate.memory.classification.sensitivity,
            extractor_version=source_candidate.extractor_version,
            provider_id=source_candidate.provider_id,
            model_id=source_candidate.model_id,
            prompt_version=source_candidate.prompt_version,
            created_at=source_candidate.created_at,
        )
        candidates.append(target_candidate)
        candidate_by_source[source_candidate.memory_id] = target_candidate

    reviews: list[EpisodicCandidateReviewAction] = []
    review_by_memory: dict[MemoryId, EpisodicCandidateReviewAction] = {}
    for source_review in bundle.reviews:
        target_candidate = candidate_by_source[source_review.candidate_id]
        target_review = EpisodicCandidateReviewAction.create(
            scope=target_scope,
            candidate_id=target_candidate.memory_id,
            decision=source_review.decision,
            source_action_key=source_review.source_action_key,
            reason=source_review.reason,
            reviewed_at=source_review.reviewed_at,
            evidence_references=source_review.evidence_references,
        )
        reviews.append(target_review)
        review_by_memory[source_review.candidate_id] = target_review

    governance: list[EpisodicMemoryGovernanceAction] = []
    current_revision = {
        source_memory_id: review.action_id
        for source_memory_id, review in review_by_memory.items()
        if review.decision is EpisodicCandidateReviewDecision.APPROVED
    }
    for source_action in bundle.governance_actions:
        target_memory = candidate_by_source[source_action.memory_id].memory_id
        expected_revision_id = current_revision[source_action.memory_id]
        if source_action.kind is EpisodicMemoryGovernanceKind.CORRECTED:
            assert source_action.corrected_claim is not None
            assert source_action.corrected_sensitivity is not None
            target_action = EpisodicMemoryGovernanceAction.correct(
                scope=target_scope,
                memory_id=target_memory,
                expected_revision_id=expected_revision_id,
                source_action_key=source_action.source_action_key,
                reason=source_action.reason,
                corrected_claim=source_action.corrected_claim,
                corrected_sensitivity=source_action.corrected_sensitivity,
                occurred_at=source_action.occurred_at,
                evidence_references=source_action.evidence_references,
            )
        else:
            target_action = EpisodicMemoryGovernanceAction.retract(
                scope=target_scope,
                memory_id=target_memory,
                expected_revision_id=expected_revision_id,
                source_action_key=source_action.source_action_key,
                reason=source_action.reason,
                occurred_at=source_action.occurred_at,
                evidence_references=source_action.evidence_references,
            )
        governance.append(target_action)
        current_revision[source_action.memory_id] = target_action.action_id

    target_review_by_memory = {review.candidate_id: review for review in reviews}
    target_actions_by_memory: dict[MemoryId, list[EpisodicMemoryGovernanceAction]] = {}
    for action in governance:
        target_actions_by_memory.setdefault(action.memory_id, []).append(action)
    revisions: list[EpisodicMemoryRevision] = []
    for candidate in candidates:
        review = target_review_by_memory.get(candidate.memory_id)
        if review is None or review.decision is EpisodicCandidateReviewDecision.REJECTED:
            continue
        revisions.extend(
            replay_episodic_memory_revisions(
                ActiveEpisodicMemory.approve(candidate, review),
                tuple(target_actions_by_memory.get(candidate.memory_id, ())),
            )
        )

    return EpisodicExportBundle.create(
        scope=target_scope,
        exported_at=bundle.exported_at,
        task_events=tuple(events),
        candidates=tuple(candidates),
        reviews=tuple(reviews),
        governance_actions=tuple(governance),
        revisions=tuple(revisions),
    )


def _candidate_batches(
    candidates: tuple[EpisodicMemoryCandidate, ...],
) -> tuple[tuple[EpisodicMemoryCandidate, ...], ...]:
    grouped: dict[tuple[EventId, str], list[EpisodicMemoryCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault((candidate.source_event_id, candidate.extractor_version), []).append(
            candidate
        )
    return tuple(
        tuple(sorted(values, key=lambda item: item.proposal_index))
        for _, values in sorted(grouped.items(), key=lambda item: (str(item[0][0]), item[0][1]))
    )


def _require_resumable_subset(
    current: EpisodicExportBundle, expected: EpisodicExportBundle
) -> None:
    for name in (*_LIVE_GROUPS, *_LIFECYCLE_GROUPS):
        current_items = getattr(current, name)
        expected_items = getattr(expected, name)
        if any(item not in expected_items for item in current_items):
            raise EpisodicImportConflict("episodic import target contains conflicting state")


def _semantically_equal(current: EpisodicExportBundle, expected: EpisodicExportBundle) -> bool:
    """Compare typed state; adapters may normalize equivalent timezone offsets differently."""
    return (
        current.format_version == expected.format_version
        and current.scope == expected.scope
        and current.exported_at == expected.exported_at
        and all(getattr(current, name) == getattr(expected, name) for name in _COUNT_GROUPS)
    )


def _result(
    source: EpisodicExportBundle,
    target: EpisodicExportBundle,
    *,
    idempotent: bool,
) -> EpisodicImportResult:
    return EpisodicImportResult(
        source.content_digest,
        target.content_digest,
        tuple((name, len(getattr(target, name))) for name in _COUNT_GROUPS),
        idempotent,
    )
