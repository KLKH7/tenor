# What the meeting did with a reminder (reader, v1)

You are given a few bullets about a team's previous meeting: each reminds the team of a task
someone took away or of something the team agreed. Below them is the full transcript of the
team's next meeting (Y). You do not know how the bullets were written or by whom.

Your job: for **every** bullet, say what happened to the thing it names in Y. One answer per
bullet, no bullet skipped. No category is the expected one: do not lean towards "nothing
happened", and do not lean towards finding that it was dealt with. Report what the transcript
shows.

## Method, per bullet

1. **Understand the bullet.** Note what it names: the task or the decision, its parts if it has
   several (listed features, numbers, conditions, two people's tasks), and what it would
   produce if carried out (a presentation, a prototype, a figure, a document, a choice). Do
   not add parts the bullet does not state. Ignore any advice in the bullet ("worth
   checking", "keep in mind"): judge only the task or decision it names.
2. **Search the whole of Y.** Read Y from the first line to the last. Look for the bullet's
   key words, for synonyms and other wordings, and for the thing it would have produced.
   Match by what is being talked about, never by who is speaking and never by wording alone.
3. **Classify the fate**, exactly one of:
   - `reported`: Y reports the thing as done or delivers its result (the presentation that was
     promised is given; the figure that was to be looked up is stated; the thing decided on is
     shown as built). All of it, as the bullet has it.
   - `returned_unchanged`: Y comes back to it (discusses it, relies on it, restates it) and it
     stands as the bullet has it, all parts. Use this when Y returns to it without delivering
     a result; use `reported` when a result is delivered.
   - `changed_openly`: Y changes or drops it, or a part of it, and someone says so or it is
     decided: withdrawn, deferred, replaced, reported as not done or not possible, asked about
     ("where did the X go?"), or a reason given. However brief, if the part or the fact of a
     change is referred to, the change is open.
   - `changed_silently`: Y comes back to it, but a part is missing, different or contradicted,
     and nobody notes the change. Stating a new result as though it had always been the plan,
     with the dropped or altered part never named, is silent. Before using this, check all of
     Y: if the part appears elsewhere in other words, it is not changed; if someone notes the
     change anywhere, it is `changed_openly`.
   - `not_mentioned`: Y never comes back to it in any way: no report, no result, no
     discussion that relies on it. A passing use of the same word about something else is not
     a return.
   If the whole thing comes back replaced by something else with nobody noting it, that is
   `changed_silently`; if it does not come back at all, that is `not_mentioned`.
4. **expect_to_hear** (`yes` / `no`), judged from the bullet alone, independent of the fate:
   would a reasonable member of this team expect to hear about this in the next meeting? `yes`
   for a task someone took away to do before the next meeting, or a decision the next step of
   the work has to build on. `no` for something settled and finished, a housekeeping remark,
   or a matter clearly for later than the next meeting.
5. **Evidence.** `y_lines`: the Y line ids you rest the answer on (at most 12, in order; the
   lines a checker should open first). Empty for `not_mentioned`. `what_changed`: for the two
   `changed_*` fates, the part and how it differs; empty otherwise. `reason`: one line.
   `confidence`: `high` = you read all of Y and the evidence is plain; `medium` = a judgement
   call a careful colleague might make differently; `low` = you would not be surprised to be
   wrong.

Use only this file. Do not open any other file.

## Output

JSONL, exactly one object per bullet, in the order listed, no other text:

```
{"bullet": 1, "fate": "reported|returned_unchanged|changed_openly|changed_silently|not_mentioned", "expect_to_hear": "yes|no", "y_lines": ["L0123"], "what_changed": "", "reason": "one line", "confidence": "high|medium|low"}
```

## One invented example (not from the data)

Bullets about a café's opening-week planning meeting:

1. Last time the team agreed the opening menu: sourdough, two soups, a vegan pastry, and free
   oat milk; keep that in mind for the menu work.
2. Dana was going to get two quotes for the awning; worth checking where that stands.
3. Sam was going to ask the landlord about the bin store; worth checking where that stands.
4. The chef was going to cost the two soups.

Y transcript (shortened; assume the rest of Y says nothing on these):

```
L0040 [06:10] Chef: so here's the printed menu, sourdough's at the top
L0041 [06:14] Chef: then the two soups, tomato and the leek one
L0042 [06:19] Chef: and the line at the bottom says oat milk is free
L0043 [06:23] Manager: looks good, send it to print
L0060 [09:30] Chef: I costed the soups, both come in under a pound a bowl
L0090 [14:02] Dana: I only got one quote for the awning, the second firm never rang back
L0091 [14:07] Manager: fine, chase them this week
```

Correct output:

```
{"bullet": 1, "fate": "changed_silently", "expect_to_hear": "yes", "y_lines": ["L0040", "L0041", "L0042", "L0043"], "what_changed": "vegan pastry: absent from the printed menu, which is approved for print", "reason": "The menu comes back with three of four parts and is approved; nobody mentions the pastry anywhere in Y.", "confidence": "high"}
{"bullet": 2, "fate": "changed_openly", "expect_to_hear": "yes", "y_lines": ["L0090", "L0091"], "what_changed": "two quotes: one brought, and Dana says the second firm never rang back", "reason": "The shortfall is stated by the person who had the task.", "confidence": "high"}
{"bullet": 3, "fate": "not_mentioned", "expect_to_hear": "yes", "y_lines": [], "what_changed": "", "reason": "Neither the bin store nor the landlord comes up anywhere in Y.", "confidence": "high"}
{"bullet": 4, "fate": "reported", "expect_to_hear": "yes", "y_lines": ["L0060"], "what_changed": "", "reason": "The chef reports the cost of both soups.", "confidence": "high"}
```

## Changelog

- v1: first version, built from `item_fates.v1`'s outcome definitions: the same five fates
  (without `not_expected`) applied to a reminder bullet instead of an annotated item, and
  `needed_followup` judged from the bullet as `expect_to_hear`.
