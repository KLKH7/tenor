# Series record update (v2)

You keep the running record of a recurring meeting series: every decision the group made, every
commitment someone took on, and every open problem the group said it must solve; what has
happened to each since; and the transcript lines each fact rests on. You are given the record as
it stands before a new meeting, and the new meeting's full transcript (lines `L0000`, each with a
time and the speaker's role). Write the update that folds the new meeting into the record.

You write an update, not a new record: code applies it, derives every status from it and checks
every pointer. You cannot change what earlier meetings recorded.

The record is shown in a compact view: an entry that is closed (`reported` or `dropped`) is one
line; a live entry shows its parts and its events with at most two pointers each.

## 1. One event for every live entry

An entry is **live** if its status is `open`, `changed_openly` or `changed_without_comment`.
For every live entry, write exactly one event saying what the new meeting did to it. For an
entry whose status is `reported` or `dropped`, write an event only if the meeting plainly comes
back to it; otherwise leave it out.

Read the whole transcript before deciding. Look for the entry's key words, other wordings, and
the thing it would produce (a presentation, a figure, a prototype, a choice). Match by what is
being talked about, never by who is speaking or by wording alone. No kind is the expected one:
do not lean towards "nothing happened" and do not lean towards finding a change.

Event kinds:

- `reported`: the meeting reports the item done or delivers its result (the promised
  presentation is given, the figure is stated, the thing decided is shown as built), as agreed.
  A first version delivered counts: if the owner presents the work they took away, the
  commitment is `reported`, and parts the presentation leaves out get the part outcome
  `not_mentioned` (or a change, if one is said). For an `issue`: it is resolved or answered.
- `reaffirmed`: the meeting comes back to the item (discusses it, relies on it, restates it) and
  it stands as recorded, with no result delivered. For an `issue`: it is raised again or still
  being worked on.
- `changed_openly`: the meeting changes a part of the item, or replaces it, and someone says so
  or it is decided aloud (withdrawn, deferred, replaced, reported not possible, a reason given).
- `changed_without_comment`: the meeting comes back to the item but a part is missing,
  different or contradicted, and nobody notes the change. Stating the new version as though it
  had always been the plan, with the dropped or altered part never named, is a change without
  comment. Before using this, check the whole transcript: if the part appears elsewhere in other
  words, it is not changed; if anyone notes the change anywhere, it is `changed_openly`.
- `dropped`: the whole item is abandoned aloud (cancelled, ruled out, "we won't do that").
- `not_mentioned`: the meeting never comes back to the item. A passing use of the same word
  about something else is not a return.

Fields of an event:

- `entry_id`: the entry's id (`E001`).
- `kind`: one of the six above.
- `summary`: one plain sentence on what happened, in this meeting's terms.
- `evidence`: the lines of **this** meeting that show it, most telling first, at most 6. Each is
  `{"line": "L0123", "quote": "..."}` where the quote is at least three consecutive words copied
  exactly from that line (or the whole line if it is shorter). Empty for `not_mentioned`.
- `searched`: for `not_mentioned` only, the key words and other wordings you looked for (at
  least two). Empty otherwise.
- `current`: for the two `changed_*` kinds only, the whole item as it now stands, in one
  sentence. `null` otherwise.
- `parts`: see section 2. Empty unless the entry has parts and the event brings it back.

## 2. Parts

An entry that names several separate things (listed features, numbers, materials, conditions,
sub-tasks, a deadline) lists them as **parts** (`P1`, `P2`, ...), so that what happens to each
can be followed. A change usually touches one part: a list comes back with one feature gone, a
price comes back different, one of two tasks is done.

When an event brings back an entry that has parts (`reported`, `reaffirmed`, `changed_openly`,
`changed_without_comment`), give **every** part that is not already dropped exactly one outcome:

- `stands`: the part comes back as recorded (said, shown, relied on, delivered).
- `changed_openly`: the part is changed or replaced and someone says so.
- `changed_without_comment`: the part is different or contradicted, or it is **left out of a
  version of the item the meeting states as complete** (the final feature list, the design as
  presented, the budget as totalled), and nobody notes it.
- `dropped`: the part is abandoned aloud.
- `not_mentioned`: the meeting does not touch the part, and states no complete version of the
  item from which it is missing.

Each part outcome is `{"part": "P1", "outcome": "stands", "evidence": [...], "current": null}`:
`evidence` as above (empty for `not_mentioned`), `current` (the part as it now stands; "absent"
if it was left out) for the two `changed_*` outcomes only.

The event's kind must agree with its parts: if any part is `changed_without_comment`, the event
is `changed_without_comment`; otherwise, if any part is `changed_openly` or `dropped`, the event
is `changed_openly`; otherwise it is `reported` or `reaffirmed`.

## 3. New entries

Add every decision the group makes, every commitment someone takes on, and every open problem
the group says must be solved, in this meeting, that is not already an entry.

- A `decision` is something the group settles (a choice, a rule, a plan for the next step,
  what the next meeting will take up). A group can settle a choice by accepting a presented
  proposal as a whole ("let's go with that", or agreement when asked to decide on it): then the
  decision lists what the proposal contained as its parts, each with the line that presented
  it.
- A `commitment` is a task a role takes away to do, usually before the next meeting. One owner
  per commitment: if a next step is split between roles, write one commitment per role. The
  owner is the role who will do the task (the one told or offering to do it), not the one who
  asks.
- An `issue` is a problem, risk or open question raised in the meeting that the group treats as
  needing an answer or a fix (a constraint that rules something out, a doubt nobody settles,
  information the team lacks and needs, confusion that holds up the work). Not every remark or
  opinion: only what the group itself treats as a problem to solve.

Do not add proposals nobody agreed to, opinions, or facts presented. If the meeting restates an
existing entry, that is an event on the entry, not a new entry.

Fields of a new entry:

- `kind`: `decision`, `commitment` or `issue`.
- `text`: one sentence stating it with **all** its parts. Never summarise a list away: if the
  group chose five features, the text names five.
- `parts`: if the entry names two or more separate things, one short phrase per thing, in the
  order the text gives them; otherwise `[]`. Never exactly one part.
- `owner`: the role who took it on, as the transcript names roles (`Industrial Designer`), or
  `null` for a group decision or an issue.
- `summary`: one plain sentence on how it was reached (who proposed, who agreed).
- `evidence`: the lines where it is agreed, assigned or raised, at least one, at most 6, same
  form as above.

## 4. Rules

- Use only this job file and the check command below. Do not open any other file in the
  repository (no annotations, no other transcripts, no earlier results).
- Pointers cite only the new meeting's lines. Copy quotes exactly; do not tidy them.
- Every live entry gets exactly one event; no entry gets two.

## Output

Write one JSON object, nothing else, to the update file named in the job:

```
{"meeting": "<meeting id>",
 "events": [
  {"entry_id": "E001", "kind": "reaffirmed", "summary": "...", "evidence": [{"line": "L0123", "quote": "..."}], "searched": [], "current": null,
   "parts": [{"part": "P1", "outcome": "stands", "evidence": [{"line": "L0123", "quote": "..."}], "current": null},
             {"part": "P2", "outcome": "not_mentioned", "evidence": [], "current": null}]},
  {"entry_id": "E002", "kind": "not_mentioned", "summary": "...", "evidence": [], "searched": ["...", "..."], "current": null, "parts": []}],
 "new_entries": [{"kind": "decision", "text": "...", "parts": ["...", "..."], "owner": null, "summary": "...", "evidence": [{"line": "L0456", "quote": "..."}]}]}
```

Then run the check command given in the job. It validates the update against the record and the
transcript, and if it passes it writes the new record. If it prints problems, fix the update file
and run it again until it passes. Do not edit any record file yourself.

## Changelog

- v2: entries list their parts and each returning event gives every part an outcome, so a part
  that quietly goes missing is visible (phase 1 lost the locator and battery light of a feature
  list); a part left out of a version the meeting states as complete is a change, not "not
  mentioned"; a new kind `issue` for open problems; one owner per commitment; the record is
  shown compact; a delivered first version is `reported`; decisions include what the next
  meeting will take up and a presented proposal accepted as a whole (its contents as parts);
  issues include missing information that holds up the work. Developed on IS1008 only (two
  rounds on the IS1008 chain and its 13 development questions).
- v1: first version.
