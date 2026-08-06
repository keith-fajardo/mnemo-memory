# Implementation plan completion audit

Audited on 2026-08-06 against `docs/implementation-plan.md` revision 2 and the current candidate
worktree. The plan contains 98 explicit Milestone 1–9 build and exit-gate requirements. Evidence
proves 97 complete; the sole open requirement is the external independent team security review.
Current requirement completion is therefore 97/98, or 98.98%.

Repository status prose is supporting evidence, not the gate by itself. The current full gate
passes with 942 default tests, 26 real-PostgreSQL tests, schema/dependency/architecture/package
checks, and installed-workflow verification. The separate team load gate passes three consecutive
runs. Exact implementation checkpoints and narrower verification are recorded under the named
sections of `docs/implementation-status.md`.

Legend: **Pass** means current code plus the cited executable evidence proves the requirement;
**External pending** means repository work cannot truthfully supply the required independence.

## Milestone 1 — Repository, domain kernel, and minimal MCP path (12/12)

| ID | Requirement | Evidence | Result |
|---|---|---|---|
| M1-B1 | Monorepo, CI, formatting, typing, tests | Status Issues 1–2; `npm run check` | Pass |
| M1-B2 | Durable plan and status | This plan plus `docs/implementation-status.md`; schema/check scripts | Pass |
| M1-B3 | Repository instructions | Root `AGENTS.md`; architecture/dependency checks | Pass |
| M1-B4 | IDs, scopes, evidence, revisions, context packet | `tests/unit/test_domain.py`, `test_context_packet.py` | Pass |
| M1-B5 | SQLite migrations and repository ports | Status Issues 3–5; SQLite/contract tests | Pass |
| M1-B6 | Init/start/status/stop CLI | Status Issue 6; checkpoint runtime tests | Pass |
| M1-B7 | Stdio `get_context`/`save_checkpoint` | Status Issue 7; `test_mcp_server.py` | Pass |
| M1-B8 | Codex and Claude connection commands | Status Issues 8–9; connection tests | Pass |
| M1-E1 | Fresh-install MCP smoke | `scripts/verify_installed_mcp.py`; package gate | Pass |
| M1-E2 | Domain tests need no database/network | `test_domain.py`; default unit gate | Pass |
| M1-E3 | Migration and corrupt-config tests | SQLite migration and connection tests | Pass |
| M1-E4 | Startup failure leaves agents usable | Codex/Claude connection failure-isolation tests | Pass |

## Milestone 2 — Native agent integration and checkpoint proof (11/11)

| ID | Requirement | Evidence | Result |
|---|---|---|---|
| M2-B1 | Codex connection and continuity support | Status Issues 10–11; Codex connection tests | Pass |
| M2-B2 | Claude MCP/hooks without model proxying | Claude connection and cross-client tests | Pass |
| M2-B3 | Permitted task lifecycle capture only | Checkpoint runtime/MCP tests; threat model | Pass |
| M2-B4 | Create/retrieve/revise/complete/abandon | Checkpoint contract, application, durability tests | Pass |
| M2-B5 | Bounded provenance-bearing fresh packet | Context packet/application tests | Pass |
| M2-B6 | Failure isolation, timeouts, read-only defaults | MCP durability and connection tests | Pass |
| M2-E1 | Fresh Codex and Claude resume same task | `test_cross_client_resumption.py` | Pass |
| M2-E2 | Resume uses fewer tokens than transcript | Fresh-session resumption evaluation | Pass |
| M2-E3 | Checkpoint source and budget are explicit | Context packet schema and tests | Pass |
| M2-E4 | Agent model endpoint is unchanged | Connection contract/security tests | Pass |
| M2-E5 | Failure degrades to ordinary agent use | Hook/connection failure-isolation tests | Pass |

## Milestone 3 — dbt-native project intelligence (11/11)

| ID | Requirement | Evidence | Result |
|---|---|---|---|
| M3-B1 | Manifest/catalog/run-results ingestion | Status Issues 12 and 15A–15C; dbt parser tests | Pass |
| M3-B2 | Complete typed dbt resource/lineage model | Issues 15A–15M; dbt artifact tests | Pass |
| M3-B3 | Up/down/path/impact/selector/freshness/coverage | dbt application and supplemental tests | Pass |
| M3-B4 | Target/invocation/Git/worktree currentness | dbt supplemental and Git-state tests | Pass |
| M3-B5 | Changed-state and affected-node indexing | Issues 15J–15L; snapshot storage tests | Pass |
| M3-B6 | dbt facts and opt-in bounded code excerpt | Issue 15M; `test_dbt_code_excerpt.py` | Pass |
| M3-E1 | Golden lineage equals artifacts | dbt manifest/application contract tests | Pass |
| M3-E2 | Staleness never presented as current | dbt state and context tests | Pass |
| M3-E3 | Dependency queries reduce source tokens | Unified dbt/context evaluation | Pass |
| M3-E4 | Fresh task has checkpoint plus dbt impact | Unified-context benchmark | Pass |
| M3-E5 | No model computes authoritative lineage | Deterministic parser/service boundary; architecture gate | Pass |

## Milestone 4 — Canonical events and production episodic memory (11/11)

| ID | Requirement | Evidence | Result |
|---|---|---|---|
| M4-B1 | Append-only typed activity/decision/failure/outcome/checkpoint events | Issues 16A–16K; episodic-event tests | Pass |
| M4-B2 | Transactional outbox and idempotent jobs | Task-event/outbox repository and real-PG tests | Pass |
| M4-B3 | Deterministic secret controls before storage/embedding | Security corpus/policy tests | Pass |
| M4-B4 | Optional schema-bound extraction candidates | Candidate extraction tests; model-budget gate | Pass |
| M4-B5 | Approval and correction workflows | Candidate review and governance tests | Pass |
| M4-B6 | Revision/supersession/expiry/retention/export/deletion | Episodic lifecycle suites | Pass |
| M4-E1 | Replay produces identical active projection | Repository/reference/SQLite parity tests | Pass |
| M4-E2 | Duplicate delivery has no duplicate effect | Outbox/idempotency tests | Pass |
| M4-E3 | Secrets are neither embedded nor returned | `tests/security`; semantic-index tests | Pass |
| M4-E4 | Correction/export/retention/deletion pass | Dedicated episodic suites | Pass |
| M4-E5 | Active memory traces to permitted evidence | Candidate/governance/context tests | Pass |

## Milestone 5 — Unified context engine (12/12)

| ID | Requirement | Evidence | Result |
|---|---|---|---|
| M5-B1 | Deterministic query classification/retrieval plan | Status Issue 17; context-engine tests | Pass |
| M5-B2 | Authorization-first category candidates | Context policy and cross-scope tests | Pass |
| M5-B3 | Lexical/structured/vector/temporal/importance scoring | Issues 17A–17F; retrieval tests | Pass |
| M5-B4 | Explainable rank fusion | Context-engine ranking tests | Pass |
| M5-B5 | Conflict/dedup/diversity/hard budgets | Context-engine and packet tests | Pass |
| M5-B6 | Client-specific rendering without canonical mutation | MCP/cross-client tests | Pass |
| M5-B7 | Explain sources/ranks/exclusions/conflicts/staleness | Status Issue 17 explain-context slices; context/MCP tests | Pass |
| M5-E1 | Packet never exceeds budget | Context packet/engine tests | Pass |
| M5-E2 | Every included claim has provenance | Schema, context, and retrieval tests | Pass |
| M5-E3 | Cross-scope leakage remains zero | Security and real-PG suites | Pass |
| M5-E4 | Retrieval beats no-memory resumption | Resumption/unified evaluations | Pass |
| M5-E5 | Fewer tokens than transcript without material regression | Evaluation fixtures and benchmark | Pass |

## Milestone 6 — Personal knowledge and Obsidian (11/11)

| ID | Requirement | Evidence | Result |
|---|---|---|---|
| M6-B1 | Markdown filesystem/Obsidian connector | Personal knowledge status; Markdown tests | Pass |
| M6-B2 | Hash sync, stable IDs, rename, tombstones | Knowledge sync/repository tests | Pass |
| M6-B3 | Frontmatter/headings/links/backlinks/chunks/citations | Markdown/link tests | Pass |
| M6-B4 | FTS first; rebuildable local embeddings | Knowledge retrieval/semantic tests | Pass |
| M6-B5 | Note text remains untrusted evidence | Knowledge policy/security tests | Pass |
| M6-B6 | Correction, priority, conflict behavior | Knowledge application/policy tests | Pass |
| M6-E1 | Create/modify/rename/delete sync | `test_knowledge_sync.py` | Pass |
| M6-E2 | Results cite path and heading | Knowledge retrieval/context tests | Pass |
| M6-E3 | Malicious notes cannot override instructions | Knowledge policy/security tests | Pass |
| M6-E4 | Unchanged reindex makes zero embedding calls | Local semantic knowledge tests | Pass |
| M6-E5 | Knowledge joins checkpoint/dbt within budget | Knowledge/unified context tests | Pass |

## Milestone 7 — Procedural memory: skills and agents (10/10)

| ID | Requirement | Evidence | Result |
|---|---|---|---|
| M7-B1 | Skill/agent frontmatter schemas | Status Issue 19A; registry fixtures/tests | Pass |
| M7-B2 | Versioned scoped compatible trusted registry | Skill registry tests | Pass |
| M7-B3 | On-demand applicable discovery | Issue 19B; context tests | Pass |
| M7-B4 | Checked-in skills outrank memory | Registry/context policy tests | Pass |
| M7-B5 | MCP list/get skill support | Issue 19C; MCP tests | Pass |
| M7-B6 | Existing sources import without mutation | Registry import/fixture tests | Pass |
| M7-E1 | Only applicable skills enter packet | Skill/context tests | Pass |
| M7-E2 | Changed skills invalidate and retain history | Registry revision tests | Pass |
| M7-E3 | Preferences cannot override mandatory rule | Context conflict/skill tests | Pass |
| M7-E4 | Frontmatter imports without semantic loss | Procedural fixtures and tests | Pass |

## Milestone 8 — Settings, inspection, and packaging (10/10)

| ID | Requirement | Evidence | Result |
|---|---|---|---|
| M8-B1 | Onboarding and connection health | Issue 20A; installed MCP verifier | Pass |
| M8-B2 | Source/model/privacy/retention/budget settings | Issue 20B; personal settings tests | Pass |
| M8-B3 | Memory inspection/correction/pin/export/delete | Issues 20C–20F plus governance suites | Pass |
| M8-B4 | Index freshness/failures/retry controls | Issues 20K–20L; sync/status tests | Pass |
| M8-B5 | Signed artifacts and pre-upgrade backup | Issues 20G–20H/20M; upgrade tests/workflows | Pass |
| M8-B6 | One-command install/start/upgrade/uninstall/diagnostics | Issues 20A, 20H–20J; package tests | Pass |
| M8-E1 | Written non-developer install/connect | User guide; installed verifier | Pass |
| M8-E2 | Upgrade/rollback preserve data | Personal upgrade/backup tests | Pass |
| M8-E3 | Uninstall separates app/data removal | Personal uninstall tests | Pass |
| M8-E4 | Logs/diagnostics redact by default | Diagnostics and security tests | Pass |

## Milestone 9 — Team workspace and production hardening (9/10)

| ID | Requirement | Evidence | Result |
|---|---|---|---|
| M9-B1 | PostgreSQL/pgvector parity and personal import | Issues 21C–21E, 21R–21V; real-PG tests | Pass |
| M9-B2 | Membership/roles/visibility/RLS/audit | Issues 21A–21C; authorization and real-PG tests | Pass |
| M9-B3 | OAuth/TLS/secrets/backup/restore/deletion | Issues 21AA–21AE; security/backup drills | Pass |
| M9-B4 | Limits/quotas/model budgets/ops/runbooks | Issues 21AF–21AK; unit/real-PG/load gates | Pass |
| M9-B5 | Knowledge ownership/conflicts/source approval | Issue 21AC; governance tests | Pass |
| M9-E1 | Cross-tenant database/service suite passes | 26-test real-PG suite plus `tests/security` | Pass |
| M9-E2 | Restore/deletion drills meet objectives | Issues 21AD–21AE; backup integration suite | Pass |
| M9-E3 | Declared load SLO passes | Issue 21AK; `npm run team-load:check` | Pass |
| M9-E4 | Personal export imports with verified counts/hashes | Issues 21R–21V; import/export tests | Pass |
| M9-E5 | Independent review has no unresolved critical/high | Review package/checker ready; no independent artifact exists | **External pending** |

## Closure rule

The implementation is not 100% complete and the team profile is not general-availability while
M9-E5 is pending. After an independent reviewer supplies an accepted revision-pinned artifact,
rerun `npm run security-review:check`, confirm no security-relevant change followed the reviewed
candidate, rerun the complete and load gates, update this audit to 98/98, and only then close
Milestone 9 and the implementation plan.
