# Since last time (writer, v2)

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
  relies on it. For a decision this means Y works on the very thing the decision was about (the
  design is presented, the product is costed, the plan is reviewed) and the decision is not
  there and nobody refers to it.

Decisions and tasks count the same. A decision that quietly dropped out of the design is as
much for you as a task nobody reported on.

Nothing else qualifies. Do not report what was done, what stands as agreed, or what was changed
in the open. Do not report things X's notes present only as an idea, an opinion or an open point.
Do not report housekeeping that changes nothing about the work (sending round the slides or the
minutes, booking the room): true or not, it is not worth a line.

## Method

Work through every item; do not stop at the first one that qualifies.

1. List for yourself every bullet X's notes record as decided or as a task (read each with its
   section and parent). For each one write down its **parts**: who, what, and every specific
   (a number, a material, a feature, a name, a date, an "and" that joins two things).
2. Read **all** of Y, first line to last. For each item note every place Y touches it: its key
   words, other wordings, and the thing it would have produced. The transcript may be a machine
   transcript: names and terms may be misspelt, speakers may not be labelled. Match by what is
   being talked about, not by wording alone.
3. Give each item one private label:
   - *reported or relied on*: someone says it was done, shows the result, or works from it as
     agreed, with every part intact. Not for you.
   - *changed in the open*: it comes back different and someone says so, gives a reason, asks
     about it or decides it. Not for you.
   - *changed quietly*: it comes back, and one of its parts is missing, different or
     contradicted, and nobody says so. Look hardest where the team restates what it has: a
     recap of last time, a presented design or prototype, a list of features, a costing, an
     evaluation. Compare each part you wrote down in step 1 against those places. A part that
     appears elsewhere in Y in other words is not missing.
   - *never touched*: nothing in Y reports it, relies on it or discusses it. Before you settle
     on this, search Y once more for the item's parts one at a time.
4. Keep the items labelled *changed quietly* (kind `changed`) and *never touched* (kind
   `not_mentioned`) that you are confident of after reading all of Y. When in doubt, leave it
   out: a wrong line costs the reader more than a missing one.
5. If more than 3 qualify, keep the 3 that matter most to the work: something the team will
   build, buy or promise differently because of it comes before a detail. Do not prefer tasks
   over decisions or the reverse.

## The sentence

One plain sentence, as you would say it to a colleague, under 30 words. Say what was agreed and
what happened to it. No category names, no ids, no "the notes" or "the transcript", no advice.

- changed: "Last time the menu had four things; today's printed menu has three, and nobody
  mentioned the vegan pastry."
- not_mentioned: "Sam was going to ask the landlord about the bin store; it did not come up
  today."

## Evidence

- `x_bullets`: the bullet id or ids of X's notes the sentence rests on (at least one). If one
  change undoes two bullets, or a task is written twice, cite every bullet it covers.
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
- v2: decisions count the same as tasks; a per-item pass with each item's parts written down
  and compared where the team restates its design; housekeeping left out; the tie-break no
  longer prefers tasks. Output format unchanged. Developed on the 6 development pairs of the
  item-fates study and on IS1008a to IS1008b.
