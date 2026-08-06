"""Private content-free diagnostic bundle coverage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

import mnemo_memory.apps.cli.main as cli
import mnemo_memory.packages.application.diagnostics as diagnostic_module
from mnemo_memory.apps.cli.main import app
from mnemo_memory.packages.application import (
    DiagnosticClientStatus,
    LocalConfig,
    PersonalDiagnosticContext,
    PersonalDiagnosticError,
    PersonalDiagnosticService,
    build_lifecycle_service,
)

NOW = datetime(2026, 8, 5, 18, 30, 12, 345678, tzinfo=UTC)
RUNTIME = {"python": "3.12.11", "system": "TestOS", "machine": "test64"}


def _context(*, registered: bool | None = True) -> PersonalDiagnosticContext:
    return PersonalDiagnosticContext(
        DiagnosticClientStatus(True, True, "connected"),
        DiagnosticClientStatus(False, False, "not_installed"),
        registered,
    )


def _manifest(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        assert archive.namelist() == ["manifest.json"]
        value = json.loads(archive.read("manifest.json"))
    assert isinstance(value, dict)
    return value


def test_diagnostic_bundle_is_private_closed_and_integrity_verifiable(tmp_path: Path) -> None:
    data_directory = tmp_path / "Private Mnemo Data"
    config = LocalConfig.defaults(data_directory)
    build_lifecycle_service(config).initialize()
    private_marker = "private-memory-payload-98f31d"
    with sqlite3.connect(config.database_path) as database:
        database.execute("CREATE TABLE private_diagnostic_fixture(payload TEXT NOT NULL)")
        database.execute("INSERT INTO private_diagnostic_fixture VALUES (?)", (private_marker,))

    result = PersonalDiagnosticService(
        config,
        context=_context(),
        clock=lambda: NOW,
        runtime_metadata=lambda: RUNTIME,
    ).create()

    assert result.reused is False
    assert result.bundle_path.parent == data_directory / "diagnostics"
    assert result.bundle_path.name == (
        f"mnemo-diagnostics-20260805T183012345678Z-"
        f"{result.archive_digest.removeprefix('sha256:')}.zip"
    )
    assert result.archive_digest == (
        "sha256:" + hashlib.sha256(result.bundle_path.read_bytes()).hexdigest()
    )
    assert result.bundle_path.stat().st_mode & 0o777 == 0o600
    assert result.bundle_path.parent.stat().st_mode & 0o777 == 0o700

    manifest = _manifest(result.bundle_path)
    included_digest = manifest.pop("manifest_digest")
    canonical = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    assert included_digest == "sha256:" + hashlib.sha256(canonical).hexdigest()
    assert included_digest == result.manifest_digest
    assert manifest == {
        "clients": {
            "claude_code": {
                "available": False,
                "connected": False,
                "status": "not_installed",
            },
            "codex": {"available": True, "connected": True, "status": "connected"},
        },
        "created_at": "2026-08-05T18:30:12.345678+00:00",
        "format": "mnemo.personal-diagnostics.v1",
        "lifecycle": {"initialized": True, "running": False, "schema_version": 29},
        "privacy": {
            "content_included": False,
            "credentials_included": False,
            "environment_included": False,
            "identifiers_included": False,
            "logs_included": False,
            "paths_included": False,
            "subprocess_output_included": False,
        },
        "project": {"registered": True},
        "runtime": {"machine": "test64", "mnemo": "0.1.0", "python": "3.12.11", "system": "TestOS"},
        "settings": {"status": "available"},
        "storage": {
            "foreign_keys": True,
            "integrity": True,
            "schema_version": 29,
            "status": "healthy",
        },
    }
    encoded = json.dumps(manifest, sort_keys=True)
    assert private_marker not in encoded
    assert str(data_directory) not in encoded
    assert str(tmp_path) not in encoded
    assert "owner_id" not in encoded
    assert "project_id" not in encoded


def test_diagnostics_remains_available_for_absent_and_corrupt_storage(tmp_path: Path) -> None:
    absent = LocalConfig.defaults(tmp_path / "absent")
    absent_result = PersonalDiagnosticService(
        absent,
        clock=lambda: NOW,
        runtime_metadata=lambda: RUNTIME,
    ).create()
    absent_manifest = _manifest(absent_result.bundle_path)
    assert absent_manifest["lifecycle"] == {
        "initialized": False,
        "running": False,
        "schema_version": None,
    }
    assert absent_manifest["storage"] == {
        "foreign_keys": None,
        "integrity": None,
        "schema_version": None,
        "status": "not_initialized",
    }
    assert not absent.database_path.exists()
    assert not absent.config_path.exists()

    corrupt = LocalConfig.defaults(tmp_path / "corrupt")
    corrupt.data_directory.mkdir()
    corrupt.config_path.write_text(json.dumps(corrupt.to_dict()), encoding="utf-8")
    private_detail = "corrupt-private-database-detail"
    corrupt.database_path.write_text(private_detail, encoding="utf-8")
    corrupt_result = PersonalDiagnosticService(
        corrupt,
        clock=lambda: NOW,
        runtime_metadata=lambda: RUNTIME,
    ).create()
    corrupt_manifest = _manifest(corrupt_result.bundle_path)
    assert corrupt_manifest["lifecycle"] == {
        "initialized": True,
        "running": False,
        "schema_version": None,
    }
    assert corrupt_manifest["storage"] == {
        "foreign_keys": None,
        "integrity": None,
        "schema_version": None,
        "status": "unavailable",
    }
    assert private_detail not in json.dumps(corrupt_manifest)


def test_identical_diagnostic_is_reused_and_collision_is_never_overwritten(
    tmp_path: Path,
) -> None:
    config = LocalConfig.defaults(tmp_path / "profile")
    first = PersonalDiagnosticService(
        config,
        clock=lambda: NOW,
        runtime_metadata=lambda: RUNTIME,
    ).create()
    repeated = PersonalDiagnosticService(
        config,
        clock=lambda: NOW,
        runtime_metadata=lambda: RUNTIME,
    ).create()

    assert repeated.reused is True
    assert repeated.bundle_path == first.bundle_path
    first.bundle_path.write_bytes(b"foreign collision")

    with pytest.raises(PersonalDiagnosticError, match="already exists"):
        PersonalDiagnosticService(
            config,
            clock=lambda: NOW,
            runtime_metadata=lambda: RUNTIME,
        ).create()
    assert first.bundle_path.read_bytes() == b"foreign collision"
    assert not any(
        path.name.startswith(".mnemo-diagnostics-") for path in first.bundle_path.parent.iterdir()
    )


def test_unsafe_directory_and_partial_archive_are_cleaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = LocalConfig.defaults(tmp_path / "profile")
    config.data_directory.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (config.data_directory / "diagnostics").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PersonalDiagnosticError, match="unsafe"):
        PersonalDiagnosticService(
            config,
            clock=lambda: NOW,
            runtime_metadata=lambda: RUNTIME,
        ).create()
    assert list(outside.iterdir()) == []

    (config.data_directory / "diagnostics").unlink()

    def fail_write(path: Path, _: bytes) -> None:
        path.write_bytes(b"partial private diagnostics")
        raise OSError("private write detail")

    monkeypatch.setattr(diagnostic_module, "_write_archive", fail_write)
    with pytest.raises(PersonalDiagnosticError, match="creation failed"):
        PersonalDiagnosticService(
            config,
            clock=lambda: NOW,
            runtime_metadata=lambda: RUNTIME,
        ).create()
    assert list((config.data_directory / "diagnostics").iterdir()) == []


def test_diagnostic_context_contract_rejects_inconsistent_or_open_values() -> None:
    with pytest.raises(ValueError):
        DiagnosticClientStatus(True, False, "private-client-detail")
    with pytest.raises(ValueError):
        DiagnosticClientStatus(False, True, "connected")
    with pytest.raises(TypeError):
        PersonalDiagnosticContext(
            DiagnosticClientStatus(False, False, "not_installed"),
            DiagnosticClientStatus(False, False, "not_installed"),
            "yes",  # type: ignore[arg-type]
        )


def test_cli_creates_bundle_and_sanitizes_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_diagnostic_context", lambda *_: _context(registered=False))
    data_directory = tmp_path / "profile"
    created = CliRunner().invoke(app, ["diagnostics", "--data-dir", str(data_directory)])

    assert created.exit_code == 0
    payload = json.loads(created.output)
    assert payload["status"] == "created"
    assert Path(payload["bundle_path"]).is_file()
    assert payload["manifest_digest"].startswith("sha256:")
    assert payload["archive_digest"].startswith("sha256:")

    unsafe = tmp_path / "unsafe"
    unsafe.mkdir()
    diagnostics = unsafe / "diagnostics"
    outside = tmp_path / "outside-cli"
    outside.mkdir()
    diagnostics.symlink_to(outside, target_is_directory=True)
    failed = CliRunner().invoke(app, ["diagnostics", "--data-dir", str(unsafe)])
    assert failed.exit_code == 1
    assert json.loads(failed.output) == {
        "code": "MNEMO_DIAGNOSTICS_FAILED",
        "status": "failed",
    }
    assert "unsafe" not in failed.output
    assert str(tmp_path) not in failed.output


def test_diagnostic_client_status_is_bounded_and_ownership_aware(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = tmp_path / "mnemo-memory"
    launcher.write_text("launcher", encoding="utf-8")
    monkeypatch.setattr(
        "mnemo_memory.apps.cli.main.shutil.which",
        lambda name: None if name == "claude" else f"/tools/{name}",
    )

    class _Codex:
        def __init__(self, *_: object) -> None:
            pass

        def inspect(self) -> dict[str, object]:
            return {"command": "foreign-private-path"}

        def is_owned(self, _: object) -> bool:
            return False

    monkeypatch.setattr(cli, "CodexMcpManager", _Codex)

    codex = cli._diagnostic_client_status("codex", launcher)
    claude = cli._diagnostic_client_status("claude-code", launcher)

    assert codex.to_dict() == {"available": True, "connected": False, "status": "available"}
    assert claude.to_dict() == {
        "available": False,
        "connected": False,
        "status": "not_installed",
    }
