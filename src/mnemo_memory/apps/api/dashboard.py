"""Sanitized read-only status composition for the personal loopback dashboard."""

from __future__ import annotations

import shutil
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from mnemo_memory.connectors.automatic_memory.git_observation import (
    GitObservationStore,
    GitSourceObservation,
    GitSourceObserver,
)
from mnemo_memory.connectors.claude_code.mcp_config import ClaudeMcpManager
from mnemo_memory.connectors.codex.mcp_config import CodexMcpManager
from mnemo_memory.packages.application import LocalConfig, build_checkpoint_runtime
from mnemo_memory.packages.application.automatic_memory import (
    AutomaticMemoryBindingError,
    LocalMemoryProjectBindingStore,
)
from mnemo_memory.packages.domain import CodeSnapshot, MemoryScope


def build_dashboard_status(
    config: LocalConfig, *, project_directory: Path | None = None
) -> dict[str, object]:
    """Return status and counts only; payloads, paths, IDs, and subprocess output are excluded."""
    binding = None
    with suppress(AutomaticMemoryBindingError, OSError, ValueError):
        binding = LocalMemoryProjectBindingStore(config.data_directory).get(
            project_directory or Path.cwd()
        )
    indexes: dict[str, object] = {
        "knowledge": {
            "status": "not_registered",
            "documents": 0,
            "staleness": "unknown",
            "last_sync_at": None,
        },
        "source": {
            "status": "not_registered",
            "files": 0,
            "symbols": 0,
            "relationships": 0,
            "staleness": "unknown",
            "last_sync_at": None,
        },
        "dbt": {
            "status": "not_registered",
            "nodes": 0,
            "relationships": 0,
            "staleness": "unknown",
            "last_sync_at": None,
        },
    }
    if binding is not None:
        try:
            with build_checkpoint_runtime(config) as runtime:
                assert runtime.source_structure_repository is not None
                assert runtime.knowledge_document_repository is not None
                source = runtime.source_structure_repository.get_active_snapshot(binding.scope)
                dbt = runtime.repository.get_active_snapshot(binding.scope)
                knowledge = runtime.knowledge_document_repository.list_active_documents(
                    binding.scope
                )
                indexes = {
                    "knowledge": {
                        "status": "ready" if knowledge else "empty",
                        "documents": len(knowledge),
                        "staleness": "unknown",
                        "last_sync_at": _timestamp(
                            runtime.knowledge_document_repository.last_sync_at(binding.scope)
                        ),
                    },
                    "source": {
                        "status": "ready" if source is not None else "empty",
                        "files": 0 if source is None else source.file_count,
                        "symbols": 0 if source is None else source.symbol_count,
                        "relationships": 0 if source is None else source.edge_count,
                        "staleness": _source_staleness(
                            config, binding.project_root, binding.scope, source
                        ),
                        "last_sync_at": _timestamp(
                            runtime.source_structure_repository.last_sync_at(binding.scope)
                        ),
                    },
                    "dbt": {
                        "status": "ready" if dbt is not None else "empty",
                        "nodes": 0 if dbt is None else dbt.node_count,
                        "relationships": 0 if dbt is None else dbt.edge_count,
                        "staleness": ("unknown" if dbt is None else dbt.metadata.currentness.value),
                        "last_sync_at": _timestamp(runtime.repository.last_sync_at(binding.scope)),
                    },
                }
        except Exception:
            indexes = {
                "knowledge": {
                    "status": "unavailable",
                    "documents": 0,
                    "staleness": "unknown",
                    "last_sync_at": None,
                },
                "source": {
                    "status": "unavailable",
                    "files": 0,
                    "symbols": 0,
                    "relationships": 0,
                    "staleness": "unknown",
                    "last_sync_at": None,
                },
                "dbt": {
                    "status": "unavailable",
                    "nodes": 0,
                    "relationships": 0,
                    "staleness": "unknown",
                    "last_sync_at": None,
                },
            }
    return {
        "connections": {
            "claude_code": _claude_status(),
            "codex": _codex_status(),
        },
        "indexes": indexes,
        "privacy": {
            "exposure": "loopback_only",
            "model_calls": "disabled_by_default",
            "profile": "personal_sqlite",
            "retrieved_content": "untrusted_evidence",
        },
        "project": {"registered": binding is not None},
    }


def _timestamp(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


def _source_staleness(
    config: LocalConfig,
    project_root: Path,
    scope: MemoryScope,
    snapshot: CodeSnapshot | None,
) -> str:
    if snapshot is None:
        return "unknown"
    stored = GitObservationStore(config.data_directory).get(scope, snapshot.source_digest)
    current = GitSourceObserver().observe(project_root, snapshot.source_digest)
    return _compare_source_observations(stored, current)


def _compare_source_observations(
    stored: GitSourceObservation | None, current: GitSourceObservation | None
) -> str:
    if stored is None or current is None:
        return "unknown"
    if stored.commit_id != current.commit_id or stored.dirty != current.dirty:
        return "stale"
    return "unknown" if stored.dirty else "current"


def _launcher() -> Path | None:
    value = shutil.which("mnemo-memory")
    return None if value is None else Path(value).resolve()


def _codex_status() -> dict[str, object]:
    launcher = _launcher()
    if launcher is None or shutil.which("codex") is None:
        return {"available": False, "connected": False, "status": "not_installed"}
    try:
        manager = CodexMcpManager.discover(launcher)
        entry = manager.inspect()
        return {
            "available": True,
            "connected": entry is not None and manager.is_owned(entry),
            "status": "connected" if entry is not None and manager.is_owned(entry) else "available",
        }
    except Exception:
        return {"available": True, "connected": False, "status": "unavailable"}


def _claude_status() -> dict[str, object]:
    launcher = _launcher()
    if launcher is None or shutil.which("claude") is None:
        return {"available": False, "connected": False, "status": "not_installed"}
    try:
        manager = ClaudeMcpManager.discover(launcher)
        detail = manager.inspect()
        return {
            "available": True,
            "connected": detail is not None and manager.is_owned(detail),
            "status": (
                "connected" if detail is not None and manager.is_owned(detail) else "available"
            ),
        }
    except Exception:
        return {"available": True, "connected": False, "status": "unavailable"}
