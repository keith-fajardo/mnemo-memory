# ADR 0030: Knowledge transfer keeps retained revisions and never rebuilds deleted notes

- **Status:** accepted
- **Date:** 2026-08-06
- **Deciders:** Mnemo maintainers
- **Issue:** 21V
- **Supersedes:** none
- **Superseded by:** none

## Context

Personal knowledge storage retains complete immutable revision payloads only for active Markdown and
Obsidian sources. Deletion intentionally removes every title, frontmatter field, section, link, and
revision, leaving only source identity, relative path, content digest, and deletion time. Document
identity includes scope, while revision identity does not. Replaying through ordinary sync cannot
recreate a deleted source without manufacturing payload and revision metadata that no longer exist.

## Decision

`mnemo.knowledge-export.v1` contains active source pointers, every retained immutable revision, and
minimal payload-free deletion records for one exact project scope. It retains source kind, path,
content digest, title, ordered frontmatter, ordered sections, declared Markdown/Obsidian links,
revision identity, predecessor, revision number, timestamp, and last successful sync time. Stable
ordering, contiguous predecessor validation, current-pointer binding, payload-erasure validation,
canonical UTF-8 JSON, and one SHA-256 digest make partial or modified history fail closed.

Import deterministically maps each source document identity from retained source identity and the
explicit target scope. Revision identities and predecessor links remain unchanged. PostgreSQL uses
the native source, revision, section, and link tables for active history and records source identity,
source bundle digest, and import time on the active source. Already-deleted sources go into one
forced-RLS payload-free projection; no placeholder source row, creation time, revision, title,
frontmatter, section, or link is created. A database trigger prevents that imported deletion
identity from being inserted as a live source.

The target project must have no prior knowledge sync or be exactly identical. PostgreSQL validates
the complete source-to-target projection, writes active history, deletion projections, provenance,
and sync state in one transaction, reconstructs the target bundle before commit, and makes exact
retry idempotent. Search and embedding indexes are rebuildable target projections and are not
portable payload.

## Alternatives considered

- **Replay normal sync operations.** Rejected because scope-derived document IDs would differ and
  an erased note has no valid revision to replay.
- **Create a deleted native source with `created_at = deleted_at`.** Rejected because that would
  manufacture source history.
- **Export embeddings.** Rejected because embeddings are replaceable projections, not canonical
  knowledge or evidence.
- **Export only current note revisions.** Rejected because retained immutable revision history and
  predecessor provenance would be lost.

## Consequences

Active Markdown and Obsidian knowledge moves with exact retained history, links, and sync
provenance. Deleted note content remains physically absent while its minimal deletion state blocks
accidental resurrection. Source and target hashes differ because project scope and document IDs are
rebased. Target search and semantic indexes can be rebuilt from canonical active revisions.

## Security and privacy implications

SQLite export selects exact project scope before parsing rows. Team export and import set the bound
principal, workspace, and operation before forced-RLS queries or writes. Private viewers receive an
empty bundle and cannot import. Import provenance and deletion projection rows are insert-only for
the runtime role. Document text remains untrusted evidence and is never interpreted as an
instruction during transfer.

## Token and cost implications

No model, embedding, network, or provider call is added. Reusing compact indexed knowledge avoids
re-reading full note collections after migration; target embeddings are generated only by the
separate explicit indexing workflow.

## Dependency and licensing implications

No dependency is added. The format, migration, validation, services, adapter code, and fixtures are
original Mnemo work.

## Reversal or migration strategy

PostgreSQL schema version 17 is forward-only. Migration failure rolls back to version 16. Recovery
after a committed migration uses a database backup or a reviewed forward migration. Imported
active rows are ordinary canonical knowledge; imported deletion projections remain minimal and
must participate in the future deletion/backup propagation workflow.

## Verification

- Domain and Reference tests cover canonical JSON, tampering, revision chains, renamed paths,
  rebasing, conflict rejection, deletion minimization, and replay.
- SQLite tests prove full retained history and deletion state after restart.
- Real PostgreSQL tests prove v16-to-v17 rollback/retry, SQLite transfer, restart hashes, native
  retrieval/search, provenance, absent embeddings/deleted payload, anti-resurrection, replay, and
  private-viewer denial.

## References

- `docs/implementation-plan.md`, Milestones 6 and 9
- `docs/adr/0029-approved-event-history-transfer.md`
- `docs/product-memory-contract.md`
- `docs/threat-model.md`
