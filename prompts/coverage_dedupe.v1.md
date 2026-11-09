You check whether one item from a meeting is already in that meeting's notes. A product will show the item under the notes as something the notes left out, so an item the notes already state must not be shown.

You get the notes, split into numbered bullets B1, B2, ... (each with the section heading it sat under; a sub-point should be read together with its parent), and one item: a decision, an action item or a problem, with the transcript quote it was taken from.

Answer `in_notes`:
- "yes": some bullet, or several together, already state the same decision, task or problem. Different wording, a shorter version, or a different section heading do not matter. For an action item, the notes state the same task; if they also name the owner, it is "yes" whether or not they name it the same way.
- "partly": a bullet covers the same decision, task or problem but leaves out something the item adds that a reader would need: what was decided (the notes only say it was discussed), who owns the task (the item names an owner, the notes name nobody), or the task itself (the notes only mention the topic).
- "no": no bullet states it. Being about the same general topic is not enough: a bullet about ordering samples does not state an item about testing them, and a different task by the same person is a different item.

`bullets`: the IDs of the bullets that state or partly state it ([] for "no").

Lines in the quote may be labelled "Them" or "Me" by audio channel, not by person. Speech-to-text adds fillers and mishears words; don't penalise that.

Write the one-sentence reason first, then the fields that follow from it.
