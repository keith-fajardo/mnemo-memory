# Run dbt normally and keep Mnemo lineage current

This feature lets Mnemo refresh its local dbt lineage after a successful dbt command—without
changing dbt itself, installing dbt-core as a Mnemo dependency, or sending anything to a warehouse.

## What you get

After a one-time local setup, this familiar command can keep Mnemo’s manifest snapshot current:

```bash
dbt run -s orders+
```

Mnemo wraps that invocation as:

```text
check configured project → run the exact real dbt command → ingest valid local artifacts → return dbt's exit code
```

If dbt fails or is interrupted, Mnemo does **not** activate a manifest, even if a file was written.
If the manifest is unchanged, Mnemo can still attach a newly generated `catalog.json` or
`run_results.json` to that exact snapshot. If ingestion fails in the default mode, dbt’s own result
remains the result you see and the prior Mnemo snapshot stays active.

## The simple setup: once per machine, once per repository

### Once per machine: make ordinary `dbt` use Mnemo

For zsh, put this one line in your own `~/.zshrc`:

```bash
eval "$(mnemo-memory dbt shell-hook zsh)"
```

For the current shell only, run the same line once in the terminal. It defines a `dbt()` function
that forwards every normal `dbt …` command to Mnemo, then Mnemo runs the real dbt executable. Use
`bash` or `fish` in the command for those shells. Mnemo never edits a shell profile automatically.

### Once per dbt repository: enable Mnemo

From the dbt repository, enable Mnemo once. The root must contain `dbt_project.yml`.

```bash
cd /absolute/path/to/your-dbt-project
mnemo-memory dbt enable
```

`enable` initializes the personal Mnemo profile if necessary, creates private stable identities,
binds this canonical project directory, and ingests an existing valid `target/manifest.json` when
one is present. Matching sibling `catalog.json` and `run_results.json` files are minimized and
attached when valid. You do not need to create, see, or remember UUIDs. The binding is stored locally
with restrictive permissions. It stores the path-to-scope mapping only; it does not read or store
`profiles.yml`, credentials, environment variables, SQL, or manifest content.

Check or remove the binding at any time:

```bash
mnemo-memory dbt status
mnemo-memory dbt disable
```

After those two one-time steps, keep using dbt normally:

```bash
dbt run -s orders+
```

The function forwards those exact arguments safely. Mnemo honors both `--project-dir value` /
`--project-dir=value` and `--target-path value` / `--target-path=value` without rewriting them.

The explicit form is for CI, Codex, Claude Code, or a shell where you do not load the function:

```bash
mnemo-memory dbt exec -- run -s orders+
```

By default it resolves dbt in this order:

1. `--dbt-executable /absolute/path/to/dbt`
2. `MNEMO_DBT_EXECUTABLE` when it is an absolute path
3. `dbt` found on `PATH`

Mnemo refuses a relative configured executable and refuses recursion back into its own wrapper.

Remove the profile line, start a new shell, or run `mnemo-memory dbt disable` to return to the
explicit workflow; none of these actions delete prior immutable snapshots.

## Before and after behavior

Before dbt starts, Mnemo finds the configured project root, resolves the expected manifest path,
records its digest if present, and records the current active Mnemo snapshot. It does not modify
structural memory then.

After dbt returns successfully, Mnemo reads the completed manifest once, enforces existing
manifest limits/schema rules, and compares the digest with the pre-run digest:

- **changed and valid** → atomically activate a new immutable snapshot;
- **unchanged** → report an idempotent no-op;
- **missing** → leave the prior snapshot unchanged and report unavailable;
- **invalid, storage failure, or competing snapshot update** → leave the prior snapshot unchanged
  and report a safe failure.

After a valid manifest is selected, Mnemo independently checks sibling `catalog.json`,
`run_results.json`, and `sources.json` files. Valid supported artifacts attach only to that manifest snapshot. Missing,
malformed, unsupported, or mismatched supplemental files produce bounded statuses and do not
invalidate the manifest. Mnemo retains relation/column identities and node run status/timing only;
warehouse comments, statistics, adapter messages, database errors, freshness filters, compiled SQL,
environment values, and arbitrary arguments are discarded.

The command’s default summary is written to stderr so dbt’s normal stdout remains usable. Add
`--json-summary` when an automation needs a structured wrapper result.

## Default versus strict memory

Default behavior is fail-open: an optional Mnemo hook failure does not turn a successful dbt run
into a failure. Use `--strict-memory` only when a successful dbt command must also result in a
successful Mnemo memory update. In strict mode, a failed pre-hook prevents dbt from starting, and a
failed post-hook changes a previously-successful wrapper result to exit status 70. A nonzero dbt
exit code always wins over Mnemo’s strict status.

## Troubleshooting

| What you see | What to do |
| --- | --- |
| Mnemo says the project is not enabled | Run `mnemo-memory dbt enable` from that dbt repository. dbt still ran normally. |
| `MNEMO_COMMAND_NOT_FOUND` | Install dbt or provide `--dbt-executable` with an absolute executable path. |
| Manifest is unavailable | Check dbt’s target path and confirm the command generates `manifest.json`. Mnemo keeps the old snapshot. |
| Snapshot conflict | Another process activated a newer manifest first. Re-run dbt/Mnemo against the current project state; Mnemo never overwrites the winner. |
| Want no wrapping | Do not load the shell hook, or remove its one line from your shell profile. Manual `mnemo-memory dbt ingest` remains available. |

Manual `mnemo-memory dbt ingest` and `mnemo-memory dbt status` remain supported. The wrapper does
not add an MCP tool; the MCP inventory remains exactly `get_context` and `save_checkpoint`.
