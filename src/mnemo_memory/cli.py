"""Installed ``mnemo-memory`` console entry point."""

from __future__ import annotations

from mnemo_memory.apps.cli.main import app


def main() -> None:
    app()


if __name__ == "__main__":
    main()
