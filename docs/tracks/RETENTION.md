# Retention track

This page defines the retention track (what an answer loses when transcripts are deleted and only notes remain). It holds the AMI replication result, its design, and the earlier private-meeting study.

**Headline (reference reading, 2026-11-09).** Deleting transcripts roughly halves correct answers: Granola Chat 60%, Granola's notes only 32% (20–1, p < 0.001). A kept transcript read on demand (D) recovers most of the loss: on not-wrong answers D and Chat do not differ (85% vs 90%, 6–3, p = 0.51), though Chat is more often fully correct (60% vs 46%, 15–5, p = 0.04, not significant after Holm). This is close to the earlier private result (D 87% vs Granola 89%). The API judge had reported the opposite on not-wrong answers (17–6, p = 0.035); see [Reference reading](#reference-reading-2026-11-09).

| | |
|---|---|
| **Question** | When transcripts are deleted, how much worse do answers get, on which questions, and does the product then decline or answer wrongly with confidence? |
| **How measured** | A ladder of conditions on the same questions: the product with transcripts; an answer model over the product's notes only; plus a kept fact layer; plus a transcript read only when needed. Paired wins and losses, exact sign tests |
| **AMI result (5 Nov 2026; re-read 9 Nov)** | 68 questions, 5 AMI meetings. Correct, reference reading: Granola Chat 60%, Granola notes only 32% (paired 20–1, p < 0.001), plus fact layer 32% (n.s.), plus on-demand transcript 46%. API judge: 41%, 21%, 19%, 32%. [Reference reading](#reference-reading-2026-11-09), [judge results](#ami-replication-5-nov-2026) |
| **Earlier result** | Author's 4 private Granola meetings, 47 DEV questions: 89% with transcripts, 57% from notes only (paired 2–17, p = 0.001); 87% with an on-demand transcript. Superseded |
| **Status** | AMI result preliminary (agent re-read of the API judge, n = 68). Earlier result private and superseded |

Code: [`evals/deletion_sim.py`](../../packages/tenor/evals/deletion_sim.py), CLI `tenor deletion-sim`.
Tests: [`tests/test_deletion_sim.py`](../../tests/test_deletion_sim.py) (fakes only, no network).
AMI replication: **run 5 Nov 2026** ([results](#ami-replication-5-nov-2026), [design](#ami-replication-design)).

## Reference reading (2026-11-09)

**Why.** The API judge (`judge.v1`, Sonnet 5.5, one pass) was re-read answer by answer. Blind agent readers (one Claude Code session per meeting, no API calls) read AMI's reference transcript in full and scored every answer with `judge.v1`'s criteria; the five conditions' answers were shuffled per question and labelled V–Z, so readers did not know which system wrote which. Every disagreement's rationale was then reviewed. The judge differed from the reference on **109 of 340 answers (32%; kappa 0.53)**, almost always harsher (106 vs 3). Main causes: literal reading of loosely worded gold (50 answers), true extra items treated as wrong because the judge had no transcript (37), names not mapped to roles even with the roster in the prompt (23), hedged answers read as abstentions (20). The errors were spread about evenly across conditions, so paired comparisons are less affected than levels, but enough to flip two of them.

Reference = `v_gold`: the original rubric applied correctly against the gold as written. Counts recomputed from the re-read rows; Wilson 95% CIs.

| Condition | Correct | Not wrong | Confident errors |
|---|---|---|---|
| A. Granola Chat, transcript kept | 41 / 68 = 60% [48, 71] | 61 = 90% [80, 95] | 7 |
| B. Granola notes only | 22 = 32% [22, 44] | 44 = 65% [53, 75] | 2 |
| B′. Wispr Flow notes only | 23 = 34% [24, 46] | 37 = 54% [43, 66] | 9 |
| C. Granola notes + fact layer | 22 = 32% [22, 44] | 47 = 69% [57, 79] | 1 |
| D. C + transcript read on abstain | 31 = 46% [34, 57] | 58 = 85% [75, 92] | 1 |

| Comparison | Correct | Not wrong | vs API judge |
|---|---|---|---|
| A vs B: deletion (plus a model change) | 20–1, p < 0.001 | 17–0, p < 0.001 | holds |
| B vs B′: Granola's notes vs Wispr Flow's | 9–10, p = 1.0 | 10–3, p = 0.09 | **flips to a tie** (judge 10–4 correct) |
| C vs B: what the fact layer adds | 1–1 | 3–0, p = 0.25 | holds (nothing) |
| D vs C: what the on-demand transcript adds | 10–1, p = 0.012 | 12–1, p = 0.003 | holds, now on both measures |
| A vs D: the gap left | 15–5, p = 0.04 | 6–3, p = 0.51 | **not wrong flips to no difference** (judge 17–6, p = 0.035) |

- **Holm correction** across the five tests per measure (computed on the reference counts): A vs B and D vs C stay significant on both measures (D vs C correct: adjusted p = 0.047); A vs D on correct does not (adjusted p = 0.12).
- **Confident errors flip.** The judge counted 9 / 8 / 15 for B / C / D; the reference counts 2 / 1 / 1. "Hedges, doesn't bluff" holds for our reader over Granola's notes after all, but not over Wispr Flow's notes (9).
- **Gold corrected against the transcript (`v_adj`)** gives the same directions: A vs B 24–2; D vs C 13–1 correct, 12–2 not wrong; A vs D 17–7 correct (p = 0.064), 7–2 not wrong.
- **Decision questions** still score low for every condition; several decision gold items are wrong (IB4003 decision-02, IS1008b/c decision-02, IS1008d decision-03), listed as errata candidates.
- **Caveats.** The readers share a model family with the judge, and this reference was not human-checked on this study (the method was, on notes completeness: 15 of 15). Some readers are generous on hedged answers; downgrading every such hedge one level shrinks the B′, C and D gains by a few points and changes no paired direction. Verdict rows stay local.

## AMI replication (5 Nov 2026)

**Preliminary; previously reported (API judge).** Superseded as the headline by the [reference reading](#reference-reading-2026-11-09). Public AMI meetings replayed into Granola and Wispr Flow; automated judge; n = 68 questions on 5 meetings (13 DEV, 55 TEST). Nothing was tuned on this run.

- Run `20261105T211504Z`, git `48ef477` (working tree dirty), seed 42. Design: [below](#ami-replication-design).
- Reader `claude-opus-5-5` for B–D (`answer.v1`; C and D add `extract.v2`, `entail.v2`). A is Granola's own Chat.
- One judge for all five: `judge.v1` on `claude-sonnet-5-5`.
- Scope: each question's own meeting(s), as Granola Chat was asked.
- Spent $5.64 (quote $7.79 expected, hard cap $11).
- Numbers: [`evals/reports/ami/deletion_ami.json`](../../evals/reports/ami/deletion_ami.json) (counts, Wilson CIs, paired tests; no answer text). Per-answer rows stay local.

### Per condition

"Not wrong" = judged correct or partially correct. Brackets: Wilson 95% CI.

| Condition | Correct | Not wrong | Confident errors |
|---|---|---|---|
| A. Granola Chat, transcript kept | 28 / 68 = 41% [30, 53] | 52 / 68 = 76% [65, 85] | 16 |
| B. Granola notes only | 14 = 21% [13, 32] | 34 = 50% [38, 62] | 9 |
| B′. Wispr Flow notes only | 8 = 12% [6, 22] | 30 = 44% [33, 56] | 14 |
| C. Granola notes + fact layer | 13 = 19% [12, 30] | 35 = 51% [40, 63] | 8 |
| D. C + transcript read on abstain | 22 = 32% [22, 44] | 41 = 60% [48, 71] | 15 |

- **D's fallback fired on 36 of 68 questions** (when C's first answer abstained or hedged).
- **Confident error** = judged incorrect without abstaining or saying the material doesn't cover it. Granola Chat has no abstain signal, so all 16 of its incorrect answers count.

### Paired tests (same 68 questions)

Wins–losses on questions where exactly one side meets the metric; exact two-sided sign test.

| Comparison | Correct | Not wrong |
|---|---|---|
| A vs B: deletion (plus a model change) | 16–2, p = 0.001 | 19–1, p < 0.001 |
| B vs B′: Granola's notes vs Wispr Flow's | 10–4, p = 0.18 (not significant) | 11–7, p = 0.48 (not significant) |
| C vs B: what the fact layer adds | 2–3, p = 1.0 (not significant) | 4–3, p = 1.0 (not significant) |
| D vs C: what the on-demand transcript adds | 9–0, p = 0.004 | 10–4, p = 0.18 (not significant) |
| A vs D: the gap left | 9–3, p = 0.15 (not significant) | 17–6, p = 0.035 |

**Multiple tests.** These p-values are uncorrected. With Holm correction across the five tests per metric, A vs B and D vs C (correct) stay significant; A vs D on not-wrong (adjusted p = 0.14) does not.

### By question type (correct, of n)

| Type | n | A | B | B′ | C | D |
|---|---|---|---|---|---|---|
| action: what | 16 | 4 | 3 | 3 | 1 | 2 |
| action: who | 15 | 11 | 7 | 4 | 8 | 9 |
| decision | 10 | 2 | 0 | 0 | 0 | 0 |
| problem | 4 | 1 | 1 | 0 | 0 | 0 |
| what changed | 11 | 4 | 1 | 0 | 1 | 2 |
| who said | 12 | 6 | 2 | 1 | 3 | 9 |

- **Decision questions score 0–20% for every condition.** Their not-wrong rate is 40–60%, so this is mostly the strict summary-level answer key, not a deletion effect.
- **"Who said" is where the transcript matters.** D gets 9 of 12 against B's 2. D also beats Granola Chat here (9 vs 6; n = 12, untested).

### What replicated

- **Deletion costs a lot, and it is significant.** A vs B: 16–2 here, 17–2 on the private meetings. Both p ≤ 0.001.
- **The fact layer adds nothing, twice.** C vs B: 2–3 here; 4–0 (p = 0.13) before. Neither is significant.
- **The on-demand transcript recovers a significant part.** D vs C: 9–0 here, 10–0 before.

### What did not replicate

- **(API judge; does not hold under the reference reading) D does not close the gap.** Before, D reached 87% against Granola's 89%. Here Granola Chat beats D, 17–6 on not-wrong (p = 0.035, not significant after Holm) and 9–3 on correct (p = 0.15).
- **(API judge; flips under the reference reading: 2 / 1 / 1) "Hedges, doesn't bluff" did not hold.** Before, B and C made 0 confident errors. Here B made 9, C 8, D 15. The automated judge over-counts errors, so these are upper bounds.
- **(API judge; a tie under the reference reading, 9–10) Granola's notes beat Wispr Flow's on the point estimate only.** 14 vs 8 correct; 10–4, not significant.

### Threats to validity

- **A vs B–D use different answerers.** A is Granola's own Chat; B–D are our reader. So A − B mixes deletion with a model change, and so does A − D. B′ − B, C − B and D − C share the reader and are clean.
- **The automated judge over-counts errors.** The reference reading above replaces it as the headline. Human checks found most of its error verdicts did not hold ([CALIBRATED_JUDGING.md](../method/CALIBRATED_JUDGING.md)). It is applied equally to all five conditions, so paired differences are less affected than levels.
- **Templated questions, strict gold.** Questions come from fixed templates over AMI annotations; gold answers are annotators' summary sentences. Decision questions show the cost: 0–20% correct everywhere.
- **Wispr Flow's notes use generic speaker labels** ("Speaker 1"), which hurts who-questions for B′ (action: who 4 vs B's 7; who said 1 vs 2).
- **Small sample.** n = 68 questions on 5 meetings, four of them one design-team series. Questions within a meeting are not independent.
- **One product's Chat.** Only Granola has a Chat answer to compare against; Wispr Flow's MCP has no question-answering tool.
- **Oracle scope.** Every condition is told which meetings to read, so retrieval failures are excluded for all.

## The question

Granola lets users turn on transcript auto-deletion. Its own help text says chat will then be limited to whatever is in the notes, and chat quality will be degraded.

How much worse does it get, on which kinds of question? And when it is wrong, does it say "I don't know", or does it answer wrong with confidence? What would recover the loss?

We can't switch deletion on inside Granola's agent and watch. So we rebuild the four situations from the outside. Each uses the same real questions, the same answer model and prompt, and the same judges.

## The four conditions

Each condition after A adds one layer to the one before it.

| | Condition | What the answer model sees | What it approximates |
|---|---|---|---|
| A | `granola` | nothing: Granola's own agent answers (`query_granola_meetings`, black box) | Granola today, transcripts kept |
| B | `notes_only` | Granola's notes (AI summary + typed notes) for **every** meeting held by the as-of date | Granola chat **after** deletion |
| C | `notes_facts` | B + verified facts, commitments and digests from the archived Concentrate memory system, every transcript deleted | deletion with a fact layer extracted first |
| D | `notes_facts_fallback` | C + transcript passages, only when C's answer abstains or says the material doesn't cover it | the hot transcript deleted, a cold copy read on demand |

B, C and D all use `answer.v1` on the same answer model as every other system. A is Granola's own agent and model.

### Where the notes come from

The notes are Granola's own AI notes, as the public MCP returns them (`get_meetings`). We use the copy already synced into `data/real.db` by `tenor sync --source granola-mcp`. That copy came from the same MCP, so no live call is needed. Only reads were ever made (the MCP source is read-only by construction).

### What each condition can and can't tell us

- **A is the only real Granola.** Everything else is our reconstruction. We can't see Granola's internals: its retrieval, its prompts, or how its agent actually behaves once transcripts are gone.
- **B approximates Granola's degraded chat. It is not Granola's actual post-deletion behaviour.**
  - B sees *all* the notes, not a retrieved subset. With 4 real meetings (about 4k tokens of notes) there is nothing to retrieve, so B is an **upper bound** for a notes-only agent: it can't miss a note by searching badly.
  - B uses our answer model, not Granola's. So A − B mixes "lost the transcripts" with "different model and prompt". C − B and D − C don't have this problem, because B, C and D share the model and prompt.
  - Granola's real degraded chat could be better than B (a better prompt) or worse (retrieval misses at scale).
- **C keeps what the fact layer kept.** Facts were extracted from the transcripts *before* deletion: that is the point of a fact layer. Their line IDs stay as provenance pointers, but no transcript line is shown.
  - Deletion uses `retire_after_days = -1`, so even a meeting held on the as-of day loses its transcript. `0` would keep same-day transcripts.
- **D is for reference: it needs the transcripts to exist.** D's first call is exactly C's request, so it costs nothing extra after C has run.
  - The fallback fires only when C hedges or abstains. So D **can't recover a confident wrong answer**, by design. If C's confident-error rate is high, that is the fallback's blind spot, and the run will show it.

## What is measured, per answer

- **Primary verdict.** `judge.v1` on the answer model, against the user's gold answer, exactly as in the [real slice](../results/REAL_SLICE.md).
- **Second judge.** The same `judge.v1` request on a different model (default `claude-sonnet-5-5`). Reported: second-judge accuracy and the agreement rate.
- **Confident error.** Judged `incorrect` *and* neither abstained nor said the material doesn't cover it.
  - "Doesn't cover it" uses `says_not_covered`, the same detector the fallback uses. It also reads Granola's prose, since Granola has no abstain flag.
  - A hedged "the notes don't say", judged incorrect, counts as a **hedged miss**, not a confident error.
- **Tags.** From P2-T's tag file (`--tags`; JSON `{qid: [tags]}` or JSONL `{"qid", "tags"}`). If there is no file, every answer is `(untagged)`.
- **Summary.** Per condition, and per category and tag: accuracy and confident-error rate (bootstrap 95% CIs), partial share, hedged-miss share, and how often the fallback fired.
  - Paired wins and losses along the ladder: B vs A, C vs B, D vs C, on the same questions.

Files: per-answer rows go to `data/real/reports/<stamp>.deletion.jsonl` (private). The aggregate goes to `<stamp>.deletion.aggregate.json`, checked by `write_committable` (no question, answer or meeting ID can leak).

## Splits

- `--split dev`: the 47 verified questions already seen while building. Granola's answers to 39 of them are recorded; 8 would be asked live (hosted, $0 to us).
- `--split test`: the user's hand-written held-out questions, imported with `tenor real-kit import-test data/real/test_questions.md`. None are imported yet.
  - Until they are, the quote prices 30 test questions (`--test-n`) at the dev per-question rate.

## The exact cost quote

Run with `tenor deletion-sim --quote --split dev|test`. It makes no paid calls.

How it is counted:
- **Input tokens are counted exactly.** Every request the system would send is built and counted with the free `count_tokens` endpoint.
  - Add the structured-output overhead that counting misses: answer +255, judge +246 tokens per call. That is the median of recorded minus counted tokens over 36 cached real-slice answer calls and 146 cached judge calls.
  - The last estimate was about 2× low because it used chars/3.5 and fixed output guesses.
- **Cached requests are $0.** That covers Granola's 39 recorded dev answers and their judge verdicts. An identical request (D's first call = C's) is paid once.
- **Output tokens are not knowable in advance.** "Expected" uses the recorded mean (answer 261, judge 65, thinking included). "High" uses the 90th percentile (456 / 84).
- **Fallback.** Expected: it fires on 47% of first answers, Concentrate's measured rate. High: on every answer.
- **Judge inputs** are exact when the answer is recorded (Granola's). Otherwise they are counted with an empty answer plus the mean recorded answer length.
- **Prices.** Answer and judge model `claude-opus-5-5` ($4 / $20 per M), second judge `claude-sonnet-5-5`, interactive (no batch discount).

| Condition | dev (47 q) expected | dev high | test (30 q, scaled) expected | test high |
|---|---|---|---|---|
| A: Granola full (judges only) | $0.05 | $0.06 | $0.20 | $0.22 |
| B: notes only | $1.56 | $1.77 | $0.99 | $1.13 |
| C: notes + fact layer | $2.73 | $2.94 | $1.74 | $1.87 |
| D: + transcript fallback | $1.93 | $3.95 | $1.23 | $2.52 |
| **Total** | **$6.27** | **$8.71** | **$4.17** | **$5.74** |

- Per condition, the second judge is about $0.10 on dev.
- C's answer calls are the largest item: about 11.5k input tokens per question, the notes plus the memory context.

## DEV results (47 seen questions, real meetings)

**DEV: these questions were seen while building Concentrate, and their gold answers were bulk-approved.** Treat the numbers as where to look, not as claims.

- Run `20261104T012414Z` at git `6dc2cb6`, seed 42. Answer model and primary judge `claude-opus-5-5`; second judge `claude-sonnet-5-5` (188 of 188 answers re-judged).
- Tags come from P2-T's portable rule (`notes_vs_transcript.real_tags` at `9acda8c`), recomputed privately: 23 notes-sufficient, 18 transcript-needed, 6 unanswerable.
- Aggregate: [`evals/reports/real-deletion-sim-dev.aggregate.json`](../../evals/reports/real-deletion-sim-dev.aggregate.json). Per-question detail stays in `data/real/` only.
- CIs: 95% bootstrap. Sign test: exact two-sided, on discordant pairs.

### Per condition

| Condition | n | Accuracy [95% CI] | Confident error [95% CI] | Hedged miss | Fallback fired | 2nd-judge accuracy | Judge agreement |
|---|---|---|---|---|---|---|---|
| A: Granola full | 47 | 89% [79–98] | 6% [0–15] | 4% | n/a | 85% | 96% |
| B: notes only | 47 | 57% [43–70] | 0% | 43% | n/a | 60% | 96% |
| C: notes + fact layer | 47 | 66% [51–79] | 0% | 34% | n/a | 64% | 98% |
| D: + transcript fallback | 47 | 87% [77–96] | 0% | 13% | 60% (28/47) | 87% | 100% |

Partial answers (neither correct nor counted as a miss above) were 4% / 9% / 4% / 6%.

Granola's 6% confident error is the same under P2-T's flag-only definition (answered and judged incorrect).

### By tag (accuracy; confident error)

| Tag | n | A Granola | B notes only | C + facts | D + fallback |
|---|---|---|---|---|---|
| notes-sufficient | 23 | 91%; 0% | 57%; 0% | 65%; 0% | 83%; 0% |
| transcript-needed | 18 | 94%; 6% | 44%; 0% | 56%; 0% | 89%; 0% |
| unanswerable | 6 | 67%; 33% | 100%; 0% | 100%; 0% | 100%; 0% |

### Paired comparisons (same questions)

| Comparison | Slice | wins / losses | Δ accuracy | sign test p |
|---|---|---|---|---|
| **B vs A: what deletion costs** | all (47) | 2 / 17 | −32 pts | 0.001 |
| | notes-sufficient (23) | 0 / 8 | −35 pts | 0.008 |
| | transcript-needed (18) | 0 / 9 | −50 pts | 0.004 |
| | unanswerable (6) | 2 / 0 | +33 pts | 0.50 |
| **C vs B: what facts recover** | all (47) | 4 / 0 | +9 pts | 0.13 |
| | notes-sufficient / transcript-needed | 2 / 0 each | +9 / +11 pts | 0.50 each |
| **D vs C: what the fallback adds** | all (47) | 10 / 0 | +21 pts | 0.002 |
| | notes-sufficient (23) | 4 / 0 | +17 pts | 0.13 |
| | transcript-needed (18) | 6 / 0 | +33 pts | 0.031 |

### Reading (DEV, n = 47; read with the caveats above)

- **Deletion costs a lot, and more where the transcript is needed.** On transcript-needed questions, notes-only drops 50 points against Granola. The loss is significant even overall (17 losses to 2).
  - Part of the A − B gap is our model and prompt, not deletion (see "What each condition can and can't tell us").
- **Without transcripts, our answer model hedges; it doesn't bluff.** None of B's or C's incorrect answers was confident: each one abstained or said the material doesn't cover it.
  - Granola, with transcripts, produced the only confident errors: 2 of its 6 unanswerable questions got a confident answer.
  - So on these questions deletion turns right answers into *refusals*, not into confident errors. That is a property of `answer.v1` on this model. Granola's own degraded chat may behave differently, and we can't observe it.
- **The fact layer alone recovers little.** C vs B: +9 points, 4–0, not significant at n = 47.
- **The fallback recovers most of the loss.** D reaches 87% against Granola's 89%. Because C never bluffed, the fallback's blind spot (it can't fire on a confident error) didn't come up here.
  - But D reads transcripts, so it is "keep a cold copy", not deletion.
- **Bad for the tag rule:** notes-only scores only 57% on *notes-sufficient* questions.
  - Either the string-matching rule over-calls notes-sufficient, or the answer model under-uses the notes. This run can't tell which.
- **Unanswerable has n = 6.** Don't read anything into its row.

### Spend vs the quote

- **Actual: $6.42.** That is $6.13 for answers and the primary judge, plus $0.29 for the second judge. The quote was $6.27 expected, $8.71 high: 2% over expected.
- **Input tokens matched the quote exactly** for B (248,707) and C (541,338).
- The overshoot came from the fallback. It fired on 60% of answers, not the 47% assumed, and outputs ran a little long.

## AMI replication: design

The result above rests on the author's 4 private meetings, so nobody else can check it. This replicates it on the public [AMI corpus](../method/AMI_REPLAY.md): AMI meetings played into Granola and Wispr Flow. It can be released, and n grows as more AMI meetings are recorded.

Status: **run 5 Nov 2026**; results [above](#ami-replication-5-nov-2026). Code: [`evals/deletion_ami.py`](../../packages/tenor/evals/deletion_ami.py), CLI `tenor ami deletion-sim`. Tests: [`tests/test_deletion_ami.py`](../../tests/test_deletion_ami.py) (fakes only).

### Conditions

Same ladder, one product comparison added.

| | Condition | What is answered from | Answered by |
|---|---|---|---|
| A | `granola_chat` | Granola's own Chat, transcripts kept (answers recorded once, replayed at $0) | Granola |
| B | `notes_only` | Granola's notes for the question's own meetings | our model, `answer.v1` |
| B′ | `notes_only_wispr` | the same, Wispr Flow's notes | our model, `answer.v1` |
| C | `notes_facts` | B + Concentrate's fact layer, extracted from Granola's transcript, then every transcript deleted | our model, `answer.v1` |
| D | `notes_facts_fallback` | C + Granola's transcript, read only when C hedges or abstains | our model, `answer.v1` |

- **Scope matched to A.** Granola Chat was asked with `document_ids` = the question's own meeting(s) (checked: 68 of 68). So every condition reads exactly those meetings: B and B′ their notes, C a fact layer built over just those meetings, D their transcript. Results meta: `scope: "question meetings, matched to condition A"`.
  - The original gave B all notes (an upper bound). Here that would mix "lost the transcript" with "distracted by other meetings" in A − B. `--scope all` keeps that variant, off by default.
  - C is built once per meeting set: 5 single meetings and 3 pairs today.
- **Same code as the original.** B, C and D are the original classes; only the inputs change.
- **Same answer model:** read from the original's meta (`claude-opus-5-5`).
- **One judge for all five,** A included: stage 3's `judge.v1` on `claude-sonnet-5-5`, gold + AMI speaker roster, product boilerplate stripped ([WRONG_FACTS.md](../method/WRONG_FACTS.md)). A's judge requests are stage 3's, byte for byte, so they replay from cache.
- **Paired:** B vs A, B′ vs B, C vs B, D vs C. Exact sign tests, bootstrap and Wilson 95% CIs, by AMI question type and by tag (DEV/TEST, one- vs two-meeting).

### What grows with the data

- **Meetings:** every AMI recording the synced DBs match (`match_recordings` + `recording_ids.txt`). No meeting list in the code.
- **Questions:** any AMI question whose meetings both apps recorded and that Granola Chat answered. Today: 68 questions on 5 meetings (13 ES2006a questions wait for Chat answers).
- **Figures:** `ami_retention_steps` (with a Granola vs Wispr notes-only panel), `ami_answer_types`, `ami_question_heatmap`. They read n from the aggregate (`evals/reports/ami/deletion-sim.aggregate.json`) and appear once it exists. The 5 Nov run did not commit that aggregate, so these figures are not generated yet; its numbers are in `deletion_ami.json`.

### Quote (2026-11-05, question scope, $0 to compute)

`tenor ami deletion-sim --quote`. Inputs counted with the free `count_tokens`; output and fallback rate from the real DEV run.

| Run | Expected | High |
|---|---|---|
| Pilot: 5 DEV questions × 5 conditions, fact layer over 1 meeting | $0.55 | $0.76 |
| Full: 68 questions × 5 conditions, fact layer over 8 meeting sets | $7.79 | $10.66 |

- **Scoping cut the quote by 40%** (was $13.08 / $16.50 with every condition reading all 7 meetings).
- **Largest items:** C ($2.76) and D ($2.28 expected, $3.99 if the fallback always fires). B and B′ are about $1.07 each (~2k tokens of notes per question).
- **ASSUMED until built:** C's memory context is priced at 6,226 tokens/question (C − B on the real run, a 4-meeting memory). A 1–2 meeting memory is probably smaller, so C and D are likely overestimated. The build ($0.62, high $0.94; 8 extraction calls) is a calibrated model estimate, not a count. After the pilot or build, re-run `--quote`: C and D become exact.
- **A costs $0:** answers are recorded and all 68 judge verdicts are cached from stage 3.
- **No second judge by default** (`--judge2 claude-opus-5-5` adds one).

### Commands

```bash
uv run tenor ami deletion-sim --quote
uv run tenor ami deletion-sim --pilot                    # hard cap $1.00, asks first
uv run tenor ami deletion-sim --max-usd 11 --commit-to evals/reports/ami/deletion-sim.aggregate.json
```

Every client (answers, judge, fact-layer build) shares one ledger and one hard cap (`LLMClient(max_usd=...)`). A run refuses to start if the quote's high estimate exceeds the cap. Per-answer rows stay in `data/ami/results/deletion/` (gitignored); the aggregate is checked by `check_committable` (no question, gold or answer text).

### Threats to validity

- **Silent replay: no typed notes.** AMI is played in with nobody typing, so "notes" are AI notes only. Real users' typed notes could make B stronger.
- **Mostly role-play.** IS1008 is a scenario series (actors designing a remote control); IB4003 is the only non-scenario meeting with questions. Real meetings have more context and less structure.
- **AMI gold is summary-level.** Gold answers are annotators' summary sentences, not facts checked against the audio; lists must be complete to score "correct", which favours partial verdicts.
- **One human verifier so far.** The calibrated judge was checked by one person (21 rows, `human_verification_v3`).
- **Small n.** 68 questions, 13 DEV; most of the ladder steps will not reach p < 0.05.
- **A − B still mixes model and deletion,** as in the original. B′ − B is clean: same model, same prompt, only the app's notes differ.
- **Scope is oracle, like A's.** Every condition is told which meetings to read. That removes retrieval as a failure mode, for Granola and for us alike; `--scope all` adds distraction back.
- **The fact layer has no AMI people directory:** only attendee metadata (the account owner), the same method as the original.

## Why outside-in

- Granola's engineers can measure deletion from the inside. An outside benchmark measures what a *user* (or an agent over the public MCP) actually gets.
- It uses only Granola's public surface: the public MCP, read-only, and its own notes. So anyone with a Granola account can re-run it on their own meetings.
- It makes the trade-off concrete, per question type: where do notes suffice, and where does deletion turn a right answer into a confident wrong one? It also tests whether a fact layer extracted before deletion recovers the loss.

## Commands

```bash
uv run tenor deletion-sim --quote --split dev
```

```bash
uv run tenor deletion-sim --split dev --tags data/real/tags.json --budget 9
```

The run asks for confirmation unless `--yes` is passed, and it stops at `--budget`.
