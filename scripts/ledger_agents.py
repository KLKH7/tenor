"""Meeting ledger built by Claude Code agents (no API).

    uv run --no-sync python scripts/ledger_agents.py dump --split dev|test \
        [--prompt meeting_ledger.v1]
    uv run --no-sync python scripts/ledger_agents.py ingest --split dev|test --run <RUN>

``dump`` writes one job file per unit of the split (DEV IS1008a:
``data/private/reader/ledger_jobs_dev/``; TEST, the 11 meetings of
``tenor.evals.coverage_e4.TEST_MEETINGS``: ``ledger_jobs/``): the prompt, a simulated attendee
list (the packet's section 1 roster; ``no attendee list`` when there is none) and the product's
transcript in segments. No notes, no gold, no AMI human transcript. Deterministic.

An agent answers each job with ``ledger_agent_runs/<RUN>/<meeting>__<product>.jsonl`` (one
``tenor.evals.ledger.Entry`` per line).

``ingest`` validates the lines, checks every quote against its T line (entries with no valid
quote are dropped and counted), and writes condition-blind reader packets
(``ledger_packets/<RUN>/``, read with ``prompts/e4_reader.v2.md``) and the private key
(``ledger_keys/<RUN>.key.json``). It fails if a unit has valid entries but no packet items.
Local data only: no LLM, no network.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "packages")]

from tenor.evals import bullet_cues as B  # noqa: E402
from tenor.evals import coverage_e4 as C4  # noqa: E402
from tenor.evals import ledger as L  # noqa: E402

READERS = Path("data/private/reader")


def jobs_dir(readers: Path, split: str) -> Path:
    return readers / ("ledger_jobs" if split == "test" else f"ledger_jobs_{split}")


def units(readers: Path, split: str) -> list[B.CueUnit]:
    """The split's units that have a packet: DEV IS1008a, TEST the 11 E4 meetings."""
    meetings = [B.PILOT_MEETING] if split == "dev" else list(C4.TEST_MEETINGS)
    return [u for u in B.load_units(readers / "packets", meetings) if u.split == split]


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, cwd=ROOT
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip()


def dump(split: str, prompt: str, readers: Path = READERS, prompts: Path = ROOT / "prompts") -> int:
    """Write the split's job files; returns how many."""
    text = (prompts / f"{prompt}.md").read_text(encoding="utf-8")
    out = jobs_dir(readers, split)
    out.mkdir(parents=True, exist_ok=True)
    us = units(readers, split)
    for u in us:
        (out / f"{u.meeting_id}__{u.product}.md").write_text(L.job_file(u, text), encoding="utf-8")
        who = L.attendees(u.packet)
        print(
            f"{u.key}: {len(u.transcript)} lines, {len(L.segments(u))} segments, "
            f"{'no attendee list' if who is None else f'{len(who)} attendees'}"
        )
    print(f"{len(us)} jobs in {out} (prompt {prompt})")
    return len(us)


def ingest(
    split: str, run: str, seed: int = 42, model: str = "", readers: Path = READERS
) -> dict[str, Any]:
    """Validate a run's answers and write the reader packets and the key; returns the counts."""
    pdir = readers / "ledger_packets" / run
    kdir = readers / "ledger_keys"
    pdir.mkdir(parents=True, exist_ok=True)
    kdir.mkdir(parents=True, exist_ok=True)
    key_units: dict[str, Any] = {}
    totals = dict.fromkeys((*L.STATS, "items"), 0)
    errors: list[str] = []
    missing: list[str] = []
    for u in units(readers, split):
        path = readers / "ledger_agent_runs" / run / f"{u.meeting_id}__{u.product}.jsonl"
        if not path.exists():
            missing.append(u.key)
            continue
        entries, stats, errs = L.ingest_unit(u, path.read_text(encoding="utf-8"))
        errors += errs
        text, key = L.build_packet(u, entries, seed)
        if entries and not key["items"]:
            raise SystemExit(f"{u.key}: {len(entries)} valid entries but 0 packet items")
        (pdir / f"{u.meeting_id}__{u.product}.md").write_text(text, encoding="utf-8")
        key_units[u.key] = key | {"stats": stats}
        for k in L.STATS:
            totals[k] += stats[k]
        totals["items"] += len(key["items"])
        print(f"{u.key}: {stats} -> {len(key['items'])} items")
    meta = {
        "key_version": L.KEY_VERSION,
        "packet_version": L.PACKET_VERSION,
        "reader_prompt": L.READER_PROMPT,
        "prompt": L.PROMPT,
        "run_id": run,
        "split": split,
        "seed": seed,
        "model": model or "Claude Code agent (model not recorded)",
        "git_sha": git_sha(),
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "units_without_output": missing,
        "totals": totals,
    }
    (kdir / f"{run}.key.json").write_text(L.key_json(meta, key_units), encoding="utf-8")
    for e in errors:
        print("DROPPED", e)
    for m in missing:
        print(f"ERROR {m}: no output file")
    print(f"totals {totals}")
    print(f"{len(key_units)} packets in {pdir}; key {kdir / f'{run}.key.json'}")
    return {"totals": totals, "missing": missing, "units": len(key_units)}


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("--split", choices=("dev", "test"), required=True)
    d.add_argument("--prompt", default=L.PROMPT)
    i = sub.add_parser("ingest")
    i.add_argument("--split", choices=("dev", "test"), required=True)
    i.add_argument("--run", required=True)
    i.add_argument("--seed", type=int, default=42)
    i.add_argument("--model", default="", help="the generator agents' model, for the key's meta")
    a = ap.parse_args(argv)
    if a.cmd == "dump":
        dump(a.split, a.prompt)
    else:
        ingest(a.split, a.run, a.seed, a.model)


if __name__ == "__main__":
    main()
