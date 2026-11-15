# Since last time (writer, v4)

You are given two consecutive meetings of the same team, as a notes product has them: the
product's **notes** of the earlier meeting (X) and the product's own **transcript** of the later
meeting (Y). Write what a member of the team should be told at the top of the new note: the
next steps from last time, or the parts of them, that nobody mentioned today. At most **3**
sentences. Often the right output is fewer, or none.

Use only this file. Do not open any other file, do not search the web, and do not use anything
you may remember about these meetings.

## What qualifies

Only silence, of two sizes.

**A whole item.** Something X's notes record as decided or as a task, that the team would
expect to come back to today, and that Y **never touches in any way**.

- A **task** someone took away (to find out, draw up, cost, ask, prepare, send, bring): the team
  expects to hear how it went. It qualifies when nobody in Y reports on it, shows its result,
  says it was not done, or refers to it at all.
- A **decision** the next meeting had to build on (a feature, a material, a price, a target, a
  shape, a rule for the work): it qualifies when Y works on the very thing the decision was
  about (the design is presented, the product is costed, the requirements are settled, the plan
  is reviewed) and the decision is not there and nobody refers to it.

**A part of an item.** Many items name several things at once: a list of features or
components, a material and a colour, a figure and a condition, "the van will deliver to the
shop and to the market stall", "Sam will cost the shelving and the lighting". Y may take the
item up and yet never say a word about one of the things it names. That part qualifies when:

- the part is named in X's bullet itself, as its own thing (a feature, a component, a material,
  a figure, a condition, a sub-task, a group of users), not something you infer from it, and
  not a general quality that no line could confirm ("user-friendly", "modern", "good");
- Y takes up the item or the thing it is about (presents the design the features belong to,
  costs the parts, reports on the task, settles the requirements), so the team would expect the
  part to be there; and
- no line of Y touches the part (see below).

Decisions and tasks count the same.

**What "touches" means.** A line of Y touches an item, or a part, when it states, uses,
reports, asks about, questions or changes its own content: the figure, the feature, the
material, the choice, the task's result, or a different value for the same thing. Talking about
the same general subject without that content does not touch it: a meeting that discusses costs
but never states or uses the agreed selling price has not touched the price, and a meeting that
presents the buttons but never brings up the agreed voice control has not touched voice
control. For a decision, that is exactly the case that qualifies.

Never write about an item or a part that Y does touch, even if it came back different, smaller,
renamed, postponed, replaced by something else or only in passing. That is not for you: you
only report silence. In particular, if Y names another value, material or feature for that very
part (another colour for the case, another price, another kind of battery), the part was
touched: leave it out. But a line that describes the item as a whole in a way that simply
leaves the part out ("the menu has three courses", "it's a basic delivery round", "we're
going for a simple van") does not touch the part, even when you could read it as quietly
leaving the part behind: a part nobody names, in its own words or in other words, is exactly
the silence to report. The question for a part is only whether anyone names it.

Nothing else qualifies. Do not report what was done or what stands as agreed. Do not report
things X's notes present only as an idea, an opinion or an open point. Do not report
housekeeping that changes nothing about the work (sending round the slides or the minutes,
booking the room, the next meeting's time), and do not report a decision that Y had no occasion
to come back to.

## Method

Work through every item and every part; do not stop at the first one that qualifies.

1. List for yourself every bullet X's notes record as decided or as a task (read each with its
   section and parent). For each one write down its key words, two or three other ways someone
   might say it, who owns it, and the thing it would produce (a figure, a drawing, an answer, a
   choice, a part of the design). If the bullet names several things, list each one as a part,
   with its own key words and other wordings.
2. Read **all** of Y, first line to last. For each item, and for each of its parts, note every
   line that touches it (see above): its key words, the other wordings, its owner reporting on
   it, its result, a different value for it. The transcript may be a machine transcript: names
   and terms may be misspelt, speakers may not be labelled. Match by what is being talked
   about, not by wording alone.
3. An item no line touches is a candidate as a **whole item**. An item that some line touches
   is out as a whole item; each of its parts that no line touches is a candidate as a **part**.
   An item with a single part has no parts to report.
4. For each candidate, search Y once more, one piece at a time (each key word, each other
   wording, the thing it would produce), and look again at the stretch where its owner presents
   or reports and where the thing it belongs to is discussed. If a line there touches it, it is
   out; a line that is only on the same general subject does not make it out.
5. For each candidate still left, ask: would the team expect it to come back today? A task
   someone took away: yes. A decision: yes when Y works on the thing it was about. A part: yes
   when Y takes up the item or the thing it is about. Keep those.
6. If more than 3 sentences' worth are kept, keep what matters most to the work: something the
   team will build, buy or promise differently because of it comes before a detail. Do not
   prefer tasks over decisions, or whole items over parts, or the reverse.

Write a sentence for everything you keep after step 6: do not hold one back because another
sentence is already written. When you are unsure whether Y touched an item or a part, leave it
out: a wrong line costs the reader more than a missing one.

## The sentence

One plain sentence, as you would say it to a colleague, under 30 words: who was going to do
what, or what the team agreed, then, for a whole item, "it did not come up today", and for a
part, "nobody mentioned" the part, named, "today". Say nothing else about Y.

- Whole item: "Sam was going to ask the landlord about the bin store; it did not come up today."
- Whole item: "Last time the team agreed the menu would be printed on recycled card; it did not
  come up today."
- Part: "Last time's plan for the menu included a children's page and a gluten-free mark;
  nobody mentioned either today."
- Part: "Last time the team agreed the van would also deliver to the second shop; nobody
  mentioned the second shop today."
- Part: "Sam was going to price both the shelving and the lighting; nobody mentioned the
  lighting today."

Two or three things that qualify and belong to the same item or plan may share a sentence
("...; neither came up today", "...; nobody mentioned either today"), but only if each qualifies on its own, and never mix whole items
and parts in one sentence.

Never say or suggest what happened instead. Do not use "changed", "replaced", "dropped",
"removed", "instead", "only", "no longer", "now", "still", "but", "even though" or any other
word that says what Y did with it, why, or what the team decided in its place. No category
names, no ids, no "the notes" or "the transcript", no advice.

## Evidence

- `x_bullets`: the bullet id or ids of X's notes the sentence rests on (at least one). If a
  task is written twice, cite every bullet it covers.
- `scope`: `item` when the sentence names whole items, `part` when it names parts of items that
  Y does take up.
- `kind` is always `not_mentioned`, `y_lines` = `[]` and `quote` = `""`. The claim is that
  nothing in Y touches what the sentence names; you must have read all of Y to make it.

## Output

JSONL, one object per sentence, at most 3 lines, no other text. If nothing qualifies, write an
empty file.

```
{"sentence": "...", "kind": "not_mentioned", "scope": "item", "x_bullets": ["B12"], "y_lines": [], "quote": ""}
{"sentence": "...", "kind": "not_mentioned", "scope": "part", "x_bullets": ["B4"], "y_lines": [], "quote": ""}
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
- v4: from v3, may also name a **part** of an item Y takes up (one of several things the bullet
  names) that no line of Y touches, still only as silence ("nobody mentioned <the part>
  today"), never as a change or a claim about what was decided instead; a part is not touched
  by a line that describes the item as a whole without it; a part touched by another value for the same slot is
  out; things from the same item or plan may share a sentence; a `scope` field (`item` or
  `part`). Developed on the 6 development pairs and IS1008a to IS1008b only.
