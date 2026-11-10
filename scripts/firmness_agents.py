"""Firmness check answered by Claude Code agents (no API).

    uv run --no-sync python scripts/firmness_agents.py dump [--split dev|test]
    uv run --no-sync python scripts/firmness_agents.py ingest --run <run_id> [--split ...] \
        [--with-cues <cue_run_id>]

``dump`` writes one job file per unit of the split (DEV: ``data/private/reader/firmness_jobs_dev/``;
TEST: ``firmness_jobs/``): ``prompts/firmness_check.v1.md``, every notes bullet, then per bullet
its parents and its widened span, the same jobs as the bullet cues (TEST: hand maps; DEV
IS1008a: ``overlap_align``, the word-overlap window the cue pilot used, since DEV has no hand
map). An agent answers each bullet with one JSON line in the
:class:`tenor.evals.firmness.FirmOut` shape plus ``bullet_id``, into
``firmness_agent_runs/<run>/<meeting>__<product>.jsonl``.

``ingest`` validates the lines, turns every flag into a reader candidate (text = ``flag_text``,
kind from ``meeting_status``, quote checked against the span) and writes condition-blind reader
packets (``cue_packets/<run>/``, read with ``prompts/e4_reader.v2.md``), the key
(``cue_keys/<run>.key.json``) and the local rows. ``--with-cues`` pools an agent cue run's rows
(``cue_agent_runs/<id>/rows.json``) into the same packets, so readers score both blind.
Local data only: no LLM, no network.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tenor.evals import bullet_cues as B
from tenor.evals import coverage_e4 as C4
from tenor.evals import firmness as F

READERS = Path("data/private/reader")
MAPS = Path("data/private/feature_tests/map")
OUT = READERS / "firmness_agent_runs"
CUE_RUNS = READERS / "cue_agent_runs"


def jobs_dir(split: str) -> Path:
    return READERS / ("firmness_jobs" if split == "test" else f"firmness_jobs_{split}")


def setup(split: str) -> tuple[list[B.CueUnit], dict[str, list[B.Job]]]:
    units, align = B.split_units(READERS / "packets", MAPS, split, C4.TEST_MEETINGS)
    return units, {u.key: B.jobs_for(u, align[u.key]) for u in units}


def dump(split: str) -> None:
    units, jobs = setup(split)
    out = jobs_dir(split)
    out.mkdir(parents=True, exist_ok=True)
    text = Path(f"prompts/{F.PROMPT}.md").read_text(encoding="utf-8")
    for u in units:
        body = B.job_file(u, jobs[u.key], text, "Firmness jobs")
        (out / f"{u.meeting_id}__{u.product}.md").write_text(body, encoding="utf-8")
        print(f"{u.key}: {len(jobs[u.key])} jobs")
    align = "hand maps" if split == "test" else "overlap_align (word overlap; no hand map on DEV)"
    print(f"{sum(map(len, jobs.values()))} jobs in {out} (prompt {F.PROMPT}; alignment {align})")


def ingest(run: str, seed: int, split: str, with_cues: str | None) -> None:
    units, jobs = setup(split)
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for u in units:
        path = OUT / run / f"{u.meeting_id}__{u.product}.jsonl"
        if not path.exists():
            errors.append(f"{u.key}: no output")
            continue
        r, e = F.ingest_unit(u, jobs[u.key], path.read_text(encoding="utf-8"))
        rows += r
        errors += e
    pooled = list(rows)
    models = [F.CONDITION]
    if with_cues:
        keys = {u.key for u in units}
        cue_rows = json.loads((CUE_RUNS / with_cues / "rows.json").read_text(encoding="utf-8"))
        pooled += [r for r in cue_rows if r["unit"] in keys]
        models.append("agent")
    texts, key_units = B.build_packets(units, pooled, {}, seed)
    pdir = READERS / "cue_packets" / run
    pdir.mkdir(parents=True, exist_ok=True)
    for name, text in texts.items():
        (pdir / name).write_text(text, encoding="utf-8")
    kdir = READERS / "cue_keys"
    kdir.mkdir(parents=True, exist_ok=True)
    key = {"meta": {"key_version": B.KEY_VERSION, "packet_version": B.PACKET_VERSION,
                    "reader_prompt": B.READER_PROMPT, "seed": seed, "run_id": run,
                    "split": split, "models": models, "prompt": F.PROMPT,
                    "with_cues": with_cues,
                    "alignment": "hand maps" if split == "test" else "overlap_align",
                    "date": datetime.now(UTC).isoformat(timespec="seconds")},
           "units": key_units}  # fmt: skip
    (kdir / f"{run}.key.json").write_text(json.dumps(key, indent=1, ensure_ascii=False) + "\n")
    (OUT / run).mkdir(parents=True, exist_ok=True)
    (OUT / run / "rows.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps(F.summary(rows), indent=1))
    print(f"packets {pdir}; items to read: {sum(len(u['items']) for u in key_units.values())}")
    for e in errors:
        print("ERROR", e)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("dump", "ingest"):
        p = sub.add_parser(name)
        p.add_argument("--split", choices=("dev", "test"), default="dev")
        if name == "ingest":
            p.add_argument("--run", required=True)
            p.add_argument("--seed", type=int, default=42)
            p.add_argument("--with-cues", default=None)
    a = ap.parse_args()
    if a.cmd == "dump":
        dump(a.split)
    else:
        ingest(a.run, a.seed, a.split, a.with_cues)
