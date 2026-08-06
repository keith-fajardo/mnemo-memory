# Team MCP deployment boundary

The optional team service is an authenticated OAuth resource server over MCP Streamable HTTP. It
is not yet a general-availability team release: backup/restore deletion propagation, quotas,
operational dashboards, load objectives, and the independent security review remain release gates.

## Install and prerequisites

Install the optional PostgreSQL profile from a reviewed Mnemo release:

```bash
uv tool install 'mnemo-unified-context[team]'
```

Provision the PostgreSQL schema and non-owner runtime role using the existing team migration
process. The runtime PostgreSQL endpoint must present a certificate trusted by the host; Mnemo
always enables hostname verification and never offers a plaintext database option.

Write the database password to a regular file owned by the service user with mode `0600`. Do not
put it in the environment, command line, repository, service definition, or proxy configuration.
Write the OAuth issuer's current PEM public key to a regular file owned by the service user; it may
be mode `0644` but must not be group- or other-writable. Symlinks, relative paths, oversized files,
and unsafe permissions fail closed.

## Non-secret environment

Configure these variables in the service manager. The example file beside this document contains
no secret value.

- `MNEMO_TEAM_DB_HOST`, `MNEMO_TEAM_DB_PORT`, `MNEMO_TEAM_DB_NAME`, `MNEMO_TEAM_DB_USER`
- `MNEMO_TEAM_DB_PASSWORD_FILE` — absolute owner-only password-file path
- `MNEMO_TEAM_OAUTH_PUBLIC_KEY_FILE` — absolute PEM public-key path
- `MNEMO_TEAM_OAUTH_ISSUER` — exact HTTPS token issuer
- `MNEMO_TEAM_RESOURCE_URL` — exact public HTTPS MCP URL, including `/mcp`
- `MNEMO_TEAM_OAUTH_ALGORITHM` — `RS256` by default; `PS256` and `ES256` are also approved
- `MNEMO_TEAM_REQUIRED_SCOPES` — whitespace-separated, default `mnemo:context`
- `MNEMO_TEAM_HTTP_PORT` — loopback upstream port, default `8766`

Start the installed entry point under a process supervisor:

```bash
mnemo-memory-team
```

Mnemo always listens on `127.0.0.1`; there is no environment override. A configuration or
connection failure emits only a stable `MNEMO_TEAM_*` code.

## HTTPS reverse proxy

The reverse proxy is a separate security boundary and is not bundled. It must:

- expose only HTTPS with a valid certificate and redirect or reject plaintext HTTP;
- proxy the public `/mcp` resource to `http://127.0.0.1:8766/mcp`;
- preserve the `Authorization: Bearer` header and request/response streaming semantics;
- accept bounded request bodies and enforce infrastructure connection/time limits;
- never log authorization headers, MCP bodies, database credentials, or token claims;
- prevent all direct network access to Mnemo's loopback port.

Do not bind Mnemo to `0.0.0.0` or publish port `8766` from a container. Any future direct
non-loopback listener requires a separate security ADR, authentication, authorization, and
encryption review.

## Start, stop, and verification

After start:

1. An unauthenticated request to the public `/mcp` URL must return `401`.
2. A valid token with the exact issuer, resource audience, UUID subject, and required scopes must
   complete MCP initialization.
3. `get_context` with one explicit authorized task scope must return that scope; a private-project
   viewer and a foreign workspace must receive no checkpoint.
4. PostgreSQL certificate or hostname failure must produce `MNEMO_TEAM_POSTGRES_UNAVAILABLE` and
   no fallback connection.

Stop the supervisor process to stop the service. The service retains no bearer or refresh token;
stopping it does not modify team data.

## Team knowledge source governance

New shared knowledge is intentionally pending: its text, links, procedures, skills, and embeddings
do not enter `get_context` until an authorized reviewer approves the source. Use
`list_knowledge_sources` with the exact owner, workspace, project, and visibility to inspect bounded
content-free status. It returns the relative path, current revision, immutable source owner,
current revision author, explicit authentication flags for both attributions, and approval
metadata; it never returns note content. Pre-v21 rows show false attribution flags because their
historical actors cannot be reconstructed.

A project maintainer, workspace administrator, or workspace owner can call
`approve_knowledge_source` with the exact `document_id`, current `expected_revision_id`, and a unique
caller-controlled `source_action_key`; its bearer token must also carry
`mnemo:knowledge:approve`. Exact retry is idempotent. A stale revision, reused key,
contributor/viewer role, foreign scope, or deleted source fails closed. Approval follows the stable
source identity through later conflict-checked revisions; two corrections based on the same
revision cannot both commit.

## Password and public-key rotation

Create the replacement file beside the old file with its final permissions, then atomically rename
it onto the configured path. Restart the service so it opens the new file without following a
symlink. For OAuth key rotation, coordinate the issuer's signing-key change and restart during the
issuer's documented overlap window; verify a new-key token succeeds and an old-key token is
rejected after the overlap. Roll back by atomically restoring the previous public file and
restarting. Never print either credential or token during verification.
