"""Content-free whole-team operational snapshot for trusted administrators."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime

from .postgres import POSTGRES_TEAM_SCHEMA_VERSION, PostgreSQLConnectionFactory


class TeamOperationsStorageFailure(RuntimeError):
    """Stable payload-free failure at the operator storage boundary."""


@dataclass(frozen=True, slots=True)
class TeamOperationsThresholds:
    quota_warning_percent: int = 90
    model_budget_warning_percent: int = 90
    pending_jobs: int = 1_000
    pending_job_age_seconds: int = 300
    failed_jobs: int = 0

    def __post_init__(self) -> None:
        for percent in (self.quota_warning_percent, self.model_budget_warning_percent):
            if isinstance(percent, bool) or not 1 <= percent <= 100:
                raise ValueError("warning percentages must be between 1 and 100")
        for item in (self.pending_jobs, self.pending_job_age_seconds, self.failed_jobs):
            if (
                isinstance(item, bool)
                or not isinstance(item, int)
                or not 0 <= item <= 1_000_000_000
            ):
                raise ValueError("team operations thresholds must be bounded non-negative integers")


@dataclass(frozen=True, slots=True)
class TeamOperationsSnapshot:
    observed_at: datetime
    schema_version: int
    workspace_count: int
    project_count: int
    active_workspace_membership_count: int
    checkpoint_aggregate_count: int
    checkpoint_revision_count: int
    checkpoint_payload_bytes: int
    quota_configured_workspace_count: int
    quota_missing_workspace_count: int
    quota_warning_workspace_count: int
    quota_exceeded_workspace_count: int
    maximum_quota_utilization_percent: int
    model_budget_configured_workspace_count: int
    model_budget_missing_workspace_count: int
    model_budget_warning_workspace_count: int
    model_budget_exhausted_workspace_count: int
    maximum_model_budget_utilization_percent: int
    pending_job_count: int
    active_lease_job_count: int
    expired_lease_job_count: int
    failed_job_count: int
    oldest_pending_job_age_seconds: int
    alerts: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.observed_at, datetime) or self.observed_at.tzinfo is None:
            raise ValueError("team operations observation time must be timezone-aware")
        for field in fields(self):
            if field.name in {"observed_at", "alerts"}:
                continue
            value = getattr(self, field.name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("team operations counters must be non-negative integers")
        if not isinstance(self.alerts, tuple) or any(
            not isinstance(item, str) or not item.startswith("MNEMO_TEAM_") for item in self.alerts
        ):
            raise ValueError("team operations alerts are invalid")

    @property
    def healthy(self) -> bool:
        return not self.alerts

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "supported_schema_version": POSTGRES_TEAM_SCHEMA_VERSION,
            "observed_at": self.observed_at.isoformat(),
            "healthy": self.healthy,
            "alerts": list(self.alerts),
            "counts": {
                "workspaces": self.workspace_count,
                "projects": self.project_count,
                "active_workspace_memberships": self.active_workspace_membership_count,
                "checkpoint_aggregates": self.checkpoint_aggregate_count,
                "checkpoint_revisions": self.checkpoint_revision_count,
                "checkpoint_payload_bytes": self.checkpoint_payload_bytes,
                "quota_configured_workspaces": self.quota_configured_workspace_count,
                "quota_missing_workspaces": self.quota_missing_workspace_count,
                "quota_warning_workspaces": self.quota_warning_workspace_count,
                "quota_exceeded_workspaces": self.quota_exceeded_workspace_count,
                "model_budget_configured_workspaces": (
                    self.model_budget_configured_workspace_count
                ),
                "model_budget_missing_workspaces": self.model_budget_missing_workspace_count,
                "model_budget_warning_workspaces": self.model_budget_warning_workspace_count,
                "model_budget_exhausted_workspaces": self.model_budget_exhausted_workspace_count,
                "pending_jobs": self.pending_job_count,
                "active_lease_jobs": self.active_lease_job_count,
                "expired_lease_jobs": self.expired_lease_job_count,
                "failed_jobs": self.failed_job_count,
            },
            "maximum_quota_utilization_percent": self.maximum_quota_utilization_percent,
            "maximum_model_budget_utilization_percent": (
                self.maximum_model_budget_utilization_percent
            ),
            "oldest_pending_job_age_seconds": self.oldest_pending_job_age_seconds,
        }


class PostgreSQLTeamOperationsRepository:
    """Read one minimized whole-team snapshot through a trusted operator connection."""

    def __init__(self, connection_factory: PostgreSQLConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def snapshot(self, thresholds: TeamOperationsThresholds) -> TeamOperationsSnapshot:
        if not isinstance(thresholds, TeamOperationsThresholds):
            raise TypeError("team operations thresholds are invalid")
        connection = self._connection_factory()
        connection.autocommit = False
        cursor = connection.cursor()
        try:
            cursor.execute(
                _SNAPSHOT_SQL,
                (thresholds.quota_warning_percent, thresholds.model_budget_warning_percent),
            )
            row = cursor.fetchone()
            if row is None or len(row) != 23:
                raise TeamOperationsStorageFailure("MNEMO_TEAM_OPERATIONS_UNAVAILABLE")
            values = tuple(row)
            observed_at = values[0]
            if not isinstance(observed_at, datetime):
                raise TeamOperationsStorageFailure("MNEMO_TEAM_OPERATIONS_UNAVAILABLE")
            counters = tuple(_counter(value) for value in values[1:])
            alerts = _alerts(counters, thresholds)
            return TeamOperationsSnapshot(
                observed_at,
                counters[0],
                counters[1],
                counters[2],
                counters[3],
                counters[4],
                counters[5],
                counters[6],
                counters[7],
                counters[8],
                counters[9],
                counters[10],
                counters[11],
                counters[12],
                counters[13],
                counters[14],
                counters[15],
                counters[16],
                counters[17],
                counters[18],
                counters[19],
                counters[20],
                counters[21],
                alerts,
            )
        except TeamOperationsStorageFailure:
            raise
        except Exception as error:
            raise TeamOperationsStorageFailure("MNEMO_TEAM_OPERATIONS_UNAVAILABLE") from error
        finally:
            connection.rollback()
            cursor.close()
            connection.close()


def _alerts(counters: tuple[int, ...], thresholds: TeamOperationsThresholds) -> tuple[str, ...]:
    (
        schema_version,
        _workspace_count,
        _project_count,
        _active_memberships,
        _aggregate_count,
        _revision_count,
        _payload_bytes,
        _quota_configured,
        quota_missing,
        quota_warning,
        quota_exceeded,
        _maximum_utilization,
        _model_budget_configured,
        model_budget_missing,
        model_budget_warning,
        model_budget_exhausted,
        _maximum_model_budget_utilization,
        pending_jobs,
        _active_leases,
        expired_leases,
        failed_jobs,
        oldest_pending_age,
    ) = counters
    values: list[str] = []
    if schema_version != POSTGRES_TEAM_SCHEMA_VERSION:
        values.append("MNEMO_TEAM_SCHEMA_VERSION_MISMATCH")
    if quota_missing:
        values.append("MNEMO_TEAM_CHECKPOINT_QUOTA_MISSING")
    if quota_exceeded:
        values.append("MNEMO_TEAM_CHECKPOINT_QUOTA_EXCEEDED")
    if quota_warning:
        values.append("MNEMO_TEAM_CHECKPOINT_QUOTA_HIGH")
    if model_budget_missing:
        values.append("MNEMO_TEAM_MODEL_BUDGET_MISSING")
    if model_budget_exhausted:
        values.append("MNEMO_TEAM_MODEL_BUDGET_EXHAUSTED")
    if model_budget_warning:
        values.append("MNEMO_TEAM_MODEL_BUDGET_HIGH")
    if pending_jobs > thresholds.pending_jobs:
        values.append("MNEMO_TEAM_OUTBOX_BACKLOG_HIGH")
    if oldest_pending_age > thresholds.pending_job_age_seconds:
        values.append("MNEMO_TEAM_OUTBOX_AGE_HIGH")
    if expired_leases:
        values.append("MNEMO_TEAM_OUTBOX_LEASE_EXPIRED")
    if failed_jobs > thresholds.failed_jobs:
        values.append("MNEMO_TEAM_OUTBOX_FAILURES_HIGH")
    return tuple(values)


def _counter(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TeamOperationsStorageFailure("MNEMO_TEAM_OPERATIONS_UNAVAILABLE")
    return value


_SNAPSHOT_SQL = """
WITH aggregate_usage AS (
    SELECT workspace_id, count(*) AS aggregate_count
      FROM mnemo_team.checkpoint_aggregates
     GROUP BY workspace_id
), revision_usage AS (
    SELECT workspace_id,
           count(*) AS revision_count,
           coalesce(sum(octet_length(content_json::text) + octet_length(evidence_json::text)), 0)
               AS payload_bytes
      FROM mnemo_team.checkpoint_revisions
     GROUP BY workspace_id
), quota_state AS (
    SELECT workspace.workspace_id,
           quota.workspace_id AS configured_workspace_id,
           quota.max_aggregate_count,
           quota.max_revision_count,
           quota.max_payload_bytes,
           coalesce(aggregate_usage.aggregate_count, 0) AS aggregate_count,
           coalesce(revision_usage.revision_count, 0) AS revision_count,
           coalesce(revision_usage.payload_bytes, 0) AS payload_bytes
      FROM mnemo_team.workspaces AS workspace
      LEFT JOIN mnemo_team.workspace_checkpoint_quotas AS quota
        ON quota.workspace_id = workspace.workspace_id
      LEFT JOIN aggregate_usage ON aggregate_usage.workspace_id = workspace.workspace_id
      LEFT JOIN revision_usage ON revision_usage.workspace_id = workspace.workspace_id
), quota_summary AS (
    SELECT coalesce(sum(aggregate_count), 0)::bigint AS aggregate_count,
           coalesce(sum(revision_count), 0)::bigint AS revision_count,
           coalesce(sum(payload_bytes), 0)::bigint AS payload_bytes,
           count(*) FILTER (WHERE configured_workspace_id IS NOT NULL)::bigint AS configured_count,
           count(*) FILTER (WHERE configured_workspace_id IS NULL)::bigint AS missing_count,
           count(*) FILTER (
               WHERE configured_workspace_id IS NOT NULL
                 AND NOT (
                     aggregate_count > max_aggregate_count
                     OR revision_count > max_revision_count
                     OR payload_bytes > max_payload_bytes
                 )
                 AND greatest(
                     aggregate_count::numeric * 100 / max_aggregate_count,
                     revision_count::numeric * 100 / max_revision_count,
                     payload_bytes::numeric * 100 / max_payload_bytes
                 ) >= %s
           )::bigint AS warning_count,
           count(*) FILTER (
               WHERE configured_workspace_id IS NOT NULL
                 AND (
                     aggregate_count > max_aggregate_count
                     OR revision_count > max_revision_count
                     OR payload_bytes > max_payload_bytes
                 )
           )::bigint AS exceeded_count,
           coalesce(max(
               CASE WHEN configured_workspace_id IS NULL THEN 0 ELSE greatest(
                   aggregate_count::numeric * 100 / max_aggregate_count,
                   revision_count::numeric * 100 / max_revision_count,
                   payload_bytes::numeric * 100 / max_payload_bytes
               ) END
           ), 0)::bigint AS maximum_utilization
      FROM quota_state
), model_budget_state AS (
    SELECT workspace.workspace_id,
           budget.workspace_id AS configured_workspace_id,
           budget.max_call_count,
           budget.max_input_tokens,
           budget.max_output_tokens,
           budget.max_cost_microusd,
           coalesce(usage.used_call_count, 0) AS used_call_count,
           coalesce(usage.used_input_tokens, 0) AS used_input_tokens,
           coalesce(usage.used_output_tokens, 0) AS used_output_tokens,
           coalesce(usage.used_cost_microusd, 0) AS used_cost_microusd
      FROM mnemo_team.workspaces AS workspace
      LEFT JOIN mnemo_team.workspace_model_budgets AS budget
        ON budget.workspace_id = workspace.workspace_id
       AND budget.task_type = 'episodic_candidate_extraction'
      LEFT JOIN mnemo_team.workspace_model_budget_usage AS usage
        ON usage.workspace_id = workspace.workspace_id
       AND usage.task_type = 'episodic_candidate_extraction'
       AND usage.usage_date = (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date
), model_budget_summary AS (
    SELECT count(*) FILTER (WHERE configured_workspace_id IS NOT NULL)::bigint
               AS configured_count,
           count(*) FILTER (WHERE configured_workspace_id IS NULL)::bigint AS missing_count,
           count(*) FILTER (
               WHERE configured_workspace_id IS NOT NULL
                 AND NOT (
                     used_call_count >= max_call_count
                     OR used_input_tokens >= max_input_tokens
                     OR used_output_tokens >= max_output_tokens
                     OR (max_cost_microusd > 0 AND used_cost_microusd >= max_cost_microusd)
                 )
                 AND greatest(
                     used_call_count::numeric * 100 / max_call_count,
                     used_input_tokens::numeric * 100 / max_input_tokens,
                     used_output_tokens::numeric * 100 / max_output_tokens,
                     CASE WHEN max_cost_microusd = 0 THEN 0
                          ELSE used_cost_microusd::numeric * 100 / max_cost_microusd END
                 ) >= %s
           )::bigint AS warning_count,
           count(*) FILTER (
               WHERE configured_workspace_id IS NOT NULL
                 AND (
                     used_call_count >= max_call_count
                     OR used_input_tokens >= max_input_tokens
                     OR used_output_tokens >= max_output_tokens
                     OR (max_cost_microusd > 0 AND used_cost_microusd >= max_cost_microusd)
                 )
           )::bigint AS exhausted_count,
           coalesce(max(
               CASE WHEN configured_workspace_id IS NULL THEN 0 ELSE greatest(
                   used_call_count::numeric * 100 / max_call_count,
                   used_input_tokens::numeric * 100 / max_input_tokens,
                   used_output_tokens::numeric * 100 / max_output_tokens,
                   CASE WHEN max_cost_microusd = 0 THEN 0
                        ELSE used_cost_microusd::numeric * 100 / max_cost_microusd END
               ) END
           ), 0)::bigint AS maximum_utilization
      FROM model_budget_state
), outbox_summary AS (
    SELECT count(*) FILTER (WHERE completed_at IS NULL)::bigint AS pending_count,
           count(*) FILTER (
               WHERE completed_at IS NULL AND lease_expires_at > CURRENT_TIMESTAMP
           )::bigint AS active_lease_count,
           count(*) FILTER (
               WHERE completed_at IS NULL AND lease_expires_at <= CURRENT_TIMESTAMP
           )::bigint AS expired_lease_count,
           count(*) FILTER (
               WHERE completed_at IS NULL AND last_failure_code IS NOT NULL
           )::bigint AS failed_count,
           greatest(coalesce(floor(extract(epoch FROM (
               CURRENT_TIMESTAMP - min(created_at) FILTER (WHERE completed_at IS NULL)
           ))), 0), 0)::bigint AS oldest_pending_age
      FROM mnemo_team.event_outbox
)
SELECT CURRENT_TIMESTAMP,
       coalesce((SELECT max(version) FROM mnemo_team.schema_migrations), 0)::bigint,
       (SELECT count(*) FROM mnemo_team.workspaces)::bigint,
       (SELECT count(*) FROM mnemo_team.projects)::bigint,
       (SELECT count(*) FROM mnemo_team.workspace_memberships WHERE status = 'active')::bigint,
       quota_summary.aggregate_count,
       quota_summary.revision_count,
       quota_summary.payload_bytes,
       quota_summary.configured_count,
       quota_summary.missing_count,
       quota_summary.warning_count,
       quota_summary.exceeded_count,
       quota_summary.maximum_utilization,
       model_budget_summary.configured_count,
       model_budget_summary.missing_count,
       model_budget_summary.warning_count,
       model_budget_summary.exhausted_count,
       model_budget_summary.maximum_utilization,
       outbox_summary.pending_count,
       outbox_summary.active_lease_count,
       outbox_summary.expired_lease_count,
       outbox_summary.failed_count,
       outbox_summary.oldest_pending_age
  FROM quota_summary CROSS JOIN model_budget_summary CROSS JOIN outbox_summary
"""
