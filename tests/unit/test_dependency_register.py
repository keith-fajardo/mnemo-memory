import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]


def test_dependency_register_matches_locked_issue_1_dependencies() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_dependencies.py"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
