# Team MCP deployment boundary

The optional team service is an authenticated OAuth resource server over MCP Streamable HTTP. The
Team v1 review pinned in `docs/security-reviews/team-v1.toml` records no unresolved critical or
high findings. Each operator must still complete the deployment-specific HTTPS/OAuth/proxy load
test, secret provisioning, recovery checks, and capacity review described below before exposing a
release.

For a non-operator explanation of the capability and trust boundary, begin with
[`docs/team-guide.md`](../../docs/team-guide.md).

## Install and prerequisites

Install the optional PostgreSQL profile from a reviewed Mnemo release:

```bash
uv tool install 'mnemo-unified-context[team]==0.1.0a4'
```

Provision the PostgreSQL schema and non-owner runtime role using the existing team migration
process. The runtime PostgreSQL endpoint must present a certificate trusted by the host; Mnemo
always enables hostname verification and never offers a plaintext database option.

Verified team backups additionally require PostgreSQL 17.10 client tools (`pg_dump` and
`pg_restore`) on the operator host. Provision a dedicated login role that is a non-superuser with
`BYPASSRLS` and `pg_read_all_data`; never reuse the MCP runtime role. This infrastructure role is
required so one exported snapshot contains every tenant while the online runtime remains subject
to forced RLS. Keep its password in a separate owner-only mode-`0600` file.

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
- `MNEMO_TEAM_RATE_LIMIT_REQUESTS` — per-principal/workspace requests per window, default `120`
- `MNEMO_TEAM_RATE_LIMIT_WINDOW_SECONDS` — fixed-window duration, default `60`
- `MNEMO_TEAM_RATE_LIMIT_IDENTITIES` — in-process tracked identity cap, default `10000`
- `MNEMO_TEAM_DB_POOL_SIZE` — bounded process-local PostgreSQL connections, default `16`, maximum
  `64`

Backup administration uses the same host, port, and database variables plus:

- `MNEMO_TEAM_BACKUP_DB_USER` — the dedicated non-superuser `BYPASSRLS` role
- `MNEMO_TEAM_BACKUP_DB_PASSWORD_FILE` — absolute owner-only password-file path
- `MNEMO_TEAM_BACKUP_SSL_ROOT_CERT_FILE` — optional absolute PEM CA path; system trust otherwise

Start the installed entry point under a process supervisor:

```bash
mnemo-memory-team
```

Mnemo always listens on `127.0.0.1`; there is no environment override. A configuration or
connection failure emits only a stable `MNEMO_TEAM_*` code.

The service applies the fixed-window rate limit only after OAuth subject and explicit workspace
validation and before constructing a repository. Buckets are isolated by exact principal and
workspace, concurrent calls are atomic, expired identities are reclaimed, and state cannot exceed
the configured identity cap. Denial returns `MNEMO_RATE_LIMITED` without touching PostgreSQL.
This limiter is intentionally process-local: use exactly one Mnemo service process for this
guarantee. A later declared multi-process profile requires a shared Mnemo-owned counter; do not
assume a reverse proxy makes these application buckets global.

## PostgreSQL connection capacity and load gate

The single Mnemo process lazily opens at most `MNEMO_TEAM_DB_POOL_SIZE` physical PostgreSQL
connections. All repositories used by one tool call share one checked-out connection while keeping
their existing transaction boundaries. Commit or rollback clears transaction-local authorization
settings before reuse; a connection that cannot roll back is discarded. Choose a pool size within
the database connection budget after reserving capacity for migrations, backup/restore, operations
checks, and administration. Do not multiply the configured size across service processes; the
supported profile remains exactly one Mnemo process.

Before exposing a reviewed release, run the server-side reference gate:

```bash
npm run team-load:check
```

It requires zero errors, nearest-rank p95 at or below 250 ms, and at least 30 authenticated
`get_context` operations per second for the checked-in eight-client workload. See
`docs/team-load-slo.md` for the exact workload, accepted reference result, exclusions, and the
mandatory deployment-specific HTTPS/OAuth capacity run.

## Provision checkpoint storage quotas

Migration 0022 intentionally leaves every workspace fail-closed for new checkpoint writes. Before
enabling agents, connect as the trusted schema owner or migration administrator and provision
positive limits for the exact workspace. Do not grant the runtime role access to the quota table.

```sql
INSERT INTO mnemo_team.workspace_checkpoint_quotas (
    workspace_id,
    max_aggregate_count,
    max_revision_count,
    max_payload_bytes,
    updated_at
) VALUES (
    '00000000-0000-0000-0000-000000000000'::uuid,
    1000,
    10000,
    268435456,
    CURRENT_TIMESTAMP
)
ON CONFLICT (workspace_id) DO UPDATE SET
    max_aggregate_count = EXCLUDED.max_aggregate_count,
    max_revision_count = EXCLUDED.max_revision_count,
    max_payload_bytes = EXCLUDED.max_payload_bytes,
    updated_at = CURRENT_TIMESTAMP;
```

Replace the example UUID and choose limits from measured workspace demand and database capacity;
the values above are examples, not defaults. `max_payload_bytes` measures retained canonical JSONB
text for revision content plus evidence. Lowering a limit below current usage never deletes data;
it blocks further affected writes. Verify the configured limit and current usage as the same trusted
administrator before start:

```sql
SELECT quota.workspace_id,
       quota.max_aggregate_count,
       quota.max_revision_count,
       quota.max_payload_bytes,
       (SELECT count(*) FROM mnemo_team.checkpoint_aggregates AS aggregate
         WHERE aggregate.workspace_id = quota.workspace_id) AS aggregate_count,
       (SELECT count(*) FROM mnemo_team.checkpoint_revisions AS revision
         WHERE revision.workspace_id = quota.workspace_id) AS revision_count,
       (SELECT coalesce(sum(octet_length(revision.content_json::text)
                         + octet_length(revision.evidence_json::text)), 0)
          FROM mnemo_team.checkpoint_revisions AS revision
         WHERE revision.workspace_id = quota.workspace_id) AS payload_bytes
  FROM mnemo_team.workspace_checkpoint_quotas AS quota
 WHERE quota.workspace_id = '00000000-0000-0000-0000-000000000000'::uuid;
```

An absent or exceeded quota returns `MNEMO_QUOTA_EXCEEDED` without partial checkpoint, evidence,
lifecycle, or outbox state. Reads remain available so an operator can diagnose and recover by
raising the exact workspace limit or applying an authorized data lifecycle action.

## Provision daily model budgets

Migration 0023 leaves every optional team model task fail-closed until an administrator provisions
an exact workspace/task budget. Mnemo currently has one such task,
`episodic_candidate_extraction`. Connect as the trusted schema owner or migration administrator;
never grant the runtime role direct access to either model-budget table.

```sql
INSERT INTO mnemo_team.workspace_model_budgets (
    workspace_id,
    task_type,
    max_call_count,
    max_input_tokens,
    max_output_tokens,
    max_cost_microusd,
    updated_at
) VALUES (
    '00000000-0000-0000-0000-000000000000'::uuid,
    'episodic_candidate_extraction',
    1000,
    2000000,
    500000,
    5000000,
    CURRENT_TIMESTAMP
)
ON CONFLICT (workspace_id, task_type) DO UPDATE SET
    max_call_count = EXCLUDED.max_call_count,
    max_input_tokens = EXCLUDED.max_input_tokens,
    max_output_tokens = EXCLUDED.max_output_tokens,
    max_cost_microusd = EXCLUDED.max_cost_microusd,
    updated_at = CURRENT_TIMESTAMP;
```

Replace the example UUID and derive all four limits from the configured provider/model's reviewed
worst-case reservation and measured demand; these values are examples, not defaults. Monetary
limits use micro-US dollars, where 1,000,000 micro-USD equals USD 1. Mnemo atomically charges the
configured worst case before every provider attempt on the PostgreSQL-defined UTC day. A malformed
output retry is a second charged attempt, and failed or interrupted calls are not refunded. An
absent or exhausted budget prevents the provider request and returns a stable payload-free code.

## Operations status and alert checks

Use the same tightly controlled backup/operations environment to render the content-free aggregate
dashboard:

```bash
mnemo-memory-team-admin status \
  --quota-warning-percent 90 \
  --model-budget-warning-percent 90 \
  --pending-jobs 1000 \
  --pending-job-age-seconds 300 \
  --failed-jobs 0
```

The canonical JSON contains schema support, whole-team workspace/project/active-membership totals,
checkpoint-quota coverage and maximum utilization, current-UTC-day model-budget coverage and
maximum utilization, and durable outbox backlog, active/expired lease, failure, and
oldest-pending-age counters. It contains no tenant identity, path, job body, memory payload,
credential, or exception. `status` exits 0 after any valid snapshot and includes all active stable
`MNEMO_TEAM_*` alert codes.

For a scheduler, service supervisor, or monitoring agent, run the same thresholds with `check`:

```bash
mnemo-memory-team-admin check \
  --quota-warning-percent 90 \
  --model-budget-warning-percent 90 \
  --pending-jobs 1000 \
  --pending-job-age-seconds 300 \
  --failed-jobs 0
```

Exit 0 means no threshold is active, exit 1 means the emitted JSON contains an alert, and exit 2
means configuration, secret loading, or PostgreSQL was unavailable. Capture the JSON as sensitive
operations metadata even though it is content-free. Schedule the check at an interval shorter than
the oldest-job threshold and route only its exit status and closed alert codes to the operator's
notification system. Do not expose either command through the HTTPS proxy, and do not replace the
backup/operations credential with the forced-RLS MCP runtime credential.

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

## Verified backup and restore drill

Choose an absolute mode-`0700` directory on an encrypted volume and create a non-overwriting
backup:

```bash
mnemo-memory-team-admin backup --output-dir /srv/mnemo/backups
```

The command uses a repeatable-read exported snapshot and publishes one mode-`0600` PostgreSQL
custom archive plus one mode-`0600` canonical manifest. The manifest binds the archive SHA-256,
size, schema-ledger version, and sorted row count for every table in `mnemo_team`. It contains no
memory payload. Mnemo does not provide at-rest encryption or remote object storage in this issue;
volume encryption, access control, copying, retention, and off-host custody remain operator
responsibilities. Command failures return only stable codes and remove partial local artifacts.

For a drill, explicitly provision a separate database owned by the backup role and install the
exact required `vector` extension version before restore. It may contain the extension but must not
contain the `mnemo_team` schema. Never name the live database. Then run:

```bash
mnemo-memory-team-admin restore-drill \
  --manifest /srv/mnemo/backups/mnemo-team-v23-<timestamp>-<digest>.dump.json \
  --target-database mnemo_restore_drill
```

Mnemo verifies file ownership/modes, manifest identity, archive digest and structure, rejects the
live or a nonempty target, restores with `--single-transaction`, and succeeds only when the target
migration ledger and every table count match. The JSON result contains only backup identity,
target database, schema/table/row counts, and bounded duration. Drop the isolated drill database
after recording the result. A successful drill proves database recovery, not deletion propagation
into external copies.

After a canonical deletion or retention purge, reconcile every backup directory controlled by
Mnemo before considering the deletion complete:

```bash
mnemo-memory-team-admin prune-deleted --backup-dir /srv/mnemo/backups
```

Version-2 manifests record the exact count of each monotonic erasure ledger at snapshot time. The
command compares those counts with one current database inventory and removes every older archive
before its manifest, fsyncing the directory between steps. This makes interruption and exact retry
safe: an orphaned stale manifest is removed on retry, while a current backup is unchanged. Version-1
manifests are conservatively removed after any recorded erasure because they lack the exact
watermark. A regressed ledger, malformed or substituted manifest/archive, unsafe permission,
symlink, or more than the bounded directory entries fails closed before a valid archive is deleted.
The result contains only removed backup, file, and byte counts.

Run this command separately for every local directory under Mnemo's control. Mnemo cannot discover
or recall archives copied to another directory, object store, removable medium, or third party;
operators must propagate deletion through those systems under their own retention policy.

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
