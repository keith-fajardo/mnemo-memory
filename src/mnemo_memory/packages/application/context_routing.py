"""Deterministic route selection for bounded automatic prompt context."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from math import exp, log

_PATH = re.compile(
    r"(?:^|\s)(?:[a-zA-Z0-9_.-]+/)*[a-zA-Z0-9_.-]+\.(?:py|ts|tsx|js|jsx|sql|md|yml|yaml|toml|json)(?:\s|$)"
)
_SYMBOL = re.compile(r"\b(?:[A-Z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*|[a-z][a-z0-9]+_[a-z0-9_]+)\b")
_ROUTER_TERM = re.compile(r"[a-z0-9]+")
_MAXIMUM_ROUTING_PROMPT_CHARACTERS = 512
_ROUTING_PROMPT_HEAD_CHARACTERS = 256
_ROUTING_PROMPT_TAIL_CHARACTERS = 255


def bounded_automatic_context_prompt(prompt: str) -> str:
    """Return one transient head/tail view suitable for every bounded routing consumer."""

    if not isinstance(prompt, str):
        raise ValueError("automatic context prompt is invalid")
    value = prompt.strip()
    if not value:
        raise ValueError("automatic context prompt is invalid")
    if len(value) <= _MAXIMUM_ROUTING_PROMPT_CHARACTERS:
        return value
    # User instructions commonly precede or follow pasted code and logs. Preserve both boundaries
    # without retaining, logging, or passing the full prompt into retrieval/model adapters.
    return (
        value[:_ROUTING_PROMPT_HEAD_CHARACTERS].rstrip()
        + "\n"
        + value[-_ROUTING_PROMPT_TAIL_CHARACTERS:].lstrip()
    )


def _router_features(prompt: str) -> frozenset[str]:
    terms = _ROUTER_TERM.findall(prompt.casefold())
    values = {f"u:{term}" for term in terms}
    values.update(f"b:{left}_{right}" for left, right in pairwise(terms))
    # Presence-only features prevent repeated text from multiplying evidence or confidence.
    return frozenset(values)


class CompactMemoryRoute(StrEnum):
    """One advisory route emitted by the embedded local classifier."""

    NONE = "none"
    PRIOR_MEMORY = "prior_memory"
    KNOWLEDGE = "knowledge"
    STRUCTURE = "structure"


_ROUTER_EXAMPLES: dict[CompactMemoryRoute, tuple[str, ...]] = {
    CompactMemoryRoute.PRIOR_MEMORY: (
        "pick up where we left off",
        "continue the work from before",
        "resume the task we were doing",
        "use what we decided before",
        "did we already try this",
        "what was the outcome of our attempt",
        "bring back the plan from another session",
        "continue from the saved handoff",
        "remind me why we chose this approach",
        "what failed when we did this last time",
        "recover the unfinished task",
        "carry on with the existing implementation",
        "follow the decision we made previously",
        "what did the agent discover before",
        "use the saved checkpoint",
        "take over the work that was in progress",
        "have we seen a similar failure in another task",
        "could work from an older task affect this change",
        "what conclusion did we reach",
        "where did our investigation stop",
        "apply the workaround that proved effective for us",
        "which design did the user choose",
    ),
    CompactMemoryRoute.KNOWLEDGE: (
        "what does the project documentation say about authentication",
        "why is this component designed this way",
        "find the documented policy for releases",
        "explain the repository convention",
        "what design rationale covers storage",
        "search the project notes for database setup",
        "is there a design note about caching",
        "which project procedure applies to this change",
        "what are the project requirements",
        "use the knowledge base to explain retention",
        "show the documented rationale",
        "check the project guidance",
        "consult our engineering standards",
        "look up the repository rules for deployment",
        "what do our checked in documents require",
        "find relevant information in the project knowledge",
    ),
    CompactMemoryRoute.STRUCTURE: (
        "show how these modules depend on each other",
        "trace every caller of this function",
        "which files are affected by this symbol",
        "map the relationships among these packages",
        "what is upstream of this model",
        "show the downstream consumers of this table",
        "how does this component connect to the codebase",
        "give me the repository structure",
        "which services call this method",
        "trace the source impact of this change",
        "show the dbt lineage for this model",
        "what tables feed this dbt model",
        "where is this class used",
        "map this code path across modules",
        "which modules participate in this flow",
        "show the dependency graph around this package",
    ),
    CompactMemoryRoute.NONE: (
        "explain how a python decorator works",
        "write a regular expression for an email address",
        "calculate the sum of these numbers",
        "translate this sentence",
        "draft a new function from the requirements below",
        "review the code shown in this prompt",
        "answer this self contained question",
        "summarize the text below",
        "improve this paragraph",
        "create a unit test for this function",
        "what is dependency injection",
        "compare breadth first and depth first search",
        "format this json",
        "solve this equation",
        "write a short poem",
        "explain the following error message",
    ),
}

_ROUTER_VOCABULARY = frozenset(
    feature
    for examples in _ROUTER_EXAMPLES.values()
    for example in examples
    for feature in _router_features(example)
)
_ROUTER_COUNTS = {
    route: Counter(feature for example in examples for feature in _router_features(example))
    for route, examples in _ROUTER_EXAMPLES.items()
}
_ROUTER_TOTALS = {route: sum(counts.values()) for route, counts in _ROUTER_COUNTS.items()}


@dataclass(frozen=True, slots=True)
class CompactMemoryRouteDecision:
    """Content-free result from the embedded classifier."""

    route: CompactMemoryRoute
    confidence: float
    margin: float


class CompactLocalMemoryRouter:
    """Tiny deterministic presence-based classifier for ambiguous context intent."""

    def classify(self, prompt: str) -> CompactMemoryRouteDecision:
        features = _router_features(bounded_automatic_context_prompt(prompt))
        scores = {route: _router_score(route, features) for route in CompactMemoryRoute}
        maximum = max(scores.values())
        weights = {route: exp(score - maximum) for route, score in scores.items()}
        denominator = sum(weights.values())
        probabilities = {route: weight / denominator for route, weight in weights.items()}
        ordered = sorted(probabilities.items(), key=lambda item: (-item[1], item[0].value))
        route, confidence = ordered[0]
        return CompactMemoryRouteDecision(route, confidence, confidence - ordered[1][1])


_COMPACT_MEMORY_ROUTER = CompactLocalMemoryRouter()


class AutomaticContextRoute(StrEnum):
    """One mutually exclusive automatic-context strategy."""

    NONE = "none"
    DIRECT_LOOKUP = "direct_lookup"
    LOCAL_DIAGNOSTICS = "local_diagnostics"
    PRIOR_MEMORY = "prior_memory"
    KNOWLEDGE = "knowledge"
    STRUCTURE = "structure"
    SKILL_DISCOVERY = "skill_discovery"


class AutomaticContextRouteReason(StrEnum):
    """Stable content-free explanation for a route decision."""

    TRIVIAL = "trivial"
    EXACT_SOURCE_LOOKUP = "exact_source_lookup"
    LOCAL_MNEMO_OPERATION = "local_mnemo_operation"
    PRIOR_MEMORY = "prior_memory"
    EXPLICIT_KNOWLEDGE = "explicit_knowledge"
    GENERAL_MEMORY_PROBE = "general_memory_probe"
    ARCHITECTURE = "architecture"
    SOURCE_IMPACT = "source_impact"
    DBT_STRUCTURE = "dbt_structure"
    SKILL_TRIGGER = "skill_trigger"
    ROUTER_PRIOR_MEMORY = "router_prior_memory"
    ROUTER_NO_MEMORY = "router_no_memory"
    ROUTER_KNOWLEDGE = "router_knowledge"
    ROUTER_STRUCTURE = "router_structure"
    ROUTER_UNCERTAIN = "router_uncertain"


_MAXIMUM_ROUTE_TOKENS = {
    AutomaticContextRoute.NONE: 0,
    AutomaticContextRoute.DIRECT_LOOKUP: 0,
    AutomaticContextRoute.LOCAL_DIAGNOSTICS: 256,
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

    prompt = bounded_automatic_context_prompt(prompt)
    if not isinstance(skill_candidate_count, int) or isinstance(skill_candidate_count, bool):
        raise TypeError("skill candidate count must be an integer")
    if not 0 <= skill_candidate_count <= 3:
        raise ValueError("skill candidate count is out of bounds")

    terms = frozenset(_ROUTER_TERM.findall(prompt.casefold()))
    if terms and terms <= {"hello", "hi", "hey", "thanks", "thank", "you"}:
        return _decision(AutomaticContextRoute.NONE, AutomaticContextRouteReason.TRIVIAL)

    if _is_local_mnemo_operation(terms):
        return _decision(
            AutomaticContextRoute.LOCAL_DIAGNOSTICS,
            AutomaticContextRouteReason.LOCAL_MNEMO_OPERATION,
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
    documented_terms = {
        "adr",
        "contract",
        "documented",
        "documentation",
        "docs",
        "guidance",
        "handbook",
        "notes",
        "policies",
        "policy",
        "standard",
        "standards",
    }
    if terms & documented_terms or {"threat", "model"} <= terms:
        return _decision(
            AutomaticContextRoute.KNOWLEDGE,
            AutomaticContextRouteReason.EXPLICIT_KNOWLEDGE,
        )

    prior_reference_terms = {
        "agent",
        "answer",
        "attempt",
        "chat",
        "choice",
        "conversation",
        "decision",
        "handoff",
        "investigation",
        "plan",
        "rationale",
        "result",
        "run",
        "session",
        "task",
        "work",
    }
    if "rename" in terms and terms & {"class", "function", "method", "symbol", "variable"}:
        return _decision(AutomaticContextRoute.NONE, AutomaticContextRouteReason.TRIVIAL)
    if (
        "recap" in terms
        or "remember" in terms
        or bool(terms & {"previous", "earlier"} and terms & prior_reference_terms)
        or bool("last" in terms and terms & {"attempt", "run", "session", "time"})
    ):
        return _decision(
            AutomaticContextRoute.PRIOR_MEMORY, AutomaticContextRouteReason.PRIOR_MEMORY
        )

    if terms & {"architecture", "components"} and terms & {
        "codebase",
        "repository",
        "repo",
        "source",
    }:
        return _decision(AutomaticContextRoute.STRUCTURE, AutomaticContextRouteReason.ARCHITECTURE)
    relationship_terms = terms & {
        "impact",
        "depends",
        "dependency",
        "dependencies",
        "downstream",
        "upstream",
        "callers",
    }
    source_terms = terms & {
        "class",
        "code",
        "codebase",
        "component",
        "file",
        "files",
        "function",
        "method",
        "model",
        "module",
        "package",
        "repo",
        "repository",
        "service",
        "source",
        "symbol",
        "table",
    }
    if relationship_terms and (source_terms or _SYMBOL.search(prompt) is not None):
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
    if terms & {"calculate", "translate"}:
        return _decision(
            AutomaticContextRoute.NONE,
            AutomaticContextRouteReason.TRIVIAL,
        )
    if terms & {"format", "rename", "solve", "summarize"} and terms & {
        "below",
        "equation",
        "following",
        "json",
        "paragraph",
        "sentence",
        "text",
        "this",
        "variable",
    }:
        return _decision(AutomaticContextRoute.NONE, AutomaticContextRouteReason.TRIVIAL)
    if terms & {"improve", "review"} and terms & {
        "below",
        "follows",
        "following",
        "paragraph",
        "snippet",
        "standalone",
        "text",
        "this",
    }:
        return _decision(AutomaticContextRoute.NONE, AutomaticContextRouteReason.TRIVIAL)
    if {"complete", "specification"} <= terms:
        return _decision(AutomaticContextRoute.NONE, AutomaticContextRouteReason.TRIVIAL)
    proposal = _COMPACT_MEMORY_ROUTER.classify(prompt)
    if proposal.route is CompactMemoryRoute.PRIOR_MEMORY and proposal.confidence >= 0.45:
        return _decision(
            AutomaticContextRoute.PRIOR_MEMORY,
            AutomaticContextRouteReason.ROUTER_PRIOR_MEMORY,
        )
    if (
        proposal.route is CompactMemoryRoute.NONE
        and proposal.confidence >= 0.78
        and proposal.margin >= 0.35
    ):
        return _decision(
            AutomaticContextRoute.NONE,
            AutomaticContextRouteReason.ROUTER_NO_MEMORY,
        )
    if proposal.route is CompactMemoryRoute.KNOWLEDGE and proposal.confidence >= 0.6:
        return _decision(
            AutomaticContextRoute.KNOWLEDGE,
            AutomaticContextRouteReason.ROUTER_KNOWLEDGE,
        )
    if proposal.route is CompactMemoryRoute.STRUCTURE and proposal.confidence >= 0.6:
        return _decision(
            AutomaticContextRoute.STRUCTURE,
            AutomaticContextRouteReason.ROUTER_STRUCTURE,
        )
    return _decision(
        AutomaticContextRoute.KNOWLEDGE,
        AutomaticContextRouteReason.ROUTER_UNCERTAIN,
    )


def _decision(
    route: AutomaticContextRoute, reason: AutomaticContextRouteReason
) -> AutomaticContextRouteDecision:
    return AutomaticContextRouteDecision(route, reason, _MAXIMUM_ROUTE_TOKENS[route])


def _router_score(route: CompactMemoryRoute, features: frozenset[str]) -> float:
    counts = _ROUTER_COUNTS[route]
    total = _ROUTER_TOTALS[route]
    vocabulary_size = len(_ROUTER_VOCABULARY)
    score = -log(len(CompactMemoryRoute))
    for feature in sorted(features):
        if feature in _ROUTER_VOCABULARY:
            score += log((counts[feature] + 1) / (total + vocabulary_size))
    return score


def _is_local_mnemo_operation(terms: frozenset[str]) -> bool:
    """Recognize only local runtime questions; generic memory topics remain normal queries."""

    operational = {
        "active",
        "config",
        "configuration",
        "current",
        "diagnose",
        "diagnostics",
        "hook",
        "hooks",
        "installed",
        "remember",
        "status",
        "timeout",
        "upgrade",
        "version",
    }
    if "mnemo" in terms and terms & operational:
        return True
    if "mnemo" in terms and "using" in terms and terms & {"what", "which", "right"}:
        return True
    lifecycle = terms & {"precompact", "sessionstart"}
    failure = terms & {"failed", "failure", "hook", "hooks", "timeout", "timed"}
    return bool(lifecycle and failure)
