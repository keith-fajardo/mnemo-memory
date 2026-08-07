# Connect Claude Code (Issue 9)

Tested with Claude Code `2.1.220`. Install Mnemo and Claude Code, then run
`mnemo connect claude-code` from the repository you want Claude Code to remember. It registers the
local MCP server and enables bounded automatic project memory immediately. Existing
`dbt_project.yml` and `target/manifest.json` files are detected and indexed without running dbt or
other project code. Use `--dry-run` to preview the connection, `--confirm` if you want an
interactive prompt, `--auto-memory-disable` to register MCP without project memory, and `--check`
to inspect without mutation. The longer `mnemo-memory` command remains compatible. Mnemo registers
`mnemo-memory` in Claude Code's `user` scope through `claude mcp add --scope user`, never a project
`.mcp.json` file.

Inspect with `claude mcp list` or `claude mcp get mnemo-memory`; remove only the Mnemo-owned entry
with `mnemo disconnect claude-code`. If Mnemo moves, disconnect and reconnect. Conflicting entries
are preserved. Use `/mcp` inside Claude Code to inspect status.

Mnemo changes no Claude model, endpoint, provider, authentication, permissions, sandbox, approval,
or network settings and does not require a subscription or API request to install. The tools use
Mnemo's local durable checkpoint profile; integration tests use an isolated `MNEMO_DATA_DIR`.

Integration tests isolate Claude Code by setting `HOME` to a temporary directory (including paths
with spaces) and remove Anthropic environment variables. Claude Code 2.1.220 has no JSON MCP
inspection flag, so tests use `claude mcp get` and `list`, then validate the isolated
`~/.claude.json` structurally. No login or model request is made.
