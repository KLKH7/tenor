# Notes track

This page defines the notes track: whether a product's notes are faithful to what was said (and, planned, complete), with results on AMI and on the earlier private meetings.

| | |
|---|---|
| **Question** | Is every claim in the notes backed by what was said? Are the important things said present in the notes? |
| **How measured** | Notes split into claims; each claim checked by the [grounding checker](../method/GROUNDING_CHECKER.md) against the product's own transcript and, on AMI, against AMI's human transcript ([`WRONG_FACTS.md`](../method/WRONG_FACTS.md) stage 2); a sample human-checked ([`HUMAN_VERIFICATION.md`](../method/HUMAN_VERIFICATION.md)). Completeness: recall of AMI's annotated decisions, actions and problems ([E3](#e3-notes-completeness-preliminary), Preliminary). Headline, agent reader `notes_reader.v2` (frozen on DEV): weighted recall Granola 0.72, Wispr Flow 0.77 on 11 TEST meetings; Wispr Flow higher on 7, lower on 2, tied on 2 (sign p = 0.18). 291 of 298 items are in the product's own transcript. Previously reported with the API judge: 0.62 / 0.69, 10 of 11, p = 0.01 ([reference reading](#e3-reference-reading-agent-reader-preliminary)) |
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
| **Judge (first reported; replaced as the headline on 2026-11-09)** | Sonnet 5.5 (`notes_completeness.v4`), blind to the product, 3 passes per unit. First each bullet: kind (decision, action, problem, other), the gold items it states, and whether AMI's human transcript supports it (yes, partly, no). Then each gold item: covered fully, partly or not, by which bullets; if not fully, whether the product's own transcript has it, with a verbatim quote that code checks by string match |

- **Recall** per category: full = 1, partial = 0.5; full-only recall reported too. For an action, the owner is part of the item: no owner, or a label nobody can match to a person, is at most partial.
- **Precision** of the notes' decision / action / problem bullets. Lenient: right = states a gold item or is fully supported. Strict: fully supported only, so a gold-matching bullet with a wrong detail counts as wrong. F1 = lenient precision with weighted recall.
- **Heard, not noted.** Gold items absent from the notes that the product's own transcript has: what a better note-writer could recover with no transcription fix. Counted as judged and as quote-verified.
- **Passes.** Each pass ends with a neutral marker ("Judging pass k."), so the disk cache does not replay pass 1. Headline = mean over passes. Alternative = majority vote per item (median coverage, voted kind and links). Spread = range and SD across passes, per unit and pooled.
- **Prompt caching.** Breakpoints after the meeting part (gold + AMI transcript: shared by both products and all passes) and after the product part (transcript + notes: shared by that product's passes). Units run meeting by meeting, passes back to back.
- **Pooling.** Means over meetings with 95% bootstrap CIs; Granola vs Wispr Flow paired per meeting (sign test, wins = Wispr Flow higher).
- **Checks.** Parser repairs and disagreements between the judge's two link lists are counted in every result.
- **Prompt changes on DEV only** (v1 to v4). Bullets judged before gold items, after the two halves disagreed. Explicit rules for generic speaker labels ("Speaker 2"), which the notes may define themselves. One example of a non-match was replaced: it had been taken from the DEV meeting and made the judge reject a real match.
- **Code:** `tenor ami notes-completeness` (`--meetings` to choose AMI meetings) and `tenor ami notes-completeness-pool` ([`notes_completeness.py`](../../packages/tenor/evals/notes_completeness.py)).

### E3 results with the API judge (Preliminary; previously reported)

**Superseded as the headline** by the [reference reading](#e3-reference-reading-agent-reader-preliminary), which disagreed with this judge on 26% of items; a human sided with the reference on all 15 disputed items checked. The tables below are kept unchanged as the first reported numbers.

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

- **(API judge) Both products' notes leave out about a third of what AMI's annotators wrote down.** Under the reference reading this becomes about a quarter (0.72 / 0.77). Pooled weighted recall is 0.62 (Granola) and 0.69 (Wispr Flow). Fully stated: 49% and 56% of gold items. The six new meetings score higher than run 1 for both products (about 0.68 vs 0.73), mostly on decisions. Their gold is decision-heavy, and those decisions are explicit design choices.
- **(API judge) Wispr Flow's overall recall is higher on 10 of 11 TEST meetings (sign test p = 0.01).** Under the reference reading: 7 higher, 2 lower, 2 tied, p = 0.18, not significant. Full-only recall: 8 wins, 1 loss, 2 ties (p = 0.04). F1: 11 of 11. The one recall loss is ES2015c. No per-category comparison is significant. Wispr Flow leads on decisions in 7 of 11 meetings; actions and problems are mixed. Read p = 0.01 with three cautions:
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

### E3 reference reading: agent reader (Preliminary)

**Why.** An independent second reading of all 298 TEST items (a Claude Code agent session, no API calls, each item decided before opening the judge's verdict) disagreed with the v4 judge on 77 items (26%; kappa 0.55, linearly weighted 0.63). On the 15 most informative disagreements a human labelled each item without seeing either verdict or the product, and sided with the second reading on 15 of 15. Rationale and trade-offs: [`DESIGN.md`](../engineering/DESIGN.md#switching-the-notes-and-answers-judge-to-an-agent-reader-2026-11-09).

**Method.** A deterministic packet per meeting and product (`scripts/reader_packets.py`): the gold items (AMI 1.6.2, the same 149 per product as the judge), linked AMI passages (3 turns before, 10 after; the full AMI transcript where an item has no links, as in IB4003), the notes bullets exactly as the judge saw them, and the numbered product transcript; no verdicts. A Claude Code agent (Opus 5.5) follows [`notes_reader.v2.md`](../../prompts/notes_reader.v2.md) and writes one JSONL row per item: notes verdict (full / partial / no), transcript verdict (yes / partly / no), evidence bullets, a verbatim quote, a reason and a confidence. Scoring by `scripts/reader_score.py` (no LLM): recall with full = 1, partial = 0.5, mean per meeting then over meetings; bootstrap CIs over meetings (2,000 resamples, seed 42).
- **Instruction development on DEV only (IS1008a).** v1: two independent reader sessions agreed on 12 of 14 items (both misses one rule gap). v2: two fresh sessions agreed on 14 of 14. v2 was frozen before any TEST item was read.
- **v3 is post hoc.** It was written after two v2 misses on the TEST human cards (owner labels matched through the notes' own bullets; a wrongly matched label = wrong owner; a rejected alternative = the opposite outcome). It is reported as a sensitivity row only.

**Validation, TEST (298 items).**

| Comparison | v2 (headline) | v3 (post hoc) |
|---|---|---|
| vs the second reading | 269 / 298 (90.3%), kappa 0.82, weighted 0.86 | 276 / 298 (92.6%), kappa 0.86, weighted 0.90 |
| vs the human, 15 cards | 13 / 15 (misses: IS1000a Wispr Flow A1, IS1008d Granola D5) | 13 / 15 (misses: IB4003 Wispr Flow A6, IS1008b Wispr Flow D1) |
| vs the v4 judge majority | 219 / 298 (73.5%), kappa 0.54 | 221 / 298 (74.2%) |

v2 and v3 differ on 19 of 298 items, which mixes run-to-run variation with the rule changes.

**Three-run majority (robustness; `test_consensus3.jsonl`).** Median per item of three independent full TEST runs: v2, v3 and a second v3 run (v3c). Unanimous on 270 of 298 (91%); pairwise differences v2–v3 19, v2–v3c 22, v3–v3c 15 (same instructions: 5%); each run vs the majority: v2 13, v3 6, v3c 9. Majority vs the second reading 278 / 298 (93.3%, kappa 0.87, weighted 0.91); vs the human cards 15 / 15. **Caveat:** v3's two rule changes were motivated by two of those cards, so 15 / 15 is not a clean held-out result; v2 alone, frozen before TEST, got 13 / 15. Recall: Granola 0.72 [0.66, 0.78], Wispr Flow 0.78 [0.72, 0.83]. The headline stays on v2 (0.72 / 0.77); item-level statements use the majority, because single runs vary on roughly 5–9% of items (5–7% between any two runs, 9% not unanimous). By category (v2 vs the second reading): decisions 94.6% (kappa 0.85), actions 84.0% (0.75), problems 90.5% (0.83).

**Results, pooled TEST (11 meetings, 149 items per product).**

| Product | Category | Gold | Reader v2 (headline) | Reader v3 (post hoc) | Second reading | API judge, majority (same files) |
|---|---|---|---|---|---|---|
| Granola | decision | 65 | 0.81 | 0.80 | 0.80 | 0.72 |
| Granola | action | 47 | 0.61 | 0.62 | 0.60 | 0.57 |
| Granola | problem | 37 | 0.62 | 0.67 | 0.58 | 0.49 |
| **Granola** | **all** | **149** | **0.72 [0.67, 0.77]** | **0.73 [0.67, 0.79]** | **0.71** | **0.63** |
| Wispr Flow | decision | 65 | 0.89 | 0.84 | 0.89 | 0.76 |
| Wispr Flow | action | 47 | 0.57 | 0.60 | 0.59 | 0.60 |
| Wispr Flow | problem | 37 | 0.74 | 0.74 | 0.72 | 0.56 |
| **Wispr Flow** | **all** | **149** | **0.77 [0.71, 0.82]** | **0.77 [0.72, 0.82]** | **0.78** | **0.70** |

- **Full-only recall (v2):** Granola 0.58 [0.51, 0.64], Wispr Flow 0.65 [0.55, 0.74].
- **Per meeting (v2, Granola / Wispr Flow):** ES2006a 0.75 / 0.65, ES2006c 0.75 / 0.83, ES2008b 0.81 / 0.81, ES2015c 0.81 / 0.88, IB4003 0.62 / 0.83, IS1000a 0.70 / 0.80, IS1008b 0.75 / 0.62, IS1008c 0.88 / 0.88, IS1008d 0.59 / 0.64, TS3006b 0.61 / 0.64, TS3009c 0.67 / 0.86. Wispr Flow higher on 7, lower on 2, tied on 2: **sign test p = 0.18, not significant** (v3: 5 / 3 / 3, p = 0.73). The judge's 10 of 11 (p = 0.01) does not hold.
- **Heard, not noted (v2).** The product's own transcript has 291 of 298 items (Granola 145 yes, 3 partly, 1 no; Wispr Flow 146 yes, 2 partly, 1 no). Items missing from the notes outright: Granola 23 (20 in its transcript, 2 partly, 1 not), Wispr Flow 16 (14, 1, 1). Every partly covered item is in the transcript (one per product only partly). Three-run majority: transcript verdicts identical (291 of 298); missing outright Granola 24 (23 in its transcript), Wispr Flow 19 (18). **Notes leave out about a quarter of what was decided, assigned or raised, while the products' own transcripts contain almost all of it.**
- **Owner sensitivity (v2).** Counting actions whose only gap is the owner as full: Granola 0.763, Wispr Flow 0.827. **Limitation:** in replay there is no calendar invite and all audio arrives on the system channel, so Granola cannot use its usual sources for names; its owner credit may be understated relative to real meetings. Granola tends to omit owners (18 of 40 action bullets in the owner check); Wispr Flow uses "Speaker N" labels nobody can resolve (14 of 30).
- **Not re-read:** precision of the notes' bullets (judge only: lenient 0.94 / 0.96) and the per-run tables above.
- **Caveats.** The reader, the second reading and the judge are all Claude models, so shared blind spots do not show where they agree; the human check covers 15 disagreements only, from one verifier. Agent runs are not seed-reproducible, so each run is stored with its verdict rows (evidence quotes included), instruction version, model, date and git commit. Verdict rows quote product outputs and stay local (`data/private/reader/`, gitignored).
- **Provenance.** Both TEST runs scored 2026-11-09 at `03360ac` (v2 instructions committed there; v3 committed afterwards as `b76ed42`). The score files' `reader_prompt` field reads `notes_reader.v1` because the scorer's default was not overridden; the runs used v2 and v3 as stated here. Reader: Claude Code agent sessions on `claude-opus-5-5`. Cost: no API spend.

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
- **Reference re-read (2026-11-09, agent reader, no API).** It agrees with 26 of the 27 majority verdicts. The exception is Granola's vote claim on CB 121282: **partial, not none.** Its notes give the correct 6–3 count, and two of the three named "no" voters match Legistar; Rinck and Lin are swapped, as in Wispr Flow's notes, which got partial for the same content. Two of the judge's passes also said Granola's transcript has the correct tally. It does not: it reads "Council Member Lin? Yes. … Council member Rink? No.", the same as the notes, so this is not a heard-but-not-noted case. Both products independently heard the same names against the official record; without listening to the audio, the record itself is not confirmed here. With the reference, Granola's vote row is 4 full / 1 partial / 0 none.
- **The judge was stable.** 23 of 27 claim verdicts (3 claims x 9 units) were the same in all 3 passes. Three of the exceptions are one Granola pass on CB 121259 that returned no claim verdicts (3 parser repairs; the other two passes agree on full). The fourth is the Wispr Flow vote on CB 121282 (partial, none, partial).
- **Caveats.** n = 5, one city, one body, items picked because the archive had clear chapter markers. Four of five votes were unanimous, so "the vote" was mostly an easy claim. One judge model, no human labels. On CB 121214, Wispr Flow writes "passed as amended". Legistar records the council action as "passed", with the committee's earlier "pass as amended"; the judge did not count this as a contradiction.
- **Provenance and cost.** Run 2026-11-09 (UTC). Code at `0410265`; the run's working tree also had this prompt file, committed afterwards. Judge `claude-sonnet-5-5`, prompt `council_notes.v1`, 3 passes.
  - Quoted beforehand: $0.61 expected / $0.84 high without prompt caching; $0.39 / $0.61 with it.
  - Sandbox: CB 121282, pass 1, both products, cap $0.20. Spent $0.04; outputs checked.
  - Full run: hard cap $0.60, spent $0.25 (the sandbox calls replayed at $0). **Total $0.29.**

## Owner check: wrong owners on action items (Preliminary)

**Question.** When a product's notes assign an action item to a person, how often is the owner wrong? And can a cheap check flag the wrong ones? This is experiment 3 in the feature scan. Its go threshold was fixed before the run: flag ≥ 60% of owner errors, with ≤ 10% false flags on correct owners, and beat the rule.

| | |
|---|---|
| **Data (no LLM)** | E3's 11 TEST meetings, both products. A bullet is included if its majority kind over E3's 3 judge passes is *action* and most passes link it to at least one gold ACTION item. That gives 70 bullets (Granola 40, Wispr Flow 30). 2 action-linked bullets of another kind were left out |
| **Stated owner** | Found by position rules in code: a leading `(X)`, a leading `X:`, a leading `X to ...`, a parenthesis holding only names, `by X`, `X to ...` in the bullet's detail, or a group phrase. "One person" counts as no owner |
| **Gold owner** | The subject of each linked AMI action sentence ("The Industrial Designer will ..."), as roles. If the gold names a coach or "one team member", the bullet is marked *no gold owner*. **Gold version: original AMI 1.6.2.** `gold_errata.v1.json` did not exist when this work started, so it is not used |
| **Roster** | Self-introductions across the whole AMI series (a–d), matched to roles, with close spellings allowed ("Shreedhar" = Shrida). One hand-resolved name, used for the truth labels only: TS3009's "Thijs" is the Industrial Designer (Granola's transcript: "Well, Thijs, show us", then the components presentation) |
| **Labels** | *correct* (stated roles = gold roles), *wrong* (no overlap), *partial* (some overlap), *missing* (no owner stated), *ambiguous* (an unresolved "Speaker N" or name), *no gold owner* |
| **Checks** | (a) **Rule**: flag an owner that is not a roster name, a role or a group. (b) **Haiku 5.5** and (c) **Sonnet 5.5** (`owner_check.v2`, low effort). Each check gets the bullet, the stated owner, the roster, and ±8 transcript lines around the 2 lines that best match the bullet (lexical match, no gold). It answers correct / wrong / can't tell, plus a suggested owner. Each LLM check runs twice: on the **product's** transcript (Granola's lines are all "Them"), and on an **oracle** variant that uses AMI's human transcript with role labels. No product has the oracle's speaker identities. Flag = "wrong" (strict); "soft" also counts "can't tell" |
| **Prompt changes** | DEV only (IS1008a). In v1, a "Speaker N" owner got "wrong" from some calls and "can't tell" from others, and one call said "wrong" while suggesting the stated owner. v2 fixes both |
| **Code** | `tenor ami owner-check` ([`owner_check.py`](../../packages/tenor/evals/owner_check.py)). Numbers: [`evals/reports/ami/owner_check.json`](../../evals/reports/ami/owner_check.json) |

**Owner labels, pooled TEST (original gold).**

| Product | Bullets | Correct | Wrong | Partial | Missing | Ambiguous ("Speaker N") | No gold owner |
|---|---|---|---|---|---|---|---|
| Granola | 40 | 19 | 1 | 0 | 18 | 0 | 2 |
| Wispr Flow | 30 | 11 | 2 | 1 | 1 | 14 | 1 |

**Spot check (10 labels read against AMI's transcript).** 7 agree. The other 3 are the 3 *wrong* labels, and in each the gold, not the notes, looks wrong:
- **ES2006a (Wispr Flow, 2 bullets): the gold swaps the two designers' tasks.** The UI designer says "at first I will be responsible for ... designing the technical functions". The Industrial Designer answers "Alright" to "you're looking at the working design". `gold_errata.v1` (made independently, at the same time) corrects the same two items.
- **IS1000a (Granola, 1 bullet): doubtful.** The PM says "you" without a name. Only back-channels suggest the same swap, so the gold is left as it is.

With the two ES2006a corrections (`spot_label` in the report), TEST has **1 wrong owner** (doubtful) and 32 correct, among 33 bullets whose stated owner resolves to a person.

- **Wrong owners are rare. Missing and unresolvable owners are common.**
  - Granola states no owner on 18 of its 40 action bullets.
  - Wispr Flow names an owner on nearly every bullet, but 14 of 30 are "Speaker N" labels that nobody reading the notes can resolve.
  - Where an owner resolves to a person, it is wrong on 1 of 20 Granola bullets and 2 of 13 Wispr Flow bullets on the original gold. After the spot check that is 1 of 33 overall.

**Checks, pooled TEST, bullets labelled correct or wrong (original gold: 3 wrong, 30 correct).**

| Condition | Recall (wrong flagged) | Precision | False flags on correct | Soft recall / false flags |
|---|---|---|---|---|
| (a) Rule | 0 / 3 | 0 / 2 | 2 / 30 (7%) | 0 / 3, 2 / 30 |
| (b) Haiku, product transcript | 1 / 3 | 1 / 3 | 2 / 30 (7%) | 2 / 3, 6 / 30 |
| (b) Haiku, oracle transcript | 0 / 3 | 0 / 4 | 4 / 30 (13%) | 2 / 3, 7 / 30 |
| (c) Sonnet, product transcript | 0 / 3 | – (no flags) | 0 / 30 | 2 / 3, 4 / 30 |
| (c) Sonnet, oracle transcript | 0 / 3 | – (no flags) | 0 / 30 | 2 / 3, 3 / 30 |

- **No-go on every condition, and the question cannot be settled on this data.** No check reaches 60% recall. But the test set holds 3 wrong owners on the original gold, and at most 1 after the spot check. Haiku's one hit is an ES2006a bullet that the transcript shows to be correct. On spot-check labels, every LLM recall is 0 / 1, and Haiku on the product transcript has 3 / 32 false flags.
- **What the rule flags is the 14 "Speaker N" labels (14 / 14) and "Thijs" twice** (a correct name with no self-introduction in AMI). It never flags a wrong role, by construction.
- **Sonnet never raised a false flag on a correct owner** (0 / 30 on either transcript). It confirmed 26 of 30 correct owners from the product transcript and 27 of 30 from the oracle. It also agreed with the transcript rather than the gold on one swapped ES2006a bullet. Haiku raised 2–4 false flags.
- **The more promising use is filling in owners, not flagging them.** Results for the suggested owner, given as right / bullets where the check named one (right / all bullets):

| Condition | "Speaker N" bullets (14) | No owner stated (19) |
|---|---|---|
| Haiku, product | 8 / 11 (8 / 14) | 4 / 7 (4 / 19) |
| Sonnet, product | 6 / 6 (6 / 14) | 6 / 7 (6 / 19) |
| Haiku, oracle | 10 / 13 (10 / 14) | 10 / 13 (10 / 19) |
| Sonnet, oracle | 11 / 12 (11 / 14) | 9 / 13 (9 / 19) |

  Sonnet on the product transcript names an owner rarely, but when it does it is right 12 of 13 times. This was not a pre-registered metric, so treat it as a lead.

**Threats to validity.**
- **n of wrong owners is 3 (original gold) or 1 (spot check).** Recall and precision on n ≤ 3 mean nothing, and the go threshold cannot be tested. A real test needs meetings with known owner errors, for example seeded errors.
- **"Speaker N" labels.** 14 of Wispr Flow's 30 owners are labels with no mapping to people. They are counted as *ambiguous*, not wrong. A reader would count them as owner failures.
- **Oracle vs product transcript.** The oracle variant reads AMI's human transcript with role labels, which no product has. Granola's own lines are all "Them", so the product variant can only use names said aloud. Even so, the oracle did not flag better. It helped only with suggestions (for example 11 vs 6 of 14 "Speaker N" bullets for Sonnet).
- **The window is lexical.** It is ±8 lines around the 2 lines that best match the bullet. It can miss an assignment made elsewhere, often at the end of the meeting. That is a plausible cause of many "can't tell" verdicts, but we have not counted it.
- **Stated owners are found by position rules**, and gold owners by the sentence subject. All 70 were reviewed by hand while building the rules, but the rules were never checked on other notes.
- **Gold is one AMI annotator's.** The spot check found 2 owner errors in 33 resolvable cases. The primary labels keep the original gold, as instructed.
- **One run of each check, no repeated passes.**

**Provenance and cost.** Run 2026-11-09 (UTC).
- Code: committed in this change. At run time it was uncommitted on top of `9f6750e` (the result records `dirty`).
- E3 links: runs `20261109T014826Z`, `20261109T063610Z`, `20261109T072942Z` (`notes_completeness.v4`, 3 passes, majority vote).
- Models `claude-haiku-5-5` and `claude-sonnet-5-5`, prompt `owner_check.v2`, seed 42, window ±8, 2 anchors.
- Quoted beforehand: $0.92 expected / $1.29 high for the full TEST run (280 calls). The DEV pilot re-priced it at $0.66 ($0.99 with a 1.5× margin).
- Spend:
  - DEV pilots (IS1008a, cap $0.20 each): $0.075 (v1) and $0.076 (v2).
  - Full TEST run (hard cap $1.80): $0.598 (Haiku $0.031, Sonnet $0.567).
  - **Total $0.75.**

## E4: coverage head-to-head (TEST run 2026-11-09, scored by agent readers; Preliminary)

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

**TEST result (2026-11-09; Preliminary).**

- **Judge changed before TEST.** The API judge above was replaced by Claude Code reader agents (see "How results are judged" in the README): `prompts/e4_reader.v2.md`, condition-blind (all conditions' items pooled, exact duplicates merged, seeded shuffle; the key back to conditions is kept outside the packets). v1 → v2 on DEV only (two readers on v1: true 73/73, in_notes 69/73, gold match 70/73; on v2: true 73/73, in_notes 72/73). Three independent full TEST runs; labels are the median of the three (unanimous on `true` for 803 of 905 items, `in_notes` 799). "Missing gold" = gold items not fully in the notes per the E3 three-run majority (104 items over 22 units).
- **Generation** (`--generate-only`, no API judge): **$3.67** under an $8 cap (quoted high $7.68); prompt-cache reads verified in a pilot first (dedupe: 206k cached vs 5k uncached input tokens). DEV candidates $0.01 (cached). Q: 11 Granola Chat answers via the read-only MCP, $0.
- **Results, both products pooled** (cluster-bootstrap 95% CIs over meetings; Q is Granola only):

| Condition | Items shown | True and new (not in notes) | Duplicates of the notes | False | Missing gold recovered |
|---|---|---|---|---|---|
| C3: Coverage, top 3 | 66 | 30% [20, 42] | 18% | 3% | 8/104 (8% [0, 16]) |
| CU: Coverage, uncapped | 223 | 38% [27, 48] | 14% | 2% | 27/104 (26% [10, 41]) |
| C3S: strict, top 3 | 48 | 60% [48, 73] | 0% | 4% | 1/104 (1%) |
| CUS: strict, uncapped | 89 | 71% [59, 78] | 0% | 2% | 8/104 (8%) |
| R: fuller notes (oracle-filtered) | 264 | 26% [22, 30] | 0% | 2% | 40/104 (38% [21, 57]) |
| RD: fuller notes, real dedupe | 312 | 21% [18, 26] | 17% | 2% | 37/104 (36% [21, 50]) |
| Q: ask Granola Chat | 59 | 12% [3, 22] | 36% | 3% | 7/61 (11% [4, 20]) |

- **Go / no-go: no go for Coverage.** At K ≤ 3 it recovers 8% of missing gold (threshold 35%) with 18% duplicates (threshold 10%); the strict version has no duplicates but recovers 1%. Uncapped Coverage recovers 26% at 38% precision. It does not beat R or RD.
- **Fuller notes recover the most and rarely invent.** Rewriting the notes from the transcript recovers 36–38% of the missing gold with 2% false items, but shows 12–14 items per meeting, most of them already in the notes.
- **Asking Chat repeats the notes**: a third of its items are already there; it recovers 11%.
- **Half the gap survives every condition.** Taking the union of all conditions, 49 of 104 missing items are fully recovered. Of the 55 that are not, 49 are in the product's own transcript, 39 are recovered partly, and **17 are action items short only of an owner**: the transcript does not say who spoke (all remote speech arrives on one "Them" channel), so no rewrite of the transcript can name them. Owner attribution, not summarisation, is the largest single residual.
- **Caveats.** One run of generation (no seeds to vary on the models); R's filter uses the reader as an oracle (RD is the fair version); readers share a model family with the generators; the missing-gold set comes from post-hoc-validated E3 labels on the original gold; 11 meetings.
- Data: `data/ami/results/e4/20261109T092238Z.candidates.json` (local), reader runs `data/private/reader/runs/e4_test_{a,b,c}/`, consensus and scores `data/private/reader/runs/e4_consensus_score.json`.

## Gap check (G1, G2; Preliminary)

**Question.** Two parts of the "gap check" proposed in `data/private/FEATURE_DECISION.md`, simulated with one Sonnet 5.5 call each and scored at $0 from the existing agent-reader labels (no API judge). (G1) Shown as a diff (only what is missing, at most 5, quoted), do E4's fuller-notes items keep their recovery without the duplicates? (G2) Can a model suggest owners for action items the notes leave without one, from the transcript alone (S1) or with a calendar-style attendee list (S2)?

| | |
|---|---|
| **G1 unit / input** | E4's 22 TEST units (DEV: IS1008a, 2 units). Input: condition R's regenerated bullets per unit (`data/ami/results/e4/20261109T092238Z.candidates.json`, DEV `20261109T101313Z`); no new generation |
| **G1 call** | `gap_diff.v1` (Sonnet 5.5, medium effort): keep only bullets that are a decision / action / open problem, not stated in the notes even partly, merged when two say the same thing, ≤ 5, ranked; each with a quote from the product transcript. Quotes are verified by string match (`list_check.quote_found`); unverified items are dropped. The model returns R ids, so a shown item's text is the R bullet's verbatim |
| **G1 scoring** | Each shown item mapped to its E4 K id through `data/private/reader/e4_keys/<run>.key.json` (source `regen:R<n>` and equal normalised text); labels from the 3-run consensus `data/private/reader/runs/e4_test_consensus/`; missing gold from `test_consensus3.jsonl` (104 items). Same counts as `e4_reader.unit_counts` |
| **G2 units** | E3 TEST gold actions where (A) the notes' consensus verdict is partial and the reason says the owner is missing / unmatched (regex `OWNER_REASON`, excluding "gold names no owner"): 20; or (B) the notes cover it but no evidence bullet states a usable owner (none, vague, or only "Speaker N", by `owner_check`'s position rules): 13. 33 units (28 distinct bullet sets); 3 left out because the gold owner is a coach or "one team member" |
| **G2 call** | `owner_suggest.v1` (Sonnet 5.5, low effort): the evidence bullet(s) → an owner as the transcript names them, or "unsure", with a quote. **S1**: transcript + notes. **S2**: plus an attendee list with the AMI roster's names and roles (**simulated: AMI has no calendar invite**; roles whose holder never says their name are listed without one; IB4003 has no roster at all) |
| **G2 scoring** | The suggestion resolved to AMI roles with `owner_check.resolve` (series roster, close spellings, hand-resolved names). Correct = equals the gold owner's roles. "unsure" or a quote not in the transcript = no suggestion. Gold: original AMI 1.6.2 (what the E3 labels used); errata v1 as sensitivity |
| **Prompt cache** | Shared system prompt `gap_system.v1`, then transcript + notes with a breakpoint, task and varying part last. G1 and G2 cannot share a cache entry (the output schema is part of the cached prefix), so G1's writes are not reused; G2's S1/S2 calls on a unit read it |
| **Code** | `tenor ami gap-check` (`--quote`, `--pilot`, `--max-usd`), [`gap_check.py`](../../packages/tenor/evals/gap_check.py). Numbers: [`evals/reports/ami/gap_check.json`](../../evals/reports/ami/gap_check.json); raw outputs local in `data/ami/results/gap_check/` |

**G1 results, TEST** (cluster-bootstrap 95% CIs over meetings, seed 42; E4 rows from the table above).

| Condition | Items shown | Per unit | True and new | Duplicates of the notes | Partly in the notes | False | Missing gold recovered (full) |
|---|---|---|---|---|---|---|---|
| **G1: diff, pooled** | 56 | 2.5 [2.0, 3.1] | 36% [20, 51] | 5% [0, 12] | 41% [22, 60] | 0% | **1/104 (1% [0, 3])** |
| G1, Granola | 37 | 3.4 | 38% [18, 60] | 3% [0, 10] | 41% | 0% | 1/61 |
| G1, Wispr Flow | 19 | 1.7 | 32% [15, 47] | 11% [0, 27] | 42% | 0% | 0/43 |
| C3: Coverage, top 3 | 66 | 3.0 | 30% [20, 42] | 18% | – | 3% | 8/104 (8%) |
| R: fuller notes (oracle-filtered) | 264 | 12.0 | 26% [22, 30] | 0% | – | 2% | 40/104 (38%) |
| RD: fuller notes, real dedupe | 312 | 14.2 | 21% [18, 26] | 17% | – | 2% | 37/104 (36%) |

- Every shown item mapped to a labelled K id (0 unlabelled). 1 returned item was dropped for a quote not in the transcript. 3 shown items are of kind "other". Partly-recovered missing gold (full or partial match): 7/104.
- **The diff removes the duplicates but loses almost all of the recovery.** Duplicates fall from RD's 17% to 5%, false items to 0, and the list shrinks from 14 to 2.5 items per unit. But it recovers 1 of the 104 missing gold items, against 37–40 for the full rewrite. The model chose true items that are mostly not the ones AMI's annotators recorded, and 41% of what it showed is still partly in the notes by the readers' standard. **No-go for G1 as specified;** "show only the difference" is not a free filter on top of the rewrite.
- One run, one prompt version (no DEV tuning after the pilot), so the shortfall may be partly the prompt's "when in doubt, drop it" and the cap of 5; not tested.

**G2 results, TEST** (33 owner-less or unmatched gold actions; Wilson 95% CIs, meeting-cluster CIs in the report).

| Setting | Group | Suggested (coverage) | Correct when suggested | Correct of all | Wrong | Ambiguous |
|---|---|---|---|---|---|---|
| S1: transcript only | pooled | 7/33 (21% [11, 38]) | 2/7 (29% [8, 64]) | 2/33 | 3 | 2 |
| | Granola | 4/16 | 1/4 | 1/16 | 3 | 0 |
| | Wispr Flow | 3/17 | 1/3 | 1/17 | 0 | 2 |
| S2: + simulated invite | pooled | 10/33 (30% [17, 47]) | 7/10 (70% [40, 89]) | 7/33 | 2 | 1 |
| | Granola | 6/16 | 3/6 | 3/16 | 2 | 1 |
| | Wispr Flow | 4/17 | 4/4 | 4/17 | 0 | 0 |

- By unit source, S2: (A) partial-owner verdicts 5/20 suggested, 2/5 right; (B) owner-less bullets 5/13 suggested, 5/5 right. S1: (A) 3/20, 0/3; (B) 4/13, 2/4.
- **Ambiguous mappings (listed, counted as suggested and not correct):** IB4003 (natural meeting, no AMI roster): "Andre" (Granola A1, S2), "Andreas" (Wispr Flow A1 and A2, S1). The gold owner is the meeting lead; whether he is Andre(as) cannot be checked against AMI.
- **All 5 wrong suggestions are "everyone" for a task the gold gives to one person** (ES2008b A3 in S1 and S2; ES2015c A1, A2 in S1; IS1000a A1 in S2). In ES2015c the transcript defers the tasks to the personal coaches, so the gold may be the weaker side; not adjudicated.
- One S2 suggestion was dropped because its quote was not in the transcript (it would have been correct: 7/11 ignoring the quote check). Errata v1 changes one G2 gold owner (ES2006a A3, Granola); neither setting suggested an owner for it, so the numbers are unchanged.
- **Reading.** From the transcript alone the model rarely names an owner and is usually wrong when it does: this is the replay's "all speech is Them" limit. With names and roles from an invite it names an owner for 30% of the actions and is right 7 of 10 times, all wrong ones being "everyone". Far below the earlier lead (12 of 13 right, `owner_check`, a different prompt and subset), and n = 10 suggestions. Part (b) still needs a test with a real invite and the mic channel (FEATURE_DECISION experiment 1).

**Threats to validity.** One run per call, no seeds to vary; 11 meetings; readers share a model family with the generator; S2's attendee list is simulated from AMI self-introductions (incomplete: 7 of the 11 TEST meetings miss at least one name, IB4003 has none), and a real invite carries names but rarely roles; G2 scores by role equality, so "everyone" for a one-person task is wrong even where the transcript is vague; G1's missing gold comes from post-hoc-validated E3 labels on the original gold.

**Provenance and cost.** Run 2026-11-09 (UTC), code at `91cc5dc` (clean). Model `claude-sonnet-5-5`; prompts `gap_system.v1`, `gap_diff.v1`, `owner_suggest.v1`; seed 42 (bootstrap only).
- Quoted beforehand: TEST $1.24 expected / $3.09 high (no cache); the pilot ledger re-priced it at $0.85 ($1.28 with x1.5).
- DEV pilot (IS1008a, cap $0.30): **$0.064**. Outputs read: quotes verbatim, G1 kept 2–3 items per unit, owner suggestions sensible (one "unsure" in S1 resolved in S2).
- TEST (hard cap $3.10, the high estimate rounded up): **$1.209**. Prompt-cache evidence (live calls): S2 28 calls, 294k cache-read vs 14k uncached input tokens, all 28 with reads; S1 156k read / 137k written; G1 13k read / 206k written (no reuse possible, see above).
- **Total $1.27.**
