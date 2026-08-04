"""Deterministic, scope-first discovery of retained source identities."""

from __future__ import annotations

from pathlib import Path

import pytest

from mnemo_memory.packages.domain import (
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.project_index import SourceStructureParser, SourceStructureParseRequest
from mnemo_memory.packages.storage import (
    ReferenceSourceStructureRepository,
    SQLiteSourceStructureRepository,
)


def _scope() -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_source_discovery_ranks_exact_prefix_and_all_token_matches(
    tmp_path: Path, adapter: str
) -> None:
    root = tmp_path / "repository"
    (root / "billing").mkdir(parents=True)
    (root / "archive").mkdir()
    (root / "billing" / "reconcile_orders.py").write_text(
        "def reconcile_orders():\n    return 1\n", encoding="utf-8"
    )
    (root / "archive" / "orders_reconcile.py").write_text(
        "def orders_reconcile():\n    return 1\n", encoding="utf-8"
    )
    artifact = SourceStructureParser(languages=frozenset({"python"})).parse(
        SourceStructureParseRequest(_scope(), root)
    )
    repository = (
        ReferenceSourceStructureRepository()
        if adapter == "reference"
        else SQLiteSourceStructureRepository(tmp_path / "memory" / "mnemo.sqlite3")
    )
    if adapter == "sqlite":
        repository.migrate()  # type: ignore[union-attr]
    repository.store_and_activate(artifact)

    exact = repository.find_symbols(
        _scope(), artifact.snapshot.snapshot_id, "billing.reconcile_orders", limit=10
    )
    discovery = repository.find_symbols(
        _scope(), artifact.snapshot.snapshot_id, "reconcile orders", limit=10
    )

    assert exact[0].qualified_name == "billing.reconcile_orders"
    assert [item.qualified_name for item in discovery] == [
        "billing.reconcile_orders",
        "billing.reconcile_orders.reconcile_orders",
        "archive.orders_reconcile",
        "archive.orders_reconcile.orders_reconcile",
    ]


@pytest.mark.parametrize("adapter", ["reference", "sqlite"])
def test_source_discovery_rejects_punctuation_only_query_without_broad_results(
    tmp_path: Path, adapter: str
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    (root / "orders.py").write_text("def reconcile():\n    return 1\n", encoding="utf-8")
    artifact = SourceStructureParser(languages=frozenset({"python"})).parse(
        SourceStructureParseRequest(_scope(), root)
    )
    repository = (
        ReferenceSourceStructureRepository()
        if adapter == "reference"
        else SQLiteSourceStructureRepository(tmp_path / "memory" / "mnemo.sqlite3")
    )
    if adapter == "sqlite":
        repository.migrate()  # type: ignore[union-attr]
    repository.store_and_activate(artifact)

    assert repository.find_symbols(_scope(), artifact.snapshot.snapshot_id, "////", limit=10) == ()
