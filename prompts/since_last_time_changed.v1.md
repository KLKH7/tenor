# Since last time: quiet changes (writer, v1)

You are given two consecutive meetings of the same team, as a notes product has them: the
product's **notes** of the earlier meeting (X) and the product's own **transcript** of the later
meeting (Y). Find what X's notes record as decided or as a task that comes back in Y in a
**different version, with nobody saying so**, and write one cautious line for each. At most
**3** lines. Many pairs of meetings have none, and then the right output is an empty file; but
read every item before deciding that.

Use only this file. Do not open any other file, do not search the web, and do not use anything
you may remember about these meetings.

Every line you write will be checked by someone who reads all of Y looking for any place where
the change is raised. A line that is wrong costs the reader far more than a missing line.

## What qualifies

All of these must hold.

1. **X recorded it.** X's notes record it as decided or as a task: a feature, a material, a
   figure, a shape, a list of parts, a rule for the work, who was to do what. Not something X's
   notes present only as an idea, an option or an open point.
2. **Y has its own version of the same thing, stated as the current one.** Someone in Y
   presents, lists, costs, draws or states the very thing the X item was about (the list of
   functions, the case material, the price, the battery, who presents what), as the plan or the
   design or what was done. A suggestion, a question, an option someone floats, a thing someone
   likes, or one person's opinion is **not** a version of the plan.
3. **The versions differ.** Y's version contradicts X's, replaces it, or leaves out a part X's
   item named (when Y gives a full list or a full design and the part is not in it). It is
   **not** a change when Y only adds detail that X left open, narrows something X left broad,
   or says the same thing in other words. If X's version is also stated anywhere in Y as still
   standing, there is no change.
4. **Nobody in Y says so.** No line of Y names the change, the old version, the dropped part,
   or the gap; nobody asks about it ("what happened to ...", "weren't we going to ..."), gives
   a reason for it (cost, time, feasibility, a new requirement), reports it as not done or not
   possible, decides to change it, or says it still has to be checked or fitted in. **However brief, any
   such line anywhere in Y means the change was raised, and the item does not qualify.** This
   includes the meeting's own recap or agenda, and a remark long before or after the stretch
   where Y's version appears.
   Stating Y's version as the plan, even as something decided ("so we've decided it's a basic
   TV remote"), does **not** raise the change: that is exactly a quiet change. The change is
   raised only when a line refers to X's version, to the part that is different or missing, or
   to the fact that something changed or was dropped, or gives a reason for moving away from
   X's version.

## Method

Work through every item; do not stop at the first one that qualifies.

1. List for yourself every bullet X's notes record as decided or as a task. For each, write
   down its parts (each feature, number, material, person, condition), its key words and two or
   three other ways someone might say each part.
2. Read **all** of Y, first line to last. For each item, note every line where Y states its own
   version of the thing the item was about, and every line that mentions any part of the item.
3. Keep an item only if a stretch of Y states a version that differs (point 3 above). Write
   down the exact lines that show Y's version.
4. For each item kept, search Y again, once for each key word and each other wording of the
   part that changed, and once for words of change and reason near the item (instead, rather,
   no longer, dropped, decided, budget, cost, too expensive, can't, not possible, still need,
   what about, I thought). Read the lines around every hit. If any line raises the change in
   the sense of point 4, drop the item.
5. For each item still kept, ask: do the lines I will quote show Y's version by themselves,
   read in context? If you need to infer it (from what nobody said, from a later decision you
   cannot quote, from a cost sheet's silence), drop the item.

When in doubt at any step, drop the item.

## The line

One plain sentence, under 35 words, as you would say it to a colleague, in this shape:

"Last time the plan had <X's version, in the words of X's notes>; today <what the quoted lines
show>, and nobody mentioned the change."

- Say only what the quotes show. If the quotes show the designer presenting a rubber case, say
  "today's design has a rubber case", not "the team chose rubber". If they show a list of
  functions without the locator, say "today's list of functions has no locator".
- Never say anything was chosen, agreed, decided, dropped, replaced, abandoned, final or
  settled unless a quoted line says those words about it. Never say why. Never say "silently",
  "quietly", "forgot" or "without discussion"; the fixed ending "and nobody mentioned the
  change" is the only claim about silence.
- Name X's version as X's notes do: "the plan had", "the team was going to", "<role> was going
  to". Do not make it firmer or broader than the bullet.
- No ids, no "the notes" or "the transcript", no advice.

Examples (invented, not from the data):

- "Last time the plan had a vegan pastry on the opening menu; today the menu read out has
  sourdough, two soups and oat milk, and nobody mentioned the change."
- "Last time Dana was going to bring two awning quotes; today she presented one quote from
  Shade Co., and nobody mentioned the change."

## Evidence

- `x_bullets`: the bullet id or ids of X's notes the line rests on (at least one).
- `before`: X's version in a few words, from the bullet.
- `after`: Y's version in a few words, from the quotes.
- `quotes`: one to four objects `{"line": "L0123", "text": "..."}`. `text` is copied
  **verbatim** from that line of Y (a contiguous run of at least 3 words, unchanged spelling and
  punctuation; you may leave out words at either end of the line but not in the middle). Quote
  the lines that show Y's version, the line that frames it as the plan or the design when that
  is a different line, and nothing else.
- `searched`: the key words and other wordings you searched for in step 4.

## Output

JSONL, one object per line, at most 3 lines, no other text. If nothing qualifies, write an
empty file.

```
{"sentence": "...", "x_bullets": ["B4"], "before": "...", "after": "...", "quotes": [{"line": "L0310", "text": "..."}], "searched": ["...", "..."]}
```

## Changelog

- v1: first version. A writer for quiet-change lines only (the "not mentioned" lines stay with
  `since_last_time.v3`): each line names X's item, quotes the lines of Y that show the
  different version verbatim with their ids, and claims only what the quotes show, in one
  fixed cautious shape; no claim about why or about what the team chose. Developed on the 6
  development pairs of the item-fates study and on IS1008a to IS1008b.
