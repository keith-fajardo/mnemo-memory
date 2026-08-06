"""Content-free team operations snapshot and alert contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

import mnemo_memory.apps.cli.team_admin as team_admin
from mnemo_memory.packages.storage import (
    PostgreSQLConnection,
    PostgreSQLTeamOperationsRepository,
    TeamOperationsSnapshot,
    TeamOperationsStorageFailure,
    TeamOperationsThresholds,
)

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


class _Cursor:
    rowcount = 1

    def __init__(self, row: tuple[object, ...] | None) -> None:
        self.row = row
        self.operation = ""
        self.args: tuple[object, ...] = ()
        self.closed = False

    def execute(self, operation: str, args: object = None) -> object:
        self.operation = operation
        self.args = tuple(args or ())  # type: ignore[arg-type]
        return None

    def fetchone(self) -> tuple[object, ...] | None:
        return self.row

    def fetchall(self) -> tuple[tuple[object, ...], ...]:
        return () if self.row is None else (self.row,)

    def close(self) -> None:
        self.closed = True


class _Connection:
    autocommit = True

    def __init__(self, cursor: _Cursor) -> None:
        self.value = cursor
        self.rolled_back = False
        self.closed = False

    def cursor(self) -> _Cursor:
        return self.value

    def commit(self) -> None:
        raise AssertionError("operations snapshots are read-only")

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def test_team_operations_snapshot_is_content_free_bounded_and_alertable() -> None:
    cursor = _Cursor(
        (
            NOW,
            23,
            3,
            5,
            8,
            91,
            140,
            9_000,
            2,
            1,
            1,
            1,
            112,
            2,
            1,
            1,
            1,
            100,
            9,
            2,
            1,
            3,
            600,
        )
    )
    connection = _Connection(cursor)
    repository = PostgreSQLTeamOperationsRepository(lambda: cast(PostgreSQLConnection, connection))

    snapshot = repository.snapshot(
        TeamOperationsThresholds(
            quota_warning_percent=85,
            pending_jobs=8,
            pending_job_age_seconds=300,
            failed_jobs=2,
        )
    )

    assert not snapshot.healthy
    assert snapshot.alerts == (
        "MNEMO_TEAM_CHECKPOINT_QUOTA_MISSING",
        "MNEMO_TEAM_CHECKPOINT_QUOTA_EXCEEDED",
        "MNEMO_TEAM_CHECKPOINT_QUOTA_HIGH",
        "MNEMO_TEAM_MODEL_BUDGET_MISSING",
        "MNEMO_TEAM_MODEL_BUDGET_EXHAUSTED",
        "MNEMO_TEAM_MODEL_BUDGET_HIGH",
        "MNEMO_TEAM_OUTBOX_BACKLOG_HIGH",
        "MNEMO_TEAM_OUTBOX_AGE_HIGH",
        "MNEMO_TEAM_OUTBOX_LEASE_EXPIRED",
        "MNEMO_TEAM_OUTBOX_FAILURES_HIGH",
    )
    payload = snapshot.to_dict()
    assert set(payload) == {
        "schema_version",
        "supported_schema_version",
        "observed_at",
        "healthy",
        "alerts",
        "counts",
        "maximum_quota_utilization_percent",
        "maximum_model_budget_utilization_percent",
        "oldest_pending_job_age_seconds",
    }
    assert set(cast(dict[str, int], payload["counts"])) == {
        "workspaces",
        "projects",
        "active_workspace_memberships",
        "checkpoint_aggregates",
        "checkpoint_revisions",
        "checkpoint_payload_bytes",
        "quota_configured_workspaces",
        "quota_missing_workspaces",
        "quota_warning_workspaces",
        "quota_exceeded_workspaces",
        "model_budget_configured_workspaces",
        "model_budget_missing_workspaces",
        "model_budget_warning_workspaces",
        "model_budget_exhausted_workspaces",
        "pending_jobs",
        "active_lease_jobs",
        "expired_lease_jobs",
        "failed_jobs",
    }
    assert cursor.args == (85, 90)
    assert "workspace_id" not in payload
    assert "content_json" not in payload
    assert connection.rolled_back and connection.closed and cursor.closed


@pytest.mark.parametrize(
    "values",
    [
        {"quota_warning_percent": 0},
        {"quota_warning_percent": 101},
        {"model_budget_warning_percent": 0},
        {"pending_jobs": -1},
        {"pending_job_age_seconds": 1_000_000_001},
        {"failed_jobs": True},
    ],
)
def test_team_operations_thresholds_reject_invalid_values(values: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        TeamOperationsThresholds(**values)  # type: ignore[arg-type]


def test_team_operations_snapshot_hides_storage_failures() -> None:
    connection = _Connection(_Cursor(None))
    repository = PostgreSQLTeamOperationsRepository(lambda: cast(PostgreSQLConnection, connection))

    with pytest.raises(TeamOperationsStorageFailure) as raised:
        repository.snapshot(TeamOperationsThresholds())

    assert str(raised.value) == "MNEMO_TEAM_OPERATIONS_UNAVAILABLE"
    assert connection.rolled_back and connection.closed


def test_team_admin_status_and_check_are_machine_readable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    password = tmp_path / "backup-password"
    password.write_text("not-read-by-fixture", encoding="utf-8")
    password.chmod(0o600)
    environment = {
        "MNEMO_TEAM_DB_HOST": "postgres.internal",
        "MNEMO_TEAM_DB_PORT": "5432",
        "MNEMO_TEAM_DB_NAME": "mnemo",
        "MNEMO_TEAM_BACKUP_DB_USER": "mnemo_backup",
        "MNEMO_TEAM_BACKUP_DB_PASSWORD_FILE": str(password),
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    snapshot = TeamOperationsSnapshot(
        NOW,
        23,
        1,
        1,
        1,
        0,
        0,
        0,
        0,
        1,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        1,
        0,
        0,
        0,
        ("MNEMO_TEAM_CHECKPOINT_QUOTA_MISSING",),
    )

    class _Repository:
        def snapshot(self, thresholds: TeamOperationsThresholds) -> TeamOperationsSnapshot:
            assert thresholds.quota_warning_percent == 75
            return snapshot

    monkeypatch.setattr(
        team_admin,
        "build_team_operations_repository",
        lambda _config: _Repository(),
    )

    team_admin.main(["status", "--quota-warning-percent", "75"])
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["healthy"] is False
    assert status_payload["alerts"] == ["MNEMO_TEAM_CHECKPOINT_QUOTA_MISSING"]

    with pytest.raises(SystemExit) as raised:
        team_admin.main(["check", "--quota-warning-percent", "75"])
    assert raised.value.code == 1
    assert json.loads(capsys.readouterr().out)["healthy"] is False
