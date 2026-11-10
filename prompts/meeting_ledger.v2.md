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

## Method: context sheet, then segment by segment, then merge

Do not read the whole transcript and then write what you remember: that is how items get lost, and how a task ends up with no owner because the line that says who is speaking is two hundred lines away. Work in three passes and write each one down in your working file (see Output).

### Pass 0: the context sheet

A transcript line carries almost no context of its own: a generic label, a pronoun ("you", "your slides", "that plan", "next time"), half a sentence. The context exists, but elsewhere in the transcript. So before listing any item, go through the whole transcript once looking **only** for the things below, and write them at the top of your working file as the context sheet. Every row needs the line id it rests on; a row with no line does not go on the sheet. You will add rows during pass 1 whenever a segment shows something new.

1. **Labels.** For each speaker label: what it can be trusted for. A label that is a person's name is that person. A channel label ("Me", "Them", "Speaker 2") is a channel, not a person: note whether it covers one voice or several (a label whose lines introduce two different names covers several), and who it is if a line shows it ("Me" introducing herself by name).
2. **Cast.** One row per person the transcript shows: the name as heard, the attendee-list spelling when one is close, the role or function, and the line that shows each (a self-introduction, being addressed by name, someone else stating the role). If a role is corrected or reassigned in the meeting, keep both lines and mark which stands at the end. A person on the attendee list who never shows up in the lines gets a row marked `list only`.
3. **Who runs the meeting**, if anyone does: the person who opens it, puts questions to the others in turn, hands out work and closes it, with two or three of those lines. Many requests and rulings in a meeting come from this person and many "I will" answers go to them, so knowing which lines are theirs is the main way to tell voices apart under a shared label.
4. **Areas.** Who is said, in the meeting, to be doing or responsible for what ("she is doing the design", "that's with finance, so me"), with the line.
5. **Named things.** The documents, artefacts, dates and events people refer back to: what "the plan", "the slides", "the quote", "next time" are, who made or presented them if a line shows it, and the line where each is first introduced.

The sheet is evidence, not background knowledge: it may hold only what these lines say. Do not fill it from the attendee list alone (the list gives spellings and roles for people the lines already show; it never tells you who is speaking), from what such a meeting usually looks like, or from anything you know from elsewhere.

### Pass 1: list, one segment at a time

Take the segments in order. For each one, before moving to the next, write to your working file every item the segment contains:

- a **decision**: a choice, a value, a plan, an order of work, a rule or requirement the group settles or is given;
- an **action**: a task for someone, whether asked for, handed out or volunteered, with any deadline or deliverable;
- a **problem**: a difficulty, risk, constraint, missing piece of information, disagreement or question that is raised.

For each, note the line ids, what was said, and how it stood at that moment (proposed, asked, objected to, agreed, put off). Be generous in this pass: a proposal nobody answered, a question left hanging and a half-finished assignment all go on the list. If an item continues one from an earlier segment, say which.

For each item also note **who is speaking and who is spoken to**, as far as you can show it, using the sheet: under a shared label, follow the turns (a question and its answer are two voices; a run of lines that asks, rules and thanks is the person running the meeting; an exchange that began by addressing someone by name goes on with that person until another name, a new topic or a new question to the room breaks it). Write `voice: unknown` when you cannot show it. And **resolve what the words point to**: when a line says "that", "those", "your slides", "the plan", "next time", "further things", write next to it which row of the sheet or which earlier line it points to, or `unclear`.

Look in particular for the kinds that get lost because they do not sound like content:

- meeting-level items: what the next meeting will cover, what the team does first or next, who prepares what for next time, when things are due;
- requirements handed down by management or the client (a price, a budget, a deadline, a feature that must or must not be there): the group did not choose them, but they are settled;
- items settled in two or three words long after they were proposed ("okay, let's do that", "yes", "fine");
- tasks given at the very end, during the wrap-up, or in passing ("could you send that round?" "sure");
- problems stated as a personal remark ("I didn't get that information", "I'm not sure this can work").

Opening small talk, introductions, the agenda being read out and a wrap-up with no content are not items (they feed the sheet instead); a segment may have none (write "none" for it and move on).

### Pass 2: merge into entries

Go through your pass-1 list and write one entry per distinct item:

- **Merge** every mention of the same item (proposed in one segment, questioned in another, agreed in a third) into one entry. Its `history` keeps the steps; its `status` is how it stood **at the end of the meeting**: a proposal agreed later is `agreed`; an agreement reopened later and not closed again is `open`.
- **Keep different items apart**: two tasks for two people are two entries; a decision and the task that follows from it are two entries; a value and a separate value are two entries. Do not bundle a list of things into one entry and do not split one item into fragments. One task given to two people together ("the two of you work out the cost") is one entry with both as owner ("A and B"). When the same joint work is asked of each side in turn ("you talk to her", later "you talk to him"), it is one entry; its owners are the people shown to have been given it.
- **A problem stays a problem until the meeting answers it.** It stops being a problem entry only when the meeting itself removes it: the missing fact is supplied, the question is answered, the disagreement is closed (record that as the decision). A task handed out to deal with it does not remove it: keep the problem (usually `agreed`: the group accepted it as a fact to work with) and write the task as its own action entry.
- **Look for the pattern across people.** When two or more people each report the same kind of difficulty in their own words at different points (each says they were not told something, each lacks the same input, each is blocked by the same thing), that is one group-level problem as well as, or instead of, several personal remarks: write one problem entry that says what the group is missing and names each instance with its line. Personal remarks merge into it unless one of them has its own consequence. Do the same for decisions: a requirement that several people state and nobody disputes is one `leaning` entry, not one `raised` entry per person.
- **Say what the words point to.** Write the statement with the references from pass 1 filled in: "the group will go through the two suppliers' quotes at the next meeting", not "they will discuss further things later". Fill a reference in only from the line it points to; if it was `unclear`, keep the meeting's own vague words in the statement and do not make them more specific than they were.
- **Keep what an absent colleague would need.** Pass 1 was generous; here, drop an item when it changed nothing and asks nothing of anyone: a remark that led nowhere and that nobody would need in order to do their work or follow the next meeting. A proposal nobody answered and a problem mentioned once are still kept (`raised`) when they concern the work itself.
- Then check the entry against its lines once more, as if someone else had written it: is every part of the statement in the quoted lines, their neighbours or the sheet rows you cite? Is the status really that firm? Is the owner shown? Weaken or drop what is not.

## Status

One scale for all three kinds; it says how settled the item was at the end.

- `agreed`: settled. A decision is `agreed` only on an explicit agreement (someone proposes, the others accept or the group closes on it) or an uncontested ruling by the person running the meeting or relaying management's requirement. A task is `agreed` when a person accepted it, or committed to it themselves in plain words ("I'll send it", "I will get back to you"), or the person running the meeting gave it **to someone** and it was not refused. A problem is `agreed` when the group accepted it as a fact they have to work with.
- `leaning`: the group, or the person concerned, inclines to it but nothing closes it: "probably", "I think we should", some support and no objection, a hedged acceptance ("I'll see what I can do"), or a person stating a need or intention of their own that nobody confirmed as a task ("I also have to look into that", "I should probably check").
- `raised`: one person said it and nobody took it up: a proposal with no answer, a request with no reply, a problem mentioned in passing, a piece of work that "has to be done" and was given to nobody and answered by nobody.
- `open`: explicitly unresolved at the end: discussed with no conclusion, disagreed on, put off ("we'll decide next time"), or agreed earlier and reopened.

Status and owner are separate questions. A task that was clearly given and clearly accepted is `agreed` even when you cannot show who accepted it (owner `""`); a task with a clear owner who only half took it is `leaning`.

When you hesitate between two levels, take the less settled one. Silence is not agreement unless the person running the meeting ruled. "Okay" after a long explanation is agreement only if it answers a proposal or request; as a filler it is nothing.

## Owner

`owner` is who the task belongs to (for a decision or problem: who was made responsible for it, usually nobody: `""`). Give one only when you can point to the lines that show who took it or was given it. `owner_basis` says **where the name came from**; the three values do not overlap:

- `said`: the name is in the exchange that gives or takes the task: the person is addressed by name there, names themselves there, or the line taking the task has a speaker label that is a name. Use the attendee list's spelling when it has a close one; otherwise the transcript's.
- `inferred`: the name is in the transcript but not in that exchange, and lines you can cite connect the two. There are two kinds of connection, and `history` must hold the connecting line either way:
  - a *continuation*: the exchange is one unbroken conversation with a person who was addressed by name where it began;
  - a *sheet link*: the task's own words point to exactly one row of the context sheet. The person asked for "your slides" is the one the sheet shows presenting them; the one who answers a request made during the closing remarks, between two lines that are the meeting leader's, is the meeting leader; "I" in a line that repeats something only one person said earlier ("as I said, I need this for the board") is that person; a role named at the task ("the designer will ...") is the person who gave that role as theirs in the meeting.
  If the words fit two rows, or the connection needs a step you cannot cite, it is a guess: leave the owner empty.
- `attendee_list`: the transcript gives a role or function for the task and **no line anywhere** ties that role to a name; only the attendee list does. Write the owner as "Name (Role)".
- `""` with `owner` `""`: not shown. A task with no shown owner is still an entry; say "someone" or use the passive. When the sheet narrows it down without settling it, say so in the statement rather than choosing ("given in reply to whoever asked about the next step; the design is Tom's area (T0041), the testing Lena's (T0057)").

`everyone` when the whole group took it on or the meeting leader gave it to the room. An area on the sheet ("she is doing the design") makes its holder the owner of a task only when the task's words name that area and nobody else is being spoken to; who would normally do such a task is never enough, and neither is a generic label or the attendee list by itself.

## Entry schema

One JSON object per line (JSONL), with exactly these fields:

```json
{"id":"E1","kind":"decision|action|problem","status":"agreed|leaning|raised|open","statement":"...","owner":"...","owner_basis":"said|attendee_list|inferred|","quote":[{"line":"T0123","text":"..."}],"history":["T0100 proposed","T0340 agreed"]}
```

- `id`: `E1`, `E2`, ... in the order the items first come up.
- `kind`: `decision`, `action` or `problem`.
- `status`: as above.
- `statement`: one plain sentence a reader who was not there can understand on its own, which states the item **with its status and its owner in words**: "The group agreed to move in the first week of May." / "Tom will get two quotes from movers before the next meeting." / "It is still open whether the kitchen is refitted." / "Lena suggested hiring temporary storage; nobody took it up." Give values, deadlines and the order ("first", "before the next meeting") when the lines give them. Name people as in `owner`; add the role in brackets when you know it. No "discussed", "talked about" or "touched on": say what the outcome was, even when the outcome is that it is open.
- `owner`, `owner_basis`: as above (`""` and `""` when there is none). Two joint owners are written "A and B".
- `quote`: 1 to 3 lines the entry rests on. Each is the line id exactly as shown and words copied character for character from that one line: the whole line or one unbroken part of it, no ellipses, no speaker label, no time. For `agreed`, one quote should be the line that settles it when that is a separate line. Every quote is checked by a program against its line; an entry with no quote that is found is thrown away.
- `history`: the steps in order, each as `T0123 word` with a word such as `proposed`, `asked`, `objected`, `agreed`, `assigned`, `accepted`, `deferred`, `reopened`, `raised`, `restated`, plus a few words in brackets if needed. For an `inferred` owner the connecting line is one of the steps.

## Output

Write two files, in the directory the caller names (default `data/private/reader/ledger_agent_runs/<run>/`):

1. `<meeting>__<product>.segments.md`: the context sheet first (pass 0), then your pass-1 list, one heading per segment, written as you go.
2. `<meeting>__<product>.jsonl`: the entries, one JSON object per line, nothing else: no comments, no Markdown, no blank fields left out.

## Worked example (invented; not from any benchmark meeting)

The lines below were written for these instructions. They show the method; nothing about their subject carries over. Attendee list: Priya Raman, Operations Lead; Tom Okafor, Facilities; Lena Fischer, Finance. Labels are "Me" and "Them".

```
[T0002 Them 00:08] Thanks for joining. I'm Priya, I'll take us through the move, I've got a few slides.
[T0004 Them 00:19] Tom, you've got the building side, yes?
[T0005 Them 00:22] Yeah, facilities, that's me.
[T0006 Me 00:25] And I'm Lena, I'm doing the budget for it.
[T0031 Me 03:02] I should say I never got the floor plan, so I couldn't cost anything.
[T0032 Them 03:07] Okay.
[T0047 Them 05:40] Tom, how many desks on the second floor?
[T0048 Them 05:44] Can't tell you, nobody sent me the headcount.
[T0049 Them 05:48] Right. I'll chase both of those today.
[T0060 Them 07:15] and can you get two quotes from movers?
[T0061 Them 07:18] I'll see what I can do, it's a busy week.
[T0074 Me 09:30] Somebody has to tell the staff about the dates at some point.
[T0075 Them 09:34] Mm. So, the kitchen.
[T0085 Them 11:02] Fine, then we look at those next week and pick one.
[T0086 Me 11:05] Okay.
[T0087 Them 11:07] Yes.
[T0088 Me 11:20] Before we go, could you send round your slides?
[T0089 Them 11:23] Will do.
[T0090 Them 11:25] Right, thanks everyone.
```

Context sheet (pass 0). Labels: "Me" = Lena (T0006), one voice; "Them" = at least two voices (Priya T0002, Tom T0005). Cast: Priya Raman, Operations Lead on the list (T0002); Tom Okafor, facilities (T0004-T0005); Lena Fischer, budget (T0006). Runs the meeting: Priya (T0002 opens, T0047 questions Tom, T0090 closes). Areas: building = Tom (T0004-T0005); budget = Lena (T0006). Named things: the slides = Priya's (T0002); "next week" = the next meeting (T0085).

Pass 1 notes on voices and references: T0048 is Tom (answers T0047, which names him). T0049 "both of those" = the floor plan (T0031) and the headcount (T0048); the voice asks, rules and closes: Priya. T0060-T0061 continue the exchange with Tom begun at T0047. T0085 "those" = the two movers' quotes (T0060). T0089 answers "your slides" (T0088): the slides are Priya's (T0002), and T0090 is her closing.

```json
{"id":"E1","kind":"problem","status":"agreed","statement":"People had not been sent the information they needed before the meeting: Lena (Finance) never got the floor plan and could not cost anything, and Tom (Facilities) was not sent the headcount; the group took this as given and Priya said she would chase both.","owner":"","owner_basis":"","quote":[{"line":"T0031","text":"I never got the floor plan, so I couldn't cost anything"},{"line":"T0048","text":"nobody sent me the headcount"}],"history":["T0031 raised (Lena)","T0048 raised (Tom)","T0049 accepted"]}
{"id":"E2","kind":"action","status":"agreed","statement":"Priya (Operations Lead) will chase the floor plan and the headcount today.","owner":"Priya Raman","owner_basis":"inferred","quote":[{"line":"T0049","text":"I'll chase both of those today."}],"history":["T0002 Priya runs the meeting (sheet link: T0047-T0049 are her lines, question to Tom then ruling)","T0049 volunteered"]}
{"id":"E3","kind":"action","status":"leaning","statement":"Tom (Facilities) was asked to get two quotes from movers and said only that he would see what he could do.","owner":"Tom Okafor","owner_basis":"inferred","quote":[{"line":"T0060","text":"can you get two quotes from movers?"},{"line":"T0061","text":"I'll see what I can do"}],"history":["T0047 asked (Tom addressed by name; the exchange continues to T0061)","T0060 asked","T0061 accepted (hedged)"]}
{"id":"E4","kind":"action","status":"raised","statement":"Lena said someone has to tell the staff about the dates; nobody took it on.","owner":"","owner_basis":"","quote":[{"line":"T0074","text":"Somebody has to tell the staff about the dates"}],"history":["T0074 raised"]}
{"id":"E5","kind":"decision","status":"agreed","statement":"The group agreed to look at the two movers' quotes at next week's meeting and pick one then.","owner":"","owner_basis":"","quote":[{"line":"T0085","text":"then we look at those next week and pick one"},{"line":"T0087","text":"Yes."}],"history":["T0060 asked (the two quotes)","T0085 proposed","T0086 agreed","T0087 agreed"]}
{"id":"E6","kind":"action","status":"agreed","statement":"Priya (Operations Lead) will send her slides round after the meeting.","owner":"Priya Raman","owner_basis":"inferred","quote":[{"line":"T0088","text":"could you send round your slides?"},{"line":"T0089","text":"Will do."}],"history":["T0002 Priya has the slides (sheet link)","T0088 asked","T0089 accepted"]}
```

What to take from it. E1 is one group-level problem built from two personal remarks, and it stays although E2 is a task to deal with it. E2 and E6 get their owner from the sheet, with the sheet line in `history`; had two people shown slides, E6 would have owner `""`. E3 is `leaning` because the acceptance is hedged, and its owner comes by continuation. E4 has no owner and no taker: `raised`, and "Mm" is not an answer. E5 says what "those" are because T0060 shows it. Lena's name comes from T0006, so nothing here is `attendee_list`: that basis would apply only if a line said "finance will do it" and no line showed who finance is.

## Changelog

v2 (from the DEV run on IS1008a only: two generator agents' reports and a reading of their ledgers against DEV gold; no TEST verdicts or gold were opened).

- New pass 0, the context sheet (labels, cast, who runs the meeting, areas, named things, each with its line). DEV: both ledgers left the owner empty on two wrap-up tasks whose owner was shown only far from the task, and both agents reported that "a chain with a gap" did not say whether such evidence counts.
- Pass 1 now records who speaks and is spoken to, and what vague references point to; pass 2 writes statements with the references filled in. DEV: the next-meeting decision was recorded as "further things will be discussed later".
- `owner_basis` now says where the name came from, with no overlap: `said` (in the exchange), `inferred` (elsewhere in the transcript, by continuation or sheet link, line cited), `attendee_list` (only the list ties role to name). DEV: the two agents chose different bases for the same people.
- A problem stays an entry until the meeting itself answers it; a task to deal with it is a separate entry. A difficulty reported by several people is one group-level problem. DEV: one ledger folded the missing-information problem into task histories and kept one person's remark only.
- Status: a plain self-commitment is `agreed`, a stated need or intention is `leaning`, work given to nobody and answered by nobody is `raised`; status and owner are judged separately. Joint and mirrored tasks are one entry with joint owners. A keep test in pass 2 for remarks that changed nothing (DEV: 31 and 26 entries for a 15-minute meeting).
- The worked examples from IS1008a are replaced by one invented example, so that no names, roles or subject matter of the DEV meeting (whose team also appears in TEST meetings IS1008b-d) reach the generator.
