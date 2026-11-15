"""A before-meeting brief against the after-meeting "since last time" line ($0, agents).

    uv run --no-sync python scripts/brief_before.py dump --run <RUN> --split dev|fresh|fresh2
        [--prompt brief_before.v1] [--model NAME]
    uv run --no-sync python scripts/brief_before.py dump-read --run <RUN> [--model NAME]
    uv run --no-sync python scripts/brief_before.py tiebreak --run <RUN> [--model NAME]
    uv run --no-sync python scripts/brief_before.py score --runs <RUN>[,<RUN>...]
        [--out NAME] [--model NAME] [--seed 42]

Pairs are those of ``scripts/since_writer.py`` (``--split dev`` only for developing the brief
prompt; ``fresh`` and ``fresh2`` are the test sets). Everything is under
``data/private/reader``.

``dump`` writes one brief job per pair (``bb_jobs/<RUN>/brief/<pair>.md``: the prompt and X's
annotated decisions and actions laid out as notes, nothing of Y) and records the run in
``bb_runs/<RUN>/run.json``. One agent per pair answers into ``bb_runs/<RUN>/brief/<pair>.jsonl``.

``dump-read`` validates the briefs and, for each pair with a valid bullet, writes a fate job
(``bb_jobs/<RUN>/fate/<pair>.md``: prompt ``brief_bullet_fate.v1``, the bullets and Y's human
transcript, nothing else), answered by readers a and b into ``bb_runs/<RUN>/<a|b>/<pair>.jsonl``,
and a naming job (``bb_jobs/<RUN>/names/<pair>.md``: prompt ``sentence_names_item.v1``, X's
items and the bullets), answered into ``bb_runs/<RUN>/names/<pair>.jsonl``.

``tiebreak`` writes, for each pair where a and b differ on a bullet's fate or expect_to_hear, a
fate job restricted to those bullets (same numbers, neither reader's answer) into
``bb_jobs/<RUN>/tiebreak/<pair>.md``; reader c answers into ``bb_runs/<RUN>/c/<pair>.jsonl``.

``score`` joins briefs, fates, naming, the item fates of each run's split and the
since-last-time v3 run on the same split (:data:`LINE_RUNS`), and writes ``rows.json`` and
``score.json`` per run, plus a pooled ``score.json`` under ``bb_runs/<OUT>`` when several runs
are given. Same inputs, same bytes. Local data only, private outputs: no LLM, no network.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from since_writer import FATES_RUNS, SPLITS, pairs_of

from tenor.ami.corpus import load_meeting
from tenor.evals import brief_before as BB
from tenor.evals import since_writer as W

READERS = Path("data/private/reader")
JOBS = READERS / "bb_jobs"
RUNS = READERS / "bb_runs"
# The since-last-time v3 run on each split, scored earlier (scripts/since_writer.py).
LINE_RUNS = {
    "dev": READERS / "sw_runs" / "swdev_v3_20261114",
    "fresh": READERS / "sw_runs" / "swfresh_v3_20261114",
    "fresh2": READERS / "sw_runs" / "swfresh2_v3_20261115",
}
SEED = 42


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _record(run: str) -> dict[str, Any]:
    path = RUNS / run / "run.json"
    if not path.exists():
        raise SystemExit(f"no run record {path}: run dump first")
    record: dict[str, Any] = _load(path)
    return record


def _save(run: str, record: dict[str, Any]) -> None:
    (RUNS / run / "run.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def dump(run: str, prompt_name: str, split: str, model: str | None) -> None:
    prompt = Path(f"prompts/{prompt_name}.md").read_text(encoding="utf-8")
    chosen = pairs_of(split)
    out = JOBS / run / "brief"
    out.mkdir(parents=True, exist_ok=True)
    (RUNS / run / "brief").mkdir(parents=True, exist_ok=True)
    for p in chosen:
        x, _, items = W.units(load_meeting(p.x), load_meeting(p.y))
        answer = str(RUNS / run / "brief" / f"{p.name}.jsonl")
        (out / f"{p.name}.md").write_text(
            BB.brief_job(x, prompt_name, prompt, answer), encoding="utf-8"
        )
        print(f"{p.name}\t{len(items)} items")
    _save(
        run,
        {
            "run": run,
            "brief_prompt": prompt_name,
            "split": split,
            "pairs": len(chosen),
            "pair_names": [p.name for p in chosen],
            "models": {"brief": model or "unrecorded"},
        },
    )
    print(f"{len(chosen)} brief jobs in {out} (prompt {prompt_name})")


def read_brief(run: str, pair: Any) -> tuple[list[dict[str, Any]], list[str]] | None:
    """The brief's bullets for a pair (``parse_brief``) and problems, ``None`` with no output."""
    path = RUNS / run / "brief" / f"{pair.name}.jsonl"
    if not path.exists():
        return None
    x, _, _ = W.units(load_meeting(pair.x), load_meeting(pair.y))
    return BB.parse_brief(path.read_text(encoding="utf-8"), [b.id for b in x.bullets])


def _valid(run: str, pair: Any) -> list[dict[str, Any]]:
    got = read_brief(run, pair)
    if got is None:
        print(f"PROBLEM {pair.name}: no brief")
        return []
    bullets, bad = got
    for e in bad:
        print(f"PROBLEM {pair.name} brief: {e}")
    for b in bullets:
        if b["status"] != "ok":
            print(f"REJECTED {pair.name} #{b['n']}: {b['status']}: {b['bullet'].bullet}")
    return [b for b in bullets if b["status"] == "ok"]


def dump_read(run: str, model: str | None) -> None:
    record = _record(run)
    if model:
        record["models"].update({"fate_a": model, "fate_b": model, "naming": model})
    record["fate_prompt"], record["naming_prompt"] = BB.FATE_PROMPT, BB.NAMING_PROMPT
    _save(run, record)
    fate_prompt = Path(f"prompts/{BB.FATE_PROMPT}.md").read_text(encoding="utf-8")
    naming_prompt = Path(f"prompts/{BB.NAMING_PROMPT}.md").read_text(encoding="utf-8")
    for d in ("fate", "names"):
        (JOBS / run / d).mkdir(parents=True, exist_ok=True)
    for d in (*BB.READERS, "names"):
        (RUNS / run / d).mkdir(parents=True, exist_ok=True)
    n_jobs = n_bullets = 0
    for p in pairs_of(record["split"]):
        ok = _valid(run, p)
        if not ok:
            continue
        _, y, items = W.units(load_meeting(p.x), load_meeting(p.y))
        shown = [(b["n"], b["bullet"].bullet) for b in ok]
        answer = str(RUNS / run / "<your reader letter: a or b>" / f"{p.name}.jsonl")
        (JOBS / run / "fate" / f"{p.name}.md").write_text(
            BB.fate_job(y, shown, fate_prompt, answer), encoding="utf-8"
        )
        listed = [(i.item_id, i.text.split("] ", 1)[-1]) for i in items]
        (JOBS / run / "names" / f"{p.name}.md").write_text(
            BB.naming_job(
                p.name,
                listed,
                [t for _, t in shown],
                naming_prompt,
                str(RUNS / run / "names" / f"{p.name}.jsonl"),
            ),
            encoding="utf-8",
        )
        n_jobs += 1
        n_bullets += len(ok)
    print(f"{n_jobs} fate jobs and naming jobs ({n_bullets} bullets) in {JOBS / run}")


def _answers(run: str, reader: str, pair: str, numbers: list[int]) -> dict[int, BB.BulletFate]:
    path = RUNS / run / reader / f"{pair}.jsonl"
    if not path.exists():
        return {}
    got, bad = BB.parse_fates(path.read_text(encoding="utf-8"), numbers)
    for e in bad:
        print(f"PROBLEM {pair} reader {reader}: {e}")
    return got


def tiebreak(run: str, model: str | None) -> None:
    record = _record(run)
    if model:
        record["models"]["fate_c"] = model
        _save(run, record)
    prompt = Path(f"prompts/{BB.FATE_PROMPT}.md").read_text(encoding="utf-8")
    out = JOBS / run / "tiebreak"
    out.mkdir(parents=True, exist_ok=True)
    (RUNS / run / "c").mkdir(parents=True, exist_ok=True)
    disputed: dict[str, list[int]] = {}
    for p in pairs_of(record["split"]):
        ok = _valid(run, p)
        if not ok:
            continue
        nums = [b["n"] for b in ok]
        a, b = (_answers(run, r, p.name, nums) for r in BB.READERS)
        bad = [n for n in nums if BB.needs_tiebreak(a.get(n), b.get(n))]
        if not bad:
            continue
        disputed[p.name] = bad
        _, y, _ = W.units(load_meeting(p.x), load_meeting(p.y))
        shown = [(b["n"], b["bullet"].bullet) for b in ok if b["n"] in bad]
        answer = str(RUNS / run / "c" / f"{p.name}.jsonl")
        (out / f"{p.name}.md").write_text(BB.fate_job(y, shown, prompt, answer), encoding="utf-8")
    print(json.dumps(disputed, indent=1))
    print(f"{len(disputed)} tie-break jobs ({sum(map(len, disputed.values()))} bullets) in {out}")


def rows_of(run: str) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """The bullet rows of a run, the pairs, and the run record."""
    record = _record(run)
    split = record["split"]
    fates = _load(FATES_RUNS[split] / "rows.json")["items"]
    rows: list[dict[str, Any]] = []
    pairs = []
    for p in pairs_of(split):
        pairs.append(p.name)
        ok = _valid(run, p)
        if not ok:
            continue
        nums = [b["n"] for b in ok]
        answers = {r: _answers(run, r, p.name, nums) for r in BB.READERS}
        disputed = [n for n in nums if BB.needs_tiebreak(answers["a"].get(n), answers["b"].get(n))]
        if disputed:
            answers["c"] = _answers(run, "c", p.name, disputed)
        _, _, items = W.units(load_meeting(p.x), load_meeting(p.y))
        of_bullet = {i.bullet_id: i.item_id for i in items}
        path = RUNS / run / "names" / f"{p.name}.jsonl"
        named: dict[int, list[str]] = {}
        if path.exists():
            by_number, bad = BB.parse_naming(
                path.read_text(encoding="utf-8"), [i.item_id for i in items], len(ok)
            )
            for e in bad:
                print(f"PROBLEM {p.name} naming: {e}")
            named = {ok[k - 1]["n"]: v for k, v in by_number.items()}
        else:
            print(f"PROBLEM {p.name}: no naming answer")
        mine = [r for r in fates if r["pair"] == p.name]
        shown = [
            (b["n"], b["bullet"].bullet, [of_bullet[i] for i in b["bullet"].x_bullets]) for b in ok
        ]
        rows += BB.bullet_rows(p.name, p.series, split, shown, answers, named, mine)
    return rows, pairs, record


def score(runs: list[str], out: str | None, model: str, seed: int) -> None:
    pooled_rows: list[dict[str, Any]] = []
    pooled_fates: list[dict[str, Any]] = []
    pooled_lines: list[dict[str, Any]] = []
    pooled_pairs: list[str] = []
    records = []
    for run in runs:
        rows, pairs, record = rows_of(run)
        split = record["split"]
        fates = [r for r in _load(FATES_RUNS[split] / "rows.json")["items"] if r["pair"] in pairs]
        line_path = LINE_RUNS[split] / "rows.json"
        if not line_path.exists():
            print(f"PROBLEM no since-last-time rows {line_path}: line counts left at zero")
        lines = _load(line_path) if line_path.exists() else []
        for r in rows:
            print(
                f"{r['pair']} #{r['n']} a={r['fate_a']} b={r['fate_b']} c={r['fate_c']} -> "
                f"{r['fate']} expect={r['expect_to_hear']} named={r['named_items']} "
                f"cited={r['cited_items']} items={r['item_fates']}\n    {r['bullet']}"
            )
        result = BB.score(rows, fates, lines, pairs, seed)
        meta = _meta(run, [record], model, seed, [split])
        (RUNS / run / "rows.json").write_text(
            json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (RUNS / run / "score.json").write_text(
            json.dumps({"meta": meta, "result": result}, indent=1) + "\n", encoding="utf-8"
        )
        print(f"== {run} ({split})\n{json.dumps(result, indent=1)}")
        pooled_rows += rows
        pooled_fates += fates
        pooled_lines += lines
        pooled_pairs += pairs
        records.append(record)
    if out and len(runs) > 1:
        result = BB.score(pooled_rows, pooled_fates, pooled_lines, pooled_pairs, seed)
        meta = _meta(out, records, model, seed, [r["split"] for r in records])
        (RUNS / out).mkdir(parents=True, exist_ok=True)
        (RUNS / out / "score.json").write_text(
            json.dumps({"meta": meta, "result": result}, indent=1) + "\n", encoding="utf-8"
        )
        print(f"== pooled {out}\n{json.dumps(result, indent=1)}")


def _meta(
    name: str, records: list[dict[str, Any]], model: str, seed: int, splits: list[str]
) -> dict[str, Any]:
    return {
        "run": name,
        "runs": [r["run"] for r in records],
        "git_sha": _git_sha(),
        "model": model,
        "models": {r["run"]: r.get("models", {}) for r in records},
        "splits": splits,
        "prompt_versions": sorted(
            {r["brief_prompt"] for r in records} | {BB.FATE_PROMPT, BB.NAMING_PROMPT}
        ),
        "fates_runs": [str(FATES_RUNS[s]) for s in splits],
        "line_runs": [str(LINE_RUNS[s]) for s in splits],
        "seed": seed,
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("--run", required=True)
    d.add_argument("--split", choices=SPLITS, required=True)
    d.add_argument("--prompt", default=BB.BRIEF_PROMPT)
    d.add_argument("--model")
    for cmd in ("dump-read", "tiebreak"):
        p = sub.add_parser(cmd)
        p.add_argument("--run", required=True)
        p.add_argument("--model")
    s = sub.add_parser("score")
    s.add_argument("--runs", required=True)
    s.add_argument("--out")
    s.add_argument("--model", default="unrecorded")
    s.add_argument("--seed", type=int, default=SEED)
    a = ap.parse_args()
    if a.cmd == "dump":
        dump(a.run, a.prompt, a.split, a.model)
    elif a.cmd == "dump-read":
        dump_read(a.run, a.model)
    elif a.cmd == "tiebreak":
        tiebreak(a.run, a.model)
    else:
        score(a.runs.split(","), a.out, a.model, a.seed)


if __name__ == "__main__":
    main()
