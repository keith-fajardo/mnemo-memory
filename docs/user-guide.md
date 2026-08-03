# Mnemo Memory: a practical guide

Mnemo helps a fresh coding-agent session pick up work without replaying an entire earlier chat.
It is local-first: your saved handoffs, optional dbt lineage, and opted-in source-structure maps
stay in your own Mnemo data directory. Mnemo does not capture every conversation or make model
calls.

## The promise in everyday terms

At the end of a task, your connected agent saves a small handoff: what it was doing, what changed,
what decision was made, what failed, what was verified, and what to do next. In a new Claude Code
or Codex session, the agent asks Mnemo for that handoff before continuing.

That is the “long-term memory”: durable task handoffs—not an automatic recording of every terminal
command or chat message. You can opt into lifecycle hooks so the connected agent is reminded to
save the handoff automatically; you do not have to remember a separate request.

## Does Mnemo remember an entire codebase?

`mnemo-memory init` only creates your private local store; it does not guess which directory you
want remembered. From a repository you care about, the recommended one-time command is
`mnemo-memory connect codex --auto-memory` or
`mnemo-memory connect claude-code --auto-memory`. It creates a local project binding, records a
private static structure snapshot of supported source files, and installs the client reminders. It
does not store a copy of source text or send code anywhere.

Mnemo remembers both the **work on a codebase**—implementation state, decisions, failed attempts,
tests, evidence, and the next action—and the available static structure for an enabled repository.
Mnemo currently parses Python, JavaScript/JSX, TypeScript/TSX, Go, Rust, C, C++, C#, Java, and
PHP. It also records an internal file/module link when an explicit import resolves unambiguously
inside the saved snapshot. More adapters and safely resolvable call edges use the same graph
contract; no unproven runtime relationship is presented as fact.

When an agent needs orientation, it can request a symbol or relative-path match through Mnemo's
existing `get_context` tool. Mnemo returns matching modules/classes/functions, declared module
imports, and explicit syntactic calls as small, provenance-bearing structural facts. That lets the
agent start from a durable map instead of first reconstructing basic structure by scanning every
file. It is still deliberately not a source-code copy, a runtime trace, or a guessed call graph.
A saved snapshot is labeled **unknown currentness** unless a later source-state comparison can prove
it matches the files being worked on.

For a dbt repository, Mnemo can also remember verified upstream/downstream structure after you
enable dbt lineage. It still does not store raw SQL or a full source checkout.

For example, to use Mnemo while working on this Mnemo repository:

1. Install Mnemo and run `mnemo-memory agent` once to initialize and connect your client.
2. Ask your connected Claude Code or Codex agent to work on a concrete task in this repository.
3. Connect with automatic task memory once in this repository:
   `mnemo-memory connect codex --auto-memory` (or `claude-code`). Mnemo then prompts the agent to
   retrieve context at session start and save a compact handoff when work stops or compacts.
4. In a fresh client session, ask it to retrieve Mnemo context before continuing that task.

Mnemo stores the handoff in its local data directory, not inside the repository. Using the same
data directory and task scope lets the later session retrieve it. Starting a new repository does
not automatically expose a checkpoint from another project.

## Start with the guided setup

After installing, run:

```bash
mnemo-memory agent
```

The guide explains the steps in your terminal, can initialize your local store after confirmation,
and shows the exact connection command for Codex, Claude Code, or both. It does not call a model,
change a client registration without your confirmation, or inspect your source code.

The short manual equivalent is:

```bash
mnemo-memory init
mnemo-memory connect claude-code --auto-memory  # or: mnemo-memory connect codex --auto-memory
```

You can connect both clients. They share saved handoffs when they use the same Mnemo data directory.

## Use task memory while working

With automatic task memory enabled, you work normally. Mnemo's hook prompts the agent to save a
fresh handoff when needed. It still uses the typed `save_checkpoint` tool, so Mnemo does not silently
store a raw conversation. The manual fallback is:

> Save a Mnemo checkpoint with the progress, decisions, failed approach, tests run, evidence, and
> exact next action.

In the next fresh session, ask:

> Retrieve Mnemo context for this task before continuing.

The first request saves an immutable checkpoint revision. The second retrieves the latest relevant
revision with its evidence. If no one saved a checkpoint, Mnemo does not pretend that it remembers
the earlier chat.

## Optional dbt help: keep lineage current without changing your dbt workflow

This part is only for dbt projects. It gives a later agent verified answers to questions such as
“what is upstream of this model?” and “what breaks downstream?”

From a dbt repository, enable Mnemo once:

```bash
cd /path/to/dbt-project
mnemo-memory dbt enable
```

You do not need to provide owner, workspace, or project IDs. Mnemo creates private stable personal
identities and keeps the project binding locally. It never derives an identity from your path or
manifest, and it does not edit `dbt_project.yml`, `profiles.yml`, or credentials.

To use ordinary `dbt` commands with Mnemo’s local pre/post handling, choose one one-time shell
setup. For zsh, add this line to your own `~/.zshrc`:

```bash
eval "$(mnemo-memory dbt shell-hook zsh)"
```

Open a new terminal afterward. Then your everyday command remains unchanged:

```bash
dbt run --select orders+
```

The shell function runs the real dbt executable with the exact arguments, then Mnemo ingests a
changed valid `target/manifest.json` only after dbt succeeds. If the project is not enabled, dbt
still runs normally; Mnemo prints one reminder to run `mnemo-memory dbt enable` and does nothing
else. For CI or agent scripts, use the explicit equivalent:

```bash
mnemo-memory dbt exec -- run --select orders+
```

## What Mnemo does not do

- It does not automatically capture every conversation or terminal command.
- It does not execute dbt, SQL, Jinja, or warehouse operations on its own.
- It does not replace the `dbt` executable or edit your shell profile automatically.
- It does not upload your local database, manifest, credentials, or task handoffs.
- It does not change Claude, Codex, provider, authentication, or model settings.

## When something is not working

Run `mnemo-memory status` for the local store and `mnemo-memory dbt status` inside an enabled dbt
repository. For an explanation of the MCP connection, see the
[Codex and Claude Code guide](codex-claude-mcp-guide.md). For the full dbt wrapper behavior,
including strict mode and how to disable it, see the [dbt wrapper guide](dbt-command-wrapper.md).
