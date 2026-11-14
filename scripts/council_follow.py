"""Follow-through on council meetings: items a meeting leaves open and their fate in the next
one ($0, agents; design and results in data/private/FOLLOW_THROUGH_COUNCIL.md).

    uv run --no-sync python scripts/council_follow.py extract-dump --meetings M1,M2,...
    uv run --no-sync python scripts/council_follow.py items --run <RUN> --meetings M1,M2,...
    uv run --no-sync python scripts/council_follow.py fates-dump --run <RUN> --pairs X__Y,...
    uv run --no-sync python scripts/council_follow.py disagreements --run <RUN> --pairs X__Y,...
    uv run --no-sync python scripts/council_follow.py score --run <RUN> --pairs X__Y,...
        [--seed 42] [--model NAME]

A meeting id names its transcript: ``data/council/captions/<id>.srt`` if that file exists (a
full meeting's official captions), else a council segment of ``data/council/recording_ids.txt``
read from Granola's own transcript in the local store. ``--split dev|test`` takes the pairs (or
their meetings) from ``data/council/follow_pairs.json`` instead of naming them.

``extract-dump`` writes one extractor job per meeting to
``data/private/reader/cf_jobs/extract/<id>.md`` (prompt ``council_items.v1``). Extractors a and
b each answer into ``data/private/reader/cf_runs/<RUN>/extract_<a|b>/<id>.jsonl``.
``items`` checks both answers, keeps the items both found and writes ``<RUN>/items.json``.
``fates-dump`` writes one fate job per pair to ``cf_jobs/fates/<RUN>/<X>__<Y>.md`` (prompt
``item_fates.v1``, unchanged); readers a and b answer into ``<RUN>/fates_<a|b>/<X>__<Y>.jsonl``.
``disagreements`` writes tie-break jobs (``cf_jobs/tiebreak/<RUN>/``; reader c answers into
``<RUN>/fates_c/``). ``score`` prints the fate distribution, silent items per pair, reader
agreement and, where ``data/council/legistar_events/<Y>/event_items.json`` exists, agreement
with the official record; it writes ``<RUN>/score.json`` and ``rows.json``. Same inputs and
seed, same bytes. Local data only: no LLM, no network.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tenor.evals import council_follow as CF
from tenor.evals import item_fates as F

COUNCIL = Path("data/council")
READERS = Path("data/private/reader")
JOBS = READERS / "cf_jobs"
RUNS = READERS / "cf_runs"
PAIRS_FILE = COUNCIL / "follow_pairs.json"


def segment_header(meeting: str) -> str:
    """What the job tells an extractor about a local segment: body, date and the agenda item."""
    manifest = json.loads((COUNCIL / "MANIFEST.json").read_text())
    for f in manifest["files"]:
        if meeting == f"seattle_{f['legistar_event_id']}_{f['legistar_event_item_id']}":
            return (
                f"{f['city']} {f['body']}, {f['meeting_date']}. This is one agenda item of the "
                f"meeting, not the whole meeting: {f['matter_file']} ({f['matter_type']})."
            )
    return ""


def load(meeting: str) -> tuple[list[CF.Line], str]:
    """A meeting's lines and job header, from its caption file or the local Granola store."""
    srt = COUNCIL / "captions" / f"{meeting}.srt"
    if srt.exists():
        return CF.parse_srt(srt.read_text(encoding="utf-8")), "Official captions; no speaker names."
    from tenor.cli import FACTS_DBS
    from tenor.core.store import read_notes
    from tenor.evals import council_notes as C
    from tenor.evals import speaker_naming as S
    from tenor.evals.wrong_facts import DROP_MIC, own_transcript

    ids = C.read_recording_ids(COUNCIL / "recording_ids.txt")
    if meeting not in ids:
        raise SystemExit(f"{meeting}: no caption file and not a recorded segment")
    notes = {n.note_id: n for n in read_notes(FACTS_DBS["granola"])}
    own = own_transcript(notes[ids[meeting]["granola"]], drop_mic=DROP_MIC.get("granola", True))
    lines = [CF.Line(seconds=p.start, speaker=p.label, text=p.text) for p in S.product_lines(own)]
    return lines, segment_header(meeting) + " Transcript: Granola's own, from the played audio."


def split_pairs(split: str) -> list[str]:
    if not PAIRS_FILE.exists():
        raise SystemExit(f"{PAIRS_FILE} does not exist: no consecutive meetings are on disk yet")
    return [f"{x}__{y}" for x, y in json.loads(PAIRS_FILE.read_text())[split]]


def pairs_of(a: argparse.Namespace) -> list[tuple[str, str]]:
    names = split_pairs(a.split) if a.split else [p for p in (a.pairs or "").split(",") if p]
    if not names:
        raise SystemExit("name pairs with --pairs X__Y,... or --split dev|test")
    return [(n.split("__")[0], n.split("__")[1]) for n in names]


def meetings_of(a: argparse.Namespace) -> list[str]:
    if a.split:
        return sorted({m for p in split_pairs(a.split) for m in p.split("__")})
    return [m for m in (a.meetings or "").split(",") if m]


def _prompt(name: str) -> str:
    return Path(f"prompts/{name}.md").read_text(encoding="utf-8")


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def extract_dump(a: argparse.Namespace) -> None:
    out = JOBS / "extract"
    out.mkdir(parents=True, exist_ok=True)
    for m in meetings_of(a):
        lines, header = load(m)
        answer = f"{RUNS}/<RUN>/extract_<a|b>/{m}.jsonl"
        text = CF.extract_job(m, header, lines, _prompt(CF.EXTRACT_PROMPT), answer)
        (out / f"{m}.md").write_text(text, encoding="utf-8")
        print(f"{m}: {len(lines)} lines -> {out / f'{m}.md'}")


def items(a: argparse.Namespace) -> None:
    run = RUNS / a.run
    out: dict[str, Any] = {"prompt": CF.EXTRACT_PROMPT, "meetings": {}}
    for m in meetings_of(a):
        lines, _ = load(m)
        got = {}
        for r in CF.EXTRACTORS:
            path = run / f"extract_{r}" / f"{m}.jsonl"
            if not path.exists():
                raise SystemExit(f"{path}: missing")
            got[r], errors = CF.parse_extracted(path.read_text(encoding="utf-8"), lines)
            for e in errors:
                print(f"ERROR {m} extractor {r}: {e}")
        kept, counts = CF.kept_items(got["a"], got["b"])
        out["meetings"][m] = {
            "counts": counts,
            "items": [k.model_dump() for k in kept],
            "only_a": [x.text for i, x in enumerate(got["a"])
                       if i not in {p for p, _ in CF.match_items(got["a"], got["b"])[0]}],
            "only_b": [x.text for j, x in enumerate(got["b"])
                       if j not in {q for _, q in CF.match_items(got["a"], got["b"])[0]}],
            "minutes": round(lines[-1].seconds / 60, 1) if lines else 0.0,
        }  # fmt: skip
        print(f"{m}: a {counts['a']}, b {counts['b']}, both {counts['both']}")
    tot = {k: sum(v["counts"][k] for v in out["meetings"].values()) for k in ("a", "b", "both")}
    out["total"] = tot
    (run / "items.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(f"total: a {tot['a']}, b {tot['b']}, both {tot['both']} -> {run / 'items.json'}")


def kept(run: str, meeting: str) -> list[CF.Item]:
    data = json.loads((RUNS / run / "items.json").read_text())["meetings"]
    if meeting not in data:
        raise SystemExit(f"{meeting}: not in {run}/items.json (run `items` first)")
    return [CF.Item.model_validate(i) for i in data[meeting]["items"]]


def fates_dump(a: argparse.Namespace) -> None:
    out = JOBS / "fates" / a.run
    out.mkdir(parents=True, exist_ok=True)
    for x, y in pairs_of(a):
        its = kept(a.run, x)
        if not its:
            print(f"{x}__{y}: no kept item, no job")
            continue
        answer = f"{RUNS}/{a.run}/fates_<a|b>/{x}__{y}.jsonl"
        text = CF.fate_job(x, y, its, load(x)[0], load(y)[0], _prompt(CF.FATE_PROMPT), answer)
        (out / f"{x}__{y}.md").write_text(text, encoding="utf-8")
        print(f"{x}__{y}: {len(its)} items -> {out / f'{x}__{y}.md'}")


def read_answers(run: str, x: str, y: str, its: list[CF.Item]) -> dict[str, dict[str, F.Answer]]:
    n_y = len(load(y)[0])
    ids = [i.item_id for i in its]
    got: dict[str, dict[str, F.Answer]] = {}
    for r in (*F.READERS, F.TIEBREAK):
        path = RUNS / run / f"fates_{r}" / f"{x}__{y}.jsonl"
        if not path.exists():
            continue
        got[r], errors = F.parse_answers(path.read_text(encoding="utf-8"), ids, n_y)
        for e in errors:
            print(f"ERROR {x}__{y} reader {r}: {e}")
    return got


def disagreements(a: argparse.Namespace) -> None:
    out = JOBS / "tiebreak" / a.run
    out.mkdir(parents=True, exist_ok=True)
    listed = {}
    for x, y in pairs_of(a):
        its = kept(a.run, x)
        got = read_answers(a.run, x, y, its)
        ids = F.disputed([i.item_id for i in its], got.get("a", {}), got.get("b", {}))
        if not ids:
            continue
        answer = f"{RUNS}/{a.run}/fates_c/{x}__{y}.jsonl"
        text = CF.fate_job(
            x, y, its, load(x)[0], load(y)[0], _prompt(CF.FATE_PROMPT), answer, only=ids
        )
        (out / f"{x}__{y}.md").write_text(text, encoding="utf-8")
        listed[f"{x}__{y}"] = ids
    print(json.dumps(listed, indent=1))


def score(a: argparse.Namespace) -> None:
    rows: list[dict[str, Any]] = []
    for x, y in pairs_of(a):
        its = kept(a.run, x)
        mine = CF.item_rows(f"{x}__{y}", its, read_answers(a.run, x, y, its))
        events = COUNCIL / "legistar_events" / y / "event_items.json"
        if events.exists():
            data = json.loads(events.read_text())
            data = data.get("data", data) if isinstance(data, dict) else data
            for r in mine:
                r.update(CF.official_return(r["matter"], data))
        rows += mine
    if not rows:
        raise SystemExit("no kept item in these pairs")
    result = {
        "run_id": a.run,
        "git_sha": _git_sha(),
        "prompts": {"extract": CF.EXTRACT_PROMPT, "fates": CF.FATE_PROMPT},
        "reader_model": a.model,
        "seed": a.seed,
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        **CF.score(rows, a.seed),
        "official_record": CF.record_agreement(rows),
    }
    run = RUNS / a.run
    (run / "rows.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
    (run / "score.json").write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
    d = result["all_items"]
    print(f"{d['items']} items, {d['pairs']} pairs")
    for fate, r in d["fates"].items():
        print(f"  {fate}: {r['num']} of {r['den']} ({r['value']}) {r['ci']}")
    s = result["silent_per_pair"]["silent_any"]["pairs_with_at_least_1"]
    print(f"pairs with a silent item: {s['num']} of {s['den']} {s['ci']}")
    print(f"official record: {result['official_record']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    steps = {"extract-dump": extract_dump, "items": items, "fates-dump": fates_dump,
             "disagreements": disagreements, "score": score}  # fmt: skip
    for name in steps:
        p = sub.add_parser(name)
        p.add_argument("--split", choices=("dev", "test"))
        if name in ("extract-dump", "items"):
            p.add_argument("--meetings")
        else:
            p.add_argument("--pairs")
        if name != "extract-dump":
            p.add_argument("--run", required=True)
        if name == "score":
            p.add_argument("--seed", type=int, default=42)
            p.add_argument("--model", default="not recorded")
    a = ap.parse_args()
    steps[a.cmd](a)


if __name__ == "__main__":
    main()
