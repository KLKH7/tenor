# Retrieval lab: Granola's MCP retrieval, rebuilt locally on AMI

Goal: a small, honest copy of the system described in Granola's engineering post "How we help
your agent find what it's looking for" (Josh Tuddenham, Nov 2026), so ideas (tool wording, a
per-series record) can be measured the way Granola measures them.

Code: `packages/tenor/retrieval/` — `chunks.py` (step 1: chunking, BM25), `tools.py` (step 2:
the four tools, logged), `server.py` (stdio MCP server), `loader.py` (AMI workspace with realism
switches), `harness.py` (step 3: retrieved / retained / accurate). CLI for agents:
`scripts/retrieval_tools.py`. Tests: `tests/test_retrieval_*.py`.

## What the post says, and what we copy

| Post | Here | Status |
|---|---|---|
| `search_meetings`: date-ordered list, or BM25 over notes when keywords are given | `search_meetings` | Copied (no dates: AMI has none) |
| Per-workspace BM25 transcript index (Turbopuffer); transcripts cut into chunks | `chunks.py`, BM25 by hand | Copied |
| Each chunk tagged with attendees, who spoke in it, a version string | `Chunk.speakers`, `Chunk.version` | Copied (attendees = speakers) |
| Agent reads chunks, not whole transcripts | `get_transcript(meeting, chunk_ids)` | Copied |
| Retrieved: the passage was in a tool response, checked deterministically | `harness.seen_lines` over the call log | Copied |
| Retained: ROUGE-L recall, calibrated on 90 hand labels | ROUGE-L >= 0.30, calibrated on 84 blind *agent* labels | Copied, weaker labels |
| Accurate: LLM judge + two hard fails (cites an unread meeting; quotes words in no tool response) | reader verdict + `cited_unopened`, `unsupported_quotes` | Copied |
| n = 10 repeats per question | `Trial.run`; pass rate per question = mean over its runs | Supported; runs cost time |
| Tool descriptions and server instructions as the lever | `WORDING` versions in `tools.py` | Copied (step 4) |

## ASSUMED (not stated in the post; our choice)

| What | Our value | Why |
|---|---|---|
| Chunk size and overlap | 15 lines, 3 overlap | Not published. A line is one AMI turn (a few seconds to a minute). |
| Results per search | k = 5 chunks | Not published. |
| BM25 parameters | k1 = 1.2, b = 0.75 | Common defaults; Turbopuffer's are not published. |
| Tokenisation | lowercase words and numbers, small stopword list, no stemming | Not published. |
| What "opened a meeting" means | its notes (get_meetings) or any of its chunks were returned | A title in a list does not count. |
| Quote check | spans of >= 3 words in "…", normalised (case, punctuation) | The post says "words that weren't in any tool response". |

## Realism: easier or harder than a real Granola workspace

| Factor | Here | Reality | Effect on scores | Switch |
|---|---|---|---|---|
| Workspace size | one series (4 meetings) | hundreds of meetings | Finding the meeting is far too easy | `distractors=` other AMI meetings |
| Notes | none by default | an AI note per meeting, read first | Harder than reality without notes | `notes="product"`: Granola's real notes (IS1008 a-d, ES2006 a, c only) |
| Notes leak | `notes="ami_summary"` | — | **Leaks the answer key** (facts are its sentences); debug only | — |
| Speakers | AMI role labels | channel only (Me/Them), no names | Too easy on who-said questions | `speakers="anonymous"` |
| Transcript | AMI human transcript | ASR (errors, missed words) | Too easy | Not modelled yet (product transcripts exist for the same meetings, but their line ids differ from the gold's) |
| Questions | template questions that name the meeting | people rarely name the meeting | Search too easy | Not modelled yet: a rewrite without meeting names |
| Meetings | role-played design meetings | real work meetings | Unknown direction | — |
| Dates | none | dated meetings, date filters | Date questions untestable | — |
| Reader for "accurate" | blind Claude Code agents | an LLM judge | Comparable; both are models | — |
| Repeats | small n (cost) | n = 10 | Wider uncertainty | `run` |

The realistic default for a run is: `notes="product"` where available, `speakers="anonymous"`,
and at least 20 distractor meetings from other series. Report every result with its switches.

## Pilot (2026-11-17, development series IS1008 only; not a result)

Setup: IS1008 a-d plus 20 distractor meetings (seed 42), Granola's real notes, anonymous
speakers; 3 questions x 2 runs; Claude Code (sonnet) answer agents through the CLI tools
(`retrieval_answer.v1`); two blind opus readers (`mcp_reading_reader.v1`), agreed 6 of 6.
Code: `scripts/retrieval_pilot.py`; outputs in `data/private/retrieval/pilot/`.

What the pilot changed in the harness (before any test run):
- **Quotes from the question are allowed.** One answer quoted the question's own phrase
  ("recognition, function, locator") and was failed as an unsupported quote; a false alarm.
- **Notes count as retrieval.** "Retrieved" counted only transcript lines; facts the agent read
  in Granola's notes were missed. Now a fact also counts if its words are in any 5-line window
  of any tool output (ROUGE-L recall >= 0.5).

Known limit, left visible: Granola's notes paraphrase, so the text rule still misses facts the
notes do convey (recall 0.14-0.21 on IS1008d's two problem facts, both answered correctly).
A blind reader label ("is this fact in what the agent was shown?") would fix it; not built yet.

Pilot numbers (for the pipeline only): 3 of 6 trials passed; one not retrieved, one not
retained, one not accurate; 17k characters read per trial on average (27k for the
cross-meeting question). Agents read the notes first, then searched transcripts (4-7 calls).

## Step 5: the running record as a tool (2026-11-17, IS1008 = the record's development series; not a result)

Same workspace as the pilot (IS1008 a-d + 20 distractors, Granola's notes, anonymous speakers),
all 11 IS1008 cross-meeting questions, 2 runs each, answer prompt `retrieval_answer.v2`; the only
difference is a fifth tool, `get_series_record` (the v2 record after IS1008d, compact view, every
pointer tagged with its chunk id). Readers: two blind opus readers per condition
(`mcp_reading_reader.v1`), agreed 22/22 (tools) and 20/22 (record); one tie-break settled 2.
Code: `scripts/retrieval_record.py`; outputs `data/private/retrieval/record_dev/`.

| | tools | tools + record |
|---|---|---|
| Reader verdicts correct / partial / incorrect | 13 / 8 / 1 | 13 / 8 / 1 |
| Trials passed (correct and no hard fail) | 11 of 22 | 10 of 22 |
| Unsupported quotes (hard fail) | 6 | 5 |
| Facts retrieved | 73% | 80% |
| Characters read per trial | 16,500 | 32,700 |
| Agents that opened the record | - | 22 of 22 |
| search_transcripts / get_transcript calls | 42 / 16 | 19 / 62 |

What it says: the record changed how agents worked (read the record, then followed its pointers
into chunks instead of keyword search) but not how often they were right, and it doubled what
they read. Per question the record won 1, lost 2, tied 8. The largest single failure in both
conditions is the one Granola's post describes: lightly reworded quotes (5-6 of 22 trials).

Harness fix found while checking the flags: a quote that runs over two transcript lines was
failed because the next line's label sat between its halves; labels are now ignored. The other
flags were read and are real rewordings (a dropped word, an invented phrase, gaps joined by an
ellipsis). Ellipsis quotes are failed, as in the post's strict rule.

Next: the post's fix for reworded quotes ("paraphrase by default; quote only when asked") as a
wording variant (step 4), on both conditions; then the record comparison on an unused series.

## Step 4: the post's quote fix as wording (2026-11-17, IS1008 dev; not a result)

One change from step 5: the server instructions gain "Paraphrase by default; quote words
directly only when the person asks for a direct quote." (`WORDING["v2"]`). Same workspace,
questions, runs, answer prompt and reader prompt; fresh agents. Readers agreed 21/22 and 19/22;
a tie-break settled 4. Outputs `data/private/retrieval/record_dev_v2/`.

| | v1 tools | v2 tools | v1 + record | v2 + record |
|---|---|---|---|---|
| Quotation marks in all 22 answers (per wording, both conditions) | 124 | 59 | (with v1) | (with v2) |
| Unsupported quotes (hard fail) | 6 | 3 | 5 | 3 |
| Trials passed, of 22 | 11 | 12 | 10 | 12 |
| Reader verdicts correct / partial / incorrect | 13/8/1 | 13/8/1 | 13/8/1 | 13/8/1 |
| Facts retrieved | 73% | 66% | 80% | 84% |
| Characters read per trial | 16,500 | 15,900 | 32,700 | 29,700 |

The same verdict totals hide movement: 6 of 22 verdicts changed between wordings in each
condition, in both directions; question difficulty dominates the totals.

What it says: the post's fix reproduces in direction. Halving the quoting halved the
unsupported-quote failures and raised passes slightly, with no loss in reader-judged accuracy
here (the post saw some give-back). The remaining flags are mostly the agent putting quote
marks around its own paraphrase ("no display for now, pending cost"). With the fix, the record
condition ties tools-only on passes (12 vs 12), gets more facts in front of the agent (84% vs
66%) and reads about twice as much. n = 22 trials per cell on a development series: directions,
not estimates.

## Correction (2026-11-17): steps 4 and 5 re-scored

The scoring scripts collected reader verdicts with the file pattern `verdicts_<condition>_*`,
and `verdicts_tools_*` also matched `verdicts_tools_record_*`. The tools-only condition was
therefore scored with a mix of its own and the record condition's verdicts (the last file read
won), which is why both conditions showed the same 13/8/1. The pattern now matches exact names;
both runs were re-scored from the same saved verdicts. The step 4 and step 5 tables above are
wrong for the tools-only column; the corrected figures:

| | v1 tools | v1 + record | v2 tools | v2 + record |
|---|---|---|---|---|
| Reader verdicts correct / partial / incorrect | 15/7/0 | 13/8/1 | 17/5/0 | 13/8/1 |
| Trials passed, of 22 | 12 | 10 | 15 | 12 |
| Unsupported quotes (hard fail) | 6 | 5 | 3 | 3 |

Corrected reading: on IS1008 the record made answers slightly worse, not equal (13 vs 15 and
13 vs 17 correct), while reading twice as much. The quote fix helped the tools-only condition
(passes 12 -> 15, correct 15 -> 17) and the record condition (passes 10 -> 12).

## Test-series run: tools vs tools + record (2026-11-17; the record's first unseen series)

Frozen at e84d829 (+3ec76df: three empty distractor meetings dropped before any answer).
Workspace: ES2005, ES2006, ES2012, IS1006, TS3007 (20 meetings) + 13 distractors; stand-in AI
notes for every meeting (`standin_notes.v1`, written from anonymous transcripts); anonymous
speakers; wording v2. 12 cross-meeting questions x 3 runs per condition; sonnet answerers;
two blind opus readers per (series, condition) packet with runs shuffled as X/Y/Z; agreement
36/36 (record), 25/36 (tools), 11 settled by a third reader. Outputs `data/private/retrieval/test/`.

| | tools | tools + record |
|---|---|---|
| Reader verdicts correct / partial / incorrect | 20 / 16 / 0 | 25 / 11 / 0 |
| Trials passed (correct, no hard fail) | 18 of 36 | 21 of 36 |
| Hard fails: unsupported quote / cited an unopened meeting | 4 / 0 | 4 / 3 |
| Facts retrieved | 92% | 87% |
| Characters read per trial | 14,300 | 24,400 |
| Agents that opened the record | - | 36 of 36 |
| Per question (correct runs): record ahead / behind / level | | 4 / 2 / 6 |

Difference in share of runs correct, record minus tools: +14 points, 95% interval -11 to +39
(bootstrap over the 12 questions, seed 42). Not distinguishable from zero.

Notes:
- The 3 "cited an unopened meeting" fails are record answers naming the meeting where an item
  was first recorded (ES2005a, IS1006a): the record describes that meeting but the agent never
  opened it. The frozen rule fails them; counting the record's description as "opened" would
  give the record 24 passes.
- Direction differs from the development series (IS1008), where the record was slightly worse
  (13 vs 17 correct with wording v2). Two small samples pointing opposite ways: no claim either way.
- Cost is consistent across both: the record condition reads 1.7-2x the characters.

## All 62 test questions (2026-11-17)

Same frozen setup as the cross-meeting run, extended to the 50 single-meeting questions
(c78603c). 62 questions x 3 runs x 2 conditions = 372 answers (3 reruns after network drops,
partial logs deleted first). Readers agreed on 289 of 300 single-meeting answers; 11 settled by a
third reader. Scores: `data/private/retrieval/test/score_all.json`.

| | tools | tools + record |
|---|---|---|
| Reader verdicts correct / partial / incorrect (of 186) | 98 / 46 / 42 | 120 / 37 / 29 |
| Trials passed | 87 | 111 |
| Hard fails: unsupported quote / cited an unopened meeting | 18 / 0 | 5 / 11 |
| Facts retrieved / retained | 79% / 81% | 83% / 87% |
| Characters read per trial | 16,700 | 43,600 |

Share of runs correct, record minus tools (bootstrap over questions, seed 42):

| Questions | n | tools | record | difference [95% interval] |
|---|---|---|---|---|
| All | 62 | 98/186 | 120/186 | +11.8 [+3.2, +21.5] |
| Cross-meeting | 12 | 20/36 | 25/36 | +13.9 [-11.1, +38.9] |
| Single-meeting | 50 | 78/150 | 95/150 | +11.3 [+2.0, +22.0] |
| Role-dependent (action_who, who_said) | 20 | 28/60 | 39/60 | +18.3 [+1.7, +38.3] |
| Not role-dependent | 42 | 70/126 | 81/126 | +8.7 [-1.6, +19.8] |

**Confound: the record knows speaker roles; the tools condition does not.** The records were
written (phase 2) from AMI transcripts with role labels (`meeting_memory.transcript`,
`speaker=role`), so entries carry owners like "Industrial Designer". In this workspace speakers
are anonymous ("Them"). On questions that depend on who someone is, the record has information
the other condition cannot reach; that is where its largest gain is. A fair version rebuilds the
records from anonymous transcripts. Without the role-dependent questions the gain is +8.7 points
and its interval includes zero.

Also: the record condition reads 2.6x the characters (it is loaded even for single-meeting
questions), and 11 of its runs fail the frozen "cited an unopened meeting" rule by naming a
meeting the record describes but the agent never opened.

## Fair rerun: records written from anonymous transcripts (2026-11-17)

To remove the confound above, the five test-series records were rebuilt from transcripts where
every speaker is "Them" (`memory_update.v3`: as v2, but an owner is recorded only when the words
name them; `meeting_memory_v2.py --anon`, into `v2_anon/`). Same build as before: one writer and
one quiet-check agent per meeting (opus), all 20 records pass the code checks with 0 problems.
Owners named in the final records fell from 50 to 25, all taken from spoken words ("I'm the
marketing expert", first names); role words in the records fell from 570 to 108.

Only the record condition was rerun (`tools_record_anon`): same 62 questions x 3 runs, workspace,
wording v2, answer prompt, blind readers (two per packet; 178 of 186 agreed, 8 settled by a third).
The tools condition is unchanged, so its 186 runs are reused.

| | tools | record (roles) | record (anonymous) |
|---|---|---|---|
| Correct / partial / incorrect | 98 / 46 / 42 | 120 / 37 / 29 | 109 / 46 / 31 |
| Passed all checks | 87 | 111 | 98 |
| Hard fails: unsupported quote / cited an unopened meeting | 18 / 0 | 5 / 11 | 10 / 3 |
| Facts retrieved / retained | 79% / 81% | 83% / 87% | 81% / 85% |
| Characters read per trial | 16,700 | 43,600 | 27,400 |

Anonymous record minus tools, share of runs correct (bootstrap over questions, seed 42):

| Questions | n | difference [95% interval] |
|---|---|---|
| All | 62 | +5.9 [-1.6, +14.0] |
| Cross-meeting | 12 | +8.3 [-13.9, +33.3] |
| Single-meeting | 50 | +5.3 [-2.7, +14.0] |
| Role-dependent | 20 | +5.0 [-1.7, +13.3] |
| Not role-dependent | 42 | +6.3 [-4.0, +18.3] |

**Result: with the role advantage removed, the record's gain halves (+11.8 to +5.9 points) and
is no longer distinguishable from zero.** The gain on role-dependent questions mostly
disappears (+18.3 to +5.0), which confirms most of the earlier gain there came from role labels
the agents could not see. The direction is still positive in every group, and the record still
cuts unsupported quotes (18 to 10) at 1.6x the reading. What this supports: a running record
built from what a product actually has (anonymous "Them" speech) may help a little; this
sample of 62 questions on five series cannot show that it does.
