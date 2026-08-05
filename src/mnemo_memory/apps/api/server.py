"""Uvicorn launcher for the loopback-only local lifecycle API."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from mnemo_memory.apps.api.app import create_app
from mnemo_memory.apps.api.dashboard import build_dashboard_status
from mnemo_memory.apps.api.jobs import retry_failed_event_jobs
from mnemo_memory.apps.api.memories import (
    build_approved_memory_export,
    build_approved_memory_page,
    correct_approved_memory,
    retract_approved_memory,
    set_approved_memory_pin,
)
from mnemo_memory.packages.application import (
    PersonalSettingsStore,
    build_lifecycle_service,
    resolve_local_config,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()
    config = resolve_local_config(Path(args.data_dir))
    service = build_lifecycle_service(config)
    service.initialize()
    uvicorn.run(
        create_app(
            service,
            lambda: build_dashboard_status(config),
            PersonalSettingsStore(config.data_directory),
            lambda offset, limit: build_approved_memory_page(config, offset=offset, limit=limit),
            lambda event_id, value: correct_approved_memory(config, event_id, value),
            lambda event_id, value: retract_approved_memory(config, event_id, value),
            lambda event_id, value: set_approved_memory_pin(config, event_id, value),
            lambda: build_approved_memory_export(config),
            lambda: retry_failed_event_jobs(config),
        ),
        host=config.host,
        port=config.port,
        log_level=config.log_level.lower(),
    )


if __name__ == "__main__":
    main()
