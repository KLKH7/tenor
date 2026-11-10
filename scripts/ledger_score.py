"""Score a meeting-ledger run from Claude Code reader verdicts (``prompts/e4_reader.v2.md``).

    uv run --no-sync python scripts/ledger_score.py --run <RUN> \
        --runs data/private/reader/runs/ledger_a data/private/reader/runs/ledger_b \
               data/private/reader/runs/ledger_c \
        [--notes data/private/reader/runs/test_consensus3.jsonl] [--seed 42] [--out PATH]

Reads every ``*.jsonl`` of each reader run, drops repeated lines within a run, and takes the
consensus per item (median ``true`` and ``in_notes``, majority kind, gold ids matched in >= 2
runs at the median level). Then, per product and pooled, with bootstrap 95% CIs that resample
meetings: entries per unit; true yes / partly / no; the ledger's coverage of all gold (full = 1,
partial = 0.5, best single entry per gold item, mean per unit then over units; overall and
D / A / P) next to the notes' coverage with the same credit from ``--notes`` (the E3 readers'
verdict on the notes as a whole); of the gold the notes miss
how much the ledger states (full / incl. partly, by notes-partial and notes-no); entries already
in the notes; true rate by ledger status; owners of action entries. Writes a JSON report (next
to the key unless ``--out``) and prints a Markdown table. Local data only: no LLM, no network.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "packages")]

from tenor.evals import e4_reader as R  # noqa: E402
from tenor.evals import ledger as L  # noqa: E402

READERS = ROOT / "data/private/reader"
NOTES = READERS / "runs/test_consensus3.jsonl"


def run_files(run: Path) -> list[Path]:
    return sorted(run.glob("*.jsonl")) if run.is_dir() else [run]


def read_run(run: Path) -> tuple[dict[str, dict[str, R.ReaderLine]], list[str], int]:
    """One reader run: lines by unit and K id (repeats dropped), line errors, repeats."""
    lines: list[R.ReaderLine] = []
    errors: list[str] = []
    for f in run_files(run):
        got, errs = L.read_reader_lines(f.read_text(encoding="utf-8"))
        lines += got
        errors += [f"{run.name}/{f.name} {e}" for e in errs]
    by_unit, repeats = L.dedupe(lines)
    return by_unit, errors, repeats


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, cwd=ROOT
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip()


def run_score(key_path: Path, runs: Sequence[Path], notes_path: Path, seed: int) -> dict[str, Any]:
    """The report for one key and its reader runs."""
    key = json.loads(key_path.read_text(encoding="utf-8"))
    notes = L.notes_by_unit(
        json.loads(x) for x in notes_path.read_text(encoding="utf-8").splitlines() if x.strip()
    )
    read = [read_run(r) for r in runs]
    verdicts, problems = L.consensus(key["units"], [r[0] for r in read])
    result = L.score(key["units"], verdicts, notes, seed)
    result["consensus"] = {
        uk: {k: dataclasses.asdict(v) for k, v in per.items()} for uk, per in verdicts.items()
    }
    result["meta"] = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_sha": git_sha(),
        "seed": seed,
        "reader_prompt": L.READER_PROMPT,
        "reader_model": "Claude Code agents (model not recorded)",
        "key": key["meta"],
        "reader_runs": [str(r) for r in runs],
        "repeated_lines_dropped": {str(r): x[2] for r, x in zip(runs, read, strict=True)},
        "line_errors": [e for x in read for e in x[1]],
        "unit_problems": problems,
        "unanimous": L.agreement(key["units"], [r[0] for r in read]),
        "units_without_ledger": key["meta"].get("units_without_output", []),
        "inputs": {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()[:16]
            for p in [key_path, notes_path, *(f for r in runs for f in run_files(r))]
        },
    }
    return result


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--runs", type=Path, nargs="+", required=True)
    ap.add_argument("--notes", type=Path, default=NOTES)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    key_path = READERS / "ledger_keys" / f"{a.run}.key.json"
    result = run_score(key_path, a.runs, a.notes, a.seed)
    out = a.out or key_path.with_name(f"{a.run}.score.json")
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    meta = result["meta"]
    for e in meta["line_errors"]:
        print(f"invalid: {e}", file=sys.stderr)
    for uk, ps in meta["unit_problems"].items():
        print(f"not scored {uk}: {'; '.join(ps[:5])}", file=sys.stderr)
    for uk in meta["units_without_ledger"]:
        print(f"not scored {uk}: the generator wrote no ledger (not in the key)", file=sys.stderr)
    for uk, why in result["units_not_scored"].items():
        if uk not in meta["unit_problems"]:
            print(f"not scored {uk}: {why}", file=sys.stderr)
    print("\n".join(L.table(result["summary"])))
    un = meta["unanimous"]
    print(f"\nunanimous over {un['items']} items read by every run: " + ", ".join(
        f"{k} {v}" for k, v in un.items() if k != "items"))  # fmt: skip
    print(f"{len(result['per_unit'])} units scored, {len(result['units_not_scored'])} not; {out}")


if __name__ == "__main__":
    main()
