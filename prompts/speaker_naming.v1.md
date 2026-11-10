# Speaker naming (v1): who is "Them"?

You are given the transcript a meeting note-taker made of one meeting, and the attendee list a
calendar invite would carry. The note-taker heard everyone through one audio channel, so its
speaker labels are generic: `Them` for everybody, or `Speaker 1`, `Speaker 2` from its own
voice clustering, now and then a name it picked up. Your job: for **every** transcript line,
say which attendee spoke it, or `unknown`.

You work from the text alone. There is no audio. Read only this job file: do not open any
other file in the repository (other files hold the answers), do not search the web, and do not
use anything you may remember about this meeting or the corpus it comes from. If you recognise
the meeting, say so in your evidence file and still work only from the lines below.

A wrong name is worse than `unknown`: a wrong name puts an action item on the wrong person,
and nobody checks. Name a line only when the text supports it.

## Input

- **Attendees**: names with roles, or roles only when the invite has no names, or `unknown`.
  Below the list is the exact set of values you may write as a speaker.
- **Transcript**: one line per utterance, `[T0123 <label> mm:ss] text`. `<label>` is the
  note-taker's own. Lines are machine-made: words are misheard, names are misspelled (an
  attendee "Shrida Daseri" may appear as "Sridhar Dasari" or "Shreedhar"), a sentence may be
  cut across several lines, and one line may hold two people's words.

## Method

Work in this order. Do not assign anything until steps 1 and 2 are done.

### 1. Collect evidence, each item with its T id

- **Self-introductions.** "My name is Agnes, and I'm a ... user interface designer": the line
  is Agnes. Match misspelled names to the attendee list by sound.
- **Role statements and role content.** "My role in this scenario is to be the industrial
  designer"; "I'm in the sales"; a person presenting their own market survey is the marketing
  person. A person may also *describe someone else's* role ("she's doing the design"): that
  says who the speaker is **not**.
- **Address terms.** "And what about Christine, what about your industrial design plan?": the
  speaker is not Christine, and the person who answers next very likely is. "Thanks, Ed": the
  previous speaker is probably Ed, the current one is not.
- **Third-person mentions.** "You need to interact more with Christine": neither the speaker
  nor the person spoken to is Christine.
- **Chairing.** One person opens the meeting, hands out turns, asks for plans, closes. On an
  invite with roles this is usually the project manager, but confirm it from the text (an
  introduction, "I'm the project manager", "my slides").
- **Note-taker labels.** Record what each label seems to cover (step 3).

### 2. Segment the transcript into turns

A turn is a run of consecutive lines by one person. Mark where the speaker changes:

- a question followed by its answer; a short echo or "Mm-hm" / "Okay" / "Yes" between two
  parts of someone's longer statement (a backchannel from a listener, usually the person who
  asked the question);
- a change of note-taker label, where the note-taker gives more than one label;
- a sentence that stops mid-way and continues on the next line is one turn ("I want to ask" /
  "Ed about your marketing plan");
- a line that holds two people ("Who is marketing? Nobody. Marketing is, uh, is me."): give
  it to the person with more of its words if that is clear, else `unknown`.

### 3. Decide what each note-taker label is worth

- `Them` on every line carries no information. Everything rests on steps 1 and 2.
- `Speaker N` labels are voice clusters. They help, but check each one against your evidence
  before you trust it: a cluster can **merge two people** (two introductions with different
  names under one `Speaker 1` means that label covers at least two people, so the label alone
  never names a line), and one person can be split over two labels. A label you have checked
  at several points and found to be one person can carry that name to its other lines.
- A name as a label (`Ed`) is the note-taker's guess. Treat it like a `Speaker N` label that
  comes with a suggestion: verify it once, then use it.
- `Me` is the owner of the note-taker account, if the attendee list says who that is.

### 4. Assign every line

For each line give a speaker from the allowed values, a confidence and a basis.

- `speaker`: an attendee exactly as written in the allowed values, or `unknown`.
- `confidence`:
  - `high`: direct evidence in or right next to this turn (the person introduces themselves in
    it; they were addressed by name in the line before and the reply fits; a label you have
    verified as exactly one person);
  - `medium`: one step of inference (role content; a verified label that may still merge
    people, backed by the content of the turn; the continuation of a `high` turn);
  - `low`: turn-taking or elimination only.
  If you would not put the name at better than about two in three, write `unknown`.
- `basis`: the main reason for this line: `intro`, `addressed` (named by another speaker just
  before or after), `role_content`, `turn_taking` (question/answer structure, continuation,
  backchannel position, elimination), `diarisation_label`, or `none`. `unknown` always has
  basis `none` and confidence `low`.

When the attendee list is `unknown`, you have no names to assign: write `unknown` for a line
unless its speaker states their own name in the transcript, and then use that name as heard.

### 5. Consistency pass

Go through your assignments once more and fix or downgrade to `unknown`:

- a person is not addressed by their own name in their own turn ("Ed, what do you think?" is
  not Ed), and does not speak of themselves in the third person;
- a question put to X is usually followed by X; if you named someone else as the answerer,
  one of the two lines is wrong;
- two adjacent turns you split in step 2 should not carry the same name unless a third person
  spoke in between;
- one person has one role: if "the marketing person" introduced themselves as Ed, a later
  line that speaks as the marketing person is Ed, and a line that speaks *to* the marketing
  person is not;
- under a `Speaker N` label you found to merge two people, every line needs its own evidence.

## Output

Write `<unit>.jsonl` at the path given in the job: one JSON object per line of the
transcript, in order, every line present exactly once, nothing else in the file:

```
{"line":"T0123","speaker":"<allowed value>","confidence":"high|medium|low","basis":"intro|addressed|role_content|turn_taking|diarisation_label|none"}
```

Also write `<unit>.evidence.md` next to it: your step 1 evidence list (with T ids), what each
note-taker label covers, and anything you could not resolve. Keep it short.

You may record your turns as ranges while you work and expand them to one object per line
with a few lines of code. Every assignment must come from your own reading: no rule or script
that assigns speakers.

## Worked examples

All from one development meeting (attendees: Shrida Daseri, Project Manager; Agnes, User
Interface Designer; Christine, Industrial Designer; Ed, Marketing Expert). Two note-takers
recorded it; the first labels with voice clusters, the second with `Them` only.

**Introductions, and a label that merges two people.**

```
[T0002 Speaker 2 00:02] Hi guys, uh, good morning everybody here. And I want to introduce myself, my name is Sridhar Dasari, and I'm a project manager for this new project ...
[T0003 Speaker 1 00:24] Uh, sure. My name is Agnes, and I'm an user- usability user interface designer.
[T0004 Ed 00:30] My name is Ed, and I do accounting.
[T0005 Speaker 2 00:33] Uh, how do you spell your name?
[T0013 Speaker 1 00:42] And I'm Christine, and my role in this scenario is to be the industrial designer.
```

T0002 is Shrida Daseri (`intro`, high; the name is misheard, the role confirms it). T0003 is
Agnes and T0004 is Ed (`intro`, high). T0005 is Shrida Daseri: the label is the one that
introduced itself in T0002 and the line chairs (`diarisation_label`, medium until the label
has been checked elsewhere). T0013 is Christine (`intro`, high), and now `Speaker 1` is known
to cover both Agnes and Christine: no later `Speaker 1` line can be named from the label.

**Address, then the reply.**

```
[T0049 Speaker 2 04:03] Mm-hm. And, uh, what about Christine, what about your- the industrial design plan? Are you having a design already on this product ...
[T0050 Speaker 1 04:16] Um, no, I- I have not begun working on the design.
[T0051 Speaker 2 04:20] Mm-hm.
[T0052 Speaker 1 04:21] And, um, I, uh, I actually didn't know we were designing a remote control. ...
```

T0049 addresses Christine, so it is not Christine; it is the chair (Shrida Daseri,
`diarisation_label`). T0050 is Christine (`addressed`, high). T0051 is a backchannel from the
asker (Shrida Daseri, `turn_taking` or `diarisation_label`). T0052 continues Christine's
answer (`turn_taking`, medium).

**Role content splits a merged label.**

```
[T0094 Speaker 2 12:53] Or maybe, uh, you have my coordinator, am I right?
[T0097 Speaker 1 12:59] Well, no, not exactly. I mean, my job from what I understood was to look at the usability requirements and make sure that the product is usable ...
[T0099 Speaker 1 13:18] So I think I'll have to interact with Christine and discuss with her.
```

`Speaker 1` is Agnes or Christine. T0097 speaks about usability as its own job: Agnes
(`role_content`, medium). T0099 names Christine in the third person, so it is not Christine:
Agnes (`role_content` or `turn_taking`, medium).

**Third-person mention rules a person out.**

```
[T0085 Speaker 2 10:25] Yes. So what I prefer maybe, uh, you need to interact more with the Christine, okay, because you know what she's going to do it, okay, and you know how to sell it ...
```

Not Christine, and the "you" who sells is not Christine either: the chair speaking to Ed.

**`Them` only: sentences cut across lines, and a line with two people.**

```
[T0029 Them 01:38] First of all, I want to ask
[T0031 Them 01:41] Eid about your marketing plan and your
[T0032 Them 01:44] product plan.
[T0033 Them 01:45] And I think we will see that throughout the day and how we're going to put this together as a marketing
[T0024 Them 01:19] who is marketing? Nobody Marketing is is me.
```

T0029 to T0032 are one turn: the chair asking Ed ("Eid" is Ed), so Shrida Daseri
(`turn_taking`, medium, given the earlier introduction as project manager). T0033 is the
reply: Ed (`addressed`, high). T0024 holds a question from one person and "Marketing is me"
from another, about half each: `unknown`, basis `none`.

**When to stop.** Near the end of that meeting several people say "Okay." / "Thank you." /
"Sure." in quick succession under `Them`. Nothing in the text says who: `unknown`.
