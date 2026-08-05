from __future__ import annotations

from typing import cast

import pytest

from mnemo_memory.packages.domain import Sensitivity
from mnemo_memory.packages.policy import (
    ContentSafetyClassifier,
    ContentSafetyDecision,
    ContentSafetyPolicy,
)


class RecordingClassifier:
    def __init__(
        self,
        name: str,
        calls: list[str],
        decision: ContentSafetyDecision,
    ) -> None:
        self._name = name
        self._calls = calls
        self._decision = decision

    def classify(self, values: tuple[str, ...]) -> ContentSafetyDecision:
        assert values
        self._calls.append(self._name)
        return self._decision


class InvalidClassifier:
    def classify(self, values: tuple[str, ...]) -> object:
        return values


class FailingClassifier:
    def classify(self, values: tuple[str, ...]) -> ContentSafetyDecision:
        raise RuntimeError(values[0])


@pytest.mark.parametrize(
    "content",
    [
        "-----BEGIN PRIVATE KEY-----",
        "AKIAABCDEFGHIJKLMNOP",
        "ghp_abcdefghijklmnopqrstuvwxyz123456",
        "sk-abcdefghijklmnopqrstuvwxyz123456",
        "api_key=1234567890abcdefghijklmnop",
    ],
)
def test_mandatory_secret_classifier_rejects_without_invoking_plugins(content: str) -> None:
    calls: list[str] = []
    permissive = RecordingClassifier(
        "permissive", calls, ContentSafetyDecision(True, Sensitivity.NORMAL)
    )

    decision = ContentSafetyPolicy((permissive,)).assess(content)

    assert decision == ContentSafetyDecision(
        False, Sensitivity.PROHIBITED, "MNEMO_CONTENT_SECRET_REJECTED"
    )
    assert calls == []
    assert content not in repr(decision)


def test_additional_classifiers_run_in_order_and_only_strengthen_sensitivity() -> None:
    calls: list[str] = []
    policy = ContentSafetyPolicy(
        (
            RecordingClassifier(
                "personal", calls, ContentSafetyDecision(True, Sensitivity.PERSONAL)
            ),
            RecordingClassifier(
                "restricted", calls, ContentSafetyDecision(True, Sensitivity.RESTRICTED)
            ),
            RecordingClassifier("normal", calls, ContentSafetyDecision(True, Sensitivity.NORMAL)),
        )
    )

    assert policy.assess("bounded ordinary content") == ContentSafetyDecision(
        True, Sensitivity.RESTRICTED
    )
    assert calls == ["personal", "restricted", "normal"]


def test_plugin_rejection_stops_later_classifiers_and_remains_content_free() -> None:
    calls: list[str] = []
    policy = ContentSafetyPolicy(
        (
            RecordingClassifier(
                "reject",
                calls,
                ContentSafetyDecision(
                    False, Sensitivity.PROHIBITED, "MNEMO_FIXTURE_POLICY_REJECTED"
                ),
            ),
            RecordingClassifier("later", calls, ContentSafetyDecision(True, Sensitivity.NORMAL)),
        )
    )

    decision = policy.assess("safe source payload")

    assert decision.code == "MNEMO_FIXTURE_POLICY_REJECTED"
    assert calls == ["reject"]
    assert "safe source payload" not in repr(decision)


@pytest.mark.parametrize(
    ("classifier", "code"),
    [
        (
            cast(ContentSafetyClassifier, InvalidClassifier()),
            "MNEMO_CONTENT_CLASSIFIER_INVALID",
        ),
        (FailingClassifier(), "MNEMO_CONTENT_CLASSIFIER_FAILED"),
    ],
)
def test_invalid_or_failed_plugins_fail_closed(
    classifier: ContentSafetyClassifier, code: str
) -> None:
    decision = ContentSafetyPolicy((classifier,)).assess("ordinary content")

    assert decision == ContentSafetyDecision(False, Sensitivity.PROHIBITED, code)


def test_classifier_configuration_and_input_are_bounded() -> None:
    classifier = RecordingClassifier("bounded", [], ContentSafetyDecision(True, Sensitivity.NORMAL))
    with pytest.raises(ValueError, match="configuration"):
        ContentSafetyPolicy((classifier,) * 9)

    decision = ContentSafetyPolicy().assess(*(("x",) * 2_049))
    assert decision.code == "MNEMO_CONTENT_CLASSIFICATION_BOUNDS_EXCEEDED"
