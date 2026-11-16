# Quiet change (verifier, v1)

You are given two consecutive meetings of the same team, as a notes product has them: the
product's notes of the earlier meeting (X) and the product's own transcript of the later meeting
(Y), in full. Below them are up to 3 proposed lines. Each claims that something X's notes
recorded comes back in Y in a different version and that nobody in Y mentioned the change, and
quotes the lines of Y it rests on. Only the lines you accept will be shown to a user, unasked.
A wrong line shown to a user is far worse than a true line held back: **reject whenever you are
unsure.** You do not know how the lines were written.

Use only this file. Do not open any other file and do not use anything you may remember about
these meetings.

## Per line

Do all three checks for every line, whatever the outcome of the first.

**(a) Do the quotes show a different version?**

1. Read the X bullet(s) the line cites. What exactly did X record, and how firmly (decided, a
   task, or only an idea)?
2. Find each quoted line in Y. Check the quoted words are there. Read at least ten lines either
   side.
3. Answer `yes` only if, read in context, the quoted lines show Y's own version of the same
   thing stated as the current plan, design or result (presented, listed, costed, built,
   reported), and that version differs from X's: it contradicts or replaces X's, or leaves out a
   part of X's item from what is plainly a full list or a full design. Answer `no` if the quoted
   lines are a suggestion, a question, an option floated, one person's liking or opinion, a
   different subject, or only more detail on something X left open; or if X's version is also
   stated anywhere in Y as still standing; or if X's notes did not record it as decided or as a
   task.

**(b) Does anyone in Y mention, question or explain the change?**

1. Write down the item's key words: every part of X's version, every part of Y's version, and
   two or three other wordings of each (synonyms, short forms, how a machine or a speaker might
   say it). At least 5 terms.
2. Search **all** of Y, first line to last, for each term. Also search for words of change and
   reason: instead, rather, no longer, not anymore, changed, dropped, decided, budget, cost,
   price, expensive, afford, can't, not possible, doesn't work, still need, what about, I
   thought, weren't we.
3. Read the lines around every hit about this item. Report the closest passages: the up to 6
   lines that come nearest to raising the change, each with a few words on what it says.
4. Answer `yes` if any line anywhere in Y names the change, the old version, the dropped or
   altered part, or the gap; asks about it; gives a reason for it; reports it as not done or
   not possible; decides to change it; or says it still has to be costed, checked or fitted in. However
   brief, and wherever it is (the recap, the agenda, a remark long before or after the quoted
   stretch), it counts. Answer `no` only if you searched every term and no line does any of
   this. Stating Y's version as the plan, even as something decided ("so we've decided
   it's a basic TV remote"), is not raising the change; a line raises it only when it refers to
   X's version, to the part that is different or missing, or to the fact of a change, or gives
   a reason for moving away from X's version.

**(c) Does the line say more than the quotes show?**

Answer `yes` if any part of the line is not shown by the X bullet or by the quoted lines read in
context. In particular: the line says something was chosen, agreed, decided, dropped, replaced,
final or settled and no quoted line says so; it gives a reason; it makes X's version firmer or
broader than X's bullet; it describes Y's version beyond what the quotes say (a material, a
figure, a list, a person the quotes do not show); or it says the change was not mentioned in a
way that goes beyond "nobody mentioned it". Answer `no` otherwise.

**Verdict.** `accept` only if (a) is `yes`, (b) is `no` and (c) is `no`. Otherwise `reject`.

## Output

JSONL, one object per line, in the order given, no other text:

```
{"line": 1, "terms_searched": ["...", "..."], "closest": [{"line": "L0123", "says": "..."}], "a_shows_different_version": "yes|no", "b_change_raised": "yes|no", "c_says_more": "yes|no", "verdict": "accept|reject", "reason": "one or two sentences"}
```

## Changelog

- v1: first version. A separate check on every proposed quiet-change line before it can be
  shown: (a) the quotes show a different version stated as the current one, (b) a search of
  the whole later transcript for the item's key words and for words of change and reason, with
  the closest passages reported, finds nobody raising the change, (c) the line says no more
  than the quotes. Accept only when all three hold. Developed on the 6 development pairs of the
  item-fates study and on IS1008a to IS1008b.
