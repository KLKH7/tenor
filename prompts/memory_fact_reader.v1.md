# Does the answer state this fact? (v1)

You read answers to questions about meetings. Each item gives a question, its full gold answer,
one fact taken from that gold answer, and an answer someone wrote. Say whether the answer
states that one fact.

- `yes`: the answer states the fact, in any words. A paraphrase counts; so does a more specific
  or a slightly shorter version that keeps what the fact says (who, what, and any part the fact
  names). A role named differently but unmistakably the same person counts.
- `no`: the answer leaves the fact out, states something different or contrary, gives it to the
  wrong person, or only hints at it. An abstention is `no`.

Judge only this one fact, not the whole gold answer, and not whether the rest of the answer is
right. You are not told how the answer was produced and should not guess it.

## Output

JSONL, one line per item, in the order listed, nothing else, to the answer file named in the job:

```
{"id": "F001", "states": "yes", "reason": "one short line"}
```

## Changelog

- v1 (2026-11-17): first version, for calibrating the ROUGE-L retained threshold on the IS1008
  development answers (memory breakdown).
