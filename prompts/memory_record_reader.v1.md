# Is this fact in the series record? (v1)

You read the running record of a meeting series: decisions, commitments and open problems, each
entry with a status, parts, what each meeting did to it, and pointers into the transcripts. You
get the record twice: the compact view an answering agent sees first, and the full view with
every event and pointer. Then a list of facts from the meetings' human summaries, each with the
question(s) it was asked in. For each fact, say whether the record holds it.

- `in_record`: some entry (or entries together) states the fact as the summary does: same
  content, same owner where the fact names one, and a status or event that does not contradict
  it. Wording may differ. For a fact about who said something, it counts only if the record
  says that person said it.
- `in_record_wrong`: an entry covers the same item but its text, owner, parts or status
  contradict the fact (for example it gives the task to someone else, or says the item was kept
  when the fact says it was dropped, or the reverse).
- `not_in_record`: no entry holds the fact. A partial overlap on a minor part, or a related
  entry that does not say what the fact says, is `not_in_record`; name the overlap in `reason`.

Also say whether the compact view alone shows the fact (`compact_shows`: `yes` or `no`); use
`no` when only the full view has it, or when the label is not `in_record`.

You see no answers and nothing about how any answer was judged.

## Output

JSONL, one line per fact, in the order listed, nothing else, to the answer file named in the
job:

```
{"id": "R001", "label": "in_record", "entries": ["E003"], "compact_shows": "yes", "reason": "one short line"}
```

## Changelog

- v1 (2026-11-17): first version, for splitting record-condition misses (memory breakdown).
