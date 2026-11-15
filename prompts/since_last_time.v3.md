# Since last time (writer, v3)

You are given two consecutive meetings of the same team, as a notes product has them: the
product's **notes** of the earlier meeting (X) and the product's own **transcript** of the later
meeting (Y). Write what a member of the team should be told at the top of the new note: the
next steps from last time that nobody mentioned today. At most **3** sentences. Often the right
output is fewer, or none.

Use only this file. Do not open any other file, do not search the web, and do not use anything
you may remember about these meetings.

## What qualifies

Only one kind of thing: something X's notes record as decided or as a task, that the team would
expect to come back to today, and that Y **never touches in any way**.

- A **task** someone took away (to find out, draw up, cost, ask, prepare, send, bring): the team
  expects to hear how it went. It qualifies when nobody in Y reports on it, shows its result,
  says it was not done, or refers to it at all.
- A **decision** the next meeting had to build on (a feature, a material, a price, a target, a
  shape, a rule for the work): it qualifies when Y works on the very thing the decision was
  about (the design is presented, the product is costed, the requirements are settled, the plan
  is reviewed) and the decision is not there and nobody refers to it.

Decisions and tasks count the same.

**What "touches" means.** A line of Y touches an item when it states, uses, reports, asks
about, questions or changes the item's own content: the figure, the feature, the material, the
choice, the task's result, or a different value for the same thing. Talking about the same
general subject without that content does not touch it: a meeting that discusses costs but
never states or uses the agreed selling price has not touched the price, and a meeting that
discusses the buttons but never brings up the agreed voice control has not touched voice
control. For a decision, that is exactly the case that qualifies.

Never write about an item Y does touch, even if it came back different, smaller, renamed,
postponed or in passing. That is not for you: you only report silence.

Nothing else qualifies. Do not report what was done or what stands as agreed. Do not report
things X's notes present only as an idea, an opinion or an open point. Do not report
housekeeping that changes nothing about the work (sending round the slides or the minutes,
booking the room, the next meeting's time), and do not report a decision that Y had no occasion
to come back to.

## Method

Work through every item; do not stop at the first one that qualifies.

1. List for yourself every bullet X's notes record as decided or as a task (read each with its
   section and parent). For each one write down its key words, two or three other ways someone
   might say it, who owns it, and the thing it would produce (a figure, a drawing, an answer, a
   choice, a part of the design).
2. Read **all** of Y, first line to last. For each item note every line that touches it (see
   above): its key words, the other wordings, its owner reporting on it, its result, a part of
   it, a different value for it. The transcript may be a machine transcript: names and terms
   may be misspelt, speakers may not be labelled. Match by what is being talked about, not by
   wording alone.
3. Any item with even one such line is out.
4. For each item left, search Y once more, one part at a time (each key word, each other
   wording, the thing it would produce), and look again at the stretch where its owner presents
   or reports. If a line there touches the item, it is out; a line that is only on the same
   general subject does not make it out.
5. For each item still left, ask: would the team expect it to come back today? A task someone
   took away: yes. A decision: yes when Y works on the thing it was about. Keep those.
6. If more than 3 are kept, keep the 3 that matter most to the work: something the team will
   build, buy or promise differently because of it comes before a detail. Do not prefer tasks
   over decisions or the reverse.

Write a sentence for every item you keep after step 6: do not hold one back because another
sentence is already written. When you are unsure whether Y touched an item, leave it out: a
wrong line costs the reader more than a missing one.

## The sentence

One plain sentence, as you would say it to a colleague, under 30 words, in this shape: who was
going to do what, or what the team agreed, then "it did not come up today". Say nothing else
about Y: no "even though", no "instead", no guess at why. No category names, no ids, no "the
notes" or "the transcript", no advice.

- "Sam was going to ask the landlord about the bin store; it did not come up today."
- "Last time the team agreed the menu would be printed on recycled card; it did not come up
  today."

## Evidence

- `x_bullets`: the bullet id or ids of X's notes the sentence rests on (at least one). If a
  task is written twice, cite every bullet it covers.
- `kind` is always `not_mentioned`, `y_lines` = `[]` and `quote` = `""`. The claim is that
  nothing in Y touches it; you must have read all of Y to make it.

## Output

JSONL, one object per sentence, at most 3 lines, no other text. If nothing qualifies, write an
empty file.

```
{"sentence": "...", "kind": "not_mentioned", "x_bullets": ["B12"], "y_lines": [], "quote": ""}
```

## Changelog

- v1: first version.
- v2: decisions count the same as tasks; a per-item pass comparing each item's parts.
- v3: from v1, for the line "which next steps from last time did nobody mention": only
  `not_mentioned` sentences, never a claim that something changed; what "touches" an item is
  defined (its own content, not the same general subject); decisions count the same as tasks,
  with the condition that Y works on what the decision was about; a per-item pass with a
  second, part-by-part search before an item is kept; every kept item gets a sentence (up to 3);
  housekeeping and decisions Y had no occasion to revisit left out; one fixed sentence shape.
  Output format unchanged. Developed on the 6 development pairs of the item-fates study and on
  IS1008a to IS1008b.
