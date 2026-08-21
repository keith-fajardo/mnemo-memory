from mnemo_memory.packages.domain.model_budget import ModelTaskType


def test_frontier_takeover_task_type_exists():
    assert ModelTaskType.FRONTIER_TAKEOVER == "frontier_takeover"
