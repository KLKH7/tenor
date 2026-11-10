# Speaker naming (v3, names-only invite, one labelled line per person): who is "Them"?

You are given the transcript a meeting note-taker made of one meeting, and the attendee list a
calendar invite would carry. The note-taker heard everyone through one audio channel, so its
speaker labels are generic: `Them` for everybody, or `Speaker 1`, `Speaker 2` from its own
voice clustering, now and then a name it picked up. Your job: for **every** transcript line,
say which attendee spoke it, or `unknown`.

You work from the text alone. There is no audio. Read only this job file: do not open any
other file in the repository (other files hold the answers), do not search the web, and do not
use anything you may remember about this meeting or the corpus it comes from. If you recognise
the meeting, say so in your evidence file and still work only from the lines below.

The invite gives **names only**: no roles, no job titles, in alphabetical order (the order
says nothing). Who does what, you must work out from the transcript itself.

The user of the note-taker has also **labelled a few lines by hand**: for each person, usually
one line, early in the meeting, with "this was X". These labels are certain. Each one ties one
voice to one name, so you do not have to find that tie in the text. What is left is the real
work: deciding, for every other line, which of those voices is speaking. Where you cannot
tell, the answer is still `unknown`.

A wrong name is worse than `unknown`: a wrong name puts an action item on the wrong person,
and nobody checks. Name a line only when the text supports it. Prefer `unknown` to a guess.

## Input

- **Attendees**: names only, alphabetical. Below the list is the exact set of values you may
  write as a speaker: one of those names, or `unknown`. Never a role or a description
  ("the chair", "the editor"): a value that is not on the list counts as a wrong name.
  A person whose name the user does not know is listed as `Person A`, `Person B`, ...: treat
  that as their name and write it in full. The letter says nothing about who they are or what
  they do, and nobody in the meeting will say it; a name you hear in the transcript that is
  not on the list is not a value you may write.
- **Lines the user has labelled**: `T0123 -> name`, one per row. The line with that id was
  spoken by that person. There may be no labelled line for some attendee.
- **Transcript**: one line per utterance, `[T0123 <label> mm:ss] text`. `<label>` is the
  note-taker's own. Lines are machine-made: words are misheard, names are misspelled (an
  attendee "Ilse Brandvold" may appear as "Elsa Brandvold" or "Ilsa"), a sentence may be
  cut across several lines, and one line may hold two people's words.

## Method

Work in this order. Do not assign anything until steps 1 and 2 are done.

The invite does not say who does what, so every name has to be **anchored**: tied to a voice
by a labelled line or by something said in this transcript. Anchors are, from strongest to
weakest: a line the user has labelled; the person says their own name; another speaker
addresses them by name and they answer; a name a note-taker label carries, once verified. Only after a name is anchored can what that person does (their
function: chairing, presenting the costs, the design) carry the name to other lines. A function
you can follow through the whole meeting but never tie to a name stays `unknown`, however sure
you are that it is one person.

### 1. Collect evidence, each item with its T id

- **Labelled lines.** Start here. A labelled line is certain, and it fixes that voice's
  identity for the whole meeting: whoever speaks that line is that person in every line they
  speak, before it and after it. Read each labelled line and the turn around it closely and
  write down what it tells you about that voice: what the person is presenting or responsible
  for (their function), what they are asked and by whom, how others address them, which
  note-taker label the line carries, how they talk. From there the name travels in three
  ways: **turn-taking** (the rest of the same turn; the reply to a question this voice asked
  is someone else; a question put to this voice is answered by it), **what the person talks
  about** (later lines that speak as the owner of the same material), and **how others
  address them** (a spoken name or "you" tied to that function). A labelled line also rules
  out: a line that speaks *to* that person, or of them in the third person, is not theirs.
  One labelled line does not name the lines around it by itself: the next line may be another
  person. And it is one line of a voice, not a promise that the voice is easy to follow: far
  from the labelled line, with nothing but a similar tone, the answer is `unknown`.
- **Self-introductions.** "My name is Kofi, and I do the photographs": the line is Kofi, and
  Kofi is the photographer. Match misspelled names to the attendee list by sound. Many
  meetings have none: people who know each other do not introduce themselves.
- **Function statements and function content.** "As the editor I ..."; "I'm on the sales
  side"; a person presenting their own cost sheet is whoever does the costs. This gives a
  *function*, not a name. Write down each function you can tell apart and what its voice
  presents, then look for an anchor that ties it to a name. A person may also describe
  someone else's function ("she's doing the layout"): that says who the speaker is **not**.
- **Address terms.** "And Dagny, where are we on the layout?": the speaker is not
  Dagny, the person who answers next very likely is, and Dagny is now tied to the
  layout. "Thanks, Dagny": the previous speaker is probably Dagny, the current one is not.
- **Third-person mentions.** "You need to sit down with Renzo on that": neither the speaker
  nor the person spoken to is Renzo. "Kofi's photos": the person who then shows photos, or is
  asked about them, is probably Kofi (one step of inference, not an anchor by itself).
- **Chairing.** One person opens the meeting, hands out turns, asks for plans, closes. That is
  a function like any other: it needs an anchor before it has a name.
- **Elimination.** A voice may take a name when every other name on the invite is ruled out
  for it by evidence you can cite (it addresses them, speaks of them in the third person, or
  they are anchored, by a labelled line or otherwise, to a voice you can tell apart from this
  one). With a labelled line for every attendee, elimination is often the quickest sound step. A name that is merely left
  over is not evidence: an attendee may say nothing, and two people may share a label.
- **What is not evidence.** The order of the list; which name "sounds like" which job; a
  name's apparent gender or origin on its own; how such meetings usually go.
- **Note-taker labels.** Record what each label seems to cover (step 3).

### 2. Segment the transcript into turns

A turn is a run of consecutive lines by one person. Mark where the speaker changes:

- a question followed by its answer; a short echo or "Mm-hm" / "Okay" / "Yes" between two
  parts of someone's longer statement (a backchannel from a listener, usually the person who
  asked the question);
- a change of note-taker label, where the note-taker gives more than one label;
- a sentence that stops mid-way and continues on the next line is one turn ("I want to ask" /
  "Dagny about the layout");
- a line that holds two people ("Who has the proofs? Nobody. No, uh, that's me."): give
  it to the person with more of its words if that is clear, else `unknown`.

### 3. Decide what each note-taker label is worth

- `Them` on every line carries no information. Everything rests on steps 1 and 2.
- `Speaker N` labels are voice clusters. A labelled line tells you one person its cluster
  holds, not that it holds only that person: two labelled lines with different names under
  one `Speaker 1` prove that label merges people. They help, but check each one against your
  evidence before you trust it: a cluster can **merge two people** (two introductions with different
  names under one `Speaker 1` means that label covers at least two people, so the label alone
  never names a line), and one person can be split over two labels. A label you have checked
  at several points and found to be one person can carry that name to its other lines.
- A name as a label (`Kofi`) is the note-taker's guess. Treat it like a `Speaker N` label that
  comes with a suggestion: verify it once, then use it.
- `Me` is the owner of the note-taker account, if the attendee list says who that is.

### 4. Assign every line

For each line give a speaker from the allowed values, a confidence and a basis.

- `speaker`: an attendee exactly as written in the allowed values, or `unknown`.
- `confidence`:
  - `high`: direct evidence in or right next to this turn (the line is labelled by the user,
    or is plainly the same turn as a labelled line; the person introduces themselves in it; they were addressed by name in the line before and the reply fits; a label you have
    verified as exactly one person);
  - `medium`: one step of inference (role content; a verified label that may still merge
    people, backed by the content of the turn; the continuation of a `high` turn);
  - `low`: turn-taking or elimination only.
  If you would not put the name at better than about two in three, write `unknown`.
- `basis`: the main reason for this line: `labelled_anchor` (the line is one the user
  labelled, or you assign it mainly by continuity with a labelled line: the same turn, or the
  same exchange a few lines on), `intro`, `addressed` (named by another speaker just
  before or after), `role_content`, `turn_taking` (question/answer structure, continuation,
  backchannel position, elimination), `diarisation_label`, or `none`. A line far from any
  labelled line that you assign because it speaks as the same function is `role_content`,
  not `labelled_anchor`, even though the name came from a labelled line. `role_content` here
  means: the line's function content, for a function you have anchored to this name. `unknown` always has
  basis `none` and confidence `low`.

### 5. Consistency pass

Go through your assignments once more and fix or downgrade to `unknown`:

- a person is not addressed by their own name in their own turn ("Kofi, what do you think?" is
  not Kofi), and does not speak of themselves in the third person;
- a question put to X is usually followed by X; if you named someone else as the answerer,
  one of the two lines is wrong;
- two adjacent turns you split in step 2 should not carry the same name unless a third person
  spoke in between;
- one person has one function: if the person who does the layout was anchored as Dagny, a
  later line that speaks as the person who does the layout is Dagny, and a line that speaks
  *to* that person is not;
- every labelled line carries exactly the name the user gave it, `high`, `labelled_anchor`,
  and nothing you assigned contradicts one (the same turn under two names; a line that
  addresses X given to X);
- every name you used has an anchor you can point to by T id (a labelled line is one); a name whose only support is
  "the others were taken" or "this is probably the chair" goes back to `unknown`;
- under a `Speaker N` label you found to merge two people, every line needs its own evidence.

## Output

Write `<unit>.jsonl` at the path given in the job: one JSON object per line of the
transcript, in order, every line present exactly once, nothing else in the file:

```
{"line":"T0123","speaker":"<allowed value>","confidence":"high|medium|low","basis":"labelled_anchor|intro|addressed|role_content|turn_taking|diarisation_label|none"}
```

Also write `<unit>.evidence.md` next to it: your step 1 evidence list (with T ids), what each
labelled line told you about its voice, the anchor for each name you used (or "no anchor
found"), what each note-taker label covers, and anything
you could not resolve. Keep it short.

You may record your turns as ranges while you work and expand them to one object per line
with a few lines of code. Every assignment must come from your own reading: no rule or script
that assigns speakers.

## Worked example

An invented meeting (not from any recording): four people plan a cookbook. The invite reads
`Dagny`, `Ilse Brandvold`, `Kofi`, `Renzo`, with no roles. The note-taker labels with voice
clusters. To keep the example short the user has labelled one line only (a real job has about
one per person):

```
T0009 -> Ilse Brandvold
```

```
[T0000 Speaker 1 00:01] Right, shall we start? Three things today: the photo schedule, the printer quotes, and the launch date.
[T0001 Speaker 1 00:09] Kofi, do you want to go first with the photos?
[T0002 Speaker 2 00:12] Sure. So I shot the first six recipes last week, and, uh, four of them I'm happy with.
[T0003 Speaker 1 00:20] Mm-hm.
[T0004 Speaker 2 00:21] The soups need doing again, the light was wrong.
[T0005 Speaker 3 00:26] Can I ask, does that move the layout? Because I was going to start the soup chapter Monday.
[T0006 Speaker 2 00:31] By a week, I think. Sorry, Dagny.
[T0007 Speaker 3 00:34] No, that's fine.
[T0008 Speaker 1 00:36] Okay. And the printer quotes?
[T0009 Speaker 3 00:39] So I've got two quotes in. The cheaper one is four weeks, the other one is two.
[T0010 Speaker 1 00:47] Thanks. Elsa and I will look at the numbers tonight.
[T0011 Speaker 2 00:51] Okay.
[T0012 Speaker 3 00:52] Yeah.
```

- T0001 addresses Kofi, so it is not Kofi. T0002 answers and speaks of its own photos: Kofi
  (`addressed`, high). T0004 continues the answer (`turn_taking`, medium). `Speaker 2` is now
  anchored at one point; check it again before the label alone carries the name.
- T0006 is the photographer still answering, under the same label: Kofi (`diarisation_label`
  or `turn_taking`, medium). It says "Sorry, Dagny" to the person who asked in T0005, so
  T0005 is Dagny (`addressed`, high), Dagny does the layout, and T0007 is Dagny's reply
  (`turn_taking`, medium).
- The chair (T0000, T0001, T0003, T0008, T0010) is one voice under one label. It is not Kofi
  (T0001 addresses him), not "Elsa", which is Ilse Brandvold misheard (T0010 speaks of her in
  the third person), and not Dagny, who was anchored to another voice in T0005 to T0007. Only
  attendees speak, so the chair is Renzo: `turn_taking`, `medium` where the line itself
  rules someone out (T0001, T0010), `low` elsewhere. This is elimination done properly: three
  names ruled out by evidence with T ids, not "the name left over".
- T0009 is labelled: Ilse Brandvold, `high`, `labelled_anchor`. What it tells: her voice is
  the one with the printer quotes, which fits T0010 tying "Elsa" to "the numbers"; and it
  sits under `Speaker 3`, the label T0005 and T0007 had. T0005 and T0007 are Dagny by
  address (T0006), so `Speaker 3` merges Dagny and Ilse Brandvold: that label alone now names
  nothing. Without the labelled line T0009 would have been `unknown` (Dagny with a second
  job, or a merged cluster? the text does not say).
- The labelled line does not travel back to T0005 or T0007 just because they share its
  label: they stay Dagny on their own evidence. It does not reach T0012 either: "Yeah."
  under a label known to hold two people, after a line addressed to nobody in particular:
  `unknown`.
- Had T0009 been followed by "and the cheaper one wants a deposit" under the same label, that
  line would be Ilse Brandvold too (`labelled_anchor`, medium: same turn). A line ten minutes
  later that speaks as the person who got the quotes is hers by `role_content`.
- T0011 "Okay." could be anyone but for its label. `Speaker 2` has only been Kofi so far:
  Kofi at `low` (`diarisation_label`), or `unknown` if the label has proved unreliable.
