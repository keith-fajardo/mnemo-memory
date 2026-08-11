"""Local, bounded evidence references for checkpoint source files."""

from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid5

from mnemo_memory.packages.domain import (
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryScope,
    ScopeLevel,
    SourceId,
    SourceTrustClass,
    VerificationStatus,
)

_SOURCE_NAMESPACE = UUID("f849e1b4-e9bf-5fb2-8129-4cf799fd7a34")
_EVIDENCE_NAMESPACE = UUID("10b10166-6ccd-567f-8df8-fefbf3ff8c15")
_MAXIMUM_EVIDENCE_FILE_BYTES = 5 * 1024 * 1024
_MAXIMUM_EVIDENCE_FILES = 16


class CheckpointFileEvidenceError(ValueError):
    """Payload-free failure while resolving local checkpoint evidence."""


class CheckpointFileEvidenceResolver:
    """Turn safe project-relative file names into canonical evidence references."""

    def __init__(
        self,
        project_root: Path,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        root = project_root.expanduser().resolve()
        if not root.is_dir():
            raise CheckpointFileEvidenceError("checkpoint evidence root is unavailable")
        self._root = root
        self._clock = clock

    def __call__(
        self, scope: MemoryScope, relative_paths: tuple[str, ...]
    ) -> tuple[EvidenceReference, ...]:
        if scope.level is not ScopeLevel.TASK or scope.project_id is None:
            raise CheckpointFileEvidenceError("checkpoint evidence scope is invalid")
        if not relative_paths or len(relative_paths) > _MAXIMUM_EVIDENCE_FILES:
            raise CheckpointFileEvidenceError("checkpoint evidence file count is invalid")
        observed_at = self._clock()
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise CheckpointFileEvidenceError("checkpoint evidence clock is invalid")
        display_revision = self._git_display_revision()
        references = tuple(
            self._reference(scope, relative_path, observed_at, display_revision)
            for relative_path in relative_paths
        )
        if len({reference.location.uri for reference in references}) != len(references):
            raise CheckpointFileEvidenceError("checkpoint evidence files must be unique")
        return references

    def _reference(
        self,
        scope: MemoryScope,
        raw_path: str,
        observed_at: datetime,
        display_revision: str,
    ) -> EvidenceReference:
        relative = self._relative_path(raw_path)
        candidate = self._root.joinpath(*relative.parts)
        current = self._root
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise CheckpointFileEvidenceError("checkpoint evidence file is unsafe")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self._root)
            stat = resolved.stat()
        except (OSError, ValueError) as error:
            raise CheckpointFileEvidenceError("checkpoint evidence file is unavailable") from error
        if not resolved.is_file() or stat.st_size > _MAXIMUM_EVIDENCE_FILE_BYTES:
            raise CheckpointFileEvidenceError("checkpoint evidence file is invalid")
        digest = self._digest(resolved)
        project_key = f"{scope.owner_id}:{scope.workspace_id}:{scope.project_id}"
        source_id = SourceId.from_string(str(uuid5(_SOURCE_NAMESPACE, f"{project_key}:{relative}")))
        evidence_id = EvidenceId.from_string(
            str(uuid5(_EVIDENCE_NAMESPACE, f"{project_key}:{relative}:{digest}"))
        )
        return EvidenceReference(
            evidence_id=evidence_id,
            source_id=source_id,
            source_type=EvidenceSourceType.REPOSITORY,
            trust_class=SourceTrustClass.CURRENT_STRUCTURAL,
            immutable_source_ref=f"working-tree:{display_revision}:{relative}",
            content_hash=f"sha256:{digest}",
            location=EvidenceLocation(f"repo://{relative}"),
            observed_at=observed_at,
            verification_status=VerificationStatus.VERIFIED,
        )

    @staticmethod
    def _relative_path(raw_path: str) -> PurePosixPath:
        if not isinstance(raw_path, str) or not raw_path.strip() or len(raw_path) > 512:
            raise CheckpointFileEvidenceError("checkpoint evidence path is invalid")
        relative = PurePosixPath(raw_path)
        if relative.is_absolute() or relative == PurePosixPath(".") or ".." in relative.parts:
            raise CheckpointFileEvidenceError("checkpoint evidence path is invalid")
        return relative

    @staticmethod
    def _digest(path: Path) -> str:
        digest = hashlib.sha256()
        try:
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "rb") as handle:
                while chunk := handle.read(65_536):
                    digest.update(chunk)
        except OSError as error:
            raise CheckpointFileEvidenceError("checkpoint evidence file is unavailable") from error
        return digest.hexdigest()

    def _git_display_revision(self) -> str:
        try:
            result = subprocess.run(
                ("git", "rev-parse", "--short=6", "HEAD"),
                cwd=self._root,
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
            value = result.stdout.strip()
            if value and all(character in "0123456789abcdef" for character in value):
                return value
        except (OSError, subprocess.SubprocessError):
            pass
        return "uncommitted"
