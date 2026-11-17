"""Meeting memory, phase 2: break each condition's answers into retrieved, retained, accurate
(rules frozen in :mod:`tenor.evals.memory_breakdown`).

    uv run --no-sync python scripts/memory_breakdown.py dump --run qa_v2_test
    uv run --no-sync python scripts/memory_breakdown.py calib-jobs --runs qa_v2_dev,qa_v2_dev_draft1
    uv run --no-sync python scripts/memory_breakdown.py calibrate
    uv run --no-sync python scripts/memory_breakdown.py record-jobs --run qa_v2_test
    uv run --no-sync python scripts/memory_breakdown.py score --run qa_v2_test
    uv run --no-sync python scripts/memory_breakdown.py handcheck-job --run qa_v2_test

``dump`` replays every logged lookup (checking the logged line and character counts), finds the
lines each answering agent saw and decides retrieval per fact; it reads no verdict. The two
``*-jobs`` commands write packets for blind agent readers (``memory_fact_reader.v1``,
``memory_record_reader.v1``) whose JSONL goes to ``readers/a|b|c/``. ``score`` joins the
verdicts and writes ``breakdown/score.json``. Same inputs -> byte-identical files (no dates or
SHAs are written into outputs except ``score.json``'s ``meta``). Local data only; no network,
no API.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
from pathlib import Path
from typing import Any

from tenor.ami.corpus import AmiMeeting, load_meeting
from tenor.ami.questions import AmiQuestion
from tenor.evals import came_back as K
from tenor.evals import meeting_memory_qa as Q
from tenor.evals import memory_breakdown as B
from tenor.memory import series_check as C
from tenor.memory import series_record as R

REPO = Path(__file__).resolve().parents[1]
QA = REPO / "data/private/memory/qa"
CALIB = QA / "breakdown_calib"
SEARCH_MAX = 20  # as in scripts/meeting_memory_v2.py
SEED = 42
FACT_PROMPT = "memory_fact_reader.v1"
RECORD_PROMPT = "memory_record_reader.v1"
HANDCHECK_N = 10

_MEETINGS: dict[str, AmiMeeting] = {}


def meeting(mid: str) -> AmiMeeting:
    if mid not in _MEETINGS:
        _MEETINGS[mid] = load_meeting(mid)
    return _MEETINGS[mid]


def meetings(series: str) -> list[str]:
    return [series + x for x in "abcd"]


def series_of(q: AmiQuestion) -> str:
    return q.meetings[0][:-1]


def group_of(q: AmiQuestion) -> str:
    return "cross" if len(q.meetings) > 1 else "single"


def final_record(series: str) -> R.SeriesRecord:
    path = REPO / f"data/private/memory/{series}/v2/record_after_{series}d.json"
    return R.SeriesRecord.model_validate_json(path.read_text(encoding="utf-8"))


def load_questions(run: str) -> list[AmiQuestion]:
    rows = json.loads((QA / run / "questions.json").read_text())["questions"]
    return [AmiQuestion.model_validate(r) for r in rows]


def load_answers(run: str) -> dict[tuple[str, str], Q.Answer]:
    out = {}
    for path in sorted((QA / run / "answers").glob("*.json")):
        a = Q.Answer.model_validate_json(path.read_text(encoding="utf-8"))
        out[(a.qid, a.condition)] = a
    return out


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def git_sha() -> str:
    out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    return out.stdout.strip()


def prompt_text(name: str) -> str:
    text = (REPO / "prompts" / f"{name}.md").read_text(encoding="utf-8")
    return text.split("\n## Changelog")[0].strip()


# -- facts -------------------------------------------------------------------------------------


def facts_of(q: AmiQuestion) -> list[B.Fact]:
    where: dict[str, B.LineKey] = {}
    sents = {}
    for mid in q.meetings:
        m = meeting(mid)
        for i, a in enumerate(m.dialogue_acts):
            where[a.da_id] = (mid, K.line_id(i))
        for s in m.sentences:
            sents[s.sentence_id] = s
    sentences = [
        (sid, sents[sid].text, [where[d] for d in sents[sid].links if d in where])
        for sid in q.gold_sentence_ids
    ]
    quoted = [where[d] for d in q.gold_evidence] if q.evidence_source == "quoted_act" else []
    return B.facts_for(q.id, sentences, quoted, q.gold_answer)


# -- replaying lookups -------------------------------------------------------------------------


def _indices(spec: str, n: int) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        lo, _, hi = part.strip().partition("-")
        a = int(lo.strip().lstrip("L"))
        b = int(hi.strip().lstrip("L")) if hi else a
        out += [i for i in range(a, b + 1) if 0 <= i < n]
    return sorted(set(out))


def replay(series: str, lk: Q.Lookup) -> str:
    """The text a logged lookup printed, recomputed; must match the logged counts."""
    if lk.command == "lines":
        m = meeting(lk.meeting)
        idx = _indices(lk.arg, len(m.dialogue_acts))
        text = "\n".join(f"{lk.meeting} {K.line(m, i)}" for i in idx)
        n = text.count("\n") + 1 if text else 0
    elif lk.command == "search":
        want = C.words(lk.arg)
        hits = []
        for mid in meetings(series) if lk.meeting == "*" else [lk.meeting]:
            m = meeting(mid)
            hits += [
                f"{mid} {K.line(m, i)}"
                for i, a in enumerate(m.dialogue_acts)
                if want and all(w in C.words(a.text) for w in want)
            ]
        shown = hits[:SEARCH_MAX]
        text = "\n".join(shown) or "(no line has all of these words)"
        if len(hits) > SEARCH_MAX:
            text += f"\n({len(hits) - SEARCH_MAX} more lines match; narrow the search)"
        n = len(shown)
    else:
        rec = final_record(series)
        got = next((e for e in rec.entries if e.entry_id == lk.arg), None)
        text = R.render_entry(got) if got else f"(no entry {lk.arg})"
        n = text.count("\n") + 1
    if (n, len(text)) != (lk.lines_returned, lk.chars_returned):
        raise SystemExit(
            f"replay of {lk.command} {lk.meeting} {lk.arg!r} gave {n} lines / {len(text)} chars, "
            f"logged {lk.lines_returned} / {lk.chars_returned}"
        )
    return text


def context_text(run: str, q: AmiQuestion, cond: str) -> str:
    """Everything the answering agent saw besides the instructions (see the frozen rules)."""
    name = f"{q.id}__{cond}"
    job = (QA / run / "jobs" / f"{name}.md").read_text(encoding="utf-8")
    end = job.index(f"\n## Question ({q.id})")
    parts: list[str] = []
    if cond == "record":
        parts.append(job[job.index("\n---\n") + 5 : end])
    elif cond == "named":
        parts.append(job[job.index("\n## Meeting ") + 1 : end])
    for d in _jsonl(QA / run / "lookups" / f"{name}.jsonl"):
        parts.append("## Lookup output\n" + replay(series_of(q), Q.Lookup.model_validate(d)))
    return "\n".join(parts)


def cmd_dump(run: str) -> None:
    qs = load_questions(run)
    out_facts: dict[str, Any] = {}
    out_ret: dict[str, Any] = {}
    for q in qs:
        facts = facts_of(q)
        out_facts[q.id] = [f.model_dump(mode="json") for f in facts]
        for cond in Q.CONDITIONS_V2:
            text = context_text(run, q, cond)
            seen = B.seen_lines(text)
            lines = text.splitlines()
            out_ret[f"{q.id}|{cond}"] = [
                B.retrieve(f, seen, lines).model_dump(mode="json") for f in facts
            ]
    base = QA / run / "breakdown"
    _dump(base / "facts.json", out_facts)
    _dump(base / "retrieval.json", out_ret)
    n = sum(len(v) for v in out_facts.values())
    by_rule = {
        r: sum(f["rule"] == r for v in out_facts.values() for f in v) for r in ("ids", "text")
    }
    qrule = {
        "all_ids": sum(all(f["rule"] == "ids" for f in v) for v in out_facts.values()),
        "some_text": sum(any(f["rule"] == "text" for f in v) for v in out_facts.values()),
    }
    print(f"{len(qs)} questions, {n} facts, by rule {by_rule}, questions {qrule}")
    for cond in Q.CONDITIONS_V2:
        rs = [r for k, v in out_ret.items() if k.endswith("|" + cond) for r in v]
        print(f"  {cond:7} facts retrieved {sum(r['retrieved'] for r in rs)}/{len(rs)}")


# -- blind fact readers (calibration set) ------------------------------------------------------


def calib_items(runs: list[str]) -> list[dict[str, Any]]:
    items = []
    for run in runs:
        answers = load_answers(run)
        for q in load_questions(run):
            for f in facts_of(q):
                for cond in Q.CONDITIONS_V2:
                    a = answers[(q.id, cond)]
                    items.append(
                        {
                            "key": f"{run}|{q.id}|{cond}|{f.fact_id}",
                            "question": q.question,
                            "gold": q.gold_answer,
                            "fact": f.text,
                            "answer": ("(abstained) " if a.abstain else "")
                            + (a.answer.strip() or "(no answer)"),
                            "rouge": round(B.rouge_l_recall(f.text, a.answer), 4),
                        }
                    )
    random.Random(f"{SEED}:breakdown.calib").shuffle(items)
    for i, it in enumerate(items, start=1):
        it["id"] = f"F{i:03d}"
    return items


def fact_packet(title: str, items: list[dict[str, Any]], out: Path) -> str:
    blocks = [
        f"### {it['id']}\nQuestion: {it['question']}\nFull gold answer: {it['gold']}\n"
        f"Fact to look for: {it['fact']}\nAnswer: {it['answer']}"
        for it in items
    ]
    return (
        "\n\n".join(
            [
                f"# Reader job {title}",
                f"Prompt `{FACT_PROMPT}`. Label all {len(items)} items below. Write your JSONL "
                f"to `{out}`. Use only this file.",
                prompt_text(FACT_PROMPT),
                "---",
                *blocks,
            ]
        )
        + "\n"
    )


def cmd_calib_jobs(runs: list[str]) -> None:
    items = calib_items(runs)
    _dump(CALIB / "items.json", {"runs": runs, "items": items})
    (CALIB / "jobs").mkdir(parents=True, exist_ok=True)
    blind = [{k: v for k, v in it.items() if k not in ("key", "rouge")} for it in items]
    for r in ("a", "b"):
        out = CALIB / "readers" / r / "labels.jsonl"
        (CALIB / "jobs" / f"reader_{r}.md").write_text(fact_packet(f"calib {r}", blind, out))
    print(f"{len(items)} items -> {CALIB / 'jobs'}")


def labels(path: Path, field: str) -> dict[str, str]:
    return {d["id"]: d[field] for d in _jsonl(path)}


def cmd_calib_tiebreak() -> None:
    items = json.loads((CALIB / "items.json").read_text())["items"]
    a = labels(CALIB / "readers/a/labels.jsonl", "states")
    b = labels(CALIB / "readers/b/labels.jsonl", "states")
    split = [it for it in items if a[it["id"]] != b[it["id"]]]
    blind = [{k: v for k, v in it.items() if k not in ("key", "rouge")} for it in split]
    out = CALIB / "readers" / "c" / "labels.jsonl"
    (CALIB / "jobs" / "reader_c.md").write_text(fact_packet("calib c (tie-break)", blind, out))
    print(f"a and b agree on {len(items) - len(split)} of {len(items)}; {len(split)} to c")


def cmd_calibrate() -> None:
    items = json.loads((CALIB / "items.json").read_text())["items"]
    a = labels(CALIB / "readers/a/labels.jsonl", "states")
    b = labels(CALIB / "readers/b/labels.jsonl", "states")
    c = labels(CALIB / "readers/c/labels.jsonl", "states")
    final, agree = B.settle(a, b, c)
    for v in final.values():
        if v not in ("yes", "no"):
            raise SystemExit(f"label {v!r}")
    cal = B.calibrate([it["rouge"] for it in items], [final[it["id"]] == "yes" for it in items])
    _dump(
        CALIB / "calibration.json",
        {
            "runs": json.loads((CALIB / "items.json").read_text())["runs"],
            "reader_prompt": FACT_PROMPT,
            "readers_agree": agree,
            "n": len(items),
            "labels": {it["key"]: final[it["id"]] for it in items},
            "calibration": cal.model_dump(mode="json"),
        },
    )
    print(json.dumps(cal.model_dump(mode="json") | {"readers_agree": agree}))


# -- blind record readers ----------------------------------------------------------------------


def record_items(run: str) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for q in load_questions(run):
        for f in facts_of(q):
            it = seen.setdefault(
                f.fact_id,
                {"fact_id": f.fact_id, "series": series_of(q), "fact": f.text, "questions": []},
            )
            it["questions"].append(q.question)
    items = sorted(seen.values(), key=lambda it: (it["series"], it["fact_id"]))
    for i, it in enumerate(items, start=1):
        it["id"] = f"R{i:03d}"
    return items


def record_packet(title: str, items: list[dict[str, Any]], out: Path) -> str:
    parts = [
        f"# Reader job {title}",
        f"Prompt `{RECORD_PROMPT}`. Label all {len(items)} items below. Write your JSONL to "
        f"`{out}`. Use only this file.",
        prompt_text(RECORD_PROMPT),
        "---",
    ]
    for s in sorted({it["series"] for it in items}):
        rec = final_record(s)
        parts += [
            f"# Series {s}",
            f"## Compact view of the {s} record (what an answering agent sees first)\n\n"
            + R.render(rec, compact=True),
            f"## Full view of the {s} record (every event and pointer)\n\n" + R.render(rec),
            f"## Facts to look for in the {s} record",
        ]
        for it in items:
            if it["series"] == s:
                qs = "\n".join(f"  - {x}" for x in it["questions"])
                parts.append(f"### {it['id']}\nFact: {it['fact']}\nAsked in question(s):\n{qs}")
    return "\n\n".join(parts) + "\n"


def cmd_record_jobs(run: str) -> None:
    base = QA / run / "breakdown" / "record"
    items = record_items(run)
    _dump(base / "items.json", {"run": run, "items": items})
    (base / "jobs").mkdir(parents=True, exist_ok=True)
    for r in ("a", "b"):
        out = base / "readers" / r / "labels.jsonl"
        (base / "jobs" / f"reader_{r}.md").write_text(record_packet(f"record {r}", items, out))
    print(f"{len(items)} facts -> {base / 'jobs'}")


def cmd_record_tiebreak(run: str) -> None:
    base = QA / run / "breakdown" / "record"
    items = json.loads((base / "items.json").read_text())["items"]
    a = labels(base / "readers/a/labels.jsonl", "label")
    b = labels(base / "readers/b/labels.jsonl", "label")
    split = [it for it in items if a[it["id"]] != b[it["id"]]]
    out = base / "readers" / "c" / "labels.jsonl"
    (base / "jobs" / "reader_c.md").write_text(record_packet("record c (tie-break)", split, out))
    print(f"a and b agree on {len(items) - len(split)} of {len(items)}; {len(split)} to c")


def record_labels(run: str) -> tuple[dict[str, B.RecordLabel], int, int]:
    base = QA / run / "breakdown" / "record"
    items = json.loads((base / "items.json").read_text())["items"]
    a = labels(base / "readers/a/labels.jsonl", "label")
    b = labels(base / "readers/b/labels.jsonl", "label")
    c = labels(base / "readers/c/labels.jsonl", "label")
    final, agree = B.settle(a, b, c)
    out: dict[str, B.RecordLabel] = {}
    for it in items:
        v = final[it["id"]]
        if v not in B.RECORD_LABELS:
            raise SystemExit(f"record label {v!r}")
        out[it["fact_id"]] = v  # type: ignore[assignment]
    return out, agree, len(items)


# -- scoring -----------------------------------------------------------------------------------


def verdicts(run: str) -> dict[tuple[str, str], str]:
    raw = json.loads((QA / run / "score.json").read_text())["verdicts"]
    return {tuple(k.split("|")): v for k, v in raw.items()}  # type: ignore[misc]


def rows_of(run: str, threshold: float) -> tuple[list[B.Row], dict[str, Any]]:
    facts = json.loads((QA / run / "breakdown" / "facts.json").read_text())
    ret = json.loads((QA / run / "breakdown" / "retrieval.json").read_text())
    answers = load_answers(run)
    ver = verdicts(run)
    rec, _, _ = record_labels(run)
    rows: list[B.Row] = []
    detail: dict[str, Any] = {}
    for q in load_questions(run):
        fs = [B.Fact.model_validate(f) for f in facts[q.id]]
        for cond in Q.CONDITIONS_V2:
            v = ver[(q.id, cond)]
            correct = v == "correct"
            got = [r["retrieved"] for r in ret[f"{q.id}|{cond}"]]
            a = answers[(q.id, cond)].answer
            scores = [round(B.rouge_l_recall(f.text, a), 4) for f in fs]
            kept = [s >= threshold or correct for s in scores]
            split = None
            if cond == "record" and not correct:
                split = B.record_split([rec[f.fact_id] for f in fs])
            row = B.Row(
                qid=q.id,
                condition=cond,
                group="cross" if len(q.meetings) > 1 else "single",
                verdict=v,
                category=B.classify(got, kept, correct),
                facts=len(fs),
                facts_retrieved=sum(got),
                facts_retained=sum(kept),
                record_split=split,
            )
            rows.append(row)
            detail[f"{q.id}|{cond}"] = row.model_dump(mode="json") | {
                "rouge": scores,
                "retrieval": ret[f"{q.id}|{cond}"],
                "record_labels": [rec[f.fact_id] for f in fs] if cond == "record" else None,
            }
    return rows, detail


def cmd_score(run: str) -> None:
    cal = json.loads((CALIB / "calibration.json").read_text())
    t = cal["calibration"]["threshold"]
    rows, detail = rows_of(run, t)
    _, rec_agree, rec_n = record_labels(run)
    tab = B.table(rows)
    # correct answers whose facts the rule says never reached the agent: a check on the rule
    tab["correct_but_not_all_retrieved"] = {
        c: sum(
            r.category == "correct" and r.facts_retrieved < r.facts
            for r in rows
            if r.condition == c
        )
        for c in Q.CONDITIONS_V2
    }
    rec_all, _, _ = record_labels(run)
    tab["record_labels_all_facts"] = {
        lab: sum(v == lab for v in rec_all.values()) for lab in B.RECORD_LABELS
    }
    _dump(
        QA / run / "breakdown" / "score.json",
        {
            "table": tab,
            "rows": detail,
            "meta": {
                "run": run,
                "rules": "tenor.evals.memory_breakdown (frozen)",
                "retained_threshold": t,
                "calibration": cal["calibration"] | {"readers_agree": cal["readers_agree"]},
                "text_threshold": B.TEXT_THRESHOLD,
                "window": B.WINDOW,
                "record_readers_agree": {"agree": rec_agree, "of": rec_n},
                "fact_prompt": FACT_PROMPT,
                "record_prompt": RECORD_PROMPT,
                "git": git_sha(),
            },
        },
    )
    print(json.dumps(tab, indent=1))


def cmd_handcheck_job(run: str) -> None:
    sc = json.loads((QA / run / "breakdown" / "score.json").read_text())["rows"]
    misses = sorted(k for k, v in sc.items() if k.endswith("|record") and v["verdict"] != "correct")
    pick = sorted(random.Random(f"{SEED}:breakdown.handcheck").sample(misses, HANDCHECK_N))
    qs = {q.id: q for q in load_questions(run)}
    answers = load_answers(run)
    facts = json.loads((QA / run / "breakdown" / "facts.json").read_text())
    blocks = []
    for k in pick:
        qid = k.split("|")[0]
        q, row = qs[qid], sc[k]
        fl = "\n".join(
            f"  - {f['fact_id']}: {f['text']} | evidence lines: "
            f"{', '.join(m + ' ' + ln for m, ln in f['evidence']) or '(none)'} | retrieved: "
            f"{r['retrieved']} via {r['via']} | ROUGE-L recall in answer: {s} | record label: {lab}"
            for f, r, s, lab in zip(
                facts[qid], row["retrieval"], row["rouge"], row["record_labels"], strict=True
            )
        )
        blocks.append(
            f"### {k}\nQuestion: {q.question}\nGold: {q.gold_answer}\nAnswer: "
            f"{answers[(qid, 'record')].answer}\nVerdict: {row['verdict']}\nFacts:\n{fl}\n"
            f"Automatic class: {row['category']}; record split: {row['record_split']}\n"
            f"What the agent saw (compact record, then each lookup's output): "
            f"{QA / run / 'breakdown' / 'handcheck' / 'contexts' / (qid + '__record.txt')}"
        )
    out = QA / run / "breakdown" / "handcheck"
    (out / "contexts").mkdir(parents=True, exist_ok=True)
    for k in pick:
        qid = k.split("|")[0]
        (out / "contexts" / f"{qid}__record.txt").write_text(context_text(run, qs[qid], "record"))
    (out / "job.md").write_text("\n\n".join(blocks) + "\n")
    print(f"{len(pick)} of {len(misses)} record misses -> {out / 'job.md'}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("dump", "record-jobs", "record-tiebreak", "score", "handcheck-job"):
        sp = sub.add_parser(name)
        sp.add_argument("--run", required=True)
    sp = sub.add_parser("calib-jobs")
    sp.add_argument("--runs", required=True)
    sub.add_parser("calib-tiebreak")
    sub.add_parser("calibrate")
    a = p.parse_args()
    if a.cmd == "dump":
        cmd_dump(a.run)
    elif a.cmd == "calib-jobs":
        cmd_calib_jobs(a.runs.split(","))
    elif a.cmd == "calib-tiebreak":
        cmd_calib_tiebreak()
    elif a.cmd == "calibrate":
        cmd_calibrate()
    elif a.cmd == "record-jobs":
        cmd_record_jobs(a.run)
    elif a.cmd == "record-tiebreak":
        cmd_record_tiebreak(a.run)
    elif a.cmd == "score":
        cmd_score(a.run)
    elif a.cmd == "handcheck-job":
        cmd_handcheck_job(a.run)


if __name__ == "__main__":
    main()
