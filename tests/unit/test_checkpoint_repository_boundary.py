from __future__ import annotations

import inspect

from packages.storage import CheckpointRepository


def test_canonical_checkpoint_port_excludes_legacy_replacement_chain_operations() -> None:
    legacy_methods = {
        "create_checkpoint",
        "get_checkpoint",
        "get_current_checkpoint",
        "list_checkpoint_history",
        "supersede",
        "create_aggregate",
    }
    assert not legacy_methods.intersection(CheckpointRepository.__dict__)


def test_canonical_checkpoint_port_requires_scope_for_reads_and_mutations() -> None:
    scoped_methods = {
        "get_aggregate",
        "get_current_revision",
        "get_revision",
        "append_revision",
        "complete_checkpoint",
        "abandon_checkpoint",
        "list_current_checkpoints",
        "select_current_checkpoint",
    }
    for method_name in scoped_methods:
        assert "scope" in inspect.signature(getattr(CheckpointRepository, method_name)).parameters
