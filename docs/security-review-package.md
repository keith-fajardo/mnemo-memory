# Independent team security review package

## Acceptance boundary

Milestone 9 is not complete until a reviewer independent of the implementation authors reviews one
exact release-candidate revision and reports no unresolved critical or high finding. Repository
tests, automated analysis, maintainer self-review, and AI-generated review may support the reviewer
but do not satisfy independence.

The reviewer must have source access, be free to report blocking findings, identify their name and
organization, and explicitly attest independence. They must not be the author of the reviewed team
implementation or the sole person responsible for accepting its release risk.

## Candidate and required evidence

Freeze one 40-character Git revision before review. Any security-relevant change after that point
creates a new candidate and requires the affected review to be repeated. The reviewer should use:

- `AGENTS.md`, `docs/product-memory-contract.md`, and `docs/threat-model.md` as mandatory contracts;
- ADRs 0021 through 0044 for team storage, transport, operations, budgets, and connection decisions;
- `deploy/team/README.md` for the supported deployment boundary;
- PostgreSQL migrations and every `tests/security` test;
- `tests/integration/test_postgres_team_control_plane.py` and
  `tests/integration/test_postgres_team_backup.py` for real-database evidence; and
- `docs/team-load-slo.md` for the measured single-process capacity boundary.

At minimum the review must cover:

1. OAuth issuer/audience/scope validation, loopback binding, HTTPS proxy assumptions, secret files,
   content-free errors, and rate-limit identity binding.
2. Workspace/project/item authorization, transaction-local identity, forced RLS, pooled connection
   reuse, runtime-role privileges, audit integrity, and cross-tenant negative cases.
3. Checkpoint, approved-event, episodic, knowledge, dbt, source-structure, skill, and procedure
   retrieval/mutation scope before ranking or reconstruction.
4. Retention, correction, retraction, deletion, export/import anti-resurrection, outbox processing,
   backup reconciliation, restore drills, and forward-only migration recovery.
5. Checkpoint quotas, model-call budgets, operations metadata, pool capacity, denial behavior, and
   concurrency races.
6. Dependency provenance, clean-room boundaries, prohibited secret persistence/logging, deployment
   least privilege, and documented residual risks.

Run at least:

```bash
uv sync --locked
npm ci --ignore-scripts
npm run check
npm run team-load:check
uv run pytest -q tests/security
```

The reviewer may add adversarial tests or tools, but external tool output must not contain tenant
payloads or secrets and does not replace manual design review.

## Severity and disposition

- Critical: practical broad tenant isolation, authentication, secret, deletion, or supply-chain
  compromise with severe impact.
- High: practical unauthorized data access/mutation, durable secret exposure, release-wide denial,
  backup/restore compromise, or bypass of a mandatory policy boundary.
- Medium: material weakness requiring non-urgent hardening with existing containment.
- Low: limited defense-in-depth or operational weakness.
- Info: observation with no direct security impact.

Critical and high findings must be `resolved` and reverified on the exact candidate. `open` and
`accepted_risk` are release-blocking for those severities. Lower findings may remain open only when
their residual risk and owner are documented outside sensitive payloads.

## Review artifact

Copy `docs/security-reviews/team-v1.example.toml` to
`docs/security-reviews/team-v1.toml`, replace every placeholder, and list every finding. The
reviewer or review owner then runs:

```bash
npm run security-review:check -- \
  --review-file docs/security-reviews/team-v1.toml \
  --expected-revision <40-hex-candidate-revision>
```

Exit 0 emits only scope, revision, and aggregate finding counts. Exit 1 means an unresolved
critical/high finding; exit 2 means the artifact is absent, malformed, not independent, or pinned
to another revision. Commit the accepted artifact with reviewer provenance. Do not mark Milestone 9
complete if the candidate changes afterward except for the review artifact and status-only closure.
