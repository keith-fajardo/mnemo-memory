"""Run the declared team context load gate against a real PostgreSQL server."""

from __future__ import annotations

from scripts.run_postgres_team_tests import run_checks

LOAD_TEST_PATH = "tests/integration/test_postgres_team_control_plane.py"
LOAD_TEST_NAME = "test_team_context_load_slo"


def main() -> None:
    run_checks(
        test_paths=(LOAD_TEST_PATH,),
        pytest_args=("-s", "-k", LOAD_TEST_NAME),
        environment_overrides={"MNEMO_RUN_TEAM_LOAD_TEST": "1"},
    )
    print("PostgreSQL team context load objectives passed.")


if __name__ == "__main__":
    main()
