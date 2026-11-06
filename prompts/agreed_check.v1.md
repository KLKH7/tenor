You check one item listed as a possible answer to a question about a meeting. The item has already been found in the transcript: it was said. Your job is a different question: what did the meeting actually do with it?

You get the question, the item, the quote it was taken from, and a window of transcript lines around the quote. Read the window, not only the quote: agreement, assignment or rejection often comes a few lines later.

Pick one status:

- "agreed_decision": the group settled it. Someone states it as decided, or proposes it and others accept it (explicit "yes / okay / let's do that", or the discussion moves on treating it as settled). An announcement by the person in charge counts.
- "assigned_action": a specific person or role is asked to do it, or volunteers, and nobody pushes back. A task for "everyone" counts only if it is clearly given as a task.
- "problem_raised": someone raises it as a problem, risk, difficulty or open question that the meeting has not resolved.
- "proposed_only": suggested, wished for or asked about, but not accepted, not assigned, or left open as an idea.
- "mentioned_only": said in passing, as background, opinion or a fact, without being decided, assigned or raised as a problem.
- "other_topic": the lines do not support the item, or they are about something else.

Then say whether the item answers the question as asked (`answers_question`): it is the kind of thing asked for (a decision, an action item, or a problem) and fits any restriction in the question, such as the person or role it asks about. An action assigned to someone other than the role asked about does not answer the question.

Lines are labelled "Them" or "Me" by audio channel, not by person, so they cannot show who spoke. Infer people and roles only from what is said. Speech-to-text adds fillers and mishears words; don't penalise that.

`confidence`: an integer from 0 to 100, how sure you are that the status you chose is right.

Write the one-sentence reason first, then the fields that follow from it.
