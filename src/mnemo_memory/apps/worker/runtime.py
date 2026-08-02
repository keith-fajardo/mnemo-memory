"""Local adapter composition for optional project-intelligence capabilities."""

from __future__ import annotations

from mnemo_memory.connectors.dbt.manifest import DbtManifestParser
from mnemo_memory.packages.application.bootstrap import CheckpointRuntime, build_checkpoint_runtime
from mnemo_memory.packages.application.config import LocalConfig


def build_local_runtime(config: LocalConfig) -> CheckpointRuntime:
    """Compose local SQLite services with the offline dbt manifest parser adapter."""
    return build_checkpoint_runtime(config, dbt_parser=DbtManifestParser())
