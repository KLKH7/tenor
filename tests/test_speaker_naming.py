"""Speaker naming: alignment of product lines to true speakers and the scoring maths
(tenor.evals.speaker_naming). Synthetic inputs only: no AMI data, no product DBs, no network."""

from __future__ import annotations

import json

from tenor.evals import speaker_naming as S

# -- helpers ----------------------------------------------------------------------------------


def _ref(turns: list[tuple[str, float, str, int]]) -> list[S.RefWord]:
    """(agent, start, text, act) turns -> reference words, one every 0.4 s."""
    out = []
    for agent, start, text, act in turns:
        out += [S.RefWord(agent, start + 0.4 * i, t, act) for i, t in enumerate(S.tokens(text))]
    return sorted(out, key=lambda w: w.start)


REF = _ref(
    [
        ("A", 10.0, "good morning everybody my name is anna and i chair this meeting", 0),
        ("B", 16.0, "hello i am ben and i do the marketing for the remote control", 1),
        ("A", 22.0, "ben will you prepare the sales plan by friday", 2),
        ("B", 26.0, "yes i will send the plan on friday", 3),
        ("C", 30.0, "mm-hmm", 4),
        ("C", 31.0, "i can draw the first prototype of the case tomorrow", 5),
    ]
)
OFFSET = 30.0  # product clock = AMI clock + 30 s


def _line(i: int, ami_start: float, ami_end: float, text: str, label: str = "Them") -> S.ProdLine:
    return S.ProdLine(i, label, ami_start + OFFSET, ami_end + OFFSET, text)


LINES = [
    _line(0, 10.0, 15.0, "Good morning, everybody. My name is Anna and I chair this meeting."),
    _line(1, 16.0, 21.0, "Hello, I am Ben and I do the marketing for the remote control."),
    _line(2, 22.0, 29.5, "Ben, will you prepare the sales plan by Friday? Yes, I will send the"),
    _line(3, 29.5, 30.0, "plan on Friday."),
    _line(4, 30.0, 30.5, "Mhmm."),
    _line(5, 31.0, 35.0, "I can draw the first prototype of the case tomorrow."),
    _line(6, 35.0, 36.0, "Zebra crossing."),
]
PEOPLE = [
    S.Attendee("A", "Project Manager", "Anna Lee"),
    S.Attendee("B", "Marketing Expert", "Ben"),
    S.Attendee("C", "Industrial Designer", None),
]


def _truth(
    spec: list[tuple[str | None, int, float]], acts: dict[int, dict[int, int]] | None = None
) -> list[S.LineTruth]:
    """(speaker, words, confidence) per line -> line truths (never mixed)."""
    return [
        S.LineTruth(i, n, who, conf, False, {who: n} if who else {}, (acts or {}).get(i, {}))
        for i, (who, n, conf) in enumerate(spec)
    ]


def _preds(*rows: tuple[str, str, str]) -> dict[int, S.Prediction]:
    return {i: S.Prediction(i, *r) for i, r in enumerate(rows)}


# -- tokens -----------------------------------------------------------------------------------


def test_tokens_keep_fillers_in_one_spelling() -> None:
    assert S.tokens("Mm-hm.") == S.tokens("Mhmm") == S.tokens("mm-hmm") == ["mhm"]
    assert S.tokens("Um, OK: 25 euros!") == ["uh", "okay", "twenty", "five", "euros"]
    assert S.tokens("...") == []


# -- alignment --------------------------------------------------------------------------------


def test_align_gives_each_line_its_speaker() -> None:
    got = S.align(REF, LINES)
    assert [t.speaker for t in got.lines] == ["A", "B", "A", "B", "C", "C", None]
    assert [t.status for t in got.lines] == [
        "confident",
        "confident",
        "mixed",
        "confident",
        "confident",
        "confident",
        "unaligned",
    ]
    assert got.lines[0].confidence == 1.0
    assert got.lines[0].n_words == 12


def test_align_mixed_line_counts_words_per_speaker_and_act() -> None:
    t = S.align(REF, LINES).lines[2]
    assert t.words == {"A": 9, "B": 5}
    assert t.confidence == round(9 / 14, 3)
    assert t.mixed
    assert t.acts == {2: 9, 3: 5}


def test_align_estimates_the_replay_offset() -> None:
    got = S.align(REF, LINES)
    assert got.offset_s is not None and abs(got.offset_s - OFFSET) < 1.0
    assert got.anchors > 40 and got.dropped == 0


def test_align_confidence_counts_unmatched_words_against_the_line() -> None:
    lines = [_line(0, 31.0, 35.0, "I can draw the first prototype blah blah blah blah blah blah")]
    t = S.align(REF, lines).lines[0]
    assert (t.speaker, t.n_words, t.words) == ("C", 12, {"C": 6})
    assert t.confidence == 0.5 and t.status == "low"


def test_align_recovers_overlapping_speech_by_local_match() -> None:
    # B says "right exactly" inside A's sentence; AMI interleaves by time, the product does not
    ref = _ref([("A", 0.0, "we should make the remote yellow and cheap for everybody", 0)])
    ref += [S.RefWord("B", 1.3, "right", 1), S.RefWord("B", 1.7, "exactly", 1)]
    ref.sort(key=lambda w: w.start)
    lines = [
        S.ProdLine(
            0, "Them", 0.0, 4.0, "We should make the remote yellow and cheap for everybody."
        ),
        S.ProdLine(1, "Them", 4.0, 4.5, "Right, exactly."),
    ]
    got = S.align(ref, lines)
    assert [t.speaker for t in got.lines] == ["A", "B"]
    assert got.lines[1].words == {"B": 2}


def test_align_does_not_match_a_word_far_away_in_time() -> None:
    ref = _ref(
        [
            ("A", 0.0, "the budget is twelve euros and fifty cents for each remote control", 0),
            ("B", 300.0, "goodbye", 1),
        ]
    )
    lines = [
        S.ProdLine(0, "Them", 0.0, 5.0, "The budget is twelve euros and fifty cents for each"),
        S.ProdLine(1, "Them", 5.0, 5.5, "Goodbye."),
        S.ProdLine(2, "Them", 5.5, 6.5, "remote control."),
    ]
    got = S.align(ref, lines)
    assert [t.speaker for t in got.lines] == ["A", None, "A"]


def test_align_is_deterministic_and_handles_empty_input() -> None:
    assert S.align(REF, LINES) == S.align(REF, LINES)
    empty = S.align(REF, [])
    assert empty.lines == () and empty.offset_s is None
    assert [t.status for t in S.align([], LINES).lines] == ["unaligned"] * len(LINES)


def test_alignment_counts() -> None:
    assert S.alignment_counts(S.align(REF, LINES).lines) == {
        "lines": 7,
        "confident": 5,
        "low": 0,
        "mixed": 1,
        "unaligned": 1,
    }


# -- attendees and predictions ----------------------------------------------------------------


def test_resolve_speaker_accepts_name_first_name_role_and_listing() -> None:
    for value in ("Anna Lee", "anna", "Project Manager", "Anna Lee (Project Manager)"):
        assert S.resolve_speaker(value, PEOPLE) == "A"
    assert S.resolve_speaker("Industrial Designer", PEOPLE) == "C"
    assert S.resolve_speaker("unknown", PEOPLE) is None
    assert S.resolve_speaker("Speaker 2", PEOPLE) is None
    twins = [S.Attendee("A", None, "Sam Hill"), S.Attendee("B", None, "Sam Wood")]
    assert S.resolve_speaker("Sam", twins) is None
    assert S.resolve_speaker("Sam Wood", twins) == "B"


def test_attendee_block_orders_by_name_and_handles_missing_rosters() -> None:
    block = S.attendee_block(PEOPLE)
    assert block[:4] == [
        "Attendee list (3 people):",
        "- Anna Lee (Project Manager)",
        "- Ben (Marketing Expert)",
        "- Industrial Designer",
    ]
    assert (
        block[-1]
        == "Write a speaker exactly as one of: Anna Lee; Ben; Industrial Designer; unknown."
    )
    nobody = [S.Attendee("A", None, None), S.Attendee("B", None, None)]
    assert not S.usable(nobody) and S.usable(PEOPLE)
    assert S.attendee_block(nobody)[0].startswith("Attendee list: unknown")


def test_names_only_invite_lists_names_alphabetically_without_roles() -> None:
    full = [
        S.Attendee("A", "Project Manager", "Zoe Lee"),
        S.Attendee("B", "Marketing Expert", "Ben"),
    ]
    assert S.all_named(full) and not S.all_named(PEOPLE) and not S.all_named([])
    people = S.names_only(full)
    block = S.attendee_block(people)
    assert block == [
        "Attendee list (2 people):",
        "- Ben",
        "- Zoe Lee",
        "",
        "Write a speaker exactly as one of: Ben; Zoe Lee; unknown.",
    ]
    text = S.job_file("XX0001a", "granola", "P\n", people, [S.ProdLine(0, "Them", 0.0, 1.0, "Hi.")])
    assert "Manager" not in text and "Marketing" not in text
    assert S.resolve_speaker("zoe", people) == "A" and S.resolve_speaker("Ben", people) == "B"
    assert S.resolve_speaker("Project Manager", people) is None
    assert S.resolve_speaker("Zoe Lee (Project Manager)", people) is None


def test_names_only_scores_a_name_and_counts_a_role_as_wrong() -> None:
    full = [S.Attendee("A", "Project Manager", "Zoe Lee"), S.Attendee("B", None, "Ben")]
    truth = [S.LineTruth(i, 4, "A", 1.0, False, {"A": 4}, {}) for i in range(3)]
    preds = {
        0: S.Prediction(0, "Zoe Lee", "high", "intro"),
        1: S.Prediction(1, "Project Manager", "medium", "role_content"),
        2: S.Prediction(2, "unknown", "low", "none"),
    }
    labels = dict.fromkeys(range(3), "Them")
    row = S.score_unit(truth, labels, S.names_only(full), preds)
    assert row["cells"]["all"]["named"] == 2 and row["cells"]["all"]["correct"] == 1
    assert row["unresolved_names"] == 1
    assert S.score_unit(truth, labels, full, preds)["cells"]["all"]["correct"] == 2


def test_parse_predictions_reports_what_is_wrong() -> None:
    ok = {"line": "T0000", "speaker": "Ben", "confidence": "high", "basis": "intro"}
    text = "\n".join(
        [
            json.dumps(ok),
            json.dumps(ok),  # repeated
            json.dumps({**ok, "line": "T0001", "confidence": "certain"}),
            json.dumps({**ok, "line": "T0009"}),
            "not json",
            json.dumps({"line": "T0002", "speaker": "unknown"}),
        ]
    )
    preds, errors = S.parse_predictions(text, 3)
    assert list(preds) == [0] and preds[0].speaker == "Ben"
    assert len(errors) == 6  # five bad rows and "2 lines without a row"
    assert errors[-1].startswith("2 lines without a row (first T0001)")


# -- scoring ----------------------------------------------------------------------------------

TRUTH = _truth(
    [("A", 10, 1.0), ("B", 4, 1.0), ("B", 2, 0.5), ("C", 1, 1.0), (None, 3, 0.0), ("A", 5, 0.8)]
)
LABELS = {0: "Speaker 1", 1: "Speaker 2", 2: "Speaker 2", 3: "Speaker 2", 4: "Speaker 1", 5: "Ben"}


def test_score_unit_counts_lines_and_words_on_confident_lines_only() -> None:
    preds = _preds(
        ("Anna Lee", "high", "intro"),
        ("Anna Lee", "medium", "turn_taking"),  # wrong
        ("Ben", "low", "turn_taking"),  # truth not confident: not scored
        ("unknown", "low", "none"),
        ("Ben", "low", "turn_taking"),  # unaligned: not scored
        ("Mystery Guest", "low", "turn_taking"),  # names no attendee: named, wrong
    )
    got = S.score_unit(TRUTH, LABELS, PEOPLE, preds)
    assert got["cells"]["all"] == {
        "lines": 4,
        "words": 20,
        "named": 3,
        "named_words": 19,
        "correct": 1,
        "correct_words": 10,
    }
    assert got["cells"]["min_words"]["lines"] == 3  # the one-word line is left out
    assert got["cells"]["confidence:high"]["correct"] == 1
    assert got["cells"]["basis:turn_taking"] == {
        "lines": 2,
        "words": 9,
        "named": 2,
        "named_words": 9,
        "correct": 0,
        "correct_words": 0,
    }
    assert got["unresolved_names"] == 1
    assert got["alignment"]["confident"] == 4


def test_missing_prediction_counts_as_not_named() -> None:
    got = S.score_unit(TRUTH, LABELS, PEOPLE, {})
    assert got["cells"]["all"]["lines"] == 4 and got["cells"]["all"]["named"] == 0


def test_majority_baseline_picks_the_most_talkative_by_words() -> None:
    assert S.majority_baseline(TRUTH) == {
        "speaker": "A",
        "lines": 4,
        "correct": 2,
        "words": 20,
        "correct_words": 15,
    }
    assert S.majority_baseline([])["speaker"] is None


def test_label_baseline_one_to_one_and_many_to_one() -> None:
    # Speaker 1 holds A twice and B once; Speaker 2 holds B twice and C twice; Speaker 3 holds C
    truth = _truth([(w, 3, 1.0) for w in "AABBBCCC"])
    labels = dict(enumerate(["S1", "S1", "S1", "S2", "S2", "S2", "S2", "S3"]))
    got = S.label_baseline(truth, labels, ["A", "B", "C"])
    assert got["labels"] == 3 and got["lines"] == 8
    assert got["one_to_one"] == {
        "mapping": {"S1": "A", "S2": "B", "S3": "C"},
        "named": 8,
        "correct": 5,
    }
    assert got["many_to_one"] == {"correct": 5}
    # one label for everyone: one-to-one names one person, the rest are wrong
    one = S.label_baseline(truth, dict.fromkeys(range(8), "Them"), ["A", "B", "C"])
    assert one["one_to_one"]["correct"] == 3 and one["many_to_one"]["correct"] == 3
    # more labels than people: a split speaker costs one-to-one, not many-to-one
    split = S.label_baseline(
        _truth([(w, 3, 1.0) for w in "AAAB"]), dict(enumerate("wxyz")), ["A", "B"]
    )
    assert split["one_to_one"]["correct"] == 2 and split["many_to_one"]["correct"] == 4
    assert split["one_to_one"]["named"] == 2


# -- action owners ----------------------------------------------------------------------------

ACT_AGENT = {0: "A", 1: "B", 2: "A", 3: "B", 4: "C", 5: "C", 20: "A"}
ACT_TRUTH = _truth(
    [("A", 12, 1.0), ("B", 13, 1.0), ("A", 9, 1.0), ("B", 8, 1.0), ("C", 1, 1.0), ("C", 10, 1.0)],
    acts={0: {0: 12}, 1: {1: 13}, 2: {2: 9}, 3: {3: 8}, 4: {4: 1}, 5: {5: 10}},
)


def _owner(action: S.GoldAction, predicted: dict[int, str | None]) -> dict[str, object]:
    return S.action_owner(action, ACT_TRUTH, ACT_AGENT, predicted)


def test_action_owner_commit_uses_the_owners_own_linked_line() -> None:
    a = S.GoldAction("A1", "The Marketing Expert will send the plan.", "B", (2, 3))
    assert _owner(a, {3: "B"}) == {
        "id": "A1",
        "text": a.text,
        "kind": "commit",
        "lines": [3],
        "outcome": "correct",
    }
    assert _owner(a, {3: "A"})["outcome"] == "wrong"
    assert _owner(a, {3: None})["outcome"] == "unnamed"
    assert _owner(a, {})["outcome"] == "unnamed"


def test_action_owner_commit_votes_by_words_and_ties_take_the_worse_outcome() -> None:
    truth = _truth(
        [("B", 4, 1.0), ("B", 4, 1.0), ("B", 9, 1.0)], acts={0: {3: 4}, 1: {3: 4}, 2: {3: 9}}
    )
    a = S.GoldAction("A1", "x", "B", (3,))
    assert S.action_owner(a, truth, ACT_AGENT, {0: "A", 1: "A", 2: "B"})["outcome"] == "correct"
    assert S.action_owner(a, truth[:2], ACT_AGENT, {0: "A", 1: "B"})["outcome"] == "wrong"
    assert S.action_owner(a, truth[:2], ACT_AGENT, {0: None, 1: "B"})["outcome"] == "unnamed"


def test_action_owner_addressed_scores_the_owners_reply() -> None:
    a = S.GoldAction("A2", "The Marketing Expert will prepare the sales plan.", "B", (2,))
    got = _owner(a, {3: "B"})
    assert (got["kind"], got["lines"], got["outcome"]) == ("addressed", [3], "correct")
    assert _owner(a, {3: "C"})["outcome"] == "wrong"
    # the owner does not speak in the next REPLY_ACTS dialogue acts: listed, not guessed
    far = S.GoldAction("A3", "The Project Manager will book a room.", "A", (5,))
    assert _owner(far, {})["kind"] == "ambiguous"
    late = [*ACT_TRUTH, S.LineTruth(6, 4, "A", 1.0, False, {"A": 4}, {5 + S.REPLY_ACTS + 1: 4})]
    assert S.action_owner(far, late, ACT_AGENT, {})["kind"] == "ambiguous"
    soon = [*ACT_TRUTH, S.LineTruth(6, 4, "A", 1.0, False, {"A": 4}, {5 + S.REPLY_ACTS: 4})]
    assert S.action_owner(far, soon, ACT_AGENT, {6: "A"})["outcome"] == "correct"


def test_action_owner_lists_what_it_cannot_score() -> None:
    def why(a: S.GoldAction) -> object:
        got = _owner(a, {})
        assert got["kind"] == "ambiguous" and "outcome" not in got
        return got["why"]

    assert why(S.GoldAction("A1", "x", None, (2,), "gold owner is group")) == "gold owner is group"
    assert why(S.GoldAction("A2", "x", "B", ())) == "no linked passage in AMI"
    assert (
        why(S.GoldAction("A3", "x", "B", (20,))) == "linked passage not in the product transcript"
    )


def test_action_owner_ignores_lines_that_are_not_confident() -> None:
    truth = [
        S.LineTruth(0, 14, "A", 0.64, True, {"A": 9, "B": 5}, {2: 9, 3: 5}),  # mixed
        S.LineTruth(1, 3, "B", 1.0, False, {"B": 3}, {3: 3}),
    ]
    a = S.GoldAction("A1", "x", "B", (3,))
    got = S.action_owner(a, truth, ACT_AGENT, {0: "A", 1: "B"})
    assert (got["kind"], got["lines"], got["outcome"]) == ("commit", [1], "correct")


# -- summary ----------------------------------------------------------------------------------


def _row(meeting: str, product: str, right: int, named: int, lines: int) -> dict[str, object]:
    preds = _preds(
        *[("Anna Lee", "high", "intro")] * right,
        *[("Ben", "low", "turn_taking")] * (named - right),
        *[("unknown", "low", "none")] * (lines - named),
    )
    truth = _truth([("A", 2, 1.0)] * lines, acts={0: {0: 2}})
    actions = [S.GoldAction("A1", "x", "A", (0,)), S.GoldAction("A2", "y", None, (0,), "group")]
    labels = dict.fromkeys(range(lines), "Them")
    unit = S.score_unit(truth, labels, PEOPLE, preds, actions, {0: "A"})
    return {"meeting": meeting, "product": product, **unit}


def test_summarise_pools_counts_and_clusters_by_meeting() -> None:
    rows = [
        _row("M1", "granola", 6, 8, 10),
        _row("M1", "wispr", 2, 4, 10),
        _row("M2", "granola", 0, 5, 20),
    ]
    got = S.summarise(rows, seed=42)
    assert (got["meetings"], got["units"]) == (2, 3)
    assert got["coverage"]["num"] == 17 and got["coverage"]["den"] == 40
    assert got["coverage"]["rate"] == 0.425
    assert got["accuracy_when_named"]["rate"] == round(8 / 17, 4)
    assert got["correct_of_all_words"]["rate"] == 0.2
    assert got["by"]["confidence:high"]["named"]["rate"] == 1.0
    assert got["by"]["basis:turn_taking"]["named"]["rate"] == 0.0
    assert got["baseline_majority"]["rate"] == 1.0
    assert got["actions"] == {"gold": 6, "commit": 3, "addressed": 0, "ambiguous": 3}
    assert got["action_owner_correct"]["num"] == 2 and got["action_owner_correct"]["den"] == 3
    lo, hi = got["coverage"]["ci"]
    assert 0.25 <= lo <= 0.425 <= hi <= 0.6  # the two meetings' own rates bound the CI
    assert S.summarise(rows, seed=42) == got
    assert S.summarise([], seed=42)["coverage"]["rate"] is None


# -- files ------------------------------------------------------------------------------------


def test_truth_file_round_trip() -> None:
    result = S.align(REF, LINES)
    payload = json.loads(json.dumps(S.truth_payload("XX0001a", "granola", PEOPLE, LINES, result)))
    assert payload["meta"]["confident"] == 5 and payload["meta"]["truth_version"] == S.TRUTH_VERSION
    assert payload["lines"][1] | {"words": 0, "acts": 0} == {
        "line": "T0001",
        "label": "Them",
        "n_words": 13,
        "status": "confident",
        "speaker": "B",
        "role": "Marketing Expert",
        "name": "Ben",
        "confidence": 1.0,
        "mixed": False,
        "words": 0,
        "acts": 0,
    }
    truth, labels, people = S.read_truth(payload)
    assert tuple(truth) == result.lines
    assert labels[0] == "Them" and people == PEOPLE


def test_job_file_has_the_transcript_and_no_truth() -> None:
    lines = [
        S.ProdLine(0, "Speaker 2", 0.0, 4.0, "Hello\nthere."),
        S.ProdLine(1, "Ben", 64.0, 66.0, "Hi."),
    ]
    text = S.job_file("XX0001a", "wispr", "PROMPT TEXT\n", PEOPLE, lines)
    assert text.startswith("PROMPT TEXT\n\n---\n\n# Job: XX0001a__wispr\n")
    assert "[T0000 Speaker 2 00:00] Hello there." in text
    assert "[T0001 Ben 01:04] Hi." in text
    assert "speaker_agent_runs/<RUN>/XX0001a__wispr.jsonl" in text
    assert "T0000 to T0001 (2 lines)" in text
    assert "AMI speaker" not in text and "confident" not in text


def test_split_meetings_keep_dev_out_of_test() -> None:
    assert S.split_meetings("dev") == ("IS1008a",)
    assert "IS1008a" not in S.split_meetings("test") and len(S.split_meetings("test")) == 11
    assert (
        S.jobs_dir("dev").name == "speaker_jobs_dev" and S.jobs_dir("test").name == "speaker_jobs"
    )
    assert S.jobs_dir("test", "names").name == "speaker_jobs_names"
    assert S.jobs_dir("dev", "names").name == "speaker_jobs_names_dev"
    assert S.PROMPTS == {"roles": "speaker_naming.v1", "names": "speaker_naming.v2"}


# -- one tap ----------------------------------------------------------------------------------


def test_one_tap_people_keep_names_and_letter_the_rest_by_first_speech() -> None:
    roster = [
        S.Attendee("A", "Project Manager", None),
        S.Attendee("B", "Marketing Expert", "Ben"),
        S.Attendee("C", None, None),
        S.Attendee("D", "Industrial Designer", None),
    ]
    people = S.one_tap_people(roster, ["C", "B", "D"])  # A never speaks: last
    assert [(a.agent, a.label, a.role) for a in people] == [
        ("A", "Person C", None),
        ("B", "Ben", None),
        ("C", "Person A", None),
        ("D", "Person B", None),
    ]
    block = S.attendee_block(people)
    assert block[1:5] == ["- Ben", "- Person A", "- Person B", "- Person C"]
    assert "Manager" not in "\n".join(block)
    assert S.resolve_speaker("person a", people) == "C" and S.resolve_speaker("Ben", people) == "B"
    assert S.resolve_speaker("Person", people) is None
    assert S.resolve_speaker("Project Manager", people) is None


def test_one_tap_anchor_is_an_early_long_sure_line_and_is_seeded() -> None:
    # A: nine lines, first third = lines 0..2; only line 2 is 8+ words, sure and not mixed
    spec: list[tuple[str | None, int, float]] = [
        ("A", 9, 0.8),  # not sure enough
        ("A", 3, 1.0),  # short
        ("A", 8, 0.9),
        *[("A", 12, 1.0)] * 6,  # long and sure, but late
    ]
    truth = _truth(spec)
    got = S.one_tap_anchors(truth, ["A"], "u")["A"]
    assert got == S.Anchor("A", 2, "8+ words", 8)
    mixed = [
        S.LineTruth(2, 8, "A", 0.9, True, {"A": 6, "B": 2}, {}) if t.index == 2 else t
        for t in truth
    ]
    assert S.one_tap_anchors(mixed, ["A"], "u")["A"] == S.Anchor("A", 1, "any length", 3)
    many = _truth([("A", 10, 1.0)] * 30)
    picks = {S.one_tap_anchors(many, ["A"], f"unit{k}")["A"] for k in range(20)}
    assert len(picks) > 1 and all(p is not None and p.line < 10 for p in picks)
    assert S.one_tap_anchors(many, ["A"], "u") == S.one_tap_anchors(many, ["A"], "u")
    assert S.one_tap_anchors(many, ["A"], "u", seed=1) != S.one_tap_anchors(
        many, ["A"], "u", seed=2
    )


def test_one_tap_anchor_relaxes_in_order_and_says_so() -> None:
    truth = _truth(
        [
            ("A", 6, 1.0),
            ("A", 2, 1.0),
            ("A", 2, 1.0),
            ("B", 2, 0.95),
            ("C", 4, 0.8),
            ("C", 1, 0.5),
            ("C", 9, 0.5),
            ("C", 9, 1.0),
        ]
    )
    got = S.one_tap_anchors(truth, ["A", "B", "C", "D"], "u")
    assert got["A"] == S.Anchor("A", 0, "5+ words", 6)
    assert got["B"] == S.Anchor("B", 3, "any length", 2)
    assert got["C"] == S.Anchor("C", 7, "any length, after the first third", 9)
    assert got["D"] is None
    people = [
        S.Attendee(g, None, n)
        for g, n in zip("ABCD", ("Zoe", "Ben", "Person A", "Person B"), strict=True)
    ]
    assert S.anchor_block(got, people) == ["T0000 -> Zoe", "T0003 -> Ben", "T0007 -> Person A"]


def test_one_tap_job_shows_labelled_lines_and_default_job_does_not() -> None:
    lines = [S.ProdLine(0, "Them", 0.0, 4.0, "Hello."), S.ProdLine(1, "Them", 5.0, 6.0, "Hi.")]
    people = S.one_tap_people(PEOPLE, ["C", "A", "B"])
    text = S.job_file("XX0001a", "granola", "P\n", people, lines, ["T0001 -> Ben"])
    head, _, tail = text.partition("## Lines the user has labelled\n\n```\nT0001 -> Ben\n```\n\n")
    assert head.rstrip().endswith("unknown.") and tail.startswith("## Transcript")
    assert "Designer" not in text and "Manager" not in text
    assert "labelled" not in S.job_file("XX0001a", "granola", "P\n", PEOPLE, lines)
    assert S.jobs_dir("test", "names", "one").name == "speaker_jobs_onetap"
    assert S.jobs_dir("dev", "names", "one").name == "speaker_jobs_onetap_dev"
    assert S.jobs_dir("test", "names").name == "speaker_jobs_names"


def test_labelled_anchor_basis_is_accepted_only_when_asked() -> None:
    row = json.dumps(
        {"line": "T0000", "speaker": "Ben", "confidence": "high", "basis": "labelled_anchor"}
    )
    assert S.parse_predictions(row, 1)[0] == {}
    preds, errors = S.parse_predictions(row, 1, S.ONE_TAP_BASES)
    assert preds[0].basis == S.ANCHOR_BASIS and errors == []


def test_one_tap_prompt_is_v2_plus_labelled_lines() -> None:
    from pathlib import Path

    v3 = Path("prompts/speaker_naming.v3.md").read_text(encoding="utf-8")
    assert "labelled_anchor" in v3 and "Lines the user has labelled" in v3
    assert "T0009 -> Ilse Brandvold" in v3 and "Person A" in v3
    for ami in ("Agnes", "Christine", "Shrida", "remote control", "Industrial Designer"):
        assert ami not in v3
