# Structural Lookup — Gap-Closing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the four real gaps in mnemo's already-built source-structure index: precise `callers`/`imports`/`contains` lookups, a skip-when-unchanged capture path, the multi-language CHECK-constraint bug, and a discoverable lookup tool.

**Architecture:** The store, capture, and diff layers already exist (`SQLiteSourceStructureRepository`, migrations `0004/0005/0008/0009`, `source_observation.py`). This plan is **additive**: one new application service (`StructuralLookupService`) built only on existing repository read methods; one new MCP tool (`structural_lookup`); a file-based scan cache that short-circuits full re-parses; and a schema migration that widens two CHECK constraints. No existing storage contract changes.

**Tech Stack:** Python 3.12, pytest 8.4.2, SQLite (via `packages/storage/sqlite.py`), FastMCP (`mcp.server.fastmcp`), pydantic `Field`/`Annotated`.

**Spec:** `docs/superpowers/specs/2026-08-25-structural-knowledge-map-design.md` (see its "Reality check" section — the plan implements the four gaps listed there).

## Global Constraints

- **No LLM in the lookup path.** Lookups and capture are deterministic (AST/tree-sitter parse + SQLite reads). No frontier or local model calls.
- **Privacy:** never store or return source bytes, comments, docstrings, or absolute paths. Only relative paths, symbol names, kinds, line numbers, and sha256 digests — matching the existing `source_structure_*` tables.
- **Fail-open:** capture and lookup must never turn a successful checkpoint save into a client-visible failure (mirror `source_observation.py`'s `except Exception: return None/False`).
- **Scope:** source-structure data is PROJECT-scoped. Use `MemoryScope(owner, ScopeLevel.PROJECT, Visibility.PROJECT, workspace, project)`.
- **Run tests** from repo root with `uv run pytest` (or `pytest`). Test files live under `tests/unit/`, named `test_*.py`.
- **Test scope construction** (verbatim helper used across tasks; UUIDs are literal 36-char strings):
  ```python
  from mnemo_memory.packages.domain import (
      MemoryScope, ScopeLevel, Visibility, OwnerId, WorkspaceId, ProjectId,
  )

  def project_scope() -> MemoryScope:
      return MemoryScope(
          OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
          ScopeLevel.PROJECT,
          Visibility.PROJECT,
          WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
          ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
      )
  ```
- **Building a snapshot in a test** (verbatim pattern from `tests/unit/test_source_structure_storage.py`):
  ```python
  from mnemo_memory.packages.project_index import (
      SourceStructureParser, SourceStructureParseRequest,
  )
  artifact = SourceStructureParser().parse(
      SourceStructureParseRequest(project_scope(), root.resolve())
  )
  ```

---

## Part A — Fix the multi-language CHECK-constraint bug

**Why:** The wired parser is multi-language, but migration `0004` CHECK constraints reject `kind='package'` (Go) and `edge_type='package_dependency'` (Rust). Storing such a snapshot raises a CHECK violation that is silently swallowed, so Go/Rust repos never index. SQLite cannot `ALTER` a CHECK, so we rebuild the two affected tables. The rebuild is FK-safe with `foreign_keys=ON` by dropping the child (`edges`) before the parent (`symbols`) and repopulating edges from a constraint-free backup.

### Task A1: Failing regression test — a package symbol/edge must persist

**Files:**
- Test: `tests/unit/test_source_structure_multilang_storage.py` (create)

**Interfaces:**
- Consumes: `SQLiteSourceStructureRepository(path)`, `.migrate()`, `.store_and_activate(artifact)`, `.get_active_snapshot(scope)`, `.iter_symbols(scope, snapshot_id)`, `.iter_edges(scope, snapshot_id)` (all existing).
- Produces: nothing (regression test only).

- [ ] **Step 1: Write the failing test**

Build a tiny Go module so the parser emits a `CodeSymbolKind.PACKAGE` symbol (a `go.mod` triggers `_go_package_symbols`), then assert it persists and reads back.

```python
from pathlib import Path

from mnemo_memory.packages.domain import CodeSymbolKind
from mnemo_memory.packages.project_index import (
    SourceStructureParser, SourceStructureParseRequest,
)
from mnemo_memory.packages.storage.sqlite import SQLiteSourceStructureRepository
# plus the project_scope() helper from Global Constraints (copy into this file)

def test_go_package_symbol_persists(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    (root).mkdir()
    (root / "go.mod").write_text("module example.com/demo\n\ngo 1.21\n")
    (root / "main.go").write_text(
        "package main\n\nfunc main() {\n\tprintln(\"hi\")\n}\n"
    )
    artifact = SourceStructureParser().parse(
        SourceStructureParseRequest(project_scope(), root.resolve())
    )
    assert any(s.kind is CodeSymbolKind.PACKAGE for s in artifact.symbols), (
        "fixture must exercise the package kind"
    )

    repo = SQLiteSourceStructureRepository(tmp_path / "memory" / "mnemo.sqlite3")
    repo.migrate()
    result = repo.store_and_activate(artifact)  # currently raises/silently fails

    active = repo.get_active_snapshot(project_scope())
    assert active is not None
    assert active.snapshot_id == result.snapshot.snapshot_id
    kinds = {s.kind for s in repo.iter_symbols(project_scope(), active.snapshot_id)}
    assert CodeSymbolKind.PACKAGE in kinds
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_source_structure_multilang_storage.py -v`
Expected: FAIL — a `SourceIndexStorageFailure`/`sqlite3.IntegrityError` (CHECK constraint `kind`) or a mismatch, proving the bug.

> If the fixture does NOT produce a `PACKAGE` symbol (parser behavior differs), first inspect what a Go/Rust fixture emits: `python -c "..."` printing `[(s.qualified_name, s.kind) for s in artifact.symbols]`, and adjust the fixture until `assert any(... PACKAGE ...)` holds before proceeding. Do not weaken the assertion.

- [ ] **Step 3: (no implementation yet — A2 provides the fix)**

Leave the test failing; A2's migration makes it pass. Commit the failing test together with A2 (they form one reviewable unit).

### Task A2: Migration 0032 — widen the CHECK constraints (FK-safe rebuild)

**Files:**
- Create: `src/mnemo_memory/resources/migrations/0032_source_structure_multilang_kinds.sql`
- Modify: `src/mnemo_memory/packages/storage/sqlite.py` (`LATEST_SCHEMA_VERSION` at line 252; migration ladder — append after the `if version < 31:` block ending at line 769)

**Interfaces:**
- Consumes: existing `_execute_sql_script`, `_migration_text`, `_timestamp` helpers and the ladder pattern.
- Produces: schema version 32 with widened `kind` and `edge_type` CHECK sets.

- [ ] **Step 1: Confirm the migration transaction boundary**

Read `_execute_sql_script` and the `migrate()` body around lines 356–769 in `sqlite.py`. Confirm each migration script runs inside a transaction with `foreign_keys=ON` (set by `_connect`, line 328). The rebuild below is written to be correct under `foreign_keys=ON` **without** toggling the pragma (drops child before parent; repopulates edges last from a constraint-free backup). If — and only if — you find migrations run with `foreign_keys=OFF`, the same SQL still works.

- [ ] **Step 2: Write the migration SQL**

Create `0032_source_structure_multilang_kinds.sql`. It rebuilds `source_structure_symbols` (adds `'package'`) and `source_structure_edges` (adds `'package_dependency'`), preserving all rows, PK/UNIQUE constraints, FKs, and indexes.

```sql
-- Widen source-structure CHECK constraints so multi-language snapshots
-- (Go package symbols, Rust/Cargo package-dependency edges) persist.
-- SQLite cannot ALTER a CHECK, so rebuild both tables. FK-safe under
-- foreign_keys=ON: the child (edges) is dropped before the parent (symbols),
-- and edges are repopulated last from a constraint-free backup.

-- 1. Back up edges into a constraint-free scratch table (survives the drop).
CREATE TABLE _source_structure_edges_backup AS
    SELECT * FROM source_structure_edges;

-- 2. Rebuild symbols with the widened kind set.
CREATE TABLE source_structure_symbols_v2 (
    snapshot_id TEXT NOT NULL REFERENCES source_structure_snapshots(snapshot_id) ON DELETE RESTRICT,
    symbol_id TEXT NOT NULL,
    relative_path TEXT NOT NULL CHECK (substr(relative_path, 1, 1) != '/'),
    qualified_name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN (
        'module', 'package', 'class', 'interface', 'struct',
        'enum', 'trait', 'function', 'async_function'
    )),
    line_number INTEGER NOT NULL CHECK (line_number >= 1),
    PRIMARY KEY (snapshot_id, symbol_id),
    UNIQUE (snapshot_id, relative_path, qualified_name, kind, line_number)
);
INSERT INTO source_structure_symbols_v2 SELECT * FROM source_structure_symbols;

-- 3. Drop the child (edges) then the parent (symbols); safe with FK on.
DROP TABLE source_structure_edges;
DROP TABLE source_structure_symbols;

-- 4. Promote the rebuilt symbols table.
ALTER TABLE source_structure_symbols_v2 RENAME TO source_structure_symbols;

-- 5. Recreate edges with the widened edge_type set.
CREATE TABLE source_structure_edges (
    snapshot_id TEXT NOT NULL REFERENCES source_structure_snapshots(snapshot_id) ON DELETE RESTRICT,
    source_symbol_id TEXT NOT NULL,
    target TEXT NOT NULL,
    edge_type TEXT NOT NULL CHECK (edge_type IN (
        'imports', 'calls', 'defines', 'package_dependency'
    )),
    target_symbol_id TEXT NULL,
    PRIMARY KEY (snapshot_id, source_symbol_id, target, edge_type),
    FOREIGN KEY (snapshot_id, source_symbol_id)
        REFERENCES source_structure_symbols(snapshot_id, symbol_id) ON DELETE RESTRICT,
    FOREIGN KEY (snapshot_id, target_symbol_id)
        REFERENCES source_structure_symbols(snapshot_id, symbol_id) ON DELETE RESTRICT
);
INSERT INTO source_structure_edges SELECT * FROM _source_structure_edges_backup;
DROP TABLE _source_structure_edges_backup;

-- 6. Recreate the indexes that lived on the rebuilt tables.
CREATE INDEX source_structure_symbol_lookup_idx
    ON source_structure_symbols(snapshot_id, qualified_name, relative_path);
CREATE INDEX source_structure_edge_source_idx
    ON source_structure_edges(snapshot_id, source_symbol_id, target);
CREATE INDEX source_structure_edge_target_idx
    ON source_structure_edges(snapshot_id, target_symbol_id)
    WHERE target_symbol_id IS NOT NULL;
```

- [ ] **Step 3: Wire the migration into the ladder**

In `sqlite.py`, bump `LATEST_SCHEMA_VERSION = 31` → `32` (line 252), and append this block immediately after the `version = 31` line (after line 769), matching the existing pattern exactly:

```python
            if version < 32:
                _execute_sql_script(
                    connection,
                    _migration_text("0032_source_structure_multilang_kinds.sql"),
                )
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (32, ?)",
                    (_timestamp(),),
                )
                if fail_after_version == 32:
                    raise SQLiteMigrationError("injected migration failure")
                version = 32
```

- [ ] **Step 4: Run the A1 regression test — expect PASS**

Run: `uv run pytest tests/unit/test_source_structure_multilang_storage.py -v`
Expected: PASS.

- [ ] **Step 5: Run the existing source-structure + migration suites**

Run: `uv run pytest tests/unit/test_source_structure_storage.py tests/unit/test_python_source_structure.py -v`
Expected: PASS (no regression; the rebuild preserved data and constraints).

- [ ] **Step 6: Add a migration idempotency/data-preservation test**

Append to `tests/unit/test_source_structure_multilang_storage.py`: store a **Python** snapshot on a schema that has run through 0032, then re-store, and assert symbol/edge rows round-trip unchanged (guards the rebuild didn't drop data for the common path).

```python
def test_python_snapshot_survives_multilang_migration(tmp_path: Path) -> None:
    root = tmp_path / "py"
    root.mkdir()
    (root / "m.py").write_text("def a():\n    return b()\n\ndef b():\n    return 1\n")
    artifact = SourceStructureParser().parse(
        SourceStructureParseRequest(project_scope(), root.resolve())
    )
    repo = SQLiteSourceStructureRepository(tmp_path / "memory" / "mnemo.sqlite3")
    repo.migrate()
    repo.store_and_activate(artifact)
    active = repo.get_active_snapshot(project_scope())
    assert active is not None
    names = {s.qualified_name for s in repo.iter_symbols(project_scope(), active.snapshot_id)}
    assert {"a", "b"} <= {n.split(".")[-1] for n in names}
```

Run: `uv run pytest tests/unit/test_source_structure_multilang_storage.py -v` → PASS.

- [ ] **Step 7: Commit**

```bash
git add src/mnemo_memory/resources/migrations/0032_source_structure_multilang_kinds.sql \
        src/mnemo_memory/packages/storage/sqlite.py \
        tests/unit/test_source_structure_multilang_storage.py
git commit -m "fix(source-index): widen CHECK constraints for multi-language symbols/edges (migration 0032)"
```

---

## Part B — `StructuralLookupService` (precise callers / imports / contains)

**Why:** `define` works, but `callers`/`imports` are only an edge-kind-conflated blast-radius traversal and `contains` is not a first-class query. This service answers all four precisely. It is built **only** on existing contract read methods — `get_active_snapshot`, `iter_symbols`, `iter_edges` — so no storage contract or reference-repo changes are needed.

**Note on efficiency:** `iter_edges` loads all edges for the active snapshot (≤200k). That is correctness-first and still vastly cheaper than an Explore dispatch; SQL pushdown is a documented later optimization, not part of this plan.

### Task B1: The result value objects and service skeleton

**Files:**
- Create: `src/mnemo_memory/packages/application/structural_lookup.py`
- Test: `tests/unit/test_structural_lookup_service.py` (create)

**Interfaces:**
- Consumes: `SourceStructureRepository` contract methods `get_active_snapshot(scope) -> CodeSnapshot | None`, `iter_symbols(scope, snapshot_id) -> tuple[CodeSymbol, ...]`, `iter_edges(scope, snapshot_id) -> tuple[CodeEdge, ...]`. `CodeSymbol` fields: `relative_path`, `qualified_name`, `kind` (`CodeSymbolKind`), `line`, `symbol_id`. `CodeEdge` fields: `source_symbol_id`, `target` (str), `kind` (`CodeEdgeKind`), `target_symbol_id` (`CodeSymbolId | None`).
- Produces: `StructuralLookupService.lookup(scope, *, kind, target, limit=50) -> StructuralLookupResult`; `StructuralHit(relative_path, qualified_name, kind, line)`; `StructuralLookupResult(kind, query, hits, snapshot_id, truncated)`; `StructuralLookupKind` = one of `"define" | "callers" | "imports" | "contains"`.

- [ ] **Step 1: Write the failing test for `define` and empty-index behavior**

```python
from pathlib import Path

from mnemo_memory.packages.application.structural_lookup import StructuralLookupService
from mnemo_memory.packages.project_index import (
    SourceStructureParser, SourceStructureParseRequest,
)
from mnemo_memory.packages.storage.sqlite import SQLiteSourceStructureRepository
# copy project_scope() helper from Global Constraints into this file

def _repo_with(root: Path, db: Path) -> SQLiteSourceStructureRepository:
    artifact = SourceStructureParser().parse(
        SourceStructureParseRequest(project_scope(), root.resolve())
    )
    repo = SQLiteSourceStructureRepository(db)
    repo.migrate()
    repo.store_and_activate(artifact)
    return repo

def test_define_locates_symbol(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    (root / "m.py").write_text("def target():\n    return 1\n")
    repo = _repo_with(root, tmp_path / "mem" / "mnemo.sqlite3")
    service = StructuralLookupService(repo)

    result = service.lookup(project_scope(), kind="define", target="target")

    assert result.kind == "define"
    assert result.snapshot_id is not None
    assert any(h.qualified_name.endswith("target") and h.relative_path == "m.py"
               for h in result.hits)

def test_lookup_on_empty_index_returns_empty(tmp_path: Path) -> None:
    repo = SQLiteSourceStructureRepository(tmp_path / "mem" / "mnemo.sqlite3")
    repo.migrate()
    service = StructuralLookupService(repo)
    result = service.lookup(project_scope(), kind="define", target="anything")
    assert result.hits == ()
    assert result.snapshot_id is None
```

- [ ] **Step 2: Run — verify it fails**

Run: `uv run pytest tests/unit/test_structural_lookup_service.py -v`
Expected: FAIL with `ModuleNotFoundError: structural_lookup`.

- [ ] **Step 3: Implement the value objects and `define`/empty path**

```python
"""Deterministic, model-free structural lookups over the active source snapshot.

Answers locate/navigate questions (define/callers/imports/contains) so an agent
does not dispatch a search agent to re-read the tree. Built only on existing
SourceStructureRepository read methods; no source bytes ever touched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mnemo_memory.packages.domain import (
    CodeEdgeKind, CodeSymbol, MemoryScope,
)
from mnemo_memory.packages.storage.contracts import SourceStructureRepository

StructuralLookupKind = Literal["define", "callers", "imports", "contains"]

_MAX_LIMIT = 200


@dataclass(frozen=True, slots=True)
class StructuralHit:
    relative_path: str
    qualified_name: str
    kind: str
    line: int


@dataclass(frozen=True, slots=True)
class StructuralLookupResult:
    kind: StructuralLookupKind
    query: str
    hits: tuple[StructuralHit, ...]
    snapshot_id: str | None
    truncated: bool


def _hit(symbol: CodeSymbol) -> StructuralHit:
    return StructuralHit(
        symbol.relative_path, symbol.qualified_name, symbol.kind.value, symbol.line
    )


def _matches_name(symbol: CodeSymbol, target: str) -> bool:
    name = symbol.qualified_name
    return name == target or name.rsplit(".", 1)[-1] == target


class StructuralLookupService:
    def __init__(self, source_repository: SourceStructureRepository) -> None:
        self._source = source_repository

    def lookup(
        self,
        scope: MemoryScope,
        *,
        kind: StructuralLookupKind,
        target: str,
        limit: int = 50,
    ) -> StructuralLookupResult:
        target = target.strip()
        bound = max(1, min(limit, _MAX_LIMIT))
        snapshot = self._source.get_active_snapshot(scope)
        if snapshot is None or not target:
            return StructuralLookupResult(kind, target, (), None, False)
        symbols = self._source.iter_symbols(scope, snapshot.snapshot_id)
        if kind == "define":
            found = [_hit(s) for s in symbols if _matches_name(s, target)]
        elif kind == "contains":
            found = [_hit(s) for s in symbols if s.relative_path == target]
        else:  # callers | imports — implemented in B2
            found = self._edge_lookup(scope, snapshot.snapshot_id, symbols, kind, target)
        return StructuralLookupResult(
            kind,
            target,
            tuple(found[:bound]),
            str(snapshot.snapshot_id),
            truncated=len(found) > bound,
        )

    def _edge_lookup(self, scope, snapshot_id, symbols, kind, target):  # B2 fills this in
        return []
```

- [ ] **Step 4: Run — verify PASS**

Run: `uv run pytest tests/unit/test_structural_lookup_service.py -v`
Expected: PASS (define + empty).

- [ ] **Step 5: Commit**

```bash
git add src/mnemo_memory/packages/application/structural_lookup.py \
        tests/unit/test_structural_lookup_service.py
git commit -m "feat(structural-lookup): service skeleton with define + empty-index handling"
```

### Task B2: `callers`, `imports`, and `contains`

**Files:**
- Modify: `src/mnemo_memory/packages/application/structural_lookup.py` (implement `_edge_lookup`)
- Test: `tests/unit/test_structural_lookup_service.py` (extend)

**Interfaces:**
- Consumes: `iter_edges(scope, snapshot_id) -> tuple[CodeEdge, ...]`, `CodeEdgeKind.CALLS`, `CodeEdgeKind.IMPORTS`.
- Produces: `callers`/`imports` return the **source** symbols of matching edges; `contains` (already done in B1) returns symbols whose `relative_path == target`.

- [ ] **Step 1: Write the failing tests**

```python
def test_callers_are_isolated_from_importers(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    (root / "m.py").write_text(
        "import os\n\n"
        "def caller():\n    return target()\n\n"
        "def target():\n    return 1\n\n"
        "def bystander():\n    return os.getpid()\n"
    )
    repo = _repo_with(root, tmp_path / "mem" / "mnemo.sqlite3")
    service = StructuralLookupService(repo)

    callers = service.lookup(project_scope(), kind="callers", target="target")
    caller_names = {h.qualified_name.rsplit(".", 1)[-1] for h in callers.hits}
    assert "caller" in caller_names
    assert "bystander" not in caller_names  # importer of os, not a caller of target

def test_imports_finds_importers(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    (root / "m.py").write_text("import os\n\ndef f():\n    return os.getpid()\n")
    repo = _repo_with(root, tmp_path / "mem" / "mnemo.sqlite3")
    service = StructuralLookupService(repo)
    result = service.lookup(project_scope(), kind="imports", target="os")
    assert result.hits, "expected at least one importer of os"

def test_contains_lists_file_symbols(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    (root / "m.py").write_text("def a():\n    return 1\n\ndef b():\n    return 2\n")
    repo = _repo_with(root, tmp_path / "mem" / "mnemo.sqlite3")
    service = StructuralLookupService(repo)
    result = service.lookup(project_scope(), kind="contains", target="m.py")
    names = {h.qualified_name.rsplit(".", 1)[-1] for h in result.hits}
    assert {"a", "b"} <= names
    assert all(h.relative_path == "m.py" for h in result.hits)
```

- [ ] **Step 2: Run — verify the callers/imports tests fail**

Run: `uv run pytest tests/unit/test_structural_lookup_service.py -v`
Expected: `test_contains_lists_file_symbols` PASSES (B1 handled contains); `callers`/`imports` FAIL (empty hits, `_edge_lookup` stub).

- [ ] **Step 3: Implement `_edge_lookup`**

Replace the stub in `structural_lookup.py`:

```python
    def _edge_lookup(self, scope, snapshot_id, symbols, kind, target):
        wanted = CodeEdgeKind.CALLS if kind == "callers" else CodeEdgeKind.IMPORTS
        by_id = {s.symbol_id: s for s in symbols}
        target_ids = {s.symbol_id for s in symbols if _matches_name(s, target)}
        edges = self._source.iter_edges(scope, snapshot_id)
        seen: set = set()
        out: list[StructuralHit] = []
        for edge in edges:
            if edge.kind is not wanted:
                continue
            hit = (
                edge.target_symbol_id is not None and edge.target_symbol_id in target_ids
            ) or edge.target == target or edge.target.rsplit(".", 1)[-1] == target
            if not hit:
                continue
            source = by_id.get(edge.source_symbol_id)
            if source is None or source.symbol_id in seen:
                continue
            seen.add(source.symbol_id)
            out.append(_hit(source))
        out.sort(key=lambda h: (h.relative_path, h.line, h.qualified_name))
        return out
```

- [ ] **Step 4: Run — verify all PASS**

Run: `uv run pytest tests/unit/test_structural_lookup_service.py -v`
Expected: PASS (define, contains, callers, imports, empty).

- [ ] **Step 5: Commit**

```bash
git add src/mnemo_memory/packages/application/structural_lookup.py \
        tests/unit/test_structural_lookup_service.py
git commit -m "feat(structural-lookup): edge-kind-precise callers and imports"
```

---

## Part C — Expose `structural_lookup` as a discoverable MCP tool

**Why:** The precise service is useless if the agent can't reach it. This adds a first-class MCP tool with a description that explicitly tells the agent to prefer it over dispatching a search/Explore agent (this also closes gap 4, discoverability).

> **Read-before-edit:** Part C wires into three verified locations. Read them first so the surrounding names match: `apps/mcp/server.py` — the `McpContextPort` protocol (imported ~line 54), an existing tool (`get_context` decorator line 293, function 309) for the exact `@server.tool(...)` + `Annotated[...]` shape, and `DurableMcpContextPort` construction (line ~1110); and `packages/application/mcp_durable.py` — the `DurableMcpContextPort` class and how it derives project scope from `binding.checkpoint_scope`.

### Task C1: Port method `structural_lookup` on `DurableMcpContextPort`

**Files:**
- Modify: `src/mnemo_memory/packages/application/mcp_durable.py` (add method + instantiate `StructuralLookupService`)
- Modify: `src/mnemo_memory/apps/mcp/server.py` (add `structural_lookup` to the `McpContextPort` protocol; pass the `source_structure_repository` into `DurableMcpContextPort` if not already available to it)
- Test: `tests/unit/test_mcp_durable_structural_lookup.py` (create)

**Interfaces:**
- Consumes: `StructuralLookupService.lookup(scope, kind=, target=, limit=)`; the port's existing project-scope derivation (mirror the internal `_project_scope`/`binding.checkpoint_scope` usage already in this class — reuse it, do not invent a new one).
- Produces: `DurableMcpContextPort.structural_lookup(request: Mapping[str, object]) -> dict[str, object]` returning `{"kind","query","snapshot_id","truncated","hits":[{"relative_path","qualified_name","kind","line"}...]}`.

- [ ] **Step 1: Write the failing test**

Construct a `DurableMcpContextPort` the way existing `mcp_durable` unit tests do (reuse their fixture/builder — read `tests/unit/` for the nearest example that builds this port with a real `SQLiteSourceStructureRepository`). Then:

```python
def test_structural_lookup_define_via_port(port_with_indexed_source) -> None:
    # fixture indexes a project containing def target(): ... and returns (port, scope-less request base)
    port, base = port_with_indexed_source
    result = port.structural_lookup({**base, "kind": "define", "target": "target", "limit": 10})
    assert result["kind"] == "define"
    assert any(h["qualified_name"].endswith("target") for h in result["hits"])
```

> If no existing fixture builds this port, add a module-level fixture in the test that mirrors the construction in `_build_local_mcp_context_session` but with a `tmp_path` DB and a small indexed project. Keep it in the test file.

- [ ] **Step 2: Run — verify it fails**

Run: `uv run pytest tests/unit/test_mcp_durable_structural_lookup.py -v`
Expected: FAIL — `AttributeError: 'DurableMcpContextPort' object has no attribute 'structural_lookup'`.

- [ ] **Step 3: Implement the port method**

In `mcp_durable.py`, instantiate the service in `__init__` from the source repository the port already holds (it receives `source_repository` via `UnifiedContextService`; if the port does not already keep a direct handle, add a constructor parameter `source_structure_repository` and thread it from `_build_local_mcp_context_session`, where `source_repository = runtime.source_structure_repository` already exists at server.py:1004). Then:

```python
    def structural_lookup(self, request: Mapping[str, object]) -> dict[str, object]:
        kind = str(request.get("kind", "")).strip()
        target = str(request.get("target", "")).strip()
        raw_limit = request.get("limit", 50)
        limit = raw_limit if isinstance(raw_limit, int) else 50
        if kind not in ("define", "callers", "imports", "contains"):
            return {"kind": kind, "query": target, "snapshot_id": None,
                    "truncated": False, "hits": []}
        scope = self._project_scope()  # reuse the existing project-scope derivation
        try:
            result = self._structural_lookup_service.lookup(
                scope, kind=kind, target=target, limit=limit
            )
        except Exception:  # fail-open, never leak storage details
            return {"kind": kind, "query": target, "snapshot_id": None,
                    "truncated": False, "hits": []}
        return {
            "kind": result.kind,
            "query": result.query,
            "snapshot_id": result.snapshot_id,
            "truncated": result.truncated,
            "hits": [
                {"relative_path": h.relative_path, "qualified_name": h.qualified_name,
                 "kind": h.kind, "line": h.line}
                for h in result.hits
            ],
        }
```

Add `structural_lookup(self, request: Mapping[str, object]) -> dict[str, object]` to the `McpContextPort` protocol in `server.py`.

- [ ] **Step 4: Run — verify PASS**

Run: `uv run pytest tests/unit/test_mcp_durable_structural_lookup.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo_memory/packages/application/mcp_durable.py \
        src/mnemo_memory/apps/mcp/server.py \
        tests/unit/test_mcp_durable_structural_lookup.py
git commit -m "feat(structural-lookup): DurableMcpContextPort.structural_lookup"
```

### Task C2: Register the `structural_lookup` MCP tool

**Files:**
- Modify: `src/mnemo_memory/apps/mcp/server.py` (add the `@server.tool` registration alongside the others, e.g. near `get_context`)

**Interfaces:**
- Consumes: `port.structural_lookup(payload)`.
- Produces: the MCP tool `structural_lookup`.

- [ ] **Step 1: Add the tool registration**

Mirror the `get_context` decorator/param style (`Annotated[..., Field(...)]`, `ToolAnnotations(readOnlyHint=True, ...)`). The description is deliberately discoverability-forward:

```python
    @server.tool(
        name="structural_lookup",
        description=(
            "Locate code in the current project from the maintained source-structure "
            "index — deterministic, no model call, a few tokens. PREFER THIS over "
            "dispatching a search/Explore subagent when you need to find where a symbol "
            "is defined, what calls it, what imports a module, or what a file contains. "
            "kind='define' (where is X defined), 'callers' (what calls X), "
            "'imports' (what imports module Z), 'contains' (symbols defined in file Y). "
            "target is a symbol name for define/callers, a module name for imports, or a "
            "project-relative file path for contains. Returns relative_path + line hits."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, openWorldHint=False
        ),
    )
    def structural_lookup(
        kind: Annotated[str, Field(pattern="^(define|callers|imports|contains)$")],
        target: Annotated[str, Field(min_length=1, max_length=512)],
        limit: Annotated[int, Field(default=50, ge=1, le=200)] = 50,
    ) -> dict[str, object]:
        return port.structural_lookup({"kind": kind, "target": target, "limit": limit})
```

- [ ] **Step 2: Contract/registration test**

If the repo has a tool-registration/contract test (check `tests/contract/`), add a case asserting `structural_lookup` is registered with `readOnlyHint=True`. Otherwise add a unit test that imports `create_server` with a fake port and asserts the tool name is present in the server's tool registry.

Run: `uv run pytest tests/contract -k structural_lookup -v` (or the unit equivalent)
Expected: PASS.

- [ ] **Step 3: Full suite sanity**

Run: `uv run pytest tests/unit tests/contract -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/mnemo_memory/apps/mcp/server.py tests/
git commit -m "feat(structural-lookup): register discoverable structural_lookup MCP tool"
```

---

## Part D — Skip full re-parse when the working tree is unchanged

**Why:** Every checkpoint-save / SessionStart / Stop re-parses the whole tree (read every file + AST/tree-sitter), even when nothing changed. A stat-only fingerprint (no file reads) lets us skip the expensive parse when the tree is identical to the last capture. The cache is a small sidecar file — no storage-contract change.

### Task D1: `working_tree_fingerprint` (stat-only, no file reads)

**Files:**
- Create: `src/mnemo_memory/connectors/automatic_memory/scan_fingerprint.py`
- Test: `tests/unit/test_scan_fingerprint.py` (create)

**Interfaces:**
- Produces: `working_tree_fingerprint(root: Path) -> str` (a `sha256:`-prefixed hex digest over sorted `(relative_path, size, mtime_ns)` tuples; skips symlinks and the same directories the parser skips: `.git`, `.venv`, `node_modules`, `__pycache__`, `build`, `dist`, `target`, caches).

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path
from mnemo_memory.connectors.automatic_memory.scan_fingerprint import working_tree_fingerprint

def test_fingerprint_stable_and_change_sensitive(tmp_path: Path) -> None:
    root = tmp_path / "p"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("x = 1\n")
    first = working_tree_fingerprint(root)
    assert first.startswith("sha256:")
    assert working_tree_fingerprint(root) == first          # stable
    (root / "pkg" / "b.py").write_text("y = 2\n")
    assert working_tree_fingerprint(root) != first          # new file changes it

def test_fingerprint_skips_noise_dirs(tmp_path: Path) -> None:
    root = tmp_path / "p"
    (root).mkdir()
    (root / "a.py").write_text("x = 1\n")
    base = working_tree_fingerprint(root)
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "junk.pyc").write_bytes(b"\x00\x01")
    assert working_tree_fingerprint(root) == base           # ignored dir
```

- [ ] **Step 2: Run — verify it fails**

Run: `uv run pytest tests/unit/test_scan_fingerprint.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
"""Stat-only working-tree fingerprint: detect 'nothing changed' without reading bytes."""

from __future__ import annotations

import hashlib
from pathlib import Path

_SKIP_DIRECTORIES = frozenset(
    {".git", ".venv", "node_modules", "__pycache__", "build", "dist", "target"}
)


def working_tree_fingerprint(root: Path) -> str:
    root = root.resolve()
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            continue
        rel = path.relative_to(root)
        if any(part in _SKIP_DIRECTORIES for part in rel.parts):
            continue
        if not path.is_file():
            continue
        stat = path.stat()
        digest.update(
            f"{rel.as_posix()}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode()
        )
    return f"sha256:{digest.hexdigest()}"
```

> Keep `_SKIP_DIRECTORIES` in sync with the parser's skip set. If the parser exposes its own set, import it instead of duplicating; a follow-up can unify them.

- [ ] **Step 4: Run — verify PASS**

Run: `uv run pytest tests/unit/test_scan_fingerprint.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo_memory/connectors/automatic_memory/scan_fingerprint.py \
        tests/unit/test_scan_fingerprint.py
git commit -m "feat(source-index): stat-only working-tree fingerprint"
```

### Task D2: Sidecar scan cache + skip in `refresh_registered_project_source`

**Files:**
- Modify: `src/mnemo_memory/connectors/automatic_memory/source_observation.py`
- Test: `tests/unit/test_source_observation_skip.py` (create)

**Interfaces:**
- Consumes: `working_tree_fingerprint(root)`, `SourceStructureRepository.get_active_snapshot(scope)`, `.store_and_activate(artifact)`.
- Produces: `refresh_registered_project_source(binding, source_repository, *, cache_dir=None, fingerprint=working_tree_fingerprint)` — skips the parse when the fingerprint matches the last capture AND an active snapshot exists; otherwise parses, stores, and rewrites the cache entry. Signature stays backward-compatible (new params keyword-only with defaults).

- [ ] **Step 1: Write the failing test (skip avoids re-parse)**

Use a spy parser to prove the parse is not called on the second run.

```python
from pathlib import Path
from mnemo_memory.connectors.automatic_memory import source_observation
from mnemo_memory.connectors.automatic_memory.source_observation import (
    refresh_registered_project_source,
)
# build a real binding + SQLiteSourceStructureRepository like other automatic_memory tests do

def test_second_refresh_skips_parse_when_unchanged(tmp_path, monkeypatch, binding_and_repo):
    binding, repo = binding_and_repo          # binding.project_root has one .py file
    cache_dir = tmp_path / "scan-cache"

    calls = {"n": 0}
    real_parse = source_observation.SourceStructureParser.parse
    def counting_parse(self, request):
        calls["n"] += 1
        return real_parse(self, request)
    monkeypatch.setattr(source_observation.SourceStructureParser, "parse", counting_parse)

    first = refresh_registered_project_source(binding, repo, cache_dir=cache_dir)
    second = refresh_registered_project_source(binding, repo, cache_dir=cache_dir)

    assert first is not None and second is not None
    assert second.snapshot_id == first.snapshot_id
    assert calls["n"] == 1                    # second run skipped the parse

def test_refresh_reparses_after_change(tmp_path, monkeypatch, binding_and_repo):
    binding, repo = binding_and_repo
    cache_dir = tmp_path / "scan-cache"
    refresh_registered_project_source(binding, repo, cache_dir=cache_dir)
    (binding.project_root / "added.py").write_text("z = 3\n")
    calls = {"n": 0}
    real_parse = source_observation.SourceStructureParser.parse
    monkeypatch.setattr(
        source_observation.SourceStructureParser, "parse",
        lambda self, r: (calls.__setitem__("n", calls["n"] + 1), real_parse(self, r))[1],
    )
    refresh_registered_project_source(binding, repo, cache_dir=cache_dir)
    assert calls["n"] == 1                     # change forced a re-parse
```

> If the existing suite already has a binding+repo fixture (see `tests/unit/test_project_index_sync_status.py` / automatic_memory tests), reuse it as `binding_and_repo`; otherwise add a local fixture building a `MemoryProjectBinding` with a `tmp_path` project root and a migrated `SQLiteSourceStructureRepository`.

- [ ] **Step 2: Run — verify it fails**

Run: `uv run pytest tests/unit/test_source_observation_skip.py -v`
Expected: FAIL — `cache_dir` param unknown / parse called twice.

- [ ] **Step 3: Implement the skip**

Rewrite `refresh_registered_project_source` (keep the fail-open contract):

```python
from pathlib import Path
from mnemo_memory.connectors.automatic_memory.scan_fingerprint import working_tree_fingerprint


def _cache_path(cache_dir: Path, binding: MemoryProjectBinding) -> Path:
    return cache_dir / f"{binding.scope.project_id}.txt"


def _read_cache(path: Path) -> tuple[str, str] | None:
    try:
        fingerprint, snapshot_id = path.read_text(encoding="utf-8").split("\n", 1)
    except (OSError, ValueError):
        return None
    return fingerprint.strip(), snapshot_id.strip()


def _write_cache(path: Path, fingerprint: str, snapshot_id: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{fingerprint}\n{snapshot_id}", encoding="utf-8")
    except OSError:
        pass  # cache is an optimization; never fail the refresh


def refresh_registered_project_source(
    binding: MemoryProjectBinding,
    source_repository: SourceStructureRepository,
    *,
    cache_dir: Path | None = None,
    fingerprint=working_tree_fingerprint,
) -> CodeSnapshot | None:
    """Refresh one registered project; skip the full parse when the tree is unchanged."""
    try:
        cache_file = _cache_path(cache_dir, binding) if cache_dir is not None else None
        current_fp = fingerprint(binding.project_root) if cache_file is not None else None
        if cache_file is not None:
            active = source_repository.get_active_snapshot(binding.scope)
            cached = _read_cache(cache_file)
            if (
                active is not None
                and cached is not None
                and cached[0] == current_fp
                and cached[1] == str(active.snapshot_id)
            ):
                return active  # nothing changed — skip parse + store
        snapshot = source_repository.store_and_activate(
            SourceStructureParser().parse(
                SourceStructureParseRequest(binding.scope, binding.project_root)
            )
        ).snapshot
        if cache_file is not None and current_fp is not None:
            _write_cache(cache_file, current_fp, str(snapshot.snapshot_id))
        return snapshot
    except Exception:
        return None
```

- [ ] **Step 4: Run — verify PASS**

Run: `uv run pytest tests/unit/test_source_observation_skip.py -v`
Expected: PASS.

- [ ] **Step 5: Thread `cache_dir` from the observer**

In `CheckpointSourceObserver.observe`, pass a `cache_dir` derived from the local data directory so production calls actually use the cache. Add a `cache_dir: Path | None = None` field to the frozen dataclass and forward it: `refresh_registered_project_source(binding, self.source_repository, cache_dir=self.cache_dir)`. At the construction site (`apps/mcp/server.py` where `CheckpointSourceObserver(...)` is built), pass `cache_dir=runtime.config.data_directory / "scan-cache"`. Also update `hook.py:_refresh_source_structure` (line ~500) and the two CLI call sites (`apps/cli/main.py:2075, 2969`) to pass the same `cache_dir` (or leave them cache-less — they still work, just without the optimization; prefer wiring the hook path since it fires most often).

Run: `uv run pytest tests/unit -k "source_observation or sync_status" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/mnemo_memory/connectors/automatic_memory/source_observation.py \
        src/mnemo_memory/apps/mcp/server.py \
        src/mnemo_memory/connectors/automatic_memory/hook.py \
        src/mnemo_memory/apps/cli/main.py \
        tests/unit/test_source_observation_skip.py
git commit -m "feat(source-index): skip full re-parse when working tree is unchanged"
```

---

## Final verification

- [ ] **Full suite:** `uv run pytest -q` — expected: all pass.
- [ ] **Type check (if configured):** run the project's mypy invocation (see `pyproject.toml`/CI); the recent commits show `mypy strict` is enforced — expected: clean.
- [ ] **Manual smoke (optional):** in a checkout, trigger a checkpoint save, then call the `structural_lookup` tool with `kind="callers", target="<a known function>"` and confirm hits return without an Explore dispatch.

---

## Self-review notes (author)

- **Spec coverage:** gap 1 → Parts B + C; gap 2 → Part D; gap 3 → Part A; gap 4 → Part C2 tool description. All four covered.
- **Independence:** Parts A and D are independent of B/C and of each other; B precedes C (C consumes the service). They may be merged as separate PRs in any order except C-after-B.
- **Contract stability:** no changes to `SourceStructureRepository` contract or reference repo — B/C/D use only existing read methods plus a sidecar file, minimizing blast radius.
- **Known residual risks flagged inline:** (a) migration transaction/FK semantics — A2 Step 1 verifies before relying; (b) `_edge_lookup` loads all edges — acceptable for correctness, SQL pushdown deferred; (c) `_SKIP_DIRECTORIES` duplicated — noted to unify with the parser's set.
