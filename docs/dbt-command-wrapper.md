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
check configured project → run the exact real dbt command → ingest a changed valid manifest → return dbt's exit code
```

If dbt fails or is interrupted, Mnemo does **not** activate a manifest, even if a file was written.
If the manifest is unchanged, it does nothing. If ingestion fails in the default mode, dbt’s own
result remains the result you see and the prior Mnemo snapshot stays active.

## One-time setup for a dbt project

First, initialize Mnemo if you have not already:

```bash
mnemo-memory init
```

Then bind the dbt project root to your stable Mnemo owner/workspace/project UUIDs. The UUIDs are
explicit privacy boundaries; they are not derived from your directory or manifest. The root must
contain `dbt_project.yml`.

```bash
mnemo-memory dbt configure \
  --project-dir /absolute/path/to/your-dbt-project \
  --owner-id "$MNEMO_OWNER_ID" \
  --workspace-id "$MNEMO_WORKSPACE_ID" \
  --project-id "$MNEMO_PROJECT_ID"
```

The binding is stored locally in Mnemo’s data directory with restrictive permissions. It stores the
path-to-scope mapping only; it does not read or store `profiles.yml`, credentials, environment
variables, SQL, or manifest content.

Check or remove the binding at any time:

```bash
mnemo-memory dbt configuration --project-dir /absolute/path/to/your-dbt-project --check
mnemo-memory dbt unconfigure --project-dir /absolute/path/to/your-dbt-project
```

## Use the wrapper immediately

The reliable form for terminals, CI, Codex, and Claude Code is explicit:

```bash
mnemo-memory dbt exec -- run -s orders+
```

Everything after `--` is passed as separate arguments to the real `dbt` executable. Mnemo honors
both `--project-dir value` / `--project-dir=value` and `--target-path value` /
`--target-path=value` without rewriting them.

By default it resolves dbt in this order:

1. `--dbt-executable /absolute/path/to/dbt`
2. `MNEMO_DBT_EXECUTABLE` when it is an absolute path
3. `dbt` found on `PATH`

Mnemo refuses a relative configured executable and refuses recursion back into its own wrapper.

## Make normal `dbt` commands use Mnemo (interactive shells)

Generate a small shell function:

```bash
mnemo-memory dbt shell-hook zsh
mnemo-memory dbt shell-hook bash
mnemo-memory dbt shell-hook fish
```

For zsh, opt in for the current shell with:

```bash
eval "$(mnemo-memory dbt shell-hook zsh)"
```

Now `dbt run -s orders+` routes to `mnemo-memory dbt exec -- run -s orders+`, which finds the
real filesystem dbt executable rather than calling the shell function again.

If you want this every time a new shell starts, add the same `eval` line to your own `~/.zshrc`
(or use the bash/fish equivalent). Mnemo never edits a shell profile automatically. Removing that
line, starting a new shell, or running `mnemo-memory dbt unconfigure` returns you to the explicit
workflow; none of these actions delete prior immutable snapshots.

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
| `MNEMO_DBT_PROJECT_UNCONFIGURED` | Run `mnemo-memory dbt configure` for the project root. |
| `MNEMO_COMMAND_NOT_FOUND` | Install dbt or provide `--dbt-executable` with an absolute executable path. |
| Manifest is unavailable | Check dbt’s target path and confirm the command generates `manifest.json`. Mnemo keeps the old snapshot. |
| Snapshot conflict | Another process activated a newer manifest first. Re-run dbt/Mnemo against the current project state; Mnemo never overwrites the winner. |
| Want no wrapping | Do not load the shell hook, or remove its one line from your shell profile. Manual `mnemo-memory dbt ingest` remains available. |

Manual `mnemo-memory dbt ingest` and `mnemo-memory dbt status` remain supported. The wrapper does
not add an MCP tool; the MCP inventory remains exactly `get_context` and `save_checkpoint`.
