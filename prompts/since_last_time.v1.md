# Since last time (writer, v1)

You are given two consecutive meetings of the same team, as a notes product has them: the
product's **notes** of the earlier meeting (X) and the product's own **transcript** of the later
meeting (Y). Write what a member of the team should be told at the top of the new note about
things from last time that went wrong quietly. At most **3** sentences. Often the right output
is fewer, or none.

Use only this file. Do not open any other file, do not search the web, and do not use anything
you may remember about these meetings.

## What qualifies

Only two kinds of thing, and only for something X's notes record as decided or as a task:

- `changed`: Y comes back to it, but a part is missing, different or contradicted, **and nobody
  in Y says so**. If anyone notes the change, gives a reason, asks about it or decides it, it is
  not for you: the team already knows.
- `not_mentioned`: X's notes record a task someone took away, or a decision the next meeting had
  to build on, and Y never comes back to it in any way: no report, no result, no discussion that
  relies on it.

Nothing else qualifies. Do not report what was done, what stands as agreed, or what was changed
in the open. Do not report things X's notes present only as an idea, an opinion or an open point.

## Method

1. List for yourself what X's notes record as decided or as a task (the bullets, read with their
   section and parent).
2. Read **all** of Y, first line to last. For each item look for its key words, for other
   wordings, and for the thing it would have produced. The transcript is a machine transcript:
   names and terms may be misspelt, speakers are often not labelled. Match by what is being
   talked about, not by wording alone.
3. Keep an item only if you are confident after reading all of Y. A part that appears elsewhere
   in other words is not missing. When in doubt, leave it out: a wrong line costs the reader
   more than a missing one.
4. If more than 3 qualify, keep the 3 a team member would most want to hear: a task nobody
   reported on before a decision detail.

## The sentence

One plain sentence, as you would say it to a colleague, under 30 words. Say what was agreed and
what happened to it. No category names, no ids, no "the notes" or "the transcript", no advice.

- changed: "Last time the menu had four things; today's printed menu has three, and nobody
  mentioned the vegan pastry."
- not_mentioned: "Sam was going to ask the landlord about the bin store; it did not come up
  today."

## Evidence

- `x_bullets`: the bullet id or ids of X's notes the sentence rests on (at least one).
- `changed`: `y_lines` = the Y line ids that show the item coming back different (1 to 6), and
  `quote` = words copied **exactly** from one of those lines (at least 4 words) that show it.
- `not_mentioned`: `y_lines` = `[]` and `quote` = `""`. The claim is that nothing in Y touches
  it; you must have read all of Y to make it.

## Output

JSONL, one object per sentence, at most 3 lines, no other text. If nothing qualifies, write an
empty file.

```
{"sentence": "...", "kind": "changed|not_mentioned", "x_bullets": ["B12"], "y_lines": ["L0123"], "quote": "words copied exactly from Y"}
```

## Changelog

- v1: first version.
