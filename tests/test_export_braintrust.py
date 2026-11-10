"""Braintrust export: row shapes, score range, no private text, determinism, upload refusal."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tenor.blackbox.kit import NotCommittable
from tenor.cli import app
from tenor.export import braintrust as B
from tenor.export.braintrust_upload import KEY_ENV, UploadRefused, upload

SECRET_REASON = "The chair said the casing should be made of recycled rubber."
SECRET_QUOTE = 'B4 "Casing: recycled rubber, decided after a long discussion"'
RUN = B.READER_RUNS["test_v2"]
DATASET_FIELDS = {"id", "input", "expected", "metadata"}  # VERIFIED dataset record fields
EVENT_FIELDS = DATASET_FIELDS | {"output", "scores"}  # VERIFIED experiment event fields


def _line(meeting: str, product: str, gold: str, notes: str, transcript: str = "yes") -> str:
    return json.dumps(
        {
            "meeting": meeting,
            "product": product,
            "gold_id": gold,
            "notes_verdict": notes,
            "transcript_verdict": transcript,
            "evidence_bullets": ["B4"],
            "evidence_quote": SECRET_QUOTE,
            "reason": SECRET_REASON,
            "confidence": "high",
        }
    )


READER_JSONL = "\n".join(
    [
        _line("ES2006a", "wispr", "D1", "full"),
        _line("ES2006a", "granola", "A2", "no", "partly"),
        _line("ES2006a", "granola", "D1", "full"),
        _line("ES2006a", "granola", "A10", "partial"),
        _line("IB4003", "granola", "P1", "partial"),
        _line("IB4003", "wispr", "P1", "no", "no"),
    ]
)
COVERAGE = {
    "meta": {
        "git_sha": "972da68",
        "date": "2026-11-05T19:56:39+00:00",
        "window_s": 15.0,
        "threshold": 0.4,
        "normaliser_version": 1,
        "method": "x" * 300,  # long free text in the report must not reach a row
    },
    "meetings": [
        {
            "meeting_id": "IS1008a",
            "products": {
                "granola": {"windows": [[0.0, 0.9], [15.0, 0.4], [30.0, 0.39]], "coverage": 0.667},
                "wispr": {"windows": [[0.0, 1.0]], "coverage": 1.0},
            },
        },
        {"meeting_id": "IB4003", "products": {"granola": {"windows": [[5.5, 0.1]]}, "wispr": None}},
    ],
}


def _notes() -> B.Export:
    verdicts, private = B.parse_reader_jsonl(READER_JSONL)
    return B.notes_completeness_export(
        verdicts, RUN, git_sha="03360ac", run_date="2026-11-09T08:58:32Z", forbidden_text=private
    )


def _coverage() -> B.Export:
    return B.transcript_coverage_export(B.CoverageReport.model_validate(COVERAGE))


def _root(tmp_path: Path) -> Path:
    """A repo-shaped folder holding the two source files."""
    for rel, text in (
        (RUN.verdicts, READER_JSONL),
        (RUN.score_report, json.dumps({"meta": {"git": "03360ac", "date": "2026-11-09"}})),
        (B.COVERAGE_REPORT, json.dumps(COVERAGE)),
    ):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    return tmp_path


def test_notes_rows_have_the_verified_shape() -> None:
    e = _notes()
    assert [r.id for r in e.rows] == [
        "ES2006a:granola:A2",
        "ES2006a:granola:A10",
        "ES2006a:granola:D1",
        "ES2006a:wispr:D1",
        "IB4003:granola:P1",
        "IB4003:wispr:P1",
    ]
    first = e.rows[0].model_dump()
    assert set(first) == EVENT_FIELDS
    assert set(e.dataset[0].model_dump()) == DATASET_FIELDS
    assert first["input"] == {
        "meeting_id": "ES2006a",
        "product": "granola",
        "category": "action",
        "item_id": "A2",
    }
    assert (first["expected"], first["output"], first["scores"]) == (
        "full",
        "none",
        {"coverage": 0.0},
    )
    assert first["metadata"]["reader_instructions"] == "notes_reader.v2"
    assert first["metadata"]["run"] == "test_v2"
    assert first["metadata"]["git_sha"] == "03360ac"
    assert first["metadata"]["in_product_transcript"] == "partly"
    assert [r.scores["coverage"] for r in e.rows] == [0.0, 0.5, 1.0, 1.0, 0.5, 0.0]
    assert [d.input for d in e.dataset] == [r.input for r in e.rows]


def test_notes_summary_reports_both_means() -> None:
    s = _notes().score_summary["coverage"]["granola"]
    # rows: 0, 0.5, 1 (ES2006a) and 0.5 (IB4003)
    assert s == {"n": 4, "groups": 2, "mean_over_rows": 0.5, "mean_over_meeting_id": 0.5}
    w = _notes().score_summary["coverage"]["wispr"]
    assert (w["mean_over_rows"], w["mean_over_meeting_id"]) == (0.5, 0.5)


def test_coverage_rows_and_threshold() -> None:
    e = _coverage()
    assert [(r.id, r.output, r.scores["captured"]) for r in e.rows] == [
        ("IS1008a:granola:w0000", "captured", 1.0),
        ("IS1008a:granola:w0001", "captured", 1.0),  # share == threshold counts as captured
        ("IS1008a:granola:w0002", "missed", 0.0),
        ("IS1008a:wispr:w0000", "captured", 1.0),
        ("IB4003:granola:w0000", "missed", 0.0),
    ]
    assert set(e.rows[0].model_dump()) == EVENT_FIELDS
    assert e.rows[2].metadata["bigram_share"] == 0.39
    g = e.score_summary["captured"]["granola"]
    assert (g["n"], g["mean_over_rows"], g["mean_over_meeting_id"]) == (4, 0.5, 0.3333)
    assert e.run_metadata["git_sha"] == "972da68"


@pytest.mark.parametrize("make", [_notes, _coverage])
def test_scores_in_unit_interval_and_no_private_text(make: Any) -> None:
    e = make()
    B.check_export(e)
    assert all(0.0 <= v <= 1.0 for r in e.rows for v in r.scores.values())
    blob = json.dumps([r.model_dump() for r in (*e.dataset, *e.rows)]) + e.model_dump_json()
    for private in (SECRET_REASON, SECRET_QUOTE, "recycled rubber", "x" * 121):
        assert private not in blob


def test_check_export_rejects_private_text_and_bad_scores() -> None:
    e = _notes()
    leaked = e.rows[0].model_copy(update={"metadata": {"note": SECRET_REASON}})
    with pytest.raises(NotCommittable):
        B.check_export(e.model_copy(update={"rows": (leaked, *e.rows[1:])}))
    over = e.rows[0].model_copy(update={"scores": {"coverage": 1.5}})
    with pytest.raises(ValueError, match="outside"):
        B.check_export(e.model_copy(update={"rows": (over, *e.rows[1:])}))
    with pytest.raises(ValueError, match="pair up"):
        B.check_export(e.model_copy(update={"rows": e.rows[1:]}))


def test_reader_rows_with_unknown_labels_or_ids_are_rejected() -> None:
    with pytest.raises(ValueError):
        B.parse_reader_jsonl(_line("ES2006a", "granola", "D1", "mostly"))
    with pytest.raises(ValueError):
        B.parse_reader_jsonl(_line("a sentence, not an id", "granola", "D1", "full"))
    verdicts, _ = B.parse_reader_jsonl(READER_JSONL + "\n" + _line("IB4003", "wispr", "P1", "full"))
    with pytest.raises(ValueError, match="duplicate"):
        B.notes_completeness_export(verdicts, RUN, git_sha="x", run_date="d")


@pytest.mark.parametrize("name", list(B.EXPERIMENTS))
def test_same_input_gives_byte_identical_files(tmp_path: Path, name: str) -> None:
    root = _root(tmp_path)
    outs = []
    for d in ("a", "b"):
        man = B.write(B.build(name, root), tmp_path / d, date="2026-11-10", git_sha="abc1234")
        outs.append({p.name: p.read_bytes() for p in sorted((tmp_path / d).iterdir())})
    assert outs[0] == outs[1]
    assert set(outs[0]) == {"dataset.jsonl", "experiment.jsonl", "manifest.json"}
    written = json.loads(outs[0]["manifest.json"])
    assert written == man
    assert written["schema_version"] == B.SCHEMA_VERSION
    assert written["counts"]["dataset"] == written["counts"]["experiment"] > 0
    assert (written["date"], written["git_sha"]) == ("2026-11-10", "abc1234")
    assert all(len(s["sha256"]) == 64 for s in written["sources"])
    lines = outs[0]["experiment.jsonl"].decode().splitlines()
    assert len(lines) == written["counts"]["experiment"]
    assert all(set(json.loads(x)) == EVENT_FIELDS for x in lines)
    for blob in outs[0].values():
        assert b"recycled rubber" not in blob


def test_build_rejects_unknown_experiment(tmp_path: Path) -> None:
    with pytest.raises(KeyError, match="unknown experiment"):
        B.build("nope", tmp_path)


class _FakeSDK:
    """Records calls in the shape of the SDK functions the upload uses."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def init_dataset(self, **kw: Any) -> _FakeSDK:
        self.calls.append(("init_dataset", kw))
        return self

    def init(self, **kw: Any) -> _FakeSDK:
        self.calls.append(("init", kw))
        return self

    def insert(self, **kw: Any) -> None:
        self.calls.append(("insert", kw))

    def log(self, **kw: Any) -> None:
        self.calls.append(("log", kw))

    def flush(self) -> None:
        self.calls.append(("flush", {}))

    def summarize(self) -> str:
        return "summary"


def _no_sdk() -> Any:
    raise ImportError("No module named 'braintrust'")


def test_upload_refuses_without_key() -> None:
    sdk = _FakeSDK()
    for env in ({}, {KEY_ENV: "  "}):
        with pytest.raises(UploadRefused, match=KEY_ENV):
            upload(_notes(), "TENOR", env=env, load_sdk=lambda: sdk)
    assert sdk.calls == []


def test_upload_refuses_without_sdk_and_never_shows_the_key() -> None:
    with pytest.raises(UploadRefused, match="not installed") as exc:
        upload(_notes(), "TENOR", env={KEY_ENV: "sk-test-value"}, load_sdk=_no_sdk)
    assert "sk-test-value" not in str(exc.value)


def test_upload_sends_only_row_fields_to_a_fake_sdk() -> None:
    sdk, e = _FakeSDK(), _notes()
    assert upload(e, "P", env={KEY_ENV: "sk-test-value"}, load_sdk=lambda: sdk) == "summary"
    names = [c for c, _ in sdk.calls]
    assert names == ["init_dataset", *["insert"] * 6, "flush", "init", *["log"] * 6]
    assert set(sdk.calls[1][1]) == DATASET_FIELDS
    assert set(sdk.calls[-1][1]) == EVENT_FIELDS
    assert sdk.calls[8][1]["experiment"] == e.experiment_name
    assert "sk-test-value" not in repr(sdk.calls)


def test_cli_writes_files_and_refuses_upload_without_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(_root(tmp_path))
    monkeypatch.delenv(KEY_ENV, raising=False)
    runner = CliRunner()
    args = ["export", "braintrust", "--experiment", "notes-completeness", "--out", "out"]
    ok = runner.invoke(app, args)
    assert ok.exit_code == 0, ok.output
    assert "6 dataset rows, 6 experiment rows" in ok.output
    assert (tmp_path / "out" / "manifest.json").exists()
    refused = runner.invoke(app, [*args, "--upload"])
    assert refused.exit_code == 1
    assert "upload refused" in refused.output and KEY_ENV in refused.output
    bad = runner.invoke(app, ["export", "braintrust", "--experiment", "nope", "--out", "out"])
    assert bad.exit_code != 0


def test_committed_coverage_report_matches_its_published_summary() -> None:
    """The real committed report: per-window rows average to the report's own pooled summary."""
    path = Path(B.COVERAGE_REPORT)
    e = B.build("transcript-coverage")
    published = json.loads(path.read_text())["summary"]
    for product, s in e.score_summary["captured"].items():
        assert round(s["mean_over_rows"], 3) == published[product]["coverage"]
