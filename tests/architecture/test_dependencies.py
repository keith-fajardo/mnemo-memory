import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]
CHECKER = REPOSITORY_ROOT / "scripts/check_architecture.py"


def run_checker(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), "--root", str(root)],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_repository_respects_architecture_boundaries() -> None:
    result = run_checker(REPOSITORY_ROOT)

    assert result.returncode == 0, result.stderr


def test_domain_rejects_third_party_import(tmp_path: Path) -> None:
    source = tmp_path / "src/mnemo_memory/packages/domain/example.py"
    source.parent.mkdir(parents=True)
    source.write_text("import fastapi\n")

    result = run_checker(tmp_path)

    assert result.returncode == 1
    assert "packages/domain may import only the standard library" in result.stderr


def test_package_rejects_reverse_dependency(tmp_path: Path) -> None:
    source = tmp_path / "src/mnemo_memory/packages/storage/example.py"
    source.parent.mkdir(parents=True)
    source.write_text("import mnemo_memory.packages.context_engine\n")

    result = run_checker(tmp_path)

    assert result.returncode == 1
    assert "packages/storage must not depend on packages/context_engine" in result.stderr


def test_connector_rejects_connector_peer(tmp_path: Path) -> None:
    source = tmp_path / "src/mnemo_memory/connectors/codex/example.py"
    source.parent.mkdir(parents=True)
    source.write_text("import mnemo_memory.connectors.dbt\n")

    result = run_checker(tmp_path)

    assert result.returncode == 1
    assert "connectors/codex must not depend on connectors/dbt" in result.stderr
