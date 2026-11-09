# Notes track

This page defines the notes track: whether a product's notes are faithful to what was said (and, planned, complete), with results on AMI and on the earlier private meetings.

| | |
|---|---|
| **Question** | Is every claim in the notes backed by what was said? Are the important things said present in the notes? |
| **How measured** | Notes split into claims; each claim checked by the [grounding checker](../method/GROUNDING_CHECKER.md) against the product's own transcript and, on AMI, against AMI's human transcript ([`WRONG_FACTS.md`](../method/WRONG_FACTS.md) stage 2); a sample human-checked ([`HUMAN_VERIFICATION.md`](../method/HUMAN_VERIFICATION.md)). Completeness: recall of AMI's annotated decisions, actions and problems ([E3](#e3-notes-completeness-preliminary), Preliminary: weighted recall Granola 0.62, Wispr Flow 0.69 on 11 TEST meetings; Wispr Flow higher on 10 of 11, sign p = 0.01, no per-category difference significant) |
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
| **Unit** | One product's AI notes for one AMI meeting that AMI summarised and both products recorded with AI notes. Run 1: the 7 replay meetings minus EN2002b (no AMI abstractive summary): 6 meetings. Runs 2 and 3: the 6 extension meetings replayed on 2026-11-08/09 (TS3009c, ES2008b, ES2015c; then ES2006c, IS1000a, TS3006b once Granola's notes existed). 12 meetings x 2 products = 24 units. DEV: IS1008a; TEST: the other 11 |
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
- **Code:** `tenor ami notes-completeness` (`--meetings` to choose AMI meetings) and `tenor ami notes-completeness-pool` ([`notes_completeness.py`](../../packages/tenor/evals/notes_completeness.py)).

### E3 results (Preliminary)

Real AMI meetings played into both products. Three runs used the same judge, prompt (v4), 3 passes and limits, and are pooled into one report. **Pooled TEST: 11 meetings, 149 gold items.** Run 1: 5 meetings, 58 items. Run 2: TS3009c, ES2008b, ES2015c, 55 items. Run 3: ES2006c, IS1000a, TS3006b, 36 items. DEV (IS1008a, used to fix the prompt) is reported apart. Recall and precision are means over meetings with 95% bootstrap CIs. Counts are summed over meetings and averaged over passes. Numbers: [`evals/reports/ami/notes_completeness.json`](../../evals/reports/ami/notes_completeness.json). `runs` holds each run with its own provenance; `pooled` holds the tables below.

**Pooled TEST (11 meetings).**

| Product | Category | Gold | Recall (partial = 0.5) | Recall, full only | Precision lenient | Precision strict | F1 | Heard, not noted |
|---|---|---|---|---|---|---|---|---|
| Granola | decision | 65 | 0.70 [0.56, 0.83] | 0.56 | 0.94 | 0.72 | 0.76 | 6.7 |
| Granola | action | 47 | 0.56 [0.42, 0.70] | 0.43 | 0.94 | 0.63 | 0.67 | 13.3 |
| Granola | problem | 37 | 0.48 [0.35, 0.60] | 0.35 | 1.00 | 0.85 | 0.56 | 12.7 |
| **Granola** | **all** | **149** | **0.62 [0.56, 0.68]** | **0.49 [0.40, 0.57]** | **0.94** | **0.74 [0.67, 0.80]** | **0.74** | **32.7** |
| Wispr Flow | decision | 65 | 0.75 [0.59, 0.90] | 0.65 | 0.98 | 0.89 | 0.79 | 4.0 |
| Wispr Flow | action | 47 | 0.61 [0.51, 0.72] | 0.45 | 0.91 | 0.76 | 0.70 | 10.7 |
| Wispr Flow | problem | 37 | 0.54 [0.36, 0.70] | 0.41 | 1.00 | 0.94 | 0.64 | 9.7 |
| **Wispr Flow** | **all** | **149** | **0.69 [0.64, 0.73]** | **0.56 [0.49, 0.62]** | **0.96** | **0.85 [0.78, 0.91]** | **0.80** | **24.3** |

**Paired, Wispr Flow vs Granola, pooled TEST (wins / losses / ties, sign test p).**

| Category | Recall | Recall, full only | Precision lenient | F1 |
|---|---|---|---|---|
| all | 10 / 1 / 0, **p = 0.01** | 8 / 1 / 2, p = 0.04 | 4 / 4 / 3, p = 1.0 | 11 / 0 / 0, p = 0.001 |
| decision | 7 / 2 / 2, p = 0.18 | 6 / 1 / 4, p = 0.13 | 4 / 2 / 4, p = 0.69 | 8 / 2 / 1, p = 0.11 |
| action | 6 / 2 / 3, p = 0.29 | 3 / 4 / 4, p = 1.0 | 2 / 4 / 5, p = 0.69 | 6 / 4 / 1, p = 0.75 |
| problem | 6 / 3 / 2, p = 0.51 | 5 / 3 / 3, p = 0.73 | 0 / 0 / 9, p = 1.0 | 6 / 3 / 2, p = 0.51 |

**New meetings, per run.** All 6 are the same remote-control design scenario, played by four teams not in run 1. TS3009c and TS3006b are one team; ES2006c is a later session of run 1's ES2006 team.

| Run | Product | Gold | Recall | Full only | Precision lenient / strict | F1 | Heard, not noted |
|---|---|---|---|---|---|---|---|
| 2 (TS3009c, ES2008b, ES2015c) | Granola | 55 | 0.68 [0.58, 0.74] | 0.57 | 0.89 / 0.75 | 0.77 | 11.0 |
| 2 | Wispr Flow | 55 | 0.72 [0.66, 0.80] | 0.58 | 0.95 / 0.83 | 0.82 | 7.7 |
| 3 (ES2006c, IS1000a, TS3006b) | Granola | 36 | 0.68 [0.63, 0.74] | 0.55 | 0.90 / 0.69 | 0.77 | 6.3 |
| 3 | Wispr Flow | 36 | 0.75 [0.68, 0.78] | 0.64 | 0.96 / 0.84 | 0.84 | 4.7 |

Per meeting, overall recall (Granola / Wispr Flow):
- TS3009c 0.58 / 0.66
- ES2008b 0.74 / 0.80
- ES2015c 0.73 / 0.70
- ES2006c 0.74 / 0.78
- IS1000a 0.67 / 0.78
- TS3006b 0.63 / 0.68

Run 3 per category, Granola / Wispr Flow:
- decisions 0.77 / 0.94
- actions 0.64 / 0.57
- problems 0.31 / 0.33 (6 gold items)

**Run 1 (5 TEST meetings, first reported 2026-11-09).** Overall recall Granola 0.56 [0.48, 0.64], Wispr Flow 0.64 [0.58, 0.70]. Full only: 0.41 / 0.50. Strict precision: 0.76 / 0.86. Heard, not noted: 15.3 / 12.0. Per-category rows are under `runs[0]`.

- **Both products' notes leave out about a third of what AMI's annotators wrote down.** Pooled weighted recall is 0.62 (Granola) and 0.69 (Wispr Flow). Fully stated: 49% and 56% of gold items. The six new meetings score higher than run 1 for both products (about 0.68 vs 0.73), mostly on decisions. Their gold is decision-heavy, and those decisions are explicit design choices.
- **Wispr Flow's overall recall is higher on 10 of 11 TEST meetings (sign test p = 0.01).** Full-only recall: 8 wins, 1 loss, 2 ties (p = 0.04). F1: 11 of 11. The one recall loss is ES2015c. No per-category comparison is significant. Wispr Flow leads on decisions in 7 of 11 meetings; actions and problems are mixed. Read p = 0.01 with three cautions:
  - The meeting set grew in three steps, and we tested after each. The meetings were added because they had been recorded, not because of earlier results, but repeated looks still inflate significance.
  - Several metrics and categories are tested; overall recall is the headline we set first.
  - Gold comes from one judge model with no human labels yet.
- **What the notes do say as a decision, action or problem is mostly right.** Pooled lenient precision: 0.94 (Granola) and 0.96 (Wispr Flow). Granola is lower on the new meetings (0.89–0.90): more of its decision and action bullets match no gold item and are only partly supported.
- **The strict-precision gap (0.74 vs 0.85) is still partly a judge artifact.** On pooled TEST, Granola's bullets got 39.0 "partly" verdicts per pass and Wispr Flow's got 20.3. In run 1, 5.0 of Granola's gave a missing or unassigned owner as the main reason, which the prompt says is not wrong. In run 2, about 3.7 of 13.7 mention owners or labels (keyword count, not read one by one). Correcting run 1 alone put Granola at about 0.85. Strict precision was not sign-tested.
- **Most missing items were heard.** Of the gold items missing outright from the notes, the product's own transcript had 32.7 (Granola) and 24.3 (Wispr Flow) per pass on pooled TEST. Nearly all were quote-verified (32.3 and 23.7). Another 29.0 and 31.0 were partly noted and heard. These counts are lower bounds: 7.7 not-fully-covered items per product were left unassessed.
- **The majority-vote aggregate agrees with the mean.** Pooled TEST overall recall: Granola 0.63 vs 0.62, Wispr Flow 0.70 vs 0.69.

**Between-pass spread (all 12 meetings, DEV included).**

| | Granola | Wispr Flow |
|---|---|---|
| Overall recall, mean range per unit (max) | 0.08 (0.15) | 0.07 (0.19) |
| Overall recall pooled, pass 1 / 2 / 3 | 0.61 / 0.61 / 0.63 | 0.68 / 0.69 / 0.67 |
| Lenient precision pooled, pass 1 / 2 / 3 | 0.94 / 0.93 / 0.92 | 0.96 / 0.94 / 0.93 |
| Gold items with the same coverage in all 3 passes | 126 of 156 | 119 of 156 |
| Bullets with the same kind in all 3 passes | 347 of 382 | 270 of 285 |
| Decision / action / problem bullets with the same support verdict in all 3 passes | 125 of 149 | 119 of 142 |

- **A single pass would move a meeting's recall by up to 0.19.** The extremes are Wispr Flow on TS3009c (0.75, 0.67, 0.56) and Granola on IS1000a (range 0.15). Only 2 of 312 gold-item verdicts got three different labels.
- **The ranking holds in every pass.** Pooled recall is higher for Wispr Flow in each of the 3 passes.

**Threats to validity.**
- **11 TEST meetings, one task.** The scenario mix is much better than in run 1, which had 4 sessions of one team (IS1008b–d among them), one other team (ES2006a) and one real meeting (IB4003). TEST now covers 7 scenario teams (IS1008, IS1000, ES2006, ES2008, ES2015, TS3006, TS3009), all four session stages, and 1 real meeting. All the scenario meetings are still the same remote-control design task, so results on other kinds of meeting are untested beyond IB4003.
- **Granola generated AI notes for 3 of the 6 new recordings only after Kian opened them.** These were ES2006c, IS1000a and TS3006b, which ended on inactivity or were not opened after stopping. Wispr Flow wrote notes without that step. The 3 notes were generated hours after the meeting. We assume this changes nothing about their content, but have not tested it.
- **ES2015c and TS3006b are re-records.** The same audio was played into both products earlier and interrupted (the `A5` recordings). The earlier notes sit in the same accounts and could in principle inform the new ones. We see no sign of that, but did not test for it.
- **Fixed before judging: Wispr Flow's TS3009c transcript had been cut short.** The Wispr source missed the live continuation marker and kept only the first 40,000 characters. Fixed in `9dab8cf` and re-fetched. Only the "heard, not noted" check reads the product transcript.
- **One automated judge model** (Sonnet 5.5). 3 passes measure its noise, not its bias, and there are no human labels yet. It does not always follow its rubric (the owner rule above).
- **Notes parsed by a heuristic splitter.** A bullet with two claims gets one kind and one support verdict.
- **Gold = AMI summary sentences.** That is one annotator's choice of what mattered. True notes items outside the gold score through the transcript, not the gold, so recall can only fall short where the annotator wrote something down.
- **Judge consistency.** On pooled TEST, 28 gold-to-bullet links appear on one side of the judge's output only: 26 in run 1, 2 in run 2, 0 in run 3 and on DEV. Recall uses the gold side, precision the bullet side. There were 49 parser repairs on TEST, mostly unassessed "heard" verdicts.
- **Labels.** Granola's transcripts label every line "Them", so owners in its notes cannot be checked against its own speaker labels.

**Provenance and cost.**
- **Run 1.** Run 2026-11-09 (UTC). Code at `6eb02fa` plus the E3 passes change, committed as `43c5dea`; item-level pass agreement was added after the run and recomputed from the saved result, with judge requests unchanged. Judge `claude-sonnet-5-5`, prompt `notes_completeness.v4`, 3 passes, seed 42 (bootstrap).
  - Judge spend: **$1.29** for 36 calls: $0.155 for the IS1008a sandbox passes (replayed by the full run) plus $1.13 in the full run.
  - Prompt cache: 614k tokens read, 164k written. Without it, $2.31, so caching saved $1.02 (44%).
  - Quoted beforehand: $2.23 expected / $2.93 high without caching; $1.23 / $1.94 with it. Hard cap $3.50.
  - Prompt development on DEV (v1–v4, single pass) cost a further $0.34.
- **Run 2.** 2026-11-09 (UTC), same judge, prompt, passes, seed and limits; approved total $2.00.
  - Quoted beforehand: $1.72 expected / $2.13 high without caching; $0.87 / $1.28 with it.
  - Steps: a sandbox (TS3009c, pass 1, both products, cap $0.40: $0.21, outputs checked), then one meeting per run with hard caps of $0.54, $0.67 and $0.66. Each cap was set after the spend before it, so the total could not pass the approval. All 3 meetings were then replayed from the disk cache at `9dab8cf` ($0) into one result.
  - Judge spend: **$0.95** for 18 calls. Cache: 522k read, 141k written; without it $1.82, so caching saved $0.87 (48%).
- **Run 3.** 2026-11-09 (UTC), same settings; approved total $1.20.
  - Quoted beforehand: $1.50 expected / $1.84 high without caching; $0.74 / $1.08 with it.
  - Steps: a sandbox (IS1000a, pass 1, cap $0.16: $0.10, outputs checked), then TS3006b (cap $0.78), ES2006c (cap $0.65) and the rest of IS1000a (cap $0.40), each cap set after the spend before it. Then a $0 replay at `d582f67`.
  - Judge spend: **$0.77** for 18 calls. Cache: 465k read, 121k written; without it $1.54, so caching saved $0.78 (50%).
- **Pooled report.** Built by `tenor ami notes-completeness-pool` over the three runs' result files, with no LLM calls. The judge calls under the pooled numbers cost $3.01.

### E3 v5 rubric and gold errata: post-hoc changes (not yet run)

**Disclosure.** Everything in this subsection was decided **after seeing the v4 results above**. An independent agent re-read all 298 TEST verdicts (same model family as the judge, so not human calibration). It agreed with the judge on 74% of items (Cohen's kappa 0.55) and found two things: a rubric dispute on problems, and errors in the AMI gold. The v4 / original-gold numbers above remain the first reported results. Any v5 / errata number will be shown next to them, never in their place.

- **Rubric, prompt `notes_completeness.v5`.** A gold problem is covered if the notes record the issue, also together with how it was resolved; no open-question framing needed. v4's judge often wanted the open question, and the re-read counted 18 disagreements of that kind. The owner rules are restated, same meaning (names count as roles; joint owners; gold with no owner; wrong owner = none). Examples in the prompt are neutral, not from DEV or TEST.
- **Gold errata, [`gold_errata.v1.json`](../../data/ami_questions/gold_errata.v1.json)** (`gold_version` `ami-1.6.2+errata.v1`). Each correction has the AMI transcript line and a quote, checked against AMI:

| Meeting | Item | Original | Action | Evidence |
|---|---|---|---|---|
| ES2006a | A2 | The interface specialist will look at the working design. | correct owner: industrial designer | L0226, ID: "I'm supposed to design all the details of of the product um how it works" |
| ES2006a | A3 | The industrial designer will work on the technical functions design. | correct owner: interface specialist | L0189, UI: "at first I will be responsible for ... designing the technical functions" |
| TS3009c | D9 | The chip is going to be a standard one. | drop (contradicts D2) | L0310, ID: "we have to use a more advanced chip" (for the LCD); D9 rests on L0767, "the inside work ... is standard work" |
| ES2006c | P1 | `*NA*.` | drop | Placeholder, no summary links |
| IS1000a | A2 | The industrial designer will work on the technica function design. | **not applied** (weak evidence) | Only back-channels show who was addressed (L0589–L0592) |

- **Applied to E3 only.** E1/E2 questions built from the same sentences should get the same errata later: `ami-ES2006a-action_who-02`, `-03`, `ami-ES2006a-action_what-02`, `-03`, `ami-ES2006c-problem-01`.
- **What can be computed without new calls.** The two drops applied to the v4 verdicts: pooled TEST recall Granola 0.62 → 0.63, Wispr Flow 0.69 → 0.70. Problems rise from 0.48 → 0.53 and 0.54 → 0.60, mostly because ES2006c no longer has a problem category. Sign test unchanged (10 / 1 / 0, p = 0.01). The owner corrections and the new problem rule need a re-judge.
- **Re-judge quote.** 12 meetings (11 TEST + DEV) x 2 products x 3 passes = 72 calls: $2.83 expected / $4.29 high with prompt caching ($5.50 / $6.96 without). That is above the $2.00 limit, so it has not been run. It waits for approval.

| Product | Category | v4, original gold (reported) | v4 verdicts, errata drops only | v5, errata v1 |
|---|---|---|---|---|
| Granola | decision | 0.70 | 0.70 | not run |
| Granola | action | 0.56 | 0.56 | not run |
| Granola | problem | 0.48 | 0.53 | not run |
| **Granola** | **all** | **0.62** | **0.63** | not run |
| Wispr Flow | decision | 0.75 | 0.75 | not run |
| Wispr Flow | action | 0.61 | 0.61 | not run |
| Wispr Flow | problem | 0.54 | 0.60 | not run |
| **Wispr Flow** | **all** | **0.69** | **0.70** | not run |

- **Code.** `tenor ami notes-completeness --prompt {notes_completeness.v4,notes_completeness.v5} --gold {errata.v1,original}`. The defaults are v5 and errata.v1. `prompt`, `gold_version` and the applied corrections are recorded in each result's meta, and pooling refuses mixed gold versions. `--prompt notes_completeness.v4 --gold original` replays all 72 v4 calls from cache at $0.

### Real meetings (Seattle council) (Preliminary)

**Question.** When a real public meeting decides something, do the notes record the official outcome? Five Seattle City Council agenda items (2024–2026), each a segment of the public video played into both products. **n = 5 items, one city.** Gold is the city's official record, not an annotator's summary.

- **Gold, built by code from Legistar JSON (no LLM).** Three claims per item:
  - G1, the item: matter number and title.
  - G2, the outcome: Legistar's action, e.g. passed, passed as amended, adopted, postponed.
  - G3, the vote: tally and named voters, from the votes endpoint.
  Four items were unanimous (9–0). One was split, 6–3.
- **Judge.** E3's machinery: the same notes splitter, judge model (Sonnet 5.5), 3 passes and prompt caching. Prompt `council_notes.v1`, derived from `notes_completeness.v4`. Changes: gold = the official claims; no reference transcript, so no precision over the other bullets. The judge also lists every bullet about the outcome or vote and says whether it contradicts the record. Per item, the passes are combined by median coverage. A bullet counts as contradicting if most passes say so.
- **Units.** Granola's notes exist for all 5 items: 3 were generated only after Kian opened the recordings. Wispr Flow's recording of CB 121259 was still reported as not finalized (`finalized: false`) at two read-only re-syncs after Kian opened it, so it was not synced and Wispr Flow has 4 items. The per-product counts below have different denominators.
- **Code:** `tenor ami council-notes` ([`council_notes.py`](../../packages/tenor/evals/council_notes.py)). Numbers: [`evals/reports/council/notes_outcomes.json`](../../evals/reports/council/notes_outcomes.json).

| Product | Items | Item named (full) | Outcome stated (full) | Vote: full / partial / none | Items with a contradicting bullet |
|---|---|---|---|---|---|
| Granola | 5 | 5 / 5 | 5 / 5 | 4 / 0 / 1 | 1 |
| Wispr Flow | 4 | 4 / 4 | 4 / 4 | 3 / 1 / 0 | 1 |

| Item | Official outcome | Vote | Granola: item / outcome / vote | Wispr Flow: item / outcome / vote |
|---|---|---|---|---|
| Res 32142 (2024-09-17) | postponed | 9–0 | full / full / full | full / full / full |
| Res 32202 (2026-06-02) | adopted | 9–0 | full / full / full | full / full / full |
| CB 121214 (2026-06-09) | passed | 9–0 | full / full / full | full / full / full |
| CB 121259 (2026-08-04) | passed as amended | 9–0 | full / full / full | not synced |
| CB 121282 (2026-10-20) | passed | 6–3 | full / full / **none** (contradiction) | full / full / **partial** (contradiction) |

- **Both products named every item and stated every outcome correctly**, including "postponed" and "passed as amended".
- **The one split vote is where both went wrong, in the same way.** Both notes give the correct 6–3 count. But both list Councilmember Rinck among the three opposed and leave out Councilmember Lin, who voted no on the record. All 3 passes flagged this for both products. Granola also files Lin among those in favor, so its vote claim is judged "none"; Wispr Flow's is "partial". Both transcripts contain the roll call. For Wispr Flow, the judge reports the transcript itself has the same name. We have not checked the audio, so we cannot say whether the error is in transcription or in note-writing.
- **The judge was stable.** 23 of 27 claim verdicts (3 claims x 9 units) were the same in all 3 passes. Three of the exceptions are one Granola pass on CB 121259 that returned no claim verdicts (3 parser repairs; the other two passes agree on full). The fourth is the Wispr Flow vote on CB 121282 (partial, none, partial).
- **Caveats.** n = 5, one city, one body, items picked because the archive had clear chapter markers. Four of five votes were unanimous, so "the vote" was mostly an easy claim. One judge model, no human labels. On CB 121214, Wispr Flow writes "passed as amended". Legistar records the council action as "passed", with the committee's earlier "pass as amended"; the judge did not count this as a contradiction.
- **Provenance and cost.** Run 2026-11-09 (UTC). Code at `0410265`; the run's working tree also had this prompt file, committed afterwards. Judge `claude-sonnet-5-5`, prompt `council_notes.v1`, 3 passes.
  - Quoted beforehand: $0.61 expected / $0.84 high without prompt caching; $0.39 / $0.61 with it.
  - Sandbox: CB 121282, pass 1, both products, cap $0.20. Spent $0.04; outputs checked.
  - Full run: hard cap $0.60, spent $0.25 (the sandbox calls replayed at $0). **Total $0.29.**

## E4: coverage head-to-head (designed; DEV pilot run, full run not run)

**Question.** Can a "+N missed items" line under a product's notes surface what the notes left out, precisely enough to be worth showing? And does it beat two simple alternatives: writing fuller notes, or asking Granola Chat? This decides whether "Coverage" is pitchable (go / no-go thresholds below, fixed before any TEST data).

| | |
|---|---|
| **Unit** | One product's AI notes for one AMI meeting: E3's units. TEST: the 11 E3 TEST meetings x 2 products = 22 units. DEV: IS1008a (pilot and any prompt change) |
| **Missing gold** | Gold items that E3's judge (majority of its 3 passes, replayed from the disk cache) says the notes do not fully cover. Gold with the E3 errata when present (`gold_version` recorded) |
| **Judge** | Sonnet 5.5, `coverage_judge.v1`, E3's machinery: the same gold, roster and AMI reference transcript, prompt-cached per meeting (meeting part, then the notes). It reads the union of every condition's items, deduplicated by text, in a seeded order, blind to which condition produced them. Per item: kind (decision / action / problem / other), the gold items it states, whether AMI's transcript supports it (yes / partly / no), whether the notes already have it (yes / partly / no, with bullets), and for actions whether the named owner is right. Several passes, combined per item by majority |

**Conditions** (all scored against the same notes):

- **N: notes alone.** E3, no new calls. Defines the missing gold.
- **C3: Coverage, top 3.** List decisions / actions / problems from the product's own transcript with quotes (E1's map + merge, `enumerate.v1` / `enumerate_merge.v1` on Opus 5.5, E1's reader; one combined question per unit instead of one per question type). Drop items whose quote is not in the transcript (string match). E2's check (`agreed_check.v3`, Haiku 5.5): keep agreed decisions, assigned actions, raised problems. A **real** dedupe against the notes' bullets (`coverage_dedupe.v1`, Haiku 5.5, one call per item, notes prompt-cached): drop "already in the notes". Rank: decisions and actions before problems, then items absent from the notes before items partly there, then the check's confidence, then transcript order. Show 3.
- **CU: Coverage, uncapped** (collapsed line with a count).
- **C3S / CUS: strict dedupe.** As C3 / CU, but also drop items the dedupe calls "partly" in the notes. Added after the DEV pilot (below), before any TEST call. Free: same judged items.
- **R: write fuller notes.** One Sonnet 5.5 call (`coverage_regen.v1`) writes notes from the product transcript, told to include every decision, action with owner, and open problem. Shown items = R's bullets the judge calls a decision / action / problem not already in the notes. This filter is the judge itself (an oracle), so R is favoured.
- **RD: fuller notes, real dedupe.** R's bullets under its Decisions / Action items / Problems headings, minus those the same Haiku dedupe finds in the notes. The fair version of R (no oracle).
- **Q: ask Granola Chat.** "What decisions, action items or open problems from this meeting are missing from my notes?", scoped to the meeting's note through the read-only MCP tool `query_granola_meetings` (sequential, rate limits waited out; no write tools). Items = the answer's bullets, citation links and Granola's standing transcript note dropped. Granola only: Wispr Flow has no Q&A tool.

**Metrics** per condition, pooled over units (Wilson and cluster-bootstrap 95% CIs), per product and both:

- **Precision as true omissions.** Lenient: not already in the notes, a decision / action / problem, and states a missing gold item or is fully supported by AMI's transcript. Strict: states a missing gold item. "New": lenient and absent from the notes (not even partly).
- **Recall of missing gold**: the share of missing gold items some shown item (not a duplicate) states. Also for gold missing outright.
- **Duplicates** of notes content (judge: already in the notes). **Wrong owner** among actions that name an owner. **False items** per unit, and items shown in categories where the notes already cover every gold item.
- Paired sign tests per unit: C3 vs R, RD, Q; CU vs C3; C3S vs C3; CU vs RD.
- **Stability** over 3 full re-runs: later (needs fresh listing calls, not disk-cache replays).
- For R: bullets, how many repeat the notes, and the gold items its bullets state.

**Go / no-go** (`data/private/FEATURE_CANDIDATES.md` section 4.4; `GO` in code). Go: precision ≥ 0.70 pooled with lower CI ≥ 0.60; recall of missing gold ≥ 0.35 at K ≤ 3; duplicates ≤ 10%; wrong owner ≤ 10%; ≤ 1 false item per complete category; beats R, RD and Q (higher precision at equal or higher recall of missing gold). If it passes only for one product, or only uncapped, that is reported as such.

**DEV pilot (IS1008a, both products; sanity only, not a result).**

- **Run:** judge 1 pass, E3 baseline `notes_completeness.v4` (cached, $0; IS1008a has no errata, so its gold is unchanged). **Spent $0.305 under a $0.50 cap**: listing $0.159, R $0.037, Haiku check + dedupe $0.014, judge $0.096.
- **Checks done by reading the outputs:**
  - Every listed item's quote was found in the transcript (42 of 42). Some quotes are weak support: "two weeks is is close enough?" for a two-week plan.
  - The Haiku dedupe is too lenient. It called "partly" items whose notes bullet differed only in an added detail, such as budget or "pinpoint the target market". The judge then marked 4 of the 6 C3 items as already in the notes. This is a weakness of the method, not a bug. It is why C3S / CUS were added.
  - Coverage missed the gold items the notes also missed ("discuss the design plans at the next meeting", "first work on a functional design", "insufficient information in advance"). These are meta-level statements the lister rarely phrases as decisions.
  - R's regenerated notes repeat the notes heavily: 13 of 25 bullets (Granola) and 18 of 29 (Wispr Flow) are already there. Its extras are mostly true.
  - Granola Chat's 6 items included 4 already in the notes.
  - No parser repairs.
- **Pilot counts** (both products; n = 14 gold items, too small to compare): C3 2 of 6 shown items true, 4 duplicates, 1 of 10 missing gold recovered. CUS 3 of 3 true, 1 of 10. R (oracle) 12 of 17, 7 of 10. RD 11 of 27, 7 of 10. Q (Granola) 2 of 6, 1 of 4.
- **Full run: not run (over $2).** Re-priced from the pilot ledger x1.5:
  - $18.6 without the E3 baseline. The judge at 3 passes is about $11.5 of it, listing $5.4.
  - The E3 baseline (`notes_completeness.v5`, errata gold) adds up to $4.6 x1.5. It is $0 if the separately quoted E3 v5 re-judge runs first, because those are the same calls.
  - Cheaper options: 1 judge pass (about $11 without E3), or a Sonnet lister (not measured).
- **Code:** `tenor ami coverage-e4` (`--quote`, `--ask-chat`, `--pilot`, `--stage pipeline`, `--e3-prompt`) and [`coverage_e4.py`](../../packages/tenor/evals/coverage_e4.py). Prompts `coverage_dedupe.v1`, `coverage_regen.v1`, `coverage_judge.v1`.
