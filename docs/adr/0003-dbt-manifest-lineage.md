# ADR 0003: dbt manifest as authoritative lineage evidence

## Context

Issue 12 begins Mnemo's structural-memory work. dbt produces a `manifest.json` artifact that
describes project resources and their declared dependencies. Mnemo needs reproducible lineage
without running dbt, parsing SQL, or asking a model to infer edges.

## Decision

Mnemo 12A supports exactly manifest schema v12, identified solely by
`metadata.dbt_schema_version == https://schemas.getdbt.com/dbt/manifest/v12.json`. It consumes
metadata, `nodes`, `sources`, each resource's `depends_on.nodes`, and (when supplied) `parent_map`
and `child_map`. Each `depends_on.nodes` entry is the canonical edge authority; both maps are
consistency checks for the parsed node/source subgraph and a disagreement rejects the artifact.
Map-only entries, and deferred children of a parsed node, remain unimplemented artifact structure
rather than being assigned invented lineage semantics.

The graph is a rebuildable, scoped projection. A dbt `unique_id` is its node identity, not a
Mnemo UUID. Nodes and edges each retain deterministic evidence references to the digest-addressed
manifest. Descriptions, tags, and meta-like descriptive content are inert untrusted data, never
instructions. Non-node/source collections are counted as deferred rather than given invented
lineage semantics.

Issue 15D applies that same rule to the v12 `exposures`, `metrics`, and `semantic_models`
collections. They become typed graph nodes only from their exact manifest identities and
`depends_on.nodes` entries, so ordinary downstream traversal can report an affected exposure or
metric through its declared semantic-model bridge. Their descriptive fields, dimensions, and
measures remain inert and are not projected.

Issue 15E adds manifest macros as typed nodes and maps only exact `depends_on.macros` identities to
`dbt_macro_dependency` edges. Ordinary `depends_on.nodes` remains `dbt_dependency`; mixing a macro
identity into the wrong field fails closed. Macro SQL is validated only by the existing bounded
string limit and is then discarded. Node lineage maps validate ordinary edges only because dbt's
macro call graph is separately authoritative in `depends_on.macros`.

Issue 15F adds directed shortest-path selection over the persisted typed graph. Breadth-first
search uses stable identity/edge-type ordering and hard node, edge, and depth bounds; it returns
only the selected path and exact node/edge evidence. It never parses SQL to bridge disconnected
resources. An unreachable destination is a normal bounded no-path result.

## Consequences

The parser is standard-library-only, offline, bounded, and schema-version-adapted. It accepts
JSON bytes or text and rejects malformed structure, missing dependencies, cycles, map conflicts,
and configured resource limits. It performs no dbt execution, Jinja rendering, SQL parsing,
warehouse access, network request, or LLM call. Another version is added through a dedicated
adapter only after field semantics are reviewed.

Raw artifact SHA-256 identifies exact input; a normalized graph SHA-256 identifies its structural
projection. Optional Git/tree/target data is supplied by a later outer adapter. Without matching
repository evidence, currentness is explicitly `unknown`, never assumed current.

## Supplemental artifacts

Issue 15A adds separate standard-library-only adapters for the current public catalog v1 and
run-results v6 schemas. Catalog parsing retains only exact relation identity/type and ordered
column name/type facts. Run-results parsing retains only exact node identity, normalized status,
bounded phase timing, failure count, elapsed time, and the producing command name. Both retain
digest-addressed evidence and explicit caller scope. Warehouse comments, owners, statistics,
environment values, adapter responses, messages, compiled code, relation SQL, arbitrary command
arguments, and thread identifiers are validated only where required for schema safety and then
discarded. These supplemental artifacts do not alter manifest lineage authority.

Issue 15B persists those minimized projections as immutable content-digest versions attached to
one exact authorized manifest snapshot. A supplemental relation or result is accepted only when
its `unique_id` exists in that snapshot. One version per manifest and artifact kind is selected as
current; an identical retry reselects the retained version without duplicating rows. Foreign keys
prevent detached catalog relations, columns, results, or timing rows. The raw artifact remains
outside canonical storage.

Issue 15C integrates them without changing manifest lineage authority. Successful wrapped dbt
commands and explicit local manifest ingestion inspect only sibling `catalog.json`,
`run_results.json`, and `sources.json`; missing, invalid, unsupported, or mismatched supplemental files fail softly
after a valid manifest is selected. Existing bounded lineage context may include at most twelve
ordered columns per matching relation and the matching node's current stored run status, execution
time, and failure count. Every included projection carries its exact artifact evidence and remains
subject to the structural token budget.

Source freshness comes only from a minimized official `sources.json` v3 observation attached to the
same manifest snapshot. Mnemo retains identity, status, observed timestamps/age, threshold
counts/periods, execution time, metadata, and evidence. It discards database error text, adapter
responses, filters, timing details, and environment values, and never recomputes the authoritative
dbt status.

Successful local ingestion may attach a bounded `SourceStateFingerprint` containing full Git HEAD,
dirty state, a content-sensitive SHA-256 working-tree fingerprint, and an explicit dbt target.
Observation is shell-free, read-only, timed, and failure-isolated. Paths, bodies, diffs, messages,
remotes, credentials, and environment values are excluded. Target disagreement is stale; a missing
target on only one side is not safely comparable.

The MCP composition may resolve one unambiguous local dbt binding by the already-resolved project
identity and inject a fresh bounded observation into an existing structural query. The transport
does not accept caller-authored source-state evidence, and any binding or observation failure
degrades currentness to unknown without changing the structural result.

## References reviewed

Reviewed 2026-08-02: [dbt manifest artifact documentation](https://docs.getdbt.com/reference/artifacts/manifest-json), [dbt schema registry](https://schemas.getdbt.com/), and [manifest v12 schema](https://schemas.getdbt.com/dbt/manifest/v12.json). The dbt documentation maps Core 1.8–1.11 to manifest v12 and cautions that dbt and manifest versions are related but distinct.

Reviewed 2026-08-05: [dbt catalog documentation](https://docs.getdbt.com/reference/artifacts/catalog-json), [catalog v1 schema](https://schemas.getdbt.com/dbt/catalog/v1.json), [dbt run-results documentation](https://docs.getdbt.com/reference/artifacts/run-results-json), and [run-results v6 schema](https://schemas.getdbt.com/dbt/run-results/v6.json).

Reviewed 2026-08-05: [dbt sources documentation](https://docs.getdbt.com/reference/artifacts/sources-json) and [sources v3 schema](https://schemas.getdbt.com/dbt/sources/v3.json).
