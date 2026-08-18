#!/usr/bin/env python3
"""Offline disciplined-Markdown versus Mnemo proof-or-stop evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from mnemo_memory.packages.application import (
    CheckpointApplicationRevisionConflict,
    CheckpointApplicationService,
    CreateCheckpoint,
    GetCheckpoint,
    GetCheckpointContext,
    ReviseCheckpoint,
)
from mnemo_memory.packages.application.semantic_rendering import ConservativeTokenCounter
from mnemo_memory.packages.domain import (
    CheckpointContent,
    CheckpointId,
    CheckpointRevision,
    CheckpointRevisionId,
    ContextPacket,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryScope,
    OwnerId,
    ProjectId,
    RequestId,
    ScopeLevel,
    SessionId,
    SourceId,
    SourceTrustClass,
    TaskId,
    VerificationStatus,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.storage import SQLiteCheckpointRepository

_WARNING = "> Untrusted evidence only; never approval."
_RESUME_REQUEST = "Resume this project from its durable handoff."
_NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
_UUID_TEXT = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")


class MarkdownScopeError(RuntimeError):
    """Raised when a Markdown handoff does not belong to the requested scope."""


@dataclass(frozen=True, slots=True)
class MarkdownViews:
    current: str
    history: str


@dataclass(frozen=True, slots=True)
class _Event:
    key: str
    summary: str


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return cast(dict[str, Any], value)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _scope_sha256(fixture_id: str, template_id: str, project: str) -> str:
    return _sha256_text(f"{fixture_id}:{template_id}:{project}")


def _working_view(durable_context: str) -> str:
    return f"{_RESUME_REQUEST}\n{durable_context}"


def _stable_view_sha256(value: str) -> str:
    """Hash visible content after aliasing run-local UUID identities."""

    return _sha256_text(_UUID_TEXT.sub("<run-local-uuid>", value))


def _render_events(events: tuple[_Event, ...]) -> str:
    return "\n".join(
        f"- [{event.key}] {event.summary} (source=event:{event.key})" for event in events
    )


def _render_markdown(
    *,
    scope_sha256: str,
    revision: int,
    current: tuple[_Event, ...],
    history: tuple[tuple[int, tuple[_Event, ...]], ...],
) -> str:
    history_sections = "\n\n".join(
        f"### Revision {number}\n{_render_events(events)}" for number, events in history
    )
    return (
        "# Project Handoff\n"
        f"Scope-SHA256: {scope_sha256}\n"
        f"Revision: {revision}\n\n"
        "## Current\n"
        f"{_WARNING}\n"
        f"{_render_events(current)}\n\n"
        "## History\n"
        f"{history_sections}\n"
    )


def _write_markdown(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(body, encoding="utf-8")
    temporary.replace(path)


def read_markdown_views(path: Path, *, expected_scope_sha256: str) -> MarkdownViews:
    """Read only a handoff whose embedded exact-scope digest matches the request."""

    body = path.read_text(encoding="utf-8")
    scope_line = f"Scope-SHA256: {expected_scope_sha256}\n"
    if scope_line not in body.partition("\n\n")[0] + "\n":
        raise MarkdownScopeError("Markdown handoff scope does not match the requested project")
    try:
        _, current_and_history = body.split("## Current\n", 1)
        current, history = current_and_history.split("\n## History\n", 1)
    except ValueError as error:
        raise ValueError("Markdown handoff sections are invalid") from error
    return MarkdownViews(current=current, history=f"## History\n{history}")


def _events_by_key(source_template: dict[str, Any]) -> dict[str, _Event]:
    return {
        event["event_key"]: _Event(event["event_key"], event["summary"])
        for event in source_template["events"]
    }


def _selected_events(keys: list[str], events: dict[str, _Event]) -> tuple[_Event, ...]:
    return tuple(events[key] for key in keys)


def build_markdown_rows(repository_root: Path, work_directory: Path) -> tuple[dict[str, Any], ...]:
    """Build the six deterministic disciplined-Markdown rows without retaining payloads."""

    fixture = _load_object(repository_root / "tests/fixtures/evals/markdown-handoff-proof-v1.json")
    source = _load_object(repository_root / cast(str, fixture["source_corpus"]))
    source_templates = {
        item["template_id"]: item for item in source["templates"] if isinstance(item, dict)
    }
    counter = ConservativeTokenCounter()
    rows: list[dict[str, Any]] = []
    for scenario in fixture["scenario_families"]:
        template_id = cast(str, scenario["template_id"])
        events = _events_by_key(source_templates[template_id])
        initial_keys = cast(list[str], scenario["initial_event_keys"])
        revised_keys = cast(list[str], scenario["revised_event_keys"])
        superseded_key = cast(str | None, scenario["superseded_event_key"])
        initial = _selected_events(initial_keys, events)
        revision_delta = _selected_events(revised_keys, events)
        current_keys = [key for key in (*initial_keys, *revised_keys) if key != superseded_key]
        current_events = _selected_events(current_keys, events)
        scope_digest = _scope_sha256(fixture["fixture_id"], template_id, "primary")
        path = work_directory / template_id / "HANDOFF.md"

        initial_body = _render_markdown(
            scope_sha256=scope_digest,
            revision=1,
            current=initial,
            history=((1, initial),),
        )
        _write_markdown(path, initial_body)
        stale_writer_snapshot = initial_body

        winning_body = _render_markdown(
            scope_sha256=scope_digest,
            revision=2,
            current=current_events,
            history=((1, initial), (2, revision_delta)),
        )
        _write_markdown(path, winning_body)
        views = read_markdown_views(path, expected_scope_sha256=scope_digest)

        protected_scope = _scope_sha256(fixture["fixture_id"], template_id, "protected")
        protected_marker = f"protected-cross-project:{template_id}"
        protected_event = _Event("protected", protected_marker)
        protected_path = work_directory / template_id / "protected" / "HANDOFF.md"
        _write_markdown(
            protected_path,
            _render_markdown(
                scope_sha256=protected_scope,
                revision=1,
                current=(protected_event,),
                history=((1, (protected_event,)),),
            ),
        )
        try:
            read_markdown_views(protected_path, expected_scope_sha256=scope_digest)
        except MarkdownScopeError:
            cross_scope_disclosure_count = 0
        else:
            cross_scope_disclosure_count = 1

        stale_body = stale_writer_snapshot.replace("Revision: 1", "Revision: 2", 1).replace(
            "## History\n", "## History\n### Stale writer overwrite\n", 1
        )
        _write_markdown(path, stale_body)
        stale_update_rejected = path.read_text(encoding="utf-8") != stale_body
        winning_revision_preserved = _sha256_text(path.read_text(encoding="utf-8")) == _sha256_text(
            winning_body
        )
        _write_markdown(path, winning_body)

        current_event = events[scenario["current_event_key"]]
        evidence_event = events[scenario["evidence_event_key"]]
        next_action_event = events[scenario["next_action_event_key"]]
        superseded_event = None if superseded_key is None else events[superseded_key]
        history_fidelity = (
            None
            if superseded_event is None
            else float(
                superseded_event.summary in views.history and current_event.summary in views.history
            )
        )
        superseded_exclusion = (
            None
            if superseded_event is None
            else float(superseded_event.summary not in views.current)
        )
        rows.append(
            {
                "schema_version": "mnemo-markdown-handoff-proof-row/1.0",
                "fixture_id": fixture["fixture_id"],
                "scenario_family": template_id,
                "condition": "DM",
                "current_event_key": current_event.key,
                "superseded_event_key": superseded_key,
                "evidence_event_key": evidence_event.key,
                "next_action_event_key": next_action_event.key,
                "supersession_applicable": superseded_event is not None,
                "required_current_fact_available": float(current_event.summary in views.current),
                "evidence_attribution_fidelity": float(
                    evidence_event.summary in views.current
                    and f"source=event:{evidence_event.key}" in views.current
                ),
                "next_action_available": float(next_action_event.summary in views.current),
                "superseded_current_exclusion": superseded_exclusion,
                "evolution_history_fidelity": history_fidelity,
                "critical_false_memory_count": 0,
                "cross_scope_disclosure_count": cross_scope_disclosure_count,
                "memory_necessity_valid": True,
                "resume_request_sha256": _sha256_text(_RESUME_REQUEST),
                "current_view_tokens": counter.count(_working_view(views.current)),
                "history_view_tokens": counter.count(views.history),
                "current_view_sha256": _stable_view_sha256(_working_view(views.current)),
                "history_view_sha256": _stable_view_sha256(views.history),
                "scope_sha256": scope_digest,
                "revision_history_count": views.history.count("### Revision "),
                "stale_update_rejected": stale_update_rejected,
                "winning_revision_preserved": winning_revision_preserved,
                "storage_backend": "markdown",
                "checkpoint_revision_count": 2,
                "checkpoint_compacted": False,
                "original_token_estimate": None,
                "final_token_estimate": None,
                "truncated_field_count": 0,
                "mnemo_model_tokens": {"input": 0, "output": 0},
                "local_deterministic_work": {
                    "file_writes": 5,
                    "file_reads": 4,
                    "model_calls": 0,
                },
                "stored_payload_fields": (),
            }
        )
    return tuple(rows)


def _uuid(label: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"mnemo-markdown-handoff-proof:{label}")


def _scope(template_id: str, project: str) -> MemoryScope:
    prefix = f"{template_id}:{project}"
    return MemoryScope(
        owner_id=OwnerId(_uuid("owner")),
        level=ScopeLevel.TASK,
        visibility=Visibility.PROJECT,
        workspace_id=WorkspaceId(_uuid("workspace")),
        project_id=ProjectId(_uuid(f"{prefix}:project")),
        session_id=SessionId(_uuid(f"{prefix}:session")),
        task_id=TaskId(_uuid(f"{prefix}:task")),
    )


def _evidence(template_id: str, project: str, event: _Event) -> EvidenceReference:
    prefix = f"{template_id}:{project}:{event.key}"
    return EvidenceReference(
        evidence_id=EvidenceId(_uuid(f"{prefix}:evidence")),
        source_id=SourceId(_uuid(f"{prefix}:source")),
        source_type=EvidenceSourceType.CHECKPOINT,
        trust_class=SourceTrustClass.USER_AUTHORED,
        immutable_source_ref=f"event:{event.key}",
        content_hash=f"sha256:{_sha256_text(event.summary)}",
        location=EvidenceLocation(f"fixture://markdown-handoff-proof/{template_id}/{event.key}"),
        observed_at=_NOW,
        verification_status=VerificationStatus.VERIFIED,
    )


def _first_with_prefix(events: tuple[_Event, ...], prefix: str) -> _Event | None:
    return next((event for event in events if event.summary.startswith(prefix)), None)


def _checkpoint_content(
    events: tuple[_Event, ...],
    *,
    current_event: _Event,
    evidence_event: _Event,
    next_action_event: _Event | None,
) -> CheckpointContent:
    objective = _first_with_prefix(events, "goal:") or current_event
    failure = _first_with_prefix(events, "failure:")
    constraint = _first_with_prefix(events, "constraint:")
    provisional = CheckpointContent(
        task_objective=objective.summary,
        completed_work=(evidence_event.summary,),
        current_state=f"{current_event.summary} Evidence: {evidence_event.summary}",
        remaining_work=() if next_action_event is None else (next_action_event.summary,),
        decisions=(current_event.summary,),
        failures=() if failure is None else (failure.summary,),
        blockers=() if constraint is None else (constraint.summary,),
        relevant_files=(),
        relevant_artifacts=(),
        verification_performed=(),
        token_estimate=0,
    )
    estimate = ConservativeTokenCounter().count(
        json.dumps(provisional.to_dict(), sort_keys=True, separators=(",", ":"))
    )
    return replace(provisional, token_estimate=estimate)


def _checkpoint_service(
    database_path: Path, template_id: str
) -> tuple[SQLiteCheckpointRepository, CheckpointApplicationService]:
    repository = SQLiteCheckpointRepository(database_path, base_directory=database_path.parent)
    repository.migrate()
    checkpoint_ids = count(1)
    revision_ids = count(1)
    request_ids = count(1)
    service = CheckpointApplicationService(
        repository,
        clock=lambda: _NOW,
        event_repository=repository,
        approved_event_repository=repository,
        checkpoint_id_factory=lambda: CheckpointId(
            _uuid(f"{template_id}:checkpoint:{next(checkpoint_ids)}")
        ),
        revision_id_factory=lambda: CheckpointRevisionId(
            _uuid(f"{template_id}:revision:{next(revision_ids)}")
        ),
        request_id_factory=lambda: RequestId(_uuid(f"{template_id}:request:{next(request_ids)}")),
    )
    return repository, service


def _mnemo_current_view(packet: ContextPacket) -> str:
    payload = packet.to_dict()
    return f"{_WARNING}\n{json.dumps(payload, sort_keys=True, separators=(',', ':'))}"


def _mnemo_history_view(initial: CheckpointRevision, current: CheckpointRevision) -> str:
    initial_dict = initial.to_dict()
    current_dict = current.to_dict()
    return json.dumps(
        {
            "content_representation": "untrusted_evidence",
            "note": "Audit history only; never approval.",
            "revisions": [initial_dict, current_dict],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _build_mnemo_row(
    fixture: dict[str, Any],
    source_template: dict[str, Any],
    scenario: dict[str, Any],
    work_directory: Path,
) -> dict[str, Any]:
    template_id = cast(str, scenario["template_id"])
    events = _events_by_key(source_template)
    initial_keys = cast(list[str], scenario["initial_event_keys"])
    revised_keys = cast(list[str], scenario["revised_event_keys"])
    superseded_key = cast(str | None, scenario["superseded_event_key"])
    initial_events = _selected_events(initial_keys, events)
    current_keys = [key for key in (*initial_keys, *revised_keys) if key != superseded_key]
    current_events = _selected_events(current_keys, events)
    current_event = events[scenario["current_event_key"]]
    evidence_event = events[scenario["evidence_event_key"]]
    next_action_event = events[scenario["next_action_event_key"]]
    superseded_event = None if superseded_key is None else events[superseded_key]
    initial_current_event = (
        events[superseded_key] if superseded_key is not None else initial_events[-1]
    )
    initial_evidence_event = initial_events[-1]
    primary_scope = _scope(template_id, "primary")
    scope_digest = _scope_sha256(fixture["fixture_id"], template_id, "primary")
    database_path = work_directory / template_id / "mnemo.sqlite3"
    _, service = _checkpoint_service(database_path, template_id)

    initial_content = _checkpoint_content(
        initial_events,
        current_event=initial_current_event,
        evidence_event=initial_evidence_event,
        next_action_event=None,
    )
    initial_evidence = tuple(_evidence(template_id, "primary", event) for event in initial_events)
    initial = service.create(CreateCheckpoint(primary_scope, initial_content, initial_evidence))
    revised_content = _checkpoint_content(
        current_events,
        current_event=current_event,
        evidence_event=evidence_event,
        next_action_event=next_action_event,
    )
    revised_evidence = tuple(_evidence(template_id, "primary", event) for event in current_events)
    winning = service.revise(
        ReviseCheckpoint(
            primary_scope,
            initial.aggregate.checkpoint_id,
            initial.revision.revision_id,
            revised_content,
            revised_evidence,
        )
    )

    try:
        service.revise(
            ReviseCheckpoint(
                primary_scope,
                initial.aggregate.checkpoint_id,
                initial.revision.revision_id,
                initial_content,
                initial_evidence,
            )
        )
    except CheckpointApplicationRevisionConflict:
        stale_update_rejected = True
    else:
        stale_update_rejected = False
    current_revision = service.get(
        GetCheckpoint(primary_scope, initial.aggregate.checkpoint_id)
    ).revision
    winning_revision_preserved = current_revision.revision_id == winning.revision.revision_id

    protected_scope = _scope(template_id, "protected")
    protected_event = _Event("protected", f"protected-cross-project:{template_id}")
    service.create(
        CreateCheckpoint(
            protected_scope,
            _checkpoint_content(
                (protected_event,),
                current_event=protected_event,
                evidence_event=protected_event,
                next_action_event=None,
            ),
            (_evidence(template_id, "protected", protected_event),),
        )
    )
    packet = service.get_context(GetCheckpointContext(primary_scope))
    current_view = _mnemo_current_view(packet)
    cross_scope_disclosure_count = int(protected_event.summary in current_view)
    historical_initial = service.get(
        GetCheckpoint(primary_scope, initial.aggregate.checkpoint_id, revision_number=1)
    ).revision
    historical_current = service.get(
        GetCheckpoint(primary_scope, initial.aggregate.checkpoint_id, revision_number=2)
    ).revision
    history_view = _mnemo_history_view(historical_initial, historical_current)
    working_view = _working_view(current_view)
    counter = ConservativeTokenCounter()
    item = packet.active_task_checkpoint
    evidence_reference = f"event:{evidence_event.key}"
    preparation = winning.preparation

    return {
        "schema_version": "mnemo-markdown-handoff-proof-row/1.0",
        "fixture_id": fixture["fixture_id"],
        "scenario_family": template_id,
        "condition": "MR",
        "current_event_key": current_event.key,
        "superseded_event_key": superseded_key,
        "evidence_event_key": evidence_event.key,
        "next_action_event_key": next_action_event.key,
        "supersession_applicable": superseded_event is not None,
        "required_current_fact_available": float(current_event.summary in current_view),
        "evidence_attribution_fidelity": float(
            evidence_event.summary in current_view and evidence_reference in current_view
        ),
        "next_action_available": float(next_action_event.summary in current_view),
        "superseded_current_exclusion": (
            None
            if superseded_event is None
            else float(superseded_event.summary not in current_view)
        ),
        "evolution_history_fidelity": (
            None
            if superseded_event is None
            else float(
                superseded_event.summary in history_view and current_event.summary in history_view
            )
        ),
        "critical_false_memory_count": 0,
        "cross_scope_disclosure_count": cross_scope_disclosure_count,
        "memory_necessity_valid": True,
        "resume_request_sha256": _sha256_text(_RESUME_REQUEST),
        "current_view_tokens": counter.count(working_view),
        "history_view_tokens": counter.count(history_view),
        "current_view_sha256": _stable_view_sha256(working_view),
        "history_view_sha256": _stable_view_sha256(history_view),
        "scope_sha256": scope_digest,
        "revision_history_count": 2,
        "stale_update_rejected": stale_update_rejected,
        "winning_revision_preserved": winning_revision_preserved,
        "storage_backend": "sqlite",
        "checkpoint_revision_count": 2,
        "checkpoint_compacted": False if preparation is None else preparation.compacted,
        "original_token_estimate": (
            None if preparation is None else preparation.original_token_estimate
        ),
        "final_token_estimate": winning.revision.content.token_estimate,
        "truncated_field_count": (0 if preparation is None else preparation.truncated_field_count),
        "content_representation": (None if item is None else item.content_representation.value),
        "mnemo_model_tokens": {"input": 0, "output": 0},
        "local_deterministic_work": {
            "checkpoint_write_attempts": 4,
            "checkpoint_reads": 4,
            "model_calls": 0,
        },
        "stored_payload_fields": (),
    }


def _no_memory_row(fixture_id: str, scenario: dict[str, Any]) -> dict[str, Any]:
    template_id = cast(str, scenario["template_id"])
    superseded_key = cast(str | None, scenario["superseded_event_key"])
    counter = ConservativeTokenCounter()
    return {
        "schema_version": "mnemo-markdown-handoff-proof-row/1.0",
        "fixture_id": fixture_id,
        "scenario_family": template_id,
        "condition": "NM",
        "current_event_key": scenario["current_event_key"],
        "superseded_event_key": superseded_key,
        "evidence_event_key": scenario["evidence_event_key"],
        "next_action_event_key": scenario["next_action_event_key"],
        "supersession_applicable": superseded_key is not None,
        "required_current_fact_available": 0.0,
        "evidence_attribution_fidelity": 0.0,
        "next_action_available": 0.0,
        "superseded_current_exclusion": None,
        "evolution_history_fidelity": None,
        "critical_false_memory_count": 0,
        "cross_scope_disclosure_count": 0,
        "memory_necessity_valid": True,
        "resume_request_sha256": _sha256_text(_RESUME_REQUEST),
        "current_view_tokens": counter.count(_RESUME_REQUEST),
        "history_view_tokens": 0,
        "current_view_sha256": _stable_view_sha256(_RESUME_REQUEST),
        "history_view_sha256": _stable_view_sha256(""),
        "scope_sha256": _scope_sha256(fixture_id, template_id, "primary"),
        "revision_history_count": 0,
        "stale_update_rejected": None,
        "winning_revision_preserved": None,
        "storage_backend": "none",
        "checkpoint_revision_count": 0,
        "checkpoint_compacted": False,
        "original_token_estimate": None,
        "final_token_estimate": None,
        "truncated_field_count": 0,
        "mnemo_model_tokens": {"input": 0, "output": 0},
        "local_deterministic_work": {"model_calls": 0},
        "stored_payload_fields": (),
    }


def build_offline_rows(repository_root: Path, work_directory: Path) -> tuple[dict[str, Any], ...]:
    """Build paired NM, disciplined-Markdown, and real-SQLite Mnemo rows."""

    fixture = _load_object(repository_root / "tests/fixtures/evals/markdown-handoff-proof-v1.json")
    source = _load_object(repository_root / cast(str, fixture["source_corpus"]))
    source_templates = {
        item["template_id"]: item for item in source["templates"] if isinstance(item, dict)
    }
    markdown = {
        row["scenario_family"]: row
        for row in build_markdown_rows(repository_root, work_directory / "markdown")
    }
    rows: list[dict[str, Any]] = []
    for scenario in fixture["scenario_families"]:
        template_id = cast(str, scenario["template_id"])
        rows.extend(
            (
                _no_memory_row(fixture["fixture_id"], scenario),
                markdown[template_id],
                _build_mnemo_row(
                    fixture,
                    source_templates[template_id],
                    scenario,
                    work_directory / "mnemo",
                ),
            )
        )
    return tuple(rows)


def _complete_groups(
    rows: tuple[dict[str, Any], ...],
) -> dict[str, dict[str, dict[str, Any]]] | None:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        family = row.get("scenario_family")
        condition = row.get("condition")
        if not isinstance(family, str) or condition not in {"NM", "DM", "MR"}:
            return None
        if condition in grouped.setdefault(family, {}):
            return None
        grouped[family][cast(str, condition)] = row
    if len(grouped) != 6 or any(set(group) != {"NM", "DM", "MR"} for group in grouped.values()):
        return None
    return grouped


def _durable_correct(row: dict[str, Any]) -> bool:
    base = (
        row.get("required_current_fact_available") == 1.0
        and row.get("evidence_attribution_fidelity") == 1.0
        and row.get("next_action_available") == 1.0
        and row.get("critical_false_memory_count") == 0
        and row.get("cross_scope_disclosure_count") == 0
    )
    if row.get("supersession_applicable") is True:
        return (
            base
            and row.get("superseded_current_exclusion") == 1.0
            and row.get("evolution_history_fidelity") == 1.0
        )
    return base


def decide_markdown_handoff_verdict(rows: tuple[dict[str, Any], ...]) -> str:
    """Apply only the frozen proof-or-stop verdict rules."""

    if not rows:
        return "NOT_EVALUATED"
    grouped = _complete_groups(rows)
    if grouped is None:
        return "INVALID"
    all_rows = tuple(row for group in grouped.values() for row in group.values())
    if any(
        row.get("stored_payload_fields") not in ((), [])
        or row.get("cross_scope_disclosure_count") != 0
        or row.get("mnemo_model_tokens") != {"input": 0, "output": 0}
        for row in all_rows
    ):
        return "INVALID"
    if any(
        group["NM"].get("required_current_fact_available") != 0.0
        or group["NM"].get("memory_necessity_valid") is not True
        for group in grouped.values()
    ):
        return "INVALID"
    dm_rows = tuple(group["DM"] for group in grouped.values())
    mr_rows = tuple(group["MR"] for group in grouped.values())
    dm_correct = all(_durable_correct(row) for row in dm_rows)
    mr_correct = all(_durable_correct(row) for row in mr_rows)
    if not dm_correct:
        return "INVALID"
    if not mr_correct:
        return "MARKDOWN_PREFERRED"
    dm_tokens = sum(cast(int, row["current_view_tokens"]) for row in dm_rows)
    mr_tokens = sum(cast(int, row["current_view_tokens"]) for row in mr_rows)
    compact = dm_tokens > 0 and mr_tokens <= 1.25 * dm_tokens
    dm_enforces = all(
        row.get("stale_update_rejected") is True and row.get("winning_revision_preserved") is True
        for row in dm_rows
    )
    mr_enforces = all(
        row.get("stale_update_rejected") is True and row.get("winning_revision_preserved") is True
        for row in mr_rows
    )
    if mr_enforces and not dm_enforces:
        return "DIFFERENTIATED" if compact else "TRADEOFF"
    return "PARITY" if compact else "MARKDOWN_PREFERRED"


def analyze_markdown_handoff_rows(rows: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    """Return a payload-free deterministic summary of the frozen measurements."""

    verdict = decide_markdown_handoff_verdict(rows)
    grouped = _complete_groups(rows)
    dm_rows = () if grouped is None else tuple(group["DM"] for group in grouped.values())
    mr_rows = () if grouped is None else tuple(group["MR"] for group in grouped.values())
    dm_tokens = sum(cast(int, row["current_view_tokens"]) for row in dm_rows)
    mr_tokens = sum(cast(int, row["current_view_tokens"]) for row in mr_rows)
    actions = {
        "INVALID": "REPAIR_EVALUATION",
        "NOT_EVALUATED": "RUN_OFFLINE_EVALUATION",
        "DIFFERENTIATED": "PROPOSE_LIVE_PILOT",
        "TRADEOFF": "SIMPLIFY_CONTEXT_BEFORE_TOKEN_CLAIM",
        "PARITY": "STOP_FEATURE_EXPANSION",
        "MARKDOWN_PREFERRED": "STOP_FEATURE_EXPANSION",
    }
    return {
        "schema_version": "mnemo-markdown-handoff-proof-analysis/1.0",
        "verdict": verdict,
        "action": actions[verdict],
        "row_count": len(rows),
        "scenario_family_count": 0 if grouped is None else len(grouped),
        "durable_correctness_gate": bool(dm_rows)
        and all(_durable_correct(row) for row in (*dm_rows, *mr_rows)),
        "markdown_stale_write_enforcement": bool(dm_rows)
        and all(
            row.get("stale_update_rejected") is True
            and row.get("winning_revision_preserved") is True
            for row in dm_rows
        ),
        "mnemo_stale_write_enforcement": bool(mr_rows)
        and all(
            row.get("stale_update_rejected") is True
            and row.get("winning_revision_preserved") is True
            for row in mr_rows
        ),
        "markdown_current_view_tokens": dm_tokens,
        "mnemo_current_view_tokens": mr_tokens,
        "current_token_ratio_mr_to_dm": None if dm_tokens == 0 else mr_tokens / dm_tokens,
        "markdown_history_view_tokens": sum(
            cast(int, row["history_view_tokens"]) for row in dm_rows
        ),
        "mnemo_history_view_tokens": sum(cast(int, row["history_view_tokens"]) for row in mr_rows),
        "mnemo_compacted_scenario_count": sum(
            row.get("checkpoint_compacted") is True for row in mr_rows
        ),
        "mnemo_model_tokens": {"input": 0, "output": 0},
        "model_generated_task_correctness": "NOT_EVALUATED",
        "claim_boundary": "offline_mechanical_comparison_only",
    }


def render_markdown_handoff_report(analysis: dict[str, Any]) -> str:
    """Render a short plain-language report without source payloads."""

    ratio = analysis["current_token_ratio_mr_to_dm"]
    ratio_text = "not available" if ratio is None else f"{cast(float, ratio):.2f}x"
    return (
        "# Disciplined Markdown vs Mnemo Offline Result\n\n"
        f"Verdict: `{analysis['verdict']}`\n\n"
        "This is an offline mechanical result. It does not prove that a model completes tasks "
        "better or that users prefer Mnemo.\n\n"
        f"- action: {analysis['action']}\n"
        f"- Markdown current-view tokens: {analysis['markdown_current_view_tokens']}\n"
        f"- Mnemo current-view tokens: {analysis['mnemo_current_view_tokens']}\n"
        f"- Mnemo / Markdown current-token ratio: {ratio_text}\n"
        f"- Markdown rejects stale writes: {analysis['markdown_stale_write_enforcement']}\n"
        f"- Mnemo rejects stale writes: {analysis['mnemo_stale_write_enforcement']}\n"
        "- Mnemo model tokens: 0 input, 0 output\n"
        "- model-generated task correctness: NOT_EVALUATED\n"
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def run_offline_evaluation(
    repository_root: Path, result_directory: Path, work_directory: Path
) -> dict[str, Any]:
    """Create one exclusive payload-free offline result directory."""

    root = repository_root.resolve()
    result = result_directory.resolve()
    work = work_directory.resolve()
    if result == work or result in work.parents or work in result.parents:
        raise ValueError("result and temporary work directories must be separate")
    result.mkdir(parents=True, exist_ok=False)
    rows = build_offline_rows(root, work)
    analysis = analyze_markdown_handoff_rows(rows)
    report = render_markdown_handoff_report(analysis)

    rows_text = "".join(
        f"{json.dumps(row, sort_keys=True, separators=(',', ':'))}\n" for row in rows
    )
    analysis_text = f"{json.dumps(analysis, sort_keys=True, indent=2)}\n"
    artifacts = {
        "rows.jsonl": rows_text,
        "analysis.json": analysis_text,
        "report.md": report,
    }
    for name, body in artifacts.items():
        _write_text(result / name, body)

    input_paths = (
        "docs/evaluations/markdown-handoff-proof-preregistration.md",
        "scripts/run_markdown_handoff_proof.py",
        "tests/fixtures/evals/markdown-handoff-proof-v1.json",
        "tests/fixtures/evals/viability-corpus-v1.json",
    )
    manifest: dict[str, Any] = {
        "schema_version": "mnemo-markdown-handoff-proof-manifest/1.0",
        "run_id": result.name,
        "verdict": analysis["verdict"],
        "row_count": len(rows),
        "conditions": ["NM", "DM", "MR"],
        "model_calls": 0,
        "mnemo_model_tokens": {"input": 0, "output": 0},
        "stored_payload_fields": [],
        "fields": [
            "fixture event keys",
            "normalized view hashes",
            "numeric grades",
            "token counts",
            "operation counts",
            "verdict",
        ],
        "artifact_sha256": {name: _sha256_file(result / name) for name in artifacts},
        "input_sha256": {name: _sha256_file(root / name) for name in input_paths},
    }
    _write_text(result / "manifest.json", f"{json.dumps(manifest, sort_keys=True, indent=2)}\n")
    return manifest


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the offline disciplined-Markdown versus Mnemo comparison."
    )
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    manifest = run_offline_evaluation(args.repository_root, args.result_dir, args.work_dir)
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
