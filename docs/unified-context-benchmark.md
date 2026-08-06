# Unified context benchmark

The unified benchmark measures how much deterministic context Mnemo needs to preserve one task
handoff together with authoritative dbt lineage in a cold fresh session.

Run it from the repository root:

```bash
npm run eval:unified-context -- --json
```

The command makes no network request, calls no model, needs no API key or warehouse, and does not
execute dbt. It emits stable JSON and exits nonzero if an acceptance gate fails.

## Current deterministic result

| Context | Estimated tokens |
| --- | ---: |
| Full synthetic prior transcript | 2,948 |
| Full synthetic dbt manifest | 3,431 |
| Mnemo checkpoint section | 453 |
| Mnemo structural facts | 927 |
| Mnemo provenance | 46 |
| Unified Mnemo packet | 1,426 |

The current fixture reports:

- **83.07%** historical-context reduction: 2,948 transcript tokens to the 499-token checkpoint
  packet including provenance;
- **72.98%** structural-context reduction: 3,431 manifest tokens to 927 selected structural
  tokens; and
- **77.65%** combined-context reduction: 6,379 transcript-plus-manifest tokens to the 1,426-token
  unified packet.

These values use `mnemo-character-heuristic-v1`, the deterministic estimate
`ceil(characters / 3)`. They are not provider tokenization, billing, latency, caching, or cost
measurements.

## Information-quality gates

The benchmark does not ask a model to generate an answer. It verifies that the information needed
for a later answer is present and correctly bounded. The fixture requires:

- 100% required historical-fact recall;
- 100% upstream and downstream dbt precision and recall;
- 100% transitive-impact precision and recall;
- complete provenance coverage;
- correct traversal depth and staleness labels;
- zero cross-scope leakage;
- checkpoint, structural, and total packet budgets; and
- the expected checkpoint and structural sections.

The dbt manifest is the authority for lineage. Mnemo does not ask a model to infer lineage from
SQL, Jinja, or prose.

## What the benchmark proves

It proves that this original synthetic fixture can retain its required handoff and dbt lineage
facts in a smaller, provenance-bearing context packet than replaying the full transcript and
manifest.

It does not prove that:

- every real project will have the same percentage reduction;
- a provider will bill the estimated token count;
- a model will necessarily produce a better answer;
- latency or monetary cost will improve by the same percentage; or
- the selected context replaces reading exact source before a code change.

Every real packet has its own content and hard budget. When information does not fit or cannot be
resolved safely, Mnemo records an omission instead of silently claiming completeness.

## Related evidence

- [Fresh-session resumption benchmark](fresh-session-resumption-benchmark.md)
- [dbt manifest intelligence](dbt-manifest-intelligence.md)
- [Practical user guide](user-guide.md)
- [Product memory contract](product-memory-contract.md)
