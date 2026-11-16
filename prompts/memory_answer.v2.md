# Answer a question about a meeting series (v2)

You answer one question about a recurring meeting series. Answer the question that is asked,
in one to three plain sentences. Name people by the role the transcript gives them (Project
Manager, Marketing Expert, User Interface Designer, Industrial Designer). Do not guess: if what
you are given does not settle the question, say what it does show, or abstain.

{condition}

## Output

Write one JSON object, nothing else, to the answer file named in the job:

```
{"qid": "<qid>", "condition": "<condition>", "answer": "...", "abstain": false, "pointers_used": ["ES2006a L0123"]}
```

`pointers_used`: the transcript lines your answer rests on (meeting id and line id), if any.
`abstain`: true only if you give no answer.

--- condition: record
You are given the series record: every decision, commitment and open problem recorded across
the meetings, its status, its parts, what each meeting did to it, and pointers
(`<meeting> <line id> "<quote>"`) into the human transcripts. The record is compact: a closed
entry is one line, and the `entry` command prints any entry's full history. Answer from the
record. If the record does not settle the question, use the lookup commands in the job: follow
pointers into the transcripts, open lines, or search. Open only what you need. Every lookup is
logged and counted. Do not open any other file: no transcript files, annotations or results.

--- condition: search
You are given no transcript text, only the list of the series' meetings and the lookup commands
in the job: search the transcripts and open lines. Find what you need with them. Open only what
you need. Every lookup is logged and counted. Do not open any other file: no transcript files,
annotations or results.

--- condition: named
You are given the full human transcripts of the meetings the question is about. Answer from
them alone. Do not open any other file.

## Changelog

- v2: three conditions (record with the compact view and an `entry` command; search over the
  series with no transcript given; the named meetings' transcripts). Wording of v1 otherwise.
- v1: two conditions (record; the named meetings' transcripts).
