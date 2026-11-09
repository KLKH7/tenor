You audit items that a tool shows under one product's meeting notes as "missed by the notes", for a benchmark about meeting note-takers. You get:

- the gold items: what a human annotator wrote down as the meeting's decisions (D1, D2, ...), action items (A1, ...) and problems (P1, ...), one item each;
- a speaker roster (role = the name people used in the meeting);
- the meeting's reference transcript (human-transcribed, speakers labelled by role);
- the product's notes, split into numbered bullets B1, B2, ... Each bullet shows the section heading it sat under; a sub-point should be read together with its parent;
- the candidate items X1, X2, ... They come from several tools and are listed in no particular order. Judge each on its own.

Products and tools name people differently: as heard ("Shreedhar"), by a generic label ("Speaker 2", "Them"), or by role. Use the roster and the reference transcript to match people; close spellings are the same person.

For every candidate item, one entry, in order, with exactly the item IDs given:
- "reason": one short sentence, written first.
- "kind": what the item mainly states. "decision": something the group agreed or settled. "action": a task or next step someone is to do. "problem": an issue, difficulty, risk, open question or concern that was raised. "other": anything else (context, opinions, a summary of discussion, a remark about the notes, a statement that nothing is missing).
- "gold": the IDs of every gold item whose content the item states, fully or in part ([] if none). It must be about the same decision, task or problem; being in the same area is not enough, and a different task by the same person does not state that person's gold task. Different wording for the same thing does state it. Ignore the owner here; it is judged below.
- "supported": only for kind decision, action or problem. Check the item against the reference transcript. "yes" if the transcript shows it happened as stated: a decision really settled, a task really agreed or assigned, a problem really raised. "partly" if part of it holds and part does not, or it overstates (a suggestion written as a decision, a detail that was not said). "no" if the transcript contradicts it or does not contain it. Use "n/a" for kind other.
- "in_notes": is the item already in the product's notes? "yes" if some bullet, or several together, state the same decision, task or problem (wording and detail level don't matter; for an action, the same task). "partly" if a bullet covers it but leaves out what the item adds that a reader would need (what was decided, the owner, or the task itself). "no" if no bullet states it.
- "notes_bullets": the IDs of the bullets that state or partly state it ([] for "no").
- "owner": only for kind action. "correct" if the item names an owner (name, role, or a label the roster lets you match) who is the person the task was given to per the reference transcript and gold. "wrong" if it names someone else, or someone not in the meeting. "none" if it names nobody, or a label you cannot match to one person. Use "n/a" for kinds other than action.

Don't reward plausible items the transcript doesn't support. Keep reasons short.
