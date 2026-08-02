"""Uvicorn launcher for the loopback-only local lifecycle API."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from apps.api.app import create_app
from packages.application import LocalConfig, build_lifecycle_service


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()
    config_path = Path(args.data_dir) / "config.json"
    config = LocalConfig.load(config_path)
    service = build_lifecycle_service(config)
    service.initialize()
    uvicorn.run(
        create_app(service), host=config.host, port=config.port, log_level=config.log_level.lower()
    )


if __name__ == "__main__":
    main()
