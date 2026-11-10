"""Source pointers: truth maths, selection, click sheet, job file, parsers, scorer (no network,
no private data)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import zoom_score as SC
from tenor.evals import zoom as Z

ROWS = [
    {"line": "T0000", "n_words": 4, "acts": {"1": 4}},
    {"line": "T0001", "n_words": 10, "acts": {"1": 1, "2": 9}},
    {"line": "T0002", "n_words": 2, "acts": {"5": 1}},
    {"line": "T0003", "n_words": 6, "acts": {"7": 6}},
]
LINES = {
    f"T{i:04d}": Z.Line(f"T{i:04d}", "Them", s, e, "words")
    for i, (s, e) in enumerate([(0, 4), (4, 9), (100, 101), (300, 303)])
}


def _truth() -> dict[str, Any]:
    def rec(bid: str, links: tuple[int, ...]) -> dict[str, Any]:
        b = Z.parse_bullet(f"{bid} [section: Plan] text of {bid}")
        return Z.bullet_truth(b, {"D1": ("full", links)}, ROWS, LINES)

    return {
        "meta": {"truth_version": Z.TRUTH_VERSION, "seed": 42, "n_selected": 3},
        "meetings": {
            "M1": {"length_s": 400.0, "bullets": [rec("B1", (2,)), rec("B2", (5,))]},
            "M2": {"length_s": 400.0, "bullets": [rec("B3", (7,))]},
        },
    }


def test_true_lines_strong_and_ranges() -> None:
    found = Z.true_lines(ROWS, [1, 5])
    assert [(t.id, t.linked_words, t.strong) for t in found] == [
        ("T0000", 4, True),
        ("T0001", 1, False),
        ("T0002", 1, True),  # 1 of 2 words
    ]
    b = Z.bullet_truth(
        Z.parse_bullet("B3 [section: S] (sub-point of B1) x"),
        {"D1": ("partial", (1,)), "A1": ("full", (5, 99)), "P1": ("full", ())},
        ROWS,
        LINES,
    )
    assert b["parent"] == "B1" and b["gold_ids"] == ["A1", "D1"] and b["verdict"] == "full"
    assert b["true_lines"] == ["T0000", "T0001", "T0002"]
    assert b["true_ranges_s"] == [[0, 9], [100, 101]] and b["true_ranges"][1] == "01:40-01:41"
    assert (b["linked_acts"], b["linked_acts_found"]) == (3, 2)
    weak = Z.bullet_truth(
        Z.parse_bullet("B4 [section: S] y"), {"D1": ("full", (1,))}, ROWS[1:], LINES
    )
    assert weak["true_lines"] == ["T0001"] and not Z.eligible(weak)


def test_distance_and_chance() -> None:
    r = [(10.0, 20.0), (100.0, 110.0)]
    assert Z.distance(15, r) == 0 and Z.distance(5, r) == 5 and Z.distance(60, r) == 40
    assert Z.distance(1, []) == float("inf")
    assert Z.chance(r, 200, 10) == pytest.approx((30 + 30) / 200)
    assert Z.chance(r, 200, 50) == pytest.approx(160 / 200)  # windows merge, clipped at 0
    assert Z.chance(r, 0, 10) == 0


def test_select_is_stratified_seeded_and_prefers_full() -> None:
    def pool(n: int, full: int) -> list[dict[str, Any]]:
        return [
            {"id": f"B{i}", "verdict": "full" if i <= full else "partial"} for i in range(1, n + 1)
        ]

    pools = {"A": pool(10, 2), "B": pool(10, 10), "C": pool(2, 0)}
    got = Z.select(pools, 9, 42)
    assert {m: len(v) for m, v in got.items()} == {"A": 4, "B": 3, "C": 2}
    assert {"B1", "B2"} <= {r["id"] for r in got["A"]}
    assert [int(r["id"][1:]) for r in got["A"]] == sorted(int(r["id"][1:]) for r in got["A"])
    assert got == Z.select(pools, 9, 42) and Z.select(pools, 9, 43)["B"] != got["B"]
    assert sum(len(v) for v in Z.select(pools, 99, 42).values()) == 22


def test_sheet_round_trip_and_hides_truth() -> None:
    truth = _truth()
    truth["meetings"]["M1"]["bullets"][0]["text"] = "a | b"
    sheet = Z.render_sheet(truth, {})
    assert "T0001" not in sheet and "00:0" not in sheet and "D1" not in sheet
    assert '"AMI M1"' in sheet and "a \\| b" in sheet
    rows, errors = Z.parse_sheet(sheet)
    assert [(s.meeting, s.bullet_id, s.times) for s in rows] == [
        ("M1", "B1", None),
        ("M1", "B2", None),
        ("M2", "B3", None),
    ]
    filled = (
        sheet.replace("| a \\| b |  |", "| a \\| b | 00:05, 1:02:03 |")
        .replace("text of B2 |  |", "text of B2 | none |")
        .replace("text of B3 |  |", "text of B3 | 4pm |")
    )
    rows, errors = Z.parse_sheet(filled)
    assert rows[0].times == (5.0, 3723.0) and rows[1].times == () and rows[2].times is None
    assert errors == ["M2 B3: not a timestamp: '4pm'"]
    as_json = json.dumps(
        [
            {"meeting": "M1", "bullet_id": "B1", "timestamps": ["00:05"]},
            {"meeting": "M1", "bullet_id": "B2", "timestamps": []},
            {"meeting": "M2", "bullet_id": "B3", "timestamps": None},
        ]
    )
    assert [s.times for s in Z.parse_sheet(as_json)[0]] == [(5.0,), (), None]


def test_job_file_has_no_truth() -> None:
    job = Z.job_file(
        "M1", "PROMPT", ["B1 [section: Plan] x"], list(LINES.values()), ["B1"], "out/M1.jsonl"
    )
    assert "[T0002 Them 01:40] words" in job and job.rstrip().endswith("B1")
    assert SC.job_lines(job) == {"T0000": 0, "T0001": 4, "T0002": 100, "T0003": 300}
    prompt = Path("prompts/source_pointer.v1.md").read_text(encoding="utf-8")
    assert (
        '"bullet_id"' in prompt and "AMI" not in prompt and "remote control" not in prompt.lower()
    )


def test_parse_pointers() -> None:
    starts = {k: v.start for k, v in LINES.items()}
    text = "\n".join(
        [
            '{"bullet_id":"B1","lines":["T0001","T9999","T0001","T0002"],"confidence":"high"}',
            '{"bullet_id":"B1","lines":["T0003"],"confidence":"low"}',
            '{"bullet_id":"B9","lines":["T0003"],"confidence":"low"}',
            "not json",
            '{"bullet_id":"B2","lines":["T0003"],"confidence":"sure"}',
        ]
    )
    got, errors = Z.parse_pointers(text, "M1", ["B1", "B2", "B3"], starts)
    assert got[0] == Z.Shown("M1", "B1", (4, 100), ("T0001", "T0002"), "high")
    assert got[1].lines == ("T0003",) and got[1].confidence is None
    assert got[2] == Z.Shown("M1", "B3", ())
    assert len(errors) == 6


def test_score_summarise_compare(tmp_path: Path) -> None:
    truth = _truth()  # B1: T0001 [4, 9]; B2: T0002 [100, 101]; B3: T0003 [300, 303]
    run = tmp_path / "runs" / "r1"
    run.mkdir(parents=True)
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    for mid in truth["meetings"]:
        jobs.joinpath(f"{mid}.md").write_text(
            Z.job_file(mid, "P", [], list(LINES.values()), [], "x"), encoding="utf-8"
        )
    (run / "M1.jsonl").write_text(
        '{"bullet_id":"B1","lines":["T0003","T0001"],"confidence":"high"}\n'
        '{"bullet_id":"B2","lines":["T0000"],"confidence":"low"}\n',
        encoding="utf-8",
    )
    p_rows, errors = SC.score_pointers(truth, run, jobs)
    assert [(r["hit30"], r["first30"], r["inside"], r["n"]) for r in p_rows] == [
        (True, False, 1, 2),
        (False, False, 0, 1),
        (False, False, 0, 0),  # M2: no answer file, so wrong
    ]
    assert p_rows[1]["hit60"] is False and p_rows[1]["nearest_s"] == 100
    assert any("no answer file" in e for e in errors)
    s = Z.summarise(p_rows, 42)
    assert s["right_moment_30s"]["num"] == 1 and s["right_moment_30s"]["den"] == 3
    assert s["timestamps_inside_true"]["rate"] == pytest.approx(0.3333)
    assert s["by_confidence"]["high"]["rate"] == 1 and s["nothing_shown"] == 1
    assert s == Z.summarise(p_rows, 42)

    sheet = tmp_path / "sheet.json"
    sheet.write_text(
        json.dumps(
            [
                {"meeting": "M1", "bullet_id": "B1", "timestamps": ["00:45"]},
                {"meeting": "M1", "bullet_id": "B2", "timestamps": ["01:15", "05:00"]},
                {"meeting": "M2", "bullet_id": "B3", "timestamps": None},
                {"meeting": "M2", "bullet_id": "B8", "timestamps": ["00:01"]},
            ]
        ),
        encoding="utf-8",
    )
    z_rows, z_err = SC.score_zoomin(truth, sheet)
    assert [(r["bullet_id"], r["hit10"], r["hit30"], r["hit60"]) for r in z_rows] == [
        ("B1", False, False, True),
        ("B2", False, True, True),
    ]
    assert z_err == ["M2 B3: not measured"]
    c = Z.compare(p_rows, z_rows, 42)
    assert c["bullets"] == 2
    assert {k: c["30s"][k] for k in ("both", "only_pointers", "only_zoomin", "neither")} == {
        "both": 0,
        "only_pointers": 1,
        "only_zoomin": 1,
        "neither": 0,
    }
    assert c["30s"]["sign_test_p"] == 1.0 and c["30s"]["difference"]["rate"] == 0
    assert c["60s"]["only_zoomin"] == 1 and c["60s"]["both"] == 1
    assert Z.chance_baseline(truth)["30s"] == pytest.approx((39 + 61 + 63) / 3 / 400, abs=1e-4)

    args = ["--truth", str(_write(tmp_path, truth)), "--runs-dir", str(tmp_path / "runs")]
    SC.main(["compare", "--run", "r1", "--sheet", str(sheet), "--jobs", str(jobs), *args])
    out = json.loads((run / "compare.json").read_text())
    assert out["meta"]["seed"] == 42 and out["compare"]["bullets"] == 2 and out["meta"]["git_sha"]


def _write(tmp: Path, truth: dict[str, Any]) -> Path:
    path = tmp / "truth.json"
    path.write_text(json.dumps(truth), encoding="utf-8")
    return path
