# Item fates (reader, v1)

You are given two consecutive meetings of the same team. From the earlier meeting (X) you get
what a human annotator recorded as its decisions and actions, each with the passage of X it
rests on. From the later meeting (Y) you get the full transcript and nothing else.

Your job: for **every** X item listed, say what happened to it in Y. One answer per item, no
item skipped. No category is the expected one: do not lean towards "nothing happened", and do
not lean towards finding a change. Report what the transcript shows.

## Method, per X item

1. **Understand the item.** Read the annotator's sentence and its X passage. Note its parts if
   it has several (listed features, numbers, conditions, sub-tasks, a deadline) and what it
   would produce if carried out (a presentation, a prototype, a figure, a document, a choice).
   The annotator's sentence is the record of what X agreed; the passage is only the excerpt the
   annotator pointed at, so a part named in the sentence counts even when the excerpt does not
   show it. Do not add parts that neither states.
2. **Search the whole of Y.** Read Y from the first line to the last. Look for the item's key
   words, for synonyms and other wordings, and for the thing it would have produced. Match by
   what is being talked about, never by who is speaking and never by wording alone.
3. **Classify the fate**, exactly one of:
   - `reported`: Y reports the item as done or delivers its result (the presentation that was
     promised is given; the figure that was to be looked up is stated; the thing decided on is
     shown as built). All of it, as agreed.
   - `returned_unchanged`: Y comes back to the item (discusses it, relies on it, restates it)
     and it stands as agreed, all parts. Use this when Y returns to it without delivering a
     result; use `reported` when a result is delivered.
   - `changed_openly`: Y changes or drops the item, or a part of it, and someone says so or it
     is decided: withdrawn, deferred, replaced, reported as not done or not possible, asked
     about ("where did the X go?"), or a reason given. However brief, if the part or the fact
     of a change is referred to, the change is open.
   - `changed_silently`: Y comes back to the item, but a part is missing, different or
     contradicted, and nobody notes the change. Stating the new result as though it had always
     been the plan, with the dropped or altered part never named, is silent. Before using
     this, check all of Y: if the part appears elsewhere in other words, the item is not
     changed; if someone notes the change anywhere, it is `changed_openly`.
   - `not_mentioned`: Y never comes back to the item in any way: no report, no result, no
     discussion that relies on it. A passing use of the same word about something else is not
     a return.
   - `not_expected`: the item was only about meeting X itself, or could not sensibly come back
     in the next meeting (say why in `reason`). Use this only when Y does not come back to it;
     if Y does come back to it, use the category that describes what happened.
   If a whole item comes back replaced by something else with nobody noting it, that is
   `changed_silently`; if it does not come back at all, that is `not_mentioned`.
4. **needed_followup** (`yes` / `no`), judged from X alone, independent of the fate: would a
   reasonable member of this team expect to hear about this item in the next meeting? `yes` for
   a task someone took away to do before the next meeting, or a decision the next step of the
   work has to build on. `no` for something settled and finished in X, a housekeeping remark,
   or a matter clearly for later than the next meeting.
5. **Evidence.** `y_lines`: the Y line ids you rest the answer on (at most 12, in order; the
   lines a checker should open first). Empty for `not_mentioned`, and for `not_expected` when
   there is nothing to cite. `what_changed`: for the two `changed_*` fates, the part and how it
   differs ("vegan pastry: absent from the printed menu"); empty otherwise. `reason`: one line.
   `confidence`: `high` = you read all of Y and the evidence is plain; `medium` = a judgement
   call a careful colleague might make differently; `low` = you would not be surprised to be
   wrong.

## Output

JSONL, exactly one object per X item listed in this file, in the order listed, no other text:

```
{"x_item": "D1", "fate": "reported|returned_unchanged|changed_openly|changed_silently|not_mentioned|not_expected", "needed_followup": "yes|no", "y_lines": ["L0123"], "what_changed": "", "reason": "one line", "confidence": "high|medium|low"}
```

## One invented example (not from the data)

X items of a café's opening-week planning meeting:

- D1 (decision): The opening menu will have four things: sourdough, two soups, a vegan pastry,
  and oat milk at no extra charge.
- D2 (decision): The café will open at seven on weekdays.
- D3 (decision): This meeting will skip the staffing point because the chef has to leave early.
- A1 (action): Dana will get two quotes for the awning and bring both.
- A2 (action): Sam will ask the landlord about the bin store.
- A3 (action): The chef will cost the two soups.

Y transcript (shortened; assume the rest of Y says nothing on these items):

```
L0040 [06:10] Chef: so here's the printed menu, sourdough's at the top
L0041 [06:14] Chef: then the two soups, tomato and the leek one
L0042 [06:19] Chef: and the line at the bottom says oat milk is free
L0043 [06:23] Manager: looks good, send it to print
L0060 [09:30] Chef: I costed the soups, both come in under a pound a bowl
L0075 [11:48] Manager: since we open at seven, the bread order has to land by half six
L0090 [14:02] Dana: I only got one quote for the awning, the second firm never rang back
L0091 [14:07] Manager: fine, chase them this week
```

Correct output:

```
{"x_item": "D1", "fate": "changed_silently", "needed_followup": "yes", "y_lines": ["L0040", "L0041", "L0042", "L0043"], "what_changed": "vegan pastry: absent from the printed menu, which is approved for print", "reason": "The menu comes back with three of four parts and is approved; nobody mentions the pastry anywhere in Y.", "confidence": "high"}
{"x_item": "D2", "fate": "returned_unchanged", "needed_followup": "no", "y_lines": ["L0075"], "what_changed": "", "reason": "The seven o'clock opening is relied on as agreed when planning the bread order.", "confidence": "high"}
{"x_item": "D3", "fate": "not_expected", "needed_followup": "no", "y_lines": [], "what_changed": "", "reason": "It only arranged the agenda of meeting X; there is nothing to come back to.", "confidence": "high"}
{"x_item": "A1", "fate": "changed_openly", "needed_followup": "yes", "y_lines": ["L0090", "L0091"], "what_changed": "two quotes: one brought, and Dana says the second firm never rang back", "reason": "The shortfall is stated by the person who had the task.", "confidence": "high"}
{"x_item": "A2", "fate": "not_mentioned", "needed_followup": "yes", "y_lines": [], "what_changed": "", "reason": "Neither the bin store nor the landlord comes up anywhere in Y.", "confidence": "high"}
{"x_item": "A3", "fate": "reported", "needed_followup": "yes", "y_lines": ["L0060"], "what_changed": "", "reason": "The chef reports the cost of both soups.", "confidence": "high"}
```

## Changelog

- v1: first version.
