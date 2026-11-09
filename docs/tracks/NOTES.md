# Notes track

This page defines the notes track: whether a product's notes are faithful to what was said (and, planned, complete), with results on AMI and on the earlier private meetings.

| | |
|---|---|
| **Question** | Is every claim in the notes backed by what was said? Are the important things said present in the notes? |
| **How measured** | Notes split into claims; each claim checked by the [grounding checker](../method/GROUNDING_CHECKER.md) against the product's own transcript and, on AMI, against AMI's human transcript ([`WRONG_FACTS.md`](../method/WRONG_FACTS.md) stage 2); a sample human-checked ([`HUMAN_VERIFICATION.md`](../method/HUMAN_VERIFICATION.md)). Completeness: recall of AMI's annotated decisions, actions and problems ([E3](#e3-notes-completeness-preliminary), Preliminary: weighted recall Granola 0.56, Wispr Flow 0.64 on 5 TEST meetings, not significant) |
| **Current results, AMI** | Corrected automated run (2026-11-05, `352698f`; supersedes the first pass). Claims supported by both transcripts: Granola 195 of 216 (90.3%, 95% CI 85.6–93.6%), Wispr Flow 138 of 171 (80.7%, 74.1–85.9%). Other classes, Granola / Wispr Flow: `borderline` 12 / 19 (one-notch checker disagreements), `transcript_propagated` 4 / 6 (upper bound, not re-read by hand), `inferred` 4 / 5, `note_error` 0 / 2, `human_only` 1 / 1. Gap 9.6 points (CI 2.5–16.7 if claims were independent; wider, since they cluster in 7 meetings); counting `borderline` as fine, 95.8% vs 91.8%. Human check of 20 sampled claims: 12 judgeable, 0 wrong, 1 overreach. Full table: [`WRONG_FACTS.md` §3](../method/WRONG_FACTS.md#3-stage-2-corrected-run-headline) |
| **Earlier result** | Author's 4 private meetings (Granola only): 84% supported, 14% inferred, 0% unsupported, 2% possibly contradicted (132 claims; §below) |
| **Status** | Preliminary. The AMI comparison is not a ranking until human calibration is done (about 100 labels, a second verifier) |

## AMI claim types

Moved from the README (5 November 2026). Corrected automated run (`352698f`), 387 claims, 7 AMI meetings. Preliminary; not yet human-calibrated.

| Claim type | Granola (of 216) | Wispr Flow (of 171) | Notes |
|---|---|---|---|
| Supported by both transcripts | 195 (90%) [86, 94] | 138 (81%) [74, 86] | The README headline |
| `borderline`: the two checks differ by one step | 12 (6%) | 19 (11%) | Mostly checker noise: one check says supported, the other inferred. Counting these as supported: 95.8% vs 91.8% |
| `transcript_propagated`: note may repeat a transcription error | 4 | 6 | Upper bound, not re-read by hand. 4 of the 10 are in the human sample: 2 supported, 1 overreach, 1 could not judge, none wrong |
| `inferred`: goes beyond what was said | 4 | 5 | |
| `note_error`: added by the note-writer | 0 [0, 2%] | 2 [0.3, 4%] | 95% Wilson CIs |
| `human_only` | 1 | 1 | |

Full table and first-pass comparison: [`WRONG_FACTS.md` §3](../method/WRONG_FACTS.md#3-stage-2-corrected-run-headline).

**Task.** Given a meeting's transcript and one system's notes for that meeting, how much of what the notes say can be pointed to in the transcript?

The track is product-neutral: any notetaker, or our own summariser, can be scored the same way.

**Why.** A note that adds things the meeting never said is worse than a shorter note: people act on it. Granola has written a version of this principle for a different feature: for its Crunched 2025 year-in-review prompts, "if you can point to it in the text, it's fair game" ([Jo Barrow, 17 Dec 2025](https://www.granola.ai/blog/how-we-wrote-the-prompts-behind-granolas-crunched-2025)). We apply the same test to meeting notes. No notetaker publishes a grounding number, so the track measures one.

**First results.** Two systems so far.

| System | Meetings / claims | Supported (strict checker) | Inferred | Unsupported | Contradicted | Cross-meeting bleed, strong signal |
|---|---|---|---|---|---|---|
| **Granola AI notes** (author's 4 real meetings) | 4 / 132 | **84%** [77, 89] | 14% [9, 21] | 0% [0, 3] | 2% [1, 6] (3) | 0 of 29 ungrounded claims |
| **Synthetic generator** (Northwind baseline) | 4 / 81 | **98%** [91, 99] | 0% [0, 5] | 0% [0, 5] | 2% [1, 9] (2) | 0 of 8 |

The strict checker is Sonnet 5.5 on every claim. Cells are rate [95% Wilson CI]. The cheap checker (Haiku 5.5) gives Granola 92% supported and the synthetic notes 90%: its labels barely tell the two apart (§4).

In one paragraph:
- **Fabrication:** Granola's notes on these meetings invent nothing outright: no unsupported claim, and no number, date or name the meeting didn't contain.
- **Overreach:** about one claim in seven goes beyond what was said. The usual cases are an added qualifier, a discussion written up as a decision, or an owner nobody confirmed. Action items and decisions are hit most.
- **Contradictions:** three claims may contradict the transcript. Each is a small inversion, and each needs the owner's check.
- **The baseline:** it shows the strict checker is not just strict. It calls the synthetic generator's notes, which are grounded by construction, 98% supported with zero `inferred`. It also caught a real bug in our own generator (§4.3).

Code: [`evals/faithfulness.py`](../../packages/tenor/evals/faithfulness.py). Prompt: [`prompts/notes_audit.v1.md`](../../prompts/notes_audit.v1.md) (the track's first prompt; the name is kept so cached calls replay). Aggregates: [`evals/reports/notes-faithfulness/`](../../evals/reports/notes-faithfulness/). Total spend for both systems: **$0.40** (Granola $0.26, baseline $0.13), each quoted beforehand with exact input-token counts.

```bash
uv run tenor notes-faithfulness --system granola --quote                # price it, $0
uv run tenor notes-faithfulness --system granola --yes                  # real meetings (private store)
uv run tenor notes-faithfulness --system northwind-synth --meetings 4 --yes
```

## 1. Input, output and pipeline

**Input.** A `NotesSystem` holds:
- a name;
- `entries`: one `(MeetingNote, notes markdown)` pair per scored meeting, where the `MeetingNote` supplies the transcript and calendar context and the markdown is *this system's* notes;
- `pool`: every meeting of the same account or corpus, which the bleed heuristic searches;
- `private`: whether per-claim output may be committed.

**Output.**
- Per-claim labels: private systems write them under `data/real/notes_faithfulness/` only, public ones next to the aggregate.
- An aggregate JSON: `evals/reports/notes-faithfulness/<system>.aggregate.json`.
- For private systems, an owner spot-check list.

**Pipeline**
1. **Claims.** `split_claims` gives one claim per bullet or numbered item, and one per sentence where an item or paragraph has several. Headings are dropped but kept as the claim's section. *Lead-ins* are dropped too: an all-bold item (often an owner's name over a task list) or an item ending in ":". Their text becomes the context of the lines under them, so the checker sees "Name > will send the deck".
2. **Check** (Haiku 5.5, low effort). One structured call per meeting. The numbered transcript (`[i] Me|Them (name if the source gives one): text`) and the calendar context go in a system prompt marked as a cache breakpoint; all the meeting's claims go in the user message. Each claim gets a ≤15-word reason, a label, a claim type and cited line numbers. A `supported` label with no valid line number would be relabelled `unsupported`; that never happened.
3. **Re-check** (Sonnet 5.5) on a seeded random 30% of claims, and a **strict pass** (Sonnet 5.5) on every claim. We added the strict pass after the 30% sample showed Haiku labelling almost everything `supported` (§4).
4. **Pre-check** (deterministic, $0). It flags a claim's digit-written numbers that the transcript never says, as digits or words ("twenty five" = 25, "5k", ordinals). It does the same for month and weekday names, and for capitalised mid-sentence words found in neither the transcript nor the calendar context.
5. **Cross-meeting bleed** (deterministic, $0, a heuristic). A claim is flagged when one of its distinctive terms is missing from its own meeting's transcript and calendar context but appears in another pool meeting's transcript or notes. Distinctive terms are names, digit numbers, and rare stemmed words of six letters or more. A word is *rare* if it occurs in at most 25% of the other meetings (at least one). *Strong* flags rest on a name or a number; *weak* ones on a rare word only. It is the one-account version of a cross-meeting leak: meeting Y's content appearing in meeting X's notes.

**Labels**

| Label | Meaning |
|---|---|
| `supported` | stated or directly implied (paraphrase and transcription errors are fine); must cite lines |
| `inferred` | plausible but not said outright: motive, sentiment, emphasis, a generalisation, or a named owner the channel labels can't establish |
| `unsupported` | nothing in the transcript says it |
| `contradicted` | the transcript says otherwise |

**Claim types:** decision · action item / owner · number / date · attribution (who said what) · opinion / sentiment · summary-level generalisation · other fact.

## 2. Metrics

Every rate comes with a 95% Wilson interval. Each aggregate reports:
- the **support rate** and the rates of `inferred`, `unsupported` and `contradicted`; their sum is reported as **ungrounded** (= not supported). This is given for both the cheap checker and the strict checker;
- the **ungrounded rate by claim type**, under each checker's own typing (they agree on the type 74% of the time on Granola);
- **pre-check flags**: the counts checked and flagged, and how the LLM labelled the flagged claims;
- **cross-meeting bleed**: flagged ungrounded claims (all flags and strong only), against the same rate over all claims as a baseline;
- **checker reliability**: raw agreement, Cohen's κ (4-way and supported-vs-not) and confusion counts for three comparisons: cheap vs strong on the 30% sample, cheap vs strict on all claims, and strict vs re-check on the same claims in different batches (test-retest);
- **provenance**: date, git SHA, models, effort, prompt version, seed, quote and cost.

## 3. How to add a system

1. Write an adapter that returns a `NotesSystem`. Pair each meeting's `MeetingNote` (transcript plus calendar context, from any `Source`) with the system's notes as markdown. Set `pool` to all the account's meetings and `private=True` for real data. `granola_system` and `northwind_system` in `evals/faithfulness.py` are about ten lines each.
2. Add it to `FaithSystem` in the `notes-faithfulness` command, then run `--quote` first.
3. Use the same prompt (`notes_audit.v1`) and models, so rows are comparable. A new prompt is a new version file and a new column, not an edit.
4. Notes that aren't markdown bullets (plain prose) work too: the splitter falls back to sentences.

## 4. Results

### 4.1 Overall

| | Granola: Haiku | Granola: Sonnet strict | Synthetic: Haiku | Synthetic: Sonnet strict |
|---|---|---|---|---|
| supported | 122 (92%, [87, 96]) | 111 (84%, [77, 89]) | 73 (90%, [82, 95]) | 79 (98%, [91, 99]) |
| inferred | 10 (8%, [4, 13]) | 18 (14%, [9, 21]) | 6 (7%, [3, 15]) | 0 (0%, [0, 5]) |
| unsupported | 0 (0%, [0, 3]) | 0 (0%, [0, 3]) | 0 (0%, [0, 5]) | 0 (0%, [0, 5]) |
| contradicted | 0 (0%, [0, 3]) | 3 (2%, [1, 6]) | 2 (2%, [1, 9]) | 2 (2%, [1, 9]) |
| **ungrounded** | 10 (8%, [4, 13]) | 21 (16%, [11, 23]) | 8 (10%, [5, 18]) | 2 (2%, [1, 9]) |

Claims per meeting: Granola 33 / 39 / 23 / 37; synthetic 24 / 18 / 13 / 26, from 4 meetings drawn with seed 0 out of 40.

**Which checker to believe.** The synthetic notes are grounded by construction: every summary bullet is written from a fact the generator also put in the transcript.
- On them, Haiku still flags 7% as `inferred`, about the same rate it gives Granola (8%). So Haiku's `inferred` rate is mostly its own noise floor: it can't tell these two systems apart.
- Sonnet gives the synthetic notes **0** `inferred` and Granola's **14%**, so the strict checker separates them.
- We therefore read Granola's result from the strict column: 16% ungrounded [11, 23], 2% possibly contradicted. The cheap column is reported but is not the headline.

### 4.2 By claim type (Granola)

| Claim type | Haiku: n | Haiku: ungrounded | Sonnet: n | Sonnet: ungrounded (inferred / contradicted) |
|---|---|---|---|---|
| decision | 18 | 1 (6%, [1, 26]) | 29 | 5 (17%, [8, 35]) (3 / 2) |
| action item / owner | 36 | 4 (11%, [4, 25]) | 26 | 6 (23%, [11, 42]) (6 / 0) |
| number / date | 2 | 0 | 1 | 0 |
| attribution (who said what) | 2 | 0 | 2 | 0 |
| opinion / sentiment | 13 | 1 (8%, [1, 33]) | 6 | 1 (17%, [3, 56]) (1 / 0) |
| summary-level generalisation | 2 | 1 (50%, [9, 91]) | 1 | 0 |
| other fact | 59 | 3 (5%, [2, 14]) | 67 | 9 (13%, [7, 24]) (8 / 1) |

**Action items and decisions go ungrounded most, under both checkers.** They are also the lines people act on. Two of the three possible contradictions are decisions. The CIs overlap with "other fact", so treat this as a pattern seen twice, not a measured difference. Numbers/dates, attributions and generalisations are too rare in these notes (n ≤ 2) to say anything about.

The synthetic notes are mostly status and opinion lines (38 of 81 typed as opinion under Sonnet). Their only ungrounded claims are the 2 contradictions below.

### 4.3 What the failures look like

The Granola examples are made-up illustrations of each pattern, in our own words. They are not quotes and do not come from the meetings.
- **Added qualifier (inferred; the most common).** "A few of last quarter's invoices are missing" becomes "invoice records are incomplete: a recurring problem".
- **Discussion promoted to decision (inferred).** Two vendors are discussed, one is mentioned last, and the note says "consensus on vendor B".
- **Owner assigned beyond what was confirmed (inferred).** "Can you two take the onboarding doc?" One says yes, the other is silent, and the note lists both.
- **Extra detail (inferred).** The meeting proposes recording who approved a change. The note adds "and the approval date".
- **Conflation (inferred).** A remark about a discount code and another about members-only lists merge into "members-only lists, unlocked by discount code".
- **Flipped attribute (contradicted).** "The channel closes when the project ends" becomes "a permanent channel per project".
- **Figure of speech read backwards (contradicted).** An idiom meaning "over-explain to them" is written up as a remark about the listeners themselves.
- **Specific swapped for generic (contradicted).** "Look at our client's website" becomes "look at well-designed sites in that industry".

**A bug in our own generator.** The synthetic data is public, so we can quote it. Both checkers agree that 2 synthetic summary bullets are contradicted. In both, the speaker says "I'm not convinced *X* is the blocker / the scary part", and the generated bullet reads "*X* is the blocker". The summary renderer drops the negation of a hedged opinion. That is a bug in `synth/` (filed as a follow-up, not fixed here: the fixtures must match the generator). It also shows the checker catches the kind of error this track exists for.

### 4.4 Pre-check

The deterministic check finds almost nothing on either system:
- **Granola:** 21 numbers, 6 date words and 80 capitalised words checked, **0** flagged. 3 claims name a person who appears only in the calendar invite, never aloud.
- **Synthetic:** 1 number flagged, and both checkers call that claim supported.
- **First-version bugs, fixed with tests:** hyphenated compounds such as "AI-generated" were flagged as names, and "14th" was not read as 14.

On these notes, grounding failures are **semantic, not lexical**: a model has to read the claim.

### 4.5 Cross-meeting bleed (heuristic: needs human confirmation)

| | Granola | Synthetic |
|---|---|---|
| ungrounded claims flagged (any term) | 11 of 29 (38%, [23, 56]) | 0 of 8 |
| ungrounded claims flagged (strong: name or number) | **0 of 29** (0%, [0, 12]) | 0 of 8 |
| baseline: all claims flagged (any term) | 33 of 132 (25%, [18, 33]) | 1 of 81 |
| baseline: all claims flagged (strong) | 0 of 132 | 0 of 81 |

**No sign of cross-meeting bleed.** No claim in either system carries a name or number that only another meeting has. All 11 weak Granola flags rest on one ordinary word each, of the "similar", "continue", "output" kind. We checked those by reading them privately. With only 4 meetings in the pool, "found in one other meeting" is not rare, so the weak signal is mostly paraphrase vocabulary; one in four *supported* claims trips it too. The heuristic can rule out the blatant case: a name or figure copied from another meeting. It can't rule out subtle bleed, and it needs a larger account to mean much. The flagged claims and their terms are in the private spot-check list.

### 4.6 Checker reliability

| Comparison | Granola: n | agree | κ (4-way) | κ (supported vs not) | Synthetic: n | agree | κ (4-way) |
|---|---|---|---|---|---|---|---|
| Haiku vs Sonnet, 30% sample | 40 | 78% | −0.04 | −0.05 | 25 | 92% | 0.64 |
| Haiku vs Sonnet strict, all claims | 132 | 86% | 0.32 | 0.32 | 81 | 93% | 0.38 |
| Sonnet test-retest (same claims, other batch) | 40 | 88% | 0.56 | 0.55 | 25 | 96% | 0.65 |

- **Lenient vs strict.** On Granola, Haiku calls 15 of the 21 claims Sonnet flags `supported`.
- **The Granola sample κ of −0.04 is a base-rate effect.** Haiku called 39 of those 40 claims supported, so any disagreement sends κ to 0.
- **Sonnet is not stable either.** It changed 5 of 40 labels when the same claims were batched with different neighbours. "Inferred" is a judgement call, and the line between "directly implied" and "plausible reading" is where most disagreements fall.

## 5. Caveats

- **Small n, one person.** The Granola row is 4 small working meetings of one account (132 claims). The synthetic row is 4 meetings (81 claims). The CIs are wide.
- **The checker is an LLM, and nothing here is human-verified yet.** The owner's spot-check list (`data/real/notes_faithfulness/granola.spotcheck.md`, private) holds all 29 claims any checker did not call supported. Each entry has both checkers' reasons, the cited and nearest transcript lines, and the bleed terms. The owner is the ground truth.
- **"Inferred" is a judgement call.** Summaries are meant to compress. An `inferred` claim is not necessarily wrong: it is one a reader can't verify by clicking through to a line.
- **The Granola notes may come from an older notes model** than the one Granola ships today. They may also use context we don't see (typed notes, templates, earlier meetings). "Not in the transcript" means not in the transcript, not invented.
- **Transcripts are imperfect.** They are speech-to-text and channel-attributed (`Me` = microphone, `Them` = everyone else). In one meeting all audio came in on one channel, so who said what could not be checked from channels at all.
- **The baseline is not a notetaker.** The synthetic notes are generated from planted facts, so the row calibrates the checker more than it competes with Granola.
- **The prompt is v1 and untuned.** We added the strict pass after seeing the 30% sample's κ. That decision changes what we report, not any label.

## 6. What Granola could do

These suggestions rest on the measurement above. They are not claims about Granola's internals.

1. **Run a per-line grounding check before the notes are shown.** It has to be model-based, because the failures are semantic: string checks found nothing. One call per meeting, with the transcript cached, labels each line and cites the lines it rests on. Here that cost under a cent per meeting with a Haiku-class model. The Sonnet-class pass this measurement shows is needed cost about $0.03 per meeting.
2. **Put a magnifying-glass citation on every claim.** A supported line gets an icon that jumps to its transcript lines. An inferred line gets a quiet marker such as "inferred: not said in so many words", rather than being deleted. Readers would then see the roughly one claim in seven that needs a second look.
3. **Be strictest on action items and decisions.** They go ungrounded most here. A decision or owner line should cite a line where someone actually agreed.
4. **Calibrate the checker against a grounded baseline.** Our cheap checker flagged grounded synthetic notes about as often as real ones. A grounded set (synthetic, or owner-labelled) shows which checker can tell the difference.

## 7. Provenance and cost

| | |
|---|---|
| Date | 2026-11-05 (UTC) |
| Git SHA | `meta.git_sha` in each aggregate JSON |
| Checker | `claude-haiku-5-5`, effort low; prompt `notes_audit.v1` |
| Re-check and strict pass | `claude-sonnet-5-5`, same prompt; 30% sample seeded (`--seed 0`) |
| Cost | Granola $0.26 (Haiku $0.01, Sonnet sample $0.15, Sonnet strict $0.10); synthetic $0.13; quoted beforehand with exact input tokens |
| Prompt caching | the transcript is a cache breakpoint (`LLMRequest.cache_system`). With one call per meeting per model nothing is reused, so here caching cost the 1.25x write premium on input. It pays off on retries or repeat checks of a meeting |

## E3: notes completeness (Preliminary)

**Question.** What share of what was decided, assigned and raised in a meeting appears in each product's notes (not Chat)? And is what the notes say about it true?

| | |
|---|---|
| **Unit** | One product's AI notes for one AMI meeting: the 7 replay meetings both products recorded, minus EN2002b (AMI has no abstractive summary for it). 6 meetings x 2 products = 12 units. DEV: IS1008a; TEST: the other 5 |
| **Gold** | AMI's human DECISIONS / ACTIONS / PROBLEMS sentences for the meeting, one item per sentence, plus the speaker roster |
| **Notes items** | The note's `summary_markdown`, split by code (no LLM) into bullets, each tagged with its heading; sub-points keep their parent; a paragraph under a bullet joins it. A bullet can hold more than one claim |
| **Judge** | Sonnet 5.5 (`notes_completeness.v4`), blind to the product, 3 passes per unit. First each bullet: kind (decision, action, problem, other), the gold items it states, and whether AMI's human transcript supports it (yes, partly, no). Then each gold item: covered fully, partly or not, by which bullets; if not fully, whether the product's own transcript has it, with a verbatim quote that code checks by string match |

- **Recall** per category: full = 1, partial = 0.5; full-only recall reported too. For an action, the owner is part of the item: no owner, or a label nobody can match to a person, is at most partial.
- **Precision** of the notes' decision / action / problem bullets. Lenient: right = states a gold item or is fully supported. Strict: fully supported only, so a gold-matching bullet with a wrong detail counts as wrong. F1 = lenient precision with weighted recall.
- **Heard, not noted.** Gold items absent from the notes that the product's own transcript has: what a better note-writer could recover with no transcription fix. Counted as judged and as quote-verified.
- **Passes.** Each pass ends with a neutral marker ("Judging pass k."), so the disk cache does not replay pass 1. Headline = mean over passes. Alternative = majority vote per item (median coverage, voted kind and links). Spread = range and SD across passes, per unit and pooled.
- **Prompt caching.** Breakpoints after the meeting part (gold + AMI transcript: shared by both products and all passes) and after the product part (transcript + notes: shared by that product's passes). Units run meeting by meeting, passes back to back.
- **Pooling.** Means over meetings with 95% bootstrap CIs; Granola vs Wispr Flow paired per meeting (sign test, wins = Wispr Flow higher).
- **Checks.** Parser repairs and disagreements between the judge's two link lists are counted in every result.
- **Prompt changes on DEV only** (v1 to v4). Bullets judged before gold items, after the two halves disagreed. Explicit rules for generic speaker labels ("Speaker 2"), which the notes may define themselves. One example of a non-match was replaced: it had been taken from the DEV meeting and made the judge reject a real match.
- **Code:** `tenor ami notes-completeness` ([`notes_completeness.py`](../../packages/tenor/evals/notes_completeness.py)).

### E3 results (Preliminary)

Real AMI meetings played into both products. **TEST: 5 meetings, 58 gold items** (DEV, 1 meeting, used to fix the prompt). 3 judge passes, mean over passes. Recall and precision are means over meetings with 95% bootstrap CIs. Counts are summed over meetings and averaged over passes. Numbers: [`evals/reports/ami/notes_completeness.json`](../../evals/reports/ami/notes_completeness.json).

| Product | Category | Gold | Recall (partial = 0.5) | Recall, full only | Precision lenient | Precision strict | F1 | Heard, not noted |
|---|---|---|---|---|---|---|---|---|
| Granola | decision | 17 | 0.55 [0.35, 0.80] | 0.36 | 1.00 | 0.69 | 0.65 | 2.7 |
| Granola | action | 22 | 0.54 [0.28, 0.80] | 0.42 | 0.99 | 0.60 | 0.65 | 6.3 |
| Granola | problem | 19 | 0.60 [0.49, 0.73] | 0.49 | 1.00 | 0.96 | 0.65 | 6.3 |
| **Granola** | **all** | **58** | **0.56 [0.48, 0.64]** | **0.41 [0.24, 0.56]** | **0.99** | **0.76 [0.68, 0.83]** | **0.71** | **15.3** |
| Wispr Flow | decision | 17 | 0.56 [0.34, 0.79] | 0.42 | 0.97 | 0.94 | 0.60 | 3.7 |
| Wispr Flow | action | 22 | 0.67 [0.51, 0.86] | 0.57 | 0.94 | 0.82 | 0.76 | 5.3 |
| Wispr Flow | problem | 19 | 0.66 [0.62, 0.71] | 0.48 | 1.00 | 0.95 | 0.79 | 3.0 |
| **Wispr Flow** | **all** | **58** | **0.64 [0.58, 0.70]** | **0.50 [0.41, 0.58]** | **0.97** | **0.86 [0.78, 0.95]** | **0.77** | **12.0** |

- **Both products' notes leave out about 40% of what AMI's annotators wrote down.** Weighted recall 0.56 (Granola) and 0.64 (Wispr Flow). Fully stated: 41% and 50% of gold items.
- **Wispr Flow's recall is higher on every TEST meeting, but not significantly.** Overall recall: 5 wins of 5, sign p = 0.06. Full-only recall: 3 wins, 2 ties (p = 0.25). Per category: no difference reaches significance. With DEV included it is 6 of 6 (p = 0.03), but DEV was used for tuning.
- **What the notes do say as a decision, action or problem is almost never wrong.** Lenient precision is 0.97–0.99 for both.
- **The strict-precision gap (Granola 0.76, Wispr Flow 0.86) is mostly a judge artifact. Don't read it as a difference.** Granola's bullets got 12.7 "partly" verdicts per pass on TEST; Wispr Flow's got 6.7. Of Granola's, 5.0 give a missing or unassigned owner as the only or main reason. The prompt says that alone is not wrong, so the judge broke its own rule. Counting those as supported puts Granola at about 0.85. The rest are real overstatements: a suggestion written as agreed, an added deadline, a wrong day. Strict precision was not sign-tested.
- **Most missing items were heard.** Of the gold items missing outright from the notes, the product's own transcript had 15.3 (Granola) and 12.0 (Wispr Flow) per pass on TEST. Nearly all were quote-verified (15.3 and 11.3). Another 8.7 and 11.3 were partly noted and heard. A better note-writer could recover these with no change to transcription. The counts are lower bounds: on 6.3 and 4.0 not-fully-covered items the judge left "heard" unassessed (mostly in IB4003).
- **The majority-vote aggregate agrees with the mean.** TEST overall recall: Granola 0.57 vs 0.56, Wispr Flow 0.64 vs 0.64.

**Between-pass spread (all 6 meetings).**

| | Granola | Wispr Flow |
|---|---|---|
| Overall recall, mean range per unit (max) | 0.08 (0.14) | 0.06 (0.14) |
| Overall recall pooled, pass 1 / 2 / 3 | 0.50 / 0.53 / 0.55 | 0.59 / 0.62 / 0.63 |
| Lenient precision pooled, pass 1 / 2 / 3 | 0.98 / 0.98 / 0.97 | 0.95 / 0.91 / 0.94 |
| Gold items with the same coverage in all 3 passes | 55 of 65 | 52 of 65 |
| Bullets with the same kind in all 3 passes | 180 of 194 | 135 of 142 |
| Decision / action / problem bullets with the same support verdict in all 3 passes | 46 of 57 | 52 of 61 |

- **A single pass would move a meeting's recall by up to 0.14.** Disagreements sit where expected: none vs partial on half-captured items (a decision noted only as an option, one side of an either/or problem), partial vs full on owner phrasing. Only 1 of 130 gold items got three different labels.
- **The ranking holds in every pass.** Pooled recall is higher for Wispr Flow in each of the 3 passes.

**Threats to validity.**
- **6 meetings, 5 of them TEST.** Four are sessions of one remote-control design scenario with the same team (IS1008a–d); one is the same scenario with another team (ES2006a); one is a real research-group meeting (IB4003). Only large effects can reach significance.
- **One automated judge model** (Sonnet 5.5). 3 passes measure its noise, not its bias; no human labels yet. It does not always follow its rubric (the owner rule above).
- **Notes parsed by a heuristic splitter.** A bullet with two claims gets one kind and one support verdict.
- **Gold = AMI summary sentences.** One annotator's choice of what mattered. True notes items outside the gold score through the transcript, not the gold, so recall can only fall short where the annotator wrote something down.
- **Judge consistency.** 26 gold-to-bullet links on TEST appear on one side of the judge's output only (0 on DEV). Recall uses the gold side, precision the bullet side. 38 parser repairs over 36 calls; 35 are the unassessed "heard" verdicts above.
- **Labels.** Granola's transcripts label every line "Them", so owners in its notes cannot be checked against its own speaker labels.

**Provenance and cost.** Run 2026-11-09 (UTC), code at `6eb02fa` plus the E3 passes change, committed as `43c5dea` (its item-level pass agreement was added after the run and recomputed from the saved result; judge requests unchanged); judge `claude-sonnet-5-5`, prompt `notes_completeness.v4`, 3 passes, seed 42 (bootstrap). Judge spend: **$1.29** for 36 calls. That is $0.155 for the IS1008a passes run first as a sandbox, which the full run replayed, plus $1.13 in the full run. Prompt cache: 614k tokens read and 164k written; without it the same calls would have cost $2.31, so caching saved $1.02 (44%). Quoted beforehand: $2.23 expected / $2.93 high without caching; $1.23 / $1.94 with it. Hard cap $3.50. Prompt development on DEV (v1–v4, single pass): a further $0.34.
