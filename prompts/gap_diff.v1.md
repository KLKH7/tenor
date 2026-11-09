## Task: gap check (what the notes left out)

Below the notes you see `<candidates>`: bullets R1, R2, ... from a fuller rewrite of these notes, each with the section it was written under. Most of them repeat what the notes already say. The user will be shown only what you return, as a short list of items to add to their notes, so return an item only if it passes every test:

(a) **Kind.** It is a decision (the group settled something, not only discussed it), an action item (someone was asked to, or agreed to, do something), or an open problem (an issue, risk or question raised and left unresolved). Background, context, opinions, agenda and summaries are not.

(b) **New.** The notes do not already state it, **not even partly**. If any bullet, or several together, cover the same decision, task or problem, drop the candidate, even when the candidate adds a detail, an owner, a number or better wording. Being about a different decision, task or problem on the same topic is new. When in doubt, drop it.

(c) **True.** The transcript shows it happened as stated. Copy a `quote` from the transcript that shows it: one span of 6 to 25 words, copied character for character from a single place in the transcript (no ellipses, no joining of separate places, no speaker labels, no fixing of mis-heard words). If you cannot find such a span, drop the candidate.

(d) **Merged.** If two or more candidates say the same thing, return the clearest one and put the ids of the others in `merged_ids`.

Return at most 5 items, most important first (what a participant would most regret not having in the notes). Return fewer, or none, when fewer pass: an empty list is the right answer for complete notes. Use the candidates' ids exactly; never write new items.

For each item write the one-sentence `reason` first (why it passes (a) and (b)), then `id`, `kind` ("decision", "action" or "problem"), `merged_ids` ([] if none) and `quote`.
