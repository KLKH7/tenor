# Does a sentence name the item? (reader, v1)

You are given a few items that a human annotator recorded for an earlier meeting (X): decisions
and tasks, each with an id. Below them are the sentences a writer produced about that meeting
for the team's next meeting. You do not know how the sentences were written or whether they
are true; do not judge their truth.

For **every** item listed, say which of the sentences, if any, is about that item: the sentence
names the item's own content (what was decided or assigned), so a reader of the sentence would
know that this item is the one meant. A sentence about something else on the same general
subject does not name the item. A sentence that names only one part of an item with several
parts does name it; say so in `reason`.

Use only this file. Do not open any other file.

## Output

JSONL, exactly one object per item listed, in the order listed, no other text:

```
{"x_item": "D2", "named_by": [1], "reason": "one line"}
```

`named_by` is the list of sentence numbers that name the item, `[]` if none does.

## Changelog

- v1: first version.
