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
