# Statement kind, support side (v1): which transcript lines does each bullet rest on?

You are given a product's AI meeting notes and, for one bullet at a time, the stretch of the product's own transcript that the bullet summarises. For each bullet, pick the transcript lines where the bullet's **content is said**.

This is a locating task only. Do not judge whether the content was a fact, an opinion, a suggestion or a decision, whether the notes got it right, or who said it. Read only this job file: do not open any other file in the repository, do not search the web, and do not use anything you may remember about this meeting.

## Input

1. `<notes>`: all of the product's notes as bullets `B1, B2, ...`, each with its section heading; a sub-point names its parent and is read together with it.
2. Per job, `<bullet>`: the one bullet (with its parents) and `<span>`: the stretch of transcript it came from, a few lines widened on each side, one line per utterance as `[T0123 Speaker 12:34] text`. A `...` line marks a jump between two stretches. The transcript is machine-made: words may be misheard, one sentence may be broken over several lines, and speaker labels ("Them", "Speaker 2") are audio channels or generic labels, not people.

## What to pick

- The lines that **carry the bullet's content**: where the thing the bullet states is put into words. 1 to 6 line ids, all from this job's `<span>`, in transcript order.
- Prefer the lines that state it over the lines around it. Leave out lines that only react ("yeah", "okay", "mm-hmm", "right"), lines that only ask the question the content answers, and lines on a neighbouring topic.
- If the content is said more than once or by more than one person, pick the lines that together cover what the bullet states (still at most 6); if one sentence is broken over several lines, pick all its parts.
- For a sub-point, pick lines for the sub-point's own content, not its parent's.
- For a heading-like bullet that only introduces its sub-points, pick the lines that introduce the topic, if any.
- `[]` if the span does not contain the bullet's content (the alignment is wrong, or the bullet states something nobody says in these lines). Do not pick the nearest lines just to have an answer.

## Example (invented)

```
<bullet>
B4 [section: Decisions] Entry will be free
</bullet>

<span>
[T0040 Them 06:10] Okay, next thing, the door.
[T0041 Them 06:13] What do we do about tickets?
[T0042 Speaker 2 06:16] I'd say we just don't charge at the door,
[T0043 Speaker 2 06:19] people spend more at the stalls when they get in for free.
[T0044 Them 06:24] Yeah.
[T0045 Them 06:25] Fine by me, free entry then.
[T0046 Them 06:29] And the raffle, who has the tickets from last year?
</span>
```

```
{"bullet_id": "B4", "support": ["T0042", "T0043", "T0045"]}
```

T0042-T0043 and T0045 put "entry is free" into words. T0041 only asks, T0044 only reacts, T0046 is the next topic.

## Output

One JSON object per line, one line per job, every job exactly once, in order, nothing else:

`{"bullet_id": "B12", "support": ["T0123", "T0124"]}`
