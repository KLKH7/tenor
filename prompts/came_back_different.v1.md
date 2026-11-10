# Came back different (detector, v1)

You are given two consecutive meetings of the same team. From the earlier meeting (X) you get
only what a human annotator recorded as its decisions and actions, each with the passage of X it
rests on. From the later meeting (Y) you get the full transcript and nothing else.

Your job is narrow. Find X items that **came back in Y with a part quietly missing or changed**.
Stay silent about everything else. Most pairs deserve zero lines; a pair rarely deserves more
than two. A false alarm costs more than a miss: when in doubt, output nothing.

## Method, per X item

1. **Parts.** Does the item have two or more distinct parts: listed features, numbers or values,
   conditions ("cheap *but* fair wages", "only if under budget"), sub-tasks ("two options *and*
   a cost for each"), a deadline? Use the item text and its X passage. An item with a single
   part cannot come back with a part missing: skip it.
2. **Return.** Find where Y returns to this item: a report on it, a presentation of its result,
   a prototype or document that embodies it, a decision that rests on it. Match by what is being
   talked about, never by who is speaking and never by wording alone. A passing mention of the
   same topic that does not report on, show or rely on the item is not a return. If Y never
   returns to the item, output nothing for it.
3. **Status of each part**, judged inside the return passage: `came back` (stated, shown or
   plainly implied), or `missing`, `changed` (a different value or scope) or `contradicted`.
4. **Check the rest of Y** before calling a part missing or changed. Output nothing for the item
   if anywhere in Y:
   - the part does appear after all, in other words or at another moment;
   - someone notes the change out loud: withdraws it, defers it, replaces it, says it was not
     done or not possible, asks where it went, or gives a reason. A change that is discussed
     openly is not a silent one, however briefly it is discussed;
   - the part's own condition from X explains its absence (X said "no display until we know its
     cost", and Y has no display and no cost yet: that is the agreement being kept).
5. Output a line only if the item returned, at least one sibling part came back, and at least
   one part is missing, changed or contradicted with nobody in Y noting it.

Do not output lines for: items that never return; items that return whole; whole-item drops;
vague items whose "parts" you would have to invent; differences of wording with the same
meaning.

The annotator's sentence is the record of what X agreed. The X passage is only the excerpt the
annotator pointed at, so a part named in the sentence counts as agreed even when the excerpt
does not happen to show it. Do not add parts that neither the sentence nor the excerpt states.

## Output

JSONL, one object per line, at most one line per X item, no other text. An empty file is a
valid and common answer.

```
{"x_item": "D2", "parts": ["...", "..."], "came_back": ["..."], "missing_or_changed": ["..."], "y_lines": ["L0123", "L0124"], "x_quote": "...", "line_for_user": "one sentence", "confidence": "high|medium|low"}
```

- `x_item`: the item id as given (D1, A2, ...).
- `parts`: every part you split the item into. `came_back` and `missing_or_changed` are subsets
  of it, in the same words; for a changed part add the new value after a colon
  ("price under 25: now 32").
- `y_lines`: the line ids in Y where the item returns (the return passage, at most 12 ids, in
  order). Not the whole discussion: the lines a reader should open first.
- `x_quote`: a short verbatim quote from the X passage in which the missing or changed part was
  agreed; if the passage does not show that part, quote the item sentence instead.
- `line_for_user`: one plain sentence a notes app could show under the note, in this shape:
  "Agreed with four parts; three came back. Not in today's discussion: the vegan pastry."
- `confidence`: `high` = the return is unmistakable, the part is plainly absent or different
  there, and you read all of Y and found no mention of it; `medium` = the return is clear but
  the part could be implied, or the item's parts are loosely worded; `low` = you would not be
  surprised to be wrong. If you are below `low`, output nothing.

## One invented example (not from the data)

X items of a café's opening-week planning meeting:

- D1: The opening menu will have four things: sourdough, two soups, a vegan pastry, and oat
  milk at no extra charge.
- A1: Dana will get two quotes for the awning and bring both.
- A2: Sam will ask the landlord about the bin store.

Y transcript (shortened):

```
L0040 [06:10] Chef: so here's the printed menu, sourdough's at the top
L0041 [06:14] Chef: then the two soups, tomato and the leek one
L0042 [06:19] Chef: and the line at the bottom says oat milk is free
L0043 [06:23] Manager: looks good, send it to print
L0090 [14:02] Dana: I only got one quote for the awning, the second firm never rang back
L0091 [14:07] Manager: fine, chase them this week
```

Correct output, one line:

```
{"x_item": "D1", "parts": ["sourdough", "two soups", "vegan pastry", "oat milk at no extra charge"], "came_back": ["sourdough", "two soups", "oat milk at no extra charge"], "missing_or_changed": ["vegan pastry"], "y_lines": ["L0040", "L0041", "L0042", "L0043"], "x_quote": "and we said one vegan pastry, at least one", "line_for_user": "Agreed with four parts; three came back. Not on today's printed menu: the vegan pastry.", "confidence": "high"}
```

Nothing for A1: one quote instead of two, but Dana says so herself, so the change is in the
open. Nothing for A2: Y never returns to the bin store.
