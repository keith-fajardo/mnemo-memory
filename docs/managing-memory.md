# Review, correct, and forget Mnemo memory

Mnemo keeps different kinds of context separate. That makes correction and deletion safer, but it
also means there is no honest single “forget” action that means the same thing for every record.
Use this guide to choose the action that matches what you want removed or changed.

## Start by deciding what you mean by “memory”

| What you see | What it is | How it changes |
| --- | --- | --- |
| Current task handoff | An evidence-backed checkpoint saved by the agent | Revise, complete, abandon, or let retention expire it |
| Correction lesson | An explicit record of a mistaken assumption and its correction | Add a later evidence-backed lesson; do not rewrite history silently |
| Approved decision, failure, or tool outcome | A small independently saved fact | Correct or retract it explicitly |
| Repository documentation | Current bounded Markdown sections | Edit or delete the source note, then sync |
| Obsidian knowledge | Current bounded Markdown from one enabled vault | Edit/delete notes or disable the vault |
| Source or dbt structure | A rebuildable projection | Refresh it from the current source or manifest |
| Backup or exported JSON | A user-controlled copy | Remove it yourself according to your backup policy |

All actions remain scoped to the enabled project. Disconnecting a client does not delete memory,
and deleting one kind of context does not silently delete the others.

## Inspect what is active

From an enabled project, inspect the current task handoff and its provenance:

```bash
mnemo memory inspect
```

List independently approved facts:

```bash
mnemo memory events
mnemo memory event inspect EVENT_ID
```

The local dashboard offers the same project-scoped approved-fact view:

```bash
mnemo start
```

Open `http://127.0.0.1:8765/`. Stop it with `mnemo stop`.

## Update a task handoff

In an automatic-memory-enabled Codex or Claude Code session, tell the agent what changed and ask it
to revise the active Mnemo checkpoint. A good update includes:

- the current state;
- decisions and failed approaches that still matter;
- verification performed;
- relevant files and evidence; and
- the exact next action.

Checkpoint revisions are immutable. A revision creates a new current handoff while retaining the
earlier evidence-bearing history. Completing or abandoning a checkpoint prevents it from being
selected as the active handoff.

If you are integrating the MCP tool directly, the exact `create`, `revise`, `complete`, and
`abandon` operations are documented in the [local MCP reference](local-mcp.md#revision-and-terminal-operations).

## Record a correction lesson

Mnemo does not infer a model's private reasoning or automatically decide that a failed command is a
lesson. Ask the agent to record a correction when a mistake is important enough to prevent later.
The lesson should state:

- what triggered the mistake;
- the assumption that was wrong;
- the evidence-backed correction; and
- how to avoid repeating it.

If the handoff already exists, the agent can append one lesson without reconstructing the whole
checkpoint. The MCP operation is `save_checkpoint` with `operation: "record_lesson"`; see
[Record a correction without resending the handoff](local-mcp.md#record-a-correction-without-resending-the-handoff).

This is additive correction, not silent rewriting. A later session can see both the historical
lesson and its evidence while checking current project structure separately.

## Correct or retract an approved fact

An approved fact is one explicit evidence-backed decision, failure, or bounded tool outcome saved
outside the full checkpoint.

Correct a wrong fact by appending a same-kind replacement:

```bash
mnemo memory event correct EVENT_ID \
  --summary "Corrected factual summary" \
  --reason "Why the retained fact was wrong" \
  --yes
```

Retract a fact that should no longer retain its payload:

```bash
mnemo memory event retract EVENT_ID \
  --reason "Why this fact is being withdrawn" \
  --yes
```

Correction preserves a link from the old fact to the replacement. Retraction removes the original
summary, stable source key, and evidence links from active retention, while keeping only a bounded
tombstone and the evidence for the retraction. Context returns only active facts.

These commands do not delete checkpoints, notes, exports, or backups. Confirmation is required
unless `--yes` is supplied.

## Update or delete project documentation

Repository Markdown is source-controlled knowledge, not an independently editable Mnemo fact.
Edit the note when guidance changes, or delete the note when it should no longer exist. At the next
safe sync, Mnemo creates the new current revision or removes the deleted content from current
search.

Automatic memory refreshes enabled repository notes at session and work boundaries. To refresh the
project's structure explicitly after changes, run:

```bash
mnemo scan
```

Old and deleted note bodies are removed from the current full-text and optional semantic search
projections. A user-held Git revision, export, or backup remains outside Mnemo's deletion control.

## Stop or remove an Obsidian vault

Editing or deleting a note in the vault updates Mnemo at its next vault sync. To stop using the
entire vault and remove its retained content-bearing revisions:

```bash
mnemo memory vault status
mnemo memory vault disable
```

Disable performs the content-removal sync before deleting the binding. If reconciliation fails,
the binding remains so Mnemo does not falsely claim the content was removed. Project checkpoints,
repository notes, and structural memory remain unaffected.

## Refresh rebuildable structure

Source and dbt indexes are projections, not durable user-authored memories. Refresh them when the
underlying repository or manifest changes:

```bash
mnemo scan
mnemo dbt status
```

Disabling dbt wrapping stops future automatic ingestion but does not claim to delete earlier
snapshots:

```bash
mnemo dbt disable
```

Structural context should always cite its immutable snapshot and state whether currentness is
current, stale, or unknown.

## Let inactive checkpoints expire

The local dashboard Settings section controls the personal episodic-retention default. At an
automatic-memory session start, an active checkpoint whose last canonical write is at least that
many days old can be marked expired and excluded from current selection. Reading it does not renew
the retention clock.

Expiry is not physical erasure: it preserves immutable audit history. The personal CLI currently
does not present a general command for selectively destroying an arbitrary checkpoint while
retaining every other record. Do not treat `disconnect`, `memory disable`, completion, abandonment,
or expiry as deletion.

Team deployments have operator-governed retention and physical-erasure workflows with backup
deletion propagation; see the [Team guide](team-guide.md).

## Disable automation without deleting data

Remove only Mnemo's automatic task-memory hooks:

```bash
mnemo memory disable
```

Or disconnect one client registration:

```bash
mnemo disconnect codex
mnemo disconnect claude-code
```

Both actions preserve saved Mnemo data. Disconnecting one client does not affect the other.

## Uninstall or erase all recognized local data

Normal uninstall removes the uv- or pipx-owned application, exact Mnemo-owned client registrations,
and hooks while preserving the configured data directory:

```bash
mnemo uninstall --yes
```

To erase that recognized local data directory as part of uninstall, use the deliberately separate
destructive form:

```bash
mnemo uninstall --delete-data --yes
```

Create a backup first only if you intend to keep a recovery copy:

```bash
mnemo backup
```

A backup contains the same private data as the live store. Mnemo cannot erase a backup, export,
copied database, package-manager cache, or repository history after you move it outside Mnemo's
control. Delete those copies according to your own retention policy.

## A simple decision rule

- **Wrong but worth preserving:** correct it and keep the evidence trail.
- **No longer valid for current work:** revise, complete, abandon, expire, edit, or disable the
  relevant source.
- **Payload should no longer be retained:** retract the approved fact or remove the source note or
  vault.
- **Remove the entire personal store:** use the explicit `--delete-data` uninstall path and manage
  every external backup or export separately.
