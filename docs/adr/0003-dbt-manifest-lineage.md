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
consistency checks and a disagreement rejects the artifact.

The graph is a rebuildable, scoped projection. A dbt `unique_id` is its node identity, not a
Mnemo UUID. Nodes and edges each retain deterministic evidence references to the digest-addressed
manifest. Descriptions, tags, and meta-like descriptive content are inert untrusted data, never
instructions. Non-node/source collections are counted as deferred rather than given invented
lineage semantics.

## Consequences

The parser is standard-library-only, offline, bounded, and schema-version-adapted. It accepts
JSON bytes or text and rejects malformed structure, missing dependencies, cycles, map conflicts,
and configured resource limits. It performs no dbt execution, Jinja rendering, SQL parsing,
warehouse access, network request, or LLM call. Another version is added through a dedicated
adapter only after field semantics are reviewed.

Raw artifact SHA-256 identifies exact input; a normalized graph SHA-256 identifies its structural
projection. Optional Git/tree/target data is supplied by a later outer adapter. Without matching
repository evidence, currentness is explicitly `unknown`, never assumed current.

## Deferred work

`catalog.json`, `run_results.json`, persistence, incremental ingestion, context-packet retrieval,
structural token benchmarks, general code graphs, and model-assisted behavior are deferred.

## References reviewed

Reviewed 2026-08-02: [dbt manifest artifact documentation](https://docs.getdbt.com/reference/artifacts/manifest-json), [dbt schema registry](https://schemas.getdbt.com/), and [manifest v12 schema](https://schemas.getdbt.com/dbt/manifest/v12.json). The dbt documentation maps Core 1.8–1.11 to manifest v12 and cautions that dbt and manifest versions are related but distinct.
