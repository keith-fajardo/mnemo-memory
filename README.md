# Mnemo Memory

## The problem

Coding agents start fresh sessions with no reliable knowledge of what happened before: what changed,
which decision won, which approach failed, which test passed, and what should happen next. Replaying
an entire transcript is expensive and noisy. Asking the agent to re-read a dbt project or a raw
manifest to understand impact is slow and can still surface stale or irrelevant information.

## The practical answer

Mnemo Memory is a local context system that lets a coding agent **save a compact task handoff now
and retrieve it in a later, fresh session**. When you opt a supported code project into automatic
memory, it also keeps a privacy-preserving static map of that project's modules, imports,
declarations, and syntactically explicit calls. It combines the handoff and requested structural
facts into one bounded context packet.

The intended workflow is simple:

```text
Work with an agent → Mnemo keeps a compact task handoff → end the session
                                            ↓
Start a fresh Codex/Claude session → retrieve Mnemo context → continue with the exact next action
```

The saved handoff contains the useful parts of prior work—not a replay of everything:

- objective, current progress, accepted decisions, and why they were made;
- failed approaches, blockers, tests/verification, evidence, and the next action; and
- optional dbt upstream/downstream impact facts from a manifest you choose to ingest.

![Diagram of Mnemo's explicit save, local store, and fresh-session retrieval flow](https://raw.githubusercontent.com/keith-fajardo/mnemo-memory/main/docs/assets/mnemo-memory-overview.svg)

## What the deterministic evidence shows

Mnemo includes model-free, reproducible synthetic benchmarks. They compare what is available to a
new session; they do **not** claim provider-billed savings or prove a model will produce a better
answer.

| Synthetic fixture comparison | Context tokens | Result |
| --- | ---: | --- |
| Full prior transcript | 2,948 | Baseline historical context. |
| Mnemo checkpoint packet | 499 | 83.1% fewer transcript-context tokens; all required handoff facts, a correction lesson, and provenance available. |
| Full dbt manifest | 2,600 | Baseline structural context. |
| Mnemo structural facts | 686 | 73.6% fewer structural-context tokens; expected lineage facts and provenance available. |
| Transcript + manifest | 5,548 | Combined baseline. |
| Unified Mnemo packet | 1,185 | 78.6% fewer combined-context tokens; checkpoint, correction lesson, lineage, currentness, and provenance gates pass. |

All numbers are deterministic estimates from Mnemo’s local estimator in cold fresh-session
fixtures—no model request, API key, provider cache, or output tokens are involved. The fixture
requires 100% required checkpoint facts, including the correction lesson, lineage precision/recall, and provenance coverage; it
rejects stale decisions presented as current. See the
[fresh-session benchmark](docs/fresh-session-resumption-benchmark.md) and
[unified-context benchmark](docs/unified-context-benchmark.md) for methodology and limits.

**Does “499 tokens” mean Mnemo uses 499 tokens all the time?** No. It is the estimated size of
one compact context packet in this synthetic fixture. Those tokens are added only when a client
actually asks Mnemo for that checkpoint context—for example at the beginning of a fresh task
session. Mnemo has no continuous token use while you work, and a different checkpoint or selected
structure map will have a different bounded size.

## What Mnemo remembers—and what it does not

It is not a general memory of everything you type. Mnemo does not capture chats, terminal output,
or source text, and it does not call a model. Opting a repository into automatic memory is an
explicit local action: Mnemo then reads supported-language syntax only to refresh a bounded
structure map. A client lifecycle hook prompts the agent to write a bounded handoff through
Mnemo's MCP tool.

- a **task checkpoint**: objective, progress, decisions, a failed approach, evidence, tests run,
  and the next action; and
- an optional **source-structure snapshot**: relative module paths, imports, declarations, and
  syntactically explicit calls for an enabled Python, JavaScript/JSX, TypeScript/TSX, Go, Rust, C,
  C++, C#, Java, or PHP project—plus bounded “what statically depends on this?” impact candidates,
  never a copy of source text; and
- optionally, a **dbt manifest snapshot**: verified upstream/downstream model relationships from
  a `manifest.json` Mnemo can ingest after you enable that dbt project once.

That distinction matters. A new agent session can retrieve a saved checkpoint and its current dbt
facts, but it cannot recover an earlier conversation that was never saved.

## What “long-term memory” means here

Imagine you ask an agent to change an `orders` model. Before stopping, the agent explicitly calls
Mnemo’s `save_checkpoint` tool with something like: “the staging model is done; the previous join
approach failed; tests passed; next update the downstream mart.” Mnemo stores that as an immutable
revision in its local SQLite database.

Later—after closing the terminal, restarting Codex or Claude Code, or starting a new MCP
process—the agent can call `get_context` against the **same Mnemo data directory and same scope**.
Mnemo returns that checkpoint, not a guessed summary or a full transcript. Later revisions retain
the same logical checkpoint identity and preserve earlier evidence.

When an agent corrects a reasoning mistake, it can save a compact **lesson** too: what triggered
the mistake, the mistaken assumption, the evidence-backed correction, and the prevention step. A
later session then receives the lesson—not merely a vague “test failed” note—so it can avoid the
same bad line of analysis. Mnemo does not claim to infer a model's private reasoning from an edit
or test output; the agent records a lesson explicitly with its checkpoint.

If the correction is discovered after the checkpoint was already saved, the agent can use
`save_checkpoint` with `operation: "record_lesson"`. That appends the single correction to the
current handoff without making it reconstruct the whole checkpoint; Mnemo keeps the original
progress and evidence, checks the current revision atomically, and enforces the same 600-token
checkpoint budget. See the [local MCP guide](docs/local-mcp.md#record-a-correction-without-resending-the-handoff).

If a later checkpoint revision concentrates on new progress and leaves out an older lesson,
Mnemo still brings that bounded lesson back as historical, evidence-cited context. It is presented
as historical task evidence—not as a claim about the current repository—so the agent can apply the
prevention step while checking current structure separately.

### Decisions and failed approaches that are worth keeping

A checkpoint is the main handoff. Sometimes there is also one small fact worth retaining without
rewriting the handoff: for example, “the Finance seed was stale,” “use business-date grain,” or
“the validation command passed.” The existing `save_checkpoint` MCP tool can record one of these
as an explicit `decision`, `failure`, or `tool_outcome`, with evidence and a stable source key.
Mnemo returns it only when `get_context` explicitly asks for approved episodic facts. When Codex
or Claude Code is connected with `--auto-memory`, Mnemo includes that option in the private
session-start context attachment. The agent therefore receives the compact handoff, prior lessons,
and these bounded verified facts before it claims to know why prior work happened. Mnemo does not
record your chat automatically; it only attaches facts that were explicitly saved with evidence.
The automatic attachment has a 1,200-token content budget and happens only at a fresh supported
client session, not continuously while you work.

This is intentionally **not automatic conversation capture**. Mnemo never turns a terminal log,
model reasoning trace, SQL query, environment, or source body into memory. The agent or user must
state the bounded fact and provide evidence, which means a later agent can see what is known and
where it came from without treating a private transcript as truth. The [local MCP guide](docs/local-mcp.md#record-an-explicit-decision-failure-or-tool-outcome)
has the exact request shape.

Running `mnemo-memory init` **does not** make the current directory memorable. It only creates or
opens the local Mnemo store. From the repository you want remembered, run one connection command
with `--auto-memory`; it creates a private project binding, records the first supported-language
structure snapshot, and installs lifecycle reminders for that client. The hook refreshes the structure map
at a later session start, when the agent saves its checkpoint, or at an unsaved task stop, then
prompts the agent to save a bounded checkpoint at a task stop or compaction. It never captures
transcripts or conversations. An optional dbt command wrapper can
refresh a configured project's manifest snapshot after a successful dbt command; it is described
in the [dbt command wrapper guide](docs/dbt-command-wrapper.md).

Mnemo stores no source-text copy and makes no network or model call. Its current local parsers cover
Python, JavaScript/JSX, TypeScript/TSX, Go, Rust, C, C++, C#, Java, and PHP. JavaScript and
TypeScript also recognize direct top-level literal CommonJS `require("./local")` bindings alongside
ES-module imports, while Python recognizes direct local `from .module import member` and parent-package
relative imports that remain inside the registered project. Rust recognizes an explicit
`use crate::path::member as local_name` spelling. They record explicit syntactic calls separately from imports and can follow only **proven** internal links to show a
bounded dependency/impact candidate. It does not guess runtime dispatch or claim a complete call
graph. That includes exact local C++ namespace calls, C# `using Namespace.Type` calls, and PHP
`use Namespace\\Type` static calls when one saved target matches; namespace-only imports, aliases,
computed/dynamic `require` calls, relative imports that escape the registered project, and duplicate
candidates stay unresolved. The storage and
context contracts are language-neutral, so more language adapters and semantic resolution can be
added without changing saved memory. The
[practical user guide](docs/user-guide.md) shows exactly how to use Mnemo on a repository such as
this one.

Mnemo also fingerprints common source files it does **not** parse yet—including dbt `.sql` models,
dbt `schema.yml`/`schema.yaml` files, dbt `.csv`/`.tsv` seed files, and Swift, Kotlin, Ruby, Scala,
Elixir, Lua, Dart, and similar source extensions. That means later
memory can truthfully say that `models/orders.sql` changed, with no SQL body stored or replayed. It
does **not** pretend to know SQL dependencies or calls from that file until a dedicated safe parser
or authoritative dbt manifest provides them.

For example, if Finance updates a dbt seed, Mnemo can record that `seeds/finance_orders.csv`
changed between two saved snapshots. It stores only the relative path and a SHA-256 fingerprint—not
the financial values—so an agent has a durable cue to investigate the current seed and its verified
dbt impact without treating the old file body as memory.

For an enabled dbt project, the authoritative manifest closes that gap: an agent can request
`dbt_lineage` with `relative_path: "models/marts/fct_orders.sql"`, and Mnemo resolves it only when
exactly one scoped manifest node owns that file. It then returns normal bounded upstream/downstream
facts with citations—without reading SQL or guessing dependencies.

From an enabled repository, you can also inspect a bounded static impact candidate directly:

```bash
mnemo-memory memory impact package.module_name
mnemo-memory memory impact --path src/billing/reconcile.py
mnemo-memory memory history
mnemo-memory memory changes
mnemo-memory memory changes --path models/orders.sql --history-limit 4
mnemo-memory memory refresh
```

`memory changes` uses the latest two recorded structural refreshes. The explicit
`--from SNAPSHOT_ID --to SNAPSHOT_ID` form remains available for an advanced historical audit.
`memory history` lists recent snapshots when you need to select an older pair. An agent can ask
`get_context` for a bounded `source_changes` history for one relative path such as
`models/orders.sql`; Mnemo returns only saved file/declaration/relationship identities for that
path, newest transition first, with snapshot citations. It never returns the SQL or source body.
You can inspect the same bounded history yourself with `memory changes --path ... --history-limit`.
Use `memory impact --path RELATIVE_PATH` when the question is “what proven static code depends on
this exact file?” It includes all saved declarations in that file and never substitutes a similarly
named path.

Mnemo is local-first. It does **not** proxy your provider, execute dbt, run SQL, render Jinja,
contact a warehouse, or send the local database anywhere.

> Status: `0.1.0a2` is an early alpha. It is useful for the documented local workflow, but the
> project intentionally does not yet provide automatic transcript capture, catalog/run-results ingestion,
> semantic cross-language call graphs, embeddings, a UI, or team workspaces.
>
> The multi-language source-structure work described below is currently in this development branch; it is
> not retroactively part of the immutable `0.1.0a2` PyPI release.

## Install

The PyPI distribution is `mnemo-unified-context`. The import package remains `mnemo_memory`, and
the command is deliberately `mnemo-memory`.

```bash
uv tool install mnemo-unified-context==0.1.0a2
mnemo-memory --help
```

`mnemo-memory` is a separate executable. It neither replaces nor shadows an existing `mnemo`
command on your machine.

For a local wheel, use `uv tool install /path/to/mnemo_unified_context-0.1.0a2-py3-none-any.whl`.
For development in this checkout:

```bash
uv sync --locked
uv run mnemo-memory --help
```

Upgrade or remove it with:

```bash
uv tool upgrade mnemo-unified-context
uv tool uninstall mnemo-unified-context
```

## Start here: let Mnemo explain the setup

If you do not want to learn configuration terms first, run:

```bash
mnemo-memory agent
```

It is a local, deterministic setup guide—not another AI agent. It explains what Mnemo remembers,
offers to initialize the local store, and shows how to connect Claude Code, Codex, or both. The
complete user walkthrough is [Mnemo Memory: a practical guide](docs/user-guide.md).

## A simple first use

### 1. Initialize one local Mnemo store

```bash
mnemo-memory init
mnemo-memory status
```

This creates a local database, normally **outside** your repository:

- macOS: `~/Library/Application Support/Mnemo`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/mnemo`
- Windows: `%LOCALAPPDATA%\Mnemo`

Use an absolute `--data-dir /absolute/path/to/mnemo-data` or `MNEMO_DATA_DIR` only when you want a
separate store, for example in a test or CI job. An explicit bad location fails safely; Mnemo does
not silently create a different empty database.

### 2. Make the MCP tools available to your client

```bash
mnemo-memory connect codex --auto-memory
mnemo-memory connect claude-code --auto-memory
```

You may run **both** commands. They are independent registrations:

| Command | What it connects | Can it coexist with the other? |
| --- | --- | --- |
| `mnemo-memory connect codex` | Codex’s MCP configuration | Yes. |
| `mnemo-memory connect claude-code` | Claude Code’s MCP configuration | Yes. |

`mnemo-memory connect` by itself does not connect anything; it shows the available client-specific
subcommands. `--auto-memory` is the recommended one-time project opt-in. After registering,
restart the relevant client. Each client sees exactly the same two Mnemo MCP tools:
`save_checkpoint` and `get_context`.

Both clients start the same installed `mnemo-memory` launcher and therefore share memory **when
they use the same Mnemo data directory and the same scope**. If you set different `MNEMO_DATA_DIR`
values, you have deliberately created isolated stores. Registration does not change model
selection, credentials, provider settings, or network permissions.

To inspect or remove one registration without affecting the other:

```bash
mnemo-memory connect codex --check
mnemo-memory disconnect codex
mnemo-memory disconnect claude-code
```

### 3. Use it deliberately during work

In a compatible client session, work normally. The opt-in lifecycle hook asks the agent to retrieve
context when a session starts and to save a handoff before it stops or compacts. The agent uses the
two MCP tools; there is no separate “remember this directory” command after the one-time
`--auto-memory` opt-in. For a local terminal walkthrough, run `mnemo-memory agent` (or its
`mnemo-memory guide` alias).

For the memory to be available later, the new session must use the same Mnemo data directory and
the same task/project scope. This is intentional: a checkpoint from one project is not disclosed
to another project merely because both run on the same machine.

### Questions people reasonably ask first

**Does `mnemo-memory init` remember my current repository?** No. It initializes one local Mnemo
database; it does not inspect or bind the current directory.

**What is remembered long term?** Explicit checkpoint revisions, and—after you opt a repository
into automatic memory—a static structure snapshot of its supported-language files. Mnemo currently
supports Python, JavaScript/JSX, TypeScript/TSX, Go, Rust, C, C++, C#, Java, and PHP. It stores
declarations plus explicit imports and calls, resolving a relationship only when exactly one saved
target matches. If two files or languages define the same candidate name, Mnemo leaves the link
unresolved rather than guessing. If you enable dbt, Mnemo also keeps its manifest projection. A
checkpoint is
a compact handoff record for one task—not a copy of your files, terminal history, or every chat
message.

**When does it get saved?** In automatic mode, Mnemo asks the connected agent to call
`save_checkpoint` at a stop or compaction boundary. You can still ask explicitly at any point.
The code structure refresh happens locally at session start, checkpoint save, and an unsaved stop;
task handoffs remain deliberately bounded rather than being raw transcript capture. Structural impact requests use the active
snapshot by default, or can name an immutable snapshot when an agent needs to reason about a
specific earlier state. A source map is called current only when its exact fresh source digest
matches; “active” alone is never treated as proof that files have not changed.

When an enabled project saves a checkpoint, Mnemo may also record the exact source snapshot it
observed immediately afterward. This is a useful, cited answer to “which saved structure was the
agent looking at when it handed work off?” It is **not** a claim that the snapshot caused the
change or explains the decision: the checkpoint’s explicit evidence and lessons remain the source
for *why* work was done.

When Mnemo observes a supported structural change between snapshots, it gives the connected agent
a short factual summary of added/removed/modified **relative files**, declarations, and proven
relationships in the automatically attached fresh-session context. A body-only edit is therefore
visible even when a file keeps the same functions—without requiring the agent to remember to make
a second query. Mnemo stores a SHA-256 fingerprint for that purpose—not source bodies—and still
does not guess **why** it changed: the agent records that decision, failed approach, and
verification in the checkpoint it saves.

After an agent changes a project file, Mnemo batches that work and refreshes at the **next user-turn
boundary**, not after every keystroke. The agent therefore receives the same small change and impact
cue while it is still working, before it needs to make another historical claim.

For a changed file in a supported parsed language, Mnemo also includes a very small list of
**static dependent candidates** from that exact file. The cue names the immutable source snapshot
that proves them, so the agent can request or compare that exact structural evidence. This saves the
agent from first asking “what could be affected?” after a fresh session. They are syntax-derived,
bounded, and cited—not proof of runtime behavior, and not a substitute for tests or current
verification.

For a changed dbt `.sql` model with an active manifest, the same cue includes bounded downstream
model identities from that manifest. Those are authoritative structural facts for that artifact,
but the cue treats their currentness as **unknown** until the agent supplies matching manifest or
source-state evidence. It names the exact immutable manifest snapshot, and Mnemo never treats an
older active manifest as proof that the project has not changed.

When an agent needs the durable version of that question in a later session, it can ask for
`source_changes` with `get_context`. Mnemo compares the two most recently recorded structural
snapshots in their actual activation order and returns a bounded list of added/removed/modified
relative-file, declaration, and relationship identities, with evidence for both snapshots. It never
guesses chronology from a snapshot UUID and never returns source text. The result is labeled
`current`, `stale`, or `unknown` only from an exact supplied source digest; “active” alone is not
called current.

For static code impact, an agent can name either a saved declaration or an **exact relative file
path**. A file-path request starts from declarations in that exact file only; Mnemo will not guess
from a similarly named file elsewhere. For dbt models, use the same `relative_path` concept under
`dbt_lineage`; Mnemo resolves the single manifest node and follows manifest-authoritative edges.

**Must I teach every agent to use Mnemo?** Not for the supported `--auto-memory` setup. Mnemo
injects a session-start instruction telling Codex or Claude Code to check context before claiming
knowledge of earlier work or impact, and how to request saved structure for a named symbol/file.
After the agent changes a project file, Mnemo also adds one short reminder before the next user
turn. It does not read the submitted prompt or a model's private reasoning, so this is transparent
timely guidance—not surveillance or a hidden automatic transcript recorder.

**How does a new agent get it?** The new agent connects to the same Mnemo store and calls
`get_context` for the same scope. It receives the latest active checkpoint and any requested dbt
facts, with provenance. If no checkpoint was saved, it receives no hidden history.

**Does changing directories create a different memory?** No. The data directory selects the
database and scope selects which project/task can be read. `--auto-memory` creates an explicit
machine-local binding for the repository you enabled; another directory stays unbound until you
choose to enable it. Mnemo never guesses that two filesystem paths are the same project.

## Optional: keep dbt lineage current automatically

This is optional. It adds verified dbt upstream/downstream facts to an agent’s context; it does
not change how task checkpoints work.

From the dbt repository, run this once:

```bash
cd /path/to/your-dbt-project
mnemo-memory dbt enable
```

Mnemo finds `dbt_project.yml`, initializes its personal profile when needed, creates private
stable identities, binds this one repository locally, and ingests `target/manifest.json` if a
valid one already exists. You do not need to generate, copy, or remember UUIDs. Mnemo does not
write `dbt_project.yml`, `profiles.yml`, warehouse credentials, or any shell profile.

If you have already enabled automatic task memory for this same repository, dbt enablement reuses
that project identity. A later `get_context` request can therefore put the task handoff (the
**why**) beside dbt lineage (the verified **what is affected**) without you having to pass IDs or
match scopes yourself.

To make normal `dbt` commands use the wrapper, opt your interactive shell in **once per machine**:

```bash
eval "$(mnemo-memory dbt shell-hook zsh)"
```

It defines a `dbt()` function only in your current shell. To make it persist, add that same line to
your own `~/.zshrc`; use the bash or fish equivalent for those shells. Mnemo never edits those
files for you. After the one-time shell setup and one-time project enablement, your everyday
command stays exactly the command you already know:

```bash
dbt run --select model_name
```

For CI, Codex, Claude Code, or a shell where you do not want the function, use the explicit
equivalent: `mnemo-memory dbt exec -- run --select model_name`. Remove the profile line or run
`mnemo-memory dbt disable` to stop wrapping a project; neither action deletes prior snapshots.

If you run dbt in a repository that has not been enabled, dbt still runs normally. Mnemo prints one
short reminder to run `mnemo-memory dbt enable` and skips ingestion.

`mnemo-memory dbt status` shows whether the enabled project has an active snapshot. Re-ingesting
the same manifest is safe and idempotent. A changed manifest becomes a new immutable snapshot and
atomically becomes active; older snapshots remain available for provenance. Mnemo stores a bounded
structural projection, not raw SQL, compiled SQL, macro bodies, credentials, or the full manifest.

## What the MCP tools do

Mnemo exposes exactly two local stdio MCP tools:

- `save_checkpoint` creates, revises, completes, or abandons an explicit task checkpoint.
- `get_context` returns a bounded packet for an explicit scope, optionally including a structured
  dbt upstream/downstream request.

A checkpoint has a stable logical ID and immutable revisions. Revision, completion, and
abandonment requests include the current revision ID, so two clients cannot silently overwrite one
another. Completed and abandoned checkpoints are excluded from automatic active selection.

Every saved revision must include evidence/provenance supplied by the caller. Mnemo does not
invent evidence, silently truncate stored checkpoint text, or replay a full transcript. The active
checkpoint section defaults to 600 estimated tokens; the entire context packet has a hard budget
with structured omissions when needed.

### Command map

| Command | Plain-language purpose |
| --- | --- |
| `mnemo-memory init` | Create/open the local Mnemo database. It does not inspect the current repository or save a checkpoint. |
| `mnemo-memory status` | Show whether that local store is initialized and usable. |
| `mnemo-memory connect codex` | Make Mnemo’s two MCP tools available to Codex. |
| `mnemo-memory connect claude-code` | Make the same two MCP tools available to Claude Code. |
| `mnemo-memory agent` | Run a deterministic interactive setup guide; it does not use a model or change a client registration. |
| `mnemo-memory dbt enable` | Enable Mnemo for the dbt project in this directory. It creates private local IDs for you. |
| `mnemo-memory dbt status` | Show whether this enabled dbt project has an active saved snapshot. |
| `mnemo-memory dbt disable` | Stop Mnemo wrapping this dbt project without deleting saved snapshots. |
| `mnemo-memory dbt exec -- …` | Run dbt explicitly with Mnemo’s optional pre/post manifest handling. |
| `mnemo-memory mcp serve --stdio` | Start the MCP server directly; client registration normally starts it for you. |

## Understanding dbt context and freshness

Mnemo supports dbt `manifest.json` schema v12. Dependencies come from the manifest’s declared
`depends_on.nodes` relationships; `parent_map` and `child_map` are consistency checks. It can
return direct or transitive upstream/downstream facts in a deterministic order, including the
artifact snapshot and evidence for every fact.

An **active** snapshot is the latest successfully ingested one. That alone does not prove it still
matches your files:

- **current** means supplied comparable digest/fingerprint evidence exactly matches;
- **stale** means comparable evidence differs;
- **unknown** means there is not enough comparable evidence.

Verified current manifest structure outranks an older checkpoint’s recollection of repository
structure. Mnemo preserves such a disagreement as evidence rather than asking a model to resolve
it.

## Reliability, privacy, and recovery

Checkpoint revisions and dbt snapshots are immutable and scoped. SQLite writes use transactions,
expected-revision/expected-active comparisons, foreign keys, and integrity checks. A new Mnemo
process using the same data directory can retrieve acknowledged writes after a restart.

Back up the data directory before upgrading: migrations are forward-only. A corrupt, unavailable,
or unsupported-newer database fails safely rather than switching to an empty store. Tool errors are
sanitized and do not include SQL, stack traces, unrelated scope data, or private paths.

## More detail

- [Local MCP guide](docs/local-mcp.md) — tool lifecycle, revision conflicts, and recovery.
- [Codex and Claude Code MCP guide](docs/codex-claude-mcp-guide.md) — complete client setup,
  cross-client sharing, prompts to use during work, and troubleshooting.
- [dbt manifest guide](docs/dbt-manifest-intelligence.md) — schema support, deterministic lineage,
  evidence, and parser safety.
- [dbt command wrapper guide](docs/dbt-command-wrapper.md) — one-time binding, automatic
  post-run manifest activation, shell setup, and failure behavior.
- [Command wrapper hooks](docs/command-wrapper.md) — the generic safe wrapper kernel, trust
  boundary, and failure semantics behind the optional dbt integration.
- [Implementation status](docs/implementation-status.md) — completed vertical slice and deferred
  milestones.

Advanced/manual dbt ingestion and explicit scope overrides remain available for controlled
automation; they are intentionally not part of the personal setup path.

## Development verification

```bash
npm ci
npm run check
uv build --no-sources
```

The verified package target is Python 3.12 on macOS and Linux. Built-artifact verification runs
outside the source checkout so migrations and schemas are proven to load from the installed
`mnemo_memory` package.
