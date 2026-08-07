import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts.verify_release_artifacts import (
    DISTRIBUTION_NAME,
    DISTRIBUTION_VERSION,
    SDIST_REQUIRED,
    WHEEL_REQUIRED,
    ArtifactVerificationError,
    verify_sdist,
    verify_source_root,
    verify_wheel,
)


def write_wheel(path: Path, *, extra: dict[str, bytes] | None = None) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for entry in WHEEL_REQUIRED:
            archive.writestr(entry, "")
        archive.writestr(
            "mnemo_unified_context-0.1.0a7.dist-info/METADATA",
            f"Metadata-Version: 2.3\nName: {DISTRIBUTION_NAME}\nVersion: {DISTRIBUTION_VERSION}\n",
        )
        archive.writestr(
            "mnemo_unified_context-0.1.0a7.dist-info/entry_points.txt",
            "[console_scripts]\n"
            "mnemo = mnemo_memory.cli:main\n"
            "mnemo-memory = mnemo_memory.cli:main\n"
            "mnemo-memory-team = mnemo_memory.apps.mcp.team_runtime:main\n"
            "mnemo-memory-team-admin = mnemo_memory.apps.cli.team_admin:main\n",
        )
        for entry, content in (extra or {}).items():
            archive.writestr(entry, content)


def write_sdist(path: Path) -> None:
    root = "mnemo_unified_context-0.1.0a7"
    with tarfile.open(path, "w:gz") as archive:
        for entry in SDIST_REQUIRED:
            content = b"MIT" if entry == "LICENSE" else b""
            info = tarfile.TarInfo(f"{root}/{entry}")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))


def test_release_artifact_verifier_accepts_complete_archives(tmp_path: Path) -> None:
    wheel = tmp_path / "package.whl"
    sdist = tmp_path / "package.tar.gz"
    write_wheel(wheel)
    write_sdist(sdist)

    verify_wheel(wheel)
    verify_sdist(sdist)


def test_release_artifact_verifier_reports_missing_and_forbidden_entries(tmp_path: Path) -> None:
    wheel = tmp_path / "package.whl"
    sdist = tmp_path / "package.tar.gz"
    write_wheel(wheel, extra={"mnemo_memory/private/manifest.json": b"{}"})
    write_sdist(sdist)

    with pytest.raises(ArtifactVerificationError, match=r"forbidden entries.*manifest.json"):
        verify_wheel(wheel)

    with tarfile.open(sdist, "w:gz") as archive:
        info = tarfile.TarInfo("mnemo_unified_context-0.1.0a7/README.md")
        archive.addfile(info, io.BytesIO())
    with pytest.raises(ArtifactVerificationError, match=r"missing required entries.*LICENSE"):
        verify_sdist(sdist)


def test_source_text_check_uses_explicit_paths_only(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("safe")
    workflow = tmp_path / ".github"
    workflow.mkdir()
    (workflow / "workflow.yml").write_text("mnemo-agent-context-placeholder")

    verify_source_root(tmp_path, ("mnemo-agent-context-placeholder",), (Path("README.md"),))
    with pytest.raises(ArtifactVerificationError, match="forbidden text"):
        verify_source_root(tmp_path, ("mnemo-agent-context-placeholder",), (Path(".github"),))
