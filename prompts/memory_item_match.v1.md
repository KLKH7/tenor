# Match annotated items to record entries (v1)

For one or more meetings you are given two lists made independently from the same transcript:

- the **items** a human annotator wrote down as the meeting's decisions and actions, each with
  the transcript lines the annotator linked to it;
- the **entries** a note-keeper wrote down for the same meeting, each with the lines it cites.

For every item, say which entry, if any, records the same decision or action.

- Same means the same content: the same choice, rule or plan, or the same task for the same
  role. Being about the same topic, or citing nearby lines, is not enough.
- If one entry records several items, give that entry for each of them.
- If an item's content is spread over several entries, give the entry that holds its main part
  as `entry` and list the others in `also`.
- If no entry records the item, give `null`. Do not stretch: a partial overlap on a minor part
  of the item is `null` with the overlap named in `reason`.

You see nothing about what happened to the items later, and you should not guess it.

## Output

JSONL, one line per item, in the order listed, nothing else, to the answer file named in the job:

```
{"meeting": "IS1008b", "item": "D1", "entry": "E016", "also": [], "reason": "one line"}
```
