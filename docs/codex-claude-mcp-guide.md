# Use Mnemo with Codex and Claude Code

Mnemo is connected to Codex and Claude Code as a **local MCP server**. You may hear this called a
plugin, but Mnemo does not install an editor extension or change either client’s model. It registers
one local tool server named `mnemo-memory`.

That server provides exactly five tools:

- `save_checkpoint` — explicitly save, revise, complete, or abandon a task handoff.
- `get_context` — retrieve the matching saved handoff and optional dbt lineage facts.
- `explain_context` — inspect sources, ranks, omissions, conflicts, staleness, and token use for a
  returned packet without repeating its retrieved content.
- `list_skills` — list current checked-in project-skill metadata compatible with the calling client.
- `get_skill` — retrieve one exact checked-in compatible skill as cited untrusted evidence.

The server starts when the client needs it and reads the local Mnemo database. It does not upload
that database, capture every conversation, or call a model on its own.

For a plain-language explanation of revisions, correction lessons, fact retraction, note removal,
retention, and full local-data erasure, see
[Review, correct, and forget Mnemo memory](managing-memory.md).

`get_context` returns the canonical structured packet by default. A direct tool caller may set
`render_for` to `codex` or `claude-code`; the response then contains the unchanged packet beside a
deterministic agent-readable rendering. Automatic-memory hooks select this rendering themselves.
Every retrieved value stays JSON-quoted under a fixed trust boundary, with provenance, conflicts,
omissions, and canonical token accounting intact.

## Before connecting

Install the published package and make sure the command works:

```bash
uv tool install mnemo-unified-context==0.1.0a16
mnemo --help
mnemo init
```

`init` creates or opens Mnemo’s local data directory. It does **not** automatically index every
source file or make a chat durable. For normal use, connect from the project directory. Confirming
that connection enables automatic task memory by default, creates a local project binding, indexes
a bounded source-structure map, and installs lifecycle reminders:

```bash
mnemo connect codex
# or
mnemo connect claude-code
```

This creates a private local project binding; you never enter scope UUIDs. At a new session, the
hook attaches the bounded saved checkpoint, lessons, approved facts, and latest bounded source
transition automatically in the configured client's rendering, then asks
the agent to create a compact checkpoint at a stop boundary. For Codex compaction, the PreCompact
hook records the pending handoff without emitting unsupported context fields; the compact-origin
SessionStart then attaches the bounded context and checkpoint reminder. It refreshes Mnemo's
syntax-only source map at session start, after a checkpoint save, and before an unsaved changed
session stops. The attachment has a 1,750-token total budget; it does not read or store a raw
transcript or source body.

For later prompts, Mnemo treats the current conversation as the agent's short-term context. It
reduces long inputs to a transient 512-character head/tail view, applies fixed intent rules, then
uses a tiny embedded local classifier only when the request is ambiguous. Repeated words count once.
A clearly self-contained prompt receives no Mnemo attachment; continuation language can select
prior task memory; project-specific intent can select a bounded knowledge probe; source-relationship
intent can select the existing deterministic structural projection. Low confidence retains the
knowledge probe instead of suppressing memory. The classifier runs locally with no provider call or
model-token charge, stores no prompt or bounded view, never computes dbt lineage, and never decides
what short-term context becomes durable memory.

After a project first becomes dirty, the prompt hook emits one compact `MNEMO_DIRTY_V1` reminder.
It suppresses repeats during the same unsaved checkpoint cycle, while Stop and PreCompact retain
their handoff enforcement. Saving a verified new checkpoint revision resets the next cycle.

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
mnemo connect codex
```

The command runs immediately. For preview, explicit confirmation, status, or an MCP-only
connection:

```bash
mnemo connect codex --dry-run
mnemo connect codex --confirm
mnemo connect codex --check
mnemo connect codex --auto-memory-disable  # MCP only
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

Restart Codex after registration. In a coding session, it can then discover `save_checkpoint`,
`get_context`, and `explain_context`.

To remove only Mnemo’s owned registration:

```bash
mnemo disconnect codex
```

## Connect Claude Code

Prerequisite: the `claude` CLI is installed and on your `PATH`.

```bash
mnemo connect claude-code
```

The same controls are available:

```bash
mnemo connect claude-code --dry-run
mnemo connect claude-code --confirm
mnemo connect claude-code --check
mnemo connect claude-code --auto-memory-disable  # MCP only
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
mnemo disconnect claude-code
```

## Connect both clients

You can and often should connect both:

```bash
mnemo connect codex
mnemo connect claude-code
```

These are two independent client registrations pointing to the same local `mnemo-memory` command.
They share durable checkpoints only when they use the same `MNEMO_DATA_DIR` and the same explicit
scope. Disconnecting one client does not disconnect the other or delete saved Mnemo data.

## How an agent uses the memory

### Normal mode: automatic handoffs

After confirming the default connection, work normally. Mnemo’s lifecycle hook attaches the bounded
saved checkpoint, lessons, and approved facts at a new session, then prompts the agent to
`save_checkpoint` before it stops meaningful work. The user does not need to remember a separate
“save this” instruction. Codex asks you to review/trust local hooks once through `/hooks`; restart
either client after changing its hook configuration.

The automatic mode remembers task handoffs and creates a private static source-structure snapshot
for the enabled project: modules, imports, declarations, and explicit syntactic calls. The local
release supports Python, JavaScript/JSX, TypeScript/TSX, Go, Rust, C, C++, C#, Java, and PHP.
Mnemo does not claim an unproven runtime call graph. A short prompt that explicitly asks about
plural dbt models receives a bounded selector result from the registered authoritative manifest,
including snapshot provenance. The prompt remains transient, the total automatic prompt packet
keeps its existing 1,300-token ceiling, and the result may state that additional models were
omitted rather than enumerating an unbounded manifest.

At a fresh session, Mnemo also shows bounded added/removed/modified relative files, declarations,
and resolved relationships from the most recent proved structural transition. It is a cue to investigate a
recent code change, not a claim about why the change was made; the saved checkpoint supplies that
task reasoning and evidence.

If the saved checkpoint identifies supported relative files as relevant, Mnemo additionally
attaches very small cited static-impact candidates from up to two exact files in checkpoint order.
This lets the next Codex or Claude session see both the handoff’s reason for the work and the
syntax-proven code that may depend on it, without the agent having to remember a second
`get_context` request.

### Ask for a previous-session recap

From an enabled repository, `mnemo recap` prints the newest saved handoff. `mnemo recap --days 3`
and `mnemo recap --3days` select a bounded three-day window. You can ask the connected agent the
same thing in natural language, for example:

> Mnemo recap what I worked on for the past 3 days.

The prompt hook routes that literal request to checkpoint history without a source or knowledge
search. The MCP equivalent is `get_context` with `recap_days: 0` for the newest handoff or a value
from 1 through 90 for a day window. Results contain at most eight newest-per-checkpoint handoffs
selected from at most 50 same-task lifecycle events and must fit the packet's existing episodic and
total-token budgets. If the newest handoff is already the active checkpoint, it appears only once.
Each returned recap item cites the exact immutable checkpoint revision; unsaved conversation is not
invented.

For code orientation, the agent can include a `source_query` (a symbol or relative-path fragment)
in `get_context`. Mnemo returns matching structural facts plus declared module imports and explicit
syntactic calls, each tied to the exact local snapshot. If an import has exactly one saved target,
its returned relationship identifies that module too; duplicate candidates are deliberately left
unresolved. It does not return source text,
chat history, or guessed calls. Like a dbt snapshot, an active source snapshot is not silently
presented as proof that the working tree is current; its currentness is explicit.

For a broad repository/codebase architecture question, the existing natural-language `query`
routes to one compact saved-graph overview instead. It returns bounded component, file, module,
declaration, and relationship samples with exact snapshot counts and provenance. The client should
not request `source_overview` repeatedly or parse an overflow file; one response is the complete
bounded overview, and omitted counts disclose the rest of the graph.

### Manual fallback

Near the end of a task, ask the agent something concrete, for example:

> Save a Mnemo checkpoint: include the current implementation state, decisions, failed approach,
> tests run, evidence, and the exact next action.

The agent calls `save_checkpoint`. Mnemo stores a stable checkpoint identity plus an immutable
revision. It does not save the full transcript.

For a named source or dbt question in a new client session, ask:

> Retrieve Mnemo context for this task before continuing. Use the saved checkpoint and, if needed,
> the current dbt lineage facts.

The session-start hook already attached the bounded saved handoff. The agent uses `get_context`
when it needs additional named source or deterministic dbt upstream/downstream facts. Every result
cites the exact checkpoint revision; a completed or abandoned checkpoint is not chosen as active
work automatically.

For a directed dbt path, the agent uses the same `dbt_lineage` object with its normal `unique_id`
or exact `relative_path`, `direction`, and a `path_to_unique_id` destination. Mnemo returns one
bounded deterministic shortest path with typed manifest-edge evidence; it does not infer a route
from SQL or search another project.

For direct dbt test coverage, the agent uses `dbt_test_coverage` with one exact `unique_id` or
`relative_path`. Mnemo returns the bounded enabled manifest tests directly attached to that
resource and includes the latest saved run status when available. It never treats a missing test
result as success or infers transitive or column-level coverage.

For a manifest inventory, the agent can send `dbt_selector` with a resource type, package, tag, or
their intersection. A broad resource-type-only question returns one cited aggregate with the exact
enabled-node count and snapshot; it does not replay node records. Package/tag intersections retain
stable bounded node results, and `include_nodes: true` requests at most eight records as a sample.
Unknown fields such as `select`, `limit`, or `path` fail instead of silently producing the same
slice. Mnemo does not evaluate dbt selector strings or expand selected nodes through the graph.

For observed source freshness, the agent sends `dbt_freshness` with one exact source `unique_id`
or unambiguous manifest file. Mnemo returns only the persisted dbt-reported `sources.json` status,
observation time/age, thresholds, and evidence. It does not query the warehouse or infer a pass
from configuration or missing data.

## Scope, in practical terms

Mnemo uses stable owner, workspace, project, session, and task identities internally. They prevent
one project’s checkpoint from being exposed to another project. They are **not** derived from a
directory or dbt manifest.

For the normal personal workflow, you do not create or paste any UUIDs: run
`mnemo connect codex` or
`mnemo connect claude-code` from the repository once. Mnemo creates a private
machine-local project binding and reuses its stable scope on later sessions. `mnemo dbt
enable` does the same for a dbt repository and reuses the automatic-memory project scope when both
are enabled for that canonical directory.

Explicit scope IDs remain an advanced interface for controlled automation and manual MCP/dbt
ingestion. They are deliberately absent from normal onboarding; use the normal personal setup in
the [README](../README.md#install-and-connect-in-five-minutes) unless you are building such
automation.

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
| `mnemo` is not found | Install with `uv tool install mnemo-unified-context` and ensure uv’s tool directory is on `PATH`. The compatibility alias is `mnemo-memory`. |
| `MNEMO_CODEX_NOT_INSTALLED` or `MNEMO_CLAUDE_NOT_INSTALLED` | Install the relevant client CLI first; Mnemo never installs it for you. |
| A fresh client cannot find a checkpoint | Ensure both sessions use the same absolute `MNEMO_DATA_DIR` and the same scope; confirm the earlier agent actually saved a checkpoint. |
| The wrong project’s context is requested | Use the correct scope. Mnemo intentionally returns the same not-found result as for an unknown checkpoint. |
| The data directory is unavailable | Fix the explicit directory or remove the bad `MNEMO_DATA_DIR`; Mnemo will not silently switch to another database. |
| The installed launcher moved | Run the relevant `disconnect` command, then connect it again so the client stores the new absolute path. |

For a guided terminal explanation, run `mnemo agent` (or `mnemo guide`). It is safe
by default: it explains the chosen store and prints client commands; it does not initialize a store
unless you confirm or pass `--initialize`, and it never registers a client itself.
