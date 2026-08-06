# ADR 0039: Reconcile managed team backups against erasure ledgers

## Status

Accepted on 2026-08-06.

## Context

A verified backup can retain payload that a later canonical deletion or retention purge removes
from the live team database. Mnemo must propagate deletion to copies it controls without deleting a
current recovery point after an unrelated write or ordinary correction. It cannot discover copies
an operator moved outside a submitted backup directory.

## Decision

Team backup manifest version 2 adds a sorted content-free count for every monotonic PostgreSQL
erasure ledger. Governance tables that contain both corrections and retractions count only exact
retractions. All counts come from the same exported snapshot as the archive and full table
inventory.

An explicit installed command scans one private directory with strict names and hard entry bounds,
validates every candidate, and obtains one current inventory through the dedicated backup role. A
backup is stale when any current erasure count exceeds its manifest count. A lower current count is
a state regression and fails closed. Version-1 manifests have no exact watermark and are stale
whenever the current database has any erasure.

Deletion is archive-first, directory-fsync, manifest-second, directory-fsync. This ordering means
an interruption cannot leave a manifest claiming a deleted payload-bearing archive is valid, and a
retry may safely clean its orphaned stale manifest. Every candidate is validated before the first
valid archive is removed.

## Consequences

- Canonical deletions and retention purges can be reconciled with local backup directories without
  expiring backups after ordinary corrections.
- Current verified backups remain byte-identical.
- Operators must invoke reconciliation for every controlled directory and separately govern every
  external copy.
- Scheduling, age-based retention, object-store integration, and cryptographic key destruction
  remain separate work.

## Security and privacy

Manifest and archive ownership, private modes, names, digest, structure, and directory bounds are
validated before deletion. Symlinks, missing current archives, malformed candidates, and erasure
regression fail closed. Results contain only removed backup, file, and byte counts.

## Token and cost

Reconciliation makes no model or embedding call and contributes no context tokens. Its cost is one
bounded directory scan, native archive validation, one database inventory, and deletion I/O.

## Dependencies and originality

The versioned watermark and reconciliation workflow are original Mnemo work using the existing
standard library, PostgreSQL client, and team backup boundaries. No dependency is added.

## Reversal and recovery

Deleted archive payload is intentionally unrecoverable from the reconciled directory. An operator
must retain at least one post-erasure current backup before pruning if recovery policy requires it.
A failed partial operation is retried with the same command. Version-1 reading remains supported;
future manifest changes require another explicit version reader.

## Verification

Focused tests cover exact stale/current selection, v1 compatibility, interruption retry, strict
names, unsafe symlinks, malformed candidates, count regression, unchanged current bytes, and exact
retry. The mandatory real-PostgreSQL drill backs up live knowledge payload, canonically tombstones
and erases it, creates a current backup, prunes only the payload-bearing archive, and retains the
current archive.
