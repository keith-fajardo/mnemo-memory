# ADR 0049: Hosted typed classifier and cheap-model tier

- **Status:** proposed
- **Date:** 2026-09-30
- **Deciders:** Mnemo maintainer
- **Issue:** Jev typed-decision routing, phase 1
  (`docs/superpowers/specs/2026-09-30-jev-typed-decision-routing-design.md`)
- **Supersedes:** none. It amends ADR 0045's local-only routing. For typed decisions only, it
  supersedes the "make no hosted-provider call" clause of item 3 in
  `docs/local-first-token-efficiency-plan.md`.
- **Superseded by:** none

## Context

Mnemo makes many closed decisions with keyword rules and a small local word-count classifier
(ADR 0045, ADR 0046). Examples are whether a prompt needs stored memory, whether a snippet is
relevant, and whether an event is worth remembering. Local Ollama extraction can take up to 30
seconds, and nothing checks correctness before a candidate is approved.

TypeSafe's Jev is a hosted classifier. It returns one value from a closed list with a probability,
cannot generate text, and bills $0.042 per million input tokens with output free. One request
answers many questions about one text. The maintainer holds an API key.

On 2026-09-29, 11 synthetic calls to pinned `jev-1.13.0` (4 questions each) took 0.37–0.52 s on
fresh connections and 0.27–0.45 s on reused ones. Processing time dominated.

TypeSafe's standard terms allow retention:
- The Master Customer Agreement (2026-09-23) §4.1 permits storing Input and Output. Since
  2026-09-19, it also keeps a perpetual right to use Customer Data for telemetry, abuse
  monitoring and legal compliance.
- The Data Processing Agreement (2026-04-24) keeps data "as long as necessary".
- The Privacy Policy (2025-11-19) promises no training on Input.
- Zero data retention (ZDR) is offered only to enterprise customers through sales. Vercel's AI
  Gateway offers per-request ZDR under a negotiated clause.

Outside this decision: routing or proxying the coding agent's own model (ADR 0048).

## Decision

1. **One port.** Every closed decision is asked through `PromptClassifier`, with
   `CascadeCommittee` producing `light` or `heavy` (`packages/model_gateway/cascade_router.py`).
   Local rule axes and hosted axes may share one committee, and a local risk axis can veto a light
   verdict.
2. **One guard.** `GuardedTypedDecisionClassifier` runs data route, credential, request shape,
   secret scan (the original text, the bounded text actually sent, and every axis string — name,
   labels, instructions and criteria), sensitivity (only `normal`), size (512-character text;
   every axis string ≤ 400 characters), budget (`ModelTaskType.TYPED_DECISION`), deadline and
   label check, in that order. Each failure falls back to today's rules with one closed reason.
   The guard never raises from `ask`.
3. **Data route.** `TypedDecisionDataRoute` has one value, `synthetic_only`, and runtime text is
   refused before any network call. Adding a route requires all three of the following:
   - signed ZDR terms covering request logs, abuse-monitoring copies, backups and telemetry
     derived from content, obtained through Vercel or TypeSafe enterprise
   - redaction of names and identifiers before the guard
   - an amendment to this ADR that records those terms
4. **Model pinning.** Jev is pinned to `jev-1.13.0`. Thresholds are recorded with the model
   version, and a model change re-runs the phase-1 evaluation.
5. **Latency.** The per-prompt deadline is 600 ms. No per-prompt decision goes live until at most
   5% of at least 50 cold round trips exceed it.
6. **Defaults.** Settings default to off. The key comes only from `TYPESAFE_API_KEY`.
7. **Model output is an untrusted proposal.** Jev may filter or propose. It never approves,
   supersedes or deletes memory.
8. **Connector hardening.** The TypeSafe connector uses a private opener that refuses HTTP
   redirects, so the key is never forwarded to another host. It rejects choice answers whose
   probabilities do not name exactly the allowed labels or whose chosen label is not the most
   likely one (schema_invalid). It rejects API keys that are not printable ASCII without
   whitespace, so malformed keys cannot surface in HTTP-library error text.

## Alternatives considered

- **Keep local rules and Ollama only (status quo).** This sends nothing off the machine. It keeps
  the weak keyword routing, has no pre-approval correctness check, and keeps Ollama's up-to-30 s
  extraction. It stays the fallback everywhere.
- **Accept standard TypeSafe terms for real prompts.** This is faster to value, but it allows
  open-ended retention and perpetual rights over derived telemetry. It is rejected until ZDR
  exists.
- **Haiku structured output for every decision.** This is simpler, but it costs more per call and
  has no calibrated probability for gating. Haiku remains the planned light tier for writing
  (phase 2).
- **A long-lived warm-connection process.** It saves about 0.1 s per call and adds a new moving
  part. It is rejected on the measured latency split.
- **Defer.** This is viable, but phase 1 has no privacy cost and produces the measurements the
  decision needs.

## Consequences

- Positive: one boundary owns every hosted decision, and the fallback is always today's
  behavior. Phase 1 measures accuracy and latency without real data.
- Negative: there is a new vendor dependency on the hosted path, a per-prompt latency cost of up
  to 600 ms once live, and vendor terms that changed twice in the week after launch.
- Follow-up: phase 2 (ZDR route, redaction, Haiku connector, shadow runs on real traffic) and
  phase 3 (write-path decisions), each gated by the spec's evaluations. Before any runtime caller
  exists (phase 2): the adapter must enforce a total monotonic deadline (the socket timeout is
  per operation, and DNS is unbounded), abandoned calls must not block process exit, and the
  runtime composition must turn a malformed TYPESAFE_API_KEY into no_credential instead of
  raising.

## Security and privacy implications

- Assets: prompts, stored memory snippets and event summaries.
- Phase 1 sends only synthetic fixture text, and runtime text is blocked before any network call.
  The secret scan covers every string sent, including snippets embedded in question text.
  Non-`normal` sensitivity is never sent.
- The key never appears in settings, logs, telemetry, reports, `repr` or exception text.
  Telemetry and reports are content-free.
- Required tests: data-route blocking with zero transport calls, secret and sensitivity
  blocking, deadline, schema rejection, key redaction, and the architecture guards (only the
  runtime composition imports the connector; the policy modules import no network library).
- See the threat-model entry "Hosted typed-decision classifier data exposure".

## Token and cost implications

Jev requests do not enter the agent's context. The measured cost was about $0.00002 per
front-door request (485 input tokens). The phase-1 evaluation makes about 180 requests. Savings
claims wait for client-reported usage in phase 2; none are made from character estimates.

## Dependency and licensing implications

No package is added: the connector uses standard-library `urllib`. TypeSafe is a hosted service
under proprietary terms, not an installable component. The dependency register records only
installable components under approved open-source licenses, so the vendor terms and their
versions are recorded here and in the threat model instead.

## Reversal or migration strategy

Every setting defaults to off, and removing the settings fields is covered by the migrated
defaults. Deleting `connectors/typesafe` and the runtime composition leaves today's behavior.
Nothing persists provider output.

## Verification

- The unit, architecture and evaluation tests named in the phase-1 plan.
- `npm run check`.
- The maintainer-authorized phase-1 evaluation report, with every gate true, before any
  phase-2 work.

## References

- `docs/superpowers/specs/2026-09-30-jev-typed-decision-routing-design.md`
- `docs/superpowers/plans/2026-09-30-jev-typed-decision-routing-phase-1.md`
- `reports/TypeSafe Jev data retention.md` and `research_notes/TypeSafe Jev data retention/`
- TypeSafe API reference: https://docs.typesafe.ai/api.md
- TypeSafe legal index: https://docs.typesafe.ai/legal.md
- ADR 0043, ADR 0045, ADR 0046, ADR 0047, ADR 0048
