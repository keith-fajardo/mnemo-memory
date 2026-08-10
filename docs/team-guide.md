# Mnemo Team mode in everyday terms

Team mode lets an organization provide one governed Mnemo service to multiple people and
workspaces. It keeps the same evidence-backed checkpoint, knowledge, source-structure, dbt, skill,
and context behavior as personal mode, but replaces the single-user SQLite trust boundary with an
authenticated PostgreSQL service.

Team mode is an operator-managed deployment, not a command that individual developers should run
on a laptop and expose to the network.

## When to use Personal or Team mode

| Situation | Recommended mode |
| --- | --- |
| One developer using Codex or Claude Code locally | Personal mode |
| One local database owned by one operating-system user | Personal mode |
| Several developers need shared workspace context | Team mode |
| Workspace membership and tenant isolation must be enforced centrally | Team mode |
| Central quotas, retention, backups, rate limits, and operations alerts are required | Team mode |

Do not use personal SQLite as a multi-user team database. Personal mode assumes one local trust
principal and binds local services to loopback.

## What a team member experiences

From a developer's point of view, the experience remains familiar:

1. The organization configures the Mnemo MCP resource for the supported agent.
2. The developer authenticates through the organization's OAuth system.
3. A request names the intended workspace and project.
4. Mnemo derives the principal from the verified token, checks membership and policy, and only then
   opens that workspace's authorized repositories.
5. The agent receives bounded, cited context and can save authorized handoffs.

One team member cannot turn a tool argument into another person's identity. An unknown or
unauthorized workspace is not treated as a wildcard, and filtering happens before ranking or
retrieval.

## What the organization gains

- shared project context without sharing personal SQLite files;
- verified OAuth identity and explicit workspace scope;
- PostgreSQL row-level security and deterministic application authorization;
- per-workspace checkpoint quotas and optional model-task budgets;
- retention, purge, correction, retraction, export, and deletion governance;
- durable outbox processing and content-free operational health signals;
- rate limits isolated by principal and workspace;
- verified backup and restore-drill workflows; and
- source, dbt, checkpoint, knowledge, and approved-fact storage parity with personal mode.

Memory is never used as authentication or authorization evidence. The authenticated request and
current control-plane membership remain authoritative.

## How the deployment boundary works

```text
Developer agent
      ↓ HTTPS + OAuth bearer token
Operator-managed TLS reverse proxy
      ↓ loopback-only MCP traffic
Mnemo Team service
      ↓ verified TLS connection
PostgreSQL with forced row-level security
```

The installed `mnemo-memory-team` service always listens on `127.0.0.1`. It cannot be configured
to bind directly to `0.0.0.0`. A separately controlled HTTPS reverse proxy exposes only the MCP
resource, forwards the authorization header, bounds requests, and prevents direct access to the
loopback port.

PostgreSQL connections require certificate verification and TLS 1.2 or newer. Database passwords
are read from bounded owner-only files, not environment values or command arguments. OAuth uses a
configured asymmetric public key and validates issuer, audience, expiry, issued-at time, subject,
client identity, algorithm, and required scopes before repository construction.

## What must be prepared before users connect

The operator must provide and verify:

- the reviewed `mnemo-unified-context[team]` installation;
- the versioned PostgreSQL schema and non-owner runtime role;
- forced row-level security and exact workspace memberships;
- positive checkpoint quotas for every enabled workspace;
- optional model-task budgets when model-backed extraction is enabled;
- a trusted PostgreSQL certificate chain;
- an OAuth issuer, resource audience, public verification key, and required scopes;
- an HTTPS reverse proxy that does not log bearer tokens or MCP bodies;
- one bounded Mnemo service process and a measured database connection budget;
- backup, restore-drill, deletion-propagation, retention, and key-rotation procedures;
- content-free status and alert checks; and
- a deployment-specific load test that includes HTTPS, OAuth, proxy, network, and database latency.

The checked-in synthetic/reference load gate does not include the full deployment network path, so
it cannot replace that environment-specific capacity test.

## Installation is only the first step

The optional runtime is installed with:

```bash
uv tool install "mnemo-unified-context[team]==0.1.0a16"
```

Do not start or expose it until the database, OAuth, secret files, quotas, proxy, operations checks,
and recovery plan are configured. The complete operator contract is in
[`deploy/team/README.md`](../deploy/team/README.md). The installed entry points are:

- `mnemo-memory-team` for the loopback MCP service; and
- `mnemo-memory-team-admin` for backup, restore-drill, deletion propagation, and content-free
  operations checks.

The team runtime is intentionally fail-closed when required configuration, secrets, membership,
quotas, or budgets are missing.

## How correction, retention, and deletion work

Team records carry explicit owner, workspace, project, source, visibility, sensitivity, and
retention scope. The service supports the same user-visible correction model as personal mode,
while administrators enforce lifecycle policy centrally:

- corrections append an evidence-linked replacement;
- retractions remove retained approved-fact payloads and preserve a bounded tombstone;
- expired episodic and checkpoint records stop appearing as current;
- authorized purge and physical-erasure operations propagate through canonical data and
  Mnemo-controlled projections;
- verified transfers preserve lifecycle tombstones instead of resurrecting deleted data; and
- backup deletion propagation prevents an erased subject from remaining in eligible retained
  backup generations.

User-held exports or copies outside Mnemo's control remain the responsibility of the organization
that created them.

## Security and release evidence

The accepted Team v1 review is pinned to revision
`e95696c41602d65b42e1c733e8e0e37696dd3ce3` in
[`docs/security-reviews/team-v1.toml`](security-reviews/team-v1.toml). It records no unresolved
critical or high findings. Its remaining informational findings require synchronized model-task
allowlists and a real deployment-specific end-to-end load test.

Useful operator references:

- [Team deployment boundary](../deploy/team/README.md)
- [Security review package](security-review-package.md)
- [Declared team load objectives](team-load-slo.md)
- [Authenticated Team MCP boundary](adr/0035-authenticated-team-mcp-boundary.md)
- [Secret-safe Team runtime](adr/0036-secret-safe-team-service-runtime.md)
- [Verified Team backup](adr/0038-verified-team-database-backup.md)
- [Team backup deletion propagation](adr/0039-team-backup-deletion-propagation.md)
