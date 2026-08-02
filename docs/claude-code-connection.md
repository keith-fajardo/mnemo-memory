# Connect Claude Code (Issue 9)

Tested with Claude Code `2.1.220`. Install Mnemo and Claude Code, then run
`mnemo-memory connect claude-code`; use `--yes` for non-interactive confirmation, `--dry-run` to preview,
and `--check` to inspect without mutation. Mnemo registers `mnemo-memory` in Claude Code's `user`
scope through `claude mcp add --scope user`, never a project `.mcp.json` file.

Inspect with `claude mcp list` or `claude mcp get mnemo-memory`; remove only the Mnemo-owned entry
with `mnemo-memory disconnect claude-code`. If Mnemo moves, disconnect and reconnect. Conflicting entries
are preserved. Use `/mcp` inside Claude Code to inspect status.

Mnemo changes no Claude model, endpoint, provider, authentication, permissions, sandbox, approval,
or network settings and does not require a subscription or API request to install. The tools use
Mnemo's local durable checkpoint profile; integration tests use an isolated `MNEMO_DATA_DIR`.

Integration tests isolate Claude Code by setting `HOME` to a temporary directory (including paths
with spaces) and remove Anthropic environment variables. Claude Code 2.1.220 has no JSON MCP
inspection flag, so tests use `claude mcp get` and `list`, then validate the isolated
`~/.claude.json` structurally. No login or model request is made.
