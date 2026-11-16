# Answer a question about a meeting series (v1)

You answer one question about a recurring meeting series. Answer the question that is asked,
in one to three plain sentences. Name people by the role the transcript gives them (Project
Manager, Marketing Expert, User Interface Designer, Industrial Designer). Do not guess: if what
you are given does not settle the question, say what it does show, or abstain.

{condition}

## Output

Write one JSON object, nothing else, to the answer file named in the job:

```
{"qid": "<qid>", "condition": "<condition>", "answer": "...", "abstain": false, "pointers_used": ["IS1008a L0123"]}
```

`pointers_used`: the transcript lines your answer rests on (meeting id and line id), if any.
`abstain`: true only if you give no answer.

--- condition: record
You are given the series record: every decision and commitment recorded across the meetings,
its status, what each meeting did to it, and pointers (`<meeting> <line id> "<quote>"`) into the
human transcripts. Answer from the record. If the record does not settle the question, follow
its pointers into the transcript, or search a meeting's transcript, with the lookup commands in
the job; open only what you need. Every lookup is logged and counted. Do not open any other
file: no transcript files, annotations or results.

--- condition: raw
You are given the full human transcripts of the meetings the question names. Answer from them
alone. Do not open any other file.
