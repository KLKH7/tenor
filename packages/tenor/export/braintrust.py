"""Braintrust-shaped rows from existing result files.

Pure functions: result data in, rows out. No network, no SDK import (the optional upload lives
in :mod:`tenor.export.braintrust_upload`). Only fields tagged VERIFIED in
``docs/engineering/SCHEMA_NOTES.md`` ("Braintrust export") are written: dataset rows
``{id, input, expected, metadata}`` and experiment rows
``{id, input, output, expected, scores, metadata}`` with every score in [0, 1].

No private text: rows hold meeting ids, item ids, labels and numbers. The loaders read only the
id and label fields of a private file; :func:`check_export` then runs the repo's committable
check over every row, with the file's free-text fields as forbidden strings.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tenor.blackbox.kit import check_committable

SCHEMA_VERSION = 1
Scalar = str | int | float | bool | None
Product = Literal["granola", "wispr"]
PRODUCTS: tuple[Product, ...] = ("granola", "wispr")
MEETING_ID = r"^[A-Z]{2}\d{4}[a-z]?$"  # AMI meeting ids (public corpus), e.g. ES2006a

_FROZEN = ConfigDict(extra="forbid", frozen=True)


class DatasetRow(BaseModel):
    """One Braintrust dataset record."""

    model_config = _FROZEN

    id: str
    input: dict[str, Scalar]
    expected: Scalar
    metadata: dict[str, Scalar]


class ExperimentRow(BaseModel):
    """One Braintrust experiment event. Scores are in [0, 1]; other numbers go in metadata."""

    model_config = _FROZEN

    id: str
    input: dict[str, Scalar]
    output: Scalar
    expected: Scalar
    scores: dict[str, float]
    metadata: dict[str, Scalar]


class SourceFile(BaseModel):
    """A file the export was built from."""

    model_config = _FROZEN

    path: str
    sha256: str


class Export(BaseModel):
    """Everything one ``tenor export braintrust`` run writes, before serialisation."""

    model_config = _FROZEN

    experiment: str
    experiment_name: str  # the name the experiment gets in Braintrust
    dataset_name: str
    run_metadata: dict[str, Scalar]  # git SHA, model, prompt version, seed, date of the run
    dataset: tuple[DatasetRow, ...]
    rows: tuple[ExperimentRow, ...]
    score_summary: dict[str, Any]
    sources: tuple[SourceFile, ...]
    forbidden_text: tuple[str, ...] = Field(default=(), exclude=True, repr=False)


# -- notes completeness (frozen agent reader) -------------------------------------------------

NotesVerdict = Literal["full", "partial", "no"]
CREDIT: dict[str, float] = {"full": 1.0, "partial": 0.5, "none": 0.0}
CATEGORY_OF = {"D": "decision", "A": "action", "P": "problem"}


class ReaderVerdict(BaseModel):
    """The id and label fields of one reader JSONL row; its free-text fields are not loaded."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    meeting: str = Field(pattern=MEETING_ID)
    product: Product
    gold_id: str = Field(pattern=r"^[DAP]\d+$")
    notes_verdict: NotesVerdict
    transcript_verdict: Literal["yes", "partly", "no"]
    confidence: Literal["high", "medium", "low"]


class ReaderRun(BaseModel):
    """What the exporter states about a reader run that the run's files do not."""

    model_config = _FROZEN

    label: str
    verdicts: str  # JSONL, one row per (meeting, product, gold item); private
    score_report: str  # the scorer's JSON for this run (git SHA and date of scoring); private
    reader_instructions: str
    judged_by: str
    model: str
    split: Literal["dev", "test"]


# The headline run. The score file's own ``reader_prompt`` field says notes_reader.v1 because the
# scorer's default was not overridden; the run used v2 (docs/tracks/NOTES.md, "Provenance").
READER_RUNS: dict[str, ReaderRun] = {
    "test_v2": ReaderRun(
        label="test_v2",
        verdicts="data/private/reader/runs/test_v2_all.jsonl",
        score_report="data/private/reader/runs/test_v2_score.json",
        reader_instructions="notes_reader.v2",
        judged_by="Claude Code agent reader",
        model="claude-opus-5-5",
        split="test",
    )
}
_TEXT_FIELDS = ("reason", "evidence_quote")


def parse_reader_jsonl(text: str) -> tuple[list[ReaderVerdict], list[str]]:
    """(verdicts, private strings) from a reader JSONL file. The private strings are the rows'
    free-text fields, kept only to check that none of them reaches an exported row."""
    verdicts: list[ReaderVerdict] = []
    private: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        verdicts.append(ReaderVerdict.model_validate(raw))
        private += [str(raw[f]) for f in _TEXT_FIELDS if raw.get(f)]
    return verdicts, private


def notes_completeness_export(
    verdicts: Sequence[ReaderVerdict],
    run: ReaderRun,
    *,
    git_sha: str,
    run_date: str,
    sources: Sequence[SourceFile] = (),
    forbidden_text: Sequence[str] = (),
) -> Export:
    """One row per gold item per product: did the product's notes cover the item?

    ``scores.coverage`` is 1 (full), 0.5 (partial) or 0 (none). ``git_sha`` and ``run_date`` are
    those of the scored run, not of the export."""
    ordered = sorted(
        verdicts, key=lambda v: (v.meeting, v.product, v.gold_id[0], int(v.gold_id[1:]))
    )
    ids = [f"{v.meeting}:{v.product}:{v.gold_id}" for v in ordered]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate (meeting, product, gold item) in the reader run")
    dataset: list[DatasetRow] = []
    rows: list[ExperimentRow] = []
    for rid, v in zip(ids, ordered, strict=True):
        label = "none" if v.notes_verdict == "no" else v.notes_verdict
        inp: dict[str, Scalar] = {
            "meeting_id": v.meeting,
            "product": v.product,
            "category": CATEGORY_OF[v.gold_id[0]],
            "item_id": v.gold_id,
        }
        dataset.append(
            DatasetRow(
                id=rid,
                input=inp,
                expected="full",
                metadata={"corpus": "AMI 1.6.2", "split": run.split},
            )
        )
        rows.append(
            ExperimentRow(
                id=rid,
                input=inp,
                output=label,
                expected="full",
                scores={"coverage": CREDIT[label]},
                metadata={
                    "reader_instructions": run.reader_instructions,
                    "run": run.label,
                    "judged_by": run.judged_by,
                    "model": run.model,
                    "git_sha": git_sha,
                    "split": run.split,
                    "in_product_transcript": v.transcript_verdict,
                    "reader_confidence": v.confidence,
                },
            )
        )
    return Export(
        experiment="notes-completeness",
        experiment_name=f"notes-completeness.{run.label}@{git_sha[:7]}",
        dataset_name="ami-notes-completeness-items",
        run_metadata={
            "git_sha": git_sha,
            "model": run.model,
            "prompt_version": run.reader_instructions,
            "seed": None,  # agent reader sessions are not seeded
            "date": run_date,
            "run": run.label,
            "judged_by": run.judged_by,
            "split": run.split,
        },
        dataset=tuple(dataset),
        rows=tuple(rows),
        score_summary=summarise(rows, "coverage", group="meeting_id"),
        sources=tuple(sources),
        forbidden_text=tuple(forbidden_text),
    )


# -- transcript coverage (zero-LLM, from the committed report) --------------------------------


class _ProductCoverage(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    windows: tuple[tuple[float, float], ...]  # (window start in seconds, bigram share)


class _MeetingCoverage(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    meeting_id: str = Field(pattern=MEETING_ID)
    products: dict[Product, _ProductCoverage | None]


class _CoverageMeta(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    git_sha: str
    date: str
    window_s: float
    threshold: float
    normaliser_version: int


class CoverageReport(BaseModel):
    """The parts of ``evals/reports/ami/coverage.json`` the export uses."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    meta: _CoverageMeta
    meetings: tuple[_MeetingCoverage, ...]


def transcript_coverage_export(
    report: CoverageReport, *, sources: Sequence[SourceFile] = ()
) -> Export:
    """One row per window of AMI's human transcript per product: did the product's transcript
    capture it? ``scores.captured`` is 1 when the window's bigram share reaches the report's
    threshold, else 0; the share itself is in ``metadata``."""
    meta = report.meta
    dataset: list[DatasetRow] = []
    rows: list[ExperimentRow] = []
    for meeting in report.meetings:
        for product in PRODUCTS:
            cov = meeting.products.get(product)
            for n, (start_s, share) in enumerate(cov.windows if cov else ()):
                rid = f"{meeting.meeting_id}:{product}:w{n:04d}"
                inp: dict[str, Scalar] = {
                    "meeting_id": meeting.meeting_id,
                    "product": product,
                    "window": n,
                    "window_start_s": start_s,
                }
                captured = share >= meta.threshold
                dataset.append(
                    DatasetRow(
                        id=rid,
                        input=inp,
                        expected="captured",
                        metadata={"corpus": "AMI 1.6.2", "window_s": meta.window_s},
                    )
                )
                rows.append(
                    ExperimentRow(
                        id=rid,
                        input=inp,
                        output="captured" if captured else "missed",
                        expected="captured",
                        scores={"captured": 1.0 if captured else 0.0},
                        metadata={
                            "bigram_share": share,
                            "threshold": meta.threshold,
                            "normaliser_version": meta.normaliser_version,
                            "judged_by": "code (no LLM)",
                            "git_sha": meta.git_sha,
                        },
                    )
                )
    return Export(
        experiment="transcript-coverage",
        experiment_name=f"transcript-coverage@{meta.git_sha[:7]}",
        dataset_name="ami-transcript-coverage-windows",
        run_metadata={
            "git_sha": meta.git_sha,
            "model": None,  # no LLM in this measurement
            "prompt_version": None,
            "seed": None,  # deterministic, no sampling
            "date": meta.date,
            "judged_by": "code (no LLM)",
        },
        dataset=tuple(dataset),
        rows=tuple(rows),
        score_summary=summarise(rows, "captured", group="meeting_id"),
        sources=tuple(sources),
    )


# -- shared -----------------------------------------------------------------------------------


def summarise(rows: Sequence[ExperimentRow], score: str, *, group: str) -> dict[str, Any]:
    """Per product: n, the mean over rows (what a Braintrust score column averages) and the mean
    of per-``group`` means (how the README reports means over meetings). Rounded to 4 places."""
    by_product: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_product[str(r.input["product"])][str(r.input[group])].append(r.scores[score])
    out: dict[str, Any] = {}
    for product, groups in sorted(by_product.items()):
        values = [x for g in groups.values() for x in g]
        out[product] = {
            "n": len(values),
            "groups": len(groups),
            "mean_over_rows": round(sum(values) / len(values), 4),
            f"mean_over_{group}": round(
                sum(sum(g) / len(g) for g in groups.values()) / len(groups), 4
            ),
        }
    return {score: out}


def check_export(export: Export) -> None:
    """Raise if a row breaks the export contract: a score outside [0, 1], dataset and experiment
    rows that do not pair up, or content that the committable check rejects (a long string, a
    per-question key, or any of the source's private strings)."""
    if [d.id for d in export.dataset] != [r.id for r in export.rows]:
        raise ValueError("dataset and experiment rows do not pair up by id")
    for row in export.rows:
        for name, value in row.scores.items():
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{row.id}: score {name}={value} is outside [0, 1]")
    payload = [r.model_dump() for r in (*export.dataset, *export.rows)]
    check_committable(payload, export.forbidden_text)
    check_committable(export.run_metadata, export.forbidden_text)


def file_sha256(path: Path) -> str:
    """Hex SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _notes_completeness(root: Path) -> Export:
    run = READER_RUNS["test_v2"]
    verdicts, private = parse_reader_jsonl((root / run.verdicts).read_text())
    scored = json.loads((root / run.score_report).read_text())["meta"]
    return notes_completeness_export(
        verdicts,
        run,
        git_sha=str(scored["git"]),
        run_date=str(scored["date"]),
        sources=[
            SourceFile(path=p, sha256=file_sha256(root / p))
            for p in (run.verdicts, run.score_report)
        ],
        forbidden_text=private,
    )


COVERAGE_REPORT = "evals/reports/ami/coverage.json"


def _transcript_coverage(root: Path) -> Export:
    path = root / COVERAGE_REPORT
    report = CoverageReport.model_validate_json(path.read_text())
    return transcript_coverage_export(
        report, sources=[SourceFile(path=COVERAGE_REPORT, sha256=file_sha256(path))]
    )


EXPERIMENTS: Mapping[str, Callable[[Path], Export]] = {
    "notes-completeness": _notes_completeness,
    "transcript-coverage": _transcript_coverage,
}


def build(experiment: str, root: Path = Path(".")) -> Export:
    """The checked export for a named experiment, read from its result files under ``root``."""
    if experiment not in EXPERIMENTS:
        raise KeyError(f"unknown experiment {experiment!r}; known: {', '.join(EXPERIMENTS)}")
    export = EXPERIMENTS[experiment](root)
    check_export(export)
    return export


def manifest(export: Export, *, date: str, git_sha: str) -> dict[str, Any]:
    """The manifest for a written export. ``date`` and ``git_sha`` describe the export itself;
    the scored run's own SHA and date are under ``run``."""
    return {
        "schema_version": SCHEMA_VERSION,
        "format": "braintrust-jsonl",
        "experiment": export.experiment,
        "experiment_name": export.experiment_name,
        "dataset_name": export.dataset_name,
        "date": date,
        "git_sha": git_sha,
        "run": export.run_metadata,
        "counts": {"dataset": len(export.dataset), "experiment": len(export.rows)},
        "score_summary": export.score_summary,
        "sources": [s.model_dump() for s in export.sources],
        "private_text": "none: ids, labels and numbers only",
    }


def _jsonl(rows: Sequence[BaseModel]) -> str:
    return "".join(
        json.dumps(r.model_dump(), sort_keys=True, ensure_ascii=False) + "\n" for r in rows
    )


def write(export: Export, out: Path, *, date: str, git_sha: str) -> dict[str, Any]:
    """Write ``dataset.jsonl``, ``experiment.jsonl`` and ``manifest.json`` under ``out`` and
    return the manifest. The same export, date and SHA give byte-identical files."""
    check_export(export)
    man = manifest(export, date=date, git_sha=git_sha)
    check_committable(man, export.forbidden_text)
    out.mkdir(parents=True, exist_ok=True)
    (out / "dataset.jsonl").write_text(_jsonl(export.dataset))
    (out / "experiment.jsonl").write_text(_jsonl(export.rows))
    (out / "manifest.json").write_text(json.dumps(man, indent=1, sort_keys=True) + "\n")
    return man
