# Use Mnemo with Codex and Claude Code

Mnemo is connected to Codex and Claude Code as a **local MCP server**. You may hear this called a
plugin, but Mnemo does not install an editor extension or change either client’s model. It registers
one local tool server named `mnemo-memory`.

That server provides exactly two tools:

- `save_checkpoint` — explicitly save, revise, complete, or abandon a task handoff.
- `get_context` — retrieve the matching saved handoff and optional dbt lineage facts.

The server starts when the client needs it and reads the local Mnemo database. It does not upload
that database, capture every conversation, or call a model on its own.

## Before connecting

Install the published package and make sure the command works:

```bash
uv tool install mnemo-unified-context==0.1.0a2
mnemo-memory --help
mnemo-memory init
```

`init` creates or opens Mnemo’s local data directory. It does **not** automatically save the
current repository, index all files, or make a chat durable. The agent must explicitly use
`save_checkpoint` while working.

By default, Codex and Claude Code launched normally will use the same personal Mnemo store. To use
an isolated store—for a test, demo, or separate profile—set an absolute path before launching the
client:

```bash
export MNEMO_DATA_DIR="/absolute/path/to/mnemo-data"
```

Use the same value for both clients when you want a Codex-saved checkpoint to be available in a
fresh Claude Code session, or the reverse.

## Connect Codex

Prerequisite: the `codex` CLI is installed and on your `PATH`.

```bash
mnemo-memory connect codex
```

Confirm the prompt. For a non-interactive or CI preview:

```bash
mnemo-memory connect codex --dry-run
mnemo-memory connect codex --yes
mnemo-memory connect codex --check
```

Mnemo asks Codex to register an MCP server named `mnemo-memory` using the absolute installed
launcher and these arguments:

```text
/absolute/path/to/mnemo-memory mcp serve --stdio
```

Inspect the registration with either command:

```bash
codex mcp get mnemo-memory --json
codex mcp list --json
```

Restart Codex after registration. In a coding session, it can then discover `save_checkpoint` and
`get_context`.

To remove only Mnemo’s owned registration:

```bash
mnemo-memory disconnect codex
```

## Connect Claude Code

Prerequisite: the `claude` CLI is installed and on your `PATH`.

```bash
mnemo-memory connect claude-code
```

The same safe controls are available:

```bash
mnemo-memory connect claude-code --dry-run
mnemo-memory connect claude-code --yes
mnemo-memory connect claude-code --check
```

Mnemo registers `mnemo-memory` in Claude Code’s **user** MCP scope. It does not write a project
`.mcp.json` file. Inspect it with:

```bash
claude mcp get mnemo-memory
claude mcp list
```

Restart Claude Code after registration; `/mcp` can show its MCP status. Remove Mnemo’s own
registration without affecting Codex or unrelated Claude entries:

```bash
mnemo-memory disconnect claude-code
```

## Connect both clients

You can and often should connect both:

```bash
mnemo-memory connect codex
mnemo-memory connect claude-code
```

These are two independent client registrations pointing to the same local `mnemo-memory` command.
They share durable checkpoints only when they use the same `MNEMO_DATA_DIR` and the same explicit
scope. Disconnecting one client does not disconnect the other or delete saved Mnemo data.

## How an agent uses the memory

Near the end of a task, ask the agent something concrete, for example:

> Save a Mnemo checkpoint: include the current implementation state, decisions, failed approach,
> tests run, evidence, and the exact next action.

The agent calls `save_checkpoint`. Mnemo stores a stable checkpoint identity plus an immutable
revision. It does not save the full transcript.

In a new client session, ask:

> Retrieve Mnemo context for this task before continuing. Use the saved checkpoint and, if needed,
> the current dbt lineage facts.

The agent calls `get_context`. The result is bounded, cites the exact checkpoint revision, and can
include deterministic dbt upstream/downstream facts. A completed or abandoned checkpoint is not
chosen as active work automatically.

## Scope, in practical terms

Mnemo requests use stable owner, workspace, project, session, and task UUIDs. They are not derived
from a directory or from dbt. They prevent one project’s checkpoint from being exposed to another
project. The current alpha keeps scope explicit; a future project-binding workflow will reduce
repeated setup for dbt command wrapping.

For manual dbt ingestion, generate and retain one stable owner/workspace/project UUID set, then
pass it to `mnemo-memory dbt ingest`. See the [README scope explanation](../README.md#scope-ids-in-plain-language).

## What Mnemo does not change

Connecting Mnemo does not change either client’s:

- model, provider, endpoint, or authentication;
- sandbox, approval, or permission policy;
- unrelated MCP registrations; or
- working-directory behavior.

It does not invoke a model or network request. Expected MCP tool failures are sanitized; a later
valid call can continue in the same server session.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `mnemo-memory` is not found | Install with `uv tool install mnemo-unified-context` and ensure uv’s tool directory is on `PATH`. |
| `MNEMO_CODEX_NOT_INSTALLED` or `MNEMO_CLAUDE_NOT_INSTALLED` | Install the relevant client CLI first; Mnemo never installs it for you. |
| A fresh client cannot find a checkpoint | Ensure both sessions use the same absolute `MNEMO_DATA_DIR` and the same scope; confirm the earlier agent actually saved a checkpoint. |
| The wrong project’s context is requested | Use the correct scope. Mnemo intentionally returns the same not-found result as for an unknown checkpoint. |
| The data directory is unavailable | Fix the explicit directory or remove the bad `MNEMO_DATA_DIR`; Mnemo will not silently switch to another database. |
| The installed launcher moved | Run the relevant `disconnect` command, then connect it again so the client stores the new absolute path. |

For a guided terminal explanation, run `mnemo-memory agent` (or `mnemo-memory guide`). It is safe
by default: it explains the chosen store and prints client commands; it does not initialize a store
unless you confirm or pass `--initialize`, and it never registers a client itself.
