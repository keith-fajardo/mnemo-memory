# Structural Knowledge Map — Design Spec

**Date:** 2026-08-25
**Status:** Draft (awaiting review)
**Scope:** v1 = "the map, fast" (locate/navigate only)

---

## Plain-language summary

Right now, when the agent needs to find something in the code ("where is the
local LLM used?"), it dispatches an **Explore agent** that re-reads the codebase
from scratch. That one lookup cost ~40k frontier tokens and ~2 minutes.

This design gives mnemo a **map of the code** — which files contain which
functions/classes, and which call which — that is stored, kept fresh, and
answered with a plain lookup. The agent asks the map instead of re-searching.

Think **map vs. directions**: this builds the *map* (where things are and how
they connect). It does not build *directions* (what to edit for a goal) — that
stays the frontier agent's job, and remembered routes are a later, separate
layer.

---

## Reality check (revised 2026-08-25 after codebase verification)

The original spec (below, kept for the reasoning trail) assumed we'd build the
store, freshness, and query surface from scratch. **Verification showed most of
it already exists**, so this spec's *actionable* scope is the four true gaps
listed here; the components table further down is annotated with what's real.

Already built:
- **Store** — `source_structure_snapshots/symbols/edges/files` tables
  (migrations `0004/0005/0008/0009`) + `SQLiteSourceStructureRepository`
  (`store_and_activate`, `get_active_snapshot`, `iter_symbols`, `iter_edges`,
  `find_symbols`, `module_symbols_for_paths`).
- **Capture** — runs automatically after every checkpoint save, on SessionStart,
  and on Stop (`connectors/automatic_memory/source_observation.py`,
  `hook.py:_refresh_source_structure`).
- **Diff / change** — `source_changes` is already a `get_context` parameter;
  `SourceImpactService.diff()` computes added/modified/removed files and sees
  new/untracked files (git status irrelevant to it).
- **Query surface** — `get_context` already exposes `source_query`,
  `source_overview`, `source_impact`, `source_changes`.

The four true gaps (this spec's real scope):

1. **Imprecise lookups.** `define` works (`source_query` → `find_symbols`), but
   `callers` and `imports` exist only as an undifferentiated reverse-dependents
   traversal in `source_impact` that **mixes callers and importers** (edge kind
   not separated, `unified_context.py:2352-2362`), and `contains` (symbols
   defined in one file) is not a first-class query at all.
2. **No incremental / skip capture.** Every trigger re-parses the whole tree
   (`rglob` + read every file); the digest dedup only fires *after* the expensive
   parse. An unchanged repo is fully re-parsed each time.
3. **Multi-language CHECK bug (latent, shippable).** The wired parser is
   multi-language, but migration `0004` CHECK constraints reject
   `kind='package'` (Go) and `edge_type='package_dependency'` (Rust). Indexing a
   Go/Rust repo raises a CHECK violation that is silently swallowed — the
   snapshot just fails to store. Python repos are unaffected, hiding it.
4. **Discoverability.** The surface exists but the agent doesn't reach for it
   (it dispatched an Explore agent instead). No dedicated, obvious lookup tool.

## Problem

- Locating code today = a live `Explore` subagent that re-scans the repo every
  time. It is always current but slow and token-expensive (a single navigation
  question ≈ 40k **frontier** tokens).
- mnemo already **computes** the needed structure and then throws it away:
  `SourceStructureParser.parse()` (in `packages/project_index/`) emits symbols,
  imports, and call edges as a `CodeStructureArtifact`. It is not persisted or
  queryable.
- Structural *memory* today is only file→sha256 **provenance**
  (`connectors/automatic_memory/checkpoint_evidence.py`) — "this answer was based
  on these files at this hash" — not a queryable symbol/edge graph.

## Goals (v1)

1. Persist the symbol/import/call graph the parser already produces.
2. Keep it trustworthy via **change-driven incremental rebuild** (re-parse only
   changed files) with a **content-hash backstop** for changes mnemo did not
   witness.
3. Expose one **deterministic** MCP lookup tool the agent calls instead of
   dispatching Explore for locate/navigate questions.

## Non-goals (explicit YAGNI cuts)

- **No impact analysis in v1.** "If I change this, what breaks?" is a natural
  phase 2 (`SourceImpactService` already exists), but v1 stays pure map.
- **Own-repo source only.** No dependencies / site-packages.
- **Passive routing only.** The agent chooses the tool because it is obviously
  cheaper. Auto-injecting structural facts via the `context_routing` classifier
  is deferred to phase 3.
- **No "directions."** Goal-specific "what to edit" and remembered routes are a
  different memory layer, out of scope here.

## Hard design rules

- **No LLM in the lookup path.** Neither the frontier model nor the local Ollama
  model runs during a lookup. Build = AST parse (deterministic). Query = SQLite
  read. The only model involvement is the frontier agent deciding to call the
  tool and reading the small result.
- **A stale map is worse than no map.** No answer is returned from an unverified
  file (see freshness).

---

## Architecture

Three small pieces over machinery that already exists.

### 1. The store (durable map)

Persist each parse into mnemo's SQLite, scoped per project (`owner/workspace/
project`). Node/edge shape:

- `source_files`: `path`, `content_hash`, `git_revision`, `parsed_at`
- `source_symbols`: `id`, `file`, `qualified_name`, `kind` (func/class/method),
  `start_line`, `end_line`
- `source_edges`: `from_symbol`, `to_symbol_or_name`, `kind`
  (`calls` | `imports` | `contains`)

This is the "web of contexts": files **contain** symbols; symbols **call**
symbols; files **import** modules — made durable instead of recomputed each
session. Bounded by the parser's existing limits (≤100k symbols, ≤200k edges).

### 2. The freshness engine

Change-driven incremental rebuild, hash as backstop:

1. Ask mnemo's existing change signal (dirty tracker / git; the same source
   feeding `MNEMO_DIRTY_V1` and `source_changes(relative_path=...)`) for the set
   of **added / modified / deleted** files since last parse.
2. Re-parse **only** added + modified files; delete rows for removed files;
   leave everything untouched otherwise.
3. Before returning an answer, **hash the specific files the answer draws from**.
   If a hash disagrees with the change signal (an edit mnemo did not witness —
   `git checkout`, another editor, another tool), re-parse that file first.
4. Answer from the now-current map.

Rationale: the change signal decides *what* to rebuild (cheap, incremental); the
hash protects the one assumption the signal rests on ("I saw every change").

**Open question (freshness completeness):** reverse queries ("who calls X?") are
only complete if *newly added* files are folded in before answering. The
add/modify/delete change-set covers this as long as the signal reports new files.
Spec-time task: confirm the change signal enumerates untracked/new files, else
add a periodic reconcile. See Open Questions.

### 3. The query tool

One new MCP tool, `structural_lookup`, deterministic, returns a compact
`file:line` list. Minimal query kinds:

- `define` — where is symbol `X` defined?
- `callers` — what calls `X`? (reverse edges)
- `contains` — what symbols are defined in file `Y`?
- `imports` — what imports module `Z`?

Worked example — "where is the local LLM used?":
`callers(generate)` + `imports(anthropic)` → a few rows, near-zero tokens, versus
the ~40k-frontier-token Explore run.

---

## Reuse map (what already exists vs. new)

| Piece | Status |
|---|---|
| Symbol/import/call extraction | **Exists** — `SourceStructureParser.parse()` → `CodeStructureArtifact` |
| Impact analysis (phase 2) | **Exists** — `SourceImpactService` |
| File hashing + git revision | **Exists** — `checkpoint_evidence.py` |
| Change signal (dirty/git) | **Exists** — `MNEMO_DIRTY_V1`, `source_changes` |
| SQLite storage | **Exists** — `packages/storage/sqlite` |
| Durable map tables | **New** |
| Freshness engine (incremental + hash backstop) | **New** |
| `structural_lookup` MCP tool | **New** |

## Phasing

- **v1 (this spec):** store + freshness + `structural_lookup` (locate/navigate).
- **Phase 2:** impact queries over `SourceImpactService` ("map + compass").
- **Phase 3:** proactive routing — `context_routing` detects locate/navigate
  intent and reaches for the map automatically. This is the real answer to
  "when should mnemo *know* to use it?" — deferred until the map has earned trust.

## Testing approach

- **Parser→store round-trip:** parse a fixture repo, assert symbols/edges land in
  SQLite with correct spans.
- **Freshness:** modify a file → assert only that file re-parses and the answer
  updates; delete a file → rows gone; **invisible change** (rewrite bytes without
  a change signal) → hash backstop catches it and the answer is current.
- **Query correctness:** `define/callers/contains/imports` on a known fixture,
  including a reverse-query completeness case with a newly added caller.
- **No-model guarantee:** assert the lookup path invokes no LLM client (local or
  frontier).
- **Token/latency check (informal):** the local-LLM-usage question answered via
  `structural_lookup` should cost a small fraction of the ~40k-token Explore run.

## Open questions

1. Does the existing change signal enumerate **new/untracked** files, or only
   tracked modifications? Determines whether reverse-query completeness needs a
   periodic reconcile.
2. Multi-language coverage for v1 — parser has Python/Go/Rust rules; confirm which
   are production-ready and scope v1 to those.
3. Where does the map live relative to project scope — one map per project, and
   how are branches handled (git revision is stored; is per-branch divergence a
   concern for v1)?
