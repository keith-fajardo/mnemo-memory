# ADR 0004: Function-level command-wrapper hooks

- **Status:** accepted
- **Date:** 2026-08-03
- **Issue:** 14A.1

## Context

Mnemo needs a generic way to observe a locally executed command without coupling application
policy to process creation or trusting project-local Python code. The wrapper must preserve the
wrapped command's normal exit status and terminal behavior while making hook failures safe.

## Decision

The application contract owns immutable invocation, context, result, hook registration, warning,
and bounded outcome value objects. It receives executable resolution, process execution, clock,
and invocation-ID dependencies through ports. The local subprocess implementation is a connector:
it invokes an argv array with `shell=False` and inherited standard streams.

Before hooks run in registration order and receive no environment dictionary. Each successful
state is private to its matching after hook; after hooks run in reverse order, including after a
nonzero child result or a launch failure. Hook exceptions and malformed outcomes become sanitized
codes. Outcomes are bounded to an enum status, code, warnings, and small string metadata; they
cannot carry raw manifests, SQL, credentials, or arbitrary payloads.

The default policy is fail-open. A hook failure records a warning and preserves execution and the
child exit code. Strict-memory mode prevents execution after a failed before hook, and turns a
successful child result into Mnemo exit status 70 after a failed after hook. It never replaces a
nonzero child exit status. Resolution/launch failures have distinct machine-readable codes and
conventional statuses: not found 127, not executable 126, and wrapper/launch failures 125.

## Trust boundary and deferred work

Only a connector may create or manage a subprocess. The generic application package imports no
connector or dbt integration. Installed-package entry-point discovery, dbt project bindings, dbt
hooks, CLI integration, and shell integration are deferred to later Issue 14 substeps. Arbitrary
Python files from a working directory or a dbt project are never loaded by this kernel.

## Consequences

The process adapter performs bounded interrupt cleanup: terminate, wait, then kill and reap if
necessary. Its clocks and process factory are injectable, so unit tests do not patch global
subprocess, UUID, or datetime state. No dependency is added and no network, model, or dbt runtime
is required.

## Verification

Focused tests cover deterministic validation, resolver behavior, recursion prevention, hook
ordering/state handoff, fail-open and strict semantics, launch/interruption cleanup, structured
outcomes, and a real synthetic argv-only subprocess. Architecture validation enforces the
application-to-connector dependency direction.
