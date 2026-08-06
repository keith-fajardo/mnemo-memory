# Team PostgreSQL load objectives

## Declared release gate

Mnemo's current single-process team profile must meet all of these server-side context-execution
objectives:

- zero failed operations;
- nearest-rank p95 latency no greater than 250 ms; and
- aggregate throughput of at least 30 completed requests per second.

Run the checked-in gate from a reviewed checkout:

```bash
npm run team-load:check
```

The command provisions an isolated real PostgreSQL server, applies every migration, creates one
private workspace/project/task with one active checkpoint, performs eight warm-up requests, and
then runs 160 concurrent authenticated `get_context` operations with eight workers. Every measured
operation traverses the production authentication binding, team composition, authorization-first
context engine, storage repositories, transaction-local scope settings, forced RLS, and bounded
connection pool. The result is one canonical content-free JSON object; any operation error or
missed objective fails the command.

## Reference result

The accepted 2026-08-06 run used PostgreSQL 17.10 on local loopback with the pool capped at eight:

```json
{"concurrency":8,"duration_seconds":0.551269,"errors":0,"operation":"authenticated_team_get_context","operations":160,"p95_latency_ms":22.598,"schema_version":"1.0","slo":{"maximum_p95_latency_ms":250.0,"minimum_throughput_per_second":30.0},"throughput_per_second":290.239,"warmup_operations":8}
```

Three consecutive accepted runs produced zero errors; the worst observed p95 was 23.245 ms and the
lowest observed throughput was 290.239 requests per second.

This reference proves the checked-in server-side path meets its declared gate on that environment.
It is not a capacity promise for a different CPU, database, network, TLS terminator, tenant data
shape, or competing workload.

## Deployment capacity rule

Before production exposure, rerun the gate on the intended release and database class, then load
the deployed HTTPS/OAuth endpoint with representative authorized data. The deployment must meet
these objectives or stricter operator objectives without increasing the pool beyond the database's
allocated connection budget. Reserve connections for migrations, backup/restore, operations
checks, and database administration. Re-run after changing Mnemo, PostgreSQL, instance size,
network placement, pool size, or representative data volume.

The checked-in gate deliberately excludes network transit, TLS termination, JWT signature cost,
reverse-proxy behavior, and external rate limiting because it supplies an already verified access
token directly to the production team port. Those layers require the deployment-specific run; do
not add their time to this result or present this number as end-to-end latency.
