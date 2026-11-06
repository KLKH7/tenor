# Answers (Chat) track

This page defines the answers track: whether a product's answers to questions about a meeting are right, complete and honest about what they do not know.

## Question

Users ask their note-taker about past meetings ("what did we decide about the price?", "who owns the follow-up?"). An answer that is wrong but confident is worse than one that says it does not know, and an answer that is right but incomplete is common and easy to miss.

## How it is measured

| Step | What happens |
|---|---|
| **Questions** | Built by fixed templates from AMI's human annotations (decisions, actions, problems, who proposed what, when it was decided, what changed, unanswerable controls); no LLM writes a question or a gold answer ([`AMI_REPLAY.md`](../method/AMI_REPLAY.md#6-question-construction-from-annotations-no-llm-drafting), [`data/ami_questions/`](../../data/ami_questions/README.md)) |
| **Asking** | Each product's own question tool, scoped to the question's meetings, through a read-only allowlist ([`HARNESS.md`](../method/HARNESS.md)) |
| **Judging** | First an automated LLM judge (`judge.v1`); then the calibrated judge, with a speaker roster, product boilerplate stripped, a stronger model and per-item scoring for list answers ([`CALIBRATED_JUDGING.md`](../method/CALIBRATED_JUDGING.md)) |
| **Answer types** | Correct; partial; incomplete and clearly flagged; wrong but confident ([`AGENT_INTERFACE.md`](AGENT_INTERFACE.md)) |
| **Human check** | A sample of judge verdicts is labelled by a human to measure how far the judge over- or under-counts ([`HUMAN_VERIFICATION.md`](../method/HUMAN_VERIFICATION.md)) |

## Current results

| Measure | Granola | Wispr Flow | n | Notes | Status |
|---|---|---|---|---|---|
| Judged wrong, automated judge | 16 (24%, 95% CI 15–35%) | not applicable | 68 questions | Of the 3 strongest "confident contradictions", the human verifier found 0 clearly wrong (all partly right) | Preliminary; over-counts errors |
| Judged partial, automated judge | 24 (35%, 95% CI 25–47%) | not applicable | 68 | Partial = some gold items missing or imprecise | Preliminary |

- **Wispr Flow has no question-answering tool in its MCP,** so there is no Wispr Flow row for the product's own answers. A comparison through a fixed outside agent over each product's read tools is built and not yet run ([`HARNESS.md`](../method/HARNESS.md#the-standard-outside-agent-agent-interface-track)).
- **The automated numbers are an upper bound on errors.** The causes of the over-count (names vs roles, product boilerplate, a weak judge, a summary-level answer key) are listed in [`CALIBRATED_JUDGING.md`](../method/CALIBRATED_JUDGING.md). Calibrated figures will replace these once the judge is checked against at least 100 human labels.
- **Cross-meeting questions were often partial,** and in one case an answer merged two separate statements (first reading of the answers; not yet quantified).
- **Earlier result, superseded:** on the author's 4 private meetings (39 DEV questions, LLM-drafted gold approved by the owner), Granola's agent scored 92% [82, 100] ([`results/REAL_SLICE.md`](../results/REAL_SLICE.md)).

## Status and caveats

- **Preliminary.** One run, one automated judge, 3 human-checked answers.
- **Summary-level answer key.** Most gold answers are AMI summary sentences, which drop nuance a product may keep; a product can be "partial" against the key and right against the transcript.
- **Contamination.** AMI has been public for about 20 years; a closed-book control (the same questions with no meeting access) is part of the protocol and has not run yet.

## E1: list then check (designed; pilot run)

**Question.** Most real shortfalls on list questions are missing items. Does listing every item from the transcript, then checking each one against its quote, raise recall without adding wrong items?

| Condition | What answers |
|---|---|
| **C** | Granola Chat with transcripts kept: its recorded answers, scored item by item (the fair baseline) |
| **N** | Granola's notes only (`answer.v1`; the deletion run's `notes_only`) |
| **L** | List every item from Granola's transcript: map over ~3k-token chunks (`enumerate.v1`, each item with a verbatim quote and line numbers), then merge duplicates (`enumerate_merge.v1`) |
| **LD** | L, then one verifier call per item against its quoted lines (`verify_item.v2`); unsupported items dropped |
| **LM** | Same check; unsupported items kept and marked "unconfirmed" |

- **Slice.** The 30 list questions (decision, action_what, problem) among the 68 the deletion run used (meetings recorded by Granola and Wispr). Each condition reads only the question's own meeting.
- **Gold.** One item per AMI human DECISIONS / ACTIONS / PROBLEMS sentence behind the question, plus the speaker roster.
- **Scoring is per item, not one label.** A Sonnet judge (`judge_items.v1`) maps each answer item to the gold items it states and checks it against AMI's reference transcript. Recall = gold items covered. Precision is reported twice: strict (states a gold item) and lenient (gold, or true per the transcript), so true items the gold left out are not counted wrong. F1 of each.
- **LD and LM are scored from L's item judgments** (one judge call, not three), so their difference from L is the filter alone, not judge noise. LM lists the same items as L, so its recall and precision equal L's by construction; its test is whether "unconfirmed" items are less often right than confirmed ones.
- **Tests.** Paired sign tests per question: L vs C and LD vs C (headline), then L vs N, LD vs L, LM vs L, LM vs LD, on recall and both precisions. With 30 questions only large effects can reach significance.
- **Known limits.** Granola's AMI transcripts label every line "Them", so the speaker label carries no information and owners are inferred. The lenient precision accepts any true item, so a decision question answered with true action items scores well on it; the strict precision does not. `verify_item.v1` rejected items whose owner the lines could not show; v2 judges content only (changed on DEV).
- **Status.** Full run done (results below). Code: `tenor ami list-check` ([`list_check.py`](../../packages/tenor/evals/list_check.py)).

### E1 results (Preliminary)

Real AMI meetings played into Granola. 30 list questions (DEV 6 + TEST 24) on 5 meetings. Automated judge. Mean per question, 95% bootstrap CI. Numbers: [`evals/reports/ami/list_check.json`](../../evals/reports/ami/list_check.json).

| Condition | Items listed | Recall | Precision, lenient | Precision, strict | F1, lenient |
|---|---|---|---|---|---|
| C: Granola Chat | 97 | 0.74 [0.61, 0.87] | 0.86 [0.77, 0.93] | 0.44 | 0.71 [0.58, 0.83] |
| N: notes only | 94 | 0.73 [0.58, 0.86] | 0.72 [0.61, 0.83] | 0.42 | 0.61 [0.48, 0.74] |
| L: list every item | 357 | **0.90** [0.79, 0.98] | 0.61 [0.51, 0.71] | 0.22 | 0.68 [0.58, 0.78] |
| LD: list, check, drop | 278 | 0.80 [0.66, 0.91] | 0.65 [0.55, 0.76] | 0.23 | 0.68 [0.56, 0.79] |
| LM: list, check, mark | 357 | 0.90 | 0.61 | 0.22 | 0.68 |

| Paired test (sign test, n = 30) | Recall: wins / losses | Lenient precision: wins / losses |
|---|---|---|
| **L vs Chat** | **8 / 0, p = 0.008** | **3 / 23, p < 0.001** |
| **LD vs Chat** | 6 / 2, p = 0.29 (not significant) | **3 / 22, p < 0.001** |
| L vs notes only | 8 / 0, p = 0.008 | 12 / 16, p = 0.57 (not significant) |
| LD vs L | 0 / 4, p = 0.13 (not significant) | 16 / 6, p = 0.053 (not significant) |

- **Listing everything finds more gold items than Chat, and adds many wrong or off-topic ones.** L covers 0.90 of gold items vs Chat's 0.74 (8 questions better, 0 worse). But 39% of L's items are not right answers to the question, vs 14% of Chat's. F1 is a tie (0.68 vs 0.71).
- **Dropping unchecked items does not fix it.** LD gives back recall (0.80, no longer better than Chat) and gains only 4 points of precision.
- **The "unconfirmed" mark carries some information.** Confirmed items: 72% right (199 / 278, Wilson CI 66–77%). Unconfirmed: 43% (34 / 79, CI 33–54%). Against the gold alone the mark carries none (21% vs 25%).
- **L's wrong items look like over-inclusion, not invention.** The judge marked 124 of 357 L items wrong. In a sample of 8, every judge reason was "proposed but not decided", "not assigned" or "another topic"; none was "never said". Every one of the 357 quotes appears in the transcript.
- **By category,** L's recall gain is largest on decisions (0.82 vs Chat 0.57, n = 10). Its precision loss is largest on action items (0.50 vs 0.84, n = 16).

**Threats to validity**
- **One meeting set.** 5 meetings and 30 questions; 25 of the questions come from one 4-meeting series. With this n, only large effects reach significance.
- **Gold lists are short (1–2 items, templated).** Strict precision mostly measures list length, not error. Read the lenient column.
- **No speaker names.** Granola's transcript labels every line "Them", so "who owns it" can't be checked against a quote. Owners are inferred.
- **Automated judge, lenient on category.** One Sonnet judge, no human check yet. It accepts any true item, even when it is a different kind of item than the one asked for. Our judge over-counts errors elsewhere ([`CALIBRATED_JUDGING.md`](../method/CALIBRATED_JUDGING.md)).
- **Checker = reader model.** The checker and the lister are both Opus, so they may share blind spots. The judge is a different model.
- **Chat answers were recorded once,** on another day. L reads the full transcript of one meeting, while Chat chose what to read.

Cost: $7.83 in total (pilot $0.37, map $2.23, merge $0.44, check + judge $4.78), under the $11.00 cap.

## E2: was it agreed? (designed; pilot run)

**Question.** E1's extra items, shown as a "+N more said in the meeting" line under Chat's answer, were ~7.6 per question and ~66% right. The wrong ones were over-inclusion ("proposed but not decided", "not assigned"). Does asking "was this agreed / assigned?" instead of "was it said?" cut the extras to 2–3 per question, keep them right, and keep most of E1's recall gain?

| Step | What happens |
|---|---|
| **Items** | E1's listed items and quotes, reused from E1's run (no new listing calls) |
| **Classify** | One call per item (`agreed_check.v3`): the question, the item, its quote and ±8 Granola transcript lines around the cited lines. Output: a status (agreed decision, assigned action, problem raised, proposed only, mentioned only, other topic), whether it answers the question as asked, a 0–100 confidence, a one-line reason |
| **Keep** | Status matches the question's kind (decision, action, problem) and it answers the question |
| **Dedupe** | Drop items Chat's answer already has, by E1's judge mapping (the item states gold items, all of them covered by Chat). An oracle a product lacks; it is how E1's extras were counted, so the comparison isolates the classifier |
| **Cap** | Top K by confidence (ties: transcript order); K = 2, 3 and uncapped |

- **Baselines.** Chat alone; Chat + every E1 extra; Chat + the extras E1's checker confirmed (the "+N more" line).
- **Metrics,** per question then mean with 95% bootstrap CIs: recall of Chat + extras; extras per question; precision of the extras (lenient: E1's judge marked it gold or true per the reference transcript; strict: gold); gold items the filter drops that the unfiltered extras had. Sign tests of each K against both baselines.
- **No new judge calls.** Every extra is an E1 item, so E1's item verdicts score it.
- **Split.** Prompt changes only on the 6 DEV questions; TEST (24) reported separately.
- **Known limits.** The lenient judge accepts true items of the wrong kind, so it can hide the filter's gain; the strict column shows it. Items that misname a person pass the filter (it checks agreement, not names). DEV has 1 gold item Chat missed, so DEV says almost nothing about recall.
- **Status.** Pilot on DEV run; full run not run. Code: `tenor ami agreed-check` ([`agreed_check.py`](../../packages/tenor/evals/agreed_check.py)).
