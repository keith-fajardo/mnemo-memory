# Local MCP server

Mnemo exposes one local stdio MCP server, `mnemo-local` version `0.1.0`:

```sh
mnemo mcp serve --stdio
```

It resolves the same durable personal data directory as the local lifecycle commands. Use an
absolute `--data-dir` or `MNEMO_DATA_DIR` for an isolated profile. The server writes only MCP
protocol messages to stdout and diagnostics to stderr; it makes no network request, model call,
or transcript capture.

## Tools

Exactly two tools are exposed. `get_context` is read-only and accepts the explicit task scope
(`owner_id`, `workspace_id`, `project_id`, `session_id`, and `task_id`), optional `checkpoint_id`,
and optional `active_task_checkpoint_tokens` / `total_tokens` budgets. It returns the canonical
versioned context packet. With no active checkpoint it returns a valid empty packet. Completed and
abandoned checkpoints are excluded from automatic selection. The active-checkpoint section is hard
limited to 600 tokens by default and structured token-budget omissions are preserved.

`save_checkpoint` is mutating but non-destructive. It requires a tagged `operation` of `create`,
`revise`, `complete`, or `abandon`; the explicit task scope; canonical checkpoint payload fields;
and structurally valid evidence references. All operations retain the submitted content and
provenance. `revise`, `complete`, and `abandon` require `checkpoint_id` plus
`expected_revision_id`; `abandon` also requires a nonblank `reason`.

```json
{
  "operation": "create",
  "owner_id": "11111111-1111-4111-8111-111111111111",
  "workspace_id": "22222222-2222-4222-8222-222222222222",
  "project_id": "33333333-3333-4333-8333-333333333333",
  "session_id": "44444444-4444-4444-8444-444444444444",
  "task_id": "55555555-5555-4555-8555-555555555555",
  "task_objective": "Resume the task",
  "current_state": "implementation in progress",
  "evidence_references": [{
    "evidence_id": "66666666-6666-4666-8666-666666666666",
    "source_id": "77777777-7777-4777-8777-777777777777",
    "source_type": "checkpoint", "trust_class": "user_authored",
    "immutable_source_ref": "synthetic://example",
    "content_hash": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "location": {"uri": "fixture://example", "start_line": null, "start_column": null, "end_line": null, "end_column": null},
    "observed_at": "2026-08-02T14:00:00+00:00", "verification_status": "verified"
  }],
  "token_estimate": 120
}
```

Successful saves return the logical checkpoint ID, immutable revision ID, revision number,
lifecycle status, scope, and `"persistence": "durable"`. Expected failures use sanitized MCP
codes including `MNEMO_INVALID_INPUT`, `MNEMO_EVIDENCE_REQUIRED`,
`MNEMO_CHECKPOINT_NOT_FOUND`, `MNEMO_REVISION_CONFLICT`, `MNEMO_INVALID_LIFECYCLE`,
`MNEMO_TOKEN_BUDGET`, and `MNEMO_STORAGE_UNAVAILABLE`. They never contain SQL, paths, tracebacks,
or unrelated checkpoint content.

The synthetic `FixtureMcpContextPort` remains available only through explicit test injection; the
production launcher never selects it. Automatic transcript ingestion, LLM extraction, and fresh
cross-client resume evaluation remain out of scope.
