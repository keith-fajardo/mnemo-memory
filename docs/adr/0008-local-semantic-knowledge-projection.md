# ADR 0008: Optional local semantic knowledge is a rebuildable, scoped projection

## Status

Accepted for personal-mode semantic retrieval.

## Context

Literal full-text search is predictable but cannot find a note when a user asks with different
words. Sending private project notes to a hosted embedding API would violate Mnemo's local-first
promise and would make ordinary retrieval depend on provider availability.

## Decision

Semantic note retrieval is an **optional local-only** extra. A user installs the `semantic` extra
and explicitly runs `mnemo-memory memory semantic index` for an already enabled project. Mnemo
then uses the FastEmbed local ONNX adapter with `BAAI/bge-small-en-v1.5`; the adapter may obtain
that public model's weights on its first explicit use, but document and query text stay on the
local machine.

The canonical document revision remains the only stored note body. SQLite stores a scoped,
rebuildable projection containing a model identifier, section digest, finite vector, and revision
identity—never note text, raw SQL, environment variables, or credentials. It is queried only with
the complete project scope and only for current document revisions. A revision replacement or a
tombstone therefore cannot be returned as current semantic evidence.

MCP callers opt in per request with `semantic_knowledge_query`. Semantic results are still
untrusted note evidence with the same immutable revision provenance and token budget as literal
knowledge results. They never override current dbt or source-structure facts.

## Consequences

Normal Mnemo installation and automatic session context do not initialize the embedding runtime,
download model weights, or send any text to a model provider. The explicit local index is
idempotent: unchanged current sections reuse their stored vector. Removing/tombstoning a document
removes its projections through foreign keys. A future provider or model replacement must use the
same scope, projection, deletion, and provenance contract.
