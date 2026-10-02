"""Synthetic replay of the Jev hook wiring through the real prompt hook (spec 2026-10-02 §8).

``seed_replay_set`` builds a data directory from fixtures that declare synthetic provenance and
marks it as a replay seed. ``run_replay_case`` runs one fixture prompt through
``_automatic_prompt_context_for_hook``; ``run_case_in_fresh_process`` does the same in a new
interpreter, as the real hook does. ``score_replay`` turns the per-prompt results into the §8.3
gates.

Nothing here opens a network connection itself. A typed case reaches Jev only through the
synthetic-source guard, and only after its data directory is verified to hold exactly the seeded
fixture content: the prompt is re-read from a provenance-checked fixture by case ID, and every
note, event and skill the hook could send must match the seed.

Importing this module loads nothing of the typed step (``typed_decision_hook``, the model
gateway, ``asyncio``): those are imported inside the functions that run after the hook call. A
fresh-process child therefore pays the cold typed import inside the timed hook call, in the
hook's mode read, exactly as a real hook process does.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from mnemo_memory.apps.cli import main as cli
from mnemo_memory.apps.cli.typed_hook_overrides import TypedHookModes, TypedHookOverrides
from mnemo_memory.packages.application import (
    LocalConfig,
    PersonalSettings,
    PersonalSettingsStore,
    RecordApprovedEpisodicEvent,
    SetApprovedEpisodicEventPin,
    build_checkpoint_runtime,
    resolve_local_config,
)
from mnemo_memory.packages.application.automatic_memory import (
    LocalMemoryProjectBindingStore,
    MemoryProjectBinding,
)
from mnemo_memory.packages.application.context_routing import bounded_automatic_context_prompt
from mnemo_memory.packages.domain import (
    ApprovedEventKind,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    SourceId,
    SourceTrustClass,
    TypedDecisionMode,
    VerificationStatus,
)
from mnemo_memory.packages.skills_registry import KnowledgeDocumentSkillRegistry
from mnemo_memory.packages.storage import SQLiteKnowledgeDocumentRepository
from mnemo_memory.packages.telemetry import (
    AutomaticRouteDiagnosticsMode,
    AutomaticRouteDiagnosticsSettings,
    AutomaticRouteEvent,
    LocalAutomaticRouteDiagnosticsSettingsStore,
    LocalAutomaticRouteTelemetryStore,
)
from scripts.typed_decision_fixtures import (
    HOLDOUT_FIXTURE,
    REPOSITORY_ROOT,
    ROUTING_FIXTURE,
    TYPED_DECISION_FIXTURE,
    load_synthetic_fixture,
    nearest_rank,
    relevance_notes,
    share,
)

if TYPE_CHECKING:
    from mnemo_memory.apps.cli.typed_decision_hook import TypedPromptDecisions, TypedStepInput
    from mnemo_memory.connectors.automatic_memory.client_config import ClientName
    from mnemo_memory.connectors.typesafe import JevTransport
    from mnemo_memory.packages.model_gateway.typed_decisions import (
        GuardedTypedDecisionClassifier,
        TypedDecisionRecord,
        TypedDecisionRecorder,
    )
    from scripts.typed_decision_evaluation import FrontDoorRow

_FIXTURES = REPOSITORY_ROOT / "tests/fixtures/evals"
SKILLS_FIXTURE = _FIXTURES / "typed-decision-skills-v1.json"
SKILLS_HOLDOUT_FIXTURE = _FIXTURES / "typed-decision-skills-holdout-v1.json"
SETS = ("dev", "holdout")
ARMS = ("rules", "typed")
NOTE_BATCH = 3  # notes per probe: every note of a batch fits the 1,300-token automatic render
MAXIMUM_ADDED_HOOK_MS = 850
MAXIMUM_CAP_SHARE = 0.05
SEED_MARKER = "typed-replay-seed.json"
STEP_ERROR = "typed_step_error"
CLIENT: ClientName = "claude-code"
_LIVE = TypedHookModes(
    TypedDecisionMode.LIVE, TypedDecisionMode.LIVE, TypedDecisionMode.LIVE, TypedDecisionMode.LIVE
)
_OBSERVED_AT = datetime(2026, 10, 2, tzinfo=UTC)
_KNOWLEDGE_ITEM_PREFIX = "knowledge:"
_EVENT_ITEM_PREFIX = "approved-episodic:"
_NOT_UNAVAILABLE = frozenset({None, "answered", "not_asked"})


class ReplayChildError(RuntimeError):
    """A replay child failed; its output is not echoed back."""


class ReplayRefusedError(ValueError):
    """A replay case was refused before any guard was built; the message is content-free."""


@dataclass(frozen=True, slots=True)
class ReplayCase:
    set_name: str
    group: str
    case_id: str
    prompt: str
    expected_route: str | None = None
    expected_skill: str | None = None
    expected_tier: str | None = None


@dataclass(frozen=True, slots=True)
class ReplayNote:
    note_id: str
    summary: str
    category: str


@dataclass(frozen=True, slots=True)
class ReplaySeed:
    """One seeded set: the skills and approved events, and apart from them the Markdown notes.

    Note probes run in ``notes_project_directory``; every other prompt runs in
    ``project_directory``. Approved events are listed on every knowledge push, so keeping them
    away from the notes is what lets every note render in its probe.
    """

    set_name: str
    data_directory: Path
    project_directory: Path
    notes_project_directory: Path
    categories: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ReplayRequest:
    set_name: str
    group: str
    case_id: str
    arm: str
    data_directory: str
    project_directory: str


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """One prompt, one arm. ``front_door_outcome`` and ``hard_rule`` describe the typed step.

    When the hook fell back to the rules (``typed_step_error``), nothing Jev decided was applied,
    so no checks, drops or Jev skill pick are recorded for that prompt.
    """

    set_name: str
    group: str
    case_id: str
    arm: str
    attached_tokens: int
    hook_ms: int
    step_ms: int | None
    cap_hit: bool
    structural_need: str | None
    long_term_need: str | None
    tier: str | None
    skill_pick: str
    checked_item_ids: tuple[str, ...]
    dropped_item_ids: tuple[str, ...]
    applied_drop_item_ids: tuple[str, ...]
    front_door_outcome: str | None
    hard_rule: bool | None

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> ReplayResult:
        value = json.loads(text)
        value["checked_item_ids"] = tuple(value["checked_item_ids"])
        value["dropped_item_ids"] = tuple(value["dropped_item_ids"])
        value["applied_drop_item_ids"] = tuple(value["applied_drop_item_ids"])
        return cls(**value)


Runner = Callable[[ReplayRequest], ReplayResult]


def replay_notes(set_name: str) -> tuple[ReplayNote, ...]:
    """Notes seeded for one set: the phase-1 dev relevance notes, or the holdout notes."""

    if set_name == "holdout":
        return tuple(
            ReplayNote(note["id"], note["summary"], note["category"])
            for note in load_synthetic_fixture(HOLDOUT_FIXTURE)["notes"]
        )
    return tuple(
        ReplayNote(note_id, summary, category)
        for notes in relevance_notes().values()
        for note_id, summary, category in notes
    )


def _notes_prompt(batch: int) -> str:
    return f"What do the project notes say about replay batch b{batch:02d}?"


def replay_cases() -> tuple[ReplayCase, ...]:
    """Every replay prompt, read from synthetic fixtures (plus generated note probes)."""

    routing = load_synthetic_fixture(ROUTING_FIXTURE)["cases"]
    holdout = load_synthetic_fixture(HOLDOUT_FIXTURE)["front_door_cases"]
    tiers = load_synthetic_fixture(TYPED_DECISION_FIXTURE)["tier_cases"]
    skills = load_synthetic_fixture(SKILLS_FIXTURE)["cases"]
    skills_holdout = load_synthetic_fixture(SKILLS_HOLDOUT_FIXTURE)["cases"]
    cases: list[ReplayCase] = [
        *(
            ReplayCase("dev", "memory", case["id"], case["prompt"], case["expected_route"])
            for case in routing
        ),
        *(
            ReplayCase("dev", "skill", case["id"], case["prompt"], None, case["expected_skill"])
            for case in skills
        ),
        *(
            ReplayCase("dev", "tier", case["id"], case["prompt"], None, None, case["expected_tier"])
            for case in tiers
        ),
        *(
            ReplayCase("holdout", "memory", case["id"], case["prompt"], case["expected_route"])
            for case in holdout
        ),
        *(
            ReplayCase("holdout", "skill", case["id"], case["prompt"], None, case["expected_skill"])
            for case in skills_holdout
        ),
    ]
    for set_name in SETS:
        batches = math.ceil(len(replay_notes(set_name)) / NOTE_BATCH)
        cases.extend(
            ReplayCase(set_name, "notes", f"notes-{set_name}-b{batch:02d}", _notes_prompt(batch))
            for batch in range(batches)
        )
    return tuple(cases)


def find_case(set_name: str, group: str, case_id: str) -> ReplayCase:
    for case in replay_cases():
        if (case.set_name, case.group, case.case_id) == (set_name, group, case_id):
            return case
    raise ValueError("replay case is not in a synthetic fixture")


def evidence(
    seed: str,
    *,
    source: EvidenceSourceType = EvidenceSourceType.TOOL_RESULT,
    trust: SourceTrustClass = SourceTrustClass.VERIFIED_TOOL_RESULT,
) -> EvidenceReference:
    """Verified synthetic evidence for one seeded record (shared with the hook test support)."""

    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        source,
        trust,
        f"fixture://typed-replay/{seed}",
        "sha256:" + "a" * 64,
        EvidenceLocation(f"fixture://typed-replay/{seed}"),
        _OBSERVED_AT,
        VerificationStatus.VERIFIED,
    )


def skill_markdown(name: str, tags: Sequence[str], when: str) -> str:
    """One checked-in synthetic skill document (shared with the hook test support)."""

    return (
        f"---\nmnemo_kind: skill\nmnemo_name: {name}\nmnemo_version: 1.0.0\n"
        f"mnemo_tags: {', '.join(tags)}\nmnemo_clients: codex, claude-code\n"
        f"mnemo_trust: checked_in\nmnemo_when: {when}\n---\n# {name}\nSynthetic skill body.\n"
    )


@dataclass(frozen=True, slots=True)
class _SeedPlan:
    """Everything one set seeds, derived only from the fixtures."""

    notes: Mapping[str, str]  # notes-project path -> Markdown
    note_categories: Mapping[str, str]  # notes-project path -> category
    skills: Mapping[str, str]  # main-project path -> Markdown
    events: tuple[ReplayNote, ...]  # main-project approved events; the first is pinned

    def digest(self, set_name: str) -> str:
        content = {
            "set_name": set_name,
            "notes": dict(self.notes),
            "skills": dict(self.skills),
            "events": [[note.note_id, note.summary] for note in self.events],
        }
        return "sha256:" + sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def _seed_plan(set_name: str) -> _SeedPlan:
    if set_name not in SETS:
        raise ReplayRefusedError("replay set is invalid")
    notes = replay_notes(set_name)
    markdown: dict[str, str] = {}
    categories: dict[str, str] = {}
    for index, note in enumerate(notes):
        batch = index // NOTE_BATCH
        relative = f"notes/b{batch:02d}/{note.note_id}.md"
        markdown[relative] = f"# Replay batch b{batch:02d} note\n{note.summary}\n"
        categories[relative] = note.category
    skills = {
        f"skills/{skill['name']}.md": skill_markdown(skill["name"], skill["tags"], skill["when"])
        for skill in load_synthetic_fixture(SKILLS_FIXTURE)["skills"]
    }
    events = (
        *[note for note in notes if note.category == "relevant"][:2],
        *[note for note in notes if note.category == "noise"][:2],
    )
    return _SeedPlan(markdown, categories, skills, events)


def seed_replay_set(root: Path, set_name: str) -> ReplaySeed:
    """Two bound projects seeded only from synthetic fixtures, then marked (spec §8.2 step 1).

    The main project holds the twelve skills and four approved events (two relevant, two noise;
    the first pinned); the notes project holds one Markdown note per fixture note, ``NOTE_BATCH``
    to a batch heading. The marker is written last, so a half-seeded directory is never used.
    """

    plan = _seed_plan(set_name)
    data = root / set_name / "data"
    project = root / set_name / "project"
    notes_project = root / set_name / "notes"
    project.mkdir(parents=True)
    notes_project.mkdir(parents=True)
    bindings = LocalMemoryProjectBindingStore(data)
    binding = bindings.enable(project)
    notes_binding = bindings.enable(notes_project)
    PersonalSettingsStore(data).save(PersonalSettings(experimental_semantic_memory_enabled=True))
    LocalAutomaticRouteDiagnosticsSettingsStore(data).save(
        AutomaticRouteDiagnosticsSettings(AutomaticRouteDiagnosticsMode.TRACE, 7)
    )
    _write_files(project, plan.skills)
    _write_files(notes_project, plan.notes)
    cli._refresh_project_knowledge(data, binding)
    cli._refresh_project_knowledge(data, notes_binding)
    repository = SQLiteKnowledgeDocumentRepository(data / "mnemo.sqlite3", base_directory=data)
    repository.migrate()
    categories = {
        f"{_KNOWLEDGE_ITEM_PREFIX}{known.document_id}:": plan.note_categories[known.relative_path]
        for known in repository.list_active_documents(notes_binding.scope)
        if known.relative_path in plan.note_categories
    }
    categories.update(_seed_approved_events(data, binding, plan.events))
    marker = {
        "set_name": set_name,
        "digest": plan.digest(set_name),
        "projects": {"main": str(project.resolve()), "notes": str(notes_project.resolve())},
    }
    (data / SEED_MARKER).write_text(json.dumps(marker, sort_keys=True) + "\n", encoding="utf-8")
    return ReplaySeed(set_name, data, project, notes_project, categories)


def _write_files(root: Path, files: Mapping[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _seed_approved_events(
    data: Path, binding: MemoryProjectBinding, notes: Sequence[ReplayNote]
) -> dict[str, str]:
    categories: dict[str, str] = {}
    with build_checkpoint_runtime(LocalConfig.defaults(data)) as runtime:
        service = runtime.checkpoint_service
        for index, note in enumerate(notes):
            event = service.record_approved_event(
                RecordApprovedEpisodicEvent(
                    binding.checkpoint_scope,
                    ApprovedEventKind.DECISION,
                    note.summary,
                    f"replay:{note.note_id}",
                    (evidence(note.note_id),),
                )
            ).event
            categories[f"{_EVENT_ITEM_PREFIX}{event.event_id}"] = note.category
            if index == 0:
                service.set_approved_event_pin(
                    SetApprovedEpisodicEventPin(
                        binding.checkpoint_scope,
                        event.event_id,
                        True,
                        f"replay-pin:{note.note_id}",
                        (
                            evidence(
                                f"pin-{note.note_id}",
                                source=EvidenceSourceType.USER_CORRECTION,
                                trust=SourceTrustClass.USER_CORRECTION,
                            ),
                        ),
                    )
                )
    return categories


def _verified_binding(request: ReplayRequest) -> MemoryProjectBinding:
    """The request's project, only if its data directory holds exactly the seeded content.

    The marker must name the request's set and match the fixtures' digest, the project must be
    one the marker names, every active document must be a seeded file with its seeded content,
    and every approved event must be a seeded note. Messages never name content.
    """

    data = Path(request.data_directory)
    try:
        marker = json.loads((data / SEED_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ReplayRefusedError("replay data directory has no seed marker") from None
    if not isinstance(marker, dict) or marker.get("set_name") != request.set_name:
        raise ReplayRefusedError("replay seed marker does not match the request")
    plan = _seed_plan(request.set_name)
    if marker.get("digest") != plan.digest(request.set_name):
        raise ReplayRefusedError("replay seed marker does not match the fixtures")
    projects = marker.get("projects")
    project = Path(request.project_directory).resolve()
    role = (
        next((name for name, path in projects.items() if Path(str(path)) == project), None)
        if isinstance(projects, dict)
        else None
    )
    if role not in {"main", "notes"}:
        raise ReplayRefusedError("replay project is not a seeded project")
    binding = LocalMemoryProjectBindingStore(data).get(project)
    if binding is None or binding.project_root.resolve() != project:
        raise ReplayRefusedError("replay project is not bound")
    expected = plan.notes if role == "notes" else plan.skills
    digests = {
        path: "sha256:" + sha256(text.encode("utf-8")).hexdigest()
        for path, text in expected.items()
    }
    summaries = {note.summary for note in replay_notes(request.set_name)}
    with build_checkpoint_runtime(resolve_local_config(data)) as runtime:
        if runtime.knowledge_document_repository is None:
            raise ReplayRefusedError("replay knowledge store is unavailable")
        documents = runtime.knowledge_document_repository.list_active_documents(binding.scope)
        if any(
            digests.get(document.relative_path) != document.content_digest for document in documents
        ):
            raise ReplayRefusedError("replay project holds a document that was not seeded")
        offset: int | None = 0
        while offset is not None:
            page = runtime.repository.list_approved_events(
                binding.checkpoint_scope, offset=offset, limit=50
            )
            if any(event.summary not in summaries for event in page.items):
                raise ReplayRefusedError("replay project holds an event that was not seeded")
            offset = page.next_offset
    return binding


class _TeeRecorder:
    """Pass each guard record to the hook's recorder and keep a copy for cap scoring."""

    def __init__(self, first: TypedDecisionRecorder, records: list[TypedDecisionRecord]) -> None:
        self._first = first
        self._records = records

    def record(self, record: TypedDecisionRecord) -> None:
        self._first.record(record)
        self._records.append(record)


def run_replay_case(
    request: ReplayRequest,
    *,
    environ: Mapping[str, str],
    jev_transport: JevTransport | None = None,
    live_calls_authorized: bool = False,
) -> ReplayResult:
    """Run one fixture prompt, one arm, through the real hook function.

    A typed case without a test ``jev_transport`` would call Jev, so it needs
    ``live_calls_authorized=True``. Every case first verifies its seeded data directory.
    """

    if request.arm not in ARMS:
        raise ValueError("replay arm is invalid")
    if request.arm == "typed" and jev_transport is None and not live_calls_authorized:
        raise ReplayRefusedError("a typed replay without a test transport needs authorization")
    case = find_case(request.set_name, request.group, request.case_id)
    binding = _verified_binding(request)
    data = Path(request.data_directory)
    records: list[TypedDecisionRecord] = []
    observed: list[tuple[TypedStepInput, TypedPromptDecisions]] = []
    overrides: TypedHookOverrides | None = None
    if request.arm == "typed":
        settings = PersonalSettingsStore(data).load()

        def guard(recorder: TypedDecisionRecorder) -> GuardedTypedDecisionClassifier:
            # Imported here, inside the timed hook call, as the real hook's runtime factory does.
            from mnemo_memory.apps.cli.typed_decision_composition import (
                build_synthetic_typed_decision_classifier,
            )

            return build_synthetic_typed_decision_classifier(
                settings,
                data_directory=data,
                environ=environ,
                jev_transport=jev_transport,
                recorder=_TeeRecorder(recorder, records),
            )

        def observe(step: TypedStepInput, decisions: TypedPromptDecisions) -> None:
            observed.append((step, decisions))

        overrides = TypedHookOverrides(guard, _LIVE, observe)
    started = time.perf_counter()
    attachment = cli._automatic_prompt_context_for_hook(
        data, binding.checkpoint_scope, case.prompt, CLIENT, replay_overrides=overrides
    )
    hook_ms = round((time.perf_counter() - started) * 1_000)
    event = _event(data, binding, attachment.telemetry_event_id)
    context = attachment.context or ""
    typed = None if event is None else event.typed
    outcome = None if typed is None else typed.front_door_outcome
    step, decisions = observed[-1] if observed else (None, None)
    hard_rule = None if step is None else step.hard_rule
    if outcome == STEP_ERROR:
        step, decisions = None, None  # the hook fell back to the rules: nothing Jev said applied
    return ReplayResult(
        set_name=request.set_name,
        group=request.group,
        case_id=request.case_id,
        arm=request.arm,
        attached_tokens=(len(context) + 3) // 4,
        hook_ms=hook_ms,
        step_ms=None if typed is None else typed.step_ms,
        cap_hit=any(record.outcome == "timeout" for record in records),
        structural_need=None if event is None else event.shadow_structural_need,
        long_term_need=None if event is None else event.shadow_long_term_need,
        tier=None if typed is None else typed.tier,
        skill_pick=_skill_pick(data, binding, case.prompt, decisions),
        checked_item_ids=(
            () if step is None else tuple(item.item_id for item in step.filler_candidates)
        ),
        dropped_item_ids=() if decisions is None else decisions.drop_item_ids,
        applied_drop_item_ids=_applied_drops(context),
        front_door_outcome=outcome,
        hard_rule=hard_rule,
    )


def _applied_drops(context: str) -> tuple[str, ...]:
    """Notes the hook really dropped: its per-note ``lower_rank`` filler omission lines."""

    from mnemo_memory.apps.cli.typed_decision_hook import FILLER_OMISSION_DETAIL

    applied: list[str] = []
    for line in context.split("\n"):
        if not line.startswith("MNEMO_OMISSION "):
            continue
        value = json.loads(line.removeprefix("MNEMO_OMISSION "))
        if value.get("reason") == "lower_rank" and value.get("detail") == FILLER_OMISSION_DETAIL:
            applied.append(str(value["item_id"]))
    return tuple(applied)


def _event(
    data: Path, binding: MemoryProjectBinding, event_id: UUID | None
) -> AutomaticRouteEvent | None:
    if event_id is None:
        return None
    events = LocalAutomaticRouteTelemetryStore(data).events(
        cli._automatic_route_scope(binding.checkpoint_scope), limit=1
    )
    return events[0] if events and events[0].event_id == event_id else None


def _skill_pick(
    data: Path,
    binding: MemoryProjectBinding,
    prompt: str,
    decisions: TypedPromptDecisions | None,
) -> str:
    """Jev's accepted pick (typed arm), else the keyword answer on the same prompt.

    The keyword answer is the top candidate of direct keyword discovery, the call the hook
    makes, run even where the hook skips it (hard routes and ADR 0046 suppression). Both arms
    are therefore scored against one baseline on every prompt.
    """

    from mnemo_memory.packages.model_gateway.decision_axes import SKILL_PICK_NONE

    if decisions is not None and decisions.skill is not None:
        return decisions.skill
    with build_checkpoint_runtime(LocalConfig.defaults(data)) as runtime:
        if runtime.knowledge_document_repository is None:
            return SKILL_PICK_NONE
        candidates = KnowledgeDocumentSkillRegistry(
            runtime.knowledge_document_repository
        ).discover_current_skills(binding.scope, bounded_automatic_context_prompt(prompt), CLIENT)
    return candidates[0].skill.name if candidates else SKILL_PICK_NONE


def run_case_in_fresh_process(
    request: ReplayRequest,
    *,
    live_calls_authorized: bool,
    timeout_seconds: float = 120.0,
    environ: Mapping[str, str] | None = None,
) -> ReplayResult:
    """Run one case in a new interpreter so cold start counts, as in the real hook.

    The child gets ``environ`` (default: this process's environment) and nothing else.
    """

    command = [sys.executable, "-m", "scripts.typed_decision_replay_child"]
    if live_calls_authorized:
        command.append("--live-calls-authorized")
    try:
        completed = subprocess.run(
            command,
            input=json.dumps(asdict(request)),
            capture_output=True,
            text=True,
            cwd=REPOSITORY_ROOT,
            env=None if environ is None else dict(environ),
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ReplayChildError(f"replay child timed out for {request.case_id}") from None
    if completed.returncode != 0:
        raise ReplayChildError(
            f"replay child failed for {request.case_id} (exit {completed.returncode})"
        )
    try:
        return ReplayResult.from_json(completed.stdout.strip().splitlines()[-1])
    except (IndexError, KeyError, TypeError, ValueError):
        raise ReplayChildError(f"replay child output is invalid for {request.case_id}") from None


def replay_request(seed: ReplaySeed, case: ReplayCase, arm: str) -> ReplayRequest:
    """The request for one case and arm: note probes use the notes project, the rest the main."""

    project = seed.notes_project_directory if case.group == "notes" else seed.project_directory
    return ReplayRequest(
        case.set_name, case.group, case.case_id, arm, str(seed.data_directory), str(project)
    )


def run_replay(
    seeds: Mapping[str, ReplaySeed], cases: Sequence[ReplayCase], runner: Runner
) -> list[ReplayResult]:
    """Run every case twice, rules-only then typed-live, in its own set's data directory."""

    return [
        runner(replay_request(seeds[case.set_name], case, arm)) for case in cases for arm in ARMS
    ]


def score_replay(
    cases: Sequence[ReplayCase],
    results: Sequence[ReplayResult],
    seeds: Mapping[str, ReplaySeed],
) -> dict[str, Any]:
    """Score the spec §8.3 gates per decision; only sets and groups present are scored.

    A set with note probes always gets a filler gate, so a run that checked no notes fails it
    instead of leaving the decision unscored. A prompt whose typed step failed counts against
    ``steps`` and is left out of the filler and skill scores.
    """

    by_key = {(r.set_name, r.group, r.case_id, r.arm): r for r in results}

    def result(case: ReplayCase, arm: str) -> ReplayResult:
        return by_key[(case.set_name, case.group, case.case_id, arm)]

    report: dict[str, Any] = {"sets": {}}
    gates: dict[str, bool] = {}
    for set_name in [name for name in SETS if any(case.set_name == name for case in cases)]:
        set_cases = [case for case in cases if case.set_name == set_name]
        typed_results = [result(case, "typed") for case in set_cases]
        section: dict[str, Any] = {"front_door_outcomes": _outcome_counts(typed_results)}
        memory = [case for case in set_cases if case.group == "memory"]
        if memory:
            section.update(_score_memory(memory, result))
            gates[f"memory_{set_name}"] = all(section["memory_typed"]["gates"].values())
        if any(case.group == "notes" for case in set_cases) or any(
            item.checked_item_ids for item in typed_results
        ):
            section["filler"] = _score_filler(
                [item for item in typed_results if not _step_error(item)], seeds[set_name]
            )
            gates[f"filler_{set_name}"] = all(section["filler"]["gates"].values())
        skill = [case for case in set_cases if case.group == "skill"]
        if skill:
            section["skill"] = _score_skill(skill, result)
            gates[f"skill_{set_name}"] = all(section["skill"]["gates"].values())
        report["sets"][set_name] = section
    tier = [case for case in cases if case.group == "tier"]
    if tier:
        report["tier"] = _score_tier(tier, result)
        gates["tier"] = all(report["tier"]["gates"].values())
    report["steps"] = _score_steps([result(case, "typed") for case in cases])
    gates["steps"] = all(report["steps"]["gates"].values())
    report["latency"] = _score_latency(cases, result)
    gates["latency"] = all(report["latency"]["gates"].values())
    report["tokens"] = _score_tokens(cases, result)
    gates["tokens"] = report["tokens"]["typed_lower"]
    report["gates"] = gates
    report["complete"] = bool(gates) and all(gates.values())
    return report


def _step_error(result: ReplayResult) -> bool:
    return result.front_door_outcome == STEP_ERROR


def _outcome_counts(results: Sequence[ReplayResult]) -> dict[str, int]:
    counts = Counter(item.front_door_outcome or "missing" for item in results)
    return dict(sorted(counts.items()))


def _score_steps(typed: Sequence[ReplayResult]) -> dict[str, Any]:
    """The typed step ran on every prompt: no ``typed_step_error`` and no missing outcome."""

    errors = sum(_step_error(item) for item in typed)
    missing = sum(item.front_door_outcome is None for item in typed)
    return {
        "prompts": len(typed),
        "step_errors": errors,
        "missing_outcomes": missing,
        "gates": {"no_step_errors": bool(typed) and errors == 0 and missing == 0},
    }


def _score_memory(
    cases: Sequence[ReplayCase], result: Callable[[ReplayCase, str], ReplayResult]
) -> dict[str, Any]:
    """Jev's memory-need gates on the prompts Jev was asked; hard-rule prompts are reported apart.

    Jev never overrides a hard rule (spec §4.1), so a hard-rule prompt is a rules finding, not a
    Jev result: its count and the ones whose rules answer misses the fixture label.
    """

    from scripts.typed_decision_evaluation import score_front_door

    asked = [case for case in cases if result(case, "typed").hard_rule is not True]
    preempted = [case for case in cases if result(case, "typed").hard_rule is True]
    hard_rule = score_front_door(
        [_front_door_row(case, result(case, "typed")) for case in preempted]
    )
    return {
        "memory_typed": score_front_door(
            [_front_door_row(case, result(case, "typed")) for case in asked]
        ),
        "memory_rules": score_front_door(
            [_front_door_row(case, result(case, "rules")) for case in asked]
        ),
        "memory_hard_rule": {
            "prompts": len(preempted),
            "mismatched_case_ids": hard_rule["missed_case_ids"],
        },
    }


def _front_door_row(case: ReplayCase, result: ReplayResult) -> FrontDoorRow:
    from mnemo_memory.packages.model_gateway.decision_axes import NeedAnswer
    from scripts.typed_decision_evaluation import FrontDoorRow

    outcome = result.front_door_outcome
    return FrontDoorRow(
        case.case_id,
        case.expected_route or "none",
        NeedAnswer(result.structural_need or "unknown"),
        NeedAnswer(result.long_term_need or "unknown"),
        result.hook_ms,
        None if outcome in _NOT_UNAVAILABLE else outcome,
    )


def _seed_key(seed: ReplaySeed, item_id: str) -> str | None:
    """The seeded note (an item-ID prefix) or approved event (an exact ID) behind ``item_id``."""

    return next((key for key in seed.categories if item_id.startswith(key)), None)


def _score_filler(results: Sequence[ReplayResult], seed: ReplaySeed) -> dict[str, Any]:
    """Jev's would-drop decisions by seeded category, per check, plus note coverage.

    Every seeded Markdown note must be checked at least once: a drop rate over the few notes
    a probe happened to render says nothing about the notes no probe ever showed. Approved
    events are reported, not gated; the pinned one is never sent.
    """

    rows: list[tuple[str, bool]] = []
    uncategorized = 0
    checked_keys: set[str] = set()
    for result in results:
        dropped = set(result.dropped_item_ids)
        for item_id in result.checked_item_ids:
            key = _seed_key(seed, item_id)
            if key is None:
                uncategorized += 1
                continue
            checked_keys.add(key)
            rows.append((seed.categories[key], item_id in dropped))
    notes = {key for key in seed.categories if key.startswith(_KNOWLEDGE_ITEM_PREFIX)}
    events = {key for key in seed.categories if key.startswith(_EVENT_ITEM_PREFIX)}
    notes_checked = len(notes & checked_keys)
    relevant = [dropped for category, dropped in rows if category == "relevant"]
    noise = [dropped for category, dropped in rows if category == "noise"]
    noise_rate = share(sum(noise), len(noise))
    judged = sum(len(result.dropped_item_ids) for result in results)
    applied = sum(len(result.applied_drop_item_ids) for result in results)
    return {
        "checks": len(rows),
        "drops_judged": judged,
        "drops_applied": applied,
        "drops_not_applied": judged - applied,
        "uncategorized_checks": uncategorized,
        "notes_seeded": len(notes),
        "notes_checked": notes_checked,
        "events_seeded": len(events),
        "events_checked": len(events & checked_keys),
        "relevant_checks": len(relevant),
        "relevant_dropped": sum(relevant),
        "noise_checks": len(noise),
        "noise_drop_rate": noise_rate,
        "superseded_dropped": sum(
            dropped for category, dropped in rows if category == "superseded"
        ),
        "gates": {
            "relevant_dropped": bool(relevant) and sum(relevant) == 0,
            "filler_removed": bool(noise) and noise_rate >= 0.9,
            "all_notes_checked": bool(notes) and notes_checked == len(notes),
        },
    }


def _score_skill(
    cases: Sequence[ReplayCase], result: Callable[[ReplayCase, str], ReplayResult]
) -> dict[str, Any]:
    from mnemo_memory.packages.model_gateway.decision_axes import SKILL_PICK_NONE

    scored = [case for case in cases if not _step_error(result(case, "typed"))]

    def correct(case: ReplayCase, arm: str) -> bool:
        return result(case, arm).skill_pick == case.expected_skill

    with_skill = [case for case in scored if case.expected_skill != SKILL_PICK_NONE]
    without = [case for case in scored if case.expected_skill == SKILL_PICK_NONE]
    typed_skill = share(sum(correct(case, "typed") for case in with_skill), len(with_skill))
    typed_none = share(sum(correct(case, "typed") for case in without), len(without))
    typed_all = share(sum(correct(case, "typed") for case in scored), len(scored))
    keyword_all = share(sum(correct(case, "rules") for case in scored), len(scored))
    return {
        "skill_prompts": len(with_skill),
        "no_skill_prompts": len(without),
        "step_error_prompts": len(cases) - len(scored),
        "typed_correct_skill": typed_skill,
        "typed_correct_none": typed_none,
        "typed_accuracy": typed_all,
        "keyword_accuracy": keyword_all,
        "gates": {
            "correct_skill": not with_skill or typed_skill >= 0.85,
            "correct_none": not without or typed_none >= 0.95,
            "better_than_keyword": typed_all > keyword_all,
        },
    }


def _score_tier(
    cases: Sequence[ReplayCase], result: Callable[[ReplayCase, str], ReplayResult]
) -> dict[str, Any]:
    heavy = [case for case in cases if case.expected_tier == "heavy"]
    recalled = sum(result(case, "typed").tier != "light" for case in heavy)
    recall = share(recalled, len(heavy))
    return {
        "heavy_cases": len(heavy),
        "heavy_recall": recall,
        "gates": {"heavy_recall": bool(heavy) and recall >= 0.95},
    }


def _score_latency(
    cases: Sequence[ReplayCase], result: Callable[[ReplayCase, str], ReplayResult]
) -> dict[str, Any]:
    typed = [result(case, "typed") for case in cases]
    rules = [result(case, "rules") for case in cases]
    added = sorted(
        max(0, result(case, "typed").hook_ms - result(case, "rules").hook_ms) for case in cases
    )
    steps = sorted(item.step_ms for item in typed if item.step_ms is not None)
    cap_share = share(sum(item.cap_hit for item in typed), len(typed))
    return {
        "prompts": len(typed),
        "cap_hit_share": cap_share,
        "added_hook_ms": _percentiles(added),
        "step_ms": _percentiles(steps),
        "hook_ms": {
            "rules": _percentiles(sorted(item.hook_ms for item in rules)),
            "typed": _percentiles(sorted(item.hook_ms for item in typed)),
        },
        "gates": {
            "cap_hit_share": bool(typed) and cap_share <= MAXIMUM_CAP_SHARE,
            "added_hook_max": bool(added) and added[-1] <= MAXIMUM_ADDED_HOOK_MS,
        },
    }


def _score_tokens(
    cases: Sequence[ReplayCase], result: Callable[[ReplayCase, str], ReplayResult]
) -> dict[str, Any]:
    def total(arm: str, group: str | None = None) -> int:
        return sum(
            result(case, arm).attached_tokens
            for case in cases
            if group is None or case.group == group
        )

    groups = sorted({case.group for case in cases})
    return {
        "rules": total("rules"),
        "typed": total("typed"),
        "by_group": {
            group: {"rules": total("rules", group), "typed": total("typed", group)}
            for group in groups
        },
        "typed_lower": total("typed") < total("rules"),
    }


def _percentiles(ordered: Sequence[int]) -> dict[str, int | None]:
    return {
        "p50": nearest_rank(ordered, 0.50),
        "p95": nearest_rank(ordered, 0.95),
        "max": ordered[-1] if ordered else None,
    }
