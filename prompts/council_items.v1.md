# Council follow-up items (extractor, v1)

You are given the transcript of one meeting of a public body (a city council or one of its
committees). List every **follow-up item** the meeting creates: something said in this meeting
that a person following the body would expect to hear about again at a later meeting.

## What counts

Exactly one `kind` per item:

- `commitment`: a named person or office says they will do something after this meeting
  ("I will circulate the amendment", "central staff will draft that language").
- `info_request`: a member asks staff, a department or a colleague for information or a
  report to be provided later, and it is not answered on the spot.
- `return`: the body holds, postpones or continues a matter, or says it will come back
  ("held until September 24", "we will take this up again next week").
- `referral`: a matter is sent to a committee, a department or another body for action.
- `scheduled`: a next step with a stated time that is none of the above (a public hearing
  set for a date, a vote announced for the next meeting, a briefing planned).

## What does not count

- A vote or decision that is finished in this meeting and creates nothing to follow
  (a bill passes; minutes are approved). The passing itself is not an item.
- Thanks, ceremony, public comment, and opinions about what the city ought to do in general.
- Something requested and answered within this meeting.
- A wish with no one to carry it ("someone should look at that").

When unsure whether something is a follow-up item, leave it out. Do not split one follow-up
into several items, and do not merge two different ones.

## Method

1. Read the whole transcript, first line to last.
2. For each follow-up item write one sentence a reader could check later without the
   transcript: who, what, and the stated time if any. Use the names, numbers and dates as
   spoken. Do not add anything the transcript does not say.
3. Cite the lines the item rests on (`lines`, at most 6 line ids) and copy a `quote` of at most
   25 words, verbatim, from one of those lines.
4. `matter`: the bill, resolution or file number the item belongs to, as spoken or as given in
   the job header (for example "CB 121214"); empty if none.
5. `due`: `next_meeting` if the transcript says or clearly implies it will come up at the
   body's next meeting, `later` if it names a later time, `unstated` otherwise.

## Output

JSONL, one object per item, in transcript order, no other text. An empty file is a valid
answer when the meeting creates no follow-up item.

```
{"kind": "commitment|info_request|return|referral|scheduled", "text": "one sentence", "lines": ["L0012"], "quote": "verbatim words from a cited line", "matter": "", "due": "next_meeting|later|unstated"}
```

## One invented example (not from the data)

```
L0030 [04:10] Speaker 2: I move to hold the tree ordinance until the twelfth
L0031 [04:15] Speaker 1: seconded, those in favour, aye, it is held
L0044 [06:02] Speaker 3: can the parks department tell us how many permits were issued last year
L0045 [06:09] Speaker 4: I do not have that number today, we will send it to the committee
L0060 [08:40] Speaker 1: the minutes are approved
```

```
{"kind": "return", "text": "The tree ordinance is held until the twelfth.", "lines": ["L0030", "L0031"], "quote": "I move to hold the tree ordinance until the twelfth", "matter": "", "due": "later"}
{"kind": "info_request", "text": "The parks department will send the committee the number of permits issued last year.", "lines": ["L0044", "L0045"], "quote": "we will send it to the committee", "matter": "", "due": "unstated"}
```

The approved minutes are not an item.
