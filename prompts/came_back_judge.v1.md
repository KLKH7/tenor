# Came back different (judge, v1)

Another reader compared two consecutive meetings of one team and claims that something agreed
in the earlier meeting (X) came back in the later meeting (Y) with a part missing or changed,
and that nobody in Y noted it. You check one claim at a time. You do not know how sure that
reader was, and you should not guess.

Each claim gives you: the X item as a human annotator wrote it, with its passage in X; the
claim; the Y lines the claim cites with 15 lines either side; and other Y passages that share
key words with the item. The excerpts are not all of Y. The packet names a file holding the
full Y transcript: open it whenever the excerpts do not settle the question, and always before
answering `true_silent_drop`, because a part that is mentioned anywhere in Y is not dropped.

Give exactly one verdict:

- `true_silent_drop`: Y does return to the item (reports on it, shows its result, relies on
  it); the named part was really agreed in X; in Y it is missing, changed or contradicted; and
  nobody in Y says so.
- `changed_openly`: the part is missing or changed, but someone in Y says so: withdraws it,
  defers it, replaces it, reports it was not done, asks about it, or gives a reason.
- `not_dropped`: the claim is wrong about the part. It is present in Y (stated, shown or plainly
  implied, possibly in other words or outside the cited lines), or it was never part of what
  X agreed (neither the annotator's sentence nor the X passage states it; the passage is only
  an excerpt, so the sentence alone is enough), or X's own condition explains its absence.
- `item_not_returned`: Y does not return to the item at all; the cited lines are about
  something else or only touch the topic in passing.
- `unclear`: the evidence does not let you choose.

If several parts are named and the claim holds for at least one of them, judge that part and
say which in the reason. Do not reward a claim for being interesting; judge only whether it is
true as defined.

Answer in JSONL, one line per claim, nothing else:

```
{"claim_id": "<as given>", "verdict": "true_silent_drop|changed_openly|not_dropped|item_not_returned|unclear", "reason": "one or two sentences naming the Y lines you relied on"}
```
