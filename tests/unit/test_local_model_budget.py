"""The local typed-decision budget is a file-locked per-UTC-day counter that fails closed."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from mnemo_memory.packages.domain import (
    ModelBudgetDenied,
    ModelBudgetReservation,
    ModelTaskType,
    WorkspaceId,
)
from mnemo_memory.packages.storage import LocalDailyModelBudget

WORKSPACE = WorkspaceId(UUID(int=0))
ONE_THOUSAND = ModelBudgetReservation(input_tokens=1_000, output_tokens=1, cost_microusd=0)
TYPED = ModelTaskType.TYPED_DECISION


def _budget(
    tmp_path: Path, limit: int = 10_000_000, at: datetime | None = None
) -> LocalDailyModelBudget:
    moment = at or datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    return LocalDailyModelBudget(
        tmp_path, task_type=TYPED, daily_input_tokens=limit, clock=lambda: moment
    )


def test_reservations_count_up_to_the_limit_then_deny(tmp_path: Path) -> None:
    budget = _budget(tmp_path, limit=2_500)
    assert budget.reserved_today() == 0
    budget.reserve(WORKSPACE, TYPED, ONE_THOUSAND)
    budget.reserve(WORKSPACE, TYPED, ONE_THOUSAND)
    with pytest.raises(ModelBudgetDenied):
        budget.reserve(WORKSPACE, TYPED, ONE_THOUSAND)
    assert budget.reserved_today() == 2_000
    assert budget.path.name == "typed_decision-budget.json"
    assert budget.path.stat().st_mode & 0o777 == 0o600
    assert json.loads(budget.path.read_text("utf-8")) == {
        "day": "2026-10-02",
        "input_tokens": 2_000,
        "task_type": "typed_decision",
        "version": 1,
    }


def test_the_counter_resets_at_each_utc_day(tmp_path: Path) -> None:
    late = _budget(tmp_path, limit=1_000, at=datetime(2026, 10, 2, 23, 59, 59, tzinfo=UTC))
    late.reserve(WORKSPACE, TYPED, ONE_THOUSAND)
    with pytest.raises(ModelBudgetDenied):
        late.reserve(WORKSPACE, TYPED, ONE_THOUSAND)
    early = _budget(tmp_path, limit=1_000, at=datetime(2026, 10, 3, 0, 0, 1, tzinfo=UTC))
    assert early.reserved_today() == 0
    early.reserve(WORKSPACE, TYPED, ONE_THOUSAND)
    assert early.reserved_today() == 1_000


def test_a_local_timezone_clock_still_counts_by_utc_day(tmp_path: Path) -> None:
    plus_fourteen = timezone(timedelta(hours=14))
    # 2026-10-03 09:00 at +14:00 is still 2026-10-02 19:00 UTC.
    budget = _budget(tmp_path, at=datetime(2026, 10, 3, 9, 0, tzinfo=plus_fourteen))
    budget.reserve(WORKSPACE, TYPED, ONE_THOUSAND)
    assert json.loads(budget.path.read_text("utf-8"))["day"] == "2026-10-02"


def test_corrupt_counter_denies_and_is_left_untouched(tmp_path: Path) -> None:
    budget = _budget(tmp_path)
    budget.path.write_text('{"version": 1, "day": "2026-10-02"', encoding="utf-8")
    with pytest.raises(ModelBudgetDenied):
        budget.reserve(WORKSPACE, TYPED, ONE_THOUSAND)
    assert budget.reserved_today() is None
    assert budget.path.read_text("utf-8") == '{"version": 1, "day": "2026-10-02"'


@pytest.mark.parametrize(
    "stored",
    [
        {"version": 1, "task_type": "frontier_takeover", "day": "2026-10-02", "input_tokens": 5},
        {"version": True, "task_type": "typed_decision", "day": "2026-10-02", "input_tokens": 5},
        {"version": 1, "task_type": "typed_decision", "day": "2026-10-02", "input_tokens": -1},
        {"version": 1, "task_type": "typed_decision", "day": "2026-10-02"},
    ],
)
def test_wrong_shape_denies(tmp_path: Path, stored: dict[str, object]) -> None:
    budget = _budget(tmp_path)
    budget.path.write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(ModelBudgetDenied):
        budget.reserve(WORKSPACE, TYPED, ONE_THOUSAND)


def test_a_symlinked_counter_denies(tmp_path: Path) -> None:
    budget = _budget(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    budget.path.symlink_to(outside)
    with pytest.raises(ModelBudgetDenied):
        budget.reserve(WORKSPACE, TYPED, ONE_THOUSAND)
    assert budget.reserved_today() is None


def test_wrong_task_type_naive_clock_and_bad_arguments_deny(tmp_path: Path) -> None:
    budget = _budget(tmp_path)
    with pytest.raises(ModelBudgetDenied):
        budget.reserve(WORKSPACE, ModelTaskType.FRONTIER_TAKEOVER, ONE_THOUSAND)
    naive = LocalDailyModelBudget(
        tmp_path, task_type=TYPED, daily_input_tokens=10, clock=lambda: datetime(2026, 10, 2)
    )
    with pytest.raises(ModelBudgetDenied):
        naive.reserve(WORKSPACE, TYPED, ONE_THOUSAND)
    with pytest.raises(ValueError, match="daily input token limit"):
        LocalDailyModelBudget(tmp_path, task_type=TYPED, daily_input_tokens=0)
    with pytest.raises(TypeError, match="task type"):
        LocalDailyModelBudget(tmp_path, task_type="typed_decision", daily_input_tokens=1)  # type: ignore[arg-type]


def test_concurrent_reservations_never_lose_an_update(tmp_path: Path) -> None:
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            budget = _budget(tmp_path)
            for _ in range(25):
                budget.reserve(WORKSPACE, TYPED, ONE_THOUSAND)
        except BaseException as error:  # pragma: no cover - surfaced below
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert _budget(tmp_path).reserved_today() == 8 * 25 * 1_000
