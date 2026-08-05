from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from mnemo_memory.apps.api.app import create_app
from mnemo_memory.apps.api.memories import (
    ApprovedMemoryActionError,
    ApprovedMemoryActionInvalid,
    ApprovedMemoryActionNotFound,
    ApprovedMemoryBrowserError,
    build_approved_memory_page,
    correct_approved_memory,
    retract_approved_memory,
    set_approved_memory_pin,
)
from mnemo_memory.packages.application import (
    CorrectApprovedEpisodicEvent,
    LocalConfig,
    RecordApprovedEpisodicEvent,
    RetractApprovedEpisodicEvent,
    build_checkpoint_runtime,
    build_lifecycle_service,
)
from mnemo_memory.packages.application.automatic_memory import LocalMemoryProjectBindingStore
from mnemo_memory.packages.domain import (
    ApprovedEventKind,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    SourceId,
    SourceTrustClass,
    VerificationStatus,
)

NOW = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


def _evidence(seed: str, *, correction: bool = False) -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.USER_CORRECTION if correction else EvidenceSourceType.TOOL_RESULT,
        SourceTrustClass.USER_CORRECTION if correction else SourceTrustClass.VERIFIED_TOOL_RESULT,
        f"fixture://memory-browser/{seed}",
        "sha256:" + ("b" if correction else "a") * 64,
        EvidenceLocation(f"fixture://memory-browser/{seed}", 1, 0, 1, 8),
        NOW,
        VerificationStatus.VERIFIED,
    )


def test_memory_browser_is_scoped_and_preserves_evidence_and_revision_lineage(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    other = tmp_path / "other"
    unregistered = tmp_path / "unregistered"
    project.mkdir()
    other.mkdir()
    unregistered.mkdir()
    config = LocalConfig.defaults(tmp_path / "profile")
    bindings = LocalMemoryProjectBindingStore(config.data_directory)
    binding = bindings.enable(project)
    bindings.enable(other)
    with build_checkpoint_runtime(config) as runtime:
        service = runtime.checkpoint_service
        active = service.record_approved_event(
            RecordApprovedEpisodicEvent(
                binding.checkpoint_scope,
                ApprovedEventKind.TOOL_OUTCOME,
                "The bounded project validation passed.",
                "browser:active",
                (_evidence("active"),),
            )
        ).event
        original = service.record_approved_event(
            RecordApprovedEpisodicEvent(
                binding.checkpoint_scope,
                ApprovedEventKind.DECISION,
                "Use the original project grain.",
                "browser:original",
                (_evidence("original"),),
            )
        ).event
        correction = service.correct_approved_event(
            CorrectApprovedEpisodicEvent(
                binding.checkpoint_scope,
                original.event_id,
                "Use the corrected project grain.",
                "browser:replacement",
                "Verified evidence changed the decision.",
                "browser:correct",
                (_evidence("correct", correction=True),),
            )
        )
        assert correction.replacement is not None
        replacement_id = correction.replacement.event_id
        service.retract_approved_event(
            RetractApprovedEpisodicEvent(
                binding.checkpoint_scope,
                replacement_id,
                "The user withdrew the replacement fact.",
                "browser:retract",
                (_evidence("retract", correction=True),),
            )
        )

    page = build_approved_memory_page(config, project_directory=project, limit=10)

    assert page["project_registered"] is True
    assert page["next_offset"] is None
    page_items = cast(list[dict[str, object]], page["items"])
    items = {str(item["event_id"]): item for item in page_items}
    active_record = items[str(active.event_id)]
    active_evidence = cast(list[dict[str, object]], active_record["evidence"])
    assert active_record["status"] == "active"
    assert active_evidence[0]["source_type"] == "tool_result"
    corrected = items[str(original.event_id)]
    corrected_governance = cast(dict[str, object], corrected["governance"])
    correction_evidence = cast(list[dict[str, object]], corrected_governance["evidence"])
    assert corrected["status"] == "corrected"
    assert corrected_governance["replacement_event_id"] == str(replacement_id)
    assert correction_evidence[0]["trust_class"] == "user_correction"
    retracted = items[str(replacement_id)]
    retraction_governance = cast(dict[str, object], retracted["governance"])
    assert retracted["status"] == "retracted"
    assert retracted["summary"] is None
    assert retracted["evidence"] == []
    assert retraction_governance["reason"] == "The user withdrew the replacement fact."
    encoded = json.dumps(page, sort_keys=True)
    assert str(binding.scope.project_id) not in encoded
    assert str(binding.checkpoint_scope.task_id) not in encoded

    assert build_approved_memory_page(config, project_directory=other)["items"] == []
    assert build_approved_memory_page(config, project_directory=unregistered) == {
        "project_registered": False,
        "items": [],
        "next_offset": None,
    }


def test_memory_browser_api_bounds_pagination_and_sanitizes_failures(tmp_path: Path) -> None:
    config = LocalConfig.defaults(tmp_path / "profile")
    service = build_lifecycle_service(config)
    service.initialize()
    observed: list[tuple[int, int]] = []

    def page(offset: int, limit: int) -> dict[str, object]:
        observed.append((offset, limit))
        return {"project_registered": True, "items": [], "next_offset": None}

    client = TestClient(create_app(service, approved_memory_page=page))
    response = client.get("/api/memories?offset=2&limit=3")
    assert response.status_code == 200
    assert observed == [(2, 3)]
    assert client.get("/api/memories?offset=-1").status_code == 422
    assert client.get("/api/memories?limit=101").status_code == 422

    def unavailable(_: int, __: int) -> dict[str, object]:
        raise ApprovedMemoryBrowserError("private storage detail")

    failed = TestClient(create_app(service, approved_memory_page=unavailable)).get("/api/memories")
    assert failed.status_code == 503
    assert failed.json() == {"detail": "MNEMO_MEMORY_BROWSER_UNAVAILABLE"}
    assert "private storage detail" not in failed.text


def test_browser_actions_correct_idempotently_and_erase_payload_in_exact_scope(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    other = tmp_path / "other"
    project.mkdir()
    other.mkdir()
    config = LocalConfig.defaults(tmp_path / "profile")
    bindings = LocalMemoryProjectBindingStore(config.data_directory)
    binding = bindings.enable(project)
    bindings.enable(other)
    original_summary = "Use the account grain for this project."
    corrected_summary = "Use the verified transaction grain for this project."
    with build_checkpoint_runtime(config) as runtime:
        original = runtime.checkpoint_service.record_approved_event(
            RecordApprovedEpisodicEvent(
                binding.checkpoint_scope,
                ApprovedEventKind.DECISION,
                original_summary,
                "browser-action:original",
                (_evidence("browser-action"),),
            )
        ).event

    correction_value = {
        "summary": corrected_summary,
        "reason": "The verified project evidence disproved the original grain.",
    }
    pinned = set_approved_memory_pin(
        config, str(original.event_id), {"pinned": True}, project_directory=project
    )
    pin_retry = set_approved_memory_pin(
        config, str(original.event_id), {"pinned": True}, project_directory=project
    )
    assert pinned["idempotent"] is False
    assert pin_retry["idempotent"] is True
    corrected = correct_approved_memory(
        config, str(original.event_id), correction_value, project_directory=project
    )
    retried = correct_approved_memory(
        config, str(original.event_id), correction_value, project_directory=project
    )
    assert corrected["idempotent"] is False
    assert retried["idempotent"] is True
    replacement = cast(dict[str, object], corrected["replacement"])
    replacement_id = str(replacement["event_id"])
    assert replacement["pinned"] is True

    with pytest.raises(ApprovedMemoryActionNotFound):
        retract_approved_memory(
            config,
            replacement_id,
            {"reason": "Another project cannot erase this fact."},
            project_directory=other,
        )
    retraction_value = {"reason": "The user withdrew this retained project fact."}
    retracted = retract_approved_memory(
        config, replacement_id, retraction_value, project_directory=project
    )
    retracted_retry = retract_approved_memory(
        config, replacement_id, retraction_value, project_directory=project
    )
    assert retracted["idempotent"] is False
    assert retracted_retry["idempotent"] is True
    tombstone = cast(dict[str, object], retracted["retracted"])
    assert tombstone["summary"] is None
    assert tombstone["evidence"] == []
    with sqlite3.connect(config.database_path) as connection:
        dump = "\n".join(connection.iterdump())
    assert corrected_summary not in dump
    assert original_summary in dump

    with pytest.raises(ApprovedMemoryActionInvalid):
        correct_approved_memory(
            config,
            str(original.event_id),
            {**correction_value, "api_key": "prohibited"},
            project_directory=project,
        )


def test_memory_write_api_requires_explicit_same_origin_intent_and_safe_errors(
    tmp_path: Path,
) -> None:
    config = LocalConfig.defaults(tmp_path / "profile")
    service = build_lifecycle_service(config)
    service.initialize()
    actions: list[tuple[str, str, object]] = []

    def correct(event_id: str, value: object) -> dict[str, object]:
        actions.append(("correct", event_id, value))
        return {"idempotent": False}

    def retract(event_id: str, value: object) -> dict[str, object]:
        actions.append(("retract", event_id, value))
        return {"idempotent": False}

    def pin(event_id: str, value: object) -> dict[str, object]:
        actions.append(("pin", event_id, value))
        return {"idempotent": False}

    client = TestClient(
        create_app(
            service,
            correct_approved_memory=correct,
            retract_approved_memory=retract,
            set_approved_memory_pin=pin,
        ),
        base_url="http://127.0.0.1:8765",
    )
    event_id = "00000000-0000-0000-0000-000000000001"
    correction = {"summary": "Corrected", "reason": "Verified"}
    assert client.post(f"/api/memories/{event_id}/correct", json=correction).status_code == 403
    assert (
        client.post(
            f"/api/memories/{event_id}/correct",
            json=correction,
            headers={
                "Origin": "https://attacker.example",
                "X-Mnemo-Intent": "correct-memory",
            },
        ).status_code
        == 403
    )
    corrected = client.post(
        f"/api/memories/{event_id}/correct",
        json=correction,
        headers={
            "Origin": "http://127.0.0.1:8765",
            "X-Mnemo-Intent": "correct-memory",
        },
    )
    assert corrected.status_code == 200
    erased = client.request(
        "DELETE",
        f"/api/memories/{event_id}",
        json={"reason": "Withdrawn"},
        headers={
            "Origin": "http://127.0.0.1:8765",
            "X-Mnemo-Intent": "retract-memory",
        },
    )
    assert erased.status_code == 200
    pinned_response = client.put(
        f"/api/memories/{event_id}/pin",
        json={"pinned": True},
        headers={
            "Origin": "http://127.0.0.1:8765",
            "X-Mnemo-Intent": "pin-memory",
        },
    )
    assert pinned_response.status_code == 200
    assert [item[0] for item in actions] == ["correct", "retract", "pin"]

    def unavailable(_: str, __: object) -> dict[str, object]:
        raise ApprovedMemoryActionError("private failure detail")

    failed = TestClient(
        create_app(service, correct_approved_memory=unavailable),
        base_url="http://127.0.0.1:8765",
    ).post(
        f"/api/memories/{event_id}/correct",
        json=correction,
        headers={
            "Origin": "http://127.0.0.1:8765",
            "X-Mnemo-Intent": "correct-memory",
        },
    )
    assert failed.status_code == 503
    assert failed.json() == {"detail": "MNEMO_MEMORY_ACTION_UNAVAILABLE"}
    assert "private failure detail" not in failed.text
