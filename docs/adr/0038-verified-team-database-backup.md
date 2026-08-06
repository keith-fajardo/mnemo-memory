# ADR 0038: Verify team database backups with isolated restore drills

## Status

Accepted on 2026-08-06.

## Context

The team profile stores canonical, projection, governance, audit, outbox, tombstone, and migration
state behind forced PostgreSQL row-level security. A normal MCP runtime principal must never bypass
that isolation, but a recoverable database backup must include every tenant in one coherent
snapshot. A copied archive is also a durable sensitive-data surface that cannot be treated as an
ordinary diagnostic artifact.

## Decision

An operator invokes a separate installed administration command. It uses an independently
provisioned login that is a non-superuser with `BYPASSRLS` and read-all-data authority; the online
MCP runtime remains non-`BYPASSRLS`. One repeatable-read transaction exports a snapshot, inventories
the complete `mnemo_team` migration ledger and sorted table counts, and keeps that transaction open
while PostgreSQL 17.10 `pg_dump` creates a custom archive from the same snapshot.

The archive is validated before atomic publication in an owner-controlled directory. A canonical
content-free manifest binds its SHA-256 digest, byte size, schema version, and every table count.
The password is read from an owner-only file and exposed to native clients only in a temporary
mode-`0600` passfile. Database connections require certificate and hostname verification.

A restore drill targets an explicitly provisioned database other than the source. The approved
`vector` extension must already exist because it is infrastructure outside `mnemo_team`. The target
must not contain the Mnemo schema. Restore uses one transaction and succeeds only when the target
ledger and every table count exactly match the manifest.

## Consequences

- Operators can prove that a whole-team backup is coherent and restorable without granting backup
  authority to the agent-facing runtime.
- Results and failures are bounded metadata and never return database payloads or native command
  output.
- Archives remain sensitive and require operator-provided encrypted storage and access control.
- Scheduling, remote storage, retention pruning, key management, and deletion-triggered backup
  rotation remain separate work.

## Security and privacy

Private modes, no-follow secret reads, non-overwriting atomic publication, digest validation,
single-transaction restore, live/nonempty-target rejection, and full inventory comparison prevent
the principal local substitution and partial-recovery failures. `BYPASSRLS` is restricted to the
offline backup identity and is not an application authorization mechanism.

## Token and cost

This workflow makes no model or embedding call and contributes no context tokens. Its cost is
operator-selected PostgreSQL I/O and retained encrypted storage.

## Dependencies and originality

The adapter and manifest are original Mnemo work. It invokes reviewed PostgreSQL 17.10 client tools
under the PostgreSQL License; the dependency register records the operational boundary. No Python
package was added.

## Reversal and recovery

Removing the command does not modify a live database or existing archives. Restore never targets
the source database. A failed native restore rolls back its transaction; an operator drops and
recreates the isolated drill database before retry. Archive format migration must retain an
explicit versioned manifest reader or require a documented PostgreSQL-native recovery path.

## Verification

Focused tests cover canonical manifests, tampering, private files, partial cleanup, role checks,
TLS and passfile command boundaries, live/nonempty targets, and inventory mismatch. The mandatory
real-PostgreSQL suite runs native dump and restore and verifies exact schema-ledger and table-count
parity.
