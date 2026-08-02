# Synthetic prior coding session: local configuration error rendering

## E01 — Objective and operating constraints

The requested change is narrow: render local configuration validation failures as stable
`MNEMO_CONFIG_INVALID` messages for the local configuration command. Keep the command offline,
keep the existing configuration precedence, and do not add a new settings library. The developer
also asked that malformed input never reveal a private data directory, a complete environment, or a
traceback. This is a synthetic exercise repository; there are no credentials, real profiles, or
external services involved.

## E02 — Initial inspection

I inspected `packages/application/config.py`, `apps/cli/main.py`, and the lifecycle tests. The
configuration object already rejects unknown fields, non-loopback hosts, invalid log levels, and
relative configured data paths. `mnemo init`, `start`, `status`, and `stop` all resolve their
configuration through the same application function. The CLI had a generic exception path, but
its output differed depending on whether a parsing failure was raised by the command wrapper or by
the configuration value object.

## E03 — Repository constraints and non-goals

The project architecture says application code owns configuration validation and transport code
only adapts expected outcomes. FastAPI must not become a second configuration parser. MCP is not in
scope for this coding task, nor are client integrations, database changes, model calls, or new
dependencies. The relevant unit suite uses isolated temporary directories, so the regression must
not inspect a real home directory. The working tree has no unrelated changes and all current
checks begin from `npm run check`.

## E04 — Superseded proposal

My first proposal was to move all configuration validation into the Typer CLI commands so that each
command could choose its own user-facing wording. That would make `init` easy to patch quickly, but
it duplicates the same host, path, and log-level rules in four commands and leaves the API startup
path with a different contract. This proposal is superseded and must not be treated as the current
decision.

## E05 — Failed approach

I tried catching `ValueError` separately in each Typer command. The result was inconsistent:
`init` returned a friendly message, while `status` fell through to the generic handler. It also
made a future API caller responsible for knowing which text belonged to which validation rule. Do
not catch ValueError separately in each Typer command because that produced inconsistent messages.
The failed branch was discarded without changing the configuration domain rules.

## E06 — More inspection and test evidence

The existing lifecycle tests already prove default data-directory selection, environment override
precedence, rejection of paths occupied by files, and loopback binding. A temporary configuration
with `log_level = "verbose-ish"` currently reaches the configuration parser, but no test asserts
the stable CLI code. The test fixture should write only a temporary configuration and invoke the
Typer runner with controlled input. It should not start Uvicorn, open SQLite, or talk to an MCP
server for this narrow error rendering regression.

## E07 — Accepted decision and rationale

The accepted decision is: keep validation in LocalConfig and translate only at the CLI boundary.
Keeping validation in LocalConfig preserves one error contract for CLI, API, and MCP startup. The
CLI boundary may map the typed local validation exception to `MNEMO_CONFIG_INVALID`, but it must
not reinterpret the invalid field or synthesize another configuration object. This keeps the
adapter thin while allowing future surfaces to choose sanitized presentation without changing the
underlying rule.

## E08 — Implementation detail

The smallest change is a shared CLI helper that recognizes the existing local configuration error
and returns the stable code with a concise field-level reason. The helper belongs next to the
existing CLI runtime-error rendering. It must be used by all four lifecycle commands through their
current common execution path, not copied into command bodies. Preserve `--json` output structure
if it already exists; the text mode must also avoid machine-specific absolute paths. No migration,
schema, or dependency-register change is required.

## E09 — Modified and relevant files

Relevant files are `packages/application/config.py`, `apps/cli/main.py`, and
`tests/unit/test_lifecycle.py`. The application configuration file contains the source validation
and should remain the owner of those rules. The CLI file contains the one presentation adapter.
The lifecycle test file is the correct home for a malformed log-level regression because it already
covers isolated configuration and command output. No other source file should be changed for this
task.

## E10 — Irrelevant discussion retained in the raw transcript

We discussed whether a future desktop UI could color error messages, whether local file names should
be shown in a troubleshooting screen, and whether a contributor guide might mention terminal color
preferences. Those ideas are not part of this change. We also talked about a hypothetical dbt
manifest parser and a future cross-client benchmark; neither belongs in this local configuration
work. This noise remains in the full-transcript baseline to model a real handoff, but it must not
be added to the checkpoint.

## E11 — Verification performed

After adding the shared CLI translation boundary, the focused lifecycle tests passed. The existing
configuration unit tests also still passed, including the temporary-directory path checks and the
loopback default. No full `npm run check` has been run after the pending malformed log-level test,
so the task is not verified complete. The checkpoint must record this as verification performed,
not as evidence that all repository checks have passed.

## E12 — Explicit uncertainty

The API likely already formats malformed log-level errors correctly, but nobody verified that in
this session. Treat this as an unverified inference, not a current fact or a reason to widen this
task. The next session should avoid changing the API unless the focused test reveals a shared
adapter defect. The regression task is intentionally limited to proving the CLI presentation code.

## E13 — Next action

Add a regression test for a malformed log level, then run `npm run check`. The test should assert
the nonzero command result, include `MNEMO_CONFIG_INVALID` in the safe output, and prove no mutation
command was attempted. Reuse the isolated test configuration convention and avoid asserting a full
exception string that might leak a path. Once the focused test passes, run the repository gate and
stop rather than expanding into unrelated configuration cleanup.

## E14 — Handoff notes and more irrelevant context

The previous session also contained a long review of naming choices, a possible changelog sentence,
and a comparison of two terminal themes. It included copied command output from successful tests,
discussion of test ordering, and a rejected idea to add a dependency for configuration parsing.
Those details may be visible in a transcript replay but are not required to resume the task. They
should not increase the canonical checkpoint merely because capacity exists.

## E15 — Explicit stopping point

The task is stopped before the malformed log-level regression test is written. The accepted
decision, failed per-command catch approach, relevant files, and focused-test result are settled.
The remaining work is exactly the regression test followed by `npm run check`; no additional design
decision is pending. Resume from that next action and preserve the accepted LocalConfig ownership
boundary.

## E16 — Handoff fact ledger

For the handoff, record the exact resumption facts: Render local configuration validation errors
as stable MNEMO_CONFIG_INVALID messages. Keep validation in LocalConfig and translate only at the
CLI boundary. Keeping validation in LocalConfig preserves one error contract for CLI, API, and MCP
startup. Do not catch ValueError separately in each Typer command because that produced inconsistent
messages. Relevant files are packages/application/config.py, apps/cli/main.py, and
tests/unit/test_lifecycle.py. The focused lifecycle tests passed after the shared CLI translator was
added. Add a regression test for a malformed log level, then run npm run check. The task is stopped
before the malformed log-level regression test is written. The previous proposal, Move all
configuration validation into the CLI commands, remains superseded and is not current.
Use an isolated temporary configuration for the regression test.
