# Statement kind, notes side (v1): how do the notes present each bullet?

You are given a product's AI meeting notes, split into bullets. For **every** bullet, say how the notes present it to a reader who was not in the meeting: as a fact, a settled decision, a task, somebody's opinion, a proposal, an open point, or mere scaffolding.

You judge the **wording of the notes only**. You do not have the transcript and you must not guess what "really" happened in the meeting: a bullet that reads as settled is `decision` even if you suspect it was only floated. Read only this job file: do not open any other file in the repository, do not search the web, and do not use anything you may remember about this meeting.

## Input

`<notes>`: every bullet as `B12 [section: <heading>] (sub-point of B10) text`. The section heading and the parent are part of how a bullet is presented: a bullet under "Decisions" or "Agreed" is presented as decided; a bullet under "Open questions" is presented as open; a sub-point is read together with its parent ("Options considered:" above it makes it a proposal).

## presented_as (exactly one)

- `fact`: stated as true, as how things are or were: background, a number, a finding, a description of the product, the market, a person or a situation. No sign that it is someone's view or that the group chose it.
- `decision`: stated as settled or agreed. Includes requirements and plain "will" / "must" / "to be" statements about the product or the plan ("The case will be rubber", "Launch in May", "Must cost under 12 euros"), and anything under a decisions heading.
- `task`: someone is to do something (an action item, an assignment, a next step for a person or the group), with or without a name.
- `opinion`: stated as someone's view, preference, liking or judgement ("Ana prefers ...", "the team felt ...", "concern that ...", "seen as too costly").
- `proposal`: stated as an idea, option or suggestion that is not settled ("Idea: ...", "Option: ...", "could add ...", "suggested using ...").
- `open`: a question, a risk, something undecided or to be decided later.
- `info`: scaffolding with no claim of its own: a heading-like bullet that only introduces its sub-points ("Three phases:"), an attendee line, a date, a meta line about the notes.

When two fit, choose by the main clause. A bare evaluative statement with nobody attached ("The yellow version is too childish") is `fact`: the notes state it as how things are. The same with a holder or a marker of view ("Ana found the yellow version too childish", "felt too childish") is `opinion`.

## attributed_to

The person, role or group the notes attach **this statement** to: who holds the view, made the proposal, decided, or is to do the task. Copy it as the notes write it ("Ana", "Marketing", "the team"). Use the bullet itself, its parent, or a section heading that is a person or role (a bullet under "Marketing report" is attached to Marketing). `""` if the notes attach it to nobody. A person who is only the topic of the bullet ("Ana is from Lisbon") is not an attribution: `""`.

## hedged

`yes` if the wording marks the statement as less than certain or less than settled: "may", "might", "could", "possibly", "likely", "considered", "suggested", "proposed", "explored", "discussed", "to be confirmed", "leaning towards", a question mark. Otherwise `no`. Judge the bullet's own words, plus a hedging parent or heading ("Ideas floated:").

## Example (invented)

```
B1 [section: Venue] Community hall seats 120
B2 [section: Venue] Hall is too gloomy for a spring fair
B3 [section: Venue] Rosa suggested moving the stalls outdoors
B4 [section: Decisions] Entry will be free
B5 [section: Next steps] Tomas to book the hall by Friday
B6 [section: Next steps] Who brings the sound system?
B7 [section: Treasurer's update] Raffle income covers the hall fee
B8 [section: Stalls] Ideas floated:
B9 [section: Stalls] (sub-point of B8) Second-hand book table
```

```
{"bullet_id": "B1", "presented_as": "fact", "attributed_to": "", "hedged": "no"}
{"bullet_id": "B2", "presented_as": "fact", "attributed_to": "", "hedged": "no"}
{"bullet_id": "B3", "presented_as": "proposal", "attributed_to": "Rosa", "hedged": "yes"}
{"bullet_id": "B4", "presented_as": "decision", "attributed_to": "", "hedged": "no"}
{"bullet_id": "B5", "presented_as": "task", "attributed_to": "Tomas", "hedged": "no"}
{"bullet_id": "B6", "presented_as": "open", "attributed_to": "", "hedged": "yes"}
{"bullet_id": "B7", "presented_as": "fact", "attributed_to": "Treasurer", "hedged": "no"}
{"bullet_id": "B8", "presented_as": "info", "attributed_to": "", "hedged": "yes"}
{"bullet_id": "B9", "presented_as": "proposal", "attributed_to": "", "hedged": "yes"}
```

B2 is `fact`: a judgement, but the notes state it flatly with nobody attached. B7 is `fact` attached to the Treasurer through its heading.

## Output

One JSON object per line, one line per bullet, every bullet of `<notes>` exactly once, in order, nothing else:

`{"bullet_id": "B12", "presented_as": "fact|decision|task|opinion|proposal|open|info", "attributed_to": "", "hedged": "yes|no"}`
