"""Test-only helpers for the Jev hook wiring: a scripted Jev transport and a seeded project.

Nothing here opens a network connection. The transport answers from a script, and the key is a
fake literal, never the real ``TYPESAFE_API_KEY``.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from mnemo_memory.apps.cli import main as cli
from mnemo_memory.apps.cli.typed_decision_composition import (
    build_synthetic_typed_decision_classifier,
)
from mnemo_memory.apps.cli.typed_decision_hook import (
    DecisionObserver,
    TypedHookModes,
    TypedHookOverrides,
)
from mnemo_memory.apps.cli.typed_note_judge import (
    JUDGE_DEADLINE_SECONDS,
    JudgeTally,
    judge_candidates,
)
from mnemo_memory.connectors.automatic_memory.hook import PromptContextAttachment
from mnemo_memory.packages.application import (
    LocalConfig,
    PersonalSettings,
    PersonalSettingsStore,
    RecordApprovedEpisodicEvent,
    SetApprovedEpisodicEventPin,
    build_checkpoint_runtime,
)
from mnemo_memory.packages.application.automatic_memory import (
    LocalMemoryProjectBindingStore,
    MemoryProjectBinding,
)
from mnemo_memory.packages.application.checkpoints import CreateCheckpoint
from mnemo_memory.packages.domain import (
    ApprovedEventKind,
    CheckpointContent,
    EvidenceSourceType,
    SourceTrustClass,
)
from mnemo_memory.packages.model_gateway.typed_decisions import (
    GuardedTypedDecisionClassifier,
    TypedDecisionRecorder,
)
from mnemo_memory.packages.storage import LocalNoteVerdictCache, SQLiteKnowledgeDocumentRepository
from mnemo_memory.packages.telemetry import (
    AutomaticRouteDiagnosticsMode,
    AutomaticRouteDiagnosticsSettings,
    LocalAutomaticRouteDiagnosticsSettingsStore,
)
from scripts.typed_decision_replay import (
    approved_event_item_ids,
    evidence,
    knowledge_note_item_ids,
    skill_markdown,
)

FAKE_TYPESAFE_KEY = "test-key-not-real-0000"
FILLER_MARKER = "FILLER"
KNOWLEDGE_PROMPT = "What do the project notes say about the invoice export?"
PRIOR_PROMPT = "Use the decision from our previous session."
SKILL_PROMPT = "Create the changelog entry for version 2.4."
LAZY_PROMPT = "finance reconciliation variance"
GREETING_PROMPT = "hello"
USEFUL_NOTE = "The invoice export must keep the ledger sequence numbers exactly."
FILLER_NOTE = "FILLER: someone mentioned the invoice export over coffee and liked the weather."
PINNED_EVENT = "Keep invoice export retries idempotent."
FILLER_EVENT = "FILLER chatter about lunch plans with the team."
HANDOFF_OBJECTIVE = "Finish the invoice export retry fix."

Answer = tuple[str, float]


def choice_answer(labels: list[str], chosen: str, confidence: float) -> dict[str, object]:
    """One Jev ``choice`` answer whose chosen label carries the highest probability."""

    others = [label for label in labels if label != chosen]
    rest = (1.0 - confidence) / len(others)
    probabilities = {label: rest for label in others}
    probabilities[chosen] = confidence
    return {
        "type": "choice",
        "choice": chosen,
        "confidence": confidence,
        "probabilities": probabilities,
    }


class ScriptedJevTransport:
    """Answer Jev requests from a script; thread-safe and never opens a socket.

    ``answers`` maps a question name to ``(label, confidence)``. ``note_substance`` is answered
    ``filler`` when the judged text contains ``FILLER``, else ``task_information``. Any other
    unscripted question gets its first label at confidence 0.55, below the 0.6 bar.
    """

    def __init__(
        self, answers: Mapping[str, Answer] | None = None, *, model: str = "jev-1.13.0"
    ) -> None:
        self.answers = dict(answers or {})
        self.model = model
        self.calls = 0
        self.questions: list[tuple[str, ...]] = []
        self.states: list[str] = []
        self._lock = threading.Lock()

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        request = json.loads(body)
        state = str(request["state"])
        with self._lock:
            self.calls += 1
            self.questions.append(tuple(request["questions"]))
            self.states.append(state)
        answers: dict[str, object] = {}
        for name, question in request["questions"].items():
            labels = list(question["criteria"])
            if name == "note_substance":
                label, confidence = (
                    ("filler", 0.95) if FILLER_MARKER in state else ("task_information", 0.95)
                )
            else:
                label, confidence = self.answers.get(name, (labels[0], 0.55))
            answers[name] = choice_answer(labels, label, confidence)
        return json.dumps(
            {"model": self.model, "answers": answers, "usage": {"input_tokens": 10}}
        ).encode()


@dataclass(frozen=True, slots=True)
class HookFixture:
    data: Path
    project: Path
    binding: MemoryProjectBinding
    useful_note_prefix: str
    filler_note_prefix: str
    pinned_event_id: str
    filler_event_id: str


_SKILLS = {
    "release-notes": skill_markdown(
        "release-notes",
        ("release", "changelog"),
        "Use when drafting release notes or a changelog entry for a new version",
    ),
    "test-plan": skill_markdown(
        "test-plan",
        ("testing", "coverage"),
        "Use when designing a test plan or deciding which tests a change needs",
    ),
}


def _handoff() -> CheckpointContent:
    return CheckpointContent(
        task_objective=HANDOFF_OBJECTIVE,
        completed_work=("Recorded bounded progress.",),
        current_state="The retry fix is half done.",
        remaining_work=("Finish the retry fix.",),
        decisions=("Keep retries idempotent.",),
        failures=(),
        blockers=(),
        relevant_files=("export.py",),
        relevant_artifacts=(),
        verification_performed=("Focused tests ran.",),
        token_estimate=70,
    )


def seed_hook_fixture(
    root: Path, *, semantic_gate: bool, with_handoff: bool = False
) -> HookFixture:
    """A bound project with two notes, two approved events (one pinned) and two skills."""

    project = root / "project"
    project.mkdir(parents=True)
    data = root / "data"
    binding = LocalMemoryProjectBindingStore(data).enable(project)
    PersonalSettingsStore(data).save(
        PersonalSettings(experimental_semantic_memory_enabled=semantic_gate)
    )
    LocalAutomaticRouteDiagnosticsSettingsStore(data).save(
        AutomaticRouteDiagnosticsSettings(AutomaticRouteDiagnosticsMode.TRACE, 7)
    )
    (project / "notes").mkdir()
    (project / "notes" / "export.md").write_text(f"# Invoice export\n{USEFUL_NOTE}\n", "utf-8")
    (project / "notes" / "chatter.md").write_text(
        f"# Invoice export chatter\n{FILLER_NOTE}\n", "utf-8"
    )
    (project / "skills").mkdir()
    for name, body in _SKILLS.items():
        (project / "skills" / f"{name}.md").write_text(body, "utf-8")
    cli._refresh_project_knowledge(data, binding)
    with build_checkpoint_runtime(LocalConfig.defaults(data)) as runtime:
        service = runtime.checkpoint_service
        if with_handoff:
            service.create(
                CreateCheckpoint(
                    binding.checkpoint_scope,
                    _handoff(),
                    (
                        evidence(
                            "handoff",
                            source=EvidenceSourceType.CHECKPOINT,
                            trust=SourceTrustClass.USER_AUTHORED,
                        ),
                    ),
                )
            )
        pinned = service.record_approved_event(
            RecordApprovedEpisodicEvent(
                binding.checkpoint_scope,
                ApprovedEventKind.DECISION,
                PINNED_EVENT,
                "typed-hook:pinned",
                (evidence("pinned"),),
            )
        ).event
        filler = service.record_approved_event(
            RecordApprovedEpisodicEvent(
                binding.checkpoint_scope,
                ApprovedEventKind.TOOL_OUTCOME,
                FILLER_EVENT,
                "typed-hook:filler",
                (evidence("filler"),),
            )
        ).event
        service.set_approved_event_pin(
            SetApprovedEpisodicEventPin(
                binding.checkpoint_scope,
                pinned.event_id,
                True,
                "typed-hook:pin",
                (
                    evidence(
                        "pin",
                        source=EvidenceSourceType.USER_CORRECTION,
                        trust=SourceTrustClass.USER_CORRECTION,
                    ),
                ),
            )
        )
    repository = SQLiteKnowledgeDocumentRepository(data / "mnemo.sqlite3", base_directory=data)
    repository.migrate()
    prefixes = {
        known.relative_path: f"knowledge:{known.document_id}:"
        for known in repository.list_active_documents(binding.scope)
    }
    return HookFixture(
        data,
        project,
        binding,
        prefixes["notes/export.md"],
        prefixes["notes/chatter.md"],
        f"approved-episodic:{pinned.event_id}",
        f"approved-episodic:{filler.event_id}",
    )


def synthetic_overrides(
    fixture: HookFixture,
    transport: ScriptedJevTransport,
    modes: TypedHookModes,
    observer: DecisionObserver | None = None,
) -> TypedHookOverrides:
    """Replay overrides whose guard reaches only ``transport`` (synthetic-fixture source)."""

    def build(recorder: TypedDecisionRecorder) -> GuardedTypedDecisionClassifier:
        return build_synthetic_typed_decision_classifier(
            PersonalSettings(),
            data_directory=fixture.data,
            environ={"TYPESAFE_API_KEY": FAKE_TYPESAFE_KEY},
            jev_transport=transport,
            recorder=recorder,
        )

    return TypedHookOverrides(build, modes, observer)


def run_hook(
    fixture: HookFixture, prompt: str, overrides: TypedHookOverrides | None = None
) -> PromptContextAttachment:
    return cli._automatic_prompt_context_for_hook(
        fixture.data,
        fixture.binding.checkpoint_scope,
        prompt,
        "codex",
        replay_overrides=overrides,
    )


def prime_note_verdicts(fixture: HookFixture, transport: ScriptedJevTransport) -> JudgeTally:
    """Warm the fixture's verdict cache: judge every note and event through ``transport`` only.

    It uses the background judge's own reader (the scoped ``get_context item_ids`` lookup, so
    the pinned event is never sent) and ``judge_candidates`` with the synthetic-source guard.
    """

    item_ids = (
        *knowledge_note_item_ids(fixture.data, fixture.binding),
        *approved_event_item_ids(fixture.data, fixture.binding),
    )
    candidates = cli._note_candidates_by_id(
        fixture.data, fixture.binding.checkpoint_scope, item_ids
    )
    settings = PersonalSettings()
    guard = build_synthetic_typed_decision_classifier(
        settings,
        data_directory=fixture.data,
        environ={"TYPESAFE_API_KEY": FAKE_TYPESAFE_KEY},
        jev_transport=transport,
        deadline_seconds=JUDGE_DEADLINE_SECONDS,
    )
    return judge_candidates(
        guard,
        candidates,
        cache=LocalNoteVerdictCache(fixture.data),
        model_version=settings.typed_decision_model_id,
    )
