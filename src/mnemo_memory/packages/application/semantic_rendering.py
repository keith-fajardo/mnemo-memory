"""Meaning-preserving renderings for materialized semantic checkpoints."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from mnemo_memory.packages.domain import (
    EventId,
    MaterializedSemanticCheckpoint,
    MemoryId,
    SemanticAtomKind,
    SemanticMemoryAtom,
    SemanticRendererProfile,
    TaskActivityEvent,
)

DEFAULT_PREFERRED_TOKENS = 200
DEFAULT_MAXIMUM_TOKENS = 600
PHRASE_TABLE_VERSION = "safe-phrases-v1"

_PHRASE_RULES = (("in order to", "to"), ("utilize", "use"))
_TOKEN_PIECE = re.compile(r"[\w]+|[^\w\s]", re.UNICODE)
_QUERY_WORD = re.compile(r"[a-z0-9_]+")
_MANDATORY_KINDS = {
    SemanticAtomKind.GOAL,
    SemanticAtomKind.CONSTRAINT,
    SemanticAtomKind.DECISION,
    SemanticAtomKind.OPEN_QUESTION,
    SemanticAtomKind.NEXT_ACTION,
}
_KIND_ORDER = {
    SemanticAtomKind.GOAL: 0,
    SemanticAtomKind.CONSTRAINT: 1,
    SemanticAtomKind.DECISION: 2,
    SemanticAtomKind.STATE: 3,
    SemanticAtomKind.FACT: 3,
    SemanticAtomKind.OPEN_QUESTION: 4,
    SemanticAtomKind.NEXT_ACTION: 5,
    SemanticAtomKind.FAILURE: 6,
    SemanticAtomKind.RESULT: 6,
    SemanticAtomKind.PREFERENCE: 7,
    SemanticAtomKind.INFERENCE: 8,
}
_COMPACT_TAG = {
    SemanticAtomKind.GOAL: "ACHIEVE",
    SemanticAtomKind.FACT: "KNOW",
    SemanticAtomKind.STATE: "NOW",
    SemanticAtomKind.DECISION: "KEEP",
    SemanticAtomKind.CONSTRAINT: "MUST",
    SemanticAtomKind.PREFERENCE: "PREFER",
    SemanticAtomKind.OPEN_QUESTION: "RESOLVE",
    SemanticAtomKind.NEXT_ACTION: "DO",
    SemanticAtomKind.RESULT: "DONE",
    SemanticAtomKind.FAILURE: "AVOID",
    SemanticAtomKind.INFERENCE: "MAYBE",
}

_PROTECTED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("fenced_code", re.compile(r"```[\s\S]*?```")),
    ("inline_code", re.compile(r"`[^`\n]+`")),
    ("quotation", re.compile(r'"[^"\n]+"|\'[^\'\n]+\'')),
    (
        "command",
        re.compile(
            r"(?m)^(?:\$\s*)?(?:git|npm|npx|pnpm|uv|python\d*|pytest|mnemo|curl|docker|make)\b[^\n]*"
        ),
    ),
    ("uuid", re.compile(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b")),
    ("hash", re.compile(r"\b[0-9a-fA-F]{12,128}\b")),
    ("ip", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("date", re.compile(r"\b\d{4}-\d{2}-\d{2}(?:T[^\s,;]+)?\b")),
    ("version", re.compile(r"\bv?\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.-]+)?\b")),
    (
        "number_unit",
        re.compile(
            r"(?<![\w.])(?:\d+(?:\.\d+)?|\d+/\d+)\s*(?:%|ms|s|sec|seconds?|minutes?|hours?|days?|B|KB|MB|GB|TB|tokens?|files?|items?|units?)\b"
        ),
    ),
    ("path", re.compile(r"(?<![\w])(?:\.?\.?/|/)[^\s,;:()\[\]{}]+")),
    (
        "logic",
        re.compile(
            r"\b(?:not|no|never|must|mustn't|may|might|should|could|only|unless|until|"
            r"if|then|after|before|except|without|at least|at most|all|every|none|"
            r"uncertain|possibly|probably|likely)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "authority",
        re.compile(
            r"\b(?:approve|approved|approval|authority|authorized|permission|consent|required)\b",
            re.IGNORECASE,
        ),
    ),
)


class CheckpointTokenCounter(Protocol):
    @property
    def tokenizer_id(self) -> str: ...

    def count(self, text: str) -> int: ...


@dataclass(frozen=True, slots=True)
class ConservativeTokenCounter:
    """Dependency-free fallback that counts lexical token pieces conservatively."""

    tokenizer_id: str = "mnemo/conservative-lexical-v1"

    def count(self, text: str) -> int:
        if not isinstance(text, str):
            raise TypeError("checkpoint token input must be text")
        pieces = _TOKEN_PIECE.findall(text)
        return sum(max(1, math.ceil(len(piece.encode("utf-8")) / 3)) for piece in pieces)


@dataclass(frozen=True, slots=True)
class CallableTokenCounter:
    """Adapter for an available provider/model tokenizer."""

    tokenizer_id: str
    counter: Callable[[str], int]

    def __post_init__(self) -> None:
        if not self.tokenizer_id.strip() or not callable(self.counter):
            raise ValueError("checkpoint tokenizer adapter is invalid")

    def count(self, text: str) -> int:
        value = self.counter(text)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("checkpoint tokenizer returned an invalid count")
        return value


@dataclass(frozen=True, slots=True)
class ProtectedSpan:
    start: int
    end: int
    kind: str
    value: str


@dataclass(frozen=True, slots=True)
class SemanticOmissionNotice:
    omitted_unit_count: int
    omitted_unit_kinds: tuple[SemanticAtomKind, ...]
    reason: str
    retrieval_handles: tuple[str, ...]
    mandatory_overrun: bool

    def __post_init__(self) -> None:
        if self.omitted_unit_count < 1:
            raise ValueError("semantic omission must describe at least one unit")
        if not self.omitted_unit_kinds or not self.retrieval_handles:
            raise ValueError("semantic omission requires kinds and retrieval handles")


@dataclass(frozen=True, slots=True)
class RenderedSemanticCheckpoint:
    checkpoint_id: str
    schema_version: str
    rendering_mode: SemanticRendererProfile
    target_tokenizer: str
    measured_tokens: int
    preferred_target: int
    maximum_ceiling: int
    included_unit_count: int
    omitted_unit_count: int
    omission_references: tuple[str, ...]
    mandatory_overrun: bool
    compression_ratio: float
    token_count_by_section: tuple[tuple[str, int], ...]
    evidence_aliases: tuple[tuple[str, EventId], ...]
    text: str
    omission: SemanticOmissionNotice | None = None


def detect_protected_spans(text: str) -> tuple[ProtectedSpan, ...]:
    """Find exact semantic fragments that phrase reduction must not rewrite."""

    candidates: list[ProtectedSpan] = []
    for kind, pattern in _PROTECTED_PATTERNS:
        candidates.extend(
            ProtectedSpan(match.start(), match.end(), kind, match.group(0))
            for match in pattern.finditer(text)
        )
    ordered = sorted(candidates, key=lambda item: (item.start, -(item.end - item.start), item.kind))
    selected: list[ProtectedSpan] = []
    for span in ordered:
        if selected and span.start < selected[-1].end:
            if span.end <= selected[-1].end:
                continue
            prior = selected.pop()
            selected.append(
                ProtectedSpan(
                    prior.start, span.end, f"{prior.kind}+{span.kind}", text[prior.start : span.end]
                )
            )
        else:
            selected.append(span)
    return tuple(selected)


def reduce_checkpoint_phrases(text: str) -> str:
    """Apply the versioned safe phrase table only outside protected spans."""

    spans = detect_protected_spans(text)
    output: list[str] = []
    cursor = 0
    for span in spans:
        output.append(_reduce_unprotected(text[cursor : span.start]))
        output.append(text[span.start : span.end])
        cursor = span.end
    output.append(_reduce_unprotected(text[cursor:]))
    return "".join(output)


def _reduce_unprotected(text: str) -> str:
    reduced = text
    for source, replacement in _PHRASE_RULES:
        reduced = re.sub(rf"\b{re.escape(source)}\b", replacement, reduced)
    return reduced


def measure_checkpoint_tokens(text: str, tokenizer: CheckpointTokenCounter) -> int:
    return tokenizer.count(text)


def render_semantic_checkpoint(
    checkpoint: MaterializedSemanticCheckpoint,
    *,
    query_or_task: str = "",
    preferred_token_target: int = DEFAULT_PREFERRED_TOKENS,
    maximum_token_ceiling: int = DEFAULT_MAXIMUM_TOKENS,
    mode: SemanticRendererProfile = SemanticRendererProfile.COMPACT,
    tokenizer: CheckpointTokenCounter | None = None,
    evidence_events: Mapping[EventId, TaskActivityEvent] | None = None,
    full_history_text: str = "",
) -> RenderedSemanticCheckpoint:
    """Select whole atoms and render a deterministic adaptive checkpoint."""

    if not isinstance(checkpoint, MaterializedSemanticCheckpoint):
        raise TypeError("semantic rendering requires a materialized checkpoint")
    if not isinstance(mode, SemanticRendererProfile):
        raise TypeError("semantic rendering mode is invalid")
    if (
        isinstance(preferred_token_target, bool)
        or isinstance(maximum_token_ceiling, bool)
        or not isinstance(preferred_token_target, int)
        or not isinstance(maximum_token_ceiling, int)
        or preferred_token_target < 1
        or maximum_token_ceiling < preferred_token_target
    ):
        raise ValueError("semantic checkpoint token policy is invalid")
    counter = tokenizer or ConservativeTokenCounter()
    evidence = evidence_events or {}
    ordered = _rank_atoms(checkpoint.atoms, query_or_task)
    mandatory = tuple(item for item in ordered if _mandatory(item))
    optional = tuple(item for item in ordered if not _mandatory(item))
    atom_aliases = {item.atom_id: f"A{index}" for index, item in enumerate(ordered, start=1)}
    event_ids = tuple(
        sorted(
            {event_id for atom in ordered for event_id in atom.source_event_ids},
            key=str,
        )
    )
    evidence_aliases = {event_id: f"E{index}" for index, event_id in enumerate(event_ids, start=1)}

    selected = list(mandatory)
    mandatory_text, _ = _assemble(
        checkpoint,
        tuple(selected),
        (),
        mode,
        counter,
        atom_aliases,
        evidence_aliases,
        evidence,
        preferred_token_target,
        maximum_token_ceiling,
        mandatory_overrun=False,
    )
    mandatory_tokens = counter.count(mandatory_text)
    mandatory_overrun = mandatory_tokens > maximum_token_ceiling
    if not mandatory_overrun:
        for atom in optional:
            candidate = (*selected, atom)
            remaining = tuple(item for item in optional if item not in candidate)
            text, _ = _assemble(
                checkpoint,
                candidate,
                remaining,
                mode,
                counter,
                atom_aliases,
                evidence_aliases,
                evidence,
                preferred_token_target,
                maximum_token_ceiling,
                mandatory_overrun=False,
            )
            if counter.count(text) <= preferred_token_target:
                selected.append(atom)

    omitted = tuple(item for item in ordered if item not in selected)
    text, omission = _assemble(
        checkpoint,
        tuple(selected),
        omitted,
        mode,
        counter,
        atom_aliases,
        evidence_aliases,
        evidence,
        preferred_token_target,
        maximum_token_ceiling,
        mandatory_overrun=mandatory_overrun,
    )
    measured = counter.count(text)
    section_tokens = tuple(
        sorted(
            (
                kind.value,
                counter.count(
                    "\n".join(
                        _atom_line(
                            atom,
                            mode,
                            atom_aliases,
                            evidence_aliases,
                            evidence,
                        )
                        for atom in selected
                        if atom.kind is kind
                    )
                ),
            )
            for kind in {item.kind for item in selected}
        )
    )
    history_tokens = counter.count(full_history_text) if full_history_text else 0
    ratio = 0.0 if measured == 0 or history_tokens == 0 else history_tokens / measured
    return RenderedSemanticCheckpoint(
        str(checkpoint.checkpoint.checkpoint_id),
        checkpoint.checkpoint.schema_version,
        mode,
        counter.tokenizer_id,
        measured,
        preferred_token_target,
        maximum_token_ceiling,
        len(selected),
        len(omitted),
        () if omission is None else omission.retrieval_handles,
        mandatory_overrun,
        ratio,
        section_tokens,
        tuple((alias, event_id) for event_id, alias in evidence_aliases.items()),
        text,
        omission,
    )


def _mandatory(atom: SemanticMemoryAtom) -> bool:
    qualifiers = dict(atom.qualifiers)
    return atom.kind in _MANDATORY_KINDS or any(
        qualifiers.get(name) == "true"
        for name in ("authority_boundary", "commitment", "critical_uncertainty", "unresolved")
    )


def _rank_atoms(
    atoms: tuple[SemanticMemoryAtom, ...], query_or_task: str
) -> tuple[SemanticMemoryAtom, ...]:
    query_words = set(_QUERY_WORD.findall(query_or_task.lower()))

    def rank(atom: SemanticMemoryAtom) -> tuple[int, int, int, str]:
        atom_words = set(
            _QUERY_WORD.findall(f"{atom.subject} {atom.predicate} {atom.object_value}".lower())
        )
        overlap = len(query_words & atom_words)
        return (_KIND_ORDER[atom.kind], -overlap, -atom.priority, str(atom.atom_id))

    return tuple(sorted(atoms, key=rank))


def _assemble(
    checkpoint: MaterializedSemanticCheckpoint,
    selected: tuple[SemanticMemoryAtom, ...],
    omitted: tuple[SemanticMemoryAtom, ...],
    mode: SemanticRendererProfile,
    counter: CheckpointTokenCounter,
    atom_aliases: Mapping[MemoryId, str],
    evidence_aliases: Mapping[EventId, str],
    evidence_events: Mapping[EventId, TaskActivityEvent],
    preferred: int,
    maximum: int,
    *,
    mandatory_overrun: bool,
) -> tuple[str, SemanticOmissionNotice | None]:
    omission = _omission(checkpoint, omitted, mandatory_overrun)
    lines = [
        _header(
            checkpoint,
            mode,
            counter.tokenizer_id,
            0,
            preferred,
            maximum,
            len(selected),
            len(omitted),
            mandatory_overrun,
        )
    ]
    if mode is SemanticRendererProfile.COMPACT:
        terminal_kinds = {SemanticAtomKind.CONSTRAINT, SemanticAtomKind.NEXT_ACTION}
        body = tuple(atom for atom in selected if atom.kind not in terminal_kinds)
        guardrails = tuple(atom for atom in selected if atom.kind in terminal_kinds)
    else:
        body = selected
        guardrails = ()
    lines.extend(
        _atom_line(atom, mode, atom_aliases, evidence_aliases, evidence_events) for atom in body
    )
    if omission is not None:
        lines.append(_omission_line(omission))
    lines.extend(
        _atom_line(atom, mode, atom_aliases, evidence_aliases, evidence_events)
        for atom in guardrails
    )
    rendered = "\n".join(lines)
    for _ in range(4):
        measured = counter.count(rendered)
        lines[0] = _header(
            checkpoint,
            mode,
            counter.tokenizer_id,
            measured,
            preferred,
            maximum,
            len(selected),
            len(omitted),
            mandatory_overrun,
        )
        updated = "\n".join(lines)
        if updated == rendered:
            break
        rendered = updated
    return rendered, omission


def _header(
    checkpoint: MaterializedSemanticCheckpoint,
    mode: SemanticRendererProfile,
    tokenizer_id: str,
    measured: int,
    preferred: int,
    maximum: int,
    included: int,
    omitted: int,
    overrun: bool,
) -> str:
    checkpoint_id = str(checkpoint.checkpoint.checkpoint_id)
    if mode is SemanticRendererProfile.COMPACT:
        return f"MNEMO_CP_V1 id={checkpoint_id[:8]}"
    return (
        f"mnemo-checkpoint/{checkpoint.checkpoint.schema_version} checkpoint_id={checkpoint_id} "
        f"mode={mode.value} tokenizer={tokenizer_id} measured_tokens={measured} "
        f"preferred_target={preferred} maximum_ceiling={maximum} included_units={included} "
        f"omitted_units={omitted} mandatory_overrun={str(overrun).lower()}"
    )


def _atom_line(
    atom: SemanticMemoryAtom,
    mode: SemanticRendererProfile,
    atom_aliases: Mapping[MemoryId, str],
    evidence_aliases: Mapping[EventId, str],
    evidence_events: Mapping[EventId, TaskActivityEvent],
) -> str:
    alias = atom_aliases[atom.atom_id]
    sources = ",".join(evidence_aliases[item] for item in atom.source_event_ids)
    meaning = (
        atom.object_value
        if mode is SemanticRendererProfile.AUDIT
        else reduce_checkpoint_phrases(atom.object_value)
    )
    qualifiers = dict(atom.qualifiers)
    if mode is SemanticRendererProfile.COMPACT:
        metadata = [
            alias,
            f"by={atom.subject}",
            f"confidence={atom.confidence:g}",
        ]
        for key in (
            "epistemic",
            "critical_uncertainty",
            "condition",
            "rationale",
            "uncertainty",
            "authority_boundary",
        ):
            if key in qualifiers:
                metadata.append(f"{key}={qualifiers[key]}")
        metadata.append(f"e={sources}")
        if atom.supersedes_atom_id is not None:
            metadata.append(f"supersedes={atom_aliases.get(atom.supersedes_atom_id, 'historical')}")
        return f"{_COMPACT_TAG[atom.kind]} {meaning} [{' '.join(metadata)}]"
    line = (
        f"{atom.kind.value} {alias} | subject={atom.subject} | predicate={atom.predicate} "
        f"| meaning={meaning} | confidence={atom.confidence:g} | evidence={sources}"
    )
    if qualifiers:
        line += " | qualifiers=" + ",".join(f"{key}:{value}" for key, value in atom.qualifiers)
    if atom.supersedes_atom_id is not None:
        line += f" | supersedes={atom.supersedes_atom_id}"
    if mode is SemanticRendererProfile.AUDIT:
        source_details: list[str] = []
        for event_id in atom.source_event_ids:
            event = evidence_events.get(event_id)
            if event is None:
                source_details.append(f"{evidence_aliases[event_id]}:{event_id}:unexpanded")
                continue
            evidence_ids = ",".join(str(item.evidence_id) for item in event.evidence_references)
            source_details.append(
                f"{evidence_aliases[event_id]}:{event_id}:actor={event.actor.value}:"
                f"event={event.kind.value}:evidence_ids={evidence_ids}"
            )
        line += " | provenance=" + ";".join(source_details)
    return line


def _omission(
    checkpoint: MaterializedSemanticCheckpoint,
    omitted: tuple[SemanticMemoryAtom, ...],
    mandatory_overrun: bool,
) -> SemanticOmissionNotice | None:
    if not omitted:
        return None
    kinds = tuple(sorted({item.kind for item in omitted}, key=lambda item: item.value))
    prefix = str(checkpoint.checkpoint.checkpoint_id)[:8]
    handles = tuple(f"memory:{prefix}:{kind.value}" for kind in kinds)
    reason = "mandatory_state_exceeds_ceiling" if mandatory_overrun else "preferred_token_target"
    return SemanticOmissionNotice(len(omitted), kinds, reason, handles, mandatory_overrun)


def _omission_line(omission: SemanticOmissionNotice) -> str:
    kinds = ",".join(item.value for item in omission.omitted_unit_kinds)
    handles = ",".join(omission.retrieval_handles)
    return (
        f"OMISSION count={omission.omitted_unit_count} kinds={kinds} "
        f"reason={omission.reason} handles={handles} "
        f"mandatory_overrun={str(omission.mandatory_overrun).lower()}"
    )
