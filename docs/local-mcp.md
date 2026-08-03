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

For a corrected analysis or reasoning mistake, `save_checkpoint` also accepts up to 16 canonical
`lessons`. Each lesson has a nonblank `trigger`, `mistaken_assumption`, `correction`, and
`prevention`, plus `evidence_ids` that must reference evidence submitted for that exact revision.
Mnemo preserves the lesson in the immutable checkpoint content and returns it in the later context
packet. It never infers a lesson from a transcript, source diff, or model output.

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
  "lessons": [{
    "trigger": "A reconciliation test disagreed with the Finance seed.",
    "mistaken_assumption": "The timestamp join represented the same business-day grain.",
    "correction": "Compare both inputs at the documented business-date grain.",
    "prevention": "Verify input grain and null behavior before changing a reconciliation join.",
    "evidence_ids": ["66666666-6666-4666-8666-666666666666"]
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

## Revision and terminal operations

Use the same scope, canonical content, and evidence fields from the create request. `revise`
replaces only the immutable current revision; it never changes the stable `checkpoint_id`:

```json
{"operation":"revise","checkpoint_id":"<checkpoint-id>","expected_revision_id":"<revision-id>","current_state":"updated state"}
```

Each revision is a complete current handoff. Include any still-applicable lesson again when you
revise it; the automatic Mnemo reminder tells a connected agent to do this. Earlier revisions
remain immutable and retrievable with their original evidence.

`complete` is explicit and succeeds only from the active state; its content must have no blockers
or remaining work. `abandon` is separate and records a nonblank reason in its terminal revision:

```json
{"operation":"complete","checkpoint_id":"<checkpoint-id>","expected_revision_id":"<revision-id>","current_state":"complete","remaining_work":[]}
{"operation":"abandon","checkpoint_id":"<checkpoint-id>","expected_revision_id":"<revision-id>","reason":"awaiting a decision"}
```

Each mutation compares `expected_revision_id` atomically. A losing writer receives
`MNEMO_REVISION_CONFLICT`; no extra revision, evidence link, or current pointer is created.
Identical terminal retries return the existing terminal revision, while incompatible retries fail.
Every revision is tied to the same explicit task scope and its submitted evidence references.

`get_context` needs only the same scope; add `checkpoint_id` to target an active checkpoint
explicitly. Returned context is untrusted evidence and cites the exact revision. It never returns a
transcript or silently truncates stored content. A 600-token checkpoint is accepted; a larger write
is rejected. A lower requested packet limit returns a structured `token_budget` omission instead.

## Durability and recovery

Acknowledged saves are committed transactionally to the resolved `mnemo.sqlite3` profile and remain
available to a new stdio server process using the same directory. A graceful or abrupt server stop
after acknowledgement does not create duplicate or partial revisions. SQLite integrity and
foreign-key checks are part of the durability suite. Corrupt, unavailable, and newer-than-supported
profiles fail startup without changing paths or falling back to an empty database. Migration is
forward-only: back up the local profile before an upgrade and restore that backup for recovery.

Codex and Claude launch the same absolute `mnemo mcp serve --stdio` command and therefore resolve
the same Mnemo profile independent of their project working directory. Installation does not change
models, providers, authentication, permissions, or network settings. Issue 11 will add the
fresh-session task-resumption fixture; Mnemo currently relies on explicit saves and never ingests
transcripts automatically.
