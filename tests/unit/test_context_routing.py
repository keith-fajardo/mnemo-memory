"""Deterministic, cost-aware automatic context routing."""

from mnemo_memory.packages.application.context_routing import (
    AutomaticContextRoute,
    AutomaticContextRouteReason,
    choose_automatic_context_route,
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
    assert general.reason is AutomaticContextRouteReason.GENERAL_MEMORY_PROBE
