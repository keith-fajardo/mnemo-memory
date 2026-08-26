# dbt Structural Awareness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a discoverable `dbt_structure` MCP tool giving the agent lineage / impact / test-coverage / freshness / changes awareness for dbt projects, every answer stamped with manifest freshness — mirroring the shipped `structural_lookup` tool for code.

**Architecture:** Additive, over machinery that already exists. A new `DbtStructureService` (application layer) wraps the existing `DbtManifestApplicationService` query methods (`query`, `query_test_coverage`, `query_source_freshness`, `query_changes`) and node resolution (`resolve_file` / `iter_nodes`), returning a uniform result with currentness. A new `dbt_structure` MCP tool + `DurableMcpContextPort.dbt_structure` method forward to it, mirroring `structural_lookup`'s wiring (protocol entry, team-port fail-open, tool registration). No new query engine; mnemo never runs dbt.

**Tech Stack:** Python 3.12, pytest 8.4.2, FastMCP, pydantic `Annotated`/`Field`. dbt manifest schema v12.

**Spec:** `docs/superpowers/specs/2026-08-26-dbt-structural-awareness-design.md` (read its "Reality check" + "Non-goals").

## Global Constraints

- **No LLM in the path.** Traversal + manifest reads are deterministic. Only the agent's call + reading the compact answer cost tokens.
- **No auto-`dbt parse` / auto-ingest.** mnemo never runs dbt. The tool reads the already-active manifest snapshot; it surfaces staleness, it does not fix it.
- **Model/node-level only.** No column-level lineage.
- **Fail-open, never raise.** No active manifest, unresolved/ambiguous node, wrong project, or any error → the empty result shape with `currentness="unknown"` + a hint. Team/Postgres ports fail-open to empty (the Part C lesson).
- **Minimal tool schema.** No scope params on the tool; scope derives from the bound project via the existing `_project_scope(_scope(request, self._default_scope))` helpers.
- **Privacy.** Return only manifest-derived metadata (unique_id, name, resource_type, relative path, currentness, test/freshness status). Never source bytes or absolute paths.
- **Tests:** `uv run pytest` from repo root. `uv run mypy` is strict project-wide. **The release gate runs `ruff format --check` (not just `ruff check`) and the FULL suite incl. `tests/integration` + the eval scripts** — Part C exists because of this.
- **Test scope helper** (copy into new test files; UUIDs literal):
  ```python
  from mnemo_memory.packages.domain import (
      MemoryScope, ScopeLevel, Visibility, OwnerId, WorkspaceId, ProjectId,
  )
  def project_scope() -> MemoryScope:
      return MemoryScope(
          OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
          ScopeLevel.PROJECT, Visibility.PROJECT,
          WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
          ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
      )
  ```
- **dbt fixture + ingest pattern** (verbatim, from `tests/unit/test_dbt_application_service.py`):
  ```python
  from pathlib import Path
  from mnemo_memory.packages.application.dbt import DbtManifestApplicationService, IngestManifest
  from mnemo_memory.connectors.dbt.manifest import DbtManifestParser
  from mnemo_memory.packages.storage.reference import ReferenceProjectIndexRepository

  FIXTURE = Path(__file__).parents[1] / "fixtures" / "dbt" / "manifest-v12.json"
  def dbt_service_with_manifest():
      repo = ReferenceProjectIndexRepository()
      writer = DbtManifestApplicationService(repo, DbtManifestParser())
      writer.ingest(IngestManifest(project_scope(), FIXTURE.read_text()))  # confirm IngestManifest field order in dbt.py
      return DbtManifestApplicationService(repo)  # reader
  ```
  A known node in the fixture: `DbtNodeId("model.mnemo_analytics.fct_orders")`, file `models/marts/fct_orders.sql`.

---

## Part A — `DbtStructureService`

**Why:** One application service that turns `(kind, target)` into a uniform, currentness-stamped result by dispatching to the existing `DbtManifestApplicationService` query methods and resolving the target node. This is the dbt analog of `StructuralLookupService`. Additive — no changes to `DbtManifestApplicationService`, the repository contract, or the reference repo.

### Task A1: Result value objects + service skeleton + node resolution

**Files:**
- Create: `src/mnemo_memory/packages/application/dbt_structure.py`
- Test: `tests/unit/test_dbt_structure_service.py`

**Interfaces:**
- Consumes (all EXISTING on `DbtManifestApplicationService`, confirmed):
  - `query(QueryLineage) -> LineageQueryResult`
  - `query_test_coverage(QueryTestCoverage) -> TestCoverageQueryResult`
  - `query_source_freshness(QuerySourceFreshness) -> SourceFreshnessQueryResult`
  - `query_changes(QueryManifestChanges) -> ManifestChangesResult`
  - `resolve_file(ResolveManifestFile) -> ResolvedManifestFile` (path → node; raises `DbtApplicationAmbiguous`/`DbtApplicationNotFound`)
  - `iter_nodes(scope, snapshot_id) -> tuple[DbtManifestNode, ...]` (via the active snapshot; for name resolution)
  - `LineageDirection.UPSTREAM/DOWNSTREAM`, `DbtNodeId("model.pkg.name")`, `ArtifactCurrentness.UNKNOWN/CURRENT/STALE`.
  - `LineageQueryResult` fields: `nodes: tuple[LineageNodeResult{node,depth},...]`, `edges: tuple[DbtLineageEdge{parent_id,child_id,edge_type},...]`, `truncated`, `currentness`, `currentness_reason`, `start_node`. `DbtManifestNode` fields: `unique_id`, `name`, `resource_type`, `original_file_path`.
- Produces:
  - `DbtStructureKind = Literal["upstream","downstream","impact","test_coverage","freshness","changes"]`
  - `@dataclass DbtStructureResult(kind, query, resolved_unique_id: str|None, currentness: str, currentness_reason: str, items: tuple[dict,...], edges: tuple[dict,...], truncated: bool)`
  - `class DbtStructureService: __init__(self, dbt_service, *, current_source_state=None)`; `lookup(self, scope, *, kind, target="", depth=None) -> DbtStructureResult`

- [ ] **Step 1: Read the three non-lineage Query dataclasses + result types**

Before coding, read in `src/mnemo_memory/packages/application/dbt.py` the exact field lists of `QueryTestCoverage` (near line 646), `QuerySourceFreshness` (near 734), `QueryManifestChanges` (near 771), and their result types `TestCoverageQueryResult`, `SourceFreshnessQueryResult`, `ManifestChangesResult` — note each result's `currentness`/`currentness_reason` (or `manifest_currentness`) field name and its row collections. Also confirm `IngestManifest`'s constructor signature and `resolve_file`'s `ResolveManifestFile(scope, original_file_path, snapshot_id=None)`. Write the item-mapping (Step 3) against the real fields. If a result lacks a `currentness` field, map it to `"unknown"`.

- [ ] **Step 2: Write the failing tests (define + resolution + empty)**

```python
from mnemo_memory.packages.application.dbt_structure import DbtStructureService
from mnemo_memory.packages.application.dbt import DbtManifestApplicationService
from mnemo_memory.packages.domain import ArtifactCurrentness
# copy project_scope() + dbt_service_with_manifest() helpers into this file

def test_downstream_from_unique_id():
    service = DbtStructureService(dbt_service_with_manifest())
    r = service.lookup(project_scope(), kind="downstream", target="model.mnemo_analytics.fct_orders")
    assert r.kind == "downstream"
    assert r.resolved_unique_id == "model.mnemo_analytics.fct_orders"
    assert r.currentness in ("current", "stale", "unknown")
    assert isinstance(r.items, tuple)

def test_target_resolves_by_relative_path():
    service = DbtStructureService(dbt_service_with_manifest())
    r = service.lookup(project_scope(), kind="upstream", target="models/marts/fct_orders.sql")
    assert r.resolved_unique_id == "model.mnemo_analytics.fct_orders"

def test_target_resolves_by_bare_name():
    service = DbtStructureService(dbt_service_with_manifest())
    r = service.lookup(project_scope(), kind="upstream", target="fct_orders")
    assert r.resolved_unique_id == "model.mnemo_analytics.fct_orders"

def test_unknown_node_fails_open():
    service = DbtStructureService(dbt_service_with_manifest())
    r = service.lookup(project_scope(), kind="upstream", target="does_not_exist_xyz")
    assert r.items == () and r.resolved_unique_id is None
    assert r.currentness == "unknown"

def test_no_active_manifest_fails_open():
    from mnemo_memory.packages.storage.reference import ReferenceProjectIndexRepository
    service = DbtStructureService(DbtManifestApplicationService(ReferenceProjectIndexRepository()))
    r = service.lookup(project_scope(), kind="changes", target="")
    assert r.items == () and r.currentness == "unknown"
```

- [ ] **Step 3: Implement the service**

```python
"""Deterministic, model-free dbt structural lookups over the active manifest.

Answers lineage/impact/test-coverage/freshness/changes for dbt projects so an
agent does not dig through target/manifest.json. Built only on existing
DbtManifestApplicationService query methods; never runs dbt; never raises.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

from mnemo_memory.packages.application.dbt import (
    DbtManifestApplicationService, LineageDirection, QueryLineage, ResolveManifestFile,
    # + QueryTestCoverage, QuerySourceFreshness, QueryManifestChanges (confirm names in Step 1)
)
from mnemo_memory.packages.domain import ArtifactCurrentness, MemoryScope
from mnemo_memory.packages.domain.dbt_manifest import DbtNodeId

DbtStructureKind = Literal["upstream","downstream","impact","test_coverage","freshness","changes"]
_LINEAGE_KINDS = frozenset({"upstream","downstream","impact"})

@dataclass(frozen=True, slots=True)
class DbtStructureResult:
    kind: str
    query: str
    resolved_unique_id: str | None
    currentness: str
    currentness_reason: str
    items: tuple[dict, ...]
    edges: tuple[dict, ...]
    truncated: bool

def _looks_like_unique_id(t: str) -> bool:
    # dbt unique_ids are "<resource>.<package>.<name>", resource in a known set
    head = t.split(".", 1)[0]
    return "." in t and head in {
        "model","source","seed","snapshot","test","analysis","exposure","metric","semantic_model","macro",
    }
def _looks_like_path(t: str) -> bool:
    return "/" in t or t.endswith(".sql")

def _empty(kind: str, target: str, reason: str) -> DbtStructureResult:
    return DbtStructureResult(kind, target, None, "unknown", reason, (), (), False)

class DbtStructureService:
    def __init__(self, dbt_service: DbtManifestApplicationService, *, current_source_state=None) -> None:
        self._dbt = dbt_service
        self._current_source_state = current_source_state

    def lookup(self, scope: MemoryScope, *, kind: DbtStructureKind, target: str = "", depth: int | None = None) -> DbtStructureResult:
        target = target.strip()
        if kind not in ("upstream","downstream","impact","test_coverage","freshness","changes"):
            return _empty(kind, target, "unsupported kind")
        css = self._resolve_source_state(scope)
        try:
            if kind == "changes":
                return self._changes(scope, css)
            unique_id = self._resolve(scope, target)
            if unique_id is None:
                return _empty(kind, target, "could not resolve target to a dbt node")
            if kind in _LINEAGE_KINDS:
                return self._lineage(scope, kind, target, unique_id, depth, css)
            if kind == "test_coverage":
                return self._test_coverage(scope, target, unique_id, css)
            return self._freshness(scope, target, unique_id, css)  # kind == "freshness"
        except Exception:
            return _empty(kind, target, "dbt structural query failed")

    def _resolve_source_state(self, scope):
        if self._current_source_state is None:
            return None
        try:
            return self._current_source_state(scope)
        except Exception:
            return None

    def _resolve(self, scope, target) -> str | None:
        if not target:
            return None
        if _looks_like_unique_id(target):
            return target
        if _looks_like_path(target):
            try:
                resolved = self._dbt.resolve_file(ResolveManifestFile(scope, target))
                return str(resolved.node.unique_id)
            except Exception:
                return None
        # bare name: scan the active snapshot's nodes for a unique name match
        try:
            snapshot = self._dbt.get_active_snapshot(scope)   # confirm method name in Step 1
            if snapshot is None:
                return None
            matches = [n for n in self._dbt.iter_nodes(scope, snapshot.snapshot_id) if n.name == target]
            return str(matches[0].unique_id) if len(matches) == 1 else None
        except Exception:
            return None

    def _lineage(self, scope, kind, target, unique_id, depth, css):
        direction = LineageDirection.UPSTREAM if kind == "upstream" else LineageDirection.DOWNSTREAM
        result = self._dbt.query(QueryLineage(
            scope=scope, unique_id=DbtNodeId(unique_id), direction=direction,
            transitive=True, maximum_depth=depth, current_source_state=css,
        ))
        items = tuple(
            {"unique_id": str(n.node.unique_id), "name": n.node.name,
             "resource_type": n.node.resource_type.value if hasattr(n.node.resource_type, "value") else str(n.node.resource_type),
             "relative_path": n.node.original_file_path, "depth": n.depth}
            for n in result.nodes
        )
        edges = tuple(
            {"parent_id": str(e.parent_id), "child_id": str(e.child_id),
             "edge_type": e.edge_type.value if hasattr(e.edge_type, "value") else str(e.edge_type)}
            for e in result.edges
        )
        return DbtStructureResult(kind, target, unique_id,
            result.currentness.value, result.currentness_reason, items, edges, result.truncated)

    # _test_coverage / _freshness / _changes: implement in A2 against the real result fields (Step 1)
    def _test_coverage(self, scope, target, unique_id, css): raise NotImplementedError
    def _freshness(self, scope, target, unique_id, css): raise NotImplementedError
    def _changes(self, scope, css): raise NotImplementedError
```

> `impact` uses `DOWNSTREAM` transitively (blast radius) — same as `downstream` but semantically "what breaks if I change this"; keep both kinds, both mapping to DOWNSTREAM (impact may later differ). Confirm `get_active_snapshot`'s exact name on the service in Step 1 (the extraction saw it on the storage layer; the service may expose it or you read the active snapshot via a query). If the service has no direct accessor, resolve bare names by running a lineage/`resolve_file` path only and treat bare-name as unsupported → empty+unknown (do NOT invent an API).

- [ ] **Step 4: Run — lineage/resolution/empty tests pass**

Run: `uv run pytest tests/unit/test_dbt_structure_service.py -v`
Expected: the unique_id / path / bare-name / unknown / no-manifest tests PASS; test_coverage/freshness/changes not yet (A2).

- [ ] **Step 5: Commit**

```bash
git add src/mnemo_memory/packages/application/dbt_structure.py tests/unit/test_dbt_structure_service.py
git commit -m "feat(dbt-structure): service skeleton with lineage + node resolution + fail-open"
```

### Task A2: `test_coverage`, `freshness`, `changes` kinds

**Files:**
- Modify: `src/mnemo_memory/packages/application/dbt_structure.py`
- Test: `tests/unit/test_dbt_structure_service.py`

**Interfaces:**
- Consumes `query_test_coverage`, `query_source_freshness`, `query_changes` + their result types (fields read in A1 Step 1).
- Produces the three `_test_coverage` / `_freshness` / `_changes` methods, each mapping result rows into `items` (flat dicts) and stamping `currentness` / `currentness_reason` from the result (map `manifest_currentness` → currentness for freshness/changes if that is the field name).

- [ ] **Step 1: Write failing tests** (mirror the fixture; assert non-empty `items` for a known model/source and that `currentness` is one of current/stale/unknown). Use a source node from `sources-v3.json` for freshness; use a tested model for coverage; for `changes`, ingest twice (or use the single snapshot — `changes` with only one snapshot returns empty, currentness known — assert it does not raise and returns the empty-but-valid shape).

- [ ] **Step 2: Run — verify fail** (`NotImplementedError`).

- [ ] **Step 3: Implement the three methods** against the real result fields, mapping rows to `items` (e.g. test_coverage item: `{"test_unique_id","subject_node","status","relative_path"}`; freshness item: `{"source_unique_id","status","max_loaded_at","age_seconds"}`; changes item: `{"kind","unique_id","resource_type","relative_path"}`), `edges=()`, `truncated` where the result provides it.

- [ ] **Step 4: Run — all service tests pass.** `uv run pytest tests/unit/test_dbt_structure_service.py -v`; then `uv run mypy src/mnemo_memory/packages/application/dbt_structure.py` clean; `ruff format --check` + `ruff check` on the two files.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo_memory/packages/application/dbt_structure.py tests/unit/test_dbt_structure_service.py
git commit -m "feat(dbt-structure): test_coverage, freshness, and changes kinds"
```

---

## Part B — `dbt_structure` MCP tool + port method + wiring

**Why:** Expose the service as the discoverable tool. This mirrors the merged `structural_lookup` wiring exactly — use it as the template.

> **Read-before-edit template (all merged on `main`):** `mcp_durable.py` `DurableMcpContextPort.structural_lookup` (~209-254) + `_project_scope`/`_scope` (~1237-1270) + how `_structural_lookup_service` is constructed (~203-207) and the constructor (~146-190); `mcp_port.py:11`; `apps/mcp/server.py` `@server.tool structural_lookup` (~987-1006) + the `names` hardening list (~1008-1015) + `_build_local_mcp_context_session` construction (~1028-1190); `apps/mcp/team.py` `_empty_structural_lookup` (~114-129) + the two team ports' short-circuits (~156-159, ~352-356) + the team factory (~310-325).

### Task B1: `DurableMcpContextPort.dbt_structure` + protocol entry + service wiring

**Files:**
- Modify: `src/mnemo_memory/packages/application/mcp_durable.py`
- Modify: `src/mnemo_memory/packages/application/mcp_port.py`
- Modify: `src/mnemo_memory/apps/mcp/server.py` (construct `DbtStructureService` and pass it into the port in `_build_local_mcp_context_session`)
- Test: `tests/unit/test_mcp_durable_dbt_structure.py`

**Interfaces:**
- Consumes `DbtStructureService(dbt_service, current_source_state=...)`; the existing `_project_scope(_scope(request, self._default_scope))`; the runtime's dbt service + `current_dbt_source_state` callable (already available where `structural_lookup` reads `source_structure_repository` and where `current_dbt_source_state` is already passed to the port).
- Produces `DurableMcpContextPort.dbt_structure(self, request: Mapping[str, object]) -> dict[str, object]`.

- [ ] **Step 1: Failing test** — build a `DurableMcpContextPort` over an ingested dbt manifest (reuse/extend the structural_lookup port fixture pattern; the port must receive a `DbtStructureService`). Assert `port.dbt_structure({"kind":"downstream","target":"fct_orders"})` returns `kind=="downstream"`, a `currentness` field, and `items`. Assert unknown kind and missing service both return the empty shape without raising.

- [ ] **Step 2: Run — verify `AttributeError`.**

- [ ] **Step 3: Implement the port method** (mirror `structural_lookup` verbatim in structure):

```python
def dbt_structure(self, request: Mapping[str, object]) -> dict[str, object]:
    kind = str(request.get("kind", "")).strip()
    target = str(request.get("target", "")).strip()
    raw_depth = request.get("depth")
    depth = raw_depth if isinstance(raw_depth, int) and not isinstance(raw_depth, bool) else None
    empty: dict[str, object] = {
        "kind": kind, "query": target, "resolved_unique_id": None,
        "currentness": "unknown", "currentness_reason": "", "freshness_hint": _DBT_STALE_HINT,
        "items": [], "edges": [], "truncated": False,
    }
    if (kind not in ("upstream","downstream","impact","test_coverage","freshness","changes")
            or self._dbt_structure_service is None):
        return empty
    try:
        scope = _project_scope(_scope(request, self._default_scope))
        r = self._dbt_structure_service.lookup(scope, kind=cast(DbtStructureKind, kind), target=target, depth=depth)
    except Exception:
        return empty
    return {
        "kind": r.kind, "query": r.query, "resolved_unique_id": r.resolved_unique_id,
        "currentness": r.currentness, "currentness_reason": r.currentness_reason,
        "freshness_hint": "" if r.currentness == "current" else _DBT_STALE_HINT,
        "items": list(r.items), "edges": list(r.edges), "truncated": r.truncated,
    }
```
with a module constant `_DBT_STALE_HINT = "Manifest may be behind your working tree; re-run dbt (through mnemo) so the lineage reflects current SQL."` Construct `self._dbt_structure_service` in `__init__` from an injected `dbt_structure_service` param (None-safe, like `_structural_lookup_service`). Add `dbt_structure(self, request: dict[str, object]) -> dict[str, object]: ...` to the `McpContextPort` protocol (`mcp_port.py`). In `_build_local_mcp_context_session`, construct `DbtStructureService(runtime.dbt_manifest_service, current_source_state=current_dbt_source_state)` and pass it as `dbt_structure_service=` into `DurableMcpContextPort(...)`.

- [ ] **Step 4: Run — port tests pass.** `uv run pytest tests/unit/test_mcp_durable_dbt_structure.py -v`; `uv run mypy` clean.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo_memory/packages/application/mcp_durable.py src/mnemo_memory/packages/application/mcp_port.py src/mnemo_memory/apps/mcp/server.py tests/unit/test_mcp_durable_dbt_structure.py
git commit -m "feat(dbt-structure): DurableMcpContextPort.dbt_structure + service wiring"
```

### Task B2: Register the `dbt_structure` tool + team-port fail-open

**Files:**
- Modify: `src/mnemo_memory/apps/mcp/server.py` (`@server.tool` + the `names` hardening list)
- Modify: `src/mnemo_memory/apps/mcp/team.py` (`_empty_dbt_structure` + both team ports)
- Test: `tests/unit/test_mcp_server.py`, `tests/security/test_team_mcp_authentication.py`

- [ ] **Step 1: Register the tool** (after `structural_lookup`, keeping `save_checkpoint`'s position; also append to the `names` list at server.py ~1008-1015):

```python
@server.tool(
    name="dbt_structure",
    description=(
        "Answer dbt model-graph questions from the maintained manifest — deterministic, "
        "no model call, a few tokens. PREFER THIS over dispatching a search/Explore agent "
        "or digging through target/manifest.json for a dbt model's dependencies, blast "
        "radius, tests, or source freshness. kind='upstream' (what feeds a model), "
        "'downstream'/'impact' (what depends on it / breaks if you change it), "
        "'test_coverage' (tests on a model), 'freshness' (a source's freshness), "
        "'changes' (models changed since the active manifest). target is a model name, a "
        "project-relative .sql path, or a dbt unique_id (empty for freshness/changes). "
        "Every answer includes a currentness stamp (current/stale/unknown); mnemo never "
        "runs dbt, so re-run dbt yourself to refresh a stale manifest."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
)
def dbt_structure(
    kind: Annotated[str, Field(pattern="^(upstream|downstream|impact|test_coverage|freshness|changes)$")],
    target: Annotated[str, Field(max_length=512)] = "",
    depth: Annotated[int, Field(ge=1, le=50)] | None = None,
) -> dict[str, object]:
    return port.dbt_structure({"kind": kind, "target": target, "depth": depth})
```
(If the `Annotated | None` default form does not satisfy the FastMCP schema builder, use `depth: Annotated[int | None, Field(default=None, ge=1, le=50)] = None` — match whatever the existing optional-int tool params in this file do.)

- [ ] **Step 2: Team-port fail-open** — add `_empty_dbt_structure(request)` returning the empty shape (mirror `_empty_structural_lookup`), and have `PostgreSQLTeamMcpPort.dbt_structure` and `AuthenticatedTeamMcpPort.dbt_structure` return it (short-circuit BEFORE the workspace-requiring `_port` path):

```python
def _empty_dbt_structure(request: dict[str, object]) -> dict[str, object]:
    return {
        "kind": str(request.get("kind", "")).strip(), "query": str(request.get("target", "")).strip(),
        "resolved_unique_id": None, "currentness": "unknown", "currentness_reason": "",
        "freshness_hint": "", "items": [], "edges": [], "truncated": False,
    }
```

- [ ] **Step 3: Tests** — add `dbt_structure` to the exact-tool-list assertions in `test_mcp_server.py` (BOTH lists, ~123-130 and ~274-282); add a security test that `dbt_structure` through the real `AuthenticatedTeamMcpPort` returns the empty shape without raising (mirror `test_structural_lookup_fails_open_instead_of_requiring_workspace_scope`).

- [ ] **Step 4: Run** — `uv run pytest tests/unit/test_mcp_server.py tests/security/test_team_mcp_authentication.py -v`; `uv run mypy` clean; `ruff format --check` + `ruff check` on changed files.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo_memory/apps/mcp/server.py src/mnemo_memory/apps/mcp/team.py tests/unit/test_mcp_server.py tests/security/test_team_mcp_authentication.py
git commit -m "feat(dbt-structure): register discoverable dbt_structure tool + team fail-open"
```

---

## Part C — Update every hardcoded tool inventory (release-gate proofing)

**Why:** Adding `structural_lookup` failed the release gate because three inventory copies outside `tests/unit` were missed. This task fixes ALL of them up front for `dbt_structure`, and runs the gates that only CI ran last time.

**Files (each gains `"dbt_structure"` appended after `"structural_lookup"`):**
- `tests/unit/test_claude_connection.py` (~:97-104)
- `tests/unit/test_codex_connection.py` (~:116-123)
- `tests/integration/test_mcp_durability.py` (~:243-250)
- `scripts/run_cross_client_benchmark.py` (~:158-165)
- `scripts/verify_installed_mcp.py` (`TOOLS`, ~:23-30)
- (`tests/unit/test_mcp_server.py` and `tests/security/test_team_mcp_authentication.py` are handled in B2; `server.py` `names` list in B2.)

- [ ] **Step 1: Grep to confirm the full set** — `grep -rn 'structural_lookup' tests/ scripts/ src/mnemo_memory/apps/mcp/server.py | grep -iE 'get_context|save_checkpoint|TOOLS|inventory|== \[|!= \['` and ensure every exact-inventory list is in the edit set. Any list found that isn't already covered is a Missing finding — add it.

- [ ] **Step 2: Append `"dbt_structure"`** to each list (after `"structural_lookup"`), matching each file's ordering/formatting.

- [ ] **Step 3: Run the release-gate suites locally** (the ones CI runs that `tests/unit` alone does not):

```bash
uv run ruff format --check .
uv run ruff check .
uv run pytest tests/unit tests/integration tests/contract -q
```
Expected: all pass (Postgres-gated tests skip). Fix any remaining exact-inventory assertion the run surfaces (the cross-client benchmark asserts inventory via `RuntimeError("unexpected MCP tool inventory")`).

- [ ] **Step 4: Commit**

```bash
git add tests/ scripts/
git commit -m "chore(dbt-structure): add dbt_structure to all hardcoded MCP tool inventories"
```

---

## Final verification

- [ ] `uv run pytest -q` — all pass.
- [ ] `uv run ruff format --check . && uv run ruff check .` — clean (the gate's first two steps).
- [ ] `uv run mypy` (project strict target) — clean.
- [ ] **Manual smoke:** spawn the server over stdio (as in the `structural_lookup` demo) and confirm `dbt_structure` appears in `tools/list`; against a project with an ingested manifest, call `dbt_structure(kind="downstream", target="<a model>")` and confirm nodes + a `currentness` stamp come back.

---

## Self-review notes (author)

- **Spec coverage:** discoverable tool → B2; six kinds → A1 (lineage×3) + A2 (test_coverage/freshness/changes); freshness banner → port method (B1) + service currentness (A); node resolution name/path/unique_id → A1; fail-open + team → A/B; inventory/gate lesson → C.
- **Spec refinement (recorded):** the spec's banner listed an always-present `changed_since_manifest` count; the plan surfaces `currentness` + `currentness_reason` + `freshness_hint` on every response instead, with the precise changed list available via `kind="changes"`. Getting an exact count on every call would require an extra diff per call — deferred as not worth it. If you want the count back on every response, say so and I'll add it.
- **Known verifications flagged inline (not placeholders):** A1 Step 1 reads the three non-lineage Query/result dataclass fields and the active-snapshot accessor before coding; B2 Step 1 matches the optional-int tool-param form to existing tools.
- **Independence:** A → B → C (B consumes A's service; C is the inventory sweep, do last). Never parallel implementers.
