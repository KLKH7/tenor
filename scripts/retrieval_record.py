"""Step 5: does a running series record help an agent answer questions across meetings?

Two conditions on the same workspace (IS1008 a-d + 20 distractors, Granola's real notes,
anonymous speakers), the same questions (every IS1008 cross-meeting question) and the same
answer prompt (retrieval_answer.v2):
  tools         the four Granola-shaped tools
  tools_record  the same four plus get_series_record (the v2 record after IS1008d)

IS1008 is the development series of the record itself: a development comparison, not a result.

  setup | readers | score
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
from tenor.retrieval.tools import WORDING, ToolCall

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from memory_breakdown import facts_of  # noqa: E402

OUT = REPO / "data/private/retrieval/record_dev"
PILOT_WS = REPO / "data/private/retrieval/pilot/workspace.json"
RECORD = "data/private/memory/IS1008/v2/record_after_IS1008d.json"
CONDITIONS = ("tools", "tools_record")
RUNS = 2
SEED = 42
TOOLS = ["search_meetings", "search_transcripts", "get_transcript", "get_meetings"]


def questions() -> list[str]:
    return sorted(q.id for q in load_questions() if "IS1008" in q.id and len(q.meetings) > 1)


def tag(cond: str, qid: str, run: int) -> str:
    return f"{cond}__{qid}__run{run}"


def cmd_setup() -> None:
    base = json.loads(PILOT_WS.read_text())
    prompt = (REPO / "prompts/retrieval_answer.v2.md").read_text().split("\n## Changelog")[0]
    qs = {q.id: q for q in load_questions()}
    for d in ("jobs", "logs", "answers", "readers"):
        (OUT / d).mkdir(parents=True, exist_ok=True)
    for cond in CONDITIONS:
        ws = dict(base)
        tools = list(TOOLS)
        if cond == "tools_record":
            ws["records"] = {"IS1008": str(REPO / RECORD)}
            tools.append("get_series_record")
        wpath = OUT / f"workspace_{cond}.json"
        wpath.write_text(json.dumps(ws, indent=1) + "\n")
        tool_list = "\n".join(f"- {t}: {WORDING['v1'][t]}" for t in tools)
        for qid in questions():
            for run in range(RUNS):
                t = tag(cond, qid, run)
                cmd = (
                    f"uv run python scripts/retrieval_tools.py --workspace {wpath} "
                    f"--log {OUT / 'logs' / (t + '.jsonl')}"
                )
                job = (
                    f"{prompt}\n\n## Tools\n\n{WORDING['v1']['server']}\n\n{tool_list}\n\n"
                    f"## Your job\n\nQuestion: {qs[qid].question}\n\n"
                    f"Tool command (run from {REPO}):\n\n    {cmd} <tool> '<json args>'\n\n"
                    f"Example:\n\n    {cmd} search_meetings '{{}}'\n\n"
                    f"Answer file: {OUT / 'answers' / (t + '.txt')}\n"
                )
                (OUT / "jobs" / f"{t}.md").write_text(job)
    print(len(list((OUT / "jobs").glob("*.md"))), "jobs")


def cmd_readers() -> None:
    qs = {q.id: q for q in load_questions()}
    prompt = (REPO / "prompts/mcp_reading_reader.v1.md").read_text().split("\n## Changelog")[0]
    rng = random.Random(SEED)
    key: dict[str, dict[str, dict[str, int]]] = {}
    for cond in CONDITIONS:
        parts, key[cond] = [prompt, "\n## Packet\n"], {}
        shown: set[str] = set()
        for qid in questions():
            for mid in qs[qid].meetings:
                if mid not in shown:
                    shown.add(mid)
                    parts.append(f"\n### Meeting {mid}: full human transcript\n")
                    parts.extend(
                        f"{k} {v.speaker}: {v.text}"
                        for k, v in transcript(load_meeting(mid)).items()
                    )
            runs = list(range(RUNS))
            rng.shuffle(runs)
            key[cond][qid] = dict(zip("XY", runs, strict=True))
            parts.append(
                f"\n### Question {qid}\n{qs[qid].question}\n\nGold answer: {qs[qid].gold_answer}\n"
            )
            for lab, run in key[cond][qid].items():
                ans = (OUT / "answers" / f"{tag(cond, qid, run)}.txt").read_text().strip()
                parts.append(f"\nAnswer {lab}:\n{ans or '(abstained)'}\n")
        (OUT / "readers" / f"packet_{cond}.md").write_text("\n".join(parts) + "\n")
    (OUT / "readers" / "key.json").write_text(json.dumps(key, indent=1) + "\n")


def settled(cond: str) -> dict[tuple[str, int], str]:
    key = json.loads((OUT / "readers/key.json").read_text())[cond]
    by: dict[tuple[str, int], list[str]] = {}
    for f in sorted((OUT / "readers").glob(f"verdicts_{cond}_*.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                v = json.loads(line)
                by.setdefault((v["qid"], key[v["qid"]][v["label"]]), []).append(v["verdict"])
    return {
        k: vs[-1] if len(vs) > 2 else (vs[0] if len(set(vs)) == 1 else "disputed")
        for k, vs in by.items()
    }


def cmd_score() -> None:
    qs = {q.id: q for q in load_questions()}
    out: dict[str, object] = {
        "git": subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
        ).stdout.strip(),
        "seed": SEED,
        "answer_prompt": "retrieval_answer.v2",
        "reader_prompt": "mcp_reading_reader.v1",
        "record": RECORD,
        "conditions": {},
    }
    for cond in CONDITIONS:
        ws = json.loads((OUT / f"workspace_{cond}.json").read_text())
        known = [*ws["meetings"], *ws["distractors"]]
        v = settled(cond)
        trials = []
        for qid in questions():
            for run in range(RUNS):
                t = tag(cond, qid, run)
                log = [
                    ToolCall.model_validate_json(x)
                    for x in (OUT / "logs" / f"{t}.jsonl").read_text().splitlines()
                    if x.strip()
                ]
                trials.append(
                    score_trial(
                        qid,
                        run,
                        facts_of(qs[qid]),
                        (OUT / "answers" / f"{t}.txt").read_text(),
                        log,
                        v.get((qid, run)) == "correct",
                        known,
                        qs[qid].question,
                    )
                )
        out["conditions"][cond] = {  # type: ignore[index]
            "summary": summarize(trials)["all"].model_dump(),
            "verdicts": {f"{q}|{r}": x for (q, r), x in sorted(v.items())},
            "trials": [
                t.model_dump() | {"passed": t.passed, "category": t.category} for t in trials
            ],
            "record_calls": sum(
                1
                for qid in questions()
                for run in range(RUNS)
                for x in (OUT / "logs" / f"{tag(cond, qid, run)}.jsonl").read_text().splitlines()
                if '"get_series_record"' in x
            ),
        }
    (OUT / "score.json").write_text(json.dumps(out, indent=1) + "\n")
    for cond, c in out["conditions"].items():  # type: ignore[attr-defined]
        print(cond, json.dumps(c["summary"]), "record calls:", c["record_calls"])


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("cmd", choices=["setup", "readers", "score"])
    {"setup": cmd_setup, "readers": cmd_readers, "score": cmd_score}[p.parse_args().cmd]()


if __name__ == "__main__":
    main()
