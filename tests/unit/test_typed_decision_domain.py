from mnemo_memory.packages.domain import (
    ModelTaskType,
    TypedDecisionDataRoute,
    TypedDecisionKind,
    TypedDecisionMode,
    TypedDecisionSource,
    TypedDecisionUnavailableReason,
    typed_decision_source_permitted,
)


def test_phase_one_has_exactly_one_data_route() -> None:
    assert [route.value for route in TypedDecisionDataRoute] == ["synthetic_only"]


def test_synthetic_only_route_permits_fixtures_and_blocks_runtime() -> None:
    route = TypedDecisionDataRoute.SYNTHETIC_ONLY
    assert typed_decision_source_permitted(route, TypedDecisionSource.SYNTHETIC_FIXTURE)
    assert not typed_decision_source_permitted(route, TypedDecisionSource.RUNTIME)


def test_closed_vocabularies_match_the_spec() -> None:
    assert {reason.value for reason in TypedDecisionUnavailableReason} == {
        "disabled",
        "data_route_blocked",
        "no_credential",
        "secret_blocked",
        "sensitivity_blocked",
        "budget_denied",
        "timeout",
        "http_error",
        "schema_invalid",
    }
    assert {kind.value for kind in TypedDecisionKind} == {
        "front_door",
        "relevance",
        "extraction_gate",
        "compaction",
        "dedupe",
        "semantic_kind",
        "verify",
        "tier_hint",
        "skill",
    }
    assert [mode.value for mode in TypedDecisionMode] == ["off", "shadow", "live"]
    assert ModelTaskType.TYPED_DECISION.value == "typed_decision"
