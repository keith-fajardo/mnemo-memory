# Mnemo Memory: a practical guide

Mnemo helps a fresh coding-agent session pick up work without replaying an entire earlier chat.
It is local-first: your saved handoffs, optional dbt lineage, and opted-in source-structure maps
stay in your own Mnemo data directory. Mnemo does not capture every conversation or make model
calls.

## The promise in everyday terms

At the end of a task, your connected agent saves a small handoff: what it was doing, what changed,
what decision was made, what failed, what was verified, and what to do next. In a new enabled
Claude Code or Codex session, Mnemo attaches that bounded handoff before the agent continues.

That is the “long-term memory”: durable task handoffs—not an automatic recording of every terminal
command or chat message. You can opt into lifecycle hooks so the connected agent receives the saved
handoff at a new session and is reminded to save the next one; you do not have to remember a
separate request.

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
inside the saved snapshot, and follows those proven links to show a bounded list of code that
statically depends on a selected file or symbol. It can also resolve a small safe subset of direct
calls (same-module, fully-qualified, or unambiguous imported members). More adapters and safely
resolvable call edges use the same graph contract; no unproven runtime relationship is presented
as fact.

When an agent needs orientation, it can request a symbol or relative-path match through Mnemo's
existing `get_context` tool. Mnemo returns matching modules/classes/functions, declared module
imports, and explicit syntactic calls as small, provenance-bearing structural facts. That lets the
agent start from a durable map instead of first reconstructing basic structure by scanning every
file. It is still deliberately not a source-code copy, a runtime trace, or a guessed call graph.
A saved snapshot is labeled **unknown currentness** unless a later source-state comparison can prove
it matches the files being worked on.

You can inspect the same proven static impact map yourself from an enabled repository:

```bash
mnemo-memory memory impact package.module_name
mnemo-memory memory impact package.module_name --direction dependencies
mnemo-memory memory impact package.module_name --direct
mnemo-memory memory changes --from SNAPSHOT_ID --to SNAPSHOT_ID
mnemo-memory memory refresh
```

The result lists only saved internal relationships that Mnemo can prove from syntax, along with
depth, snapshot identity, and a clear `unknown` currentness label when no fresh source-state proof
was supplied. It is a useful change-planning aid, not a promise that every runtime effect was found.
An agent can also request an exact immutable snapshot ID through `get_context` when it needs to
compare a past structural state rather than the active one; otherwise Mnemo uses the active snapshot.
When automatic memory refreshes a repository at session start, its private agent instruction carries
the exact source digest for that refresh. An impact request that supplies that same digest is labeled
`current`; a different digest is labeled `stale`; without comparable evidence it remains `unknown`.
`require_current` omits a stale or unproven source map rather than presenting it as current.
`memory changes` compares saved structural identities only: it never stores or prints source text.
Run `memory refresh` after edits when you are not using an automatic client lifecycle hook. With
automatic memory enabled, Mnemo refreshes at session start, after a checkpoint save, and before an
unsaved changed session is stopped. It rebuilds the bounded structural snapshot from current local
syntax and preserves the previous snapshot for comparison.

With automatic task memory, Mnemo also keeps the most recent proved structural transition. At a
fresh session, the connected agent receives a short list of added/removed/modified **relative
files**, declarations, and resolved relationships, tied to the source snapshot Mnemo just
refreshed. That includes a body-only edit even when a file kept the same functions. Mnemo stores a
SHA-256 fingerprint for this purpose—not source bodies—and does not pretend it can infer the
reason from a diff. The checkpoint is where the agent records why the change was made, what failed,
and what was verified. When it corrects a reasoning mistake, it should also save a compact
**lesson**: the trigger, the assumption that was wrong, the evidence-backed correction, and how to
avoid it next time. A later agent receives that lesson with the task handoff. Mnemo does not guess
private reasoning from a diff or a failed test; it preserves a correction only when the agent
records it explicitly. If a later revision focuses on fresh progress and omits an older lesson,
Mnemo still returns the bounded lesson as historical episodic evidence, with the exact original
revision and evidence references.

For a dbt repository, Mnemo can also remember verified upstream/downstream structure after you
enable dbt lineage. It still does not store raw SQL or a full source checkout.

For example, to use Mnemo while working on this Mnemo repository:

1. Install Mnemo and run `mnemo-memory agent` once to initialize and connect your client.
2. Ask your connected Claude Code or Codex agent to work on a concrete task in this repository.
3. Connect with automatic task memory once in this repository:
   `mnemo-memory connect codex --auto-memory` (or `claude-code`). Mnemo then attaches the compact
   saved handoff at session start and prompts the agent to save one when work stops or compacts.
4. In a fresh client session, continue normally. Ask for `get_context` only when the task needs
   additional named source or dbt facts beyond the attached handoff.

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

After you connect a supported client with `--auto-memory`, you do **not** need to repeat a custom
memory rule in every `CLAUDE.md` or `AGENTS.md`. Mnemo provides a private session-start context:
the bounded saved checkpoint, lessons, and approved decision/failure/tool-outcome facts. It also
tells the agent to treat that material as evidence rather than instructions, and how to ask for
relevant saved structure when a task names a symbol or file. This is a
reliable reminder at a fresh-session boundary, not hidden transcript monitoring or a promise that
Mnemo can read a model's private reasoning.

There is one additional timely cue: when the agent has edited a project file, Mnemo adds a short
memory reminder before the next user turn. It does **not** inspect that user prompt. The reminder
only says that project work changed and that history/impact claims should use Mnemo evidence; it
stops once the agent saves its checkpoint. This makes memory use a normal part of supported
Codex/Claude Code work without making you maintain a parallel instruction file.

The short manual equivalent is:

```bash
mnemo-memory init
mnemo-memory connect claude-code --auto-memory  # or: mnemo-memory connect codex --auto-memory
```

You can connect both clients. They share saved handoffs when they use the same Mnemo data directory.

## Use task memory while working

With automatic task memory enabled, you work normally. At a fresh session Mnemo attaches the
bounded saved handoff, lessons, and approved facts automatically. This automatic attachment has a
1,200-token content budget and happens only when a supported client starts a new session—not
continuously while you work. Mnemo's hook still prompts the agent to save a fresh handoff when
needed. It uses the typed `save_checkpoint` tool, so Mnemo does not silently store a raw
conversation. The manual fallback is:

> Save a Mnemo checkpoint with the progress, decisions, failed approach, tests run, evidence, and
> exact next action. If you corrected an analysis mistake, also save its trigger, mistaken
> assumption, correction, prevention, and evidence IDs as a lesson. If one verified decision,
> failure, or tool result matters on its own, record it with `record_event` and evidence—but still
> save a complete checkpoint before stopping. If the handoff is already saved, use the existing
> `save_checkpoint` operation `record_lesson` to append just that one correction rather than
> rewriting the whole handoff.

In the next enabled fresh session, the hook attaches the latest relevant revision with its evidence.
If no one saved a checkpoint, Mnemo does not pretend that it remembers the earlier chat. The agent
can still call `get_context` for an explicit historical checkpoint, a named source, or dbt lineage.

### Example: a reconciliation investigation

Suppose an agent is investigating why a dbt reconciliation model does not match Finance seed
values. Its checkpoint records the important *reasoning*, not just a filename: the comparison key
chosen, why it was chosen, a failed timestamp-based join, the validation already run, the models or
seeds involved, and the precise next check. In a later fresh session, the same connected agent gets
that handoff first. If dbt lineage is enabled, it can then request verified upstream Finance inputs
and downstream impact for the named dbt model. Mnemo therefore carries forward both **why the work
changed** and **which saved structural facts the agent should trust**. It never invents either from
an old transcript.

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

If automatic task memory is already enabled for this repository, Mnemo reuses its private project
identity. That is what lets one later `get_context` request safely combine the checkpoint's
explanation of **why** a reconciliation changed with dbt's verified evidence of **what** is
upstream or downstream—without asking you to manage scopes or UUIDs.

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
