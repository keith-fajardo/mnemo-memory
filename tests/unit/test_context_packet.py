import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mnemo_memory.packages.domain import (
    BudgetOverride,
    ConflictNotice,
    ConflictState,
    ContentRepresentation,
    ContextBudget,
    ContextItem,
    ContextItemType,
    ContextPacket,
    EventId,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryScope,
    OmissionNotice,
    OmissionReason,
    OverrideRequesterType,
    OwnerId,
    PacketSchemaVersion,
    ProjectId,
    ProvenanceNotice,
    RequestId,
    ScopeLevel,
    Sensitivity,
    SourceId,
    SourceTrustClass,
    TaskId,
    ValidityState,
    VerificationStatus,
    Visibility,
)

ROOT = Path(__file__).parents[2]
NOW = datetime(2026, 8, 2, 9, 0, tzinfo=UTC)
HASH = "sha256:" + "b" * 64


class CharacterEstimator:
    def estimate(self, content: str) -> int:
        return len(content)


def scope() -> MemoryScope:
    return MemoryScope(
        OwnerId.new(), ScopeLevel.PROJECT, Visibility.PROJECT, project_id=ProjectId.new()
    )


def evidence() -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.REPOSITORY,
        SourceTrustClass.CURRENT_STRUCTURAL,
        "git:abc:README.md",
        HASH,
        EvidenceLocation("repo://README.md", 1, 0, 1, 4),
        NOW,
        VerificationStatus.VERIFIED,
    )


def item(item_id: str, item_type: ContextItemType, tokens: int = 1) -> ContextItem:
    return ContextItem(
        item_id,
        item_type,
        scope(),
        "untrusted evidence",
        ContentRepresentation.UNTRUSTED_EVIDENCE,
        tokens,
        (evidence(),),
        SourceTrustClass.CURRENT_STRUCTURAL,
        Sensitivity.NORMAL,
        ValidityState.CURRENT,
        None,
        ConflictState.NONE,
        NOW,
    )


def provenance(context_item: ContextItem, tokens: int = 0) -> ProvenanceNotice:
    return ProvenanceNotice(
        f"provenance:{context_item.item_id}",
        context_item.item_id,
        "repo://README.md",
        HASH,
        context_item.evidence_references,
        tokens,
    )


def packet(**changes: object) -> ContextPacket:
    values: dict[str, object] = {
        "schema_version": PacketSchemaVersion.V1,
        "request_id": RequestId.new(),
        "owner_scope": scope(),
        "query_id": "resume task",
        "task_id": None,
        "created_at": NOW,
        "expires_at": None,
        "declared_total_tokens": 0,
        "budget": ContextBudget(),
        "producer_version": "mnemo-test/1",
    }
    values.update(changes)
    return ContextPacket(**values)  # type: ignore[arg-type]


def test_minimal_empty_packet_and_representative_json_fixture_are_valid() -> None:
    assert packet().remaining_budget == 5700
    fixture = json.loads((ROOT / "tests/fixtures/context-packet-v1-minimal.json").read_text())
    parsed = ContextPacket.from_dict(fixture)
    assert parsed.to_dict() == fixture


def test_fully_populated_packet_preserves_conflict_and_round_trips() -> None:
    checkpoint = item("checkpoint", ContextItemType.ACTIVE_TASK_CHECKPOINT, 600)
    episodic = item("episode", ContextItemType.EPISODIC_MEMORY, 800)
    knowledge = item("knowledge", ContextItemType.KNOWLEDGE, 1200)
    structural = item("structure", ContextItemType.STRUCTURAL_FACT, 1500)
    skill = item("skill", ContextItemType.MANDATORY_PROCEDURE, 1200)
    items = (checkpoint, episodic, knowledge, structural, skill)
    conflict = ConflictNotice(
        "conflict:1",
        ("episode", "knowledge"),
        episodic.evidence_references,
        ConflictState.UNRESOLVED,
        0,
    )
    result = packet(
        active_task_checkpoint=checkpoint,
        episodic_memories=(episodic,),
        knowledge_items=(knowledge,),
        structural_items=(structural,),
        skills_and_procedures=(skill,),
        provenance=tuple(provenance(value) for value in items),
        conflicts=(conflict,),
        omissions=(OmissionNotice("unselected", OmissionReason.LOWER_RANK),),
        declared_total_tokens=5300,
    )
    assert result.remaining_budget == 400
    assert result.conflicts[0].state is ConflictState.UNRESOLVED
    assert ContextPacket.from_json(result.to_json()) == result


@pytest.mark.parametrize(
    ("attribute", "item_type", "limit"),
    [
        ("active_task_checkpoint", ContextItemType.ACTIVE_TASK_CHECKPOINT, 600),
        ("episodic_memories", ContextItemType.EPISODIC_MEMORY, 800),
        ("knowledge_items", ContextItemType.KNOWLEDGE, 1200),
        ("structural_items", ContextItemType.STRUCTURAL_FACT, 1500),
        ("skills_and_procedures", ContextItemType.SKILL, 1200),
    ],
)
def test_section_boundaries_and_overflow(
    attribute: str, item_type: ContextItemType, limit: int
) -> None:
    value = item(attribute, item_type, limit)
    changes: dict[str, object] = {
        attribute: value if attribute == "active_task_checkpoint" else (value,)
    }
    changes["provenance"] = (provenance(value),)
    changes["declared_total_tokens"] = limit
    assert packet(**changes).computed_total_tokens == limit
    overflow = item(f"{attribute}:overflow", item_type, limit + 1)
    changes[attribute] = overflow if attribute == "active_task_checkpoint" else (overflow,)
    changes["provenance"] = (provenance(overflow),)
    changes["declared_total_tokens"] = limit + 1
    with pytest.raises(ValueError, match="exceeds its token budget"):
        packet(**changes)


def test_total_boundary_overflow_and_declared_total_are_rejected() -> None:
    first = item("a", ContextItemType.EPISODIC_MEMORY, 800)
    second = item("b", ContextItemType.KNOWLEDGE, 1200)
    third = item("c", ContextItemType.STRUCTURAL_FACT, 1500)
    fourth = item("d", ContextItemType.SKILL, 1200)
    fifth = item("e", ContextItemType.ACTIVE_TASK_CHECKPOINT, 600)
    notices = (
        provenance(first, 400),
        *(provenance(value) for value in (second, third, fourth, fifth)),
    )
    exact = packet(
        active_task_checkpoint=fifth,
        episodic_memories=(first,),
        knowledge_items=(second,),
        structural_items=(third,),
        skills_and_procedures=(fourth,),
        provenance=notices,
        declared_total_tokens=5700,
    )
    assert exact.computed_total_tokens == 5700
    with pytest.raises(ValueError, match="declared_total_tokens"):
        packet(
            episodic_memories=(first,), provenance=(provenance(first),), declared_total_tokens=799
        )
    over_budget = ContextBudget(total_limit=5299)
    with pytest.raises(ValueError, match="total hard"):
        packet(
            active_task_checkpoint=fifth,
            episodic_memories=(first,),
            knowledge_items=(second,),
            structural_items=(third,),
            skills_and_procedures=(fourth,),
            provenance=notices,
            declared_total_tokens=5700,
            budget=over_budget,
        )
    too_large_provenance = provenance(first, 401)
    with pytest.raises(ValueError, match="provenance_and_conflicts exceeds"):
        packet(
            episodic_memories=(first,),
            provenance=(too_large_provenance,),
            declared_total_tokens=1201,
        )


def test_explicit_override_and_negative_token_validation() -> None:
    override = BudgetOverride(OverrideRequesterType.OWNER, "owner-1", "large approved review", NOW)
    assert ContextBudget(total_limit=8001, override=override).override == override
    with pytest.raises(ValueError, match="require an explicit"):
        ContextBudget(total_limit=8001)
    with pytest.raises(ValueError, match="valid only"):
        ContextBudget(override=override)
    with pytest.raises(ValueError, match="non-negative"):
        item("negative", ContextItemType.KNOWLEDGE, -1)


def test_missing_provenance_invalid_scope_cross_type_id_and_time_are_rejected() -> None:
    entry = item("entry", ContextItemType.EPISODIC_MEMORY)
    with pytest.raises(ValueError, match="provenance"):
        packet(episodic_memories=(entry,), declared_total_tokens=1)
    with pytest.raises(TypeError, match="TaskId"):
        packet(query_id=None, task_id=EventId.new())
    with pytest.raises(ValueError, match="expires_at"):
        packet(expires_at=NOW - timedelta(seconds=1))
    with pytest.raises(ValueError, match="timezone-aware"):
        packet(created_at=datetime(2026, 8, 2, 9, 0))


def test_all_omission_codes_and_unknown_fields_are_strict() -> None:
    omissions = tuple(OmissionNotice(f"item:{reason.value}", reason) for reason in OmissionReason)
    result = packet(omissions=omissions)
    assert {item.reason for item in result.omissions} == set(OmissionReason)
    serialized = result.to_dict()
    serialized["unexpected"] = True
    with pytest.raises(ValueError, match="unknown"):
        ContextPacket.from_dict(serialized)


def test_schema_contract_matches_model_and_fixture_shape() -> None:
    schema = json.loads((ROOT / "schemas/context-packet-v1.json").read_text())
    model_keys = set(packet().to_dict())
    assert set(schema["required"]) == model_keys == set(schema["properties"])
    assert schema["properties"]["schema_version"]["const"] == PacketSchemaVersion.V1.value
    assert schema["$defs"]["omission"]["properties"]["reason"]["enum"] == [
        reason.value for reason in OmissionReason
    ]
    assert schema["$defs"]["item"]["properties"]["item_type"]["enum"] == [
        kind.value for kind in ContextItemType
    ]
    assert (
        schema["$defs"]["item"]["properties"]["content_representation"]["const"]
        == ContentRepresentation.UNTRUSTED_EVIDENCE.value
    )
    fixture = json.loads((ROOT / "tests/fixtures/context-packet-v1-minimal.json").read_text())
    assert ContextPacket.from_dict(fixture).schema_version is PacketSchemaVersion.V1


def test_deterministic_test_estimator_has_no_provider_dependency() -> None:
    assert CharacterEstimator().estimate("four") == 4
    assert TaskId.from_string(str(TaskId.new())) != TaskId.from_string(str(TaskId.new()))
