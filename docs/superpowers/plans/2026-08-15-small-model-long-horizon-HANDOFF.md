# Next-session handoff prompt

Paste the block below into a fresh Claude Code session (run from the `mnemo-memory` repo root) to have the next agent implement the plan.

---

You are implementing the **Small-Model Long-Horizon** plan for the `mnemo-memory` project — a local-first memory plugin (MCP server) for coding agents. Goal: make Mnemo demonstrably help a *small local model* on long-horizon work — cheaper tokens, zero false memories, and higher task correctness via deterministic enforcement — and prove it on the evaluation harness.

**Start here, in order:**
1. This project uses Mnemo itself for memory. First call `get_context` (omit all scope IDs) to load the latest checkpoint and prior context — it summarizes the diagnosis and the design.
2. Read the plan: `docs/superpowers/plans/2026-08-15-small-model-long-horizon.md`. Before touching code, read its "Evidence base" links plus `docs/evaluations/memory-value-investigation.md` and `docs/adr/0048-experimental-live-semantic-handoffs.md`.
3. Implement task-by-task with **superpowers:executing-plans** (or subagent-driven-development). TDD every task (failing test → confirm fail → minimal code → confirm pass → commit). Branch off `main`; commit per task; **do not run the deploy/release sequence.**

**Hard constraints — do not violate (flag if a task seems to require it):**
- Mnemo NEVER proxies/wraps/re-runs the agent's model endpoint; it only provides context and is called as a tool/hook.
- No transcripts, prompts, tool bodies, or model reasoning are stored. All memory mutation is deterministic (compiler + SQLite patch); no model calls, no embeddings by default.
- New semantic features stay behind `experimental_semantic_memory_enabled` (default false). Keep the stable default unchanged.
- Retrieved records are `untrusted_evidence` and cannot authorize actions; any new tool output is a *consistency fact*, never approval.

**Order of work:**
- **Phase 0 (enablers)** then **Phase 1 (charter-safe token + false-memory fixes)** — implement fully and validate. These are the safe, high-confidence wins.
- **Phase 2 (deterministic verify + reconcile)** is the ONLY lever that moves task accuracy, and it is charter-stretch — **get explicit maintainer sign-off before starting, especially Task 2.2 (`reconcile`).**
- **Phase 3 (decomposition)** only if longer-horizon targets are in scope.

**Validation:**
- Unit-test the deterministic parts offline — render token counts, false-memory transport, verifier logic, and the zero-model-token deterministic-ceiling diagnostic — no model or authorization needed.
- Accuracy and model-token deltas need an **authorized live Ollama run** on the **capability ladder** in the plan's "Model selection" section: the weak anchor `qwen2.5-coder:7b` plus the recommended mid / strong / reasoning-tuned small models. Re-run the preregistered gate per model; expect `SD − SI` to widen with model capability.
- **Target eval machine is a 24 GB M4 MacBook Air.** On it the practical primary is **Qwen3-14B** (`qwen3:14b`) — the ~19 GB 30B primary and ~20 GB 32B ceiling do NOT fit comfortably (unified RAM + ~16 GB default Metal wired limit + long-context KV cache + fanless throttling). Run the 30B/32B rungs on a >=32 GB Mac (48 GB+ for long context), a discrete 24 GB GPU, or a hosted endpoint. See the plan's "Hardware fit" table.

**Honest expectation to hold:** Phase 0/1 are token/safety wins and are accuracy-neutral by design; only Phase 2 enforcement can move task correctness, and only on constraint-backed fields. The reasoning ceiling is real (the 7B scored 0/30 with near-perfect memory) — proving Mnemo's value likely depends on running the right model from the ladder, not the 7B.

**Before you stop or compact:** save a Mnemo checkpoint (short objective, current state, exact next action, verification performed, project-relative `evidence_files`).

---
