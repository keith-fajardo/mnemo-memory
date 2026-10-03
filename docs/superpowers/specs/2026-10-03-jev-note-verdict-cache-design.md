# Jev note-verdict cache — design spec

- **Date:** 2026-10-03
- **Status:** approved by the maintainer in conversation; spec for review
- **Builds on:**
  - `2026-10-02-jev-hook-wiring-design.md` (the "hook spec")
  - ADR 0049
  - branch `fix/jev-hedged-connect` (hedged TCP connect, f3cd236)

## 1. Why

The first two live synthetic replays (`2026-10-03-replay-a`, `-replay-b`) passed only the `steps` and `tokens` gates and failed every decision gate. When Jev answered, its quality was strong. Too many answers arrived after the 0.8 s cap. Live probes found the cause:

- **The hook waits on the slowest of many requests.** It sends up to **17 requests per prompt**: one front-door request plus one filler request per note. The prompt therefore waits on the *slowest of 17* fresh HTTPS requests.
- **Each request has three independent tails:**
  - TCP SYN loss: about 10–15% of new connections wait a 1 s retransmit
  - TLS handshake: p90 about 440 ms at 17-way concurrency
  - Jev server time: p95 about 600 ms
- **So the slowest of 17 often exceeds 0.8 s** (cap-hit share 0.38 and 0.44 of prompts). The hedged connect removes only the first tail, and replay-b did not improve: its cap-hit share was 0.44.
- **The filler check does not depend on the prompt.** Since the 2026-10-01 redesign it judges each note *alone*, as task information or filler. A note's verdict is therefore a property of the note, so it can be computed once and reused.

**Goal:** each prompt sends at most **one** Jev request, the front door (none when no question is asked). Filler verdicts come from a local cache, filled in the background after the prompt.

## 2. Decisions (maintainer, 2026-10-03)

| Question | Answer |
|---|---|
| Direction | Cache note verdicts, rather than a warm-connection helper |
| When notes are judged | **After the prompt, in the background.** The hook never waits. A note with no verdict is kept |
| Front-door time limit | **Keep 0.8 s** (step-relative). Re-measure after this change, and change it only if the answered-share gate still fails |
| Replay | **Warm the cache first** by judging all seeded notes, then run the prompts |

## 3. Hook changes

- **At most one request per prompt.** The typed step's `ask_each` carries only the front-door request (memory need, complexity, tool need, skill). The per-note filler requests are removed from the prompt path. The cap is unchanged: it starts at the mode read, and is 0.8 s minus local time, never below 0.1 s.
- **Filler decisions come from the cache.**
  - The candidate set is unchanged from the hook spec §4.2 and later rulings: rendered notes only, fetchable IDs only, plus every existing exemption.
  - For each candidate, the hook looks up the verdict cache (§5).
  - A cached `p_filler ≥ FILLER_DROP_AT` (0.5) means drop in live mode, or "would drop" in shadow mode.
  - **Note (2026-10-03):** lowered to 0.5 after the live probe; headed notes score lower than bare text. Phase 1 calibrated 0.7 on bare note text, where synthetic filler scored 0.75-0.90. The hook and judge score the stored note text including its heading, where filler scored 0.54-0.76 and relevant notes about 0.00-0.01.
  - A missing, stale or unreadable verdict means **keep**.
  - Everything downstream is unchanged: the pinned re-render (`only_item_ids`), the per-note `lower_rank` omission, the fit rule, and "a changed route stays unfiltered".
- **Queueing.** Candidates with no usable verdict are appended to the judge queue (§4), as IDs only. This happens only when relevance mode is not `off`.
- **Spawning the judge.** If the queue is non-empty, the hook starts the background judge (§4) **after** it has built its output. The start is fire-and-forget: a detached process in a new session, with no waiting. Any failure to start is ignored, and the hook's output never depends on the judge.
- **Failure handling.** Every cache read, queue write and spawn sits inside the existing typed-step guard. Any failure means today's behaviour, which is keep.

> **Dated note (2026-10-03):** the spawn rule above says "if the queue is non-empty". The implemented rule is narrower, and the maintainer approved the deviation: the hook starts the judge only when the data route could actually send something AND this prompt queued a note. It is also never started under replay overrides, with relevance `off`, or with the master switch off. Under `synthetic_only` the real hook therefore never starts the judge, because every judge request would only be `data_route_blocked`. Queued notes wait until a route opens.

## 4. Background judge

- **Entry point.** A hidden CLI command, `mnemo-memory typed-decisions judge-notes --data-dir <d>`. It is started only by the hook.
- **Input.** It reads the queue file `typed-decision-judge-queue.json` from the data directory.
  - The queue holds only item IDs and the scope needed to read them: owner, workspace, project and session/task.
  - **No note text appears in the queue or on the command line.**
  - The judge re-reads each note's text from Mnemo's own store, using the same scoped lookups as `get_context item_ids`. That means the same exemptions, sensitivity checks and secret scan.
- **Guard.** It uses a guard built by the existing composition module with source `RUNTIME`. Under `synthetic_only` every request is `data_route_blocked`, so **no real note leaves the machine today**. Settings locks, the master switch and the daily token budget all apply unchanged.
- **Pacing:**
  - per-request deadline 5 s
  - at most 4 requests in flight (`ask_each` in batches, or a bounded pool)
  - at most 32 notes per run
  - a single instance, enforced with an exclusive lock file. A second start exits at once.
  - the judge exits when the queue is empty or the per-run limit is reached
- **Retries.** An unanswered note (timeout or unavailable) gets no verdict. Its attempt count for that content digest is recorded. After 3 failed attempts the note is not queued again until its content changes.
- **Silence.** The judge prints nothing and exits 0 on any failure, so a broken judge can never surface in the user's session.

## 5. Verdict cache

- **File and locking.** The cache is the file `typed-decision-note-verdicts.json` in the data directory. It is file-locked, written atomically, and capped at 1 MB.
- **Key:**
  - the item ID
  - the SHA-256 of the exact judged text (`note_text(...)`, 300 characters)
  - the pinned Jev model version
  - the filler question version, a constant bumped when the `NOTE_SUBSTANCE` wording changes
- **Value:**
  - `p_filler`, a float in [0, 1]
  - the judged-at UTC timestamp
  - the attempt count for failures
- **Content-free.** The cache holds no note text, prompt text or skill names. A test asserts this.
- **Freshness.** A verdict counts only if every key part matches and it is under 30 days old.
- **Size limit.** The cache keeps at most 5,000 entries, dropping the oldest first.
- **Unreadable cache.** A corrupt or unreadable cache is treated as empty: every note is kept, and the cache is rebuilt by later judging. It never denies service.

## 6. Telemetry and CLI

- **`typed_v1` additions.** Two new closed integer fields, each 0–16: `typed_notes_cached` (candidates with a usable verdict) and `typed_notes_queued`.
  - Existing semantics stay. `typed_notes_checked` becomes "candidates looked up in the cache".
  - `typed_notes_unanswered` stays 0 on the prompt path.
  - The `from_dict` key-set tier must accept old records.
- **`typed-decisions status`** adds the cache entry count, the queue length, and whether a judge is running (lock held).

## 7. Replay changes

- **Warm-up.** After seeding and before any prompt, the replay runs a priming pass that judges every seeded note. It uses the synthetic-source guard, the seeded-directory guard and synthetic provenance, as today, and writes verdicts into the seed's cache.
- **New sub-gate.** The priming answered-share must be at least 0.95: the share of seeded notes that received a verdict.
- **Typed arm.** The typed arm then runs prompts with the warm cache. The prompt path sends only the front-door request.
- **Filler gates.** These are unchanged: zero relevant notes dropped, and at least 90% of noise dropped. They are now scored from cached verdicts applied in the hook.
- **Latency and answered-share gates.** These now measure the single front-door request.
- **Background judge.** The replay never starts it during the prompt runs, because the cache is already warm. A test pins that the hook's spawn is suppressed under replay overrides.

## 8. Testing

All tests use fake transports; there is no live network.

- **Hook:**
  - one Jev request per prompt (count transport calls)
  - cached filler drops in live mode
  - shadow records the drop and changes no output
  - a missing or stale verdict means keep
  - candidates are queued
  - the spawn happens after the output is built, and never when relevance is off or the master switch is off
  - a spawn failure is ignored
- **Judge:**
  - reads text only via the scoped store
  - `synthetic_only` means zero transport calls
  - the lock prevents a second run
  - the 32-note limit and the 4-in-flight limit
  - the 3-attempt backoff
  - no text in the queue or cache files
  - silent exit on failure
- **Cache:**
  - the key mismatch cases: content, model and question version
  - the 30-day expiry and the 5,000-entry size limit
  - corrupt means empty
  - concurrent writers lose no update
- **Contracts.** Off and shadow stay byte-identical to `main` (the existing contract tests). The existing security tests pass.
- **Replay:**
  - the priming pass and its gate
  - the prompt path sends one request
  - the fake-transport run passes the gates

## 9. Out of scope

- A warm-connection helper.
- Changing the 0.8 s cap. That is decided after the next live replay.
- Judging notes at write time.
- Any real-traffic route or redaction. Real data stays blocked.
- The ADR 0049 promotion blockers not affected here: skill attachment behind the gate, the post-answer fetch outside the cap, and the shadow CLI view.

## 10. Docs

- **ADR 0049:** record the cache, the background judge and the replay-a/b evidence.
- **User guide:** the judge, and what `status` shows.
- **Phase-2 memory note:** update after the next live replay.
