# Since last time (checker, v1)

You are given two consecutive meetings of the same team, as a notes product has them: the
product's notes of the earlier meeting (X), the items of those notes that are decisions or
tasks (each with an id), and the product's own transcript of the later meeting (Y). Below them
are up to 3 sentences that claim something from X went wrong quietly in Y. You do not know how
the sentences were written. Check each one against the two documents.

Use only this file. Do not open any other file and do not use anything you may remember about
these meetings. Do not lean towards agreeing or disagreeing: report what the documents show.

## Per sentence

1. **What it claims.** A sentence claims two things: what X's notes recorded, and what happened
   to it in Y. It is one of two kinds: `changed` (Y comes back to it with a part missing,
   different or contradicted, and nobody in Y says so) or `not_mentioned` (Y never comes back to
   it in any way).
2. **Check X.** Is what the sentence says was agreed or assigned what X's notes record?
3. **Check Y.** Read all of Y, first line to last. The transcript is a machine transcript: names
   and terms may be misspelt and speakers unlabelled; match by what is being talked about.
   - For `not_mentioned`: search for the item's key words, other wordings and the thing it would
     have produced. If Y reports on it, relies on it or discusses it anywhere, the claim is wrong.
   - For `changed`: check that Y comes back to the item as the sentence says, that the part
     really is missing or different in all of Y (not said elsewhere in other words), and that
     nobody notes the change, gives a reason or asks about it. If someone does, the claim is wrong.
4. **supported**, exactly one of:
   - `yes`: both halves hold as stated.
   - `partly`: the core holds but a stated detail is wrong or overstated (say which).
   - `no`: X's notes do not record it, or Y shows otherwise.
5. **x_items**: the ids from the item list that the sentence is about (the item it says went
   wrong). Usually one. `[]` if it is about nothing on the list.
6. **y_lines**: for `no` or `partly`, the Y lines that show the problem (at most 6); for `yes`
   on a `changed` sentence, the lines you rest on; otherwise `[]`.

## Output

JSONL, one object per sentence, in the order given, no other text:

```
{"line": 1, "supported": "yes|partly|no", "x_items": ["D3"], "y_lines": ["L0123"], "reason": "one line"}
```

## Changelog

- v1: first version.
