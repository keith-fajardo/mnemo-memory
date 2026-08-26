# dbt Structural Awareness — Design Spec

**Date:** 2026-08-26
**Status:** Draft (awaiting review)
**Scope:** A discoverable `dbt_structure` MCP tool giving the agent the same
structural awareness for dbt projects that `structural_lookup` gives for code —
lineage + test coverage + freshness + changed-models, every answer stamped with
manifest freshness.

---

## Plain-language summary

For code, the agent can now ask a cheap tool "what calls this / what does this
file contain" instead of dispatching an Explore agent (`structural_lookup`,
shipped in `0.1.0a24`). For **dbt** projects, the equivalent question is about
the **model graph** — "what feeds this model, what breaks if I change it, is it
tested, is the data fresh." mnemo already *can* answer these, but the ability is
**hidden** as a `get_context` parameter, so the agent rarely reaches for it.

This adds a **first-class, discoverable `dbt_structure` tool** — one obvious entry
point the agent chooses over a manual dig through `target/manifest.json` — and
makes every answer **honest about staleness**, because unlike code, mnemo cannot
refresh a dbt manifest on its own (regenerating it requires *your* `dbt parse`;
mnemo deliberately never runs dbt).

Think **map vs. directions** again: this is the dbt *map* (the model DAG from the
compiled manifest), made cheap and discoverable — with a freshness stamp so the
agent knows when the map is behind your working tree.

---

## Reality check (verified 2026-08-26)

Most of the machinery already exists. This spec's actionable scope is a thin
discoverable tool + a freshness contract over it.

Already built:
- **Query engine** — `DbtLineageGraph` (`direct_upstream/downstream`,
  `transitive_upstream/downstream`) + `DbtManifestApplicationService.query`
  (`QueryLineage`), model-level DAG traversal, bounded.
- **Query dispatch already reachable** — `get_context` accepts `dbt_lineage`,
  `dbt_test_coverage`, `dbt_freshness`, `dbt_changes` params, dispatched in
  `packages/application/mcp_durable.py` (one dbt structural query at a time).
- **Per-project dbt binding** — `find_dbt_project_root` (walks up to
  `dbt_project.yml`), `DbtProjectBinding(project_root, scope)`,
  `LocalDbtProjectBindingStore` (explicit scope, never inferred).
- **Manifest ingestion + active snapshot** — `DbtManifestApplicationService.ingest`,
  `store_and_activate` / `get_active_snapshot`, `DbtManifestSnapshot(is_active)`.
- **Staleness model** — `ArtifactCurrentness` (CURRENT / STALE / UNKNOWN),
  `SourceStateFingerprint`, `current_source_state` threaded through queries;
  `hook.py` already emits read-only dbt downstream cues without running dbt.

The gaps (this spec's real scope):
1. **Discoverability** — the query is a buried `get_context` param, not a tool the
   agent obviously reaches for.
2. **Unified surface** — the four query families are separate params with
   heterogeneous shapes; no single dbt-shaped entry point.
3. **Freshness surfacing** — currentness exists internally but isn't returned as a
   loud, consistent banner on every answer, and there's no discoverable nudge to
   re-`dbt parse` when stale.

## Goals

1. One discoverable `dbt_structure` MCP tool, `kind`-discriminated, that the agent
   prefers over an Explore/manual dig for dbt model questions.
2. Cover the full bundle: `upstream`, `downstream`, `impact`, `test_coverage`,
   `freshness`, `changes`.
3. Every response carries a freshness banner (CURRENT / STALE / UNKNOWN + count of
   dbt files changed since the active manifest + a re-`dbt parse` hint).
4. Agent-friendly node addressing: accept model **name**, **relative path**, or
   `unique_id`, resolved internally.

## Non-goals (explicit)

- **No auto-`dbt parse` / auto-ingest.** mnemo never runs dbt. The manifest is
  refreshed only by the user running dbt through mnemo's command wrapper or an
  explicit CLI ingest — unchanged. This tool *surfaces* staleness; it does not fix
  it.
- **Model/node-level lineage only** — no column-level lineage (the manifest
  doesn't carry it).
- **No new query engine** — reuse `DbtLineageGraph` / the existing `get_context`
  dbt dispatch entirely.
- **No new sibling tools** — one `dbt_structure` tool, not several.

## Hard design rules

- **No LLM in the path** — traversal + manifest read are deterministic; the only
  tokens are the agent's call + reading the compact answer.
- **Fail-open** — no active manifest (project never ingested), unresolved node, or
  any storage error → empty hits + `currentness: UNKNOWN` + a hint; never raise.
  Team/Postgres ports fail-open to empty (the Part C lesson).
- **Minimal tool schema** — no scope params on the tool; scope derives from the
  bound project (the Part C lesson).
- **Never serve a stale map silently** — a STALE result is still returned (it's
  often still useful) but is clearly labeled STALE with the changed-file count.

---

## Architecture

One new tool, one port method, forwarding to dispatch that already exists.

### The tool: `dbt_structure`

`@server.tool` (readOnlyHint=True), discoverability-forward description:
"Answer dbt model-graph questions from the maintained manifest — deterministic,
no model call. PREFER THIS over dispatching a search/Explore agent or digging
through target/manifest.json when you need a model's upstream/downstream
dependencies, blast radius, test coverage, or source freshness."

Inputs:
- `kind`: `upstream | downstream | impact | test_coverage | freshness | changes`
  (pattern-restricted).
- `target`: model name / relative path / `unique_id`. Required for
  `upstream|downstream|impact|test_coverage`; optional for `freshness|changes`
  (whole-project).
- `depth`: optional int bound for transitive `upstream|downstream`
  (`impact` = transitive downstream, unbounded by default within engine limits).

Output (stable shape):
```
{
  "kind": "...",
  "query": "<target as given>",
  "resolved_unique_id": "model.proj.x" | null,
  "currentness": "current" | "stale" | "unknown",
  "changed_since_manifest": <int>,
  "freshness_hint": "<one line; empty when current>",
  "nodes": [ {"unique_id","name","resource_type","relative_path","depth"} ... ],
  "edges": [ {"parent_id","child_id","edge_type"} ... ],   // lineage kinds
  "truncated": <bool>
}
```
(`test_coverage` / `freshness` / `changes` fill `nodes` with their result rows and
omit `edges`; the freshness banner fields are present on every kind.)

### The port method

`DurableMcpContextPort.dbt_structure(request)` — a thin adapter that:
1. Validates `kind`; resolves `target` → `unique_id` (name/path/unique_id).
2. Derives PROJECT scope from the dbt binding (reuse existing dbt scope
   derivation / `current_dbt_source_state`).
3. Forwards to the existing `context_service.get_context(GetUnifiedContext(...))`
   dbt dispatch for the matching family (the port already routes dbt queries this
   way).
4. Computes/attaches the freshness banner from `current_source_state` /
   `ArtifactCurrentness`.
5. Wraps everything in try/except → the empty/UNKNOWN shape on any failure.

Added to the `McpContextPort` protocol (`mcp_port.py`) and implemented on the
deferred port and both team ports (team ports fail-open to empty).

### Node resolution

`target` → `unique_id`: if it already looks like a `unique_id` (`model.pkg.name`),
use it; else resolve by relative path (existing `resolve_file`) or by model name
against the active manifest's nodes. Ambiguous/unknown name → empty + UNKNOWN with
a "could not resolve" hint. (Exact resolver wiring is a plan-time verification —
see Open Questions.)

## Reuse map

| Piece | Status |
|---|---|
| Lineage traversal (upstream/downstream/impact) | **Exists** — `DbtLineageGraph`, `QueryLineage` |
| test_coverage / freshness / changes handlers | **Exists** — `get_context` dbt dispatch in `mcp_durable.py` |
| dbt binding + scope | **Exists** — `DbtProjectBinding`, `LocalDbtProjectBindingStore` |
| Manifest ingest + active snapshot | **Exists** — `DbtManifestApplicationService` |
| Staleness / currentness | **Exists** — `ArtifactCurrentness`, `current_source_state` |
| `dbt_structure` tool + description | **New** |
| `dbt_structure` port method (all port impls) | **New** |
| Freshness banner on every response | **New** (assembled from existing currentness) |
| Node resolution (name/path/unique_id → unique_id) | **New** (thin; may reuse `resolve_file`) |

## Phasing

- **v1 (this spec):** the `dbt_structure` tool over the four existing families +
  freshness banner + node resolution.
- **Later (out of scope):** column-level lineage; proactive routing (auto-steer dbt
  questions to the tool); a staleness-driven prompt to run dbt.

## Testing approach

- **Per-kind correctness** on a fixture manifest (v12): `upstream`/`downstream`
  return the right nodes/edges; `impact` = transitive downstream; `test_coverage`
  / `freshness` / `changes` return their rows.
- **Node resolution**: name, relative path, and `unique_id` all resolve to the same
  node; unknown/ambiguous name → empty + UNKNOWN + hint (no raise).
- **Freshness banner**: fresh manifest → `current`; simulate a changed source-state
  fingerprint → `stale` + `changed_since_manifest > 0` + non-empty hint; no active
  manifest → `unknown` + empty hits (fail-open).
- **Fail-open**: project not dbt-bound / storage error → empty + UNKNOWN, never
  raises; team ports return empty for `dbt_structure` without touching the
  workspace guard.
- **No-LLM guarantee**: the tool path invokes no model client.
- **Tool inventory (release-gate lesson)**: the plan MUST enumerate and update every
  hardcoded exact tool-inventory assertion — `tests/unit/test_mcp_server.py`,
  `tests/unit/test_claude_connection.py`, `tests/unit/test_codex_connection.py`,
  `tests/security/test_team_mcp_authentication.py`,
  `tests/integration/test_mcp_durability.py`,
  `scripts/run_cross_client_benchmark.py`, `scripts/verify_installed_mcp.py` — and
  run `tests/integration` + `tests/contract` (not just `tests/unit`) plus
  `ruff format --check` before any release.

## Open questions (resolve at plan time)

1. **Exact node resolution** — is there a name→`unique_id` resolver, or must the
   tool scan the active manifest's nodes for a name match? Confirm `resolve_file`'s
   signature for path→node.
2. **Freshness computation ownership** — does `current_source_state` /
   `_resolve_current_dbt_source_state` already yield CURRENT/STALE/UNKNOWN + a
   changed-file count the port can surface directly, or must the banner be
   assembled from `dbt_changes` output?
3. **Single-tool param heterogeneity** — final validation rules (which params are
   required/forbidden per `kind`), mirroring the existing "one dbt query at a time"
   guard.
4. **Tool ordering** — register `dbt_structure` so it does not disturb the
   index-referenced position of `save_checkpoint` (as `structural_lookup` was
   registered last).
