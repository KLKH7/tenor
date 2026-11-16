# Series record update (v1)

You keep the running record of a recurring meeting series: every decision the group made and
every commitment someone took on, what has happened to each since, and the transcript lines each
fact rests on. You are given the record as it stands before a new meeting, and the new meeting's
full transcript (lines `L0000`, each with a time and the speaker's role). Write the update that
folds the new meeting into the record.

You write an update, not a new record: code applies it, derives every status from it and checks
every pointer. You cannot change what earlier meetings recorded.

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
- `reaffirmed`: the meeting comes back to the item (discusses it, relies on it, restates it) and
  it stands as recorded, all parts, with no result delivered.
- `changed_openly`: the meeting changes a part of the item, or replaces it, and someone says so
  or it is decided aloud (withdrawn, deferred, replaced, reported not possible, a reason given).
- `changed_without_comment`: the meeting comes back to the item but a part is missing,
  different or contradicted, and nobody notes the change. Before using this, check the whole
  transcript: if the part appears elsewhere in other words, it is not changed; if anyone notes
  the change anywhere, it is `changed_openly`.
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

## 2. New entries

Add every decision the group makes and every commitment someone takes on in this meeting that is
not already an entry. A decision is something the group settles (a choice, a rule, a plan for
the next step); a commitment is a task a named role takes away to do, usually before the next
meeting. Do not add proposals nobody agreed to, open questions, opinions, or facts presented.
Write one entry per decision or commitment as a careful team member would list it in the
minutes; a set of features chosen together may be one entry that lists them. If the meeting
restates an existing entry, that is an event on the entry, not a new entry.

Fields of a new entry:

- `kind`: `decision` or `commitment`.
- `text`: one sentence stating it with all its parts.
- `owner`: the role who took it on, as the transcript names roles (`Industrial Designer`), or
  `null` for a group decision.
- `summary`: one plain sentence on how it was reached (who proposed, who agreed).
- `evidence`: the lines where it is agreed or assigned, at least one, at most 6, same form as
  above.

## 3. Rules

- Use only this job file and the check command below. Do not open any other file in the
  repository (no annotations, no other transcripts, no earlier results).
- Pointers cite only the new meeting's lines. Copy quotes exactly; do not tidy them.
- Every live entry gets exactly one event; no entry gets two.

## Output

Write one JSON object, nothing else, to the update file named in the job:

```
{"meeting": "<meeting id>",
 "events": [{"entry_id": "E001", "kind": "reported", "summary": "...", "evidence": [{"line": "L0123", "quote": "..."}], "searched": [], "current": null}],
 "new_entries": [{"kind": "commitment", "text": "...", "owner": "Marketing Expert", "summary": "...", "evidence": [{"line": "L0456", "quote": "..."}]}]}
```

Then run the check command given in the job. It validates the update against the record and the
transcript, and if it passes it writes the new record. If it prints problems, fix the update file
and run it again until it passes. Do not edit any record file yourself.
