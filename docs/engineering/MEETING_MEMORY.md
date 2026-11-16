# Meeting memory for agents (phase 1 design)

Status: design and a one-series prototype. Not evaluated on test data. Phase-1 results are
development results on one AMI series (IS1008); they are summarised in `DESIGN.md` ("Meeting
memory, phase 1") and must not be read as a benchmark figure.

## Problem

An agent asked about a recurring meeting ("what did we decide about the display?", "is the
sales projection still owed?") can search the raw transcripts of every meeting in the series.
That costs tokens in proportion to everything ever said, and it asks the agent to reconstruct,
at question time, how a decision moved across meetings: something no single transcript states.

The alternative studied here: after each meeting, update a **running record** for the series.
The record holds every decision and commitment, its current status, what each meeting did to
it, and pointers (meeting id + line id + verbatim quote) to the transcript lines each fact rests
on. An agent reads the record and opens transcript lines only when the record does not settle
the question.

The record is the context-management layer; the transcripts stay the source of truth. Every
statement in the record must be checkable against them by code.

## The record

Pydantic v2 models in `packages/tenor/memory/series_record.py`.

| Model | Fields | Notes |
|---|---|---|
| `SeriesRecord` | `series`, `meetings` (folded in, in order), `steps` (per meeting: how it was folded in), `entries` | Immutable; each update makes a new record. |
| `Entry` | `entry_id` (`E001`…), `kind` (`decision` \| `commitment`), `text` (as first recorded), `owner` (role or null), `introduced_in`, `origin` (`annotation` \| `transcript`), `annotation_id`, `status`, `current`, `history` | `status` and `current` are stored for readability but are derived from `history`; the checker rejects any mismatch. |
| `Event` | `meeting`, `kind`, `summary`, `evidence` (pointers), `searched`, `current` | One per entry per meeting at most. |
| `LineRef` | `meeting`, `line` (`L0123`), `quote` | The pointer. |

Event kinds and the status each sets:

| Event | Status after | Meaning |
|---|---|---|
| `introduced` | `open` | First recorded in this meeting. |
| `reaffirmed` | `open` | The meeting comes back to it; it stands as recorded; no result delivered. |
| `reported` | `reported` | Done or delivered as agreed. |
| `changed_openly` | `changed_openly` | A part changed or replaced, and someone said so. |
| `changed_without_comment` | `changed_without_comment` | It comes back with a part missing, different or contradicted, and nobody notes it. |
| `dropped` | `dropped` | Abandoned aloud. |
| `not_mentioned` | unchanged | The meeting never comes back to it. Carries no lines, but lists what was looked for. |

The event definitions follow the item-fates reader prompt (`prompts/item_fates.v1.md`), so the
record's events can be compared with those labels.

A status is **live** if it is `open`, `changed_openly` or `changed_without_comment`. `reported`
and `dropped` are closed.

## The update step

Inputs: the record before the meeting (rendered as text, see "Querying") and the new meeting's
full transcript (`L0000 [mm:ss] Role: words`). Output: an `Update`:

- `events`: exactly one `EventIn` for every live entry, and optionally for a closed entry the
  meeting plainly returns to;
- `new_entries`: decisions and commitments first made in this meeting, each with at least one
  pointer.

The update is written by a model (prompt `prompts/memory_update.v1.md`). It is not a new
record: `apply_update` folds it in deterministically, appends events, assigns new ids
(`E{n:03d}` in order), derives each status and current text, and records the step. The writer
cannot edit earlier history, change a status without an event, or invent ids.

In the prototype the writer is an agent that runs `scripts/meeting_memory.py check-update`
itself and repairs its update until the check passes; the check writes the new record only when
it is clean.

Seeding the first meeting: either from the AMI annotator's decisions and actions, with the
dialogue acts the annotator linked as pointers (`tenor.evals.meeting_memory.seed_record`), or
by running the same update step on an empty record. Both are implemented; they make two
"chains" (below).

## Invariants

Checked by `packages/tenor/memory/series_check.py` (`update_problems` before folding,
`record_problems` on a whole record). Every check returns a list of problems; none raises.

1. **Provenance.** Every pointer names a meeting in the record and a line that exists in that
   meeting's transcript, and its quote is in that line: at least three consecutive words, or
   the whole line if shorter, compared after lower-casing and dropping punctuation.
2. **Events cite their own meeting.** An event's pointers are lines of the meeting it belongs
   to.
3. **Every event is backed.** Every event except `not_mentioned` cites at least one line;
   `not_mentioned` cites none and lists at least one searched term; a `changed_*` event states
   the item as it now stands; no other event may.
4. **Statuses are derived.** Stored `status` and `current` equal what the history implies. A
   status therefore only moves with a cited event; `not_mentioned` never moves it.
5. **Append-only.** A history starts with `introduced`, has at most one event per meeting and
   is in meeting order; entry ids are dense and in order.
6. **Complete.** Every entry that was live before a meeting has an event in that meeting. An
   update cannot silently skip an entry; "nobody mentioned it" must be said.

What the checker cannot verify: that a quote *supports* the event (a real line quoted for the
wrong claim passes), that a `not_mentioned` judgement is right, or that the meeting held no
decision the writer left out. Those need readers; phase 1 measures them against existing labels
(below).

## Querying

`render(record)` gives one block per entry: id, kind, status, owner, first meeting, the text as
first recorded, the current text if changed, and one line per event with its pointers and
quotes. Same record, same text.

An agent answering a question gets the rendered record and two logged tools:

- `lines --meeting M --lines L0120-L0130`: open transcript lines (follow a pointer, read
  context around it);
- `search --meeting M --words "..."`: lines of one meeting containing all the words (at most
  20).

Every call is appended to a per-question log, so "tokens read" counts what the tools printed,
not what the agent says it read. Prompt: `prompts/memory_answer.v1.md` (condition `record`).

## Cost model

Approximate tokens as characters / 4 (no tokenizer is called; the same rule for both sides).
Let P be the fixed prompt, R_k the rendered record after meeting k, T_k meeting k's transcript,
L the lookup output.

- Update after meeting k: `P_update + R_(k-1) + T_k` read, one update written.
- Question from the record: `P_answer + R_n + L`.
- Question from raw transcripts: `P_answer + Σ T_i` over the meetings the agent must read; with
  an oracle that names the meeting, just that meeting's transcript.

R grows with the number of entries (each carries its history and quotes), not with talk. On
IS1008 (4 meetings, about 35,300 transcript tokens in all):

| | After a | After b | After c | After d |
|---|---|---|---|---|
| Transcript of that meeting | 5,283 | 10,757 | 9,294 | 10,008 |
| Record, transcript-only chain (entries) | 1,299 (8) | 3,553 (20) | 5,787 (26) | 8,086 (31) |
| Update job read, transcript-only chain | 6,782 | 13,505 | 14,296 | 17,244 |

About 260 tokens per entry. A question from the record read 8,871 tokens on average (8,086 of
record, about 220 of lookups, the rest prompt and question). A question from the one meeting it
names read 5,641; from all four transcripts it would read about 35,300. So on this series the
record is cheaper than reading the whole series (about 4×) and dearer than reading one named
meeting (about 1.6×). Without compaction the record grows linearly in entries; long series need
closed entries folded into one line each (phase 2).

## What is not handled

- **Owners and speakers from audio.** The prototype uses AMI's human transcripts, where every
  line has a role. A product transcript is channel-attributed (`microphone` = the note owner,
  system audio = everyone else) with no names; "who took this on" would rest on what is said,
  not on diarization. Replaying audio to recover owners is out of scope.
- **Real time.** The update runs after a meeting ends, on its full transcript. No streaming,
  no mid-meeting updates.
- **What happens between meetings.** A change announced by email or in a document looks silent
  here.
- **Notes as input.** The update reads the transcript, not a product's AI notes. Using notes
  would make it cheaper and inherit the notes' omissions.
- **More than one series, cross-series links, and permissions.** One record per series; no
  sharing model.
- **Discussion that is not a decision or commitment.** Problems raised, opinions and who said
  what are not entries; questions about them go through the lookup tools.
- **Compaction and repair.** No rule closes an entry that has gone unmentioned for many
  meetings; no way to correct an earlier event except by a later one.

## Phase 1 prototype

- Code: `packages/tenor/memory/series_record.py`, `packages/tenor/memory/series_check.py`,
  `packages/tenor/evals/meeting_memory.py` (AMI transcripts, seed, matching to annotated items,
  agreement with item fates), `packages/tenor/evals/meeting_memory_qa.py` (the question
  comparison), `scripts/meeting_memory.py`; tests in `tests/test_series_memory.py`.
- Prompts: `memory_update.v1` (the update step), `memory_answer.v1` (answering, two
  conditions), `memory_item_match.v1` (a reader that maps annotated items to record entries).
  The blind grader is the existing `mcp_reading_reader.v1`, shown two answers per question.
- Two chains on IS1008 a→b→c→d: `main` (meeting a seeded from the annotation, b–d by the update
  agent) and `transcript_only` (all four by the update agent from an empty record). The question
  comparison uses `transcript_only`, because the development questions are all about IS1008a
  and are built from the same annotation the `main` seed copies.
- All model work is done by agent sessions reading job files; no API calls. Outputs live under
  `data/private/memory/IS1008/` (not committed).

## Open questions for phase 2

- Entries lose parts. The update writer summarised a list decision without two of its parts
  (locator, battery indicator); a later silent drop of exactly those parts was then invisible
  to the record. An explicit `parts` list per entry, each with its own fate, is the obvious fix
  to test.
- `changed_without_comment` was never used in seven update steps; the two quiet changes in the
  series were recorded as `not_mentioned` or `reported`. Whether a separate verifier
  (as in `quiet_change_verify.v1`) should check every `reaffirmed`/`reported` event of a
  multi-part entry is open.
- Granularity of entries differs from the annotator's, which makes agreement hard to score;
  matching needs readers, not line proximity.
- The record should be tested where it should help: questions spanning meetings, with no oracle
  meeting selection, on series not used for development.
