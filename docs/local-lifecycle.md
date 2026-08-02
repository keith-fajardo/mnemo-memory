# Local lifecycle commands

Issue 6 provides a personal-profile lifecycle foundation only. From a source checkout, use
`./mnemo init`, `./mnemo start`, `./mnemo status`, and `./mnemo stop` with `--data-dir` when an
explicit local data directory is needed. The same Typer adapter is available as
`uv run python -m apps.cli.main` for development.
The environment prefix is `MNEMO_`; `MNEMO_DATA_DIR` overrides the data directory. Resolution is
deterministic: an explicit `--data-dir`, then `MNEMO_DATA_DIR`, then an existing configuration in
the platform default, then the platform default itself. The macOS default is
`~/Library/Application Support/Mnemo`; Linux follows `XDG_DATA_HOME` (or `~/.local/share/mnemo`),
and Windows uses `%LOCALAPPDATA%/Mnemo`. Direct and environment paths must be absolute so a
working-directory change cannot create another local profile. The canonical database file is
`mnemo.sqlite3` under that directory.

`init` safely creates `config.json`, the local SQLite database, and its schema without overwriting
an existing configuration. `start` launches the minimal lifecycle API on `127.0.0.1:8765`; `status`
reads local process state; `stop` sends a local termination signal to the recorded process. The API
has only `/live`, `/ready`, `/version`, and `/process` endpoints. Interactive API documentation and
permissive CORS are disabled.

The JSON configuration is closed and contains only the data directory, database path, loopback host,
port, log level, process-state path, and the `personal` profile. It rejects non-loopback bindings,
unknown fields, unsafe paths, credentials, and model configuration. All timestamps and SQLite schema
checks remain local. The durable checkpoint runtime composes this same configuration, the migrated
SQLite profile, and the storage-independent checkpoint application service. It creates no global
connection: each SQLite operation uses the profile's foreign-key, WAL, busy-timeout, and transaction
policies. Opening an invalid, unavailable, or newer-than-supported configured database fails safely;
Mnemo does not fall back to a different or empty profile. MCP remains fixture-backed until Issue
10B.2b.
