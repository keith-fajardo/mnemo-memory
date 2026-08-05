"""Bounded shell-free Git state observation for dbt manifest provenance."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path, PurePosixPath

from mnemo_memory.packages.domain import SourceStateFingerprint
from mnemo_memory.packages.policy.knowledge import contains_high_confidence_secret

_GIT_ID = re.compile(rb"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_MAX_CHANGED_PATHS = 4_096
_MAX_STATUS_BYTES = 1_000_000
_MAX_FILE_BYTES = 16_000_000
_MAX_TOTAL_BYTES = 64_000_000
_Runner = Callable[[tuple[str, ...], Path], bytes | None]


class DbtGitStateObserver:
    """Observe exact bounded local state without retaining paths or source content."""

    def __init__(self, runner: _Runner | None = None) -> None:
        self._runner = runner or _run_git

    def observe(
        self, root: Path, *, target_name: str | None = None
    ) -> SourceStateFingerprint | None:
        project_root = root.resolve()
        if not project_root.is_absolute() or not project_root.is_dir():
            return None
        inside = self._run(("rev-parse", "--is-inside-work-tree"), project_root)
        if inside is None or inside.strip() != b"true":
            return None
        commit = self._run(("rev-parse", "--verify", "HEAD"), project_root)
        if commit is None:
            return None
        commit = commit.strip()
        if _GIT_ID.fullmatch(commit) is None:
            return None
        status_value = self._run(
            ("status", "--porcelain=v1", "-z", "--untracked-files=all"), project_root
        )
        if status_value is None or len(status_value) > _MAX_STATUS_BYTES:
            return None
        changed = _changed_paths(status_value)
        if changed is None or len(changed) > _MAX_CHANGED_PATHS:
            return None
        fingerprint = sha256()
        fingerprint.update(b"mnemo-dbt-working-tree-v1\0")
        fingerprint.update(commit)
        total_bytes = 0
        for status_code, raw_path in changed:
            observed = _path_digest(project_root, raw_path)
            if observed is None:
                return None
            digest, size = observed
            total_bytes += size
            if total_bytes > _MAX_TOTAL_BYTES:
                return None
            fingerprint.update(status_code)
            fingerprint.update(b"\0")
            fingerprint.update(raw_path)
            fingerprint.update(b"\0")
            fingerprint.update(digest)
        target = _bounded_target(target_name)
        return SourceStateFingerprint(
            git_commit=commit.decode("ascii"),
            working_tree_fingerprint=f"sha256:{fingerprint.hexdigest()}",
            dirty=bool(changed),
            target_name=target,
        )

    def _run(self, arguments: tuple[str, ...], root: Path) -> bytes | None:
        value = self._runner(arguments, root)
        return value if isinstance(value, bytes) else None


def _changed_paths(value: bytes) -> tuple[tuple[bytes, bytes], ...] | None:
    if not value:
        return ()
    records = value.split(b"\0")
    if records[-1] != b"":
        return None
    records.pop()
    result: list[tuple[bytes, bytes]] = []
    index = 0
    while index < len(records):
        record = records[index]
        if len(record) < 4 or record[2:3] != b" ":
            return None
        status_code = record[:2]
        raw_path = record[3:]
        if not raw_path:
            return None
        result.append((status_code, raw_path))
        if b"R" in status_code or b"C" in status_code:
            index += 1
            if index >= len(records) or not records[index]:
                return None
            result.append((status_code, records[index]))
        index += 1
    return tuple(sorted(set(result), key=lambda item: (item[1], item[0])))


def _path_digest(root: Path, raw_path: bytes) -> tuple[bytes, int] | None:
    try:
        decoded = os.fsdecode(raw_path)
        relative = PurePosixPath(decoded)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            return None
        candidate = root.joinpath(*relative.parts)
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    try:
        metadata = candidate.lstat()
    except FileNotFoundError:
        return sha256(b"deleted").digest(), 0
    except OSError:
        return None
    mode = metadata.st_mode
    if stat.S_ISLNK(mode):
        try:
            target = os.fsencode(os.readlink(candidate))
        except (OSError, ValueError):
            return None
        return sha256(b"symlink\0" + target).digest(), len(target)
    if not stat.S_ISREG(mode) or metadata.st_size > _MAX_FILE_BYTES:
        return None
    digest = sha256()
    observed_bytes = 0
    try:
        with candidate.open("rb") as handle:
            while chunk := handle.read(128 * 1024):
                observed_bytes += len(chunk)
                if observed_bytes > _MAX_FILE_BYTES:
                    return None
                digest.update(chunk)
    except OSError:
        return None
    return digest.digest(), observed_bytes


def _bounded_target(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not value.strip()
        or len(value) > 256
        or any(ord(char) < 32 for char in value)
        or contains_high_confidence_secret(value)
    ):
        return None
    return value


def _run_git(arguments: tuple[str, ...], root: Path) -> bytes | None:
    executable = shutil.which("git")
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            (executable, "-C", str(root), *arguments),
            check=False,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0 or len(completed.stdout) > _MAX_STATUS_BYTES:
        return None
    return completed.stdout
