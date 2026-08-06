# ADR 0042: Expose team operations as a content-free administrator snapshot

## Status

Accepted on 2026-08-06.

## Context

The team service now has durable jobs, workspace checkpoint quotas, migrations, backups, and restore
drills, but operators cannot see whether those controls need attention without writing payload-
capable database queries. A public dashboard or metrics listener would add another authenticated
network surface before the team release audit.

## Decision

The installed `mnemo-memory-team-admin` command provides `status` and `check` over the existing
dedicated backup/operations credential. One read-only PostgreSQL query returns only whole-team
aggregate counts: schema version, workspace/project/active-membership totals, checkpoint and quota
coverage/utilization, and pending, leased, expired-lease, failed, and oldest durable-job state. It
never materializes a tenant identity, job body, source path, or canonical payload outside the
database.

Operators configure strict quota-utilization, backlog, age, and failure thresholds per invocation.
The snapshot returns stable content-free alert codes. `status` always exits successfully after a
valid snapshot; `check` exits 0 when healthy, 1 when an alert is active, and 2 when configuration,
secret loading, or storage fails. Notification delivery remains the supervisor's responsibility.

## Consequences

- A scheduler or monitoring agent can consume bounded canonical JSON without database-specific
  queries or access to the MCP runtime credential.
- The runtime role cannot execute the whole-team query; the operator credential is already a
  sensitive `BYPASSRLS` backup role and must retain its existing controls.
- Counts are a point-in-time observation, not a durable metrics history or per-tenant analytics
  surface.
- No public endpoint, notification transport, background scheduler, or dependency is added.

## Security and privacy

Only aggregate non-negative counters, UTC observation time, supported/current schema versions, and
closed alert codes leave PostgreSQL. Database errors become `MNEMO_TEAM_OPERATIONS_UNAVAILABLE`.
The existing owner-only password file, verified TLS, and content-free logging rules apply.

## Token and cost

The snapshot makes no model call and adds no agent-context tokens. Its checkpoint payload measure
uses byte lengths inside PostgreSQL; payload values are never returned to Python.

## Dependencies and originality

The implementation is original Mnemo standard-library and PostgreSQL code and adds no dependency.

## Reversal and recovery

Removing the two commands does not change durable data. An alert is recovered through the existing
documented control: provision a quota, process or repair jobs, adjust a measured threshold, or
upgrade the schema. Storage failure leaves the database unchanged.

## Verification

Unit tests cover threshold validation, deterministic alerts, content-free serialization, CLI exit
codes, and payload-free failure. The mandatory real-PostgreSQL test creates actual checkpoint and
outbox state, validates quota/backlog alerts and identity-free output, and proves the runtime
credential cannot execute the snapshot.
