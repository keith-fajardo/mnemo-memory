# Local lifecycle commands

Issue 6 provides a personal-profile lifecycle foundation only. From a source checkout, use
`./mnemo init`, `./mnemo start`, `./mnemo status`, and `./mnemo stop` with `--data-dir` when an
explicit local data directory is needed. The same Typer adapter is available as
`uv run python -m apps.cli.main` for development.
The environment prefix is `MNEMO_`; currently `MNEMO_DATA_DIR` selects the default data directory.

`init` safely creates `config.json`, the local SQLite database, and its schema without overwriting
an existing configuration. `start` launches the minimal lifecycle API on `127.0.0.1:8765`; `status`
reads local process state; `stop` sends a local termination signal to the recorded process. The API
has only `/live`, `/ready`, `/version`, and `/process` endpoints. Interactive API documentation and
permissive CORS are disabled.

The JSON configuration is closed and contains only the data directory, database path, loopback host,
port, log level, process-state path, and the `personal` profile. It rejects non-loopback bindings,
unknown fields, unsafe paths, credentials, and model configuration. All timestamps and SQLite schema
checks remain local. Codex/Claude Code setup, MCP, context retrieval, checkpoint capture, and all
remote or team operation are intentionally deferred.
