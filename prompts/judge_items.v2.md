You score a list answer item by item in a benchmark about meeting notes. You get the meeting's reference transcript (human-transcribed, speakers labelled by role), then a question, the gold items (written by a human annotator from the meeting), a speaker roster, and a system's answer.

Step 1, the answer's items:
- If the answer is given as numbered items (A1, A2, ...), use exactly those items and IDs, one entry each, in order. Copy each item's text.
- If the answer is free text, split it into its distinct items (one decision, action item or problem each), number them A1, A2, ... and copy each item's text. Leave out framing that is not an item ("Here are the decisions:"). An answer that says it cannot answer has no items.

Step 2, for each answer item:
- "gold": the IDs of every gold item it states. It states a gold item when it gives the same decision, action item or problem; wording, detail level and paraphrase don't matter, but a different value, owner or outcome does. An item can state several gold items; several items can state the same gold item. Use [] if it states none.
- "in_transcript": true if the reference transcript shows the item is a correct answer to the question (it happened as stated, it is the kind of thing asked for, and it concerns the person or topic asked about), whether or not it is in the gold. False if the transcript contradicts it, does not contain it, or it does not answer the question. People are named differently by products; use the roster to match names and roles.
- "reason": one short sentence.

Judge each item on its own. Don't reward plausible items that the transcript doesn't support.
