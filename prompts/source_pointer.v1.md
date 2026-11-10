# Source pointer (v1): where in the meeting does this note bullet come from?

You are given the notes a meeting note-taker wrote for one meeting, split into bullets, and
the note-taker's own transcript of that meeting. For each bullet you are asked about, name
the transcript lines where what the bullet states was actually said or settled. A reader who
doubts the bullet will jump to the lines you name and read around them; they cannot replay
the audio, so your lines are their only way to check a number, a decision or who took a task.

Read only this job file: do not open any other file in the repository (other files hold the
answers), do not search the web, and do not use anything you may remember about this meeting
or the corpus it comes from. Work from the bullets and the transcript below, nothing else.

## Input

- **Notes**: every bullet of the notes, `B12 [section: heading] (sub-point of B10) text`. You
  get all of them, so you can tell neighbouring bullets apart and read a sub-point with its
  parent. You answer only the ids listed under "Bullets to answer".
- **Transcript**: the whole meeting, one line per utterance, `[T0123 <label> mm:ss] text`.
  The lines are machine-made: words are misheard, a sentence may be cut across several lines,
  one line may hold two people, and the speaker label is usually the same for everybody.

## Method

1. **Read the whole transcript once** before answering anything, so you know how the meeting
   moves: which topics come up, where they come back, where things get concluded.
2. **For each bullet, say to yourself what it claims**: the fact, number, decision, task or
   problem, and any detail that pins it down (an amount, a name, a date, a condition).
3. **Find every place the topic comes up.** A topic is often raised early, argued over in the
   middle and settled later, or stated once and repeated in a closing summary.
4. **Pick the moment the bullet's content comes from.** That is where the thing the bullet
   states was actually said in the form the bullet gives it:
   - for a decision or an agreed value, the lines where it is concluded (the proposal that is
     accepted together with the acceptance), not the first mention of the topic and not an
     earlier option that was dropped;
   - for a task, the lines where it is given or taken, with the owner or the deadline if the
     bullet has them;
   - for a fact or a number, the lines where that fact or number is spoken; if the transcript
     has a different number from the bullet, still point to where the number is spoken;
   - for a problem or an open question, the lines where it is raised and, if the bullet says
     how it was left, where it was left that way.
   If the bullet merges two separate moments (two decisions in one sentence), you may name
   lines from both. If the same content is said twice in full (once in the discussion, once
   in a recap), prefer the discussion and add the recap line only if you have room.
5. **Name 1 to 6 lines.** Fewer is better: the lines that carry the content, not the whole
   discussion around them. Put the single best line first. Consecutive lines that form one
   statement cut across lines count separately, so choose the ones holding the key words.
6. **Say how sure you are**:
   - `high`: the lines state the bullet's content plainly; a reader would agree at once;
   - `medium`: the lines are about the right exchange, but the wording is loose, garbled, or
     the content is spread over more lines than you could name;
   - `low`: you could not find the content as stated; the lines are your best guess at where
     the bullet was drawn from.
   Always answer every bullet, even at `low`: never leave one out and never return no lines.

Check each answer before you write it: read your first line and its neighbours again and ask
whether a reader landing there would see the bullet's claim within a few lines. If they would
see only the topic and not the claim, look further.

## Output

Write the file named in the job: one JSON object per line, one per bullet asked, in the order
asked, nothing else in the file:

```
{"bullet_id":"B12","lines":["T0123","T0124"],"confidence":"high|medium|low"}
```

Every choice must come from your own reading. You may search the transcript for words to
find candidate places, but no rule or script may choose the lines.

## Worked example (invented)

Notes (three of the bullets):

```
B4 [section: Venue] Summer offsite will be at the lake house, not the city hotel
B5 [section: Venue] (sub-point of B4) Budget cap: £4,000 including transport
B9 [section: Next steps] Priya to send the booking form by Friday
```

Transcript (extract):

```
[T0031 Them 03:10] So for the offsite we've got two options, the city hotel or the lake house.
[T0032 Them 03:16] The hotel is easier to get to.
[T0033 Them 03:19] Yeah, but it's nearly six thousand once you add the rooms.
[T0047 Them 05:02] What's the most we can spend?
[T0048 Them 05:05] Finance said four thousand all in, and that has to cover
[T0049 Them 05:09] the coach as well.
[T0050 Them 05:11] Then the hotel's out. Lake house?
[T0051 Them 05:14] Lake house. Agreed.
[T0052 Them 05:16] Fine by me.
[T0088 Them 09:40] I can do the booking form. End of the week okay?
[T0089 Them 09:44] Friday's fine, thanks Priya.
[T0102 Them 11:20] So, to recap, lake house, four thousand cap, and Priya's doing the form.
```

Answers:

```
{"bullet_id":"B4","lines":["T0051","T0050"],"confidence":"high"}
{"bullet_id":"B5","lines":["T0048","T0049"],"confidence":"high"}
{"bullet_id":"B9","lines":["T0088","T0089"],"confidence":"high"}
```

B4: the topic first comes up at T0031, but nothing is settled there; the choice is made at
T0050 to T0051, so those are the lines. T0033 is about the option that was dropped. B5: the
number is spoken at T0048 and "including transport" is T0049 (one sentence cut in two). B9:
the task is taken at T0088 and the deadline and the name are in T0089. The recap at T0102
repeats all three; it is not where any of them was settled, so it is left out.
