# Connect Codex (Issue 8)

Prerequisites: install Mnemo as the `mnemo` command, and install a Codex CLI that supports
`codex mcp add`, `get --json`, `list --json`, and `remove`.

Run `mnemo connect codex` from the repository you want Codex to remember. It registers the local MCP
server and enables bounded automatic project memory immediately. Existing `dbt_project.yml` and
`target/manifest.json` files are detected and indexed without running dbt or other project code.
Use `--dry-run` to preview the connection, `--confirm` if you want an interactive prompt,
`--auto-memory-disable` to register MCP without project memory, and `--check` to inspect the named
entry without changing it. The longer `mnemo-memory` command remains compatible.

Mnemo registers one stdio server named `mnemo-memory` with the absolute launcher array:
`["/absolute/path/to/mnemo-memory", "mcp", "serve", "--stdio"]`. Inspect it with
`codex mcp get mnemo-memory --json` or `codex mcp list --json`. Use
`mnemo disconnect codex` to remove only an entry whose command and arguments match Mnemo's owned
identity. If Mnemo moves or is upgraded at a different absolute path, disconnect and reconnect.

This changes no Codex model, provider, authentication, sandbox, approval, or network setting and
does not proxy model traffic. The two exposed tools use Mnemo's local durable checkpoint profile;
tests set an isolated `MNEMO_DATA_DIR`. No automatic transcript capture or model call is performed.
