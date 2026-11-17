"""Retrieval lab pilot: agents answer through the logged tools, blind readers grade, the harness
scores retrieved / retained / accurate.

  setup   write the workspace, the questions and one job file per (question, run)
  readers write blind reader packets (both runs of a question, shuffled, with the gold answer)
  score   join answers, call logs and settled verdicts into harness trials and a summary
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

from tenor.ami.corpus import load_meeting
from tenor.ami.questions import load_questions
from tenor.evals.meeting_memory import transcript
from tenor.retrieval.harness import score_trial, summarize
from tenor.retrieval.tools import ToolCall

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from memory_breakdown import facts_of  # noqa: E402

OUT = REPO / "data/private/retrieval/pilot"
SERIES = ["IS1008a", "IS1008b", "IS1008c", "IS1008d"]
QIDS = [
    "ami-IS1008d-action_what-01",
    "ami-IS1008b-IS1008c-what_changed-04",
    "ami-IS1008d-problem-01",
]
RUNS = 2
SEED = 42


def workspace() -> dict[str, object]:
    others = sorted({m for q in load_questions() for m in q.meetings} - set(SERIES))
    return {
        "meetings": SERIES,
        "notes": "product",
        "speakers": "anonymous",
        "distractors": sorted(random.Random(SEED).sample(others, 20)),
    }


def cmd_setup() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "workspace.json").write_text(json.dumps(workspace(), indent=1) + "\n")
    qs = {q.id: q for q in load_questions()}
    rows = [{"qid": i, "question": qs[i].question} for i in QIDS]
    (OUT / "questions.json").write_text(json.dumps(rows, indent=1) + "\n")
    prompt = (REPO / "prompts/retrieval_answer.v1.md").read_text().split("\n## Changelog")[0]
    for r in rows:
        for run in range(RUNS):
            tag = f"{r['qid']}__run{run}"
            log, ans = OUT / "logs" / f"{tag}.jsonl", OUT / "answers" / f"{tag}.txt"
            log.parent.mkdir(exist_ok=True)
            ans.parent.mkdir(exist_ok=True)
            cmd = (
                f"uv run python scripts/retrieval_tools.py --workspace {OUT / 'workspace.json'} "
                f"--log {log} <tool> '<json args>'"
            )
            job = (
                f"{prompt}\n\n## Your job\n\nQuestion: {r['question']}\n\nTool command (run from "
                f"{REPO}):\n\n    {cmd}\n\nExample:\n\n    "
                + cmd.replace(
                    "<tool> '<json args>'",
                    'search_transcripts \'{"keywords": "remote control price"}\'',
                )
                + f"\n\nAnswer file: {ans}\n"
            )
            (OUT / "jobs").mkdir(exist_ok=True)
            (OUT / "jobs" / f"{tag}.md").write_text(job)
    print(f"{len(rows) * RUNS} jobs in {OUT / 'jobs'}")


def cmd_readers() -> None:
    qs = {q.id: q for q in load_questions()}
    prompt = (REPO / "prompts/mcp_reading_reader.v1.md").read_text().split("\n## Changelog")[0]
    rng = random.Random(SEED)
    key: dict[str, dict[str, int]] = {}
    parts = [prompt, "\n## Packet\n"]
    for qid in QIDS:
        q = qs[qid]
        for mid in q.meetings:
            m = load_meeting(mid)
            parts.append(f"\n### Meeting {mid}: full human transcript\n")
            parts.extend(f"{k} {v.speaker}: {v.text}" for k, v in transcript(m).items())
        runs = list(range(RUNS))
        rng.shuffle(runs)
        labels = "XYZ"[: len(runs)]
        key[qid] = {lab: run for lab, run in zip(labels, runs, strict=True)}
        parts.append(f"\n### Question {qid}\n{q.question}\n\nGold answer: {q.gold_answer}\n")
        for lab, run in key[qid].items():
            ans = (OUT / "answers" / f"{qid}__run{run}.txt").read_text().strip() or "(abstained)"
            parts.append(f"\nAnswer {lab}:\n{ans}\n")
    (OUT / "readers").mkdir(exist_ok=True)
    (OUT / "readers" / "packet.md").write_text("\n".join(parts) + "\n")
    (OUT / "readers" / "key.json").write_text(json.dumps(key, indent=1) + "\n")
    print(OUT / "readers" / "packet.md")


def verdicts() -> dict[tuple[str, int], str]:
    key = json.loads((OUT / "readers/key.json").read_text())
    by: dict[tuple[str, int], list[str]] = {}
    for f in sorted((OUT / "readers").glob("verdicts_*.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                v = json.loads(line)
                by.setdefault((v["qid"], key[v["qid"]][v["label"]]), []).append(v["verdict"])
    out = {}
    for k, vs in by.items():
        out[k] = vs[-1] if len(vs) > 2 else (vs[0] if len(set(vs)) == 1 else "disputed")
    return out


def cmd_score() -> None:
    qs = {q.id: q for q in load_questions()}
    ws = json.loads((OUT / "workspace.json").read_text())
    known = [*ws["meetings"], *ws["distractors"]]
    v = verdicts()
    trials, groups = [], {}
    for qid in QIDS:
        groups[qid] = "cross" if len(qs[qid].meetings) > 1 else "single"
        for run in range(RUNS):
            tag = f"{qid}__run{run}"
            log = [
                ToolCall.model_validate_json(x)
                for x in (OUT / "logs" / f"{tag}.jsonl").read_text().splitlines()
                if x.strip()
            ]
            ans = (OUT / "answers" / f"{tag}.txt").read_text()
            trials.append(
                score_trial(
                    qid,
                    run,
                    facts_of(qs[qid]),
                    ans,
                    log,
                    v.get((qid, run)) == "correct",
                    known,
                    qs[qid].question,
                )
            )
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    out = {
        "git": sha.stdout.strip(),
        "seed": SEED,
        "answer_prompt": "retrieval_answer.v1",
        "reader_prompt": "mcp_reading_reader.v1",
        "workspace": ws,
        "verdicts": {f"{q}|{r}": x for (q, r), x in sorted(v.items())},
        "trials": [t.model_dump() | {"passed": t.passed, "category": t.category} for t in trials],
        "summary": {k: s.model_dump() for k, s in summarize(trials, groups).items()},
    }
    (OUT / "score.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out["summary"], indent=1))
    for t in trials:
        print(t.qid, t.run, t.category, [(f.retrieved, f.retained) for f in t.facts], t.chars_read)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("cmd", choices=["setup", "readers", "score"])
    {"setup": cmd_setup, "readers": cmd_readers, "score": cmd_score}[p.parse_args().cmd]()


if __name__ == "__main__":
    main()
