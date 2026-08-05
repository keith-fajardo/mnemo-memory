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
PHP. It also records an internal file/module link when an explicit import resolves to exactly one
saved target, and follows those proven links to show a bounded list of code that
statically depends on a selected file or symbol. It can also resolve a small safe subset of direct
calls (same-module, fully-qualified, or unique imported members). Duplicate candidates across
files or languages remain unresolved rather than being guessed. A JavaScript/TypeScript local
top-level `const name = () => …` or `const name = function …` is indexed as a function, which covers
the common `export const handler = …` style. Mutable, conditional, and nested bindings stay absent.
A JavaScript/TypeScript local
default import is resolved only for one named `export default function` or `export default class`;
an exact local named barrel export such as `export { validate as check } from "./helpers"` is also
resolved. A local wildcard barrel can resolve one non-default member only when it has exactly one
proven local declaration. Anonymous, ambiguous, and value-flow defaults remain deliberately unresolved. For a default
class, `Class.method()` is linked only when `method` is explicitly static; Mnemo does not turn an
instance method into a guessed class call. Python package-initializer exports
(`from .core import member as public_name`) follow the same rule: only one exact saved local
declaration is followed; wildcard imports stay unresolved. More adapters and safely resolvable
call edges use the same graph contract. That subset now includes exact local C++
namespace calls, C# `using Namespace.Type` and `using static Namespace.Type` calls, PHP `use Namespace\\Type`,
explicit `use Namespace\\Type as Alias`, explicit `use function Namespace\\member`, and flat grouped
imports such as `use Namespace\\{Type as Alias}` calls, and
direct top-level literal CommonJS `require("./local")` bindings in JavaScript and TypeScript.
Python also resolves direct local `from .module import member` and parent-package relative imports
that remain inside the registered project. Rust resolves an explicit
`use crate::path::member as local_name` spelling and flat `use crate::path::{member as local_name, member}` lists when each saved target is unique. Namespace-only
imports, computed/dynamic `require` calls, relative imports that escape the registered project,
and aliases that cannot be matched uniquely remain unresolved. C# also resolves an explicit
`using Local = Namespace.Type` spelling when the target is unique. No unproven runtime relationship is
presented as fact. Java resolves direct `import static package.Type.member` only when its local
class and method target are unique.

Common unparsed project files—such as JSON/XML/INI configuration, dependency lockfiles,
Dockerfile/Makefile-style build files, CSS, HTML, GraphQL, TOML, dbt SQL/YAML, and several other
language extensions—still participate as file-only fingerprints. Mnemo can tell that a safe
relative path changed, but does not store the file body or claim its dependencies, symbols, or
runtime behavior.

For common source extensions Mnemo does not parse yet—such as dbt `.sql` models, dbt
`schema.yml`/`schema.yaml` files, dbt `.csv`/`.tsv` seed files, Swift, Kotlin, Ruby, Scala, Elixir,
Lua, and Dart—it still saves an immutable relative-path and byte fingerprint.
So a later source-change summary can honestly say that `models/orders.sql` changed without keeping
or sending the SQL body. These files do not receive invented declarations, calls, or dependencies:
for dbt structure, use the authoritative manifest; for another language, wait for a safe parser.

This also covers the common reconciliation question, “did the Finance seed change?” Mnemo can say
that an exact seed path changed between saved snapshots, but it does not retain the seed rows or
claim why the numbers changed. Use the current source and dbt manifest for those facts, and keep
the investigation’s decision and verification in the task checkpoint.

When an agent needs orientation, it can request a symbol or relative-path match through Mnemo's
existing `get_context` tool. Mnemo returns matching modules/classes/functions, declared module
imports, and explicit syntactic calls as small, provenance-bearing structural facts. That lets the
agent start from a durable map instead of first reconstructing basic structure by scanning every
file. It is still deliberately not a source-code copy, a runtime trace, or a guessed call graph.
A saved snapshot is labeled **unknown currentness** unless a later source-state comparison can prove
it matches the files being worked on.

An exact safe file name such as `package.json`, `uv.lock`, or `Dockerfile` is useful too. Mnemo
returns its cited relative-file identity even when it does not parse that file's language. It never
returns the file contents or turns a configuration file into guessed symbols or dependencies.

You can inspect the same proven static impact map yourself from an enabled repository:

```bash
mnemo-memory memory impact package.module_name
mnemo-memory memory impact package.module_name --direction dependencies
mnemo-memory memory impact package.module_name --direct
mnemo-memory memory impact --path src/billing/reconcile.py
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
`memory impact --path` is the safer form when you know the changed file: Mnemo starts from every
saved declaration in that exact relative path and never falls back to a same-named file elsewhere.
`memory changes` compares saved structural identities only: it never stores or prints source text.
Run `memory refresh` after edits when you are not using an automatic client lifecycle hook. With
automatic memory enabled, Mnemo refreshes at session start, after a checkpoint save, and before an
unsaved changed session is stopped. It rebuilds the bounded structural snapshot from current local
syntax and preserves the previous snapshot for comparison.

When the question is specifically “what changed in this model or file?”, an agent can request a
small `source_changes` history for a path such as `models/orders.sql`. Mnemo returns the newest
matching saved transitions first, with snapshot citations. It shows only file/declaration/
relationship identities and fingerprints—never the SQL or source body—and the checkpoint or a
recorded lesson remains the evidence for **why** a change was made.

You can inspect that same safe history yourself:

```bash
mnemo-memory memory changes --path models/orders.sql --history-limit 4
```

The path is relative to the enabled repository. Mnemo rejects absolute paths and parent traversal,
and it returns no other project’s history.

With automatic task memory, Mnemo also keeps the most recent proved structural transition. At a
fresh session, the connected agent receives a short list of added/removed/renamed/modified **relative
files**, declarations, and resolved relationships, tied to the source snapshot Mnemo just
refreshed. That includes a body-only edit even when a file kept the same functions. Mnemo stores a
SHA-256 fingerprint for this purpose—not source bodies. When exactly one removed and one added path
share that fingerprint, it reports a rename; copied or ambiguous content remains add/remove rather
than a guessed move. For an enabled dbt project, Mnemo can use the new path of a proven renamed
`.sql` file for its normal exact manifest lookup; it never infers lineage from the SQL file itself.
Mnemo does not pretend it can infer the
reason from a diff. The checkpoint is where the agent records why the change was made, what failed,
and what was verified. When it corrects a reasoning mistake, it should also save a compact
**lesson**: the trigger, the assumption that was wrong, the evidence-backed correction, and how to
avoid it next time. A later agent receives that lesson with the task handoff. Mnemo does not guess
private reasoning from a diff or a failed test; it preserves a correction only when the agent
records it explicitly. If a later revision focuses on fresh progress and omits an older lesson,
Mnemo still returns the bounded lesson as historical episodic evidence, with the exact original
revision and evidence references.

When there is no recent transition, Mnemo still gives the fresh session a small **source overview**:
the cited immutable snapshot ID, counts of indexed files/symbols/relationships, and a deterministic
sample of saved relative files, modules, and declarations. It is a compact map of the registered repository, not a
copy of its source code. The packet never includes source bodies, chat prompts, terminal output, or
absolute local paths. A budget-constrained packet says exactly what it omitted rather than silently
altering structural identities. The overview also reports how many file/module/declaration
identities remain outside its bounded sample.

At session start, Mnemo has just refreshed the snapshot it attaches, so that attached source map
is explicitly labeled **current** for the captured digest. Later manual requests still require
comparable source-state evidence before Mnemo calls a saved snapshot current; active alone is not
enough.

During an active session, Mnemo does not scan after every edit. It marks project work as changed,
then refreshes once at the next user-turn boundary and attaches the same small file/impact cue.
That gives the agent relevant structural facts while it is analyzing the next request without
capturing the user’s prompt or source body.

For an exact changed file in a supported parsed language, that fresh-session cue also includes a
small list of proven **static dependent candidates** and the immutable source snapshot that proves
them. This is a practical “what might be affected?” starting point, not a runtime promise: the
agent should still inspect the cited structure and run the appropriate verification.

For a JavaScript or TypeScript monorepo with a strict JSON root `package.json` and local
`workspaces`, Mnemo also recognizes a narrow package-level relationship: a local package's runtime
`dependencies` entry with a `workspace:` specifier can point to another proven local workspace
package. This lets the same impact cue say that a saved local package depends on another saved
local package. Mnemo does not run npm, pnpm, or Yarn; inspect a lockfile; or guess package
resolution. External packages and ordinary version ranges, plus development, peer, and optional
dependencies, remain outside this evidence boundary.

### Finding a structural starting point

You or a connected agent do not need to remember an exact source path before asking Mnemo about an
unfamiliar repository. Use `get_context` with a short literal `source_query`, for example
`reconcile orders`. Mnemo searches the **current scoped structural snapshot** and ranks an exact
saved symbol or path first, then prefixes, then identities containing every supplied word. The
result remains a bounded cited map of module/class/function/package identities; it never searches
or returns source bodies, comments, chat history, embeddings, or another project. From that result,
use the normal source-impact request to inspect only relationships Mnemo can prove.

Rust projects have the same narrow package-level support for an explicit local Cargo runtime
dependency: `[dependencies] name = { path = "..." }`. Mnemo records it only when the normalized
local path and unrenamed package name both match another parsed local library crate. It does not
run Cargo, inspect `Cargo.lock`, or claim version, feature, build, development, optional, renamed,
or workspace-inherited dependency behavior.

The same help is available when there was no new edit yet: if the current saved checkpoint lists
supported relative files under `relevant_files`, Mnemo uses up to two matching files in checkpoint
order as small static-impact starting points in the next automatic handoff. For example, a
reconciliation handoff can name `models/reconcile.py` and `models/ledger.py`; the next agent
receives only the bounded saved dependents Mnemo can prove from syntax. The checkpoint records
*why* the files matter; the cited source snapshot records *which structural relationships* are
known. Mnemo never treats the checkpoint’s file list as proof of a dependency.

If that changed file is a dbt `.sql` model and Mnemo has an active manifest for the same project,
the cue instead also includes bounded downstream dbt model identities from the manifest. That is
the authoritative source for dbt structure; it still does not expose SQL or pretend that an active
manifest is automatically current. The cue includes the immutable snapshot ID so an agent can
request or compare that exact historical structure.

After a checkpoint is saved for an enabled project, Mnemo may add one small citation saying which
immutable source snapshot it observed immediately afterward. Think of it as a timestamped bookmark
between the handoff and the saved source map. It helps a later agent compare the handoff with the
same structural view, but it never claims that a file diff explains the author’s decision. The
checkpoint’s own evidence and recorded lessons remain the truthful place to answer *why*.

For a dbt repository, Mnemo can also remember verified upstream/downstream structure after you
enable dbt lineage. It still does not store raw SQL or a full source checkout.

If you are looking at a changed dbt model file, you do not need to know dbt’s internal unique ID.
Ask Mnemo for lineage using its exact relative path, such as `models/marts/fct_orders.sql`. Mnemo
matches it only when one active manifest node owns that path, then uses the manifest’s verified
dependencies to show impact. It never guesses lineage from the SQL text; ambiguous files are
reported instead of being silently assigned to a model.

To ask how one dbt resource reaches another, add `path_to_unique_id` to that same structured
`dbt_lineage` request. Mnemo returns one deterministic shortest directed path, including the typed
edge supporting each step. The path stays in the selected immutable snapshot and existing token,
node, edge, and depth limits; no path produces a bounded omission instead of a broad graph replay.

To inspect direct dbt test coverage, request `dbt_test_coverage` with one exact `unique_id` or
`relative_path`. Mnemo returns only enabled manifest test nodes that directly depend on that
resource, plus their latest persisted `run_results.json` status when one is available. A missing
run result stays unobserved rather than being treated as a pass, and no attached tests produces a
bounded omission rather than inferred coverage.

For a small manifest inventory, request `dbt_selector` with one or more exact
`resource_type`, `package_name`, or `tag` filters. Mnemo intersects the supplied fields, returns
enabled nodes in stable unique-ID order, and caps the result before packet rendering. This is a
structured Mnemo query, not dbt selector-string syntax, so it never executes selector expressions.

To review a dbt transition, request `dbt_changes`. With no snapshot IDs it compares the latest two
explicit manifest activations; advanced callers may provide both `before_snapshot_id` and
`after_snapshot_id`. Mnemo reports bounded added, modified, and removed resources and bounded
downstream nodes that may need refresh. Those candidates come only from authoritative manifest
edges. `require_current: true` omits the result unless the after snapshot matches the current
bounded repository observation.

For a small view of the current starting file, add `include_code_excerpt: true` to an exact
`dbt_lineage` request. `excerpt_start_line` defaults to 1 and `excerpt_maximum_lines` defaults to 20
and cannot exceed 40. Mnemo reads only the resolved node's registered-project `.sql`, `.yml`, or
`.yaml` file, returns the selected lines with digest/line evidence, and treats them as untrusted
current-file content. The excerpt does not establish dependencies or make a stale manifest current.
Unsafe paths, secrets, unsupported or oversized files, and insufficient budget produce an
omission while the lineage result remains usable.

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

To inspect the same bounded active handoff yourself without starting an MCP client, run this from
the explicitly enabled repository:

```bash
mnemo-memory memory inspect
```

The command prints the canonical context packet, including the exact immutable checkpoint revision
and evidence provenance. It returns `active_task_checkpoint: null` when no active handoff exists and
fails closed outside an enabled project. Inspection is read-only: it does not refresh source or
notes, call a model, or broaden retrieval to another project.

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

### Review and govern approved facts

An agent may explicitly save one evidence-backed decision, failure, or bounded tool outcome in
addition to the full checkpoint. You can review those facts from the enabled repository without an
MCP client:

```bash
mnemo-memory memory events
mnemo-memory memory event inspect EVENT_ID
```

If a fact is wrong, append a same-kind immutable replacement while preserving the correction link:

```bash
mnemo-memory memory event correct EVENT_ID \
  --summary "Corrected factual summary" \
  --reason "Why the retained fact was wrong" \
  --yes
```

If the fact should no longer be retained, retract it:

```bash
mnemo-memory memory event retract EVENT_ID \
  --reason "Why this fact is being withdrawn" \
  --yes
```

Correction and retraction are scoped to the enabled project and require confirmation unless `--yes`
is supplied. Context returns only active facts. Retraction removes the original summary, source key,
and evidence links while retaining a bounded tombstone and the evidence for the retraction itself.
It does not delete checkpoints, notes, exports, or future backups.

The loopback dashboard's **Export JSON** button downloads every approved fact and tombstone in the
current enabled task scope. The canonical file contains the exact internal scope, immutable event
and governance records, complete evidence references, current pin state, a UTC export timestamp,
and a SHA-256 content digest. Export requires an explicit same-origin action, uses a fixed filename
that does not reveal the repository, and creates no additional server-side copy. Keep the downloaded
file private: later correction, retraction, or Mnemo uninstall cannot recall a copy you control.

You also do not supply or guess owner/workspace/project/session/task UUIDs. When the local MCP
server starts inside an enabled repository, it resolves that repository's saved internal scope.
Calling `get_context` with no scope fields is therefore valid there; supplying only part of a scope
is rejected rather than mixed with another project's identity.

There is one additional timely cue: when the agent has edited a project file, Mnemo adds a short
memory reminder before the next user turn. With your explicit `--auto-memory` consent, Mnemo may
use at most 512 characters of that prompt transiently to select already-saved, same-project memory.
It never writes the prompt to its database, hook state, or logs. The reminder stops only after
Mnemo verifies that the scoped checkpoint revision actually changed—not merely because a tool name
was observed. This makes memory use a normal part of supported Codex/Claude Code work without
making you maintain a parallel instruction file.

The short manual equivalent is:

```bash
mnemo-memory init
mnemo-memory connect claude-code --auto-memory  # or: mnemo-memory connect codex --auto-memory
```

You can connect both clients. They share saved handoffs when they use the same Mnemo data directory.

### Your repository notes are refreshed safely too

The same explicit `--auto-memory` opt-in also keeps bounded Markdown notes inside that enabled
repository current. This is useful for a repository's `README.md`, `docs/`, architecture notes, or
decision records: a later agent can ask Mnemo for a relevant note section instead of rereading the
whole documentation tree. Mnemo refreshes only at a session/work boundary, not on every keystroke.

This does not mean Mnemo treats a note as a command. A returned note section is labelled untrusted,
cited to its relative path, heading, digest, and immutable revision, and is placed within the
knowledge budget. Mnemo scans no other folder, follows no symlink or Markdown link, and rejects
clear credential-like values before a batch can be stored. It does not promise to detect every
possible secret, so keep secrets out of project documentation as usual.

Mnemo searches current scoped notes through a local rebuildable SQLite full-text index. It does not
send your notes to a hosted model or silently include every note in an agent request. At a prompt
boundary it builds a deterministic packet capped at 1,300 estimated tokens from the active
checkpoint and relevant saved notes; unrelated and cross-project notes stay out. Deleted note
bodies and old note revisions are removed from the index; every selected section still cites its
exact document revision.

### Optional local semantic note search

Literal search is the default. If you want a note about “invoice reconciliation” to be findable
when you ask about “billing variance,” install the optional local runtime and build one index for
the project you already enabled:

```bash
uv tool install "mnemo-unified-context[semantic]"
mnemo-memory memory semantic index
mnemo-memory memory semantic search "billing variance"
```

This is an explicit personal-machine choice. The first index can download public embedding-model
weights; the note text and later query text are processed only by the local runtime. Mnemo stores
a vector attached to the note's current immutable revision, not another copy of the note. It does
not build the index automatically or change the authority of returned notes: they remain bounded,
cited, untrusted evidence. Once you explicitly build the index, automatic-memory prompt retrieval
may use it locally and falls back to literal search if the optional runtime is unavailable. An MCP
client can also request it explicitly with `semantic_knowledge_query`.

### Correcting or flagging a note disagreement

Edit a note when its guidance changes. Mnemo creates a new immutable revision and searches only
the new current revision; it does not silently present the old wording as current knowledge.

If two current notes genuinely disagree, do not expect Mnemo to guess which prose is correct. Put
one explicit, relative-path declaration in the note that raises the disagreement:

```markdown
---
mnemo_conflicts_with: docs/legacy-reconciliation.md
---
```

When that note is retrieved, Mnemo includes both cited current revisions and marks the pair as an
**unresolved conflict**. Both remain untrusted user-authored evidence; verified dbt and source
facts still take priority for current repository structure. A missing, cross-project, or unsafe
path is ignored rather than disclosing another project or guessing a conflict.

When equally matching repository and optional-vault notes are returned, repository Markdown appears
first as a predictable tie-breaker. Both are still separate, untrusted, cited evidence—not facts
that can silently override current dbt or source-structure evidence.

At the next supported client session start, Mnemo tells the agent only that scoped project knowledge
is available and attaches only bounded selected context, never every note. At later prompt
boundaries, the explicit automatic-memory opt-in permits transient local selection from the bounded
prompt; prompt text is not persisted. This makes the capability useful without making you maintain
an additional agent instruction file.

If the saved task checkpoint already lists a relevant file such as `models/reconciliation.sql`, the
fresh-session packet automatically uses its file stem (`reconciliation`) to select a small current
same-project note section. This is a bounded 250-token convenience, not a full notes replay. The
returned section still has exact revision provenance and remains untrusted. For a new topic with no
saved relevant file, transient automatic retrieval or an explicit `knowledge_query` selects a
bounded same-project result.

### Reusable project playbooks

For a repeatable project workflow, keep the instructions in ordinary checked-in Markdown and mark
them explicitly. This is useful for a reconciliation analyst, an incident triage checklist, or a
safe release process:

```markdown
---
mnemo_kind: procedure
mnemo_tags: reconciliation, dbt
mnemo_mandatory: true
---

# Reconciliation workflow

Confirm the cited input grain, inspect the current manifest impact, and record the verified result.
```

An agent requests a known playbook through `get_context` with
`"procedure_tags":["reconciliation"]`. Mnemo returns only a matching **current immutable
revision** in the packet's procedures section. It includes exact revision and digest provenance,
and it observes the existing 1,200-token procedures budget and overall packet budget.

This is intentionally explicit. Mnemo does not guess a tag from your prompt, load every Markdown
file, execute the playbook, or treat it as a higher authority than system/user instructions,
scope policy, or verified current structural evidence. `mnemo_mandatory: true` prioritizes that
checked-in project rule among matching project procedures; it is not an authorization mechanism.

#### Have Mnemo attach the right playbook at session start

For normal Codex and Claude Code use, you should not need to remember a tag in every conversation.
After you have enabled automatic memory for this project, add one checked-in profile file:

```markdown
---
mnemo_kind: agent_profile
mnemo_client: any
mnemo_procedure_tags: reconciliation, dbt
---

# Default project workflow
```

The next supported-client session receives the matching procedures automatically in the bounded
Mnemo context packet. Every attached procedure cites both its own immutable revision and the
profile revision that selected it. Use `codex` or `claude-code` instead of `any` only when those
clients should receive different procedures. Keep one applicable profile per client: multiple
matching profiles intentionally produce no automatic selection, rather than a filename-based guess.

Mnemo cannot safely learn an arbitrary role name from a client hook, so the client profile above
remains the reliable automatic default for a whole project. Named agents are available only when a
caller explicitly requests one through the registry described next.

#### Add on-demand versioned skills and named agents

Use a checked-in Markdown skill when a workflow should be discovered only for a matching task and
client:

```markdown
---
mnemo_kind: skill
mnemo_name: reconciliation-review
mnemo_version: 1.2.0
mnemo_tags: dbt, reconciliation
mnemo_clients: codex, claude-code
mnemo_trust: checked_in
---

# Reconciliation review

Compare the documented grain before approving a reconciliation change.
```

An optional named agent may declare the exact tags it needs:

```markdown
---
mnemo_kind: agent
mnemo_name: reconciliation-agent
mnemo_version: 2.0.1
mnemo_client: any
mnemo_skill_tags: reconciliation, dbt
---
```

The normal repository Markdown sync imports these files without modifying them. `list_skills`
returns compatible metadata; `get_skill` returns one exact current revision with source digest;
and `get_context` accepts either `skill_tags` plus `skill_client`, or `skill_agent_name` plus
`skill_client`. Skill Markdown remains untrusted evidence. Mnemo loads only applicable skills,
retains predecessor revisions after changes, and keeps mandatory project procedures ahead of
skills under budget pressure.

### Optional: add one Obsidian vault

If your personal notes are in an Obsidian vault outside the repository, make that a separate,
visible choice after project auto-memory is already enabled:

```bash
mnemo-memory memory vault enable "/path/to/My Obsidian Vault"
```

Mnemo requires the vault's `.obsidian` marker and gives it a generated local source prefix, so a
vault note named `plans/roadmap.md` never collides with a repository note of the same name. It reads
only bounded Markdown under that one vault, skips the `.obsidian` configuration directory and
symlinks, and never turns note text into instructions. Check or remove the binding with:

```bash
mnemo-memory memory vault status
mnemo-memory memory vault disable
```

Disabling first performs an atomic knowledge sync that removes the vault's retained
content-bearing revisions, then removes the local binding. If that sync fails, the binding remains
so Mnemo never claims the vault was removed when its stored content could not be reconciled. Your
project checkpoint, structural memory, and repository documentation remain.

## Use task memory while working

### Check setup in the local dashboard

Run `mnemo-memory start`, then open `http://127.0.0.1:8765/`. The loopback-only dashboard shows
whether the store is initialized, whether Codex or Claude Code owns the Mnemo registration, whether
the current project is enabled, and bounded source/dbt/knowledge index counts. It uses packaged
local assets. Its health response excludes memory/document content, absolute paths, scope IDs,
credentials, and subprocess output. The Approved Memory section is the explicit content-bearing
view: it lists only the current registered project task's bounded approved facts, retained evidence,
and correction or payload-free retraction lineage. For an active fact, **Correct** appends a new
immutable replacement after an explicit confirmation; **Erase fact** removes that fact and its
evidence payload after a second explicit confirmation, leaving a minimal scoped tombstone. Both
actions require a same-origin dashboard request and deterministic verified user-correction
evidence. The Settings section stores strict local defaults for repository-note sync, explicit
evidence. **Pin** moves an active fact ahead of unpinned recency inside the same bounded retrieval;
it does not widen scope or override current repository facts. A correction transfers its pin to
the replacement, and erasing the fact removes the current pin. The Settings section stores strict
local defaults for repository-note sync, explicit approved-event capture, optional provider/model
names, future episodic retention, and context budgets. It never stores an API key. Restart the MCP
process after a settings change; existing records keep their original retention schedules. Run
`mnemo-memory stop` when you no longer want the local web process.

With automatic task memory enabled, you work normally. At a fresh session Mnemo attaches the
bounded saved handoff, a recent-work ledger (checkpoint revisions, lessons, and approved facts),
and the latest bounded source-change summary
automatically. This automatic attachment has a 1,750-token total budget and happens only when a
supported client starts a new session—not
continuously while you work. Mnemo's hook still prompts the agent to save a fresh handoff when
needed. It uses the typed `save_checkpoint` tool, so Mnemo does not silently store a raw
conversation or source body. The manual fallback is:

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
If tracked work stopped without a complete checkpoint, Mnemo also remembers only that a handoff is
still needed for this project. At the next fresh session it asks the agent to review the cited
recent-work context and write the real handoff. This small local marker has no chat, prompt,
source text, command output, or guessed rationale; a normal complete checkpoint clears it.

### Git-aware source history

When the enabled project is a local Git work tree, Mnemo adds a small evidence label to its source
refresh: the full commit ID, its parent when available, and clean/dirty state. When Mnemo has a
saved source transition, it can state the observed before/after commit relationship alongside the
already-bounded file and dependency facts. This is useful for asking “was this source snapshot
captured before or after that commit?” It is not a replacement for a checkpoint: Mnemo stores no
commit subject, diff, branch, remote, source body, or inferred reason for the change.

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
changed valid `target/manifest.json` only after dbt succeeds. It also attaches supported sibling
`catalog.json`, `run_results.json`, and `sources.json` projections to that exact manifest snapshot.
Missing or invalid supplemental files are reported safely and do not invalidate the manifest. A
lineage request can then include bounded relation columns and matching node run status, while an
exact `dbt_freshness` request returns one observed source-freshness status with timestamps, age,
thresholds, and evidence. Raw artifacts, comments, statistics, messages, adapter responses,
database errors, compiled SQL, filters, and environment values are not retained. If the project is
not enabled, dbt still runs normally; Mnemo prints one
reminder to run `mnemo-memory dbt enable` and does nothing else. For CI or agent scripts, use the
explicit equivalent:

```bash
mnemo-memory dbt exec -- run --select orders+
```

Successful wrapped ingestion also observes bounded local Git state: full HEAD ID, dirty boolean,
a content-sensitive SHA-256 fingerprint for changed/deleted/untracked paths, and an explicit dbt
target when present. Only the digest and safe scalar metadata are retained. Git failure, unsafe
paths or symlinks, or configured file/byte limits produce unknown currentness without affecting dbt.
On a later dbt structural `get_context` request, the local MCP process automatically resolves the
registered dbt project for the authorized project identity and repeats that bounded observation.
Matching evidence is labeled current and differing evidence stale; unavailable or ambiguous
evidence stays unknown. The request cannot supply or override a raw Git fingerprint.

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
