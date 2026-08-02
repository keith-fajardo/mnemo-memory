# Connect Codex (Issue 8)

Prerequisites: install Mnemo as an executable named `mnemo`, and install a Codex CLI that supports
`codex mcp add`, `get --json`, `list --json`, and `remove`.

Run `mnemo connect codex` and confirm the prompt, or use `mnemo connect codex --yes` for an
explicit non-interactive registration. `--dry-run` shows the exact argument-array launcher without
changing Codex. `--check` reports whether the named entry exists without changing it.

Mnemo registers one stdio server named `mnemo-memory` with the absolute launcher array:
`["/absolute/path/to/mnemo", "mcp", "serve", "--stdio"]`. Inspect it with
`codex mcp get mnemo-memory --json` or `codex mcp list --json`. Use
`mnemo disconnect codex` to remove only an entry whose command and arguments match Mnemo's owned
identity. If Mnemo moves or is upgraded at a different absolute path, disconnect and reconnect.

This changes no Codex model, provider, authentication, sandbox, approval, or network setting and
does not proxy model traffic. The two exposed tools remain fixture-backed; durable checkpoint
persistence begins in Issue 10. Claude Code integration is deferred to Issue 9.
