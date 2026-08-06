# ADR 0035: Bind team MCP storage identity to a verified OAuth subject

## Status

Accepted on 2026-08-06.

## Context

PostgreSQL team repositories already apply deterministic authorization and forced row-level
security, but their `principal_id` is a constructor input. A network transport must never derive
that principal from MCP tool arguments. It also needs an OAuth resource-server boundary without
making Mnemo an authorization server or retaining bearer tokens.

## Decision

The optional team MCP profile uses FastMCP's Streamable HTTP bearer middleware and a Mnemo-owned
`TokenVerifier` adapter. The adapter validates a JWT with one configured asymmetric public key and
one approved algorithm (`RS256`, `PS256`, or `ES256`). It requires an exact HTTPS issuer and
resource audience, `exp`, `iat`, a canonical UUID `sub`, bounded client identity, and every
configured scope. Algorithm choice is never taken from the token. It returns only the minimized
issuer claim to request context and never persists or logs the token.

After middleware verification, the request-bound MCP port obtains the principal only from `sub`
and requires an explicit canonical `workspace_id` in the tool request. Only then may it construct
the existing PostgreSQL checkpoint, episodic, knowledge, source-structure, dbt, procedure, and
skill repositories for that principal/workspace pair. Existing application validation,
authorization policy, and forced RLS remain the canonical data controls. Tool-supplied owner or
workspace values cannot replace the authenticated principal.

The server is stateless and binds only to `127.0.0.1`. Its advertised resource URL and issuer must
use HTTPS, so deployment requires a separately controlled TLS reverse proxy. This issue does not
open a non-loopback listener. Static public-key configuration avoids a runtime JWKS network and its
SSRF/cache/rotation complexity; planned deployment configuration must provide a restart-based key
rotation procedure before general team availability.

## Consequences

- Unauthenticated, invalidly signed, expired, wrong-issuer, wrong-audience, wrong-scope, and
  malformed-subject requests fail before repository construction.
- One request cannot select its database principal through MCP arguments.
- The same PostgreSQL parity implementation serves local integration and authenticated MCP calls;
  no competing memory path or schema is introduced.
- Mnemo is an OAuth resource server, not an authorization server, and stores no client secret,
  signing key, refresh token, or bearer token.
- A deployment command, TLS proxy contract, public-key file permissions, and rotation runbook
  remain required before the loopback server becomes an operable remote service.

## Dependencies and reversal

The optional `team` extra now declares the already locked and reviewed `pyjwt==2.13.0` package as a
direct replaceable dependency. Its MIT license and provenance are recorded in the dependency
register. Removing the connector and optional requirement returns the prior storage-only profile;
no stored data or migration changes are involved.

## Verification

Security tests generate independent keys and cover exact valid claims, alternate signatures,
issuer, audience, scope, subject, client identity, unauthenticated HTTP, loopback/stateless server
settings, and failure before repository construction. The mandatory real-PostgreSQL suite proves
an authenticated owner receives its checkpoint while a private-project viewer and a foreign
workspace receive no checkpoint through the service composition.
