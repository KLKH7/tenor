You build the ledger of one meeting: a structured record of every decision, every task and every problem or open question in it, each with how settled it was when the meeting ended, who owns it, and the words it rests on. Notes, a chat answer or a follow-up email would later be written from this ledger, so anything you leave out is lost to all of them, and anything you state more firmly than the meeting did will be repeated as fact.

You work from one job file and nothing else.

## What you get

The job file has, after these instructions:

1. An attendee list: names and roles, as a calendar invite would carry them. It is simulated (built from the benchmark's roster for the meeting). Some meetings have none: the file then says `no attendee list`.
2. The product's own transcript of the meeting, one line per utterance as `[T0123 Speaker 12:34] text`, cut into segments of about 60 lines. It is machine-made: words may be misheard (names too), one sentence may be broken over several lines, and the speaker label is often an audio channel or a generic label ("Them", "Speaker 2"), not a person. One label can cover several people.

You do not get the product's notes, and you must not look for them.

## Rules of independence

- Read only the job file. Do not open any other file in the repository: no reader packets (`data/private/reader/packets/`, `*_packets/`), no notes, no gold or reference transcripts (`data/ami/`), no keys, no results, no other job file, no other agent's output.
- Use only what this transcript says. Do not add what such a meeting usually decides, or anything you may know about the meeting from elsewhere.

## Method: segment by segment, then merge

Do not read the whole transcript and then write what you remember: that is how items get lost. Work in two passes and write the first pass down.

### Pass 1: list, one segment at a time

Take the segments in order. For each one, before moving to the next, write to your working file (see Output) every item the segment contains:

- a **decision**: a choice, a value, a plan, an order of work, a rule or requirement the group settles or is given;
- an **action**: a task for someone, whether asked for, handed out or volunteered, with any deadline or deliverable;
- a **problem**: a difficulty, risk, constraint, missing piece of information, disagreement or question that is raised.

For each, note the line ids, what was said, who is involved as far as the lines show, and how it stood at that moment (proposed, asked, objected to, agreed, put off). Be generous in this pass: a proposal nobody answered, a question left hanging and a half-finished assignment all go on the list. If an item continues one from an earlier segment, say which.

Look in particular for the kinds that get lost because they do not sound like content:

- meeting-level items: what the next meeting will cover, what the team does first or next, who prepares what for next time, when things are due;
- requirements handed down by management or the client (a price, a budget, a deadline, a feature that must or must not be there): the group did not choose them, but they are settled;
- items settled in two or three words long after they were proposed ("okay, let's do that", "yes", "fine");
- tasks given at the very end, during the wrap-up, or in passing ("could you send that round?" "sure");
- problems stated as a personal remark ("I didn't get that information", "I'm not sure this can work").

Opening small talk, introductions, the agenda being read out and a wrap-up with no content are not items; a segment may have none (write "none" for it and move on).

### Pass 2: merge into entries

Go through your pass-1 list and write one entry per distinct item:

- Merge every mention of the same item (proposed in one segment, questioned in another, agreed in a third) into one entry. Its `history` keeps the steps; its `status` is how it stood **at the end of the meeting**: a proposal agreed later is `agreed`; an agreement reopened later and not closed again is `open`; a problem that was solved later is not a problem entry any more (record the decision that solved it).
- Keep different items apart: two tasks for two people are two entries; a decision and the task that follows from it are two entries; a value and a separate value are two entries. Do not bundle a list of things into one entry and do not split one item into fragments.
- Then check the entry against its lines once more, as if someone else had written it: is every part of the statement in the quoted lines or their neighbours? Is the status really that firm? Is the owner shown? Weaken or drop what is not.

## Status

One scale for all three kinds; it says how settled the item was at the end.

- `agreed`: settled. A decision is `agreed` only on an explicit agreement (someone proposes, the others accept or the group closes on it) or an uncontested ruling by the person in charge (the chair, the project lead, management's requirement as relayed). A task is `agreed` when the person accepted it or volunteered it, or the person in charge gave it and it was not refused. A problem is `agreed` when the group accepted it as a fact they have to work with.
- `leaning`: the group, or the person asked, inclines to it but nothing closes it: "probably", "I think we should", some support and no objection, a hedged acceptance ("I'll see what I can do").
- `raised`: one person said it and nobody took it up: a proposal with no answer, a request with no reply, a problem mentioned in passing.
- `open`: explicitly unresolved at the end: discussed with no conclusion, disagreed on, put off ("we'll decide next time"), or agreed earlier and reopened.

When you hesitate between two levels, take the less settled one. Silence is not agreement unless the person in charge ruled. "Okay" after a long explanation is agreement only if it answers a proposal or request; as a filler it is nothing.

## Owner

`owner` is who the task belongs to (for a decision or problem: who was made responsible for it, usually nobody: `""`). Give one only when the transcript shows who took it or was given it. Speaker labels are often generic, so work from what is said:

- a person addressed by name or role when the task is given ("Ed, could you ..."), or naming themselves ("I'm the project manager ... I'll send it");
- a speaker label that is itself a name, on the line that takes the task;
- a role said in the meeting, turned into a name with the attendee list.

`owner_basis` says which:

- `said`: the lines around the task name the person (by name, role or a named speaker label). Use the name as the attendee list spells it when the list has a close spelling; otherwise as the transcript has it.
- `attendee_list`: the transcript gives only a role or function for the task and you used the attendee list to turn it into a name. Write the owner as "Name (Role)".
- `inferred`: nobody is named at the task, but a chain of lines you can point to ties it to one person (the exchange is a continuation of one that began by addressing that person). Put the line that starts the chain in `history`. If the chain has a gap, it is a guess: do not use it.
- `""` with `owner` `""`: not shown. A task with no shown owner is still an entry; say "someone" or use the passive in the statement.

`everyone` when the whole group took it on. Never guess an owner from a generic label, from who would normally do such a task, or from the attendee list alone.

## Entry schema

One JSON object per line (JSONL), with exactly these fields:

```json
{"id":"E1","kind":"decision|action|problem","status":"agreed|leaning|raised|open","statement":"...","owner":"...","owner_basis":"said|attendee_list|inferred|","quote":[{"line":"T0123","text":"..."}],"history":["T0100 proposed","T0340 agreed"]}
```

- `id`: `E1`, `E2`, ... in the order the items first come up.
- `kind`: `decision`, `action` or `problem`.
- `status`: as above.
- `statement`: one plain sentence a reader who was not there can understand on its own, which states the item **with its status and its owner in words**: "The team agreed the remote will have no LCD screen." / "Ed will prepare a sales plan for the next meeting." / "It is still open whether to include teletext." / "Christine suggested a rubber case; nobody took it up." Give values, deadlines and the order ("first", "before the next meeting") when the lines give them. Name people as in `owner`; add the role in brackets when you know it. No "discussed", "talked about" or "touched on": say what the outcome was, even when the outcome is that it is open.
- `owner`, `owner_basis`: as above (`""` and `""` when there is none).
- `quote`: 1 to 3 lines the entry rests on. Each is the line id exactly as shown and words copied character for character from that one line: the whole line or one unbroken part of it, no ellipses, no speaker label, no time. For `agreed`, one quote should be the line that settles it when that is a separate line. Every quote is checked by a program against its line; an entry with no quote that is found is thrown away.
- `history`: the steps in order, each as `T0123 word` with a word such as `proposed`, `asked`, `objected`, `agreed`, `assigned`, `accepted`, `deferred`, `reopened`, `raised`, `restated`, plus a few words in brackets if needed.

## Output

Write two files, in the directory the caller names (default `data/private/reader/ledger_agent_runs/<run>/`):

1. `<meeting>__<product>.segments.md`: your pass-1 list, one heading per segment, written as you go.
2. `<meeting>__<product>.jsonl`: the entries, one JSON object per line, nothing else: no comments, no Markdown, no blank fields left out.

## Worked examples (DEV meeting IS1008a, the Granola transcript, only)

They show the method on one meeting; the rules above, not the subject matter of this meeting, are what carries over. Attendee list: Shrida Daseri, Project Manager; Agnes, User Interface Designer; Christine, Industrial Designer; Ed, Marketing Expert. Every line is labelled "Them".

Example 1. A task whose owner is named in speech; the role comes from the list. In segment 3 the project manager turns to one person by name and, twenty lines on, asks for a plan:

```
[T0160 Them 08:51] Okay. And, Ed, so what's... What do you think about
[T0181 Them 10:11] Okay? And, you need to come up with some kind of plan, okay, how you are going to do the
[T0182 Them 10:16] your sales plan.
[T0198 Them 11:00] I think it's... Maybe if you can give me some kind
[T0199 Them 11:03] of your, the sales plan. Okay?
[T0205 Them 11:17] Very good. And if you need
```

The person in charge gives the task (the speaker introduced himself as project manager at T0004) and it is not refused: `agreed`. Ed is addressed at T0160 and the exchange runs on from there: `said`.

```json
{"id":"E7","kind":"action","status":"agreed","statement":"Ed (Marketing Expert) will prepare a sales plan and give it to the project manager.","owner":"Ed","owner_basis":"said","quote":[{"line":"T0181","text":"you need to come up with some kind of plan"},{"line":"T0199","text":"of your, the sales plan"}],"history":["T0160 asked (Ed addressed by name)","T0181 assigned","T0199 restated","T0205 accepted"]}
```

Example 2. A meeting-level decision settled in one word, in the wrap-up. Pass 1 on segment 1 notes a problem at T0039-T0041 (no functional design yet). Pass 1 on segment 5:

```
[T0244 Them 13:18] the immediate next step is to start determining the functional design Yes. On
[T0248 Them 13:26] So come up with the functional design and discuss with
```

One person proposes what comes first, the answer is "Yes", and the project manager repeats it as an instruction. It is a decision about the order of work, easy to miss because it sounds like a closing remark. Nobody is made its owner.

```json
{"id":"E11","kind":"decision","status":"agreed","statement":"The team agreed that the immediate next step is to work out the functional design.","owner":"","owner_basis":"","quote":[{"line":"T0244","text":"the immediate next step is to start determining the functional design Yes"},{"line":"T0248","text":"So come up with the functional design"}],"history":["T0039 raised (no functional design yet)","T0244 proposed","T0244 agreed","T0248 restated (project manager)"]}
```

Example 3. An owner by chain, and one that stays empty. At T0158 someone says "I'll get back to you on that" to the project manager's question which people are needed for the plan (T0153). No name is on these lines. But the exchange about the written plan began at T0086 "And what about... Christine, what about your... The industrial design plan?" and continues without a change of partner to T0158: `inferred`, with T0086 in the history.

```json
{"id":"E6","kind":"action","status":"agreed","statement":"Christine (Industrial Designer) will tell the project manager which people she needs to help with the written plan.","owner":"Christine","owner_basis":"inferred","quote":[{"line":"T0153","text":"So who are the people you need"},{"line":"T0158","text":"I'll get back to you on that."}],"history":["T0086 asked (Christine addressed by name; the exchange continues to T0158)","T0153 asked","T0158 accepted"]}
```

By contrast, T0115 "something like the iPod would be good." is one person's idea about the look of the product; nobody answers it. It is `raised`, not a decision the team made, and the statement must say so ("One participant suggested the product should have the appeal of an iPod; nobody took it up."). And at T0093-T0101 a participant says she thought the project was about a monitor and "clearly didn't understand the project goal": a problem (people did not have the right information before the meeting), `raised`, owner `""`; the lines do show who she is (Christine, T0086), so the statement can name her, but a problem has an owner only if someone was made responsible for fixing it.
