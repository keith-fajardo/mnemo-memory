# Hook background source refresh and snapshot pruning — design spec

- **Date:** 2026-10-03
- **Status:** design approved by the maintainer in conversation; spec for review
- **Branch:** `fix/hook-background-source-refresh` (from main a7101ba)

## 1. Why

Claude Code reports `UserPromptSubmit hook … timed out after 30s — output discarded`, repeatedly.

- **The hook rebuilds the code map synchronously.**
  - `AutomaticMemoryHook._refresh_source_structure` (connectors/automatic_memory/hook.py) runs `refresh_registered_project_source`. When the stat-only fingerprint changed, that does a full `SourceStructureParser().parse`, then `store_and_activate`, then a two-snapshot `SourceImpactService.diff`.
  - It runs on:
    - SessionStart, always
    - UserPromptSubmit, when the session is dirty and unsaved
    - Stop and PreCompact
    - PostToolUse after a checkpoint save
  - There is no time budget.
- **Measured on this repo, a parse alone takes 16.0 s.** That covers 640 files, 7,735 symbols and 43,741 edges.
- **Snapshots are never pruned.**
  - The user's `mnemo.sqlite3` is 4.8 GB, and about 4.7 GB of it is source-structure tables and indexes.
  - This project has 277 snapshots and 10.0M edges.
  - No code deletes source snapshots, and nothing runs VACUUM.
- **A timeout repeats itself.** When the client kills the hook mid-store, the transaction rolls back and the scan cache is not written. The next prompt therefore re-parses and times out again.

**Goal:**
- No hook event ever parses or stores source structure.
- The map refreshes in a detached background process.
- The database stays bounded.
- A one-off command shrinks the existing database.

## 2. Hook changes

- **The freshness check stays in the hook.** It is the existing stat-only fingerprint compared against the scan cache and the active snapshot id, with no parse.
  - **Fresh:** behaviour is unchanged. The hook uses the active snapshot. When the caller asks for it (`include_latest_transition`), it computes the latest transition's diff as today.
  - **Stale, or no active snapshot:**
    - The hook starts the background refresh (§3) and returns immediately using the current active snapshot. If there is none, it returns `_SourceRefresh(None)`, as a failed refresh does today.
    - It reports changes only from the latest *stored* transition, and only when the caller asked for it.
    - The result carries `refresh_pending=True`.
- **Notices.**
  - When `refresh_pending` is set, the dirty reminder and the Stop/PreCompact checkpoint instruction add one fixed line: `Code map refresh running in background; structural change details may lag one prompt.`
  - They contain no paths or file names beyond what they contain today.
  - *Update 2026-10-03 (implementation):* the fixed line is now `Code map refresh running in background; use source_changes for structural change details.` The hook no longer carries change details at all on a stale map (it never parses, so it has nothing current to report), so "may lag one prompt" overpromised; the line instead points agents to `source_changes`, which reads the stored transitions once the worker has written them.
- **Git baseline (PostToolUse after save).** When the map is stale, no new baseline is written. This fails closed, as today's refresh failure does.
- **Spawn rules:**
  - The spawn happens after the hook has built its output, never waits, and ignores every failure.
  - It is skipped when a refresh worker already holds the run lock (a status probe only; the worker's own lock is authoritative).
  - The argv is `[sys.executable, "-P", "-m", "mnemo_memory.apps.cli.source_refresh_entry", "--data-dir", <dir>, "--project-root", <root>]`. It starts in a new session with DEVNULL stdio and `close_fds`, the same pattern as `_start_note_judge`.
  - The spawn function is injectable, so tests never start real processes.

## 3. Background refresh worker

- **Entry module.** `mnemo_memory.apps.cli.source_refresh_entry`. Like `judge_entry`, it loads neither typer nor `apps/cli/main.py`, catches every exception, prints nothing and always exits 0.
- **Single instance.** It takes a non-blocking exclusive `flock` on `<data_dir>/.source-refresh.lock`. If the lock is busy, it exits at once.
- **Work, in order:**
  1. Resolve the project binding for `--project-root` the same way the hook does.
  2. Run `refresh_registered_project_source` with the same `cache_dir`. On a changed tree that means parse, store and activate, then write the cache.
  3. Record the git observation for the new digest, as the hook does today (`_observe_git`, through a shared helper rather than a copy).
  4. Prune (§4) one bounded step.
  5. Exit.
- **Re-check.** If the tree changed again while the worker was parsing (the fingerprint differs after the store), the worker loops once more, at most 3 parses per run.

## 4. Snapshot retention

- **Keep:**
  - the active snapshot
  - every snapshot among the most recent **17** activations of the project, which is enough for `source_changes` (at most 17 transitions) and `latest_transition`
  - the header row of every snapshot referenced by `checkpoint_source_observations`
- **Delete** everything else for that project's scope. For a checkpoint-referenced snapshot, that means its files, symbols and edges but not its header. For any other snapshot, it means the whole snapshot, along with its activation rows.
  - Activation rows of a header-only snapshot are kept. Deleting them would shorten the history that `list_activation_history` reports.
- **Order** (every FK is `ON DELETE RESTRICT`): edges, then symbols, then files, then activations, then the snapshot.
- **Bounded steps.** The worker deletes at most **4 snapshots per run**, one transaction per snapshot, so the write lock is held briefly and concurrent hooks are not starved (`busy_timeout` 5 s).
- **Pinned lookups.** Reading a pruned snapshot through an explicit MCP `snapshot_id` raises the existing `SourceSnapshotNotFound`; behaviour is unchanged.
- **Repository API.** `SQLiteSourceStructureRepository.prune_source_snapshots(scope, *, keep_activations=17, max_snapshots: int | None) -> int` returns the number of snapshots pruned (full or header-only). `None` means no limit (CLI only).

## 5. One-off maintenance command

- **Command.** `mnemo-memory maintenance prune-source [--project-root PATH | --all-projects] [--compact] [--dry-run]`.
  - It prunes with no per-run limit, following the §4 rules.
  - `--compact` then runs `VACUUM`.
  - `--dry-run` prints only counts: snapshots to prune (full and header-only), the edges and symbols involved, and the current file size. It changes nothing.
- **Safety:**
  - It refuses to run while a refresh worker holds `.source-refresh.lock`.
  - Before `VACUUM`, it checks that free disk space is at least the database file size, and refuses otherwise.
  - It prints the before and after file sizes.
  - It never runs from a hook.

## 6. Out of scope

- **The MCP `save_checkpoint` path.** `CheckpointSourceObserver` parses synchronously in the MCP process. It is slow, but it does not hit the hook's 30 s limit. Recorded as a follow-up.
- **Incremental parsing.**
- **The Postgres team schema.**
- **The client hook `timeout` in `~/.claude/settings.json`.** It stays as is.
- **Reinstalling the user's tool** (installed a25). That is a separate, explicit step after merge, done with the maintainer's approval.

## 7. Testing

Tests use temporary data directories and fake spawners only. The user's real database is never opened by a test.

- **Hook:**
  - A stale tree makes no parse call and starts the worker once.
  - A fresh tree behaves as today.
  - A pending refresh adds the fixed notice line.
  - A spawn failure is ignored.
  - No spawn happens while the worker lock is held.
  - The PostToolUse baseline is skipped when stale.
  - Wall-time guard: a hook run against a stale tree with a parser that sleeps 5 s returns in under 1 s.
- **Worker:**
  - The lock prevents a second run.
  - It parses, stores and writes the cache.
  - It re-checks at most 3 times.
  - It exits 0 silently on any exception.
  - The entry module loads neither typer nor `apps.cli.main`.
- **Prune:**
  - It keeps the active snapshot and the latest 17 activations.
  - Checkpoint-referenced snapshots keep only their header.
  - The FK delete order works with `foreign_keys=ON`.
  - The per-run limit of 4 is respected.
  - After pruning, `source_changes` and `latest_transition` still work.
- **CLI:**
  - `--dry-run` changes nothing.
  - It refuses while the lock is held.
  - It refuses `--compact` when free space is low (injected).
  - The file shrinks after `--compact` on a seeded temporary database.
- **Checks.** The existing hook contract and lifecycle tests pass, and `npm run check` is green.
