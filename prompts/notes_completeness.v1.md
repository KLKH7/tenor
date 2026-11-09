You audit one product's meeting notes against a meeting, for a benchmark about meeting note-takers. You get:

- the gold items: what a human annotator wrote down as the meeting's decisions (D1, D2, ...), action items (A1, ...) and problems (P1, ...), one item each;
- a speaker roster (role = the name people used in the meeting);
- the meeting's reference transcript (human-transcribed, speakers labelled by role);
- the product's own transcript, if included (machine-transcribed; speaker labels are the product's own and may be generic or wrong);
- the product's notes, split into numbered bullets B1, B2, ... Each bullet shows the section heading it sat under; a sub-point names its parent bullet and should be read together with it.

Products name people differently: as heard ("Shreedhar"), by a generic label ("Speaker 2", "Them"), or by role. Use the roster and the reference transcript to match people. A note that gives no owner is not wrong for that reason alone.

## Part 1: each gold item

For every gold item, one entry, in order, with exactly the gold IDs given:
- "coverage": "full" if the notes state it: the same decision, task or problem, with no wrong value, owner or outcome. Wording and detail level don't matter, and the content may be spread over several bullets. "partial" if the notes state part of it (e.g. the task without its distinctive content, one of two things decided, the topic but not what was settled) or state it more weakly (as discussed or proposed when it was decided). "none" if no bullet states it. A bullet that gets the value, owner or outcome wrong does not cover it.
- "bullets": the IDs of the bullets that state it ([] for none).
- "in_product_transcript": only for coverage "none" or "partial". "yes" if the product's own transcript contains what the gold item says (the product heard it); "no" if it does not. "not_assessed" if coverage is "full" or no product transcript is included.
- "quote": when in_product_transcript is "yes", a short verbatim quote (5 to 25 words, copied exactly) from the product's transcript that shows it; otherwise "".
- "reason": one short sentence.

## Part 2: each bullet

For every bullet, one entry, in order, with exactly the bullet IDs given:
- "kind": what the bullet mainly states. "decision": something the group agreed or settled. "action": a task or next step someone is to do. "problem": an issue, difficulty, risk, open question or concern that was raised. "other": anything else (context, who is on the team, opinions, summary of discussion). If a bullet states several things, pick the decision, action or problem it states, if any.
- "gold": the IDs of every gold item the bullet states, fully or in part ([] if none). Keep this consistent with Part 1.
- "supported": only for kind decision, action or problem. Check the bullet against the reference transcript. "yes" if the transcript shows it happened as stated (a decision really settled, a task really agreed or assigned, a problem really raised). "partly" if part of it holds and part does not, or it overstates (a suggestion written as a decision, an owner nobody confirmed, a detail that was not said). "no" if the transcript contradicts it or does not contain it. Use "n/a" for kind other.
- "reason": one short sentence.

Judge each item on its own. Don't reward plausible items the transcripts don't support. Keep reasons short.
