"""Smoke-test dbt wrapper behavior through an already-installed Mnemo command.

The script uses only an external synthetic manifest fixture and temporary files. It does not import
Mnemo from the source checkout, execute dbt Core, or contact a warehouse.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


class InstalledDbtWrapperVerificationError(RuntimeError):
    """A concise failure from the installed-artifact wrapper smoke test."""


def _run(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def _require_success(result: subprocess.CompletedProcess[str], label: str) -> None:
    if result.returncode != 0:
        raise InstalledDbtWrapperVerificationError(f"{label} failed with exit {result.returncode}")


def exercise(command: Path, work_directory: Path, manifest: Path) -> None:
    if not command.is_absolute() or not command.is_file():
        raise InstalledDbtWrapperVerificationError("installed mnemo-memory command is unavailable")
    if not manifest.is_absolute() or not manifest.is_file():
        raise InstalledDbtWrapperVerificationError("synthetic manifest fixture is unavailable")
    work_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    project = work_directory / "dbt project Δ"
    target = project / "target"
    target.mkdir(parents=True, exist_ok=True)
    project.joinpath("dbt_project.yml").write_text("name: synthetic\n", encoding="utf-8")
    data_directory = work_directory / "Mnemo data Δ"

    enabled = _run(
        str(command),
        "dbt",
        "enable",
        "--project-dir",
        str(project),
        "--data-dir",
        str(data_directory),
        cwd=work_directory,
    )
    _require_success(enabled, "dbt enable")
    if json.loads(enabled.stdout).get("existing_manifest") != "unavailable":
        raise InstalledDbtWrapperVerificationError(
            "dbt enable did not report missing initial manifest"
        )

    shell_hook = _run(str(command), "dbt", "shell-hook", "zsh", cwd=work_directory)
    _require_success(shell_hook, "dbt shell-hook")
    if shell_hook.stdout.strip() != 'dbt() { command mnemo-memory dbt exec -- "$@"; }':
        raise InstalledDbtWrapperVerificationError("dbt shell-hook did not return the safe wrapper")

    fake_dbt = work_directory / "fake dbt.py"
    fake_dbt.write_text(
        "from pathlib import Path\nimport shutil, sys\n"
        f"fixture = {str(manifest)!r}\n"
        "destination = Path(sys.argv[sys.argv.index('--target-path') + 1]) / 'manifest.json'\n"
        "shutil.copyfile(fixture, destination)\n",
        encoding="utf-8",
    )
    arguments = (
        str(command),
        "dbt",
        "exec",
        "--data-dir",
        str(data_directory),
        "--dbt-executable",
        str(Path(sys.executable).resolve()),
        "--json-summary",
        "--",
        str(fake_dbt),
        "run",
        "--project-dir",
        str(project),
        "--target-path",
        str(target),
    )
    activated = _run(*arguments, cwd=work_directory)
    _require_success(activated, "dbt exec activation")
    activation_summary = json.loads(activated.stdout)
    if "MNEMO_DBT_MANIFEST_ACTIVATED" not in {
        item["code"] for item in activation_summary["outcomes"]
    }:
        raise InstalledDbtWrapperVerificationError(
            "dbt exec did not activate the synthetic manifest"
        )

    active = _run(
        str(command),
        "dbt",
        "status",
        "--project-dir",
        str(project),
        "--data-dir",
        str(data_directory),
        cwd=work_directory,
    )
    _require_success(active, "dbt status")
    if json.loads(active.stdout).get("active") is not True:
        raise InstalledDbtWrapperVerificationError("activated manifest was not durable")

    unchanged = _run(*arguments, cwd=work_directory)
    _require_success(unchanged, "dbt exec idempotency")
    unchanged_summary = json.loads(unchanged.stdout)
    if "MNEMO_DBT_MANIFEST_UNCHANGED" not in {
        item["code"] for item in unchanged_summary["outcomes"]
    }:
        raise InstalledDbtWrapperVerificationError("unchanged manifest was not idempotent")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", type=Path, required=True)
    parser.add_argument("--work-directory", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        exercise(
            arguments.command.resolve(),
            arguments.work_directory.resolve(),
            arguments.manifest.resolve(),
        )
    except (
        InstalledDbtWrapperVerificationError,
        json.JSONDecodeError,
        OSError,
        subprocess.TimeoutExpired,
    ) as error:
        raise SystemExit(f"INSTALLED_DBT_WRAPPER_VERIFICATION_FAILED: {error}") from error
    print("Installed dbt wrapper verification passed")


if __name__ == "__main__":
    main()
