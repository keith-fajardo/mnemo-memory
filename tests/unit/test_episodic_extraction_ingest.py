from mnemo_memory.packages.application.episodic_extraction_ingest import (
    approved_kind_for_episodic,
)
from mnemo_memory.packages.domain.approved_episodic_events import ApprovedEventKind
from mnemo_memory.packages.domain.episodic_candidates import EpisodicMemoryKind


def test_kind_mapping_maps_known_and_drops_unmapped() -> None:
    assert approved_kind_for_episodic(EpisodicMemoryKind.DECISION) is ApprovedEventKind.DECISION
    assert approved_kind_for_episodic(EpisodicMemoryKind.FAILURE) is ApprovedEventKind.FAILURE
    assert approved_kind_for_episodic(EpisodicMemoryKind.OUTCOME) is ApprovedEventKind.TOOL_OUTCOME
    assert approved_kind_for_episodic(EpisodicMemoryKind.LESSON) is None
    assert approved_kind_for_episodic(EpisodicMemoryKind.PREFERENCE) is None
