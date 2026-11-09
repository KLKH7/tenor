# TENOR (Transcripts, Evidence, Notes, Outcomes, Recall)

**An open benchmark for meeting note-takers.**

TENOR replays public meeting recordings into real meeting products and measures, from the outside, what their users depend on: whether the meeting was captured, whether the notes are faithful and complete, whether answers about the meeting are right, and what is lost when transcripts are deleted. Granola and Wispr Flow are the reference products.

*Independent research prototype; [not affiliated with Granola or Wispr](#not-affiliated-with-granola-or-wispr).*

## Key findings (preliminary)

All findings come from public AMI meetings (7 for capture, transcription and answers; 11 held-out meetings for notes completeness) and are preliminary; read them as directions, not rankings. Notes and answers are scored by agent readers, a method that a small human check supported ([How results are judged](#how-results-are-judged)); earlier figures from the first automated judge are listed under [Previously reported](#previously-reported).

- **Notes leave out about a quarter of what was decided, assigned or raised, while the products' own transcripts contain almost all of it.** On 11 meetings (149 annotated items), Granola's notes covered 0.72 and Wispr Flow's 0.77 of the items, counting partly covered items as half. The products' own transcripts contained 291 of the 298 product-item pairs ([Notes completeness](#notes-completeness)).
- **Capture works under normal use.** Both products captured almost all speech: 98.5% (Granola) and 99.4% (Wispr Flow) of 15-second windows, across 7 meetings ([Capture](#capture)).
- **Note claims are mostly grounded.** 90% of Granola's and 81% of Wispr Flow's note claims were supported by both the product's and AMI's transcript (387 claims; automated checker, not yet re-read) ([Notes](#notes)).
- **Chat's list answers are precise and fairly complete; listing everything adds recall but loses precision.** On 30 list questions, 89% of Granola Chat's items were true and it found 84% of the annotated items. Listing every item from the full transcript found 93% but only 64% of its items were true, through over-inclusion rather than invention. Chat had the better balance of the two (F1 0.80 vs 0.70; better on 22 questions, worse on 5, p = 0.002) ([Answers](#answers)).
- **Answering from notes alone roughly halves correct answers.** Correct answers fell from 60% with transcripts (Granola Chat) to 32% from Granola's notes alone (68 questions; 20 questions lost, 1 gained, p < 0.001) ([Retention](#retention-what-deleting-transcripts-costs)).
- **The first automated judge was wrong too often to trust on its own.** It disagreed with a careful second reading on 26% of notes-completeness items, and on 15 of those items a human, blind to both verdicts, sided with the second reading every time. Several earlier conclusions changed as a result ([How results are judged](#how-results-are-judged)).
- **Long silences did not cause silent failures; an app update did.** In two runs with 17–19 minutes of silence mid-meeting, both products warned the user and resumed when speech returned; in one run an automatic application update ended an active recording without a visible warning ([Capture under stress](#capture-under-stress)).

## Status

What has been built and measured so far, as of 9 November 2026.

| What exists | Size |
|---|---|
| Public meetings replayed into products | 13 AMI meetings, each replayed into 2 products, Granola and Wispr Flow: the first 7 (about 3 hours of audio) and 6 more of the same design-team scenario used for notes completeness |
| Questions about those meetings | 68, built by fixed templates from AMI's human annotations (13 used during development) |
| Annotated items checked against notes | 149 items in 11 held-out meetings, per product (298 product-item pairs) |
| Human labels on product outputs | 38 from 1 verifier: 20 note claims, 3 Chat answers, and 15 notes-completeness items where two automated readings disagreed |

**All AMI results are preliminary.** The samples are small (7 to 11 meetings), the human-labelled set is small, and the reader that scores notes and answers has been checked against 15 human labels, not the planned 100. No result below is confirmed. Planned next: capture stress tests and a nightly replay suite ([next research step](#capture-under-stress)), and about 100 human labels.

Results from an earlier study on the author's own private meetings are collected in one place, [Earlier private study](#earlier-private-study).

## Motivation

Why measure shipped meeting products end to end, and why these two.

Meeting note-takers are now ordinary work tools. People rely on them to record what was said, write it up, and answer questions about it later, often through an AI agent that reads the product's data via MCP (Model Context Protocol, the interface agents use to call a product's tools). When one of these steps fails, the user usually cannot tell: a missed half-hour, a misheard number or an over-confident answer looks the same as a correct one.

**Existing benchmarks test parts, not shipped products.** Speech-recognition benchmarks score transcription models; agent benchmarks score agents on fixed tasks. Meeting benchmarks such as QMSum, MeetingQA and ELITR-Bench give a model a transcript and grade the model; memory benchmarks such as LongMemEval and LoCoMo use chat or generated dialogue. None that we found tests a shipped meeting product end to end, from audio in to answers out, through the interfaces its users and their agents actually use ([`docs/RELATED_WORK.md`](docs/RELATED_WORK.md)). The closest comparison, a vendor-run test of four note-takers on five AMI meetings, scores notes only.

**Outside-in evaluation fills that gap.** TENOR plays the same public audio into each product, reads the results back only through each product's public, read-only MCP tools, and scores them against human ground truth. It makes no claims about product internals; everything reported is observable by any user.

**Why these two reference products.** Granola and Wispr Flow are the two leading bot-free meeting note-takers (they capture audio on the user's device rather than sending a bot into the call) with public MCP access. Both can be measured the same way, on the same meetings, through the same kind of interface. More products are planned ([roadmap](#roadmap-to-v1)).

## Method

How audio goes in, how outputs come back, and how they are scored.

![How does the outside-in evaluation harness work? Public AMI meeting audio is played into Granola and Wispr Flow at once; transcripts, notes and answers are read back through each product's read-only MCP tools into a private local store, then scored by scorers that use no LLM and by an LLM judge against AMI's human transcripts, annotations and human labels](docs/figures/harness_flow.svg)

*Figure 1. How does the evaluation harness work? Boxes are stages; solid arrows show what passes from one stage to the next, and dashed arrows show the answer key each scorer uses. Granola is black and Wispr Flow teal, as in every figure. The 7 AMI meetings total 3 hours of audio. Only scores leave the private store.*

**AMI replay protocol.** Meetings come from the AMI Meeting Corpus: real recorded speech with human transcripts, word timings and annotations (decisions, actions, problems), licensed CC BY 4.0. The set is the four-meeting IS1008 design-team series, ES2006a (another design-team role-play), IB4003 (a staged meeting) and EN2002b (a naturally occurring meeting). Each meeting's headset mix is played in real time as system audio into both products at once. The protocol for scored runs clears personal dictionaries, mutes the speakers so the room stays silent, and drops and counts any microphone turns. Details, licence analysis and risks: [`docs/method/AMI_REPLAY.md`](docs/method/AMI_REPLAY.md).

**Read-only MCP collection.** Each product's notes, transcripts and answers are read through its official MCP server. Every call goes through an allowlist that refuses any tool not on it before anything is sent, so no write tool can be called. Product outputs are stored locally under gitignored paths and are never committed; only scores, labels and short quoted examples beside the AMI source are published. Harness and pairing: [`docs/method/HARNESS.md`](docs/method/HARNESS.md).

**Contract check (harness maintenance).** Products may change their MCP reply formats without notice, so a contract check compares each read tool's reply shape with a committed baseline and tells us when the harness's parser needs updating. It was added on 5 November 2026, after the harness detected a change in one product's reply format and was updated ([details](docs/tracks/AGENT_INTERFACE.md#harness-maintenance-the-mcp-contract-check)).

**Free scorers.** Capture coverage aligns AMI's timed human words with the product transcript in 15-second windows. Word error rate (WER) and content-word recall compare the full transcript with AMI's. Fact-token recall checks numbers, money, names and roles. None of these uses an LLM, so they cost nothing and are deterministic. Track definitions: [`docs/tracks/`](docs/tracks/README.md).

**Judging.** Notes and answers need a reader who can decide whether a note or an answer states an annotated item. They were first scored by an automated LLM judge; they are now scored by agent readers, a method that a small human check supported. Why, how, and how far to trust it: [How results are judged](#how-results-are-judged).

**Human verification.** A human labels a sample of note claims, answers and notes-completeness items against the AMI source, on public AMI items so that labels can be released. These labels measure how far each automated reader over- or under-counts. Protocol and status: [`docs/method/HUMAN_VERIFICATION.md`](docs/method/HUMAN_VERIFICATION.md).

## How results are judged

Which reader decides whether notes and answers contain the annotated items, why it was changed on 9 November 2026, and how far to trust it.

**Why the first judge was replaced.** Notes and answers were first scored by an automated judge: Sonnet 5.5 called through the Anthropic API with a fixed rubric, one call per unit (3 passes for notes completeness and the council study, 1 elsewhere). A second reading found it wrong too often to report on its own. That reading was done by a Claude Code agent session (no API calls), which decided every item from the source material before opening the judge's verdict, and then compared the two:

| Study | Units re-read | Judge differs from the reference reading | Main causes |
|---|---|---|---|
| Notes completeness (E3) | 298 product-item pairs | 77 (26%; Cohen's kappa 0.55) | Missed content that is present; wanted problems framed as open questions; misapplied its own owner rules |
| List answers (E1) | 548 answer items | 66 (12%) | Missed matches with differently worded annotations (most often for Chat); credited the wrong kind of item |
| Deletion study | 340 answers | 109 (32%) | Penalised true extra details it could not check; did not map names to roles; matched loosely worded annotations literally |

On the 15 notes-completeness items where the judge and the second reading disagreed most informatively, a human labelled each item blind to both verdicts and to the product. The human sided with the second reading on all 15.

**The reader used now.** An agent reader: a Claude Code agent session (Opus 5.5; no calls through the project's API client) that works through one meeting and one product at a time from a fixed, code-built packet. The packet holds the annotated items, the linked passages of AMI's human transcript, the product's notes split into numbered bullets exactly as the first judge saw them, and the product's own transcript, with no earlier verdicts. The reader follows written instructions ([`prompts/notes_reader.v2.md`](prompts/notes_reader.v2.md)) and records, for each item: covered fully, partly or not; the bullets and a verbatim quote as evidence; whether the product's own transcript has the item; and a one-line reason. The instructions were tuned only on the development meeting (IS1008a) and frozen before any held-out item was read, once two independent reader sessions agreed on all 14 of its items (the previous version agreed on 12).

**Validation (notes completeness, 11 held-out meetings).**

| Comparison | Agreement |
|---|---|
| Frozen reader vs the second reading, 298 items | 269 (90.3%), kappa 0.82 (linearly weighted 0.86) |
| Frozen reader vs the human, 15 disputed items | 13 of 15 |
| Second reading vs the human, same 15 items | 15 of 15 |
| First judge vs the human, same 15 items | 0 of 15 |
| Frozen reader vs the first judge, 298 items | 219 (73.5%), kappa 0.54 |
| Majority of three full reader runs vs the second reading, 298 items | 278 (93.3%), kappa 0.87 (linearly weighted 0.91) |
| Majority of three full reader runs vs the human, 15 disputed items | 15 of 15 (not a clean held-out result; see below) |

The 15 human-labelled items were chosen because the two automated readings disagreed on them, so these are not error rates over all items; they show which reading to trust when the two disagree. Revised instructions (v3), written after two of the reader's misses on held-out items, agree with the second reading on 92.6% (kappa 0.86) and with the human on 13 of 15 (different misses). They are reported as a sensitivity check, not as the headline.

**Run-to-run variation and the three-run majority.** Three independent full reader runs on the held-out items (one with the frozen v2 instructions, two with v3) agree unanimously on 270 of 298 items (91%); any two runs differ on 15 to 22 items (5–7%), and at least one run differs from the others on 28 (9%). For this reason, statements about individual items use the majority (median) of the three runs, while the headline recall stays on the single run with instructions frozen before the held-out reading (v2). The majority agrees with the human on all 15 disputed items, but this is not a clean held-out result: v3's two rule changes were motivated by two of those items. v2 alone got 13 of 15.

For list answers (E1), the deletion study and the council notes, the reference verdicts come from blind agent readers (one session per meeting; in the deletion study the five systems' answers were shuffled and unlabelled), followed by an adjudication of every row where the reader and the judge disagreed. These references were not separately human-checked; they use the same kind of reading that the notes-completeness human check supported.

**Caveats.**
- **Same model family.** The readers and the first judge are all Claude models. Where they agree, errors they share would not show; the human check covers disagreements only.
- **Not seed-reproducible.** An agent session cannot be replayed byte for byte like a cached API call, and runs differ on some items: two runs with the same v3 instructions differ on 15 of 298 (5%), and any two of the three runs on 15 to 22. Item-level statements therefore use the three-run majority. Every verdict is therefore stored with its evidence quote, and every run with its instruction version, model, date and git commit, so any verdict can be checked by hand. Verdict files quote product outputs and stay local; scores and versions are published.
- **One human verifier, 15 items,** who also designed the study.

### Previously reported

Results first reported with the automated judge, beside the same results re-scored with the reference reading. The judge numbers stay in the result files and track pages; they are no longer the headline.

| Result | First judge (previously reported) | Reference reading (now) | What changed |
|---|---|---|---|
| Notes completeness, Granola / Wispr Flow | 0.62 / 0.69 | 0.72 / 0.77 | Both higher: notes leave out about a quarter, not about a third |
| Wispr Flow higher per meeting (notes completeness) | 10 of 11, p = 0.01 | 7 higher, 2 lower, 2 tied, p = 0.18 | No longer significant |
| Notes completeness: missing items that the product's transcript has | most (about 33 and 24 per pass) | 22 of 23 and 15 of 16 | Holds |
| E1 Granola Chat, recall / precision | 0.74 / 0.86 | 0.84 / 0.89 | Chat more complete than reported |
| E1 listing everything, recall / precision | 0.90 / 0.61 | 0.93 / 0.64 | |
| E1 listing vs Chat, recall | better on 8 questions, worse on 0, p = 0.008 | 5 / 0, p = 0.06 | Same direction, no longer significant |
| E1 listing vs Chat, F1 | 0.68 vs 0.71, "a tie" (8 / 16, p = 0.15) | 0.70 vs 0.80 (5 / 22, p = 0.002) | Flips: Chat better |
| E2 extras kept vs dropped, right (24 test questions) | 78% vs 37% | 78% vs 33% | Holds |
| Deletion, correct: Granola Chat / Granola's notes only | 41% / 21% | 60% / 32% | Levels higher; the loss holds |
| Deletion, correct: Granola's notes vs Wispr Flow's notes | 21% vs 12% (10 / 4) | 32% vs 34% (9 / 10) | A tie |
| Deletion, Chat vs notes plus on-demand transcript, not wrong | 17 / 6, p = 0.035 | 6 / 3, p = 0.51 | No difference |
| Deletion, same pair, correct | 9 / 3, p = 0.15 | 15 / 5, p = 0.04 (not significant after Holm correction) | Gap moves to fully correct answers |
| Deletion, confident errors: Granola's notes / plus facts / plus transcript | 9 / 8 / 15 | 2 / 1 / 1 | Answers over Granola's notes hedge rather than bluff |
| Council split vote, Granola's vote claim | not covered | partly covered | Two of the three named "no" votes match the record |

## Results

What each product did on the AMI meetings (7, or 11 held-out meetings for notes completeness), one measure per row, Granola first. *Preliminary* means small n or a method still changing; read it as a direction, not a ranking. Intervals in brackets are 95% confidence intervals (Wilson score intervals unless stated). Dated log of every result: [`docs/results/INDEX.md`](docs/results/INDEX.md).

![Where do Granola and Wispr Flow differ, and by how much? Dot plot of five measures both products have. Speech captured: Granola 98.5%, Wispr Flow 99.4% of 15-second windows. Note claims supported by both transcripts: 90% vs 81%. Answers from notes, scored by the first automated judge: correct or partly correct 50% vs 44%; correct 21% vs 12%. Word error rate: 16.6% vs 16.0%](docs/figures/results_overview.svg)

*Figure 2 (answers-from-notes rows: old judge). Where do Granola and Wispr Flow differ, and by how much? Each dot is one product's value on a measure both products have (Granola black, Wispr Flow teal); higher is better in the top panel and lower is better in the bottom one. Solid whiskers are 95% confidence intervals (Wilson score). Dotted whiskers are the lowest and highest meeting, used for speech captured and word error rate because windows and words cluster within meetings. n: speech captured, 665 15-second windows per product over 7 meetings; note claims, 216 (Granola) and 171 (Wispr Flow) over 7 meetings; answers from notes, the same 68 questions over 5 meetings, answered by our reader from each product's notes; word error rate, 28,581 reference words over 7 meetings, pooled. **The answers-from-notes rows show the first automated judge (old judge)**, with its paired sign tests: Wispr Flow was better on 7 questions and worse on 11 for correct or partly correct (p = 0.48), and better on 4 and worse on 10 for correct (p = 0.18). The reference reading gives 65% vs 54% correct or partly correct (Wispr Flow better on 3, worse on 10, p = 0.09) and 32% vs 34% correct (better on 10, worse on 9, p = 1.0); no difference is significant under either ([How results are judged](#how-results-are-judged)). Note claims differ between the products, so they have no paired test. Data: AMI Meeting Corpus.*

Measures only one product has (Granola Chat answers; Wispr Flow's MCP has no question-answering tool) are left out of Figure 2 and given in the tables below.

### Capture

Did each product record the whole meeting? We split AMI's human transcript into 15-second windows and count a window as captured when at least 40% of its distinctive word pairs appear in the product's transcript.

![How much of what was said did each product capture? Line chart over one 30-minute AMI meeting: the share of speech captured in each 15-second window by Granola and Wispr Flow stays mostly above the 40% threshold and dips at the same moments, while a different meeting's transcript, used as a control, stays below it](docs/figures/capture_coverage.svg)

*Figure 3. How much of what was said did each product capture? Share of AMI's human-transcribed speech found in each product's transcript, per 15-second window, over one meeting (EN2002b, the one naturally occurring meeting, 30 minutes). A window counts as captured above the dashed 40% line. The dotted grey line scores an unrelated meeting's human transcript (IB4003) as if it were a product's, as a control: 0.9% of its windows pass. In this meeting Granola captured 93.6% of windows and Wispr Flow 96.4%; over all 7 meetings, 98.5% and 99.4% of 665 windows each. Data: [`evals/reports/ami/coverage.json`](evals/reports/ami/coverage.json).*

| Measure | Granola | Wispr Flow | n | Status |
|---|---|---|---|---|
| 15-second windows of speech captured (%) | 98.5% | 99.4% | 665 windows per product, 7 meetings | Preliminary |
| Lowest and highest meeting (%) | 93.6–100% | 96.4–100% | 7 meetings | Preliminary |
| Gaps of 30 seconds or more (count) | 1 (51 s) | 0 | 7 meetings | Preliminary |
| Silent capture failures (count) | 0 | 0 | 7 meetings | Preliminary |

Windows cluster within meetings, so no pooled interval is given. Granola's one gap falls where Wispr Flow's coverage also dips, during overlapping speech. As a control, an unrelated meeting's transcript passes only 0–2% of windows, so the 40% threshold separates capture from chance.

Both products captured almost all of every meeting under normal conditions. Silent failure under stress, the capture question that matters most to users, is not yet tested ([next research step](#capture-under-stress)). Track: [`docs/tracks/CAPTURE.md`](docs/tracks/CAPTURE.md).

### Transcription

How accurate is each product's transcript? We compare it with AMI's manual transcript using word error rate (WER: word edits needed to turn one into the other, divided by the number of reference words) and content-word recall (the share of AMI's content words that appear anywhere in the product transcript).

| Measure | Granola | Wispr Flow | n | Status |
|---|---|---|---|---|
| WER, all meetings pooled (%, lower is better) | 16.6% | 16.0% | 7 meetings, 28,581 reference words | Preliminary |
| WER, lowest and highest meeting (%) | 9.3–28.6% | 8.0–26.4% | 7 meetings | Preliminary |
| Content-word recall, pooled (%, higher is better) | 87.8% | 90.0% | 7 meetings | Preliminary |
| Content-word recall, lowest and highest meeting (%) | 77.7–94.4% | 82.9–96.9% | 7 meetings | Preliminary |

Pooled values divide total errors by total words, so longer meetings weigh more. Granola's microphone turns are dropped before scoring (4 turns in 2 meetings); Wispr Flow has no separate microphone channel, so its whole transcript is scored. No significance test was run.

The two products are within 0.7 points on pooled WER: Wispr Flow was lower on 4 meetings, Granola on 2, and 1 was tied. Wispr Flow's content-word recall was higher on all 7 meetings, by 0.5 to 5.2 points. Both did worst on EN2002b, the one naturally occurring meeting. Because AMI is a standard speech benchmark that product models may have seen, these figures are a best case, not a claim about everyday accuracy. Track: [`docs/tracks/TRANSCRIPTION.md`](docs/tracks/TRANSCRIPTION.md); numbers: [`evals/reports/ami/wer.json`](evals/reports/ami/wer.json).

### Notes

Two questions: are the claims in each product's notes backed by what was said, and do the notes contain what was decided, assigned and raised ([Notes completeness](#notes-completeness))? For the first, we split the notes into single claims and check each one against both the product's own transcript and AMI's human transcript with an automated checker; a human checks a sample.

| Measure | Granola | Wispr Flow | n | Status |
|---|---|---|---|---|
| Note claims supported by both transcripts (%) | 90% [86, 94] (195 of 216) | 81% [74, 86] (138 of 171) | 387 claims, 7 meetings | Preliminary |
| Human check of sampled claims | 12 of 20 could be judged; 0 wrong, 1 went beyond what was said | (pooled with Granola) | 20 claims | Preliminary |

The first row is from the corrected automated run, which replaces a first pass (94% and 80%). The human sample pools both products and was drawn mostly from claims the checker had flagged.

Granola's notes were supported 9.6 points more often (95% CI 2.5 to 16.7 if claims were independent; the true interval is wider, since claims cluster in 7 meetings). Granola was higher in 4 meetings, Wispr Flow in 2, and 1 was tied. About half the gap comes from claims on which the two checks differed by one step (one said supported, the other said inferred); counting those as supported gives 95.8% and 91.8%. Few claims were clearly added by the note-writer: 0 of 216 for Granola and 2 of 171 for Wispr Flow. Human calibration is still pending. Full breakdown by claim type: [`docs/tracks/NOTES.md`](docs/tracks/NOTES.md#ami-claim-types); method: [`docs/method/WRONG_FACTS.md`](docs/method/WRONG_FACTS.md).

#### Notes completeness

Do the notes contain what the meeting decided, assigned and raised? AMI's annotators wrote one sentence for each decision, action and problem in a meeting. For each of these items the reader decides whether the product's notes cover it fully, partly or not at all, and whether the product's own transcript contains it. For an action, the owner is part of the item: a task with no owner, or with a label nobody can match to a person, is at most partly covered. 11 held-out meetings, 149 items per product. A twelfth meeting (IS1008a) was used to write the reader's instructions and is not scored.

| Measure | Granola | Wispr Flow | n | Status |
|---|---|---|---|---|
| Items covered (full = 1, partly = 0.5) | 0.72 [0.67, 0.77] | 0.77 [0.71, 0.82] | 149 items, 11 meetings | Preliminary |
| Decisions / actions / problems covered | 0.81 / 0.61 / 0.62 | 0.89 / 0.57 / 0.74 | 65 / 47 / 37 items | Preliminary |
| Items fully covered | 0.58 [0.51, 0.64] | 0.65 [0.55, 0.74] | 149 items | Preliminary |
| Items the product's own transcript contains | 145 (3 more partly) | 146 (2 more partly) | 149 items | Preliminary |
| Items missing from the notes outright; of those, in the transcript | 23; 22 (2 only partly) | 16; 15 (1 only partly) | 149 items | Preliminary |
| Same, three-run majority (used for item-level statements) | 24; 23 (2 only partly) | 19; 18 (1 only partly) | 149 items | Preliminary |
| Sensitivity: revised reader instructions (v3, written after the held-out reading) | 0.73 | 0.77 | 149 items | Preliminary |
| Robustness: majority of three full reader runs | 0.72 [0.66, 0.78] | 0.78 [0.72, 0.83] | 149 items | Preliminary |
| Sensitivity: actions whose only gap is the owner counted as full | 0.76 | 0.83 | 149 items | Preliminary |

Values are means over meetings with 95% bootstrap intervals (resampling meetings, seed 42). The headline uses the reader instructions frozen before the held-out meetings were read (v2). The first automated judge gave 0.62 and 0.69 ([Previously reported](#previously-reported)).

**Notes leave out about a quarter of what was decided, assigned or raised, while the products' own transcripts contain almost all of it** (291 of 298 product-item pairs fully, 5 partly; the same in the three-run majority). Of the items missing from the notes outright, all but one per product were in the product's transcript. What the notes leave out is a note-writing gap, not a transcription gap. Actions are the weakest category for both products. Wispr Flow covered more on 7 meetings, less on 2, and the same on 2 (sign test p = 0.18), so the difference between the products is not established.

**Owners explain much of the action gap, and they may be understated for Granola.** Granola's notes state no owner on 18 of 40 action bullets; Wispr Flow names an owner on almost every bullet, but 14 of 30 are "Speaker N" labels that a reader cannot resolve ([owner check](docs/tracks/NOTES.md#owner-check-wrong-owners-on-action-items-preliminary)). In replay there is no calendar invite and all audio arrives on the system channel, so Granola cannot use its usual sources of names; in real meetings its owner credit may be higher. Precision of the notes' bullets was scored only by the first judge (0.94 and 0.96 of decision, action and problem bullets right) and has not been re-read. Track: [`docs/tracks/NOTES.md`](docs/tracks/NOTES.md#e3-notes-completeness-preliminary).

### Answers

When asked to list a meeting's decisions, actions or problems, how many of the right items does an answer contain, and how many of its items are true? We score each listed item against AMI's annotated items and its transcript. Wispr Flow's MCP has no question-answering tool, so this section covers Granola only.

| How the answer was produced | Annotated items found (recall) | Listed items that are true (precision) | F1 | Items per answer | n | Status |
|---|---|---|---|---|---|---|
| Granola Chat, transcripts kept | 0.84 | 0.89 | 0.80 | 3.2 | 30 questions | Preliminary |
| Our reader on Granola's notes only | 0.79 | 0.74 | 0.69 | 3.1 | 30 questions | Preliminary |
| Our reader listing every item from Granola's full transcript | 0.93 | 0.64 | 0.70 | 11.9 | 30 questions | Preliminary |

Values are means per question, scored by the reference reading ([How results are judged](#how-results-are-judged)); bootstrap intervals have not been recomputed for it. An item counts as true if it matches an annotated item or is confirmed by AMI's transcript, so true items the annotators left out are not counted wrong. F1 combines the two. Paired sign tests per question (n = 30): listing every item found more annotated items than Chat on 5 questions and fewer on 0 (p = 0.06, not significant), had a lower share of true items on 26 and a higher share on 2 (p < 0.001), and had a lower F1 on 22 and a higher F1 on 5 (p = 0.002). Against notes only, it found more items on 7 questions and fewer on 0 (p = 0.016); the share of true items did not differ significantly (10 higher, 18 lower, p = 0.18). Chat had a higher share of true items than notes only on 14 questions and a lower share on 2 (p = 0.004). The first automated judge had given Chat 0.74 recall and 0.86 precision, and listing 0.90 and 0.61, and had called the F1 comparison a tie ([Previously reported](#previously-reported)).

**Chat is precise and finds most items; listing everything finds a few more but is much less precise, and Chat has the better balance.** The extra items from the full listing were over-inclusion, not invention: every one of its 357 quotes appears in the transcript, and in a sample of 8 items marked wrong the reasons were "proposed but not decided", "not assigned" or "another topic". In a hand reading of Chat's imperfect answers by one coder, most real shortfalls were missing items and none was invented content. A cheap "was it agreed?" check on the listed extras keeps items that are right 78% of the time and drops items that are right 33% of the time (24 test questions; [E2](docs/tracks/ANSWERS.md#e2-results-preliminary)). Track: [`docs/tracks/ANSWERS.md`](docs/tracks/ANSWERS.md#e1-results-preliminary); judge numbers: [`evals/reports/ami/list_check.json`](evals/reports/ami/list_check.json).

#### How far to trust the automated judges

Do automated "wrong" verdicts survive a human reading? A human labelled a sample of outputs the automated judges had flagged, against the AMI source.

![When an automated judge says "wrong", does a human agree? Stacked bars of human verdicts on 23 flagged outputs: of 20 note claims, the human found 11 supported, 1 going beyond what was said and none wrong, and could not judge 8; all 3 Chat answers the judge called confidently wrong were partly right](docs/figures/judge_vs_human.svg)

*Figure 4. When an automated judge says "wrong", does a human agree? Each bar groups outputs by the automated verdict; its segments show what one human verifier decided, with counts. n = 23 human labels: 20 note claims sampled from both products' notes, mostly from claims the checker had flagged, and the 3 Chat answers the automated judge called most confidently wrong. Note-claim verdicts are from the first checking pass, on which the sample was drawn. No interval is shown: counts this small give a direction, not a rate. The verifier also designed the study.*

| Measure | Granola | Wispr Flow | n | Status |
|---|---|---|---|---|
| Of the 3 strongest automated "confident contradictions" in Chat answers, wrong on human check | 0 of 3 | no question tool | 3 answers | Preliminary |
| Note claims a human found wrong, of those comparable with the checker | 0 of 12 | (pooled with Granola) | 12 claims | Preliminary |
| Notes-completeness items where the judge and the second reading disagreed: human sided with the judge | 0 of 15 | (pooled with Granola) | 15 items | Preliminary |

All 3 Chat answers were partly right. On the note claims, the human agreed with the checker on 4 of 12 for the first pass (on which the sample was drawn) and 7 of 12 for the corrected run. The 15 notes-completeness items came after Figure 4 and are not in it; the human answered each before seeing either verdict, and without knowing which product wrote the notes.

**The first automated judge over-counted errors, and also missed content that was present.** In every human-checked sample, most of its error verdicts did not survive a human reading. With 38 labels this is a direction, not a rate. Notes and answers are now scored by the agent reader described in [How results are judged](#how-results-are-judged); the grounding checker behind the note-claim figures has not been re-read. Track: [`docs/tracks/ANSWERS.md`](docs/tracks/ANSWERS.md).

### Retention: what deleting transcripts costs

If a product keeps only its notes and deletes transcripts, how many questions can still be answered? We answer the same 68 AMI questions (5 meetings) five ways, each over the question's own meetings, and score each answer correct, partially correct or wrong against AMI's human summaries. Scores below are the reference reading ([How results are judged](#how-results-are-judged)); the first automated judge's scores are in Figure 5 and under [Previously reported](#previously-reported).

![What does deleting transcripts cost, and what wins it back? Dot plot of 68 AMI questions answered five ways, scored by the first automated judge. Correct: Granola Chat with transcripts 41%, from Granola's notes 21%, from Wispr Flow's notes 12%, notes plus extracted facts 19%, notes plus the transcript when needed 32%](docs/figures/ami_retention.svg)

*Figure 5 (old judge). What does deleting transcripts cost, and what wins it back? Share of 68 AMI questions (5 meetings) answered correctly (filled dots) and correctly or partly correctly (open dots), five ways, **as scored by the first automated judge**; the reference reading in the table below is higher on every row and changes two comparisons (Granola's vs Wispr Flow's notes; Chat vs the on-demand transcript). Whiskers are 95% confidence intervals (Wilson score). Granola Chat answers with Granola's own model and its transcripts. Every other row uses our reader: over Granola's notes (black) or Wispr Flow's notes (teal), then Granola's notes plus extracted facts, then those plus the transcript, read only when the answer abstains or hedges.*

| How the answer was produced | Correct (%) | Correct or partly correct (%) | Confident errors | n | Status |
|---|---|---|---|---|---|
| Granola Chat, transcripts kept | 60% [48, 71] | 90% [80, 95] | 7 | 68 questions | Preliminary |
| Our reader on Granola's notes only | 32% [22, 44] | 65% [53, 75] | 2 | 68 questions | Preliminary |
| Our reader on Wispr Flow's notes only | 34% [24, 46] | 54% [43, 66] | 9 | 68 questions | Preliminary |
| Granola's notes plus an extracted fact layer | 32% [22, 44] | 69% [57, 79] | 1 | 68 questions | Preliminary |
| Notes plus facts, reading the transcript only when the answer abstains or hedges | 46% [34, 57] | 85% [75, 92] | 1 | 68 questions | Preliminary |

A confident error is a wrong answer that does not abstain or say the material does not cover the question; Granola Chat has no abstain signal, so every wrong Chat answer counts. Paired sign tests, as questions gained–lost, correct / correct or partly correct: Granola's notes only vs Chat, 1–20 (p < 0.001) / 0–17 (p < 0.001); Wispr Flow's notes vs Granola's notes, 10–9 (p = 1.0) / 3–10 (p = 0.09); adding facts to Granola's notes, 1–1 / 3–0 (neither significant); reading the transcript when needed vs notes plus facts, 10–1 (p = 0.012) / 12–1 (p = 0.003); Chat vs reading the transcript when needed, 15–5 (p = 0.04) / 6–3 (p = 0.51). With Holm correction across the five tests per measure, the deletion loss and the on-demand transcript's gain stay significant on both measures; Chat vs the on-demand transcript does not.

**Deletion costs a lot; the fact layer adds nothing; reading the transcript on demand recovers most of the loss.** Chat used Granola's own model and the other rows used our reader, so the first drop mixes deletion with a change of model. With the transcript read on demand, answers are as often correct or partly correct as Chat's (85% vs 90%, no significant difference), though fewer are fully correct (46% vs 60%). Answers over Granola's notes rarely give a confident wrong answer (1–2 per 68), so they hedge rather than bluff; answers over Wispr Flow's notes do so more often (9). Granola's and Wispr Flow's notes support about the same number of correct answers. Decision questions score low everywhere because the answer key is strict and several decision annotations are wrong. The first judge had reported a significant gap between Chat and the on-demand transcript and a point-estimate lead for Granola's notes; neither holds under the reference reading. Track: [`docs/tracks/RETENTION.md`](docs/tracks/RETENTION.md); judge numbers: [`evals/reports/ami/deletion_ami.json`](evals/reports/ami/deletion_ami.json).

### Earlier private study

Before the AMI work, the same measures were run on the author's own 4 private Granola meetings (October–November 2026). These numbers are **not reproducible from public data**, cover Granola only, and are superseded wherever an AMI replication exists.

| Measure | Granola | n | AMI replication |
|---|---|---|---|
| Note claims supported / inferred / unsupported (%) | 84% [77, 89] / 14% / 0% | 132 claims | Superseded by [Notes](#notes) |
| Answers judged useful and comprehensive (%) | 89% [77, 95] | 47 questions | None yet |
| Answers that point inside a meeting (line, chunk or timestamp) | 0 of 47 | 47 questions | None yet |
| Correct answers: Granola's agent / notes only / plus facts / plus on-demand transcript (%) | 89% / 57% / 66% / 87% | 47 questions | Superseded by [Retention](#retention-what-deleting-transcripts-costs) |

All questions were used during development. Notes only vs Granola's agent: 2 gained, 17 lost (p = 0.001); the fact layer's gain was not significant. Granola's answers linked to whole meetings only and carried no abstain or coverage field. Details: [`docs/results/TENOR_TECHNICAL_REPORT.md`](docs/results/TENOR_TECHNICAL_REPORT.md), [`docs/tracks/NOTES.md`](docs/tracks/NOTES.md), [`docs/tracks/AGENT_INTERFACE.md`](docs/tracks/AGENT_INTERFACE.md).

## Capture under stress

*Does a product keep recording through conditions that break capture in real use, and does it tell the user when it stops?* Silent capture failure (a recording that appears to run but misses part of the meeting) was the most common theme in a 2026 sample of public user reports about meeting note-takers (about 16 of 75 items, from one forum and mostly one product's users). Normal replays showed no failures in either product ([Capture](#capture)), so each stress condition replays a known AMI meeting into both products at once and scores capture before and after the stressor with the free coverage scorer. Full design and run sheet: [`docs/tracks/CAPTURE.md`](docs/tracks/CAPTURE.md).

### Long silence (A1)

Meeting IS1008d with digital silence inserted at 12 minutes: 17 minutes of silence in run 1, 19 minutes in run 2. Microphone input was held at zero; neither product transcribed any microphone speech in either run.

| Run | Product | Warning during the silence | Speech captured after the silence (15-second windows) | What happened |
|---|---|---|---|---|
| 1 (17 min) | Granola | Yes: an "are you still there?" prompt with a 3-minute countdown, about 14½ minutes into the silence | Resumed immediately | Not answered; speech returned before the countdown ended. Later windows are missing only because the run was stopped early |
| 1 (17 min) | Wispr Flow | None seen | 0% | The app installed an automatic update and restarted about 10 minutes into the silence, which ended the recording; nothing after it was captured |
| 2 (19 min) | Granola | Yes, same prompt | 100% of 46 windows | The prompt was answered |
| 2 (19 min) | Wispr Flow | Yes, a prompt | 100% of 46 windows | The prompt was answered |

*Note.* Two runs on one meeting; read as observations, not rates. In run 2 both prompts were answered by the person running the test, so run 2 shows the behaviour when a user is present. The restart in run 1 is confirmed by the update installer's own log; whether it was triggered by the silence or would have happened at any time is unknown. Not yet tested: what a product does when its countdown runs out with nobody present (planned: 20 or more minutes of silence, nobody at the machine). Numbers: [`evals/reports/ami/stress_A1_IS1008d_run1.json`](evals/reports/ami/stress_A1_IS1008d_run1.json), [`run2`](evals/reports/ami/stress_A1_IS1008d_run2.json).

**What this shows so far.** Both products warn during a long silence and resume when speech returns, so silence alone did not cause a silent failure. The one loss came from outside the meeting: an application update ended an active recording without a visible warning. Conditions of that kind (another app in front, system sleep, network loss) are the next ones to test.

### Planned conditions

| Condition | What it tests |
|---|---|
| A1, long silence with nobody present | Whether capture ends for good when a prompt goes unanswered |
| A2, two meetings in one recording | Whether content from one meeting is written into the other |
| A3, second recording started late | Whether the opening is missed, and which note gets the overlap |
| A4, app in the background or on another desktop | Capture when the app is not visible |
| A5, display sleep or lid closed | Capture across system sleep |
| A6, network drop for 60 seconds | Behaviour while offline |

### Detecting a stopped transcript

*Could a product tell, while recording, that its transcript has stopped growing?* A prototype detector compares two signals the product already has, speech in the incoming audio and new transcript text, and alerts when there is speech but no new text. On Granola transcripts with failures inserted artificially (237 failures across 6 held-out meetings, 2.7 hours), it caught every failure with no false alarms when given an exact speech signal, and 234 of 237 with about one false alarm per hour using a simple loudness-based speech signal. It cannot detect a failure in the audio input itself, because it listens to the same input. Details: [`docs/tracks/CAPTURE_HEALTH.md`](docs/tracks/CAPTURE_HEALTH.md).

![Can a product tell when its transcript has stopped? Timeline of one simulated failure: speech continues while new transcript text stops for 118 seconds, and four detector variants raise an alert 13 to 33 seconds after it starts](docs/figures/capture_health.svg)

*Figure 6. Can a product tell when its transcript has stopped? One simulated failure in a held-out AMI meeting (IS1008b, minutes 20.5–25). The top two rows show when speech is present, from AMI's word timings (exact) and from audio loudness. The third row shows when transcript text arrives; the text that arrived in the shaded 118 seconds was removed (grey dashed marks) to simulate the failure. The bottom four rows show when each detector variant raises its alert, labelled with the seconds since the failure began. Variants differ in the speech signal they use and in whether they watch for new transcript blocks or for estimated live words. Totals over 237 simulated failures are given in the text above.*

### Nightly replay

The same harness, run every night on a fixed, versioned set of public meetings, becomes a regression test: capture coverage, word error rate and fact recall per product, compared with the previous night and with human ground truth. A separate contract check keeps the harness in step with each product's MCP reply format; it maintains the harness and does not measure the product.

## Design implications for note-takers

What TENOR's evidence could mean for product design (capture-health indicators, completeness prompts, marking inferences, reconciling numbers and names across sources without storing audio, and more) is discussed product-neutrally in [`docs/product/IMPLICATIONS.md`](docs/product/IMPLICATIONS.md). Each item gives the evidence, what a product could do, how TENOR would measure it, and why a product might reasonably not do it.

## Limitations

What these results do and do not show.

- **Mostly role-play meetings.** Six of the first seven AMI meetings are role-play recorded around 2005 (five design-team scenario meetings and one staged meeting), mostly by non-native English speakers; one (EN2002b) is a naturally occurring meeting. The six meetings added for notes completeness are the same design-team scenario. Results describe this material, not typical work calls.
- **Replay is not a live call.** Audio enters digitally as clean 16 kHz system audio, with no calendar invite, attendee names, call compression or meeting-app context. The comparison between products is fair (same input, same time); absolute numbers describe this condition only. A call-path condition is planned.
- **Silent replay, no typed notes.** Nobody typed during the replays. Products that blend the user's typing into their notes are measured in their least favourable mode, so notes results are a lower bound for an engaged user. A typed-vs-silent test is designed.
- **One human verifier,** who also designed the study. Agreement between humans is unmeasured until a second verifier labels a shared subset.
- **Summary-level answer key.** Most gold answers are AMI annotators' summary sentences, which can drop nuance a product keeps, and a few are wrong (for example, two swapped task owners). The readers check items against AMI's transcript as well, and corrections are kept in a versioned errata file.
- **Automated reader, small human check.** Notes and answers are scored by an agent reader of the same model family as the judge it replaced, checked against 15 human labels on disputed items only. Its verdicts are not seed-reproducible; each is stored with its evidence and versions instead ([How results are judged](#how-results-are-judged)).
- **Small n.** 7 to 11 meetings per study, 68 questions, 38 human labels. Most differences between products are within their intervals; where a difference is not significant, it is reported as a tie.
- **Contamination.** AMI has been public for about 20 years and is a standard speech benchmark; product and judge models may have seen it. WER is reported as a best case, and a closed-book control for questions is part of the protocol (not yet run).
- **Account and settings effects.** Both products ran on the author's main accounts (one product accepts only work accounts); personal dictionaries are cleared for scored runs, but voice profiles and account names could not be removed.
- **Terms-of-service constraints.** Some products' terms restrict publishing comparative results without consent; those products are excluded for now (see [Reproducing](#reproducing)), which limits how general the comparison can be.

## Reproducing

How to rerun every free measure, and what the paid ones cost.

**Setup.** Python 3.11 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                                   # install (locked)
uv run pytest                                             # offline tests, no network, no API spend
uv run tenor figures 20261103T002456Z --check             # regenerate every figure and compare
```

**Replaying AMI and scoring it.** Download the AMI files listed in [`docs/method/AMI_REPLAY.md`](docs/method/AMI_REPLAY.md#8-exact-files-to-download) into `data/ami/` (checksums in [`data/ami/MANIFEST.json`](data/ami/MANIFEST.json)), play each into the products as described there, then sync and score. Synced products stay in local, gitignored SQLite files.

```bash
uv run tenor sync --source granola-mcp --db data/ami_granola.db         # read-only, browser sign-in
uv run tenor sync --source wispr-mcp --db data/ami_wispr.db
uv run tenor ami status                                                 # ground truth and recordings found
uv run tenor ami coverage                                               # capture track, $0
uv run tenor ami wer --db data/ami_granola.db --product granola         # transcription track, $0
uv run tenor ami wer --db data/ami_wispr.db --product wispr --keep-mic        # Wispr Flow has no mic channel
uv run tenor facts stage1                                               # fact-token recall, $0
uv run tenor facts stage2 --quote                                       # notes grounding: price first
uv run tenor mcp-contract                                               # reply-format check (harness)
```

**Costs.** Capture, WER, fact recall and the contract check make no LLM calls. Judged steps need `ANTHROPIC_API_KEY` in `.env` (see `.env.example`): notes grounding about $0.10 for all 7 meetings (measured); answer attribution at most about $0.07; the AMI retention replication $5.64 (run; quoted at about $8). Every paid command prints an exact quote first and asks before spending more than $2.

**Product plans.** Reading Granola transcripts through its MCP requires a paid plan (a free trial covers it), and Granola accepts work accounts only. Wispr Flow needs its Notetaker, MCP access and cloud sync enabled; plan requirements beyond that have not been verified.

**Products excluded for now.** Notion, tl;dv and products reached through Zoom's API have terms that require consent before comparative results are published, so they are not included.

**Your own meetings.** The black-box kit runs the question tracks on your own account, locally, with only content-free scores leaving your machine: [`docs/method/BLACKBOX.md`](docs/method/BLACKBOX.md).

## Roadmap to v1

What is needed before TENOR results can be compared across products and dates.

1. **More products,** starting with Fireflies, Circleback and Otter, subject to each product's terms.
2. **A frozen, versioned meeting set,** so that results from different dates and products are comparable, and nightly replay can detect regressions.
3. **A public leaderboard,** per track, with intervals and status labels, never a single combined score.
4. **A vendor response and dispute process:** products are shown their results before publication, can point out errors in method or data, and their responses are published alongside.
5. **A technical report** covering the AMI results once the agent reader has been checked against about 100 human labels and the capture stress tests are complete.

## Repository guide

Where each part of the project lives.

| Path | What it holds |
|---|---|
| [`docs/`](docs/README.md) | Method, tracks, dated results, product implications, engineering notes, archive ([map](docs/README.md)) |
| [`packages/tenor/`](packages/tenor/) | The Python package and `tenor` CLI (`concentrate` still works as a deprecated alias): sources (read-only MCP clients), AMI loaders and scorers, evals, figures |
| [`evals/reports/`](evals/reports/) | Committed result files: numbers and IDs only. Older files use the earlier project name |
| [`data/`](data/README.md) | AMI manifest and AMI-derived questions (committed); synced products and private data (gitignored) |
| [`prompts/`](prompts/) | Versioned judge and checker prompts |
| [`tests/`](tests/) | Offline tests; HTTP and MCP are faked |
| [`web/`](web/) | A local web viewer for results (views of the archived Concentrate memory system) |

Contributing a product or system: [`CONTRIBUTING.md`](CONTRIBUTING.md). Reporting a problem: [`SECURITY.md`](SECURITY.md).

## Related work

TENOR sits between model benchmarks for meetings (QMSum, MeetingQA, ELITR-Bench, MeetBench-XL, MeetingProbe), memory benchmarks over chat (LongMemEval, LoCoMo, BEAM) and claim-level faithfulness checking (FActScore, MiniCheck). What it adds is measurement of shipped products, end to end, on the same public meetings, through the interfaces their users and agents use. Table, gap and references: [`docs/RELATED_WORK.md`](docs/RELATED_WORK.md).

## Licence

How the code, data and derived files may be reused.

Code is [MIT](LICENSE). The synthetic Northwind data under `data/fixtures/` is CC BY 4.0 ([`data/README.md`](data/README.md#license)). AMI-derived files (questions, labels, scores) are adaptations of the AMI Meeting Corpus (CC BY 4.0) and carry the attribution below. Product outputs are not redistributed beyond short quoted examples beside the AMI source.

## Data attribution

The required attribution for the AMI Meeting Corpus.

> Uses the AMI Meeting Corpus (University of Edinburgh and AMI consortium partners), licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/), https://groups.inf.ed.ac.uk/ami/corpus/. Changes: the audio was played in real time into third-party meeting products, and questions were derived mechanically from the manual annotations. Citation: J. Carletta et al., "The AMI Meeting Corpus: A Pre-announcement", MLMI 2005, LNCS 3869, pp. 28–39.

## Not affiliated with Granola or Wispr

TENOR is independent research. It is not affiliated with, endorsed by or funded by Granola or Wispr. Everything reported about either product is observable through its public interfaces, using the author's own accounts; no claim is made about either product's internals. Product names are used only to identify what was measured.

## Citation

How to cite TENOR.

```bibtex
@software{lakhani2026tenor,
  author = {Lakhani, Kian},
  title  = {TENOR: an open benchmark for meeting note-takers (Transcripts, Evidence, Notes, Outcomes, Recall)},
  year   = {2026},
  url    = {https://github.com/KLKH7/TENOR},
  note   = {Research prototype}
}
```

See also [`CITATION.cff`](CITATION.cff).
