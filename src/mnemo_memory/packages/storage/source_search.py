"""Shared deterministic matching for bounded static source discovery.

This is deliberately lexical, not semantic: it ranks only retained symbol and relative-path
identities. Source bodies, comments, docstrings, embeddings, and model calls are out of scope.
"""

from __future__ import annotations

import re

from mnemo_memory.packages.domain import CodeSymbol

_TOKEN_PATTERN = re.compile(r"[^\W_]+", flags=re.UNICODE)


def source_search_terms(query: str) -> tuple[str, ...]:
    """Return bounded, deterministic literal tokens without treating punctuation as a wildcard."""
    normalized = query.casefold().strip()
    if not normalized:
        return ()
    values: list[str] = []
    for token in _TOKEN_PATTERN.findall(normalized.replace("_", " ")):
        if token not in values:
            values.append(token)
        if len(values) == 8:
            break
    return tuple(values)


def source_symbol_matches(symbol: CodeSymbol, terms: tuple[str, ...]) -> bool:
    """Require every literal term to occur in a retained source identity."""
    if not terms:
        return False
    values = (symbol.qualified_name.casefold(), symbol.relative_path.casefold())
    return all(any(term in value for value in values) for term in terms)


def source_symbol_rank(
    symbol: CodeSymbol, query: str, terms: tuple[str, ...]
) -> tuple[object, ...]:
    """Rank exact identities before prefixes, then deterministic all-token matches."""
    normalized = query.casefold().strip()
    qualified_name = symbol.qualified_name.casefold()
    relative_path = symbol.relative_path.casefold()
    final_name = qualified_name.rsplit(".", maxsplit=1)[-1]
    filename = relative_path.rsplit("/", maxsplit=1)[-1].rsplit(".", maxsplit=1)[0]
    if normalized in {qualified_name, relative_path}:
        tier = 0
    elif normalized in {final_name, filename}:
        tier = 1
    elif qualified_name.startswith(normalized) or relative_path.startswith(normalized):
        tier = 2
    else:
        tier = 3
    positions = tuple(
        min(
            (value.find(term) for value in (qualified_name, relative_path) if term in value),
            default=1024,
        )
        for term in terms
    )
    return (tier, positions, relative_path, symbol.line, qualified_name, str(symbol.symbol_id))
