# Granola Chat follow-through questions (v1)

The exact questions put to the notes product's own chat (Granola Chat, reached through the
official MCP tool `query_granola_meetings`), worded as a user would type them. Each call is
restricted with `document_ids` to exactly the two notes of one consecutive pair: the earlier
meeting and the later one. Nothing else is added to the question: no meeting titles, no ids, no
instructions about format. Chat is not deterministic, so each question is asked several times.

Fixed on the development pair (AMI IS1008a -> IS1008b) only, then frozen before any test pair
was asked.

## Questions

--- q1
Which next steps or decisions from the earlier meeting were not mentioned or followed up in the later meeting?

--- q2
What did we say we'd do last time that didn't come up today?

## Changelog

- v1: first version. On the development pair Chat answered both questions as written (it
  worked out which meeting was earlier and answered about the pair only), so the wording was
  frozen unchanged.
