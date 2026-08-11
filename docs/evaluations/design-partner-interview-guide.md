# Mnemo design-partner interview guide

Use this guide only with informed participant consent. Record structured answers in the pilot
evidence schema; do not paste confidential transcripts, credentials, customer content, or source
code into the repository.

## Screening

1. How often does work continue across agent sessions, people, or model providers?
2. What is the longest normal task horizon: turns, days, and handoffs?
3. What context is repeatedly reconstructed, by whom, and at what cost?
4. Which mistakes arise from missing, stale, or incorrectly attributed context?

## Current behavior and severity

1. Walk through the most recent real recovery or handoff without naming customers or secrets.
2. How much time and model usage did recovery consume?
3. What happened when an old constraint, decision, or failure was missed?
4. Which existing workaround is used: full replay, summary, documents, issue tracker, or none?
5. Rate problem severity and frequency from 0–1 and provide observable evidence.

## Mnemo pilot hypothesis

1. Which exact workflows should Mnemo support, and which should it avoid?
2. What critical facts and authority boundaries must never be lost?
3. What baseline should the pilot beat?
4. Define task success, tolerated quality decline, token/cost target, and human-intervention target.
5. What data may be stored locally, and what must remain only as an external reference?

## Commercial evidence

1. Who owns the budget and what current cost could Mnemo replace?
2. Would the organization fund a time-bounded pilot? Record evidence, not enthusiasm.
3. What price or verified avoided cost would be acceptable?
4. What security, deployment, and procurement conditions block adoption?
5. What continued usage event would demonstrate retention after 30 and 90 days?

## Required close

Confirm the next action, owner, date, pilot success threshold, allowed telemetry, and permission to
retain anonymized aggregate evidence. A positive interview alone is not a design partner or pilot.
