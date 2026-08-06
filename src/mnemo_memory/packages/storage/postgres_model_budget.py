"""Atomic PostgreSQL reservations for daily per-workspace model budgets."""

from __future__ import annotations

from collections.abc import Mapping

from mnemo_memory.packages.domain import (
    ModelBudgetDenied,
    ModelBudgetReservation,
    ModelTaskType,
    OwnerId,
    WorkspaceId,
)
from mnemo_memory.packages.policy import TeamOperation

from .postgres import PostgreSQLConnectionFactory


class ModelBudgetStorageFailure(RuntimeError):
    """Payload-free model-budget persistence failure."""


class PostgreSQLModelBudgetRepository:
    """Reserve one worst-case provider charge under exact team scope."""

    def __init__(
        self,
        connection_factory: PostgreSQLConnectionFactory,
        *,
        principal_id: OwnerId,
        workspace_id: WorkspaceId,
        statement_timeout_ms: int = 5_000,
    ) -> None:
        if not isinstance(principal_id, OwnerId) or not isinstance(workspace_id, WorkspaceId):
            raise TypeError("model budget repository scope is invalid")
        if (
            isinstance(statement_timeout_ms, bool)
            or not isinstance(statement_timeout_ms, int)
            or not 1 <= statement_timeout_ms <= 60_000
        ):
            raise ValueError("model budget statement timeout is invalid")
        self._connection_factory = connection_factory
        self._principal_id = principal_id
        self._workspace_id = workspace_id
        self._statement_timeout_ms = statement_timeout_ms

    def reserve(
        self,
        workspace_id: WorkspaceId,
        task_type: ModelTaskType,
        reservation: ModelBudgetReservation,
    ) -> None:
        if not isinstance(workspace_id, WorkspaceId) or not isinstance(task_type, ModelTaskType):
            raise TypeError("model budget reservation scope is invalid")
        if not isinstance(reservation, ModelBudgetReservation):
            raise TypeError("model budget reservation is invalid")
        if workspace_id != self._workspace_id:
            raise ModelBudgetDenied("model budget denied")
        try:
            connection = self._connection_factory()
            connection.autocommit = False
        except Exception as error:
            raise ModelBudgetStorageFailure("model budget storage is unavailable") from error
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT set_config('mnemo.principal_id', %s, true), "
                "set_config('mnemo.workspace_id', %s, true), "
                "set_config('mnemo.operation', %s, true), "
                "set_config('statement_timeout', %s, true)",
                (
                    str(self._principal_id),
                    str(self._workspace_id),
                    TeamOperation.CONTRIBUTE.value,
                    str(self._statement_timeout_ms),
                ),
            )
            cursor.execute(
                "SELECT mnemo_team.reserve_model_budget(CAST(%s AS uuid), %s, %s, %s, %s)",
                (
                    str(workspace_id),
                    task_type.value,
                    reservation.input_tokens,
                    reservation.output_tokens,
                    reservation.cost_microusd,
                ),
            )
            connection.commit()
        except ModelBudgetDenied:
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            if _sqlstate(error) in {"MZB01", "42501"}:
                raise ModelBudgetDenied("model budget denied") from error
            raise ModelBudgetStorageFailure("model budget storage is unavailable") from error
        finally:
            cursor.close()
            connection.close()


def _sqlstate(error: Exception) -> str | None:
    for value in error.args:
        if isinstance(value, Mapping):
            state = value.get("C")
            if isinstance(state, str):
                return state
    return None
