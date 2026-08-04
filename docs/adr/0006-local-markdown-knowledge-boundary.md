# ADR 0006: Local Markdown knowledge is untrusted, scoped source evidence

## Status

Accepted for the first personal-knowledge foundation slice.

## Context

Checkpoints preserve a task handoff and source snapshots preserve static structure, but people
also keep project decisions, runbooks, meeting notes, and research in ordinary Markdown or an
Obsidian vault. Replaying a whole vault into an agent wastes context and lets arbitrary note text
masquerade as instructions.

## Decision

Mnemo's first knowledge boundary is a deterministic parser for one caller-provided Markdown byte
or string input. It requires an explicit Mnemo scope and a safe relative source identity. It:

- records a stable path-scoped document identity, a SHA-256 content digest, simple scalar
  frontmatter, headings, bounded literal sections, and declared Markdown/Obsidian links;
- marks every parsed document and section as **untrusted data**; document text cannot become a
  tool instruction, policy decision, or active memory merely by being parsed;
- accepts no absolute source path, follows no links, reads no filesystem, executes no Markdown or
  frontmatter, and makes no network or model call; and
- applies byte, section, link, frontmatter, and section-size limits before any later storage or
  context decision.

The parser uses only the Python standard library. It intentionally supports only a narrow scalar
frontmatter form in this foundation slice; nested YAML is rejected rather than interpreted by an
implicit YAML implementation.

## Consequences

This establishes a safe, testable input contract for a later filesystem/Obsidian connector. It is
not yet vault discovery, durable storage, deletion propagation, lexical search, embeddings, or
automatic context retrieval. Those later layers must add secret policy, source registration,
content-hash incremental sync, rename/tombstone handling, explicit user consent, and cited,
budgeted retrieval without weakening this untrusted-data boundary.
