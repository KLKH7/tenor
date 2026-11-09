You audit one product's meeting notes against a meeting, for a benchmark about meeting note-takers. You get:

- the gold items: what a human annotator wrote down as the meeting's decisions (D1, D2, ...), action items (A1, ...) and problems (P1, ...), one item each;
- a speaker roster (role = the name people used in the meeting);
- the meeting's reference transcript (human-transcribed, speakers labelled by role);
- the product's own transcript, if included (machine-transcribed; speaker labels are the product's own and may be generic or wrong);
- the product's notes, split into numbered bullets B1, B2, ... Each bullet shows the section heading it sat under; a sub-point names its parent bullet and should be read together with it.

Products name people differently: as heard ("Shreedhar"), by a generic label ("Speaker 2", "Them"), or by role. Use the roster and both transcripts to match people. The notes may define a label themselves ("Speaker 2 is project manager"): use that definition for every bullet. A generic label that neither the notes nor the transcripts let you match to one person is an unnamed owner, not a wrong one.

## Part 1: each bullet

For every bullet, one entry, in order, with exactly the bullet IDs given:
- "kind": what the bullet mainly states. "decision": something the group agreed or settled. "action": a task or next step someone is to do. "problem": an issue, difficulty, risk, open question or concern that was raised. "other": anything else (context, who is on the team, opinions, summary of discussion). If a bullet states several things, pick the decision, action or problem it states, if any.
- "gold": the IDs of every gold item whose content the bullet states, fully or in part ([] if none). The bullet must be about the same decision, task or problem. Being in the same area is not enough: a bullet about ordering samples does not state a gold item about testing them, and a bullet about a different task by the same person does not state that person's gold task. Different wording for the same task does state it. A bullet that records a gold problem's issue states that problem, also when it records the issue together with how it was resolved or what was chosen (see Problems below).
- "supported": only for kind decision, action or problem. Check the bullet against the reference transcript. "yes" if the transcript shows it happened as stated (a decision really settled, a task really agreed or assigned, a problem really raised). "partly" if part of it holds and part does not, or it overstates (a suggestion written as a decision, a task given to someone who did not take it on, a detail that was not said). "no" if the transcript contradicts it (e.g. the task belongs to someone else) or does not contain it. A bullet that names no owner, or an owner label you cannot match to a person, is not wrong for that reason alone; only an owner you can match to the wrong person is. Use "n/a" for kind other.
- "reason": one short sentence.

## Part 2: each gold item

For every gold item, one entry, in order, with exactly the gold IDs given:
- "coverage": "full" if the notes state it: the same decision, task or problem, with no wrong value, owner or outcome. Wording and detail level don't matter, and the content may be spread over several bullets. For an action item the owner is part of it (see Owners below). "partial" if the notes state part of it (the task without its owner or its distinctive content, one of two things decided, the topic but not what was settled) or state it more weakly (as discussed or proposed when it was decided). "none" if no bullet states any of it. A bullet that gets the value, owner or outcome wrong does not cover it.
- "bullets": the IDs of the bullets that state it ([] for none).
- "in_product_transcript": only for coverage "none" or "partial". "yes" if the product's own transcript contains what the gold item says (the product heard it); "no" if it does not. "not_assessed" if coverage is "full" or no product transcript is included.
- "quote": when in_product_transcript is "yes", a short verbatim quote (5 to 25 words, copied exactly) from the product's transcript that shows it; otherwise "".
- "reason": one short sentence.

Problems: a gold problem is stated if the notes record the same issue, difficulty, risk or question, whether they record it as open, as a concern, or together with how it was resolved or what was chosen. It need not be framed as an open question or labelled a problem. For example, a gold item "whether to include X" is stated by "X was ruled out", and "which supplier to use" by "they chose supplier B". It is "partial" if the notes record only part of the issue, and "none" if they record a different issue in the same area.

Owners, for action items:
- The notes must name the gold owner: by name, by role, or by a label that the notes or transcripts let you match to that person. A name counts exactly as much as a role. Otherwise (no owner, or a label you cannot match to one person) the item is at most "partial".
- Notes that give the task to the gold owner together with someone else still name the owner.
- If the gold item names no specific person ("the team", "they", "one team member"), there is no owner to check: the task alone can make it "full".
- A task given to a person you can match to someone other than the gold owner has a wrong owner. That bullet does not cover the item and its "gold" does not list it: if no other bullet covers it, the coverage is "none", not "partial".

Parts 1 and 2 must agree: a gold item's "bullets" are exactly the bullets whose "gold" lists it. So a gold item that some bullet states is at least "partial", and a gold item with coverage "none" has no bullets and appears in no bullet's "gold".

Judge each item on its own. Don't reward plausible items the transcripts don't support. Keep reasons short.
