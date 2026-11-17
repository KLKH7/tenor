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
