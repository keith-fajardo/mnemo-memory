"""Uvicorn launcher for the loopback-only local lifecycle API."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from mnemo_memory.apps.api.app import create_app
from mnemo_memory.packages.application import build_lifecycle_service, resolve_local_config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()
    config = resolve_local_config(Path(args.data_dir))
    service = build_lifecycle_service(config)
    service.initialize()
    uvicorn.run(
        create_app(service), host=config.host, port=config.port, log_level=config.log_level.lower()
    )


if __name__ == "__main__":
    main()
