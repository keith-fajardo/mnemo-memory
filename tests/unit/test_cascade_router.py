import asyncio
import math

import pytest

from mnemo_memory.packages.model_gateway.cascade_router import (
    YES_NO_LABELS,
    AxisKind,
    AxisRoutedClassifier,
    CascadeCommittee,
    CascadeRouterError,
    ClassifierAxis,
    ClassifierResult,
    PrecomputedClassifier,
    logprob_from_probability,
)

AXES = (
    ClassifierAxis("domain", "classify domain fit", ("FIT", "MISS"), -0.25),
    ClassifierAxis("complexity", "classify complexity", ("EASY", "HARD"), 0.5),
    ClassifierAxis("risk", "classify stakes", ("LOW", "HIGH"), 0.6, veto_score=0.9),
    ClassifierAxis("tool_need", "classify tools", ("NO", "YES"), 0.4),
)


class StubClassifier:
    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores
        self.calls: list[str] = []

    async def classify(self, axis: ClassifierAxis, prompt: str) -> ClassifierResult:
        self.calls.append(axis.name)
        return ClassifierResult(axis.name, axis.allowed_labels[0], -0.1, self.scores[axis.name])


class BatchStub:
    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores
        self.batches: list[tuple[str, ...]] = []

    async def classify(self, axis: ClassifierAxis, prompt: str) -> ClassifierResult:
        raise AssertionError("batch-capable adapters must not be asked axis by axis")

    async def classify_batch(
        self, axes: tuple[ClassifierAxis, ...], prompt: str
    ) -> tuple[ClassifierResult, ...]:
        self.batches.append(tuple(axis.name for axis in axes))
        return tuple(
            ClassifierResult(axis.name, axis.allowed_labels[0], -0.1, self.scores[axis.name])
            for axis in axes
        )


def test_committee_consolidates_diverse_axis_scores_to_light() -> None:
    classifier = StubClassifier({"domain": 1.0, "complexity": 0.1, "risk": 0.0, "tool_need": 0.0})
    decision = asyncio.run(
        CascadeCommittee(AXES, escalation_threshold=0.4).classify("write a title", classifier)
    )
    assert decision.route == "light"
    assert decision.reason == "light"
    assert decision.route_score == pytest.approx(-0.2)
    assert classifier.calls == ["domain", "complexity", "risk", "tool_need"]


def test_committee_threshold_and_any_veto_escalate_to_heavy() -> None:
    threshold = StubClassifier({"domain": 0.0, "complexity": 0.9, "risk": 0.0, "tool_need": 0.0})
    decision = asyncio.run(
        CascadeCommittee(AXES, escalation_threshold=0.4).classify("prove theorem", threshold)
    )
    assert (decision.route, decision.reason) == ("heavy", "threshold")

    veto = StubClassifier({"domain": 1.0, "complexity": 0.0, "risk": 0.9, "tool_need": 0.0})
    decision = asyncio.run(
        CascadeCommittee(AXES, escalation_threshold=0.99).classify("medical dosage", veto)
    )
    assert (decision.route, decision.reason) == ("heavy", "veto:risk")


def test_committee_starts_all_classifiers_before_waiting_for_any_result() -> None:
    started: set[str] = set()
    release = asyncio.Event()

    class BlockingClassifier:
        async def classify(self, axis: ClassifierAxis, prompt: str) -> ClassifierResult:
            started.add(axis.name)
            await release.wait()
            return ClassifierResult(axis.name, axis.allowed_labels[0], -0.1, 0.0)

    async def exercise() -> None:
        task = asyncio.create_task(
            CascadeCommittee(AXES, escalation_threshold=0.4).classify("task", BlockingClassifier())
        )
        for _ in range(10):
            if len(started) == len(AXES):
                break
            await asyncio.sleep(0)
        assert started == {axis.name for axis in AXES}
        release.set()
        assert (await task).route == "light"

    asyncio.run(exercise())


def test_committee_rejects_unconstrained_or_mismatched_provider_output() -> None:
    class BadClassifier:
        async def classify(self, axis: ClassifierAxis, prompt: str) -> ClassifierResult:
            return ClassifierResult(axis.name, "unconstrained prose", -0.1, 0.0)

    with pytest.raises(CascadeRouterError, match="LABEL_INVALID"):
        asyncio.run(
            CascadeCommittee(AXES, escalation_threshold=0.4).classify("task", BadClassifier())
        )


def test_router_rejects_empty_prompt_and_invalid_configuration() -> None:
    with pytest.raises(CascadeRouterError, match="PROMPT_INVALID"):
        asyncio.run(
            CascadeCommittee(AXES, escalation_threshold=0.4).classify(" ", StubClassifier({}))
        )
    with pytest.raises(CascadeRouterError, match="THRESHOLD_INVALID"):
        CascadeCommittee(AXES, escalation_threshold=1.1)
    with pytest.raises(CascadeRouterError, match="AXIS_INVALID"):
        ClassifierAxis("risk", "prompt", ("ONE",), 1.0)
    with pytest.raises(CascadeRouterError, match="CLASSIFIER_RESULT_INVALID"):
        ClassifierResult("risk", "HIGH", float("nan"), 1.0)


def test_committee_asks_a_batch_capable_adapter_exactly_once() -> None:
    stub = BatchStub({"domain": 0.0, "complexity": 0.9, "risk": 0.0, "tool_need": 0.0})
    decision = asyncio.run(CascadeCommittee(AXES, escalation_threshold=0.4).classify("task", stub))
    assert decision.route == "heavy"
    assert stub.batches == [("domain", "complexity", "risk", "tool_need")]


def test_yes_no_axis_requires_no_yes_labels_and_scores_probability_of_yes() -> None:
    axis = ClassifierAxis("needs_x", "needs x", YES_NO_LABELS, 0.0, kind=AxisKind.YES_NO)
    assert axis.escalation_score_for({"yes": 0.93, "no": 0.07}) == pytest.approx(0.93)
    with pytest.raises(CascadeRouterError, match="AXIS_INVALID"):
        ClassifierAxis("needs_x", "needs x", ("yes", "no"), 0.0, kind=AxisKind.YES_NO)


def test_choice_axis_escalation_is_the_expected_label_score() -> None:
    axis = ClassifierAxis(
        "tool_need",
        "tools",
        ("none", "read_heavy", "edit"),
        0.4,
        label_scores=(0.0, 0.25, 0.75),
    )
    probabilities = {"none": 0.18, "read_heavy": 0.82, "edit": 0.0}
    assert axis.escalation_score_for(probabilities) == pytest.approx(0.205)
    assert ClassifierAxis("kind", "kind", ("a", "b"), 0.0).escalation_score_for({"a": 1.0}) == 0.0
    with pytest.raises(CascadeRouterError, match="AXIS_INVALID"):
        ClassifierAxis("t", "t", ("a", "b"), 0.0, label_scores=(0.0,))
    with pytest.raises(CascadeRouterError, match="AXIS_INVALID"):
        ClassifierAxis("t", "t", ("a", "b"), 0.0, label_scores=(0.0, 1.5))


def test_criteria_must_follow_allowed_labels() -> None:
    ClassifierAxis(
        "c", "c", ("light", "heavy"), 0.0, criteria=(("light", "easy"), ("heavy", "hard"))
    )
    with pytest.raises(CascadeRouterError, match="AXIS_INVALID"):
        ClassifierAxis(
            "c", "c", ("light", "heavy"), 0.0, criteria=(("heavy", "hard"), ("light", "easy"))
        )
    with pytest.raises(CascadeRouterError, match="AXIS_INVALID"):
        ClassifierAxis(
            "c", "c", ("light", "heavy"), 0.0, criteria=(("light", " "), ("heavy", "hard"))
        )


def test_logprob_from_probability_clamps_zero_and_rejects_invalid() -> None:
    assert logprob_from_probability(1.0) == 0.0
    assert logprob_from_probability(0.0) == pytest.approx(math.log(1e-6))
    for bad in (float("nan"), -0.1, 1.1):
        with pytest.raises(CascadeRouterError, match="PROBABILITY_INVALID"):
            logprob_from_probability(bad)


def test_result_confidence_must_be_a_probability() -> None:
    assert ClassifierResult("a", "x", -0.1, 0.5, confidence=0.6).confidence == 0.6
    with pytest.raises(CascadeRouterError, match="CLASSIFIER_RESULT_INVALID"):
        ClassifierResult("a", "x", -0.1, 0.5, confidence=1.2)


def test_axis_routed_classifier_batches_per_source_and_preserves_axis_order() -> None:
    remote = BatchStub({"complexity": 0.2, "tool_need": 0.25})
    local = StubClassifier({"domain": 1.0, "risk": 0.0})
    routed = AxisRoutedClassifier(
        {"complexity": remote, "tool_need": remote, "domain": local, "risk": local}
    )
    results = asyncio.run(routed.classify_batch(AXES, "task"))
    assert [result.axis_name for result in results] == [
        "domain",
        "complexity",
        "risk",
        "tool_need",
    ]
    assert remote.batches == [("complexity", "tool_need")]
    assert sorted(local.calls) == ["domain", "risk"]


def test_axis_routed_classifier_rejects_unrouted_axis() -> None:
    routed = AxisRoutedClassifier({"domain": StubClassifier({"domain": 0.0})})
    with pytest.raises(CascadeRouterError, match="AXIS_UNROUTED"):
        asyncio.run(routed.classify_batch(AXES, "task"))


def test_precomputed_classifier_serves_known_axes_and_rejects_unknown() -> None:
    known = ClassifierResult("complexity", "EASY", -0.1, 0.2)
    precomputed = PrecomputedClassifier((known,))
    assert asyncio.run(precomputed.classify(AXES[1], "task")) is known
    with pytest.raises(CascadeRouterError, match="AXIS_MISMATCH"):
        asyncio.run(precomputed.classify(AXES[0], "task"))
