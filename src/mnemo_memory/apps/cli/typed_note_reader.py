"""The note reader shared by the CLI and the background judge (spec 2026-10-03 §4).

It lives apart from ``main`` so the judge process need not import the whole CLI. ``main`` keeps
its private names for these functions as aliases.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from mnemo_memory.packages.application import (
    CheckpointRuntime,
    build_checkpoint_runtime,
    resolve_local_config,
)
from mnemo_memory.packages.application.unified_context import (
    GetUnifiedContext,
    UnifiedContextService,
    is_requestable_item_id,
)
from mnemo_memory.packages.context_engine import UnifiedContextEngine
from mnemo_memory.packages.domain import ContextPacket, EventId, MemoryScope

if TYPE_CHECKING:
    from mnemo_memory.apps.cli.typed_decision_hook import FillerCandidate
    from mnemo_memory.packages.knowledge import LocalSemanticKnowledgeRetriever


def automatic_prompt_context_service(
    runtime: CheckpointRuntime,
    semantic: LocalSemanticKnowledgeRetriever | None,
    *,
    include_semantic_memory: bool = False,
) -> UnifiedContextEngine:
    return UnifiedContextEngine(
        UnifiedContextService(
            runtime.checkpoint_service,
            runtime.dbt_manifest_service,
            runtime.source_structure_repository,
            runtime.repository,
            runtime.knowledge_document_repository,
            semantic_knowledge=semantic,
            semantic_memory=(runtime.semantic_memory_service if include_semantic_memory else None),
        ),
        runtime.repository,
    )


def pinned_approved_item_ids(runtime: CheckpointRuntime, packet: ContextPacket) -> frozenset[str]:
    """Pinned approved events in ``packet``; an unreadable pin state counts as pinned.

    Each scope's pin states are read in one pass. If that read fails, each event is read on its
    own, so one unreadable event never hides the others' pin states.
    """

    pinned: set[str] = set()
    by_scope: dict[MemoryScope, list[tuple[str, EventId]]] = {}
    for item in packet.episodic_memories:
        if not item.item_id.startswith("approved-episodic:"):
            continue
        try:
            event_id = EventId.from_string(item.item_id.removeprefix("approved-episodic:"))
        except Exception:
            pinned.add(item.item_id)  # keep is the safe side
            continue
        by_scope.setdefault(item.source_scope, []).append((item.item_id, event_id))
    for scope, items in by_scope.items():
        event_ids = tuple(event_id for _, event_id in items)
        try:
            states = [
                record.pinned
                for record in runtime.repository.get_approved_event_records(scope, event_ids)
            ]
        except Exception:
            states = [pin_state(runtime, scope, event_id) for event_id in event_ids]
        pinned.update(item_id for (item_id, _), state in zip(items, states, strict=True) if state)
    return frozenset(pinned)


def pin_state(runtime: CheckpointRuntime, scope: MemoryScope, event_id: EventId) -> bool:
    try:
        return runtime.repository.get_approved_event_record(scope, event_id).pinned
    except Exception:
        return True  # keep is the safe side


NOTE_READ_BATCH = 4


def note_candidates_by_id(
    data_directory: Path, scope: MemoryScope, item_ids: Sequence[str]
) -> tuple[FillerCandidate, ...]:
    """Re-read notes by ID through the scoped ``get_context item_ids`` lookup (spec §4).

    The lookup rechecks scope, currentness and sensitivity. The hook's own
    ``filler_candidates`` then applies every exemption it can see in its own small packet (pinned
    events, conflicts among the packet's notes, non-normal sensitivity, the secret scan,
    unreadable text) and builds the 300-character judged text, so the judge keys verdicts on
    exactly the text the hook looks up. Conflicts with notes outside that packet are only applied
    by the hook. An ID the lookup cannot
    serve (gone, changed, foreign, malformed) is skipped. Nothing is sent anywhere.
    """

    from mnemo_memory.apps.cli.typed_decision_hook import filler_candidates

    requested = tuple(
        dict.fromkeys(item_id for item_id in item_ids if is_requestable_item_id(item_id))
    )
    if not requested:
        return ()
    candidates: list[FillerCandidate] = []
    with build_checkpoint_runtime(resolve_local_config(data_directory)) as runtime:
        service = automatic_prompt_context_service(runtime, None)
        for start in range(0, len(requested), NOTE_READ_BATCH):
            chunk = requested[start : start + NOTE_READ_BATCH]
            packet = service.get_context(GetUnifiedContext(scope, item_ids=chunk))
            candidates.extend(
                filler_candidates(
                    packet,
                    pinned_item_ids=pinned_approved_item_ids(runtime, packet),
                    rendered_item_ids=frozenset(chunk),
                )
            )
    return tuple(candidates)
