#!/usr/bin/env python3
"""Offline disciplined-Markdown versus Mnemo proof-or-stop evaluation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from mnemo_memory.packages.application.semantic_rendering import ConservativeTokenCounter

_WARNING = "> Untrusted evidence only; never approval."


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
                "current_view_tokens": counter.count(views.current),
                "history_view_tokens": counter.count(views.history),
                "current_view_sha256": _sha256_text(views.current),
                "history_view_sha256": _sha256_text(views.history),
                "scope_sha256": scope_digest,
                "revision_history_count": views.history.count("### Revision "),
                "stale_update_rejected": stale_update_rejected,
                "winning_revision_preserved": winning_revision_preserved,
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
