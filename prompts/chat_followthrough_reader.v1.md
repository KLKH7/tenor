# Follow-through answers (reader, v1)

You are given two consecutive meetings of the same team, as a notes product has them: the
product's notes of the earlier meeting (X), the items of those notes that are decisions or tasks
(each with an id), the product's own machine transcript of X, and the product's own machine
transcript of the later meeting (Y). Below them are several answers to the question "what from
the earlier meeting was not mentioned or followed up in the later one?", each under a letter and
split into numbered claims. You do not know how the answers were written. Check every claim.

Use only this file. Do not open any other file, do not search, and do not use anything you may
remember about these meetings. Do not lean towards agreeing or disagreeing: report what the
documents show.

## Per claim

1. **What it claims.** Most claims say that something from X (a task, a decision, an open point,
   an idea) did not come up in Y, or came up only in part. Some say the opposite (that something
   *was* followed up), or describe what Y did instead. A claim that only names a thing ("Put
   everyone's materials in the shared folder.") means that thing was not mentioned or followed
   up in Y. A claim may bundle several things; judge all of them.
2. **Check X.** Was it said or recorded in X as the claim describes (X's notes or X's
   transcript)? An item a claim calls a "decision" or "next step" that X only raised as an idea
   is an overstatement, not a falsehood: mark `partly`.
3. **Check Y.** Read **all** of Y, first line to last. It is a machine transcript: names and terms
   may be misspelt and speakers unlabelled; match by what is being talked about, not by words.
   - "Not mentioned / not followed up / not revisited": search for the thing's key words, other
     wordings and the thing it would have produced. If Y reports on it, relies on it, discusses
     it or settles it anywhere, the claim is wrong (`no`), or `partly` if Y touches it only in
     passing and the claim says "not explicitly" or "only partly".
   - "Was followed up" or "Y did Z": check Y shows it.
4. **verdict**, exactly one of:
   - `yes`: everything the claim says holds.
   - `partly`: the core holds but a part or detail is wrong or overstated (say which).
   - `no`: the core is wrong: X has no such thing, or Y does come back to it.
5. **x_items**: the ids from the item list that the claim says went unmentioned or unfollowed
   (all of them if it bundles several; an item may appear under two ids if the notes repeat it).
   `[]` if it names none of them (for example an idea that is not on the list, or a claim that
   something *was* followed up).
6. **y_lines**: for `no` or `partly`, the Y lines that show the problem (at most 6), else `[]`.

## Output

JSONL, one object per claim, in the order given (all answers, all claims), no other text:

```
{"answer": "C", "claim": 2, "verdict": "yes|partly|no", "x_items": ["A3"], "y_lines": ["L0123"], "reason": "one line"}
```

## Changelog

- v1: first version, adapted from `since_last_time_check.v1` so that the product's chat and our
  writer are judged by the same standard, in the same job, under hidden labels.
