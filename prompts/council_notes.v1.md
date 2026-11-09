You audit one product's meeting notes against the official record of a city council agenda item, for a benchmark about meeting note-takers. You get:

- the gold items: what the city's official record (Legistar) says about this agenda item, one claim each: G1 the item (its number and title), G2 the outcome (passed, passed as amended, adopted, postponed, ...), G3 the vote (the tally and how each named councilmember voted);
- the product's own transcript, if included (machine-transcribed from a recording of this agenda item; speaker labels are the product's own and may be generic or wrong; names may be misspelled);
- the product's notes, split into numbered bullets B1, B2, ... Each bullet shows the section heading it sat under; a sub-point names its parent bullet and should be read together with it.

Products name people differently: in full, by surname, misspelled as heard ("Council Member Reveera"), or by a generic label ("Speaker 2", "Them"). A misspelling or a surname alone that clearly points to one councilmember on the record is that councilmember. The recording covers this agenda item only, with a little of the item before and after; ignore what the notes say about other items.

## Part 1: bullets about the outcome or the vote

List every bullet that states anything about this item's outcome or vote: whether it passed, was adopted, amended, postponed or failed; a tally or count; who voted for or against; that it was unanimous. Skip bullets that only describe the discussion, the content of the item, or other items. One entry per such bullet, in order, with exactly the bullet IDs given:
- "gold": the IDs of the gold items whose content the bullet states, fully or in part ([] if none).
- "contradicts": "yes" if what the bullet says about the outcome or the vote contradicts the official record: a wrong outcome (passed when it was postponed, adopted without the amendment when it was amended), a wrong tally or count, a councilmember on the wrong side, unanimous when it was not, or not unanimous when it was. "no" otherwise. Leaving something out is not a contradiction, and neither is a misspelled name that still points to the right person.
- "reason": one short sentence.

## Part 2: each gold item

For every gold item, one entry, in order, with exactly the gold IDs given:
- "coverage":
  - G1 (the item): "full" if the notes identify this agenda item: its number (e.g. "CB 121282", "Resolution 32202"), or its subject stated specifically enough to tell it from other items. "partial" if they name only the general topic. "none" if not.
  - G2 (the outcome): "full" if the notes state the outcome as recorded, with its qualifier when it has one (as amended; postponed to a date: the postponement counts, the date is not required). "partial" if they state part of it (passed but not that it was amended; that a vote was taken or a motion made, without the result) or state it more weakly (expected to pass, likely). "none" if they say nothing of the outcome.
  - G3 (the vote): "full" if the notes give the tally (counts, or "unanimous" / "all in favor" for a vote with no one opposed) and, for a split vote, name the councilmembers on the losing side. "partial" if they give only part (the counts without the names on a split vote; some names; "passed by a vote" without numbers). "none" if not.
  A bullet that gets the outcome or the tally wrong does not cover that gold item; the content may be spread over several bullets.
- "bullets": the IDs of the bullets that state it ([] for none).
- "in_product_transcript": only for coverage "none" or "partial". "yes" if the product's own transcript contains what the gold item says (the product heard it: the item named, the result announced, the roll call); "no" if it does not. "not_assessed" if coverage is "full" or no product transcript is included.
- "quote": when in_product_transcript is "yes", a short verbatim quote (5 to 25 words, copied exactly) from the product's transcript that shows it; otherwise "".
- "reason": one short sentence.

Parts 1 and 2 must agree on G2 and G3: a bullet listed in Part 1 whose "gold" includes G2 or G3 appears in that gold item's "bullets", and the other way round.

Judge each item on its own. Don't reward outcomes the notes guess at. Keep reasons short.
