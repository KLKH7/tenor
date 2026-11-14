"""Agents answering AMI questions through the notes product's MCP (read tools only).

    uv run --no-sync python scripts/mcp_reading.py jobs --split dev [--limit 3]
    uv run --no-sync python scripts/mcp_reading.py packets --run <run_id> [--seed 42]
    uv run --no-sync python scripts/mcp_reading.py tiebreak --run <run_id>
    uv run --no-sync python scripts/mcp_reading.py score --run <run_id> [--seed 42]

``jobs`` writes ``data/private/reader/mcp_jobs/<split>/``: one agent prompt file per question and
condition (``mcp_agent.v1``), with the product's note ids for the question's meetings read from
the local AMI database (they never leave ``data/private``). Agents write
``mcp_runs/<run>/runs.jsonl`` (:class:`tenor.evals.mcp_reading.Run` lines). ``packets`` checks
those runs and writes one blind reader job per meeting set: the human transcript(s), then each
question's gold answer and the three answers under shuffled labels, plus the key. Readers a and
b write ``readers/<a|b>/<group>.jsonl``; ``tiebreak`` lists the rows they split on for reader c;
``score`` settles every row (agreement, else c) and writes ``score.json``. Local data only.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tenor.ami.corpus import load_meeting
from tenor.ami.questions import AmiQuestion, load_questions
from tenor.evals import came_back as K
from tenor.evals import mcp_reading as M

READERS = Path("data/private/reader")
JOBS = READERS / "mcp_jobs"
RUNS = READERS / "mcp_runs"
DB = Path("data/ami_granola.db")
STUDY = Path("data/private/JUDGE_REREAD_deletion.jsonl")
VERDICTS = ("correct", "partially_correct", "incorrect")


def study_questions(split: str) -> list[AmiQuestion]:
    """The retention study's questions in ``split``, in id order."""
    used = {json.loads(line)["qid"] for line in STUDY.read_text(encoding="utf-8").splitlines()}
    return sorted(
        (q for q in load_questions() if q.id in used and q.split == split), key=lambda q: q.id
    )


def note_ids(meetings: set[str]) -> dict[str, str]:
    """AMI meeting id -> the product's note id, by the exact title ``AMI <id>``."""
    with sqlite3.connect(DB) as db:
        rows = dict(db.execute("select title, note_id from notes").fetchall())
    return {m: rows[f"AMI {m}"] for m in sorted(meetings)}


def job_prompt(job: M.Job, template: str) -> str:
    head, *blocks = template.split("\n--- condition: ")
    block = next(b.split("\n", 1)[1].strip() for b in blocks if b.startswith(job.condition + "\n"))
    return (
        head.replace("{condition}", block).strip()
        + f"\n\nMeeting ids: {', '.join(job.meeting_ids)}\nQuestion: {job.question}\n"
    )


def cmd_jobs(split: str, limit: int | None) -> None:
    qs = study_questions(split)[:limit]
    ids = note_ids({m for q in qs for m in q.meetings})
    template = Path(f"prompts/{M.PROMPT}.md").read_text(encoding="utf-8")
    rows = [
        {"qid": j.qid, "condition": j.condition, "prompt": job_prompt(j, template)}
        for j in M.jobs([(q.id, q.question, q.meetings) for q in qs], ids)
    ]
    name = split if limit is None else f"{split}_{limit}"
    out = JOBS / name
    out.mkdir(parents=True, exist_ok=True)
    for row in rows:
        (out / f"{row['qid']}__{row['condition']}.md").write_text(row["prompt"], encoding="utf-8")
    index = [[row["qid"], row["condition"]] for row in rows]
    (JOBS / f"{name}.json").write_text(json.dumps(index) + "\n")
    print(f"{len(qs)} questions x {len(M.CONDITIONS)} conditions = {len(rows)} jobs -> {out}")


def load_runs(run: str) -> dict[tuple[str, str], M.Run]:
    out = {}
    for line in (RUNS / run / "runs.jsonl").read_text(encoding="utf-8").splitlines():
        r = M.Run.model_validate_json(line)
        out[(r.qid, r.condition)] = r
    return out


def transcript_block(meeting: str) -> str:
    m = load_meeting(meeting)
    lines = "\n".join(K.line(m, i) for i in range(len(m.dialogue_acts)))
    roster = K.roster(m, m).splitlines()[0]
    head = f"## Meeting {meeting}: full human transcript"
    return f"{head}\n\nRoster: {roster}\n\n```\n{lines}\n```"


def cmd_packets(run: str, seed: int) -> None:
    runs = load_runs(run)
    for r in runs.values():
        for p in M.problems(r):
            print(f"RULE {r.qid} {r.condition}: {p}")
    qs = {q.id: q for q in load_questions()}
    qids = sorted({q for q, _ in runs})
    key = M.packet_key(qids, seed)
    prompt = Path(f"prompts/{M.READER_PROMPT}.md").read_text(encoding="utf-8").strip()
    groups: dict[tuple[str, ...], list[str]] = {}
    for qid in qids:
        groups.setdefault(tuple(qs[qid].meetings), []).append(qid)
    out = RUNS / run / "packets"
    out.mkdir(parents=True, exist_ok=True)
    for meetings, ids in sorted(groups.items()):
        name = "__".join(meetings)
        rows = [(q, qs[q].question, qs[q].gold_answer) for q in ids]
        body = "\n\n".join(
            [
                f"# Reader job {name}",
                f"Prompt `{M.READER_PROMPT}`. Grade all {len(ids) * 3} answers below. Write your "
                f"JSONL to the answer file you were given. Use only this file.",
                prompt,
                "---",
                *(transcript_block(m) for m in meetings),
                "## Questions and answers",
                M.packet(rows, runs, key),
            ]
        )
        (out / f"{name}.md").write_text(body, encoding="utf-8")
        print(f"{name}: {len(ids)} questions")
    meta = {"run": run, "seed": seed, "reader_prompt": M.READER_PROMPT, "key": key}
    (RUNS / run / "key.json").write_text(json.dumps(meta, indent=1) + "\n")


def reader_rows(run: str, reader: str) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for path in sorted((RUNS / run / "readers" / reader).glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if d["verdict"] not in VERDICTS:
                raise ValueError(f"{path.name}: verdict {d['verdict']!r}")
            out[(d["qid"], d["label"])] = d["verdict"]
    return out


def cmd_tiebreak(run: str) -> None:
    a, b = reader_rows(run, "a"), reader_rows(run, "b")
    split = sorted(k for k in a if b.get(k) != a[k])
    (RUNS / run / "tiebreak.json").write_text(json.dumps([list(k) for k in split], indent=1) + "\n")
    print(f"readers agree on {len(a) - len(split)} of {len(a)}; {len(split)} for the tie-break")


def cmd_score(run: str, seed: int) -> None:
    runs = load_runs(run)
    key = json.loads((RUNS / run / "key.json").read_text())["key"]
    a, b = reader_rows(run, "a"), reader_rows(run, "b")
    c = reader_rows(run, "c") if (RUNS / run / "readers" / "c").exists() else {}
    verdicts: dict[tuple[str, str], str] = {}
    agree = 0
    for (qid, label), va in a.items():
        vb = b[(qid, label)]
        agree += va == vb
        verdicts[(qid, key[qid][label])] = va if va == vb else c[(qid, label)]
    result: dict[str, Any] = M.score(list(runs.values()), verdicts, seed)
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    result["meta"] = {
        "run": run,
        "prompt": M.PROMPT,
        "reader_prompt": M.READER_PROMPT,
        "git": sha.stdout.strip(),
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "reader_agreement": {"agree": agree, "of": len(a), "tiebreaks": len(a) - agree},
        "rule_breaks": sum(bool(M.problems(r)) for r in runs.values()),
    }
    result["verdicts"] = {f"{q}|{cnd}": v for (q, cnd), v in sorted(verdicts.items())}
    (RUNS / run / "score.json").write_text(json.dumps(result, indent=1) + "\n")
    for cnd, s in result["conditions"].items():
        n = result["n"]
        print(
            f"{cnd:10} correct {s['correct']['k']}/{n} {s['correct']['ci95']}  not wrong "
            f"{s['not_wrong']['k']}/{n} {s['not_wrong']['ci95']}  read transcript "
            f"{s['read_transcript']}/{n}  abstained {s['abstained']}"
        )
    print(json.dumps(result["paired"]), json.dumps(result["free_by_reading"]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("jobs")
    p.add_argument("--split", choices=("dev", "test"), required=True)
    p.add_argument("--limit", type=int)
    for name in ("packets", "tiebreak", "score"):
        p = sub.add_parser(name)
        p.add_argument("--run", required=True)
        if name != "tiebreak":
            p.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    if a.cmd == "jobs":
        cmd_jobs(a.split, a.limit)
    elif a.cmd == "packets":
        cmd_packets(a.run, a.seed)
    elif a.cmd == "tiebreak":
        cmd_tiebreak(a.run)
    else:
        cmd_score(a.run, a.seed)
