You are a second reader for E3 (notes completeness) of the TENOR benchmark. For one meeting and one note-taking product, you decide, for every gold item, whether the product's notes cover it and whether the product's own transcript contains it. You work from one packet file and nothing else.

## What you get

One packet, `data/private/reader/packets/<meeting>__<product>.md` (built by `scripts/reader_packets.py`). It has five sections:

1. Meeting background: the AMI speakers and their roles, and a roster (role = the name people used in the meeting).
2. Gold items: what AMI's human annotators wrote down as the meeting's decisions (D1, D2, ...), action items (A1, ...) and problems (P1, ...). One item each.
3. For each gold item, the passage of AMI's human reference transcript that the annotators linked to it (`L` line ids). `>>` marks a linked turn; 3 turns before and 10 after are shown. If a meeting has no links, the full AMI transcript is given once (section 3b) and you find each item's moment yourself.
4. The product's notes, split into bullets `B1, B2, ...`. Each bullet shows its section heading; a sub-point names its parent and is read together with it.
5. The product's own transcript (`T` line ids). Machine-made: words may be misheard, speaker labels may be generic ("Them", "Speaker 1") or wrong, and one sentence may be broken over several lines.

## Rules of independence

- Read only the packet. Do not open any other file in the repository: no judge results (`data/ami/results/`, `evals/reports/`), no adjudication or check files (`data/private/E3_*`), no other reader's output, no other packet for the same meeting.
- Decide each item before you see any other verdict on it. There is none in the packet; do not go looking.
- Judge each item on its own. Do not carry a verdict over from a similar item or from the other product.
- Do not reward plausible content the packet does not support.

## Procedure, for each gold item in packet order

Step 1. Understand the item. Read the gold text and its AMI passage (section 3). Note what is distinctive about it: the decision's value, the task and its owner, the issue raised. Note the item's key words and their likely synonyms or paraphrases (for example "slides" / "presentation" / "deck"; "Industrial Designer" / "ID" / "Christine" if the roster says so).

Step 2. Retrieval, before any judgement. Search section 4 for every key word and synonym. List every candidate bullet, not just the first hit. Read each candidate with its section heading, its parent bullet if it is a sub-point, and the bullets right before and after it. Content may be spread over several bullets.

Step 3. Search section 5 the same way. Because lines are fragments, read 2-3 lines either side of each hit. Find where the product heard the item, if it did.

Step 4. Decide the notes verdict (`full`, `partial` or `no`) with the rules below.

Step 5. Decide the transcript verdict (`yes`, `partly` or `no`) with the rules below.

Step 6. Write one JSON line (schema below). Then move to the next item.

## Notes verdict

- `full`: the notes state the same decision, task or problem, with no wrong value, owner or outcome. Wording and detail level do not matter. The content may be spread over several bullets.
- `partial`: the notes state part of it: the task without its owner or without its distinctive content; one of two things decided; the topic but not what was settled. Or they state it more weakly than it happened: as discussed or proposed when it was decided.
- `no`: no bullet states any of it.

Being in the same area is not enough. A bullet about ordering samples does not state an item about testing them. A bullet about a different task by the same person does not state that person's gold task. Different wording for the same thing does state it.

A bullet that gets the value, owner or outcome wrong does not cover the item. If that is the only bullet, the verdict is `no`, not `partial`. This includes a bullet that states the alternative the gold rejected ("X rather than Y" in the gold; the notes say Y): that is the opposite outcome, not a weaker statement of it.

### Decisions

- Owner rules apply only to action items. A decision recorded as a task given to one person (or with a speaker label) still states the decision; ignore the label.
- If the decision's distinctive point is an order ("first", "next step", "before X"), the notes must state or clearly imply that order for `full`. A task merely listed under a "Next steps" heading does not state that it comes first: `partial`.
- Notes that record only the preparation for a decision (deliverables and deadlines for the next meeting) but not the decision itself (what will be discussed or done there) state the topic but not what was settled: `partial`.

### Actions with several parts

An action is `full` if its core deliverable and its owner are present. Secondary clauses ("and discuss X with the team") are detail; a close synonym of a part ("cost" for "budget") counts as that part.

### Owners (action items)

- An action needs its owner. The notes must name the gold owner by name, by role, or by a label that the notes or the transcript let you match to that one person. A name counts exactly as much as a role. Use the roster: close spellings of a name are the same person.
- The notes may define a label themselves ("Speaker 2 is project manager"). Use that definition for every bullet.
- No owner, or a label you cannot match to one person: at most `partial`. An unmatched label is an unnamed owner, not a wrong one.
- A label is matched when the notes themselves tie it to one person elsewhere: another bullet with the same label records something only one person said or presented (check it against the AMI passage and roster). Use that match for every bullet with the label. Do not infer who a label stands for from the content of the task bullet you are scoring.
- A label matched this way to someone other than the gold owner is a wrong owner: that bullet does not cover the item.
- A label the notes attach to things said by different people is unmatched (at most `partial`), never wrong.
- Notes that give the task to the gold owner together with someone else still name the owner.
- If the gold item names no specific person ("the team", "they", "one team member"), there is no owner to check: the task alone can make it `full`.
- A task given to a person you can match to someone other than the gold owner has a wrong owner. That bullet does not cover the item.

### Problems

A gold problem is stated if the notes record the same issue, difficulty, risk or question: as open, as a concern, or together with how it was resolved or what was chosen. It need not be framed as an open question or labelled a problem. "Whether to include X" is stated by "X was ruled out"; "which supplier to use" by "they chose supplier B". It is `partial` if the notes record only part of the issue, and `no` if they record a different issue in the same area.

### Gold that looks wrong

Score against the gold text as written, even if the AMI passage suggests the annotators made a mistake (for example swapped two owners). Say so in `reason` and lower `confidence`. If the gold adds detail its linked passage does not contain, judge the transcript verdict against the passage and the notes verdict against the gold's core outcome. If the gold text has no content (for example `*NA*.`), the verdict is `no` with `confidence` `low`.

## Transcript verdict

Does the product's own transcript (section 5) contain what the gold item says, i.e. did the product hear it?

- `yes`: the content is there, recognisably (misheard filler words do not matter).
- `partly`: only part of it is there, or a key word is misheard so the meaning is unclear.
- `no`: it is not there.

Owners in the transcript: the product's speaker labels are often generic, so judge the content, and use the AMI passage to see who said it. Put the `T` line ids you relied on in `reason`.

## Output

Write one JSON object per line (JSONL), one line per gold item, in packet order, with exactly these fields and no others:

```json
{"meeting": "IS1008a", "product": "granola", "gold_id": "A4", "notes_verdict": "full", "transcript_verdict": "yes", "evidence_bullets": ["B22"], "evidence_quote": "B22 \"Send meeting slides to all attendees (Shreedhar)\"", "reason": "PM (Shreedhar = Shrida Daseri in the roster) sends the slides to everyone; heard at T0259-T0261.", "confidence": "high"}
```

- `meeting`: the meeting id from the packet title. `product`: `granola` or `wispr` (as in the packet).
- `gold_id`: the item id exactly as in section 2.
- `notes_verdict`: `full` | `partial` | `no`.
- `transcript_verdict`: `yes` | `partly` | `no`.
- `evidence_bullets`: the ids of the bullets your verdict relies on, in order. `[]` when `notes_verdict` is `no`. A bullet with a wrong owner or value is not evidence; mention it in `reason` instead (for any item type).
- `evidence_quote`: each evidence bullet's id and its text copied (shorten a long bullet with `...`; straight vs curly quotes do not matter), as `B22 "text"`, separated by `; `. `""` when there are no evidence bullets.
- `reason`: one or two sentences: why this verdict (what is present, what is missing or wrong), plus the `T` ids for the transcript verdict.
- `confidence`: `high` | `medium` | `low`: how sure you are a careful human would give the same notes verdict.

Write the file the caller names (default `data/private/reader/runs/<run_id>/<meeting>__<product>.jsonl`). Write valid JSON only: no comments, no trailing commas, no Markdown around the lines.

## Worked examples (DEV meeting IS1008a only)

These come from the one DEV meeting. Roster: Project Manager = Shrida Daseri; User Interface Designer = Agnes; Industrial Designer = Christine; Marketing Expert = Ed.

Example 1. Owner by name. Gold A4: "The Project Manager will post his presentation to be available for all participants." Granola. Retrieval: search "slides", "presentation", "send", "share", the PM's name. Candidates: B22 "Send meeting slides to all attendees (Shreedhar) — Requested at close of meeting."; B17 "All emails and documents shared across the full team" (no owner, general sharing). B22 is the task; "Shreedhar" is a close spelling of the PM's name. Transcript: T0259-T0261 "could you send us a copy of your slides? ... Yes."

```json
{"meeting": "IS1008a", "product": "granola", "gold_id": "A4", "notes_verdict": "full", "transcript_verdict": "yes", "evidence_bullets": ["B22"], "evidence_quote": "B22 \"Send meeting slides to all attendees (Shreedhar) — Requested at close of meeting.\"", "reason": "Same task with the PM named (Shreedhar = Shrida Daseri); heard at T0259-T0261.", "confidence": "high"}
```

Example 2. Owner by a label the notes define. Same gold A4, Wispr Flow. B15 "(Speaker 2) Send the team a copy of the meeting slides". "Speaker 2" alone would be unmatched, but B2 says "Speaker 2 is project manager", so the owner is named. Transcript: T0126-T0128.

```json
{"meeting": "IS1008a", "product": "wispr", "gold_id": "A4", "notes_verdict": "full", "transcript_verdict": "yes", "evidence_bullets": ["B15"], "evidence_quote": "B15 \"(Speaker 2) Send the team a copy of the meeting slides\"", "reason": "B2 defines Speaker 2 as the project manager, so the PM owns the slides task; heard at T0126-T0128.", "confidence": "high"}
```

Example 3. Unmatched label. Gold A2: "The Industrial Designer will prepare an initial functional design." Wispr Flow. Retrieval: search "functional design", "design plan", "written plan", "Christine", "industrial designer". Candidates: B11 "(Speaker 1) Deliver written plan (functional/technical design, staffing, time frame, budget, market approach) within two weeks"; B14 "(Speaker 1) Start the functional design, ..."; B8 (what the plan should cover, no owner). The task is there, but nothing in the notes defines "Speaker 1", and the transcript labels speakers only "Them" and "Ed", so the label cannot be matched to one person: unnamed owner, at most partial. Transcript: T0064-T0070 (the PM asks for the functional and technical design in writing; "two weeks").

```json
{"meeting": "IS1008a", "product": "wispr", "gold_id": "A2", "notes_verdict": "partial", "transcript_verdict": "yes", "evidence_bullets": ["B11", "B14"], "evidence_quote": "B11 \"(Speaker 1) Deliver written plan (functional/technical design, staffing, time frame, budget, market approach) within two weeks\"; B14 \"(Speaker 1) Start the functional design, covering user acceptance and how it will work in the market\"", "reason": "Task stated but owner is 'Speaker 1', which neither the notes nor the transcript match to one person; heard at T0064-T0070.", "confidence": "high"}
```

Example 4. Owner by name, and what a wrong owner would do. Same gold A2, Granola. B20 "Submit written functional and technical design plan (Christine) — Include team size needed and timeline; target: two weeks (by 19th November)." Christine is the Industrial Designer: `full`. Transcript: T0121-T0126 and T0140-T0143. Had B20 said "(Agnes)" instead, the task would sit with the User Interface Designer, a wrong owner: B20 would not be evidence, and with no other bullet the verdict would be `no`, not `partial`.

```json
{"meeting": "IS1008a", "product": "granola", "gold_id": "A2", "notes_verdict": "full", "transcript_verdict": "yes", "evidence_bullets": ["B20"], "evidence_quote": "B20 \"Submit written functional and technical design plan (Christine) — Include team size needed and timeline; target: two weeks (by 19th November).\"", "reason": "Christine (Industrial Designer) is given the functional design plan; heard at T0121-T0126 and T0140-T0143.", "confidence": "high"}
```

Example 5. A problem recorded as what happened, not as a question. Gold P1: "The group did not receive sufficient information about the project in advance to be prepared to create a project plan in the first meeting." Granola. Retrieval: search "information", "advance", "prepared", "plan", "clarified", "monitor", "coffee". Candidates: B9 "Product direction only clarified during the meeting" with sub-point B10 "Christine had assumed a 7-inch monitor; Ed had heard coffee"; B19 (the PM's project plan, a different item). B9-B10 record the issue (the team came in without knowing what the project was). The gold's "to be prepared to create a project plan" is the consequence, a detail, not a second issue. Transcript: T0090-T0097 ("I actually didn't know we were designing a remote control").

```json
{"meeting": "IS1008a", "product": "granola", "gold_id": "P1", "notes_verdict": "full", "transcript_verdict": "yes", "evidence_bullets": ["B9", "B10"], "evidence_quote": "B9 \"Product direction only clarified during the meeting\"; B10 \"Christine had assumed a 7-inch monitor; Ed had heard coffee\"", "reason": "Notes record that the team lacked information about the project beforehand; the planning consequence is detail; heard at T0090-T0097.", "confidence": "medium"}
```

## Checks before you finish

- One line per gold item in section 2, none missing, none extra, ids exact.
- `evidence_bullets` is `[]` exactly when `notes_verdict` is `no`; every id in it exists in section 4.
- Every action marked `full` names the gold owner (or the gold names no one).
- No verdict rests on a bullet with a wrong owner or value.
