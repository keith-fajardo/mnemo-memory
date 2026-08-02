# Local MCP server (Issue 7)

Mnemo provides a local, stdio-only MCP server for synthetic Issue 7 fixtures:

```sh
uv run python -m apps.cli.main mcp serve --stdio
```

The server is named `mnemo-local` and currently reports version `0.1.0`. It writes only MCP
protocol messages to standard output. Diagnostics are sent to standard error. It neither starts a
network listener nor proxies, observes, or modifies any coding-agent model traffic.

## Tools

`get_context` is read-only (`readOnlyHint: true`, `destructiveHint: false`,
`openWorldHint: false`). It accepts an explicit owner UUID and a non-empty query of at most 4,000
characters. It returns a valid, empty version-1 Mnemo context packet scoped to that owner. The
packet is synthetic fixture data: no SQLite data, retrieval, ranking, embeddings, or durable memory
is read.

```json
{"owner_id":"11111111-1111-4111-8111-111111111111","query":"resume synthetic task"}
```

`save_checkpoint` is state-changing but non-destructive (`readOnlyHint: false`,
`destructiveHint: false`, `openWorldHint: false`). It accepts an explicit owner UUID, one to 64
synthetic evidence-reference strings, and an optional sensitivity value. It returns a stable
fixture checkpoint ID and revision, marked `fixture-only`. It does not write a database or claim
production durability; durable checkpoint lifecycle work remains Issue 10.

```json
{
  "owner_id":"11111111-1111-4111-8111-111111111111",
  "evidence_references":["synthetic-evidence:issue-7"],
  "sensitivity":"normal"
}
```

Both schemas reject unknown fields and enforce their declared bounds. `save_checkpoint` rejects
`prohibited` sensitivity and missing evidence. Tool errors are protocol-valid and use concise,
redacted codes: `MNEMO_INVALID_INPUT`, `MNEMO_EVIDENCE_REQUIRED`, and
`MNEMO_PROHIBITED_CONTENT`. No real paths, credentials, checkpoint contents, stack traces, or
storage details are returned.

## Current limitations

This adapter uses a replaceable application port backed by synthetic, non-sensitive fixtures. It
does not configure Codex or Claude Code; those integrations are deferred to Issues 8 and 9. It also
does not implement automatic capture, retrieval, semantic search, remote MCP, OAuth, dbt,
Obsidian, UI, or team functionality.
