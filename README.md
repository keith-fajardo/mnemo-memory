# Mnemo Unified Memory

Mnemo is an independently owned, local-first unified context platform for coding agents.
The authoritative build plan is [revision 2](docs/implementation-plan.md), and implementation
progress is recorded in [the status log](docs/implementation-status.md).

## Prerequisites

- Python 3.12.11
- [uv](https://docs.astral.sh/uv/)
- Node.js 24.4.1 with npm 11.4.2

## Bootstrap

```bash
uv sync --locked
npm ci
```

## Verification

Run the complete local quality gate:

```bash
npm run check
```

Individual checks are also available:

```bash
npm run format:check
npm run lint
npm run typecheck
npm test
```

The product implementation is intentionally split across runnable applications in `apps/`,
reusable Python packages in `packages/`, external-system adapters in `connectors/`, and the
test layers in `tests/`. Product features have not started; Issue 1 establishes only the
repository and its verification tooling.

## Governance and contracts

- [Repository instructions](AGENTS.md)
- [Product ownership policy](docs/product-ownership-policy.md)
- [Third-party dependency register](docs/dependency-register.toml)
- [Product memory contract](docs/product-memory-contract.md)
- [Initial threat model](docs/threat-model.md)
- [Evaluation baseline](docs/evaluation-baseline.md)
- [Architecture decision records](docs/adr/README.md)
