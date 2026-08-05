# Unified context benchmark

Run the deterministic, offline fixture with:

```bash
npm run eval:unified-context -- --json
```

It compares task-prompt-only, full transcript, full synthetic dbt manifest, and Mnemo's compact
checkpoint plus evidenced lineage facts. Token values are deterministic cold-input estimates, not
provider-billed tokens; no model, API key, warehouse, dbt execution, or prompt-cache assumption is
used. The fixture gates required facts, direct/transitive lineage, provenance, currentness, scope,
and the 600/1,500/5,700 token budgets. It is an information-availability proof, not a live model
quality benchmark.

For local use, initialize Mnemo, connect either client, run `mnemo dbt ingest MANIFEST` with explicit
scope IDs, save a checkpoint, then request `get_context` with an optional structured `dbt_lineage`
object. The server also exposes content-free `explain_context` and mutating `save_checkpoint`. Raw
SQL, manifest bodies, warehouse
credentials, dbt execution, file watching, catalog/run-results artifacts, automatic transcript
capture, general code graphs, embeddings, UI, and team support remain out of scope.
