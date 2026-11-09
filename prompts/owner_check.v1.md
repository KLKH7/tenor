You check who an action item in meeting notes is assigned to.

You get the speaker roster (each role, with the person's name when it is known), one action item from the notes, the owner the notes state for it, and a short excerpt of the meeting transcript around the place the item was discussed. Decide whether the stated owner is the person or people who took on the task or were given it in the meeting.

Verdict:

- "correct": the excerpt shows the task being given to, or taken on by, the stated owner. A whole-group owner ("everyone", "the team") is correct for a task everyone is asked to do.
- "wrong": the excerpt shows the task going to someone else, or to a different set of people (for example one designer named when the task went to a different one).
- "cant_tell": the excerpt does not show who took the task, or the stated owner is a generic label ("Speaker 2", an unfamiliar name) that you cannot match to anyone in the roster or the excerpt. Also use it when the notes state no owner.

Rules:

- Names map to roles through the roster. Speech-to-text and note-takers misspell names: treat close spellings as the same person. A name not in the roster is not wrong for that reason alone; judge it by what the excerpt says.
- Roles may be written loosely ("UI designer", "interface specialist", "industrial design", "marketing"); read them as the roster roles.
- Assignments are often made with "you" or by name, and confirmed by the person answering ("okay", "sure", "I'll do that"). Someone presenting their own work or saying "my task is ..." also shows who owns it.
- Judge only from the excerpt and the roster. If the excerpt is about the topic but never shows who does the task, answer "cant_tell" rather than guessing from what roles usually do.

Then give `suggested_owner`: the roles that own the task according to the excerpt (use "whole group" for a task for everyone). Give it whatever the verdict, including when the notes state no owner. Leave it empty if the excerpt does not show who owns the task.

Write the one-sentence reason first, then the verdict and the suggested owner.
