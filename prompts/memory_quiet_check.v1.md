# Quiet-change check on a series record (v1)

A note-keeper has folded a new meeting into the running record of a meeting series. For some
entries that have several parts, the note-keeper said the meeting brought the entry back
unchanged (`reaffirmed`) or delivered it (`reported`). You check each of those entries against
the new meeting's full transcript and decide whether a part in fact came back **missing,
different or contradicted, with nobody noting the change**. You do not know how the note-keeper
worked, and its judgement may be right or wrong. No verdict is the expected one: a change you
invent is as bad as a change you miss.

Use only this file. Do not open any other file and do not use anything you may remember about
these meetings.

## Per entry

Do all three steps for every entry listed.

**(a) Find the meeting's own version of the item.** Read the entry: its text, its parts (`P1`,
...), and what earlier meetings did to it. Read the note-keeper's event and the lines it cites,
with at least ten lines either side. Then read the whole transcript for the item: its key words,
other wordings, and the thing it would produce. Find where the meeting states its own version of
the item as current: presented, listed, shown as built, costed, decided, reported.

**(b) Compare part by part.** For each part, find it in the meeting's version, in any wording.
A part is changed when the meeting's version gives a different value, material, number or
owner, contradicts it, or leaves it out of a version the meeting plainly states as complete (the
final feature list, the design as presented, the prototype as shown). A part the meeting simply
does not discuss, while stating no complete version, is **not** changed. More detail on
something the record left open is not a change. A suggestion, a question, an option floated or
one person's liking is not the meeting's version.

**(c) Does anyone note the change?** For every part you found changed: write down its key words
and two or three other wordings of each, and the words of the meeting's version. Search the
whole transcript, first line to last, for each, and for words of change and reason: instead,
rather, no longer, not anymore, changed, dropped, removed, decided, budget, cost, price,
expensive, afford, can't, not possible, doesn't work, what about, I thought. Read around every
hit. If any line names the change, the old version or the missing part, asks about it, gives a
reason for it, or decides it, the change is noted, and the part is **not** a quiet change.
Report the closest passages you found either way.

**Verdict.** `quiet_change` if at least one part is changed (b) and nobody notes it (c);
otherwise `holds` (this includes a part changed openly, which is outside this check).

## Output

JSONL, one object per entry, in the order listed, no other text, to the answer file named in
the job:

```
{"entry_id": "E004", "verdict": "holds|quiet_change", "parts": [{"part": "P3", "evidence": [{"line": "L0123", "quote": "..."}], "current": "absent", "what_changed": "the locator is not in the final feature list"}], "current": "the whole item as it now stands, one sentence", "terms_searched": ["...", "..."], "closest": [{"line": "L0456", "says": "..."}], "reason": "one or two sentences"}
```

- `parts`: for `quiet_change` only, each part changed without comment, with the lines of this
  meeting that show the meeting's version (at least one; quotes of at least three consecutive
  words copied exactly from the line, or the whole line if shorter) and the part as it now
  stands (`current`: the new value, or "absent"). Empty for `holds`.
- `current`: for `quiet_change` only, the whole item as it now stands. `null` for `holds`.
- `terms_searched`, `closest`, `reason`: for both verdicts.

Then run the check command given in the job and fix the file until it passes.

## Changelog

- v1: first version. A second reader on every `reported` or `reaffirmed` event that brings
  back an entry with at least two standing parts; its finding replaces the event only with
  cited lines. Steps follow `item_fates.v1` (the definition of a silent change) and
  `quiet_change_verify.v1` (the search for anyone raising it). Developed on IS1008 only.
