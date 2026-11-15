# Before the meeting (brief, v1)

You are given the notes of a team's last meeting (X). The team meets again shortly. Write the
short brief a member of the team would read just before walking in: where you left off and any
open items from last time. At most **3** bullets.

You have the notes of the last meeting only. You do not know what the next meeting will cover,
and you must not guess at it. Use only this file. Do not open any other file, do not search the
web, and do not use anything you may remember about these meetings.

## What goes in the brief

The things from last time that are still open going into the next meeting:

- A **task** someone took away (to find out, draw up, cost, ask, prepare, send, bring, work
  on): the team will want to hear how it went.
- A **decision** the next step of the work has to build on (a feature, a material, a price, a
  target, a shape, a rule for the work): worth having in mind before the work moves on.

Leave out housekeeping that changes nothing about the work (sending round the slides or the
minutes, booking the room, the next meeting's time), and anything the notes present as settled
and finished in that meeting with nothing left to do.

If more than 3 things qualify, keep the 3 that matter most to the work: something the team
will build, buy or promise differently because of it comes before a detail. Two related items
may share a bullet when they belong together (the same person's two tasks, say). If nothing is
open, write fewer bullets, or none.

## The bullet

One plain sentence per bullet, as you would say it to a colleague, under 30 words: who was
going to do what, or what the team agreed, and that it is open going into today. Say it as a
reminder, not as news: you do not know what has happened since. No ids, no "the notes", no
advice beyond the reminder.

- "Dana was going to get two quotes for the awning; worth checking where that stands."
- "Last time the team agreed the menu would be printed on recycled card; keep that in mind
  for the menu work."

## Evidence

- `x_bullets`: the bullet id or ids of X's notes the bullet rests on (at least one). If a
  bullet covers two items, cite both.

## Output

JSONL, one object per bullet, at most 3 lines, no other text. If nothing is open, write an
empty file.

```
{"bullet": "...", "x_bullets": ["B4"]}
```

## Changelog

- v1: first version. A stand-in for a pre-meeting brief (where you left off and any open
  items), written from the earlier meeting's notes alone. Developed on the 6 development pairs
  of the item-fates study only.
