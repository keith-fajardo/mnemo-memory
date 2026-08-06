# ADR 0036: Keep team service secrets file-backed and transport loopback-only

## Status

Accepted on 2026-08-06.

## Context

The authenticated team MCP server needs an installed process entry point. Passing a database
password in a command argument or environment variable risks process-list, supervisor, diagnostic,
and crash-report disclosure. Directly exposing the Python server would also bypass a deliberately
managed TLS termination boundary.

## Decision

`mnemo-memory-team` loads only bounded non-secret connection and OAuth metadata from environment.
The database password comes from an absolute owner-owned regular file with no group or other
permissions. The OAuth public key comes from an absolute owner-owned regular file with no group or
other write permission. Both are opened read-only with no-follow semantics, verified by descriptor,
bounded before UTF-8 decoding, and read once at startup. Errors return stable content-free codes.

Every PostgreSQL connection uses the standard certificate-verifying client context with hostname
checking and TLS 1.2 or newer. There is no plaintext option. The MCP upstream always binds to
`127.0.0.1`; external HTTPS termination and authorization-header forwarding belong to a separately
controlled reverse proxy. Mnemo offers no host override.

Password and public-key rotation use an atomic file replacement followed by service restart. The
deployment runbook defines permissions, proxy requirements, start/stop checks, expected 401
behavior, database-TLS failure, key rotation, and rollback without printing secrets or tokens.

## Consequences

- Secrets do not appear in the installed command arguments, environment contract, or repository.
- Symlinks, relative paths, permissive password modes, writable public keys, oversized files, and
  invalid UTF-8 fail before server start.
- PostgreSQL never silently falls back from verified TLS.
- A reverse proxy and its certificate remain deployment responsibilities; Mnemo itself stays
  unreachable off-host.
- Rate limits, quotas, service dashboards, backup/restore, and release audits remain separate
  blockers to general team availability.

## Verification

Security tests cover valid startup, every unsafe password mode, secret/key symlinks, relative and
invalid configuration, content-free failures, and the exact TLS context passed to the database
driver. Packaging verification includes the dedicated entry point and deployment files. The full
gate continues to run the bearer-authentication and real PostgreSQL RLS suites.
