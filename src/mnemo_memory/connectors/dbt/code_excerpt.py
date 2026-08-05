"""Bounded current-file excerpts for already-authorized dbt manifest nodes."""

from __future__ import annotations

import json
import stat
from collections.abc import Callable
from datetime import datetime
from hashlib import sha256
from pathlib import PurePosixPath
from uuid import UUID, uuid5

from mnemo_memory.connectors.dbt.project_binding import (
    DbtProjectBindingError,
    LocalDbtProjectBindingStore,
)
from mnemo_memory.packages.application.unified_context import DbtCodeExcerpt
from mnemo_memory.packages.domain import (
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    MemoryScope,
    SourceId,
    SourceTrustClass,
    VerificationStatus,
)
from mnemo_memory.packages.policy.knowledge import contains_high_confidence_secret

_SOURCE_NAMESPACE = UUID("d8cbbc36-a0ef-4ce8-9f26-c1f8db2e6887")
_EVIDENCE_NAMESPACE = UUID("742c09be-dc11-44da-82c2-16c4eecce69d")
_ALLOWED_SUFFIXES = frozenset({".sql", ".yml", ".yaml"})
_MAX_FILE_BYTES = 1_000_000
_MAX_EXCERPT_BYTES = 4_000


class DbtLocalCodeExcerptReader:
    def __init__(
        self,
        bindings: LocalDbtProjectBindingStore,
        clock: Callable[[], datetime],
    ) -> None:
        self._bindings = bindings
        self._clock = clock

    def read(
        self,
        scope: MemoryScope,
        relative_path: str,
        *,
        start_line: int,
        maximum_lines: int,
    ) -> DbtCodeExcerpt | None:
        if start_line < 1 or not 1 <= maximum_lines <= 40:
            return None
        try:
            binding = self._bindings.get_for_scope(scope)
            relative = PurePosixPath(relative_path)
            if (
                binding is None
                or relative.is_absolute()
                or not relative.parts
                or ".." in relative.parts
                or relative.as_posix() != relative_path
                or relative.suffix.lower() not in _ALLOWED_SUFFIXES
            ):
                return None
            candidate = binding.project_root.joinpath(*relative.parts)
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(binding.project_root)
            metadata = resolved.stat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_FILE_BYTES:
                return None
            with resolved.open("rb") as handle:
                raw = handle.read(_MAX_FILE_BYTES + 1)
            if len(raw) > _MAX_FILE_BYTES:
                return None
            text = raw.decode("utf-8")
        except (DbtProjectBindingError, OSError, UnicodeError, ValueError):
            return None
        if "\x00" in text:
            return None
        lines = text.splitlines()
        if start_line > len(lines):
            return None
        selected: list[str] = []
        used_bytes = 0
        for line in lines[start_line - 1 : start_line - 1 + maximum_lines]:
            added = len(line.encode("utf-8")) + (1 if selected else 0)
            if used_bytes + added > _MAX_EXCERPT_BYTES:
                break
            selected.append(line)
            used_bytes += added
        if not selected:
            return None
        content = "\n".join(selected)
        if contains_high_confidence_secret(content):
            return None
        end_line = start_line + len(selected) - 1
        file_digest = sha256(raw).hexdigest()
        excerpt_digest = sha256(content.encode("utf-8")).hexdigest()
        scope_key = json.dumps(scope.to_dict(), sort_keys=True, separators=(",", ":"))
        source_id = SourceId(uuid5(_SOURCE_NAMESPACE, f"{scope_key}:{relative_path}:{file_digest}"))
        immutable_ref = f"mnemo:repository-file/sha256:{file_digest}/{relative_path}"
        try:
            evidence = EvidenceReference(
                EvidenceId(
                    uuid5(
                        _EVIDENCE_NAMESPACE,
                        f"{source_id}:{start_line}:{end_line}:{excerpt_digest}",
                    )
                ),
                source_id,
                EvidenceSourceType.REPOSITORY,
                SourceTrustClass.CURRENT_STRUCTURAL,
                immutable_ref,
                f"sha256:{excerpt_digest}",
                EvidenceLocation(
                    immutable_ref,
                    start_line,
                    0,
                    end_line,
                    len(selected[-1]),
                ),
                self._clock(),
                VerificationStatus.VERIFIED,
            )
        except (TypeError, ValueError):
            return None
        return DbtCodeExcerpt(relative_path, start_line, end_line, content, evidence)
