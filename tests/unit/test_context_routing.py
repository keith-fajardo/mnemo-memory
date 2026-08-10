"""Deterministic, cost-aware automatic context routing."""

import pytest

from mnemo_memory.packages.application.context_routing import (
    AutomaticContextNeed,
    AutomaticContextRoute,
    AutomaticContextRouteReason,
    CompactLocalMemoryRouter,
    CompactMemoryRoute,
    CompactMemoryRouteDecision,
    LearnedRoutePhrase,
    bounded_automatic_context_prompt,
    choose_automatic_context_route,
    plan_automatic_context_needs,
)


def test_exact_lookup_prefers_no_attachment_and_direct_inspection() -> None:
    decision = choose_automatic_context_route("Where is AutomaticMemoryHook defined?")
    file_count = choose_automatic_context_route("How many SQL files are in this repository?")

    assert decision.route is AutomaticContextRoute.DIRECT_LOOKUP
    assert decision.reason is AutomaticContextRouteReason.EXACT_SOURCE_LOOKUP
    assert decision.maximum_attachment_tokens == 0
    assert file_count.route is AutomaticContextRoute.DIRECT_LOOKUP
    assert file_count.maximum_attachment_tokens == 0


def test_saved_structure_is_selected_for_relationship_and_inventory_questions() -> None:
    architecture = choose_automatic_context_route(
        "Show the architecture of this repository and its main components."
    )
    inventory = choose_automatic_context_route("How many dbt models are in this project?")
    impact = choose_automatic_context_route("What depends on ContextPacket downstream?")

    assert architecture.route is AutomaticContextRoute.STRUCTURE
    assert architecture.reason is AutomaticContextRouteReason.ARCHITECTURE
    assert inventory.route is AutomaticContextRoute.STRUCTURE
    assert inventory.reason is AutomaticContextRouteReason.DBT_STRUCTURE
    assert impact.route is AutomaticContextRoute.STRUCTURE
    assert impact.reason is AutomaticContextRouteReason.SOURCE_IMPACT


def test_prior_memory_and_lazy_skill_discovery_take_explicit_routes() -> None:
    recap = choose_automatic_context_route("What did we finish in the previous session?")
    skill = choose_automatic_context_route(
        "Help me design a complex feature with unclear requirements.", skill_candidate_count=1
    )

    assert recap.route is AutomaticContextRoute.PRIOR_MEMORY
    assert recap.reason is AutomaticContextRouteReason.PRIOR_MEMORY
    assert skill.route is AutomaticContextRoute.SKILL_DISCOVERY
    assert skill.reason is AutomaticContextRouteReason.SKILL_TRIGGER
    assert skill.maximum_attachment_tokens < 300


def test_local_mnemo_operations_select_bounded_diagnostics_guidance() -> None:
    version = choose_automatic_context_route("What Mnemo are you using right?")
    hook = choose_automatic_context_route("Why did the SessionStart hook timeout?")

    assert version.route is AutomaticContextRoute.LOCAL_DIAGNOSTICS
    assert version.reason is AutomaticContextRouteReason.LOCAL_MNEMO_OPERATION
    assert version.maximum_attachment_tokens == 256
    assert hook.route is AutomaticContextRoute.LOCAL_DIAGNOSTICS
    assert choose_automatic_context_route("Is long-term memory worth using?").route is (
        AutomaticContextRoute.KNOWLEDGE
    )


def test_trivial_prompt_attaches_nothing_and_general_domain_query_probes_memory() -> None:
    assert choose_automatic_context_route("hello").route is AutomaticContextRoute.NONE
    general = choose_automatic_context_route("finance reconciliation variance")
    assert general.route is AutomaticContextRoute.KNOWLEDGE
    assert general.reason is AutomaticContextRouteReason.ROUTER_UNCERTAIN


def test_compact_router_recognizes_held_out_memory_intent_without_a_model_call() -> None:
    router = CompactLocalMemoryRouter()
    expected = {
        CompactMemoryRoute.PRIOR_MEMORY: (
            "Let's carry on from where the other chat stopped.",
            "Use the rationale we settled on.",
            "Did an attempt at this break already?",
            "Restore the unfinished handoff.",
            "Have we encountered a similar failure?",
            "Use the approach from our other conversation.",
        ),
        CompactMemoryRoute.KNOWLEDGE: (
            "Consult the ADR about database tenancy.",
            "Look up our release rules.",
            "Search repository notes for OAuth.",
            "What do our checked-in docs require?",
        ),
        CompactMemoryRoute.STRUCTURE: (
            "Which parts of the codebase collaborate in request routing?",
            "Trace every caller that can reach this adapter.",
            "Which modules participate in checkpoint persistence?",
            "Show the producers and consumers around this table.",
        ),
        CompactMemoryRoute.NONE: (
            "Start a new implementation from this specification.",
            "Review this new patch.",
            "Solve this equation.",
            "Write a haiku.",
            "Write a unit test for this code.",
            "Summarize the following text.",
        ),
    }

    for route, prompts in expected.items():
        assert all(router.classify(prompt).route is route for prompt in prompts)


def test_compact_router_only_skips_memory_for_a_separated_no_memory_prediction() -> None:
    self_contained = choose_automatic_context_route(
        "Start a new implementation from this specification."
    )
    continuation = choose_automatic_context_route(
        "Let's carry on from where the other chat stopped."
    )
    ambiguous = choose_automatic_context_route("finance reconciliation variance")

    assert self_contained.route is AutomaticContextRoute.NONE
    assert self_contained.reason is AutomaticContextRouteReason.ROUTER_NO_MEMORY
    assert self_contained.maximum_attachment_tokens == 0
    assert continuation.route is AutomaticContextRoute.PRIOR_MEMORY
    assert continuation.reason is AutomaticContextRouteReason.ROUTER_PRIOR_MEMORY
    assert ambiguous.route is AutomaticContextRoute.KNOWLEDGE
    assert ambiguous.reason is AutomaticContextRouteReason.ROUTER_UNCERTAIN


def test_literal_routes_remain_authoritative_over_compact_router_predictions() -> None:
    exact = choose_automatic_context_route("Where is CompactLocalMemoryRouter defined?")
    explicit = choose_automatic_context_route("What does the documented policy require?")
    structure = choose_automatic_context_route("What depends on CompactLocalMemoryRouter?")

    assert exact.reason is AutomaticContextRouteReason.EXACT_SOURCE_LOOKUP
    assert explicit.reason is AutomaticContextRouteReason.EXPLICIT_KNOWLEDGE
    assert structure.reason is AutomaticContextRouteReason.SOURCE_IMPACT


def test_long_prompt_uses_one_bounded_head_and_tail_view_for_every_route() -> None:
    middle_marker = "middle-private-marker-4831"
    prompt = (
        "Use the rationale from our earlier session. "
        + ("pasted-prefix-noise " * 40)
        + middle_marker
        + (" pasted-suffix-noise" * 40)
        + "Finish from the saved handoff."
    )

    bounded = bounded_automatic_context_prompt(prompt)
    decision = choose_automatic_context_route(prompt)

    assert len(prompt) > 512
    assert len(bounded) <= 512
    assert bounded.startswith("Use the rationale from our earlier session.")
    assert bounded.endswith("Finish from the saved handoff.")
    assert middle_marker not in bounded
    assert decision.route is AutomaticContextRoute.PRIOR_MEMORY


def test_long_prompt_can_select_structural_context_from_its_instruction_tail() -> None:
    prompt = (
        "standalone pasted log line\n" * 100
    ) + "Trace every caller that reaches this adapter."

    decision = choose_automatic_context_route(prompt)

    assert decision.route is AutomaticContextRoute.STRUCTURE
    assert decision.reason is AutomaticContextRouteReason.ROUTER_STRUCTURE
    assert decision.maximum_attachment_tokens == 1_000


def test_presence_features_make_repeated_padding_idempotent() -> None:
    router = CompactLocalMemoryRouter()
    base = "Continue the work we discussed earlier. "
    padding = "write a unit test for this function "

    twice = router.classify(base + padding * 2)
    repeated = router.classify(base + padding * 20)

    assert repeated == twice
    assert choose_automatic_context_route(base + padding * 20).route is (
        AutomaticContextRoute.PRIOR_MEMORY
    )


def test_document_and_self_contained_cues_override_memory_word_distractors() -> None:
    documented = choose_automatic_context_route(
        "Ignore earlier chat; use the checked-in policy for dependency approvals."
    )
    previous_value = choose_automatic_context_route("Write a cache that stores the previous value.")
    translation = choose_automatic_context_route(
        "Translate 'continue from where you stopped' into French."
    )

    assert documented.route is AutomaticContextRoute.KNOWLEDGE
    assert documented.reason is AutomaticContextRouteReason.EXPLICIT_KNOWLEDGE
    assert previous_value.route is AutomaticContextRoute.NONE
    assert translation.route is AutomaticContextRoute.NONE


def test_shadow_planner_can_request_structure_and_long_term_under_one_ceiling() -> None:
    prompt = (
        "Use the architecture decision from our previous session and show which modules depend "
        "on the router."
    )

    live = choose_automatic_context_route(prompt)
    shadow = plan_automatic_context_needs(prompt)

    assert live.route is AutomaticContextRoute.PRIOR_MEMORY
    assert shadow.structural_need is AutomaticContextNeed.YES
    assert shadow.long_term_need is AutomaticContextNeed.YES
    assert shadow.structural_tokens == 600
    assert shadow.long_term_tokens == 700
    assert shadow.structural_tokens + shadow.long_term_tokens == shadow.shared_maximum_tokens


def test_learned_phrase_affects_only_one_shadow_axis_and_cannot_suppress_memory() -> None:
    phrase = LearnedRoutePhrase("reconcile the ledger", CompactMemoryRoute.STRUCTURE)

    live = choose_automatic_context_route("Please reconcile the ledger for this request.")
    shadow = plan_automatic_context_needs(
        "Please reconcile the ledger for this request.", learned_phrases=(phrase,)
    )

    assert live.reason is AutomaticContextRouteReason.ROUTER_UNCERTAIN
    assert shadow.structural_need is AutomaticContextNeed.YES
    assert shadow.long_term_need is AutomaticContextNeed.UNKNOWN
    assert shadow.reason == "learned_phrase"
    with pytest.raises(ValueError, match="cannot suppress"):
        LearnedRoutePhrase("skip all memory", CompactMemoryRoute.NONE)


class _NoneSemanticRouter:
    def classify(self, prompt: str) -> CompactMemoryRouteDecision:
        return CompactMemoryRouteDecision(CompactMemoryRoute.NONE, 0.9, 0.8)


def test_semantic_none_proposal_does_not_turn_unknown_into_no() -> None:
    shadow = plan_automatic_context_needs(
        "finance reconciliation variance", semantic_router=_NoneSemanticRouter()
    )

    assert shadow.semantic_invoked is True
    assert shadow.semantic_route is CompactMemoryRoute.NONE
    assert shadow.structural_need is AutomaticContextNeed.UNKNOWN
    assert shadow.long_term_need is AutomaticContextNeed.UNKNOWN
    assert shadow.structural_tokens == shadow.long_term_tokens == 0
