You are a reader for E4 (coverage head-to-head) of the TENOR benchmark. For one meeting and one note-taking product, you get a list of candidate items: things that might be missing from the product's notes. For every candidate you decide whether it is true, whether the notes already have it, and which gold items it states. You work from one packet file and nothing else.

## What you get

One packet, `data/private/reader/e4_packets/<meeting>__<product>.md` (built by `scripts/e4_reader_packets.py`). It has six sections:

1. Meeting background: the AMI speakers and their roles, and a roster (role = the name people used in the meeting).
2. Gold items: what AMI's human annotators wrote down as the meeting's decisions (D1, D2, ...), action items (A1, ...) and problems (P1, ...).
3. For each gold item, the passage of AMI's human reference transcript that the annotators linked to it (`L` line ids). `>>` marks a linked turn. If items have no links, the full AMI transcript is given once (section 3b).
4. The product's notes, split into bullets `B1, B2, ...`. Each bullet shows its section heading; a sub-point names its parent and is read together with it.
5. The product's own transcript (`T` line ids). Machine-made: words may be misheard, speaker labels may be generic ("Them", "Speaker 1") or wrong, and one sentence may be broken over several lines.
6. Candidate items `K1, K2, ...`, in random order. They come from several methods mixed together. Nothing tells you which method produced an item, and it must not matter.

## Rules of independence

- Read only the packet. Do not open any other file in the repository: no key files (`data/private/reader/e4_keys/`), no candidate or result files (`data/ami/results/`, `evals/reports/`), no other reader's output, no other packet for the same meeting, no E3 check or adjudication files.
- Do not try to guess which method produced an item. Score the item's text as written.
- Judge each candidate on its own. If two candidates say nearly the same thing, give each its own verdict; they may well get the same one.
- Do not reward plausible content the packet does not support. An item is not true because it sounds like something a meeting would decide.

## Procedure, for each candidate in packet order

Step 1. Understand the item. What does it claim: a decision (and its value), a task (and its owner, deadline, deliverable), a problem or open issue, or something else? Split an item with several parts ("A, B and C are undecided") into its parts. Note key words and likely synonyms, and the people it names (use the roster: close spellings are the same person; a role and a name can be the same person).

Step 2. Find it in the transcripts. Search section 5 for every key word and synonym; read 2-3 lines either side of each hit. If it touches a gold item, read that item's AMI passage in section 3 too: it has real speaker roles. Work out who said what from context (who is addressed by name, who answers).

Step 3. Decide `kind` and `true` (rules below).

Step 4. Search section 4 for every key word and synonym. List every candidate bullet, with its heading, parent, and neighbours. Decide `in_notes` (rules below).

Step 5. Compare the item with section 2. Decide `gold_match` (rules below).

Step 6. Write one JSON line (schema below). Then move to the next candidate.

## kind

What the item is, as it happened in the meeting (not just how it is phrased):

- `decision`: the group agreed or settled something (a choice, a value, a plan, an order of work).
- `action`: someone was given, or took on, a task.
- `problem`: an issue, difficulty, risk, constraint or open question was raised.
- `other`: anything else: background, who attended, roles, the agenda or meeting length, a summary of a discussion with nothing settled, an offer of help ("let me know if you need anything"), a restatement of the project goal.

A decision recorded as a task given to one person is still a decision if that is what happened; pick the kind that best fits the item's main claim.

## true

Did it happen as the item says? Use section 5 (everything the product heard) and section 3 (the human transcript, where it covers the moment).

- `yes`: the transcripts show it was decided / assigned / raised as stated. Every part is supported. Any owner, value, deadline or outcome it names is right. Wording does not matter.
- `partly`: it happened, but more weakly than stated (proposed or discussed, not decided; an offer, not an assignment); or only some parts of a multi-part item are supported; or the owner it names cannot be confirmed from the packet (not contradicted, just not shown); or a detail is added that nothing supports.
- `no`: it did not happen: nothing in the packet supports it; or the packet contradicts it (wrong owner, wrong value or deadline, the opposite outcome, the rejected alternative).

For `kind` `other`, `true` is simply whether the statement is accurate.

## in_notes

Do the notes (section 4) already state this item? The question a user would ask: does this item tell me anything my notes do not?

- `yes`: the notes state the same decision, task or problem, with no wrong value, owner or outcome. Wording and detail level do not matter: an item that only elaborates (adds an example, rephrases, repeats the reason) is `yes`. The content may be spread over several bullets.
- `partial`: the notes state part of it, but miss its distinctive point: an owner, a value, a deadline, an order ("first", "before X"), or one of its parts. Or they state it more weakly (as discussed when the item says decided).
- `no`: no bullet states any of it.

Being in the same area is not enough. A bullet about a different task by the same person does not state this task. A bullet about the PM's general availability does not state a specific task.

A bullet that disagrees with the item on owner, value or outcome does not state it. If that is the only bullet, the verdict is `no`. This holds whichever side is right: if the item is right and the notes are wrong, the item is new information; if the item is wrong, `true` is `no` and `in_notes` is `no`.

For actions, the notes must give the same owner (by name, role, or a label the notes define, as in E3's rules). A notes bullet with the task but no owner, when the item names one, is `partial`.

For a multi-part item, `yes` only if every part is in the notes; `partial` if some are.

`notes_bullets`: the bullets your verdict relies on; `[]` exactly when `in_notes` is `no`. A bullet that disagrees with the item is not evidence; mention it in `reason`.

## gold_match

The gold items (section 2) this candidate states, each `full` or `partial`, judged as you would judge a notes bullet against gold:

- `full`: the candidate states the same decision, task or problem with nothing wrong. For an action gold item it must name the gold owner (by name or role; use the roster), unless the gold names no specific person.
- `partial`: it states part of the gold item: the task without its owner, one of two things decided, the topic but not what was settled, or more weakly than it happened.
- A candidate in the same area as a gold item but about a different thing does not state it. A candidate with a wrong owner or value does not state it.
- `[]` when it states none. A candidate can state several gold items.

Score against the gold text as written; if the gold looks wrong (its passage says otherwise), say so in `reason`.

## Output

Write one JSON object per line (JSONL), one line per candidate, in packet order (K1, K2, ...), with exactly these fields and no others:

```json
{"meeting": "IS1008a", "product": "granola", "cand_id": "K5", "kind": "action", "true": "yes", "in_notes": "no", "notes_bullets": [], "gold_match": [], "evidence_quote": "T0153 \"So who are the people you need, from the marketing or, the technical side or\"; T0158 \"I'll get back to you on that.\"", "reason": "The PM asks Christine (the plan exchange from T0120) which people she needs and she will get back to him; B6 (team size depends on budget) and B18 (PM available) are different points.", "confidence": "medium"}
```

- `meeting`: the meeting id from the packet title. `product`: `granola` or `wispr` (as in the packet).
- `cand_id`: the candidate's id exactly as in section 6.
- `kind`: `decision` | `action` | `problem` | `other`.
- `true`: `yes` | `partly` | `no`.
- `in_notes`: `yes` | `partial` | `no`; `notes_bullets`: bullet ids, `[]` exactly when `in_notes` is `no`.
- `gold_match`: a list of `{"id": "A2", "match": "full"}` or `"partial"`; `[]` for none.
- `evidence_quote`: the lines you relied on, each as `T0153 "text"` (or `L0229 "text"`, or `B20 "text"`), shortened with `...` if long, separated by `; `. At least one `T` or `L` line unless `true` is `no` because nothing supports the item (then `""`).
- `reason`: one or two sentences: why these verdicts (what is supported, what the notes have or miss, any wrong owner or value).
- `confidence`: `high` | `medium` | `low`: how sure you are a careful human would give the same `true` and `in_notes` verdicts.

Write the file the caller names (default `data/private/reader/runs/e4_<run_id>/<meeting>__<product>.jsonl`). Write valid JSON only: no comments, no trailing commas, no Markdown around the lines.

## Worked examples (DEV meeting IS1008a, Granola, only)

Roster: Project Manager = Shrida Daseri (Granola spells "Shreedhar"); User Interface Designer = Agnes; Industrial Designer = Christine; Marketing Expert = Ed. The K ids here are illustrative. Relevant notes bullets:

- B6 [Team Roles and Gaps] Christine: industrial design lead; team size depends on budget allocated
- B18 [Coordination Plan] Shreedhar available by call, email, or in person for any coordination needs
- B19 [Next Steps] Develop initial project plan (Shreedhar) — Needed to present scope, cost, and timeline to management; target: next meeting.
- B20 [Next Steps] Submit written functional and technical design plan (Christine) — Include team size needed and timeline; target: two weeks (by 19th November).
- B21 [Next Steps] Develop sales and marketing strategy plan (Ed) — Coordinate with Christine on design direction and cost; research competitor remote controls.

Example 1. A true new omission (action). "Christine to tell the project manager which people she needs from marketing, technical or administration to help with the plan." Transcript: T0151 "Are there other people who will be contributing to the plan?", T0153-T0154 "who are the people you need, from the marketing or, the technical side or the administration point of view?", T0157-T0158 "just let me know so I can coordinate all the teams. Okay. I'll get back to you on that." From T0120 the PM is asking the Industrial Designer for her written plan, so the person who will get back is Christine. Notes: B6 is about team size and budget, B18 about the PM's availability: neither states this task. No gold item is about it (A2 is the functional design itself).

```json
{"meeting": "IS1008a", "product": "granola", "cand_id": "K5", "kind": "action", "true": "yes", "in_notes": "no", "notes_bullets": [], "gold_match": [], "evidence_quote": "T0153 \"So who are the people you need, from the marketing or, the technical side or\"; T0157 \"Some technical point of view. So just let me know so I can\"; T0158 \"coordinate all the teams. Okay. I'll get back to you on that.\"", "reason": "The PM asks Christine (the plan exchange from T0120) which people she needs and she says she will get back to him; B6 (team size depends on budget) and B18 (PM's availability) are different points.", "confidence": "medium"}
```

Example 2. A duplicate. "Christine (industrial designer) to deliver a written plan with functional/technical design, staffing, timeframe and budget within two weeks." B20 states the same task, owner and deadline: `yes`. It also states gold A2 (Industrial Designer prepares an initial functional design) with the right owner. Had the item said "within one week", the transcript (T0142 "two weeks is is close enough?" / T0143 "Yes.") would contradict it: `true` `no`, B20 would disagree with it on the deadline so `in_notes` `no`, and it would not state A2.

```json
{"meeting": "IS1008a", "product": "granola", "cand_id": "K12", "kind": "action", "true": "yes", "in_notes": "yes", "notes_bullets": ["B20"], "gold_match": [{"id": "A2", "match": "full"}], "evidence_quote": "T0126 \"what's your functional design? What's your technical design?\"; T0142 \"two weeks is is close enough?\"; T0143 \"Yes. I think that will be good...\"; B20 \"Submit written functional and technical design plan (Christine) — ... target: two weeks (by 19th November).\"", "reason": "B20 has the same written plan, owner (Christine) and two-week deadline; heard at T0120-T0143.", "confidence": "high"}
```

Example 3. True, and the notes give it to the wrong person. "Ed (marketing/accounting) to provide a project plan by the next meeting." Transcript: T0031 "Eid about your marketing plan", then T0044-T0045 "Do you have some project plan?", T0051-T0053 "by when you think you can give me some kind of, project plan? ... Certainly by the next meeting." The PM is talking to Ed. B19 has the project plan by the next meeting but gives it to Shreedhar: it disagrees on the owner, so it does not state the item. B21 (Ed's sales and marketing plan) is a different deliverable. The item is new information: `in_notes` `no`. It does not state gold A1 (a sales plan, a later moment).

```json
{"meeting": "IS1008a", "product": "granola", "cand_id": "K3", "kind": "action", "true": "yes", "in_notes": "no", "notes_bullets": [], "gold_match": [], "evidence_quote": "T0031 \"Eid about your marketing plan and your\"; T0052 \"give me some kind of, project plan?\"; T0053 \"Okay, discussion with Certainly by the next meeting.\"", "reason": "The PM asks Ed for a project plan and he promises it by the next meeting; B19 gives the project plan to Shreedhar (wrong owner), B21 is Ed's sales plan, a different deliverable.", "confidence": "medium"}
```

Example 4. Partly in the notes: the order is missing. "Immediate next step: come up with the functional design and discuss it with Ed, including user acceptance, before the next meeting." Transcript T0244 "the immediate next step is to start determining the functional design" and T0248 "So come up with the functional design and discuss with"; AMI L0226-L0229 (D2's passage). It is a decision about what comes first. B20 lists the design plan under Next Steps and B21 has Ed coordinating with Christine, but nothing says the design comes first: `partial`. It states D2 fully (the order is its point) and A2 only partly (no owner named).

```json
{"meeting": "IS1008a", "product": "granola", "cand_id": "K9", "kind": "decision", "true": "yes", "in_notes": "partial", "notes_bullets": ["B20", "B21"], "gold_match": [{"id": "D2", "match": "full"}, {"id": "A2", "match": "partial"}], "evidence_quote": "T0244 \"the immediate next step is to start determining the functional design Yes.\"; L0229 \"To come up with the functional design and uh to discuss with Ed...\"; B20 \"Submit written functional and technical design plan (Christine)...\"", "reason": "Agreed as the immediate next step (T0244, L0226-L0229); B20/B21 have the design plan and the coordination with Ed but not that it comes first.", "confidence": "medium"}
```

Example 5. An offer, not a task. "Project manager offered to coordinate with technical or commercial vendors if needed." Transcript T0205-T0209 "if you need any coordination in between ... Some technical vendors or commercial vendors ... So you let me know. ... I can coordinate." Accurate, but it is a conditional offer, not an assigned task or decision: `kind` `other`. B18 states the PM's availability for coordination in general: `partial`.

```json
{"meeting": "IS1008a", "product": "granola", "cand_id": "K14", "kind": "other", "true": "yes", "in_notes": "partial", "notes_bullets": ["B18"], "gold_match": [], "evidence_quote": "T0206 \"any coordination in between compared to the... Maybe the... Some technical vendors or\"; T0209 \"Okay. I can coordinate.\"", "reason": "An offer of help if asked, not an assignment; B18 has the PM available for coordination in general but not the vendors.", "confidence": "high"}
```

Example 6. A new problem. "Christine's availability is limited by other projects; design could take one to six months." Transcript T0122-T0125 "I've got a lot of other projects ... it could be a month ... it might be six months". A risk raised: `problem`. B6 (team size depends on budget) and B20 (two-week target for the written plan) are different points. Not gold P1 (P1 is about information before the meeting).

```json
{"meeting": "IS1008a", "product": "granola", "cand_id": "K1", "kind": "problem", "true": "yes", "in_notes": "no", "notes_bullets": [], "gold_match": [], "evidence_quote": "T0122 \"Well, I've got a lot of other projects I'm doing right now.\"; T0124 \"go quickly, then it could be a month if\"; T0125 \"if I run into any problems on my other projects, it might be six months...\"", "reason": "Christine raises that her other projects make the timing one to six months; the notes have only the two-week target for the written plan (B20) and team size vs budget (B6).", "confidence": "high"}
```

Example 7. A multi-part item. "Open problems: No price/cost target, initial budget, required design-team size, delivery timeline, target market, competitive differentiation, or expected company benefit had been determined." Split it and check each part. The PM asks for team size, time frame and initial budget (T0127-T0128) and when the product will be introduced (T0132); cost, how to compete and the benefit to the company are left to Ed and Christine (T0186-T0194, AMI L0178). Every part is supported: `true` `yes`. Price/cost (B13 "Pricing and cost limit not yet set") and team size vs budget (B6) are in the notes; the timeline, market, competition and company benefit are not: `partial`. P1 is about information missing before the meeting; this item lists what was still open after it: same area, different issue, so no gold match.

```json
{"meeting": "IS1008a", "product": "granola", "cand_id": "K20", "kind": "problem", "true": "yes", "in_notes": "partial", "notes_bullets": ["B13", "B6"], "gold_match": [], "evidence_quote": "T0127 \"And how many people you need for this project? And what's the time\"; T0128 \"frame you are looking, okay, and what is the budget, maybe initial budget you are looking.\"; L0178 \"...come up with the cost, and how we are going to compete in the market... how much we are going to benefit for the company\"; B13 \"Pricing and cost limit not yet set; Christine and Ed to decide together\"", "reason": "All parts were open (T0127-T0132, L0178); price/cost and team size are in B13/B6, the timeline, market, competition and company benefit are not.", "confidence": "medium"}
```

## Checks before you finish

- One line per candidate in section 6, none missing, none extra, ids exact, in order.
- `notes_bullets` is `[]` exactly when `in_notes` is `no`; every id in it exists in section 4.
- Every `gold_match` id exists in section 2; an action gold item marked `full` has its gold owner in the candidate.
- No `in_notes` verdict rests on a bullet that disagrees with the item on owner, value or outcome.
- Every `true` `yes` has transcript evidence in `evidence_quote`.
