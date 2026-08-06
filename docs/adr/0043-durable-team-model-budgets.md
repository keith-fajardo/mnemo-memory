# ADR 0043: Reserve worst-case team model cost before provider invocation

## Status

Accepted on 2026-08-06.

## Context

Mnemo's optional episodic-candidate extraction gateway may retry one malformed provider response.
Request rate limits and storage quotas do not bound provider calls, input/output tokens, or money,
and an application-only daily counter can overshoot under concurrent workers or reset on restart.
Provider-reported usage arrives too late to prevent an over-budget call.

## Decision

Every workspace must have an explicit administrator-provisioned daily budget for each enabled model
task. The first task is `episodic_candidate_extraction`. A trusted composition supplies a
worst-case per-attempt reservation containing positive input/output tokens and non-negative
micro-USD. The schema-bound gateway reserves that charge before each provider invocation, including
its single malformed-output retry. Denial and budget-storage failure prevent the call.

PostgreSQL defines the UTC usage day, locks the exact workspace/task budget row, and atomically
increments call, input-token, output-token, and monetary counters only when all four remain within
their independent maxima. An unprovisioned or exhausted budget fails closed. A fixed-search-path
security-definer function verifies the transaction's exact workspace, authenticated active
membership, and `contribute` authority. The runtime role can execute that function but cannot read
or mutate budget tables directly.

Attempt reservations are deliberately conservative and are not refunded after provider failure.
Mnemo does not trust provider output to authorize more spend and does not discover pricing. The
administrator must set the reservation at or above the provider adapter's configured maximum call
cost.

## Consequences

- Concurrent workers cannot overshoot a workspace's declared daily maxima.
- A malformed-output retry consumes a second reservation, making the existing one-retry behavior
  visible in capacity planning.
- Local or zero-price providers may use a zero monetary reservation and budget while calls and
  tokens remain bounded.
- Daily usage creates at most one row per workspace/task/day; retention of historical operations
  accounting requires a later explicit policy.
- No provider SDK, endpoint, worker, MCP tool, billing engine, or dynamic price service is added.

## Security and privacy

The provider receives only the existing minimized extraction request and never receives workspace,
budget, usage, or price data. Denials expose only `MNEMO_MODEL_BUDGET_EXCEEDED` or
`MNEMO_MODEL_BUDGET_UNAVAILABLE`. Scope or membership mismatch uses the same payload-free denial.

## Token and cost

The control itself makes no model call. It prevents a call unless its complete configured
worst-case token and monetary reservation fits all remaining daily limits.

## Dependencies and originality

The implementation is original Mnemo domain, gateway, and PostgreSQL code and adds no dependency.

## Reversal and recovery

Migration 0023 is forward-only. An administrator can provision or raise the exact budget and retry;
UTC-day rollover creates a new usage row automatically. Existing usage is never reduced by changing
a limit. Removing enforcement requires a reviewed later migration and a replacement mandatory
gateway budget port.

## Verification

Gateway tests prove reservation before call, one charge per attempt, retry charging, and denial
before provider invocation. The mandatory real-PostgreSQL suite covers absent budgets, runtime
table privilege denial, exact UTC-day accounting, three concurrent reservations with two winners,
all four accumulated limits, inactive/foreign scope denial, and content-free operator alerts.
