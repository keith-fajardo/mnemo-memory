# ADR 0041: Enforce team checkpoint quotas inside PostgreSQL

## Status

Accepted on 2026-08-06.

## Context

OAuth, forced row-level security, and request-rate limits do not bound durable storage consumed by
an authorized workspace. The current team agent surface mutates canonical content only through
checkpoint aggregate and revision writes. Admission must remain correct under concurrent service
requests and must not leave checkpoint, evidence, lifecycle, or outbox fragments after denial.

## Decision

Each workspace requires an explicit schema-administrator quota containing positive limits for
checkpoint aggregates, retained revisions, and retained canonical payload bytes. PostgreSQL
`BEFORE INSERT` triggers acquire the exact workspace quota row `FOR UPDATE`, calculate usage only
for that workspace, and reject an over-limit or unprovisioned write with private SQLSTATE `MZQ01`.
The revision byte measure is the UTF-8 size of PostgreSQL's canonical JSONB text for checkpoint
content and evidence.

The runtime role has no direct access to quota rows. Fixed-search-path security-definer functions
perform admission after confirming the transaction's exact workspace. The repository translates
`MZQ01` into a typed storage error and the authenticated MCP boundary returns only
`MNEMO_QUOTA_EXCEEDED`. Reads remain available, and idempotent retries that insert no row consume no
additional quota.

## Consequences

- Concurrent admissions for one workspace serialize on one small quota row and cannot overshoot.
- Existing workspaces fail closed for new checkpoint writes until an administrator provisions a
  quota; migrations do not guess capacity from current data.
- Lowering a limit below current usage preserves existing data and blocks additional affected
  writes until usage or the configured limit changes.
- This bounds the current checkpoint mutation surface only. Model budgets, database-wide capacity,
  dashboards, alerts, and future ingestion surfaces remain separate issues.

## Security and privacy

Quota checks execute in the same transaction as canonical mutation, use exact workspace predicates,
and expose no usage or tenant identifier on denial. The quota table is inaccessible to the runtime
role and `PUBLIC`; only the trusted schema administrator can provision limits.

## Token and cost

Quota enforcement makes no model call and adds no context tokens. Each admitted revision performs
one workspace-scoped aggregate over retained revision payloads; load testing must validate its
production latency before declaring a team SLO.

## Dependencies and originality

The implementation is original Mnemo PostgreSQL and standard-library code and adds no dependency.

## Reversal and recovery

Migration 0022 is forward-only. Recovery is to provision or raise the exact workspace quota and
retry the rejected operation. Existing canonical rows need no repair because denial rolls back the
entire transaction. Removing enforcement requires a reviewed later migration that drops both
triggers and functions while retaining or explicitly migrating administrator quota records.

## Verification

The mandatory real-PostgreSQL suite covers an unprovisioned workspace, atomic rollback, runtime
privilege denial, two concurrent aggregate admissions with exactly one winner, aggregate and
revision count limits, payload-byte limits, successful writes after administrative adjustment, and
idempotent terminal replay.
