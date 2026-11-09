# Design notes

A running log of decisions and trade-offs, newest phase last.

## Phase 1: models, source layer, sync

### Package layout
The brief asked for `packages/core`, `packages/sources`, and so on. Hatchling can't do an editable install that maps `packages/` → `tenor/` (see pfmoore/editables#20). Generic top-level names like `core` would also collide with other installed packages. So modules live at `packages/tenor/<module>` and are imported as `tenor.<module>` (the package was called `concentrate` until 2026-11-05). Each module still has its own directory, as the brief laid out.

### Two model layers
- `tenor.core.granola` holds **wire models** that mirror the OpenAPI schemas field for field, including camelCase `hasMore` and British `organiser`. They use `extra="ignore"` so additive API changes (six minor versions in 2026) can't break sync.
- `tenor.core.models` holds **normalized models** (`MeetingNote`, `Utterance`, `Channel`). Everything downstream uses only these, so it has no idea whether data came from the API or from fixtures.
- The store keeps the **raw wire JSON** of every note. Normalization can change without a re-sync, and nothing Granola gave us is lost.

### Utterance identity
The API has no utterance IDs, but citations and gold evidence need stable ones. We use `{note_id}:{index:04d}` in API order. This is only stable if Granola returns transcript items in the same order every time (ASSUMED, see SCHEMA_NOTES). If a transcript were ever re-processed server-side, IDs could shift. A content-hash ID would survive reordering but not text edits. Index is simpler and readable, and the trade-off is recorded here.

### Channel attribution
`attribution` (`me`/`them`) wins when present; otherwise `source` decides: `microphone` → Me, `speaker` → Them. We don't infer speaker names. That is the job of the enhancement layer (Phase 4) and the whole point of the "which Sam?" problem. In-person meetings put everyone on the mic, so they come out as all "Me". The sample fixture includes one of these on purpose.

### Rate limiting
Granola documents "burst 25 / 5 s window" and "sustained 5 req/s". A token bucket alone (capacity 25, 5/s) allows ~50 requests in the first 5 s, which a strict window would reject. We enforce **both** a token bucket and a 25-per-5-s sliding-window log. The window is the binding constraint, and it already implies 5 req/s long-run. Time is injected (`Clock`), and the tests run on a `FakeClock` against a fake server that enforces its own limit. The test asserts **zero** 429s across 62 requests.

On 429 we honour `Retry-After` if sent (undocumented), else use full-jitter exponential backoff. We also drain the local bucket so the next requests don't stampede.

Bug found and fixed while testing: a residual wait smaller than the clock's float resolution made `acquire` spin forever. Time comparisons now use an epsilon.

### Transcript fetching
We try `GET /v1/notes/{id}?include=transcript` first: one request per note, which matters at 5 req/s. On `413 TRANSCRIPT_TOO_LARGE` we refetch the note without the transcript and page `GET /v1/notes/{id}/transcript` at 100 per page. Always using the paginated endpoint would cost at least 2 requests per note.

### Sync
- **Watermark = max server `updated_at` observed**, never our wall clock, so clock skew can't skip notes.
- **10-minute overlap re-scan.** This absorbs same-timestamp updates and the undocumented inclusive/exclusive semantics of `updated_after`. Notes whose `updated_at` matches what we store are skipped without a Get Note call.
- **Pending queue.** A note that 404s after retries is recorded in `pending_notes` and retried on every later sync, independent of the watermark. That covers the List→Get race window.
- **Soft deletes.** If `deleted_at` is set we keep the row (for audit) and drop the transcript. That fits Granola's direction of not hoarding verbatim content.
- **Open risk:** if `updated_at` doesn't move when a note finishes processing, a note whose processing outlasts the overlap window could be missed. `sync --full` is the backstop.

### Why we still poll even though webhooks exist
The brief said there are no webhooks, but the docs show they exist (CORRECTED in SCHEMA_NOTES). We still poll in Phase 1. Webhooks need a public HTTPS endpoint and a Business/Enterprise workspace, and their payloads carry no content (`{event_id, event_type, note_id, occurred_at}`), so a webhook would only trigger the same Get Note call. A `webhooks` receiver that verifies Standard Webhooks signatures and enqueues `note_id`s into the same `fetch()` path is a small, well-defined follow-up. The poller stays as the reconciliation loop either way.

### Secrets
The key is read from `GRANOLA_API_KEY`, placed only in the httpx client's default headers, and never stored on any attribute that `repr` shows. Errors carry status and path only. httpx's own INFO logging is silenced by the CLI. A test captures DEBUG logs during 429/retry flows and asserts the key never appears.

### Testing approach
There is no network in tests. `tests/fake_granola.py` is a small in-memory Granola that covers List/Get/Transcript/Folders, cursor pagination, page-size validation, auth, server-side rate limiting and injectable failures. The strongest test is `test_api_and_fixture_sources_produce_identical_stores`: syncing the same data through `GranolaAPISource` (over the fake HTTP server) and through `FixtureSource` produces byte-identical normalized notes.

## Phase 2: synthetic company and benchmark questions

### Ground truth first, text second
`synth.scenario.build_world(seed)` builds the world *before* any text exists. A meeting is a list of **beats** (one clean speaker turn each). Every fact and commitment is registered against the exact beats that state it, so gold evidence comes from construction rather than from string-searching the output afterwards. Each beat's `protect` spans hold the words that carry a fact (e.g. "twenty five dollars"). The builder refuses to register a fact unless its spans are protected in the beats it cites.

### What is authored and what is seeded
The **plot** is hand-authored, so every difficulty is deliberate:
- the price changes from $20 to $25, with a reason;
- the launch slips twice (Oct 14 → Oct 28 → Nov 11), each time with a reason;
- Falcon is handed from Sam Patel to Tomás;
- both Sams appear in one meeting;
- the internal project Beacon sits alongside the customer Beacon Freight;
- some customer requests are resolved, some are open, and one is never answered;
- private health, family and compensation content appears in 1:1s and one customer call.

The **seed** controls everything else: which engineers commit to which tasks in standups, what happens to each commitment (kept 45%, slipped 20%, dropped 20%, reassigned 15%, resolved in later standups), small talk, filler placement, start-time jitter, noise and the dev/test split. A fully procedural plot was rejected. It would make the planted traps accidental and much harder to explain to a reader.

### Padding
Without padding, scripted beats average about 7 turns per meeting, which is unrealistically clean. `synth.pad` inserts fact-free filler to reach about 40–70 turns per meeting depending on the meeting kind. Filler is never inserted inside an evidence group, so a question like "Megan, what's on your plate?" stays directly before its answer, and all evidence indices are remapped afterwards.

**Honest limitation:** the template-only corpus is about 20k words over 40 meetings (~500 words each). A real 30-minute meeting is closer to 4–5k words. A smaller haystack makes retrieval *easier* for the baselines, so any advantage of the archived Concentrate memory system measured here is conservative. `TARGET_TURNS` is the knob for a larger run.

### Hybrid rendering (LLM polish)
There is one LLM call per meeting (prompt `prompts/synth_polish.v1.md`, effort `low`). It rewrites every turn, and `polish.check_turn` accepts or rejects each rewritten turn independently:
- **Fact turns:** every protected span must survive verbatim. No new numbers (compared by value via ITN), names or dates.
- **Filler turns:** no numbers, dates, person names or commitment/decision language, and no entities other than those the meeting's fact turns mention.
- A rejected turn falls back to its template text.
- Acceptance and rejection counts by reason are written to `manifest.json`, so the dataset documents how much of it is LLM-written.

The test suite exercises this end to end with a fake LLM that deliberately breaks turns. Bad rewrites are rejected, validation still passes, and a second run served from cache reproduces the files byte for byte.

### Granola realism
- **Channels:** the owner's turns are `microphone`, everyone else on a call is `speaker`, and everyone in an in-person meeting is `microphone`. The in-person 1:1s are therefore all "Me", which is deliberately hard.
- **Diarization and names:** one in-person 1:1 is diarized (iOS). Investor meetings carry identified speaker names (ASSUMED, see SCHEMA_NOTES).
- **Crosstalk:** backchannels from the other channel overlap long turns.
- **Noise on unprotected text:** fillers, repetitions, false starts, name mishearing (Megan → Meghan), homophones and dropped short words. Rates are recorded in the manifest.
- **Summaries and typed notes:** `summary_markdown` is a lossy AI summary with 20% of points dropped, so the summaries-only baseline is honestly handicapped. Typed notes are sparse and terse.
- **Calendar:** one ad-hoc in-person meeting has no calendar event and no attendees.

### Questions (230 for seed 42)
All gold answers are computed from the world. The validator (`synth.questions.validate`) is mechanical: each answerable question's `evidence_keys` must occur in the text of its `gold_utterance_ids`, unanswerable questions must have no evidence, and each unanswerable question is guarded against the world so that a later plot change can't silently make it answerable.

This proves the answer is *stated* in the evidence, but not that a reader could *infer* it. For example, "who is 'Sam' here" still requires using the context cues. That inference gap is exactly what the benchmark measures. An LLM-judged entailment pass could be added; it is not used today, to keep validation free and deterministic.

Distribution (seed 42): single-hop 63, temporal 46, entity disambiguation 25, unanswerable 25 (11%), commitment status 22, latest state 17, multi-hop 12, decision change 10, aggregation 10. Private facts are excluded and reserved for the Phase 5 redaction eval. Split: 71 dev / 159 test, stratified by category. Only dev may be used for tuning.

### Determinism
The same seed gives byte-identical files (tested). A test also asserts that the committed `data/fixtures/northwind` equals a fresh `tenor synth --seed 42`, so fixtures can't drift from the generator. With `--polish`, the LLM cache is the source of truth, and `--offline` re-renders from cache with zero API calls.

### LLM client (`tenor.llm`)
- **Calls:** structured output via `output_config.format`, because the default model (`claude-opus-5-5`) rejects forced tool use.
- **Refusals:** server-side refusal fallback (`fallbacks: "default"`), with typed errors for refusal and truncation. Failed calls are never cached.
- **Cache:** a disk cache keyed by (model, prompt id, system, user, schema, max_tokens, effort).
- **Accounting:** a per-call ledger with per-stage totals. Unknown model pricing raises instead of silently reporting $0.
- **Cost estimate:** the pre-run estimate adds a 30% thinking-token allowance, because current models think adaptively and bill thinking as output. The real ratio will be measured on the first paid run.

### Fix found while building the UI prototype (2026-10-29)
The brief requires ASR name misspellings (Megan → Meghan), but the first fixtures contained **none**. Names almost only appeared in protected cue lines ("Megan, what's on your plate?"), and protected text is exempt from noise. Those cue names were never evidence keys (commitment evidence is the task phrase), so they are now unprotected. Fixtures were regenerated and validation still passes. The transcripts now contain realistic mishearings such as "Inez", "Thomas", "Lina" and "Coffee" (Kofi). The last one is outside edit distance 1, so the prototype linker misses it. That's a concrete case for Phase 4's phonetic matching.

## Note-view prototype (built ahead of Phase 6)
`web/` is Next.js 15 + TypeScript + Tailwind v4. It reads only Granola notes plus `directory.json`, never `world.json` (ground truth). Entity linking in the UI uses `web/lib/linker.ts`, a deliberately simple stand-in with these rules:
- full names resolve directly;
- a first name resolves when exactly one attendee has it, falling back to a unique directory match;
- edit-distance-1 matching catches ASR misspellings;
- glossary aliases and spoken ticket codes ("PAY two twelve" → PAY-212) expand to the project;
- the longest match wins, so "Beacon Freight" (customer) beats "Beacon" (project).

It uses no context, so speaker attribution ("which Them said this?") is untouched. Phase 4 replaces it with the Python enhancer, and the UI will render that output instead.

## Real data via Granola's MCP (2026-11-02)
- **Why:** the public REST API requires Business/Enterprise; the author's account is on Plus. The MCP works on every plan, so `GranolaMCPSource` is the realistic route to running Concentrate on real notes. The live probe (`docs/archive/PROBE_2026-11-02.md`) informed every mapping.
- **Same protocol, different wire variant.** The MCP returns UUIDs, not `not_` IDs. `McpNote`/`McpNoteSummary`/`McpFolder` subclass the API models and override only ID constraints, so normalization and the store are shared. The store records `raw_format` (`api`|`mcp`) so raw JSON reloads with the right model. Existing local `.db` files predate the column; delete them and re-sync.
- **Parsing agent-oriented text.** `list_meetings` and `get_meetings` return XML-like text meant for LLMs, not strict XML. The parser uses lenient regexes plus HTML unescaping, and is tested against fixtures copied from the live format with invented content.
- **Transcripts by chunk, not by character page.** Chunk reads return structured passages (speaker label, start time). Full reads return paged text. We read 10 chunks per call, pin the `transcript_version`, and derive `end_time` from the next passage's start.
- **Auth.** OAuth 2.0 with dynamic client registration via the official `mcp` SDK (v2.3): a browser sign-in once, a local callback on 127.0.0.1, and tokens in `~/.config/concentrate/granola-mcp.json` (mode 600, never logged). The live transport is a sync wrapper (anyio blocking portal) around one async session.
- **Read-only by construction.** The source calls only `get_account_info`, `list_meetings`, `get_meetings`, `get_meeting_transcript` and `list_meeting_folders`, even though the MCP now has write tools.

## Synthetic transcripts updated to the live format (2026-11-02)
The probe showed three differences between real transcripts and our fixtures, and the generator now models them:
1. **Partial speaker naming:** most remote meetings name some "Them" speakers, and named speakers are still unlabeled on some passages.
2. **Short, fragmentary passages:** median 6 words, with cuts never inside a fact phrase.
3. **Attendees with no display name.**

All three make attribution *easier* than our earlier Me/Them-only fixtures in some meetings, and harder in others (nameless attendees break name→email links, which is exactly the cross-meeting miss we saw live). Fixtures were regenerated, and all 230 gold answers still validate against the new utterance IDs.

### Recalibration on real data (2026-11-02)
Measured on the author's 4 MCP-synced meetings (aggregates only, recorded in `PROBE_2026-11-02.md` §6):
- naming raised to match the real 0.82 named share;
- remote turns are now often kept whole, with one-word lead-ins;
- in-person meetings merge into long single-microphone blocks.

The remote 38+ word monologue tail is a content limitation of scripted turns and is documented rather than faked.

## Phase 3: baselines and eval harness (2026-11-02)
- **Four baselines, all with the same structured answer** (`answer`, `abstain`, `citations`):
  - **B0 top-k:** the 20 most relevant meetings, with full notes and transcripts in context. Retrieval is hybrid over transcript chunks and summaries: BM25 plus `all-MiniLM-L6-v2` embeddings, fused with RRF and ranked at meeting level.
  - **B1 summaries-only:** the same retrieval, but AI summaries and typed notes only.
  - **B2 full-context:** every meeting in a **cached system prompt**. That's more context but the cheapest per question after the first call, so it's an honest ceiling.
  - **B3 agentic:** a tool-using agent whose four tools mirror the live MCP (`search_meetings` over titles, summaries and typed notes only; `get_meetings`; `search_transcripts` returning 240-character passages *without speakers*; `get_meeting_transcript` returning chunks with speakers and line IDs). It has a 12-turn budget, and exhausting it counts as a failed answer. This is the baseline that matters after the probe.
- **Deviation from the real MCP:** transcript chunks carry our line IDs so every system can cite at line level, which makes citation metrics comparable. Granola's MCP passages have timestamps instead.
- **Scoring:**
  - The primary signal is an LLM judge (`judge.v1`), counting "correct" only.
  - Shown alongside so judge bias is visible: exact match of gold normalized values (date, number and person aware), and citation precision/recall against gold line IDs.
  - Abstention precision/recall, tokens (cache reads included), cost, latency p50/p95, and tool calls.
  - Accuracy has 95% bootstrap CIs (seeded).
- **Reproducibility and cost:**
  - Every turn is disk-cached by its full request, and reruns report the *original* latency, not the cached ~0 s.
  - Reports (JSON, Markdown and per-question JSONL) record the git SHA and dirty flag, model, prompt versions, seed, split and spend.
  - `CONCENTRATE_BUDGET_USD` is a hard per-run cap, checked before each question, and partial reports say so.
  - Estimates are offline (heuristic tokens; B3 assumed), so a small pilot run should calibrate them before a full run.

### Batch mode (2026-11-02)
- `tenor eval --batch` (default) sends the single-turn systems (B0, B1, B2, B2o) and the judge through the Message Batches API at 50% of the price. B3 is multi-turn and stays interactive.
- **No second code path.** Each batch result is written to the exact cache file the interactive call would write (the custom_id is the 64-char cache key), then the normal code replays it. Single-turn systems expose `turn_request(q)`, and a test pins the pre-batch cache keys so old caches still hit.
- **Honest accounting.** Batch completions are flagged `batch`: the cost is recorded once at the batch rate, and replays are cache hits. Refused or truncated items are billed but not cached. Errored, expired, refused and truncated items fall back to the interactive call during the run, and the report counts them.
- **Honest latency.** The first `--latency-sample` questions (default 10) run fully interactively at full price, and they are the only latency source for batched systems. The report says how many rows each p50/p95 comes from.
- **Budget.** Each batch is estimated before it is submitted and refused if it would exceed the remaining cap. The estimate uses exact token counts when an API key is set, otherwise the heuristic ×1.93.
- **Differences from interactive.** The Batches API rejects the server-side `fallbacks` parameter, so a refusal in a batch is retried interactively, with the fallback. Prompt caching inside a batch is best-effort, so B2's cache-read assumption is optimistic in batch mode.

## Framing: benchmark first (2026-11-02)

**Decision (with the author):** the headline deliverable is the open meeting-memory benchmark and the head-to-head comparison. Concentrate is one entry in it, not the focus.

**Why:** Granola has publicly described prototyping agent-oriented "enhanced transcripts" (resolving which Sam, expanding project names), so Concentrate's techniques are unlikely to be new to them. What an outsider can add:
- an independent, reproducible measurement;
- a public benchmark on meetings (we found none; existing ones are chat logs);
- concrete live-probe findings;
- numbers on the transcript-retirement question they've said they're weighing.

**Trade-off:** the work hardly changes, but polish moves to the benchmark: a card ([`BENCHMARK.md`](../archive/northwind/DESIGN.md)), one command per system, and a documented way to add a system. Its biggest weakness, being synthetic, is stated up front.

## Hardening: timeouts, retries, stall watchdog, CI (2026-11-02)

**Trigger:** three ingest builds froze for 30+ minutes at 0% CPU. With no timeout set, the SDK
used its defaults (600 s read timeout), and our `max_retries=4` retried silently, so one dead
connection could block for up to 50 minutes. Full write-up: [`HARDENING.md`](HARDENING.md).

**Decisions:**
- **Own the retry loop** (`tenor.llm.resilience`) and turn SDK retries off.
  - Why: one policy for sync and async, every SDK entry point, Mem0 and Graphiti; logged at
    WARNING; testable with fake sleeps and clocks; and SDK and outer retries can never multiply.
  - Trade-off: we re-implement `Retry-After` handling, which the SDK already had.
- **Read timeout scales with `max_tokens`** (120–300 s), calibrated on measured latencies
  (p99 12 s, max 32 s). Trade-off: a legitimately very long non-streaming reply (more than
  ~9k tokens at the slowest rate seen) would time out. Streaming is the follow-up if that matters.
- **Stall watchdog, not just timeouts.** `ingest` and `eval` exit 75 after 15 minutes without a
  heartbeat and dump thread stacks; restarting is the recovery, because builds resume from
  `progress.jsonl`. This catches hangs that timeouts can't (deadlocks, library bugs).
- **Torn progress lines are repaired**, not fatal. Corruption in the middle of the file still
  raises.
- **CI** runs ruff, format, mypy and pytest with empty API keys. `*.md` is excluded from
  `ruff format`, because docs code blocks are hand-aligned sketches.

**Not fixed:**
- abandoned attempts may still be billed, and the ledger can't see it;
- `batches.create` can duplicate a batch on a lost reply;
- Mem0 and Graphiti have a one-chunk double-write window on kill.

## Report v2: paired significance, power, Pareto, failure taxonomy (2026-11-02)

`tenor analyze <report>` works post hoc on any saved report and costs nothing unless `--llm` is passed. Methods are in [`ANALYSIS.md`](../archive/northwind/ANALYSIS.md).
- **Paired, not overlapping CIs.** Every system answers the same questions, so pairs are compared with McNemar's exact test and a paired bootstrap CI of the gap. Holm–Bonferroni corrects across all pairs; each category is its own family.
- **Power from observed discordance.** Sample sizes use Connor's approximation with the observed share of questions on which two systems disagree. A Monte Carlo test confirms about 80% power. Trade-off: discordance from a small pilot is noisy, so the numbers are re-estimated on each report rather than fixed.
- **Ingest amortised at a stated volume.** The frontier is shown at several volumes, along with break-even volumes, because write-time vs query-time is a volume question.
- **Deterministic failure types first.** They come from ground truth: fact histories, the directory and gold lines. An optional Haiku classifier only refines the generic buckets, including a `judge_error` label. Trade-off: string matching misses paraphrases, so `stale_value` and `wrong_entity` are lower bounds.
- **Pilot finding (10 dev questions).** No pairwise gap is reliable after correction, and a typical pair would need about a 62-point gap at n = 10. At the pilot's discordance, 120 test questions detect gaps of about 11 points and 206 detect about 9 (re-check on the XL report).

## Concentrate as an MCP server (workstream D, 2026-11-02)

`tenor mcp-serve [--xl]` serves a built memory over stdio with six read-only tools (`ask_memory`, `get_facts`, `get_commitments`, `resolve_entity`, `get_meeting`, `get_transcript_lines`) and one resource (`concentrate://memory`). Details, client config and a demo script are in [`MCP_SERVER.md`](../archive/concentrate/MCP_SERVER.md).

- **Logic split from protocol.** `mcp_server/service.py` is plain Python over `MemoryStore`, `EntityDirectory` and `build_context`. `server.py` only registers tools (mcp 2.x `MCPServer`; FastMCP was renamed in v2). Tests drive the real protocol through the SDK's in-process client.
- **Read-only twice over.** The tools carry `readOnlyHint` annotations, and `MemoryStore(read_only=True)` opens SQLite with `mode=ro`, so a write fails at the database. The read-only connection is shared across the SDK's worker threads (`check_same_thread=False`), which is safe because the sqlite3 module serializes access.
- **Deterministic "now".** `as_of` defaults to the last meeting day in the memory, not the clock.
- **Ambiguity is an error, not a guess.** A name that matches several entities ("Sam") returns the candidates and asks the agent to choose. Unresolved commitment owners stay unresolved.
- **The agent answers by default.** `ask_memory` returns the context; Concentrate's own answer (`answer.v1`, the eval's answer model) costs one call and is opt-in via `answer: true`.
- **Build selection.** The server picks the newest *complete* build for the corpus hash; partial builds need `--allow-partial`.
- **Caveat:** Granola's MCP reads the user's real meetings, while this server serves synthetic Northwind, so "ask both" compares behaviour on a kind of question, not answers to the same fact. Serving a memory built from real notes is the open next step.

## Results explorer (workstream E, 2026-11-02)

`/results` in the web app reads eval reports straight from `evals/reports/` (no DB) and makes every number clickable down to the transcript line a system cited. Details: [`RESULTS_EXPLORER.md`](RESULTS_EXPLORER.md).

**Decision:** before colouring citations, the explorer recomputes each answer's citation precision/recall from the gold lines and only trusts a fixture version that reproduces the stored values. It reads fixtures at the report's git SHA when they're committed. **Why:** utterance ids are positional, and the pilot report shows the risk: against today's regenerated fixtures 18/36 answers don't reproduce; at the run's SHA all 36 do.

**Trade-off:** a `git show` per opened note (cheap, local), and gitignored fixtures (XL) can only be checked for drift, not pinned. **Found:** the runner's jsonl drops the judge's reason (the row's `verdict` string overwrites the judge's object); the explorer shows it once the runner keeps it.

## Pre-meeting brief (workstream H, 2026-11-02)

**Built:** `tenor brief <note_id>` prints a cited brief for an upcoming meeting: open commitments, what changed since the series last met, open customer requests, and the last meeting's digest. `tenor brief-eval` grades briefs against the generator's world. Details and results are in [`BRIEF.md`](../archive/concentrate/BRIEF.md).

**Decisions:**
- The brief is a deterministic **as-of query**: it reads only rows recorded before the meeting starts, so a historical brief has no leakage and every meeting is a test case.
- Gold lines come from protected spoken spans found in the fixture text, not from re-rendering, because the XL fixtures predate generator 1.1.
- The baseline gets top-24 chunks from earlier meetings, plus the previous series meeting's summary.

**Result (XL test sample of 36):** memory finds 91% of open commitments vs 30% for top-k + Sonnet, and 58% vs 5% of "changes since last met". It is less precise on commitments (43% vs 90%), mostly because of owner attribution in in-person meetings and owner promises the world doesn't track.

## Benchmark v2: realistic wording (generator `northwind-2`, 2026-11-02)

Details and metrics are in `docs/archive/northwind/FIDELITY_V2.md`.

**Decisions.**
- **Template-composed filler instead of an LLM rewrite.** Filler is composed from templates filled with meeting-specific vocabulary (`synth/filler.py`), so it costs $0 and the same seed gives byte-identical output. This brings exact ≥6-word duplicates from 78% down to 4.9% and TTR@2k from .11 to .21. The trade-off: the sentence frames still recur (54% of long passages share their first 6 words; real data: 0.6%). An LLM polish would fix that for an estimated ≈$28–55 and is left for the user to decide.
- **Numbers written as digits at render time** (`core.spoken.written`, 75% of turns), not in the world model. Facts, gold values and questions are unchanged. The gold validator accepts a key in either its spoken or written form.
- **Topic names in filler are protected spans.** They are realistic retrieval distractors, but ASR noise and passage cuts must not split "Beacon Freight". The enhancer's entity-trap test caught exactly this.
- **XL v2 goes to a new directory.** v1 results and built memories stay valid on v1, and the CLI refuses to overwrite a different generator version's XL.
- **Base set factor 0.35.** The committed fixture grows from 20k to 57k words rather than to the full ~150k, which keeps the repo and tests light. XL uses the real length.
- **Fixture-length-dependent test expectations re-pinned.** These are the batch cache keys and the Graphiti episode counts.

**Known gaps.** Template signatures, a short long tail (p90 26 vs 38), "like" density, and summaries at ~270 words vs ~420. See FIDELITY_V2 §4.

**Follow-up: stopping safely (same day).** A SIGTERM during a Graphiti write left Kuzu's WAL
torn, and every restart died natively replaying it (about $10.66 of work was lost recovering by
hand). So stopping is now two-phase:
- a stall or signal *asks* the run to stop between items, and the Graphiti store is
  checkpointed and closed;
- the watchdog only forces `os._exit` after a 6-minute grace, leaving a `FORCED_EXIT` marker.

An unclean store is test-opened in a child process before use, and fails with recovery steps
instead of a bus error. Resume reconciles `progress.jsonl` against the Episodic uuids in Kuzu,
and reports lost spend separately (`orphaned_cost_usd`).

Trade-off: a stall now takes up to 15 + 6 minutes to end instead of 15, in exchange for
closing the database cleanly whenever the main thread can still move.

## Concentrate tuning on XL dev (workstream L, 2026-11-02)
Query-side only, dev only; details, error analysis and ablations in `docs/archive/CONCENTRATE_TUNING.md`.
- **Decision:** fix lookup misses in `memory/query.py` (role-qualified names, named-meeting anchoring, list-wide commitment lookups) rather than re-extract. Dev accuracy 89% → 90% (within noise); gold lines in context 70% → 86%.
- **Ablations:** removing supersession/validity state costs 6 pts on dev, the largest effect; removing query-time entity linking costs 1 pt.
- **Trade-off:** the entail.v2 false rejects on commitment updates have a measured fix (owner-free update claims; 5 of 7 flip, Δ 0 on dev). It ships only with an explicit `entail.v3` rebuild, so the cached store and the code stay consistent.

## Transcript-retirement curve (workstream C, 2026-11-02)
- **Decision:** retirement is applied identically to every system (`evals/retirement.is_retired`:
  `day < as_of − N`). For b0, deleting a transcript removes its chunks from the index too (the
  meeting is still findable through its summary or notes), so b0's N = 0 limit is b1.
- **Decision:** "evidence in context" is split into gold line *text* shown vs gold line *ID*
  referenced, since Concentrate keeps IDs as provenance pointers after retirement.
- **Result (XL dev, $7.04):** Concentrate 89% at N = ∞ → 88% at N = 0, keeping ~5% of transcript
  tokens; summaries-only b1 scores 64%. b0's retired accuracy was not run (≈ $29 at N = ∞). The
  benchmark has no verbatim-quote questions, so the main expected loss is untested. See
  `docs/archive/northwind/RETIREMENT.md`.

## Report provenance: judge reasons, data version, per-system k (workstream R, 2026-11-02)

- **Judge reason kept.** Per-question JSONL rows used to merge `{**details, **asdict(row)}`,
  which overwrote the judge's `{verdict, reason}` with the row's verdict string, so the reason
  was lost. Rows now keep `verdict` as the string (what `failures.py`, `judge_check.py` and old
  readers expect) and add `judge` (the structured judge output) and `judge_reason` (the field
  the results explorer shows). Reports written before this have no reason; the explorer copes.
- **Data version pinned.** `meta.data` records `content_sha256`: a sha256 over sorted
  `(relative path, sha256(file))` for `notes/*.json`, `questions.jsonl` and `world.json`, plus
  the file count and `generator`/`scale`/`seed` from `manifest.json`. Paths are relative, so a
  copied or regenerated fixture set hashes the same; `manifest.json` isn't hashed, since it
  describes the data rather than being the data. This matters because XL fixtures are gitignored:
  a report can now be checked against the exact data later (`fixtures_version(path)`).
- **Per-system settings.** `meta.systems[name]` records `k` (b0/b1; `null` otherwise) and, for
  write-time systems with `config()` (c, b4, b5), the config and a 12-hex `config_sha256` (same
  JSON canonicalisation as their store paths, but a longer digest). The markdown header shows
  `Retrieval: b0_topk k=…`, the memory config hashes and the data hash. This makes the
  **pre-registered final XL setting visible: b0 top-k with k=10** (chosen for budget before any
  test results were seen; base runs keep the default k=20).
- These helpers can't crash report writing: a failing `config()` or unreadable fixtures is
  recorded as an `*_error` field, because the report is written only after the money is spent.

## Unit economics and scale curve (workstream F, 2026-11-02)

Details and results: [`ECONOMICS.md`](../archive/northwind/ECONOMICS.md).

- **Re-price measured tokens, don't estimate costs.** `tenor economics` reads the builds'
  `progress.jsonl` and an eval report's per-question rows, infers each build's model by
  re-pricing, and re-prices on any model. The only guesses are usage volumes (CLI flags).
- **Scale curve holds the questions fixed.** Questions are the dev questions answerable inside
  the 50% prefix; each size runs the subset answerable there. Comparing different question
  samples per size would confound haystack size with question difficulty. Cost: smaller n
  (only 21 dev questions are answerable in the first 25% of meetings, 8 of them unanswerable).
- **Concentrate by transaction-time rollback, not rebuild.** Ingestion is causal (each meeting
  sees only earlier ones), so the store as of a prefix is the full store with later rows deleted
  and their supersessions undone. A test checks rollback == building on the prefix.
- **Haiku for the curve.** Opus would cost ~$47, Sonnet ~$24 for the full design; Haiku ~$1.35.
  The curve measures a *trend* with model held constant; absolute accuracy is not comparable to
  the Opus headline numbers.

## Enhance v2: in-person attribution (workstream O, opt-in)

- `enhance-v2` adds two cues. **Task continuity**: an "I'll take X" or "X is done" line is credited to whoever said X on identified lines in other meetings. **Diarized owner prior**: the owner gets a stronger prior on the largest iOS diarization cluster. It is opt-in (`CONCENTRATE_ENHANCER`) so existing stores stay valid.
- Turn-taking and an HMM over the microphone run were measured and rejected *for this corpus*. Synthetic filler speakers are drawn i.i.d., so there is no structure to learn. That's a fidelity gap to close, not a verdict on the idea.
- With batch lookahead, v2 fixes 4 of the 8 owner-misattributed in-person commitments behind the brief's precision gap, adding no new errors. With history only (what the pipeline does today), it fixes none. Numbers: docs/archive/concentrate/enhance_v2.md.

## Benchmark v2.1 extras (workstream P, generator `northwind-2.1`, 2026-11-02)

Details: [BENCHMARK_V2_EXTRAS.md](../archive/northwind/V2_EXTRAS.md).
- **Additive and opt-in.** `--extras` appends 10 hand-authored meetings about one new project (Halyard) and 51 questions in seven new categories:
  `room_attribution`, `identity_resolution`, `correction`, `commitment_tracking`, `long_horizon`, and the transcript-only `exact_quote` and `speaker_attribution`.
  `questions.NEEDS_TRANSCRIPT` marks the categories the retirement curve should report separately.
  - **Why opt-in:** every v2 note, question and split stays byte-identical, so v2 scores remain comparable and the committed fixture and pinned test values don't churn.
  - **Trade-off:** extras live in one storyline with small n, so they are a diagnostic, not a leaderboard.
- **New ground truth: `World.extras`.** It records which facts are retractions (never true), which commitments were implicit, which were resolved out of band, and which meetings had a shared room channel.
  It is omitted from `world.json` when off, so v2 output is unchanged.
- **Identity over time: `roster.identity_on(pid, date)`.** Invites and labels use the identity a person had on the meeting date. The directory holds only the current identity.
- **Room labelling is ASSUMED** (SCHEMA_NOTES). The free probes (`synth/extras_checks.py`) confirm the intended difficulty:
  - label-only attribution scores 0/6 on room questions, against 2/5 for v2 "who said";
  - lexical retrieval still finds every room meeting.

## Workstream B: the real-data slice (2026-11-02)

- **Granola's real agent is called from Python** (`LiveMcpCaller`), not by hand from a chat session, so latencies are wall-clock times of single calls. Every call goes through `ReadOnlyCaller`, an allowlist of 7 read tools; a test checks that every write, folder, feedback and sign-out tool is refused. Answers are recorded once (`data/real/granola/`) and replayed, which makes the slice reproducible without re-querying Granola.
- **Same judge, different gold.** `judge.v1` grades every system against the gold answer (LLM-drafted, approved by the meeting owner; in the first run, approved in bulk with 0 edits). Citations are scored at the meeting level (real data has no gold lines). The report `.jsonl` keeps the runner's shape, so `judge-check` works unchanged.
- **LLM-drafted, human-verified questions.** Drafting makes 30 questions feasible in ~20 minutes of the user's time. The risk is a bias toward questions an LLM reading transcripts finds easy. Mitigation: the user can edit or reject each draft and write their own; rejected drafts are kept for audit.
- **Privacy by path.** Everything content-bearing lives in `data/real/` (gitignored), including a separate LLM cache and memory store. Only `*.aggregate.json` numbers are copied into docs. The data physically lives in the main checkout (worktrees symlink to it) so it survives worktree cleanup.
- **b0 uses k=1** on the 4-meeting corpus (k=20 would be full context; k=1 fits the budget, since one real meeting is ~18k tokens). **b3 was not run** (user's call, to stay under $5): the real agent replaces the re-implementation as the reference.
- **Same-data MCP comparison.** `real-kit serve` puts WS-D's read-only server in front of the real memory, so Granola's MCP and Concentrate's can be asked about the same meetings.
- **Known gap found on real data:** Concentrate's real directory comes from attendee metadata only, so a person who is only mentioned isn't an entity.
- **First real result (bad for Concentrate, reported as-is).** On 39 real questions, Granola's agent scored 92% [82, 100], b0 76% (k=1, n=37) and Concentrate 59% [44, 74]. Concentrate got 0 of 8 on "who said X", and 10 of its 16 misses were "not in the material". The cause: the write-time memory keeps ~43 facts for 4 meetings, and real questions target long-tail transcript details that our synthetic questions (generated from planted facts) never ask about. Concentrate led only on "what changed" (6/6). No tuning was done on this slice; the next step needs fresh real questions as a dev set. Full detail is in REAL_SLICE.md.

## Transcript fallback for Concentrate (workstream L, 2026-11-03)
Details: `docs/archive/concentrate/fallback.md`. Everything below is **dev**.
- **Decision:** a hybrid. Facts answer state and change questions; raw transcript passages fill the long tail.
  - `ConcentrateSystem(fallback="off"|"on_abstain"|"always")`, default `off`.
  - Passages use b0's chunking and the hybrid index, keep line IDs, respect as-of and retirement, and fit a fixed token budget.
  - Each line shows its raw channel label, with Enhance's speaker name marked as a guess.
- **Evidence (dev, partial, budget-capped):** real slice 12/22 → 20/22 with `on_abstain`, paired 8 wins / 0 losses (p = 0.008). On XL dev it fixed the one extraction miss it could reach (q248), with no visible harm. The fired XL answers are unjudged.
- **Trade-offs:**
  - Each fired question pays a second answer call (≈ $0.08 on real meetings).
  - The trigger also fires on some correct answers.
  - `on_abstain` can't catch confidently wrong answers (2 of 4 real who-said questions).
  - Speaker attribution in passages is still partly a guess.
- **Open:** confirm on fresh real test questions written after this design freeze.

## Black-box track: any meeting MCP (P1-B, 2026-11-03)
Details: `docs/method/BLACKBOX.md`.
- **One protocol, `Product.ask(question) -> ProductAnswer`** (text, cited IDs, latency, raw response, abstain flag if any). Adapters: Granola (`query_granola_meetings`), Concentrate's own server (`ask_memory`, over stdio), and a JSON spec for any other MCP. The kit (`real-kit run`) wraps a product in `ProductSystem`, so scoring reuses `run_slice` and `judge.v1` unchanged.
- **Least privilege, by construction.** Each adapter allowlists exactly its one question tool (the old kit allowed 7 for Granola; probes keep that list). `AllowlistCaller` refuses everything else before sending. For servers we can list, a preflight also refuses a tool annotated as not read-only. Unannotated tools pass the preflight, so the human checklist in BLACKBOX.md stays the real guard.
- **Questions are product-independent.** They are about the user's meetings, so `--product` appears where a product is involved (`status`, `run`, `spotcheck`), not on `draft`/`review`/`add`. Drafting from a product's own answers would bias the gold toward that product.
- **A real dev/test split.** `RealQuestion.split`, with one file per split (`questions.jsonl` = the 47 seen dev questions, `questions.test.jsonl` = hand-written, held out). `load_split` refuses a file holding the other split's rows. `real-kit import-test` parses the Q/A/KEY/KIND template. It skips fenced examples and blank blocks, stops at the `---` above the "already used" list, and re-imports idempotently.
- **Recordings are compatible.** Pre-black-box Granola recordings (`data/real/granola/`, raw only) replay through the new path. The offline smoke test reproduced the published Granola row exactly (n=39, 92%, p50 7.9 s, $0).
- **Committable reports are checked, not trusted.** `write_committable` rejects:
  - any per-question key;
  - meeting IDs;
  - strings over 120 characters;
  - any question, gold answer or answer text from the run.

  It refuses *before* writing. The check is a deny-list plus run-specific text, so it catches mistakes, not a determined leak (e.g. a short gold answer under 12 characters isn't matched).
- **Trade-off:** a product's answer is recorded once and replayed. That makes results reproducible, but freezes them at `asked_at`, so a product that improves needs a fresh recording directory to be re-measured.

## Figures pipeline (P1-F, 2026-11-03)

`tenor figures <stamp>` (`evals/figures.py`, style in `evals/figstyle.py`) renders the
eleven PLAN section-5 figures into `docs/figures/` as SVG; `docs/results/INDEX.md` lists them.

- **Determinism.** Agg backend, fixed `svg.hashsalt`, no `Date` metadata, constant `Creator`,
  text kept as `<text>` (`svg.fonttype = none`, no glyph outlines), mathtext off (so "$4/M" is a
  price). Same inputs on the same platform give byte-identical files (tested): that is the
  local guarantee.
- **The committed-figure check is platform-independent (revised 2026-11-03).** The first
  version compared SVG numbers with a 0.05 tolerance. It failed on GitHub's Linux: different
  text metrics move labels, legends and wrapped lines by more than noise. Now each figure has
  a sidecar `docs/figures/data/<name>.values.json` holding every plotted number and string,
  read back from the matplotlib objects in *data* coordinates (lines, error bars, points, bars,
  heatmap cells, ticks, limits, titles, labels, notes). It contains no pixel positions, so it
  doesn't depend on fonts, and `--check` compares it **exactly** (rounded to 10 significant
  digits). The SVG is compared only structurally: element counts by tag, plus its words in
  order with whitespace normalised (line wrapping depends on fonts). Tests show that a changed
  plotted number fails the check, and that a different font, size and DPI (all SVGs
  byte-different) still pass it. Trade-off: a purely cosmetic change, such as a moved label,
  is not caught across platforms. That's intended: the check guards that the charts match the
  data.
- **Real data never reaches CI.** Real-slice figures read `docs/figures/data/real_slice.json`, a
  content-free snapshot (accuracy, bootstrap CI, paired win/loss counts). `--refresh-real`
  rebuilds it from `data/real/reports/*.jsonl`, reading only `qid`, `system`, `category`,
  `correct`, `verdict` (a test checks that question/answer text never leaks). Every number in
  it was already public in REAL_SLICE.md and fallback.md.
- **Equal prices.** Dollar figures are re-priced from token counts at $4/M input and $20/M
  output for every system and every token, ingest included, with no cache or batch discounts.
  So top-k looks dearer than its billed cost (its 81k-token prompts were mostly cache reads).
  Chosen to compare token *use*, not deals. Ingest uses the original build's tokens.
- **Honesty choices.** Every figure carries its split (TEST/DEV), n and CI method on the image.
  The oracle is never on the cost frontier (it is told the right meetings). The hero's left
  panel uses b3 (our re-implementation) because the real Granola agent can only run on real
  data; the footnote says so. The real panel is labelled DEV (seen questions, bulk-approved
  gold). Real-slice top-k is restricted to the 39 questions the other two answered, so the
  comparison is paired. Scale-curve CIs are Wilson (only aggregates are saved for it).
- **Trade-offs.** The judge-agreement figure parses the judge-check Markdown (there is no JSON
  output yet); a format change there breaks it loudly in the test, not silently. The
  reliability diagram waits for X5 (no confidence field yet).

## P1-M: the measurement framework and the $0 metrics (2026-11-03)
Definitions: [`docs/method/METRICS.md`](../method/METRICS.md). Code: `evals/metrics.py`, wired into `tenor analyze`.
- **Decision: one price list for every $/question.** `analyze` and `economics` re-price each answer from its tokens at the interactive `--price-model` price (Opus 5.5 by default). The Batch API saving is a separate column, never mixed in.
  - Fixes red-team R1: recorded `cost_usd` mixed 50%-off batched rows with full-price rows.
  - `economics` additionally read that discount as ~50% prompt-cache hits.
  - Concentrate moves from $0.022 to $0.040/q, top-k from $0.177 to $0.328/q; the agent stays at $0.137.
- **Bug fixed: cached builds counted as free ingest.** A run that reuses a store records $0 ingest; `analyze` now uses the original build cost (`detail.built`). Concentrate ($4.96) and Mem0 ($17.02) were missing from the break-even table before.
- **Decision: quote break-even questions per corpus, not "dominated at 1,000".** The summary now says, e.g., "Concentrate pays back its ingest after 51 questions vs the agent". The oracle is excluded as a rival.
- **Decision: source recall is meeting-level and is printed with what `retrieved` means per system.** The agent counts meetings it opened (not search hits); Granola's agent counts meetings it cited. Mem0, Graphiti and summaries are marked as citing no lines; Granola's agent as having no abstain flag and unobservable tokens.
- **Finding (bad for the metric): source recall barely separates systems on synthetic data** (93–100% for all) and is trivially 100% on the 4-meeting real slice. Kept, because it rules out meeting-level retrieval as the main failure; finer (fact-level) recall would need gold memory IDs, which only Concentrate exposes.
- **Finding: on the real slice, Concentrate's loss is mostly declining.** 10 of its 16 wrong answers were abstentions on answerable questions (coverage 74%); when it answered it was right 79% of the time vs Granola's agent's 92% overall. Dev-quality data (n = 39).
- **Privacy: `analyze --aggregate-only`** writes counts and rates only (no question IDs, failure signals or failures file). Used for `evals/reports/real-*.analysis.*`.
- **Open:** partial credit uses an arbitrary 0.5 weight; latency for batched systems still rests on n = 10; calibration (X5) and faithfulness (X2) need runs.

## P2-T: notes-sufficient vs transcript-needed tags, confident error (2026-11-03)
Write-up: [`docs/archive/northwind/NOTES_VS_TRANSCRIPT.md`](../archive/northwind/NOTES_VS_TRANSCRIPT.md). Code: `evals/notes_tags.py`, `evals/by_tag.py`, `evals/notes_vs_transcript.py`; command `tenor notes-vs-transcript` ($0).
- **Decision: two deterministic taggers, not hand labels.**
  - The *generator reference* (did the planted summary bullet survive?) is the primary synthetic tag: it is exact by construction.
  - The *portable rule* (gold key values string-matched in the gold meetings' title, date, summary and typed notes) is the only option on real data. It is measured against the reference: v1 test 90%, κ 0.63.
  - A 20-question hand check on held-out test questions: reference 19/20, rule 16/20.
  - The rule was tuned on dev only. Its test-found misses (zero-padded dates such as "Dec 04"; "done" matching another item's Done line) are reported, not fixed.
- **Decision: a notes-only reader sees title + date + summary + typed notes.** Granola's notes carry the meeting date, so a date answer that is only the meeting date counts as notes-sufficient.
- **Decision: confident error = answered and judged *incorrect*.** Partials are excluded, and answering an unanswerable counts. It is a column of `analyze` (the 20261103T002456Z analysis was regenerated: only that column changed). It is an upper bound for flagless systems (Granola's agent).
- **Finding: notes-only loses almost everything on transcript-needed questions, and half of it confidently.** On synthetic test (n = 17), summaries score 0%, with 7/17 confidently wrong. Transcript readers score 76–82%, and Concentrate after transcript deletion (dev retirement runs) keeps 80%.
  - Caveats: the 0% is partly definitional; n is small; the transcript-needed share is a generator parameter.
- **Finding: real notes hold far less than synthetic ones.** 18 of 41 real answerable questions are transcript-needed by the (strict) rule, against 13% synthetic. Only counts per category are committed; real tags stay in memory.
- **Open:** a real notes-only run and a v2.1 run would give larger n and a direct real deletion measurement (both cost money).

## P2-D: the deletion simulation, built not run (2026-11-03)
Design: [`docs/tracks/RETENTION.md`](../tracks/RETENTION.md). Code: `evals/deletion_sim.py`, `tenor deletion-sim`.
- **Decision: a ladder of four conditions, each adding one layer.** A Granola's agent (black box) → B Granola's notes only → C + Concentrate facts/digests with every transcript deleted → D + the on-abstain transcript fallback. B, C and D share the answer model, prompt and judges, so C − B and D − C are clean; A − B also mixes in Granola's own model and prompt, and the doc says so.
- **Decision: B shows all notes, not a retrieval.** With 4 meetings (~4k tokens of notes) retrieval is moot, so B is an upper bound for a notes-only agent. It approximates Granola's degraded chat; it is not Granola's post-deletion behaviour, which we can't observe.
- **Decision: notes come from the synced `data/real.db`** (the same public-MCP `get_meetings` data), so the run makes no extra live Granola calls beyond `query_granola_meetings` for unrecorded A answers.
- **Decision: deletion is `retire_after_days = -1`.** `0` keeps the transcript of a meeting held on the as-of day; `-1` deletes every held meeting's. The retirement notice in the context is reworded to "are deleted" (no "older than -1 days").
- **Decision: D's passages ignore the deletion** (cold copy read on demand), unlike `c_on_abstain` with retirement. D's first call is byte-identical to C's, so it is free once C has run, and the quote counts it once.
- **Decision: confident error = judged incorrect and not hedged** (`says_not_covered`, which also reads Granola's prose). The fallback can't fire on a confident error by construction; the run reports that rate per condition so the blind spot is visible.
- **Decision: the quote counts inputs exactly** (`count_tokens` + the structured-output overhead measured on 36 cached answer and 146 cached judge calls: +255 / +246 tokens per call), prices cached requests at $0, and gives outputs as a range (recorded mean vs p90). Dev: $6.27 expected / $8.71 high; 30 test questions: $4.17 / $5.74.
- **Trade-offs / open:** the second judge runs after the primary pass (not interleaved), so a budget stop can skip it entirely (recorded as `judge2_skipped`). The tag format is a guess at P2-T's (JSON map or JSONL); P2-T may define the canonical confident-error metric, in which case this one should defer to it.
- **DEV result (2026-11-04, $6.42 of a $9 cap):** A 89%, B 57%, C 66%, D 87% (n = 47). B vs A 2–17 (p = 0.001); C vs B 4–0 (p = 0.13); D vs C 10–0 (p = 0.002). B and C never answered confidently wrong (all misses hedged). Notes-only scored just 57% on "notes-sufficient" questions, which is bad news for the tag rule or the answer model. Details: DELETION_SIM.md.
- **Bug fixed during the run:** the committable check refused the aggregate because the gold placeholder "UNANSWERABLE" matched the category name `unanswerable`. The CLI now leaves placeholder golds out of the forbidden-text list, and adds the run's answers to it.

## Notes faithfulness track (2026-11-05)
Write-up: [`docs/tracks/NOTES.md`](../tracks/NOTES.md). Code: `evals/faithfulness.py`, prompt `notes_audit.v1`, command `tenor notes-faithfulness --system granola|northwind-synth` ($0.40 total).
- **Decision: a product-neutral benchmark track, not a Granola audit** (Kian). Input is a `NotesSystem`, a set of (meeting, that system's notes markdown) pairs plus the account's meeting pool, built by a ~10-line adapter. Granola's AI notes are the first entry and the synthetic Northwind summaries a baseline; any notetaker or our own summariser plugs in the same way.
- **Decision: one claim per bullet or sentence.** Headings and lead-ins (all-bold items, items ending in ":") are labels, not claims; they are kept as each claim's section/context so an owner's name over a task list still reaches the checker.
- **Decision: one call per meeting, transcript as a cached system prompt.** `LLMRequest.cache_system` (new, opt-in; it enters the cache key only when set, so old keys are unchanged) marks the system prompt as a cache breakpoint, and `Completion` now records cache read/write tokens so cost is exact. With one call per meeting per model nothing is reused, so caching here costs the 1.25x write premium; it pays off on retries.
- **Decision: "supported" must cite a valid line**, or it is relabelled unsupported (never triggered).
- **Decision: a strict Sonnet pass over every claim**, added after the 30% re-check gave κ ≈ 0 on Granola (Haiku called 39/40 supported). The synthetic baseline then showed why: Haiku flags grounded-by-construction notes as `inferred` at the same rate as Granola's (7% vs 8%), Sonnet at 0% vs 14%. The strict column is the headline.
- **Decision: cross-meeting bleed is a deterministic heuristic with strong (name/number) and weak (rare word) flags**, reported against an all-claims baseline. In a 4-meeting pool "rare" is weak, so only strong flags are evidence.
- **Finding:** Granola 84% supported (strict), 14% inferred, 0 unsupported, 2% (3) possibly contradicted; action items and decisions go ungrounded most; no strong bleed flags. Synthetic 98% supported.
- **Finding: a generator bug.** 2 synthetic summary bullets drop the negation of a hedged opinion ("not convinced X is the blocker" becomes "X is the blocker"). Follow-up in `synth/`; fixtures not touched.
- **Bugs fixed during the run:** the aggregate key `not_supported` matched the note-ID pattern (`not_…`) of the committable check (renamed `ungrounded`); the name pre-check flagged hyphenated compounds; ordinals ("14th") weren't read as numbers.
- **Open:** the owner's hand verification of the 29-claim private spot-check list is the ground truth and has not happened yet.

## Notes grounding checker, validated on AMI (2026-11-05)
Write-up: [`docs/method/GROUNDING_CHECKER.md`](../method/GROUNDING_CHECKER.md). Code: `evals/grounding.py`, `evals/grounding_ami.py`, prompt `grounding.v1`, commands `tenor ground` and `tenor ground-ami` ($0.0445 spent).
- **Decision: a tool, not a leaderboard.** Per claim: verdict, cited `utterance_id`s, a verbatim quote. It reuses the faithfulness track's `split_claims`, `precheck`, labels and `nearest_lines` (now with optional IDF weights and stemming; `claims_message` factored out of `check_request`).
- **Decision: retrieval first, full transcript only on doubt.** Per claim, the top-8 IDF-weighted lines ±1 go to one excerpt call per meeting; claims with no candidates or not called `supported` go to one full-transcript call whose verdict is final. A retrieval miss costs a call, not a false flag.
- **Decision: the quote is copied, not generated** (the cited line with most shared words, 25 words max).
- **Decision: validate against AMI's human annotations, not a second judge.** Positives are AMI's summary sentences, with summary links as evidence gold; negatives are seeded rule-based edits (role/person swap, number, negation, same- and other-series injection), each checked in a call apart from its source sentence. IS1008a is DEV; the prompt and rules predate any verdict.
- **Parser:** `ami.corpus` now reads dialogue acts (word-id ranges, which can end on non-word events) and `summlink.xml`; `meeting_note()` turns an AMI meeting into a `MeetingNote` (one utterance per dialogue act, channel `them`, role as speaker name). IB4003 has no summary links.
- **Finding:** TEST edits flagged 89% [82, 94] (110); human sentences supported 78% (78), flagged 18%; evidence recall 92% (36). Decision sentences disagree most (9 of 16 flagged). On our unverified read, 5 of the 20 non-supported human sentences are fair flags (the summary is wrong), 6 judgement calls, 9 checker errors (names vs roles, strict "decided", lost pronoun context).
- **Open:** run on Granola/Wispr notes for the same AMI meetings once synced; a Sonnet column ($0.44–1.43 est.); give the checker name-to-role hints and the previous sentence as context.

## Wispr Flow as the second product (2026-11-04)

Built with fakes only; nothing has run live and no results exist. Write-up: [PRODUCTS.md](../method/HARNESS.md).

- **Same `Source` protocol, new wire variant.** `WisprMCPSource` maps Wispr's MCP onto the Granola wire models. `WisprNote` is the API `Note` with free-form IDs, stored as `raw_format = 'wispr'`. Sync, storage and every downstream reader work unchanged. The cost is a Granola-shaped vocabulary: Wispr's generated notes body lands in `private_notes_markdown`, and Me/Them is inferred from speaker names because Wispr has no capture channel. Both are ASSUMED rows in SCHEMA_NOTES.md.
- **Lenient parsing, every guess listed.** Tool names and arguments are verified from the published schemas. Response key names, the continuation-marker text and the transcript line format were not observed, so each is a single tolerant function (`split_range`, `parse_transcript`), easy to fix after the first real sync. We deliberately did not call the connected Wispr tools to look: that would have put real meetings in a session transcript.
- **Allowlist moved to `tenor.sources.allowlist`.** Sources now use it too, not only black-box products. `blackbox.base` re-exports it.
- **Pairing** is a hard ±10-minute gate plus a weighted score (transcript 3-gram containment 0.6, attendees 0.2, title 0.2), assigned greedily one-to-one, with manual overrides. We chose a transparent, deterministic heuristic over a learned matcher because a few dozen pairs are checked by hand anyway.
- **The outside agent** fixes model, prompt, budget and schema, and varies only the product's published read tools. Trade-off: tool *design* (call granularity, transcript search) is part of what's measured, by intent. Granola's own agent stays an extra row.
- **Open:** whether Wispr accepts dynamically registered OAuth clients (if not, the live sync can't sign in); Wispr has no updated-since filter (every sync re-lists, and edits may be missed without a modified time); wiring the outside agent into `real-kit run`.

## Evidence compression, $0 parts (2026-11-05)

Plan: [EVIDENCE_COMPRESSION.md](../method/EVIDENCE_COMPRESSION.md). Code: `ami/questions.py`, `evals/compression.py`, prompt `closed_book.v1`, commands `tenor ami questions` and `tenor compression`. Nothing paid has run.

- **Decision: questions by template, not by LLM.** Each annotator sentence is split at its first will/decided/discussed/agreed; the subject picks the template, the sentence is the gold. Pronouns take the previous subject. Trade-off: stilted wording and summary-level questions; in return, every gold answer is human-written and the set is reproducible from the seed.
- **Decision: who-said-it keys on AMI's participant code**, because NXT letters change between IS1008 sessions. Quotes are summary-linked acts of 10–40 words, distinct speakers first.
- **Decision: compressors take a meeting and nothing else** (`Compressor = Callable[[Material], KeptLayer]`); all layers are built before questions are loaded. Random lines use one seeded permutation per meeting, so budgets nest (5% ⊂ 10% ⊂ …).
- **Decision: verbatim share = words inside a 5-word run copied from the transcript**, line IDs and speaker labels stripped; short lines match whole. Extractive layers score 1.0 by construction; it matters for abstractive ones.
- **Reuse:** `answer.v1` via `agents.baselines._Base`, `judge.v1` via `judge_request_for`, `deletion_sim._rates/_paired/confident_error`, `agent_interface.bucket` (Granola's four answer types), `stats.paired_bootstrap` (H1 reads the lower bound of the paired CI vs full transcript).
- **Quote:** inputs counted per prompt part (free `count_tokens`); LLM methods priced at their budget's random-lines size; output from the deletion-sim calibration and cached Haiku judge calls. Real-meeting sizes are assumed (real data not read). $4.92 expected vs ~$3 planned.
- **Decision: sandbox first, one hard cap per run.** Paid runs need `--max-usd` (or `CONCENTRATE_MAX_USD`); reader and both judges share one ledger, so the cap covers the whole run, and a `BudgetExceeded` stops it cleanly with rows kept. `--pilot` runs 3 DEV questions × (full transcript, random 20%) under $0.10, second-judges all of them, prints the answers, and re-prices the full plan per row by measured ÷ quoted $/call × 1.5.
- **Decision: expand by whole series, seeded and site-balanced** (`select_meetings`, recorded in `data/ami_questions/meetings.json`) to 311 questions, each tagged `recorded` or `annotation_only`; summaries are reported per subset because stand-in notes are less realistic. Templates v2 map ES/TS annotators' role names, send clause subjects ("in june they") to the group, and cut who-questions at the first sentence (a second sentence often names the answer). Trade-off: the new 243 questions use AMI's abstract as notes, which flatters notes-only.
- **Decision: H4 breadth arms share one retriever and pack whole blocks.** `SharedRetriever` is BM25 only ($0, deterministic) over notes and 15-line transcript chunks, with meeting IDs masked from the query (with IDs it is an oracle). `pack` fills the budget in rank order and skips a block that doesn't fit, so a later, smaller one can fill the space; no truncation. Trade-off: who-said-it quotes make retrieval near-perfect (98% top-1), so H4 on those questions mostly tests whether one transcript fits.
- **Open:** swap in product notes (`with_notes`) and fill `notes_can_answer`; build the LLM compressors (§2) and the H2 sign test against the best method; H4 detail questions that don't quote the transcript.

## Anatomy of a wrong fact, $0 parts (2026-11-05)

Write-up: [WRONG_FACTS.md](../method/WRONG_FACTS.md). Code: `ami/facts.py`, `evals/wrong_facts.py`, command `tenor facts stage1|stage2|stage3`. Nothing paid has run.

- **Decision: stage 1 is deterministic.** Fact tokens (numbers, money/percent, names, roles) come from AMI's manual transcript by rule; substitutions come from n-gram anchoring plus a small word alignment per fact, not a full-meeting alignment (cheaper, and robust to products segmenting and ordering overlapping speech differently). Validated: AMI scored against itself gives 100% recall, 0 substitutions on all six meetings.
- **Decision: the human side is utterance-based** (dialogue acts, or punctuated word turns for EN2002b), so number runs never cross speakers and sentence starts are visible to the name rule.
- **Decision: reuse the grounding checker twice rather than a new prompt.** Own-transcript vs AMI-transcript verdicts give the stage-2 class; the full verdict matrix is saved so boundaries can be argued later. Stage 3 reuses `judge.v1` and the checker (notes as "transcript").
- **Decision: databases are read read-only** (`store.read_notes`, SQLite `mode=ro`): the sync can run alongside, and nothing is created or migrated.
- **Found:** Wispr's MCP transcript starts with a `<<<…>>>` safety banner that `sources/wispr_mcp.py` stores as utterance 0. The facts code drops it; the source and `ami wer` still include it.
- **Open:** fix the banner in the Wispr source; a phonetic-similarity split for name substitutions (spelling vs a different person); collect Chat answers for stage 3.

## Retention replicated on AMI (2026-11-05)

Write-up: [RETENTION.md](../tracks/RETENTION.md#ami-replication-5-nov-2026). Numbers: `evals/reports/ami/deletion_ami.json`. Run `20261105T211504Z`, $5.64.

- **Finding: deletion cost replicated; full recovery did not.** Notes only vs Granola Chat 2–16 (p = 0.001). The on-demand transcript recovered part (D vs C 9–0), but Granola Chat still beat D 17–6 on not-wrong (p = 0.035 uncorrected; 0.14 after Holm).
- **Decision: drop the fact layer from future work.** Two null results: C vs B 4–0 (p = 0.13) on the private meetings, 2–3 on AMI. It costs an extraction pass per meeting and adds nothing measurable.
- **Decision: the on-demand transcript is the retained mechanism.** It is the only layer with a significant gain in both studies. It needs the transcript to exist, so it models "keep a cold copy", not deletion.
- **Trade-off:** A − D still mixes Granola's model with ours. Whether a cold-copy reader with Granola's own model would close the gap is open.
- **Process:** numbers were recomputed from the local per-answer rows (questions matched across systems) and committed as counts only; no answer, gold or question text.

## Package and CLI renamed to `tenor` (2026-11-05)

- **Decision: code name matches the project name.** `packages/concentrate` → `packages/tenor`, `import tenor`, CLI `tenor`. "Concentrate" now means only the archived Concentrate memory system.
- **Decision: `concentrate` stays as a temporary CLI alias.** It prints a one-line deprecation notice to stderr and runs the same app, so scheduled jobs (`concentrate mcp-contract`) keep working.
- **Kept stable on purpose:** `CONCENTRATE_*` env vars, `~/.config/concentrate/` OAuth tokens, `data/cache/concentrate/` stores, the `concentrate` system id in results, and the figure hash salt and SVG metadata. The LLM cache key hashes request content only, so no cached call is invalidated; `synth --seed 42` and `figures --check` stay byte-identical.
- **Historical files keep their names:** `docs/archive/**`, `evals/reports/**`, `evals/economics/**` and `evals/scale/**` (only archived link targets were repointed to `packages/tenor`).

## Prompt caching everywhere a prefix repeats (2026-11-08)

The Console reported "Prompt caching: not enabled". Only the faithfulness and grounding checks set `cache_system`, and E1 to E3 sent every request uncached.

- **Decision: the system prompt is a cache breakpoint by default.** `LLMRequest` and `TurnRequest` gained `cache_ttl` (`"5m"` default, `"1h"`, or `None` to turn caching off). Tools and the structured-output schema render before the system prompt, so they are cached with it.
  - Measured live: the schema's injected text is part of the cached prefix. `verify_item.v2` counts 365 tokens, but Haiku 5.5 wrote 548. So even our ~300-500-token instructions clear the 512-token minimum on the current models.
- **Decision: requests can add breakpoints inside the user text.** `cache_breaks` holds character offsets after a shared prefix (a transcript, a notes block), with at most 3 plus the system prompt. The helper is `breaks_after(shared, ..., varying)`.
  - The text is split into blocks that concatenate back to the same string, so the model reads the same bytes.
- **Decision: cache markers are not part of the disk-cache key.** `cache_breaks` and `cache_ttl` never enter `LLMClient._key` or `_turn_key`. Every existing entry replays, and E1's judge user text is byte-identical to before. The legacy `cache_system` flag stays in keys where it already was.
- **Accounting.** The pieces:
  - `Completion` and `TurnCompletion` record cache reads, writes and the 1-hour part of the writes (`usage.cache_creation.ephemeral_1h_input_tokens`).
  - `cost_usd` prices them: reads at the model's rate, which is 0.05x on Opus 5.5 and 0.1x on Sonnet 5.5 (Haiku 5.5's 0.1x is ASSUMED, as the skill's table doesn't list it). Writes cost 1.25x with the 5-minute TTL and 2x with the 1-hour TTL.
  - `CallRecord` and `Ledger.by_stage()` carry `cache_read_tokens` and `cache_write_tokens`.
  - Fixed a bug: `prefetch_structured` dropped cache tokens when it wrote batch results, so their replays were under-priced.
- **Prompt layout: static first, varying last.** Semantic content is unchanged unless a version was bumped.
  - **E1 judge.** The judge calls for one question (C, N, L) now share system + question + gold + reference transcript, with a breakpoint before the answer, but only when more than one judge call runs. `run_question` now runs the reader's calls first and the judge's three back to back, so the 5-minute cache stays warm. The requests themselves are identical.
  - **Opt-in `judge_items.v2`** (`--judge-prompt`) has the same instructions and puts the reference transcript first. Only the sentence describing the input order changed, hence the bump. Every question on a meeting then shares one cached transcript. v1 stays the default because it is what E1 ran.
  - **E2.** Windows differ per item, so only system + schema are cached (~1k tokens).
  - **N answer (notes-only).** The notes block comes first. A breakpoint at its end is shared by every question on the same corpus.
  - **E1 map / merge / verify.** Only the instructions are shared.
  - **E3** (another workstream's module, not edited). It gets system caching for free. A `breaks_after(gold + reference, rest)` in its `judge_request` would also share the meeting-level part across products.
- **Quotes show both prices.** `simulate_prompt_cache` replays each uncached call's prefix segments in run order against a warm cache. It is ASSUMED that calls sharing a prefix start within the TTL; in a batch, hits are best-effort, so the result is an upper bound there. The spend cap is still checked against the uncached high.
- **Live verification (≤ $0.10 cap, 2026-11-08).** Each test sent two calls with the same prefix and a different suffix. Every second call read the whole prefix from the cache:

  | test | prefix | write → read | 2nd call |
  |---|---|---|---|
  | Haiku 5.5, E2 system | system | 986 tokens | -65% |
  | Sonnet 5.5, judge-shaped | ~5k-token transcript prefix | 5,208 tokens | $0.0106 → $0.0012 (-88%) |
  | Opus 5.5 | ~2.5k-token prefix | 2,471 tokens | $0.0102 → $0.0008 (-92%) |

  Total spent $0.0283 against $0.0421 uncached: -33% over pairs, including the write premium.
- **Re-quoted fresh runs (empty disk cache, exact inputs, ASSUMED outputs, expected):**

  | eval | uncached | cached | saving |
  |---|---|---|---|
  | E1 (v1 judge) | $7.17 | $5.13 | -28%; judge $2.69 → $1.46 |
  | E1 (v2 judge) | $7.17 | $4.45 | -38%; judge → $0.78 |
  | E2 (Haiku) | $0.091 | $0.057 | -37% |
  | E2 (Opus) | $3.63 | $2.21 | -39% |
  | E3 (12 units, Sonnet) | $0.97 | $0.93 today | $0.84 with the meeting-level break |

- **Trade-offs:**
  - A breakpoint on a prefix that is used once costs a 25% write premium on it. This is why the judge breakpoint depends on how many judge calls are planned. The system-prompt default pays it once per run per prompt.
  - We use the 5-minute TTL by default, because grouped calls start seconds apart and the 1-hour TTL's 2x write only pays for gaps of 5-60 minutes.
  - Not done: putting the whole transcript in E1's verifier or E2's prefix would cache far more, but it changes what the model reads (a new experiment, not a transport change).

## E3 on the overnight replays; two source fixes (2026-11-09)

- **Wispr transcripts over 40,000 characters were cut short.** The live continuation marker (`(...truncated, N chars remaining; continue with view_transcript.start_char=40000...)`) is followed by `<<<END TRANSCRIPT>>>`. Our parser required the marker at the very end of the text, so it missed it. Each continuation range also repeats the safety banner. Both are now handled, and a fake-server test reproduces the live shape. Three synced notes were affected and re-fetched: AMI TS3009c and two non-AMI notes. No previously scored transcript was over the limit.
- **Granola's "no summary" placeholder was stored as notes.** The MCP returns `<summary status="transcript_only">` with a sentence ("No AI summary has been generated…") when a recording has no AI notes. It is now `summary_markdown = null`. E3 skips any unit without AI notes and lists it, so a placeholder can't be judged as a one-bullet note.
- **E3 runs are pooled, not re-run.** `ami notes-completeness --meetings` scores any AMI meetings. `ami notes-completeness-pool` merges full runs from their saved results (same judge, prompt, passes and limits, enforced; no unit twice) and keeps each run's provenance. Adding meetings costs only their calls, and the judge requests stay byte-identical, so cached calls replay.
- **Trade-off:** to stay under the approved $2.00, run 2 was split into a sandbox and three capped single-meeting runs, then replayed at $0 into one result. That result's own `meta.spent_usd` is 0. The live spend and the runs it replays are recorded in the pooled report's `meta.notes` and in NOTES.md.
- **Granola's missing notes:** Granola generated no AI notes for 3 of 6 AMI recordings and 3 of 5 council recordings from the same night. All were generated once Kian opened the notes in the app. We did not call MCP `generate_summary`, because it is a write tool. Those 3 AMI meetings became E3 run 3. The pooled report now covers 11 TEST meetings.
- **Open:** Wispr's council 6847 note is still `finalized: false` hours later, so it can't be synced.

## E3 v5 rubric and AMI gold errata: post-hoc changes (2026-11-09)

These changes were made **after seeing the E3 v4 TEST results**, prompted by an independent agent re-read of all 298 TEST verdicts (`data/private/E3_ADJUDICATION.md`, not committed). They are disclosed as post hoc, and v4 / original-gold results stay the first reported numbers. v5 / errata results are always reported next to them, never instead of them.

- **Rubric (`notes_completeness.v5`, Kian's decision).** A gold *problem* counts as covered if the notes record the issue, also when they record it together with how it was resolved. It need not be framed as an open question. v4's judge often wanted the question ("outcome, not the open question"): 18 of 77 disagreements in the re-read. The prompt's examples are neutral, not taken from DEV or TEST. The existing owner rules are restated as a list, with the same meaning: a name counts as much as a role; a joint assignment still names the owner; a gold item with no specific owner needs none; a wrong owner gives "none", not "partial". The re-read found the judge breaking each of these.
- **Gold errata (`data/ami_questions/gold_errata.v1.json`, AMI-derived, CC BY 4.0).** Only corrections with clear evidence in the AMI transcript, each checked against it, with line id and quote (a test checks the original text and that every quote is verbatim when the AMI zip is present):
  - ES2006a A2/A3: owners swapped back (the UI designer says the technical functions are his task, L0189; the industrial designer's role is how it works, L0226).
  - TS3009c D9 ("standard chip") dropped: it contradicts D2 (advanced chip for the LCD, L0310). D9's only link is "the inside work is standard" (L0767).
  - ES2006c P1 (`*NA*`) dropped: a placeholder, not an item.
  - IS1000a A2 **not applied**: in that meeting only back-channels say who was addressed (weak evidence). Recorded under `not_applied` for a human decision.
- **Mechanism.** `tenor.ami.errata` loads and validates the file. `notes_completeness.gold_items(..., errata, meeting_id)` applies it after numbering, so a drop does not renumber other items, and it refuses a correction whose item id or original text does not match. `ami notes-completeness --prompt {v4,v5} --gold {errata.v1,original}` (defaults v5 and errata.v1) records `prompt`, `gold_version` (`ami-1.6.2` or `ami-1.6.2+errata.v1`) and the applied corrections in result metadata. Pooling refuses to mix gold versions. Results from before this change count as `ami-1.6.2`. With `--prompt notes_completeness.v4 --gold original`, all 72 v4 judge calls replay from the disk cache at $0, so old results stay reproducible.
- **Scope.** Errata apply to E3 only for now. The E1/E2 questions built from the corrected sentences (`ami-ES2006a-action_who-02/03`, `ami-ES2006a-action_what-02/03`, `ami-ES2006c-problem-01`) should get the same errata in a later, versioned regeneration (listed in the file under `affects_questions`).
- **Trade-off.** Re-judging is needed for every unit: the problem rule touches every meeting with problem items, and the owner restatement every meeting with actions. Only the two drops can be applied to v4 verdicts without new calls. The 3-pass re-run was quoted at $2.83 expected / $4.29 high with prompt caching, above the $2.00 limit, so it waits for Kian's approval.

## Switching the notes and answers judge to an agent reader (2026-11-09)

**Decision.** Published results for notes completeness (E3), list answers (E1, E2), the deletion study and the council notes are now based on agent readings, not on the API judge. The judge's numbers stay in the result files and are shown beside the new ones in the README ("Previously reported") and the track pages; nothing is deleted.

- **Why.** An independent second reading of every verdict, by a Claude Code agent session that decided each item from the source material before opening the judge's verdict, found the judge wrong far more often than its between-pass spread suggested: 26% of E3 items (kappa 0.55), 12% of E1 answer items, 32% of deletion-study answers. A human labelled 15 E3 disagreements blind to both verdicts and to the product, and sided with the second reading on all 15. The judge's errors were not one-directional: it missed content that is present, matched loosely worded gold literally, penalised true extra details it could not check, did not map names to roles, and in E1 credited the wrong kind of item. Prompt fixes were possible for each, but each fix would itself need validation, and the errors had already changed conclusions (E1 F1 "tie" → Chat better; deletion A vs D on not-wrong → no difference; Granola vs Wispr Flow notes → tie; confident errors 9/8/15 → 2/1/1; E3 per-meeting sign test p = 0.01 → 0.18).
- **The method.** For E3: a deterministic packet per (meeting, product) built by code (`scripts/reader_packets.py`: gold, linked AMI passages, the notes bullets exactly as the judge saw them, the product transcript; no verdicts), written instructions (`prompts/notes_reader.v<N>.md`), one JSONL row per item with a verbatim evidence quote, and a code scorer (`scripts/reader_score.py`). Instructions changed only on DEV (IS1008a): v1 two sessions agreed 12/14, v2 14/14, then frozen. TEST v2: 90.3% agreement with the second reading (kappa 0.82), 13/15 on the human cards, 73.5% with the judge (kappa 0.54). v3 was written after two TEST misses and is reported only as a sensitivity row (92.6%, kappa 0.86, 13/15 with different misses). For E1, deletion and council: blind per-meeting reader sessions (deletion answers shuffled and unlabelled) plus adjudication of every disagreement.
- **Trade-offs.**
  - *Reproducibility.* API judge calls are cached by request hash and replay byte for byte; agent sessions do not (two v3 runs differ on 15/298; any two of three full runs on 15–22; 28/298 not unanimous). Item-level claims therefore use the median of three full runs (v2, v3, v3c: 93.3% vs the second reading, kappa 0.87; 15/15 human cards, not clean because v3 was motivated by two of them); the headline recall stays on the single DEV-frozen v2 run. Mitigation: every verdict row carries its evidence quote, and every run records instruction version, model, date and git SHA, so verdicts are auditable by hand even if not re-generable. This weakens the "same seed → same output" rule for this one stage; the scorer itself stays deterministic.
  - *Independence.* Reader, second reading and judge are all Claude models; agreement among them is weaker evidence than human agreement, and shared blind spots do not show. The human check covers disagreements only (15 items, one verifier). The E1, deletion and council references were not separately human-checked.
  - *Cost and scale.* No API spend, but each reading takes a long agent session and does not scale to nightly runs. The API judge remains the right tool for regression tests once its prompt is fixed and re-validated against these references (the re-read lists seven concrete prompt fixes).
  - *Post-hoc risk.* The decision to switch was made after seeing results. To limit tuning on TEST, the headline is the instruction version frozen on DEV before TEST (v2), and the post-hoc v3 is shown only as sensitivity.
- **Not re-based.** The notes grounding checker (claims supported by both transcripts) and E3 bullet precision were not re-read; E1/E2 bootstrap CIs and E2 recall columns were not recomputed; the README's Figure 2 (answers-from-notes rows) and Figure 5 still plot the judge's numbers and are captioned "old judge" rather than regenerated, because the reference verdicts are not in a committed report file yet.
- **Open.** About 100 human labels across E3, E1 and the deletion study; a judge prompt re-validated against the references for automated runs; listening to the council roll call (CB 121282), where both products' transcripts disagree with the official record.
