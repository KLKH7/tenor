# AMI questions for the evidence-compression experiment

311 questions about 33 AMI meetings, for [EVIDENCE_COMPRESSION.md](../../docs/method/EVIDENCE_COMPRESSION.md). Built by fixed templates from AMI's human annotations; no LLM wrote any question or answer.

**Source and licence.** AMI Meeting Corpus, manual annotations v1.6.2 (University of Edinburgh and the AMI Consortium), CC BY 4.0. Gold answers quote the AMI annotators' abstractive summaries; who-said-it questions quote AMI's manual transcript. Regenerate with `uv run tenor ami questions --seed 42` (byte-identical; a test checks it).

| File | What |
|---|---|
| `questions.jsonl` | One question per line: `type`, `template`, `meetings`, `question`, `gold_answer`, `gold_evidence` (AMI dialogue-act IDs), `evidence_source`, `fact_group`, `notes_can_answer` (null for now), `split`, **`subset`**, `seed` |
| `closed_book.jsonl` | The same questions exactly as the closed-book control sends them: no meeting at all |
| `meetings.json` | Which meetings, the seeded rule that chose them, and the full candidate order |
| `replay_extension.jsonl` | 109 questions about the 7 meetings added to the replay on 2026-11-05 (subset `replay_extension`); `uv run tenor ami extension-questions --seed 42` |
| `replay_extension_closed_book.jsonl` | The same, as the closed-book control sends them |

## Two subsets: report them apart

| Subset | Meetings | Questions | Notes the reader gets |
|---|---|---|---|
| `recorded` | IS1008a–d, IB4003, EN2002b (played into Granola and Wispr) | 68 | AMI's abstract now; real product notes once synced |
| `annotation_only` | 6 series (ES2006, IS1006, TS3009, ES2012, IS1003, TS3007) + IB4005, IB4010, IB4011 | 243 | AMI's abstract, a **stand-in**, less realistic |

**How meetings were chosen.** The recorded six, plus every other non-scenario meeting with a summary (only IB4005, IB4010, IB4011 exist). Then whole scenario series: of AMI's 32 series with a summary and summary links in all four sessions, each site's (ES, IS, TS) were shuffled with seed 42 and taken round-robin until the set reached 300 questions. Whole series keep the cross-meeting questions working.

| Type | DEV | TEST | recorded | annotation_only | Built from |
|---|---|---|---|---|---|
| decision | 1 | 72 | 10 | 63 | decision sentences, grouped by subject |
| action_who | 4 | 42 | 15 | 31 | action sentences whose subject is a role |
| action_what | 4 | 52 | 16 | 40 | action sentences, grouped by subject |
| problem | 1 | 28 | 4 | 25 | problem sentences, one list per meeting |
| who_said | 3 | 81 | 12 | 72 | a seeded sample of summary-linked dialogue acts, quoted; gold is the role, by participant code |
| what_changed | 0 | 23 | 11 | 12 | session k action item → what that role presented in session k+1; a decision and its closest decision next session |

## Replay extension (2026-11-05): a separate file

Seven more meetings are being played into the products ([AMI_REPLAY.md §11](../../docs/method/AMI_REPLAY.md)). Their questions live in `replay_extension.jsonl` so `questions.jsonl` and every score built on it stay unchanged. Same templates and seed; all TEST (the DEV rule is IS1008a only). One meeting per team, so there are no cross-meeting questions.

| Type | TS3009c | ES2015c | ES2008b | ES2006c | TS3006b | ES2007c | IS1000a | Total |
|---|---|---|---|---|---|---|---|---|
| decision | 4 | 7 | 4 | 4 | 5 | 3 | 4 | 31 |
| action_what | 2 | 5 | 6 | 4 | 3 | 4 | 3 | 27 |
| problem | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 7 |
| action_who | 2 | 4 | 4 | 3 | 3 | 4 | 3 | 23 |
| who_said | 3 | 3 | 3 | 3 | 3 | 3 | 3 | 21 |
| **all** | 12 | 20 | 18 | 15 | 15 | 15 | 14 | **109** |

**Overlap:** TS3009c and ES2006c are also in `questions.jsonl` (subset `annotation_only`). Their questions here have the same `id`, wording and gold; only `subset` differs. Join on `id`, and never pool the two files without deduplicating.

**Limits, read before using.**
- **DEV = questions about IS1008a only** (13). Everything else is TEST.
- **EN2002b gives no questions** (no summary or dialogue acts in AMI 1.6.2). It still counts for compression sizes.
- **IB4003–IB4011 have no gold evidence** (no summary links) and no who-said-it questions (no scenario roles). IB4005–IB4011 also have no dialogue acts; their transcript is AMI's words grouped into speaker turns.
- **Lists over 5 sentences are dropped**: judge.v1 needs every item for "correct".
- **Summary-level questions.** Most gold answers are summary sentences, so notes resembling that summary can answer them. Only `who_said` is a pure detail question.
- **`action_who` and `action_what` on one sentence share a `fact_group`**: they are not independent.
