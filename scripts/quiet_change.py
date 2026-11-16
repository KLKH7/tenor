"""Quiet-change lines, written then verified, on the item-fates pairs ($0, agents).

    uv run --no-sync python scripts/quiet_change.py dump --run <RUN>
        [--split test|dev|fresh|fresh2 | --pairs IS1008a-b,...] [--prompt NAME] [--model NAME]
    uv run --no-sync python scripts/quiet_change.py dump-verify --run <RUN> [--prompt NAME]
        [--model NAME]
    uv run --no-sync python scripts/quiet_change.py dump-check --run <RUN> [--model NAME]
    uv run --no-sync python scripts/quiet_change.py score --run <RUN> [--seed 42]
    uv run --no-sync python scripts/quiet_change.py evidence --run <RUN>

Pairs and fates are those of ``scripts/since_writer.py`` (``--split dev`` and IS1008a-b only for
developing the prompts). Everything is under ``data/private/reader``.

``dump`` writes one writer job per pair (``qc_jobs/<RUN>/writer/<pair>.md``: prompt
``since_last_time_changed.v1`` by default, X's annotated items as notes, Y's human transcript;
no fates) and records the run in ``qc_runs/<RUN>/run.json``; one agent per pair answers into
``qc_runs/<RUN>/writer/<pair>.jsonl``.

``dump-verify`` checks the writer's lines mechanically and writes one verifier job per pair with
a valid line (``qc_jobs/<RUN>/verify/<pair>.md``: prompt ``quiet_change_verify.v1`` by default,
X's notes, all of Y, the proposed lines and their quotes); one agent per pair answers into
``qc_runs/<RUN>/verify/<pair>.jsonl``.

``dump-check`` writes one checker job per pair with a kept line (``qc_jobs/<RUN>/check/<pair>.md``:
prompt ``since_last_time_check.v1`` unchanged, the kept lines as ``changed`` sentences); checkers
a and b answer into ``qc_runs/<RUN>/check_<a|b>/<pair>.jsonl``.

``score`` joins lines, verifier, checkers and fates and writes ``rows.json`` and ``score.json``.
``evidence`` prints, for every line kept and judged true, the X lines (from the pair's fates job
under ``fates_jobs/``) and the quoted Y lines it rests on, verbatim.

Same inputs, same bytes. Local data only, private outputs: no LLM, no network.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from since_writer import FATES_RUNS, SPLITS, pair_fates, pairs_of

from tenor.ami.corpus import load_meeting
from tenor.evals import came_back as K
from tenor.evals import follow_through as T
from tenor.evals import item_fates as F
from tenor.evals import quiet_change as Q
from tenor.evals import since_writer as W

READERS = Path("data/private/reader")
JOBS = READERS / "qc_jobs"
RUNS = READERS / "qc_runs"
FATES_JOBS = READERS / "fates_jobs"
WRITER_PROMPT = "since_last_time_changed.v1"
VERIFY_PROMPT = "quiet_change_verify.v1"
SEED = 42


def _record(run: str) -> dict[str, Any]:
    path = RUNS / run / "run.json"
    if not path.exists():
        raise SystemExit(f"no run record {path}: run dump first")
    record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return record


def _save(run: str, record: dict[str, Any]) -> None:
    (RUNS / run / "run.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")


def _pairs(record: dict[str, Any]) -> list[K.Pair]:
    return pairs_of(record["split"], record.get("pair_names"))


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _units(p: K.Pair) -> tuple[Any, Any, list[T.NoteItem]]:
    return W.units(load_meeting(p.x), load_meeting(p.y))


def dump(
    run: str, prompt_name: str, split: str, model: str | None, names: list[str] | None
) -> None:
    prompt = Path(f"prompts/{prompt_name}.md").read_text(encoding="utf-8")
    chosen = pairs_of(split, names)
    out = JOBS / run / "writer"
    out.mkdir(parents=True, exist_ok=True)
    (RUNS / run / "writer").mkdir(parents=True, exist_ok=True)
    for p in chosen:
        x, y, items = _units(p)
        answer = str(RUNS / run / "writer" / f"{p.name}.jsonl")
        (out / f"{p.name}.md").write_text(
            W.writer_job(x, y, prompt_name, prompt, answer), encoding="utf-8"
        )
        print(f"{p.name}\t{len(items)} items\t{len(y.transcript)} lines")
    record: dict[str, Any] = {
        "run": run,
        "writer_prompt": prompt_name,
        "split": split,
        "pairs": len(chosen),
        "pair_names": [p.name for p in chosen],
        "models": {"writer": model or "unrecorded"},
        "git_sha_at_dump": _git_sha(),
    }
    _save(run, record)
    print(f"{len(chosen)} writer jobs in {out} (prompt {prompt_name})")


def _written(run: str, p: K.Pair) -> tuple[list[dict[str, Any]], list[str]] | None:
    path = RUNS / run / "writer" / f"{p.name}.jsonl"
    if not path.exists():
        return None
    x, y, _ = _units(p)
    return Q.parse_changed(path.read_text(encoding="utf-8"), [b.id for b in x.bullets], y)


def _verified(run: str, p: K.Pair, n_ok: int) -> tuple[dict[int, Q.Verification] | None, list[str]]:
    path = RUNS / run / "verify" / f"{p.name}.jsonl"
    if not path.exists():
        return None, ([f"no verifier answer ({n_ok} lines)"] if n_ok else [])
    got, errors = Q.parse_verifications(path.read_text(encoding="utf-8"), n_ok)
    errors += [f"no verdict for line {k}" for k in range(1, n_ok + 1) if k not in got]
    return got, errors


def dump_verify(run: str, prompt_name: str, model: str | None) -> None:
    record = _record(run)
    record["verify_prompt"] = prompt_name
    record.setdefault("models", {})["verifier"] = model or "unrecorded"
    _save(run, record)
    prompt = Path(f"prompts/{prompt_name}.md").read_text(encoding="utf-8")
    out = JOBS / run / "verify"
    out.mkdir(parents=True, exist_ok=True)
    (RUNS / run / "verify").mkdir(parents=True, exist_ok=True)
    n_jobs = n_lines = 0
    for p in _pairs(record):
        got = _written(run, p)
        if got is None:
            print(f"PROBLEM {p.name}: no writer output")
            continue
        written, bad = got
        for e in bad:
            print(f"PROBLEM {p.name} writer: {e}")
        for w in written:
            if w["status"] != "ok":
                print(f"REJECTED {p.name} #{w['n']}: {w['status']}: {w['line'].sentence}")
        ok = [w["line"] for w in written if w["status"] == "ok"]
        if not ok:
            continue
        x, y, _ = _units(p)
        answer = str(RUNS / run / "verify" / f"{p.name}.jsonl")
        (out / f"{p.name}.md").write_text(Q.verify_job(x, y, ok, prompt, answer), encoding="utf-8")
        n_jobs += 1
        n_lines += len(ok)
    print(f"{n_jobs} verifier jobs ({n_lines} lines) in {out}")


def dump_check(run: str, model: str | None) -> None:
    record = _record(run)
    record["checker_prompt"] = T.CHECK_PROMPT
    record.setdefault("models", {}).update(
        {f"checker_{c}": model or "unrecorded" for c in T.CHECKERS}
    )
    _save(run, record)
    prompt = Path(f"prompts/{T.CHECK_PROMPT}.md").read_text(encoding="utf-8")
    out = JOBS / run / "check"
    out.mkdir(parents=True, exist_ok=True)
    for c in T.CHECKERS:
        (RUNS / run / f"check_{c}").mkdir(parents=True, exist_ok=True)
    n_jobs = n_lines = 0
    for p in _pairs(record):
        got = _written(run, p)
        if got is None:
            continue
        ok = [w["line"] for w in got[0] if w["status"] == "ok"]
        verified, errors = _verified(run, p, len(ok))
        for e in errors:
            print(f"PROBLEM {p.name} verifier: {e}")
        kept = [
            c for k, c in enumerate(ok, start=1) if verified and k in verified and verified[k].keeps
        ]
        if not kept:
            continue
        x, y, items = _units(p)
        answer = str(RUNS / run / "check_<your checker letter: a or b>" / f"{p.name}.jsonl")
        lines = [Q.as_writer_line(c) for c in kept]
        (out / f"{p.name}.md").write_text(
            W.check_job(x, y, items, lines, prompt, answer), encoding="utf-8"
        )
        n_jobs += 1
        n_lines += len(kept)
    print(f"{n_jobs} checker jobs ({n_lines} kept lines) in {out}")


def _fates(record: dict[str, Any], chosen: list[K.Pair]) -> tuple[list[dict[str, Any]], list[str]]:
    split = record["split"]
    if split == "pairs":
        return pair_fates(chosen)
    path = FATES_RUNS[split] / "rows.json"
    items: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))["items"]
    return items, [str(FATES_RUNS[split])]


def rows_of(
    run: str,
) -> tuple[
    dict[str, Any], list[K.Pair], list[dict[str, Any]], list[dict[str, Any]], list[str], list[str]
]:
    """The run record, its pairs, one row per proposed line, the fates, the fates runs and the
    problems."""
    record = _record(run)
    chosen = _pairs(record)
    fates, fates_runs = _fates(record, chosen)
    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    for p in chosen:
        got = _written(run, p)
        if got is None:
            problems.append(f"{p.name}: no writer output")
            continue
        written, bad = got
        problems += [f"{p.name} writer: {e}" for e in bad]
        n_ok = sum(w["status"] == "ok" for w in written)
        verified, errors = _verified(run, p, n_ok)
        problems += [f"{p.name} verifier: {e}" for e in errors]
        n_kept = sum(
            1 for k in range(1, n_ok + 1) if verified and k in verified and verified[k].keeps
        )
        _, _, items = _units(p)
        ids = [i.item_id for i in items]
        checks: dict[str, dict[int, T.Verdict]] = {}
        for c in T.CHECKERS:
            path = RUNS / run / f"check_{c}" / f"{p.name}.jsonl"
            if not path.exists():
                if n_kept:
                    problems.append(f"{p.name}: no answer from checker {c}")
                continue
            v, bad = T.parse_verdicts(path.read_text(encoding="utf-8"), n_kept, ids)
            checks[c] = v
            problems += [f"{p.name} checker {c}: {e}" for e in bad]
        mine = [r for r in fates if r["pair"] == p.name]
        rows += Q.line_rows(p.name, written, verified, checks, items, mine)
    return record, chosen, rows, fates, fates_runs, problems


def score(run: str, seed: int) -> None:
    record, chosen, rows, fates, fates_runs, problems = rows_of(run)
    for r in rows:
        print(
            f"{r['pair']} #{r['n']} status={r['status']} verdict={r['verdict']} "
            f"failed={r['failed']} kept={r['kept']} a={r['supported_a']} b={r['supported_b']} "
            f"writer_items={r['writer_items']} fates={r['writer_item_fates']}\n    {r['sentence']}"
        )
    for e in problems:
        print(f"PROBLEM {e}")
    meta = {
        "run": run,
        "git_sha": _git_sha(),
        "models": record.get("models", {}),
        "split": record["split"],
        "prompt_versions": [
            record["writer_prompt"],
            record.get("verify_prompt"),
            T.CHECK_PROMPT,
            F.PROMPT,
        ],
        "seed": seed,
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "fates_run": fates_runs[0] if len(fates_runs) == 1 else fates_runs,
        "problems": problems,
    }
    out = RUNS / run
    (out / "rows.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
    result = Q.score(rows, fates, [p.name for p in chosen], seed)
    print(json.dumps(result, indent=1))
    (out / "score.json").write_text(json.dumps({"meta": meta, "result": result}, indent=1) + "\n")
    print(f"wrote {out / 'score.json'} and {out / 'rows.json'}")


_X_LINE = re.compile(r"^\* (X\d{4}) \[([^\]]+)\] ([^:]+): (.*)$")
_Y_LINE = re.compile(r"^(L\d{4}) \[([^\]]+)\] ([^:]+): (.*)$")


def _fates_job_lines(
    pair: str,
) -> tuple[dict[str, list[tuple[str, str, str, str]]], dict[str, tuple[str, str, str]]]:
    """From a pair's fates job: item id -> the X lines the annotator linked (``*``), and Y line
    id -> (time, speaker, text)."""
    text = (FATES_JOBS / f"{pair}.md").read_text(encoding="utf-8")
    x_part, y_part = text.split("\n## Meeting Y", 1)
    starred: dict[str, list[tuple[str, str, str, str]]] = {}
    item = ""
    for line in x_part.splitlines():
        if line.startswith("### "):
            item = line[4:].split(" ", 1)[0]
        elif (m := _X_LINE.match(line)) and item:
            starred.setdefault(item, []).append((m[1], m[2], m[3], m[4]))
    y = {m[1]: (m[2], m[3], m[4]) for line in y_part.splitlines() if (m := _Y_LINE.match(line))}
    return starred, y


def evidence(run: str) -> None:
    _, _, rows, _, _, problems = rows_of(run)
    for e in problems:
        print(f"PROBLEM {e}")
    for r in rows:
        if not r["true"]:
            continue
        x_id, y_id = r["pair"].split("__")
        starred, y = _fates_job_lines(r["pair"])
        print(f"### {x_id} -> {y_id}\n\n> {r['sentence']}\n")
        print(f"Items (writer): {', '.join(r['writer_items'])}; fates: {r['writer_item_fates']}\n")
        print(f"Earlier meeting ({x_id}), lines the annotator linked to the item:\n")
        for item in r["writer_items"]:
            for lid, t, who, said in starred.get(item, []):
                print(f"- {x_id} {lid} [{t}] {who}: {said}")
        print(f"\nLater meeting ({y_id}), the quoted lines:\n")
        for q in r["quotes"]:
            t, who, said = y[q["line"]]
            print(f"- {y_id} {q['line']} [{t}] {who}: {said}")
        print()


def main() -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd in ("dump", "dump-verify", "dump-check", "score", "evidence"):
        p = sub.add_parser(cmd)
        p.add_argument("--run", required=True)
        if cmd == "dump":
            p.add_argument("--prompt", default=WRITER_PROMPT)
            which = p.add_mutually_exclusive_group()
            which.add_argument("--split", choices=SPLITS, default="test")
            which.add_argument("--pairs", help="named pairs, comma-separated (IS1008a-b)")
            p.add_argument("--note", help="a note kept in run.json")
        if cmd == "dump-verify":
            p.add_argument("--prompt", default=VERIFY_PROMPT)
        if cmd in ("dump", "dump-verify", "dump-check"):
            p.add_argument("--model", help="the agents' model, recorded in run.json")
        if cmd == "score":
            p.add_argument("--seed", type=int, default=SEED)
    a = ap.parse_args()
    if a.cmd == "dump":
        names = a.pairs.split(",") if a.pairs else None
        dump(a.run, a.prompt, "pairs" if a.pairs else a.split, a.model, names)
        if a.note:
            record = _record(a.run)
            record["note"] = a.note
            _save(a.run, record)
    elif a.cmd == "dump-verify":
        dump_verify(a.run, a.prompt, a.model)
    elif a.cmd == "dump-check":
        dump_check(a.run, a.model)
    elif a.cmd == "score":
        score(a.run, a.seed)
    else:
        evidence(a.run)


if __name__ == "__main__":
    main()
