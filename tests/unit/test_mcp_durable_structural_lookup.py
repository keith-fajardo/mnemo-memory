"""`DurableMcpContextPort.structural_lookup` over a real indexed source snapshot.

Builds the port directly over a `tmp_path` `SQLiteSourceStructureRepository` (mirroring
`test_structural_lookup_service.py`'s snapshot construction) so the port's project-scope
derivation, validation, and fail-open contract are exercised without any MCP transport,
model call, or full `CheckpointRuntime`.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from mnemo_memory.packages.application.checkpoints import CheckpointApplicationService
from mnemo_memory.packages.application.mcp_durable import DurableMcpContextPort
from mnemo_memory.packages.domain import (
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.project_index import (
    SourceStructureParser,
    SourceStructureParseRequest,
)
from mnemo_memory.packages.storage.sqlite import SQLiteSourceStructureRepository


def project_scope() -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )


def _indexed_repo(root: Path, db: Path) -> SQLiteSourceStructureRepository:
    artifact = SourceStructureParser().parse(
        SourceStructureParseRequest(project_scope(), root.resolve())
    )
    repo = SQLiteSourceStructureRepository(db)
    repo.migrate()
    repo.store_and_activate(artifact)
    return repo


def _port(repo: SQLiteSourceStructureRepository | None) -> DurableMcpContextPort:
    # The checkpoint service is unused by structural_lookup; a stand-in keeps the
    # construction faithful to production positional wiring without a full runtime.
    return DurableMcpContextPort(
        cast(CheckpointApplicationService, object()),
        default_scope=project_scope(),
        source_structure_repository=repo,
    )


@pytest.fixture
def port_with_indexed_source(
    tmp_path: Path,
) -> tuple[DurableMcpContextPort, dict[str, object]]:
    root = tmp_path / "p"
    root.mkdir()
    (root / "m.py").write_text(
        "import shared\n\ndef target():\n    return 1\n\ndef caller():\n    return target()\n"
    )
    repo = _indexed_repo(root, tmp_path / "mem" / "mnemo.sqlite3")
    return _port(repo), {}


def test_structural_lookup_define_via_port(
    port_with_indexed_source: tuple[DurableMcpContextPort, dict[str, object]],
) -> None:
    port, base = port_with_indexed_source
    result = port.structural_lookup({**base, "kind": "define", "target": "target", "limit": 10})
    assert result["kind"] == "define"
    assert result["query"] == "target"
    assert result["snapshot_id"] is not None
    hits = cast(list[dict[str, object]], result["hits"])
    assert any(str(hit["qualified_name"]).endswith("target") for hit in hits)
    assert all(hit["relative_path"] == "m.py" for hit in hits)


def test_structural_lookup_callers_via_port(
    port_with_indexed_source: tuple[DurableMcpContextPort, dict[str, object]],
) -> None:
    port, base = port_with_indexed_source
    result = port.structural_lookup({**base, "kind": "callers", "target": "target"})
    names = {
        str(hit["qualified_name"]).rsplit(".", 1)[-1]
        for hit in cast(list[dict[str, object]], result["hits"])
    }
    assert names == {"caller"}


def test_structural_lookup_imports_via_port(
    port_with_indexed_source: tuple[DurableMcpContextPort, dict[str, object]],
) -> None:
    port, base = port_with_indexed_source
    result = port.structural_lookup({**base, "kind": "imports", "target": "shared"})
    names = {
        str(hit["qualified_name"]).rsplit(".", 1)[-1]
        for hit in cast(list[dict[str, object]], result["hits"])
    }
    assert names == {"m"}


def test_structural_lookup_contains_via_port(
    port_with_indexed_source: tuple[DurableMcpContextPort, dict[str, object]],
) -> None:
    port, base = port_with_indexed_source
    result = port.structural_lookup({**base, "kind": "contains", "target": "m.py"})
    names = {
        str(hit["qualified_name"]).rsplit(".", 1)[-1]
        for hit in cast(list[dict[str, object]], result["hits"])
    }
    assert {"target", "caller"} <= names


def test_structural_lookup_rejects_unknown_kind(
    port_with_indexed_source: tuple[DurableMcpContextPort, dict[str, object]],
) -> None:
    port, base = port_with_indexed_source
    result = port.structural_lookup({**base, "kind": "grep", "target": "target"})
    assert result["kind"] == "grep"
    assert result["hits"] == []
    assert result["snapshot_id"] is None
    assert result["truncated"] is False


def test_structural_lookup_without_repository_is_fail_open() -> None:
    port = _port(None)
    result = port.structural_lookup({"kind": "define", "target": "target"})
    assert result["hits"] == []
    assert result["snapshot_id"] is None


def test_structural_lookup_missing_scope_is_fail_open(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    (root / "m.py").write_text("def target():\n    return 1\n")
    repo = _indexed_repo(root, tmp_path / "mem" / "mnemo.sqlite3")
    # No default scope and no scope identifiers in the request: scope derivation raises
    # internally and must be swallowed into an empty result, never a leaked error.
    port = DurableMcpContextPort(
        cast(CheckpointApplicationService, object()),
        source_structure_repository=repo,
    )
    result = port.structural_lookup({"kind": "define", "target": "target"})
    assert result["hits"] == []
    assert result["snapshot_id"] is None
