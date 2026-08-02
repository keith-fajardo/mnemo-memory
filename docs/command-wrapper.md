# Command wrapping: what it will do for you

When this feature is enabled in a later Issue 14 step, you will be able to run a dbt command
through Mnemo and have Mnemo safely update its local structural memory after a successful dbt run.
The intended experience is:

```text
You run dbt → dbt runs normally → Mnemo notices the new valid manifest → later agents have current lineage
```

For example, the planned explicit form is:

```bash
mnemo-memory dbt exec -- dbt run --select orders+
```

It is designed to preserve the real dbt command’s arguments, interactive terminal output, colors,
working directory, and exit status.

## What is available today

The current build has only the safe underlying wrapper engine. It does **not yet** wrap `dbt`,
create a shell alias, inspect a dbt project, bind a project to Mnemo, or ingest a manifest after a
dbt command. Continue using the explicit workflow today:

```bash
# You run dbt yourself.
dbt run --select orders+

# Then explicitly ingest the manifest you want Mnemo to use.
mnemo-memory dbt ingest target/manifest.json \
  --owner-id "$MNEMO_OWNER_ID" \
  --workspace-id "$MNEMO_WORKSPACE_ID" \
  --project-id "$MNEMO_PROJECT_ID"
```

This limitation is deliberate: Mnemo will not pretend automatic dbt memory exists until the
project binding and post-run safety rules are complete and tested.

## What the future wrapper will and will not change

It will:

- run the exact dbt executable and arguments you supplied;
- check a configured local project binding before dbt starts;
- ingest a new valid manifest only after a successful, non-interrupted dbt command; and
- keep the previously active Mnemo snapshot if the command, parser, storage, or activation fails.

It will not:

- execute warehouse SQL hooks or modify your dbt project;
- install dbt-core or an adapter as a Mnemo dependency;
- read `profiles.yml`, expose environment variables, or store credentials;
- send a manifest, SQL, or task data to a network service; or
- replace dbt’s useful nonzero exit status with a Mnemo error.

## Failure behavior in plain language

The wrapper’s default is **fail-open**. If Mnemo’s optional memory step has a problem, dbt still
runs and its own result remains authoritative. Mnemo reports a short safe warning; it does not
print a traceback, SQL, credentials, or manifest contents.

A future `--strict-memory` option will be for users who prefer the opposite policy: a failed
pre-check stops dbt before it runs, and a failed memory update turns an otherwise-successful
wrapper result into Mnemo exit status 70. Even in strict mode, a dbt command that already failed
keeps dbt’s original failure status.

## Why this is a local process hook, not a dbt SQL hook

dbt SQL hooks run in a data warehouse. Mnemo’s planned hooks run locally around a local process.
That keeps task-memory lifecycle work out of the warehouse and avoids treating Mnemo as a dbt
adapter or a source of executable SQL.

## Technical reference

The generic engine uses an argv array rather than a shell command string, prevents recursion back
into Mnemo, preserves terminal streams, and uses bounded cleanup on interruption. It accepts only
trusted installed integrations; it will never import a Python file from your current directory or
dbt project. The exact contract and tests are recorded in
[ADR 0004](adr/0004-command-wrapper-hooks.md).
