"""Verify Mnemo release archives using only the Python standard library."""

from __future__ import annotations

import argparse
import configparser
import re
import tarfile
import zipfile
from collections.abc import Iterable
from email.parser import BytesParser
from email.policy import default
from pathlib import Path, PurePosixPath

DISTRIBUTION_NAME = "mnemo-unified-context"
DISTRIBUTION_VERSION = "0.1.0a2"
REQUIRED_MIGRATIONS = (
    "0001_initial.sql",
    "0002_checkpoint_aggregate_revisions.sql",
    "0003_dbt_manifest_snapshots.sql",
    "0004_source_structure_snapshots.sql",
    "0005_source_snapshot_activations.sql",
)
WHEEL_REQUIRED = (
    "mnemo_memory/py.typed",
    *(f"mnemo_memory/resources/migrations/{migration}" for migration in REQUIRED_MIGRATIONS),
    "mnemo_memory/resources/schemas/context-packet-v1.json",
)
SDIST_REQUIRED = (
    "README.md",
    "LICENSE",
    "pyproject.toml",
    "src/mnemo_memory/__init__.py",
    "src/mnemo_memory/cli.py",
    "src/mnemo_memory/apps/cli/main.py",
    "src/mnemo_memory/apps/mcp/server.py",
    "src/mnemo_memory/connectors/dbt/manifest.py",
    "src/mnemo_memory/packages/storage/sqlite.py",
    *(f"src/mnemo_memory/resources/migrations/{migration}" for migration in REQUIRED_MIGRATIONS),
    "src/mnemo_memory/resources/schemas/context-packet-v1.json",
)
FORBIDDEN_SUFFIXES = frozenset({".sqlite", ".sqlite3", ".db", ".wal", ".shm", ".pem", ".key"})
CACHE_COMPONENTS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
ABSOLUTE_PATH_MARKERS = ("/Users/", "file:///Users/", "C:\\Users\\")


class ArtifactVerificationError(ValueError):
    """A named release artifact verification failure."""


def _raise_missing(kind: str, entries: Iterable[str]) -> None:
    missing = sorted(entries)
    if missing:
        raise ArtifactVerificationError(f"{kind} missing required entries: {', '.join(missing)}")


def _forbidden_entries(entries: Iterable[str]) -> list[str]:
    forbidden: list[str] = []
    for entry in entries:
        normalized = entry.replace("\\", "/")
        path = PurePosixPath(normalized)
        name = path.name.casefold()
        if (
            path.is_absolute()
            or bool(re.match(r"^[a-zA-Z]:/", normalized))
            or any(component in CACHE_COMPONENTS for component in path.parts)
            or name == "manifest.json"
            or name == ".env"
            or name.startswith(".env.")
            or name.endswith(".pyc")
            or path.suffix.casefold() in FORBIDDEN_SUFFIXES
        ):
            forbidden.append(entry)
    return sorted(set(forbidden))


def _verify_safe_text(name: str, payload: bytes) -> None:
    if Path(name).suffix.casefold() not in {".md", ".toml", ".txt"} and not name.endswith(
        ("METADATA", "PKG-INFO")
    ):
        return
    text = payload.decode("utf-8", errors="replace")
    markers = [marker for marker in ABSOLUTE_PATH_MARKERS if marker in text]
    if markers:
        raise ArtifactVerificationError(
            f"{name} contains machine-specific absolute-path markers: {', '.join(markers)}"
        )


def _wheel_metadata_name(entries: set[str]) -> str:
    matches = sorted(entry for entry in entries if entry.endswith(".dist-info/METADATA"))
    if len(matches) != 1:
        raise ArtifactVerificationError("wheel must contain exactly one .dist-info/METADATA entry")
    return matches[0]


def verify_wheel(path: Path) -> None:
    if not path.is_file():
        raise ArtifactVerificationError(f"wheel file is missing: {path}")
    with zipfile.ZipFile(path) as archive:
        entries = set(archive.namelist())
        _raise_missing("wheel", set(WHEEL_REQUIRED) - entries)
        forbidden = _forbidden_entries(entries)
        if forbidden:
            raise ArtifactVerificationError(
                f"wheel contains forbidden entries: {', '.join(forbidden)}"
            )

        metadata_entry = _wheel_metadata_name(entries)
        metadata = BytesParser(policy=default).parsebytes(archive.read(metadata_entry))
        if metadata["Name"] != DISTRIBUTION_NAME:
            raise ArtifactVerificationError(
                f"wheel metadata Name is {metadata['Name']!r}, expected {DISTRIBUTION_NAME!r}"
            )
        if metadata["Version"] != DISTRIBUTION_VERSION:
            raise ArtifactVerificationError(
                "wheel metadata Version is "
                f"{metadata['Version']!r}, expected {DISTRIBUTION_VERSION!r}"
            )

        entry_points = sorted(
            entry for entry in entries if entry.endswith(".dist-info/entry_points.txt")
        )
        if len(entry_points) != 1:
            raise ArtifactVerificationError(
                "wheel must contain exactly one .dist-info/entry_points.txt"
            )
        parser = configparser.ConfigParser()
        parser.read_string(archive.read(entry_points[0]).decode("utf-8"))
        command = parser.get("console_scripts", "mnemo-memory", fallback=None)
        if command != "mnemo_memory.cli:main":
            raise ArtifactVerificationError(
                "wheel console entry point mnemo-memory must target mnemo_memory.cli:main"
            )

        for entry in entries:
            _verify_safe_text(entry, archive.read(entry))


def verify_sdist(path: Path) -> None:
    if not path.is_file():
        raise ArtifactVerificationError(f"source distribution file is missing: {path}")
    with tarfile.open(path, "r:gz") as archive:
        files = [member for member in archive.getmembers() if member.isfile()]
        roots = {PurePosixPath(member.name).parts[0] for member in files if member.name}
        if len(roots) != 1:
            raise ArtifactVerificationError(
                "source distribution must contain exactly one top-level root"
            )
        root = next(iter(roots))
        entries = {str(PurePosixPath(member.name).relative_to(root)) for member in files}
        _raise_missing("source distribution", set(SDIST_REQUIRED) - entries)
        forbidden = _forbidden_entries(entries)
        if forbidden:
            raise ArtifactVerificationError(
                f"source distribution contains forbidden entries: {', '.join(forbidden)}"
            )
        for member in files:
            extracted = archive.extractfile(member)
            if extracted is not None:
                _verify_safe_text(member.name, extracted.read())


def _text_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        yield path
        return
    if path.is_dir():
        for candidate in path.rglob("*"):
            if candidate.is_file() and candidate.suffix.casefold() in {
                ".json",
                ".md",
                ".py",
                ".toml",
                ".txt",
                ".yaml",
                ".yml",
            }:
                yield candidate


def verify_source_root(
    source_root: Path, forbidden_text: tuple[str, ...], text_paths: tuple[Path, ...]
) -> None:
    if not source_root.is_dir():
        raise ArtifactVerificationError(f"source root is missing: {source_root}")
    candidates = [
        path
        for relative in text_paths
        for path in _text_files(source_root / relative)
        if ".git" not in path.parts
        and ".venv" not in path.parts
        and "node_modules" not in path.parts
    ]
    for forbidden in forbidden_text:
        matches = [
            path.relative_to(source_root)
            for path in candidates
            if forbidden in path.read_text(encoding="utf-8", errors="ignore")
        ]
        if matches:
            listed = ", ".join(str(path) for path in sorted(matches))
            raise ArtifactVerificationError(f"forbidden text {forbidden!r} found in: {listed}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--sdist", type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--forbidden-text", action="append", default=[])
    parser.add_argument("--text-path", action="append", type=Path, default=[])
    args = parser.parse_args()
    if (args.wheel is None) != (args.sdist is None):
        parser.error("--wheel and --sdist must be supplied together")
    if args.wheel is None and args.source_root is None:
        parser.error("supply --wheel/--sdist and/or --source-root")
    if args.forbidden_text and args.source_root is None:
        parser.error("--forbidden-text requires --source-root")
    if args.forbidden_text and not args.text_path:
        parser.error("--forbidden-text requires at least one --text-path")
    return args


def main() -> None:
    args = parse_args()
    try:
        if args.source_root is not None:
            verify_source_root(args.source_root, tuple(args.forbidden_text), tuple(args.text_path))
        if args.wheel is not None:
            verify_wheel(args.wheel)
            verify_sdist(args.sdist)
    except ArtifactVerificationError as error:
        raise SystemExit(f"RELEASE_ARTIFACT_VERIFICATION_FAILED: {error}") from error
    print(
        "Release artifact verification passed: "
        f"distribution={DISTRIBUTION_NAME} version={DISTRIBUTION_VERSION}"
    )


if __name__ == "__main__":
    main()
