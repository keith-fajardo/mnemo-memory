# Deterministic fresh-session resumption benchmark

Issue 11A provides a local, model-free proof that a compact checkpoint retains the information
needed to resume one synthetic coding task more efficiently than replaying its whole prior
transcript. The fixture is original to Mnemo and contains no user data, credentials, external
repository content, or dbt artifacts.

Run it from the repository root:

```sh
npm run eval:resumption -- --json
```

The command makes no network request, requires no API key, calls no model, and emits stable JSON.
It exits nonzero if an acceptance gate fails. Without `--json`, it emits a concise comparison table
followed by the same JSON report.

## Conditions

All conditions receive the same fresh-session task prompt.

| Condition | Additional context |
| --- | --- |
| No memory | None. Historical resumption facts are expected to be unavailable. |
| Full transcript | The complete synthetic prior-session transcript, including stale discussion and noise. |
| Mnemo context | A canonical context packet with one explicit, evidenced checkpoint revision. |

The fixture transcript includes the objective, inspected files, decision rationale, a failed
approach, a superseded proposal, current accepted decision, verification state, remaining action,
and unrelated discussion. The golden facts identify their transcript evidence sections. The
checkpoint retains only the accepted task state and evidence; it never embeds the transcript or the
superseded decision as current.

## Token and quality accounting

`mnemo-character-heuristic-v1` deterministically estimates cold input as `ceil(characters / 3)`.
It is deliberately a reproducible fixture estimate, not a provider tokenizer or billed-token
measurement. Cached tokens are excluded because every condition models a cold fresh session.

The report separates common prompt, full transcript, checkpoint content, provenance, context-packet,
and total contextual input estimates. It calculates:

```text
context savings % = (full transcript context - Mnemo context) / full transcript context × 100
```

It also reports total-input savings including the shared prompt. The current fixture gate requires:

- checkpoint content at or below 600 tokens;
- packet at or below the 5,700-token hard limit;
- 100% required-fact recall and provenance coverage for Mnemo;
- the current decision and expected next action present;
- the evidence-backed correction lesson present;
- no forbidden superseded decision presented as current; and
- at least 50% fewer contextual tokens than full transcript replay.

The scorer uses exact, structured fixture markers and packet content. It measures information
availability, not a model answer’s quality. Provider-specific quality, latency, cached-token, and
cost baselines remain future work; this fixture does not claim model behavior.

The 499-token Mnemo number is not a continuous background charge. It is the estimated cold input
size when a client asks for this fixture's checkpoint context. A normal turn that does not request
Mnemo context adds none of those tokens, and every real packet has its own bounded estimate.

## Boundaries

This fixture isolates task-handoff resumption. The separate
[unified-context benchmark](unified-context-benchmark.md) measures a checkpoint combined with
authoritative dbt lineage. The released product also supports automatic lifecycle reminders,
rebuildable source-structure projections, bounded project knowledge, and optional local semantic
note search; none of those features changes this fixture's checkpoint-only comparison.

Mnemo still performs no automatic transcript ingestion or model-based extraction of checkpoint
facts. A connected agent explicitly saves the evidence-backed handoff. The benchmark does not
measure provider-specific answer quality, latency, cache behavior, or cost.

## Cross-client transport proof

The cross-client evaluator runs the same fixture through real isolated Codex CLI and Claude Code
registrations. The latest completion audit recorded Codex CLI `0.146.0` and Claude Code `2.1.221`.
Reproduce it with:

```sh
npm run eval:cross-client -- --json
```

The command creates temporary `CODEX_HOME`, `HOME`, project, launcher, and Mnemo data directories
(including spaces and Unicode), registers `mnemo-memory` through `mnemo connect codex` and
`mnemo connect claude-code`, then reads each stored launcher back before starting it. It never
uses a real client configuration, API key, login, interactive agent session, or model request.

It proves Codex-to-Claude and Claude-to-Codex retrieval of the same durable, evidenced checkpoint,
then alternates a revision from Claude back to Codex. The report normalizes runtime revision labels
and exposes launcher digests rather than machine-specific paths. It also checks no-memory fact
availability (0%) against returned Mnemo context (100% required-fact and provenance coverage),
scope non-disclosure, stale revision conflict recovery, missing-launcher failure, and corrupt-profile
failure without a database fallback. Existing restart tests cover abrupt MCP process termination;
the registered launchers are always restarted as fresh processes in this proof.

This is an MCP transport and information-retention proof, not a claim about a generated answer
during a live client outage. Provider-specific model-quality and cost studies remain separate,
explicit opt-in work.
