"""Deterministic route selection for bounded automatic prompt context."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

_TERMS = re.compile(r"[a-z0-9_./:-]+")
_PATH = re.compile(
    r"(?:^|\s)(?:[a-zA-Z0-9_.-]+/)*[a-zA-Z0-9_.-]+\.(?:py|ts|tsx|js|jsx|sql|md|yml|yaml|toml|json)(?:\s|$)"
)
_SYMBOL = re.compile(r"\b(?:[A-Z][A-Za-z0-9]{3,}|[a-z][a-z0-9]+_[a-z0-9_]+)\b")


class AutomaticContextRoute(StrEnum):
    """One mutually exclusive automatic-context strategy."""

    NONE = "none"
    DIRECT_LOOKUP = "direct_lookup"
    PRIOR_MEMORY = "prior_memory"
    KNOWLEDGE = "knowledge"
    STRUCTURE = "structure"
    SKILL_DISCOVERY = "skill_discovery"


class AutomaticContextRouteReason(StrEnum):
    """Stable content-free explanation for a route decision."""

    TRIVIAL = "trivial"
    EXACT_SOURCE_LOOKUP = "exact_source_lookup"
    PRIOR_MEMORY = "prior_memory"
    EXPLICIT_KNOWLEDGE = "explicit_knowledge"
    GENERAL_MEMORY_PROBE = "general_memory_probe"
    ARCHITECTURE = "architecture"
    SOURCE_IMPACT = "source_impact"
    DBT_STRUCTURE = "dbt_structure"
    SKILL_TRIGGER = "skill_trigger"


_MAXIMUM_ROUTE_TOKENS = {
    AutomaticContextRoute.NONE: 0,
    AutomaticContextRoute.DIRECT_LOOKUP: 0,
    AutomaticContextRoute.PRIOR_MEMORY: 1_300,
    AutomaticContextRoute.KNOWLEDGE: 1_300,
    AutomaticContextRoute.STRUCTURE: 1_000,
    AutomaticContextRoute.SKILL_DISCOVERY: 256,
}


@dataclass(frozen=True, slots=True)
class AutomaticContextRouteDecision:
    route: AutomaticContextRoute
    reason: AutomaticContextRouteReason
    maximum_attachment_tokens: int


def choose_automatic_context_route(
    prompt: str, *, skill_candidate_count: int = 0
) -> AutomaticContextRouteDecision:
    """Choose the smallest route from transient prompt shape and bounded candidate count."""

    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 512:
        raise ValueError("automatic context prompt is invalid")
    if not isinstance(skill_candidate_count, int) or isinstance(skill_candidate_count, bool):
        raise TypeError("skill candidate count must be an integer")
    if not 0 <= skill_candidate_count <= 3:
        raise ValueError("skill candidate count is out of bounds")

    terms = frozenset(_TERMS.findall(prompt.casefold()))
    if terms and terms <= {"hello", "hi", "hey", "thanks", "thank", "you"}:
        return _decision(AutomaticContextRoute.NONE, AutomaticContextRouteReason.TRIVIAL)

    if (
        "recap" in terms
        or terms & {"previous", "earlier", "remember"}
        or ("last" in terms and "session" in terms)
    ):
        return _decision(
            AutomaticContextRoute.PRIOR_MEMORY, AutomaticContextRouteReason.PRIOR_MEMORY
        )

    action_terms = terms & {"build", "create", "design", "implement", "plan", "change"}
    if skill_candidate_count and action_terms:
        return _decision(
            AutomaticContextRoute.SKILL_DISCOVERY,
            AutomaticContextRouteReason.SKILL_TRIGGER,
        )

    if "dbt" in terms and terms & {
        "lineage",
        "model",
        "models",
        "source",
        "sources",
        "test",
        "tests",
    }:
        return _decision(AutomaticContextRoute.STRUCTURE, AutomaticContextRouteReason.DBT_STRUCTURE)
    if terms & {"architecture", "components"} and terms & {
        "codebase",
        "repository",
        "repo",
        "source",
    }:
        return _decision(AutomaticContextRoute.STRUCTURE, AutomaticContextRouteReason.ARCHITECTURE)
    if terms & {
        "impact",
        "depends",
        "dependency",
        "dependencies",
        "downstream",
        "upstream",
        "callers",
    }:
        return _decision(AutomaticContextRoute.STRUCTURE, AutomaticContextRouteReason.SOURCE_IMPACT)

    exact_request = bool(terms & {"where", "find", "locate", "defined", "definition"})
    if exact_request and (_PATH.search(prompt) is not None or _SYMBOL.search(prompt) is not None):
        return _decision(
            AutomaticContextRoute.DIRECT_LOOKUP,
            AutomaticContextRouteReason.EXACT_SOURCE_LOOKUP,
        )
    if _PATH.search(prompt) is not None and terms & {"count", "many", "show", "read"}:
        return _decision(
            AutomaticContextRoute.DIRECT_LOOKUP,
            AutomaticContextRouteReason.EXACT_SOURCE_LOOKUP,
        )
    if terms & {"count", "many"} and terms & {"file", "files"}:
        return _decision(
            AutomaticContextRoute.DIRECT_LOOKUP,
            AutomaticContextRouteReason.EXACT_SOURCE_LOOKUP,
        )

    if skill_candidate_count:
        return _decision(
            AutomaticContextRoute.SKILL_DISCOVERY,
            AutomaticContextRouteReason.SKILL_TRIGGER,
        )
    if terms & {"adr", "decision", "documented", "documentation", "notes", "policy"}:
        return _decision(
            AutomaticContextRoute.KNOWLEDGE,
            AutomaticContextRouteReason.EXPLICIT_KNOWLEDGE,
        )
    return _decision(
        AutomaticContextRoute.KNOWLEDGE,
        AutomaticContextRouteReason.GENERAL_MEMORY_PROBE,
    )


def _decision(
    route: AutomaticContextRoute, reason: AutomaticContextRouteReason
) -> AutomaticContextRouteDecision:
    return AutomaticContextRouteDecision(route, reason, _MAXIMUM_ROUTE_TOKENS[route])
