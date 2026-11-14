"""Follow-through from a product's own notes: item fates and a writer ($0, agents).

    uv run --no-sync python scripts/follow_through.py dump-kind --run <RUN>
    uv run --no-sync python scripts/follow_through.py dump --run <RUN> [--split dev|test|all]
    uv run --no-sync python scripts/follow_through.py disagreements --run <RUN>
    uv run --no-sync python scripts/follow_through.py score --run <RUN> [--model NAME]
    uv run --no-sync python scripts/follow_through.py dump-check --run <RUN> [--split ...]
    uv run --no-sync python scripts/follow_through.py score-writer --run <RUN> [--model NAME]

Units are every consecutive AMI pair X -> Y for which a product has a reader packet of both
meetings (``data/private/reader/packets``), per product. A pair in ``follow_through.DEV_PAIRS``
is DEV; the rest are TEST. Everything is under ``data/private/reader``; ``<U>`` below is
``<X>__<Y>__<product>``.

``dump-kind`` writes a ``statement_kind_notes.v1`` job (``ft_jobs/kind/<X>__<product>.md``) for
each X whose notes have no labels in the earlier run ``kind_agent_runs/kind_20261110``; an agent
answers into ``ft_runs/<RUN>/kind/<X>__<product>.jsonl``.

``dump`` writes per unit a fates job (``ft_jobs/fates/<U>.md``: prompt ``item_fates.v1``, the
notes bullets labelled decision or task as items, Y's product transcript) and a writer job
(``ft_jobs/writer/<U>.md``: prompt ``since_last_time.v1``). Fates readers a and b answer into
``ft_runs/<RUN>/<a|b>/<U>.jsonl``; the writer into ``ft_runs/<RUN>/writer/<U>.jsonl``.

``disagreements`` prints the units where a and b differ on a fate and writes a tie-break job
per such unit to ``ft_jobs/tiebreak/<RUN>/<U>.md``; reader c answers into ``ft_runs/<RUN>/c/``.

``score`` prints the fates of notes items per product and split next to the annotation-based
fates of the same pairs (``fates_runs/fates_20261110`` and ``fates_extra_20261110``), reader
agreement, and writes ``ft_runs/<RUN>/score.json`` and ``rows.json``.

``dump-check`` validates the writer's lines and writes a checker job per unit with at least one
valid line (``ft_jobs/check/<RUN>/<U>.md``: prompt ``since_last_time_check.v1``); checkers a and
b answer into ``ft_runs/<RUN>/check_<a|b>/<U>.jsonl``. ``score-writer`` prints, per split, lines
supported and silent items covered, and writes ``writer_score.json`` and ``writer_rows.json``.

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

from tenor.evals import bullet_cues as B
from tenor.evals import came_back as K
from tenor.evals import follow_through as T
from tenor.evals import item_fates as F
from tenor.evals import statement_kind as SK

READERS = Path("data/private/reader")
PACKETS = READERS / "packets"
JOBS = READERS / "ft_jobs"
RUNS = READERS / "ft_runs"
KIND_RUN = READERS / "kind_agent_runs" / "kind_20261110" / "notes"
ANNOTATION_RUNS = (
    READERS / "fates_runs" / "fates_20261110",
    READERS / "fates_runs" / "fates_extra_20261110",
)
_PACKET = re.compile(r"^((?:ES|IS|TS)\d{4})([a-d])__(\w+)\.md$")


def units() -> list[tuple[K.Pair, str]]:
    """Every (consecutive pair, product) with a packet of both meetings, sorted."""
    have = {
        (m[1] + m[2], m[3]) for p in sorted(PACKETS.glob("*.md")) if (m := _PACKET.match(p.name))
    }
    out = []
    for meeting, product in sorted(have):
        nxt = meeting[:-1] + chr(ord(meeting[-1]) + 1)
        if (nxt, product) in have:
            out.append((K.Pair(x=meeting, y=nxt), product))
    return out


def split_of(pair: K.Pair) -> str:
    return "dev" if pair.name in T.DEV_PAIRS else "test"


def chosen(split: str) -> list[tuple[K.Pair, str]]:
    return [u for u in units() if split in ("all", split_of(u[0]))]


def name(pair: K.Pair, product: str) -> str:
    return f"{pair.name}__{product}"


def load(meeting: str, product: str) -> B.CueUnit:
    text = (PACKETS / f"{meeting}__{product}.md").read_text(encoding="utf-8")
    return B.parse_packet(text, meeting, product, "n/a")


def kinds_path(run: str, meeting: str, product: str) -> Path | None:
    """The statement-kind labels of a meeting's notes: this run's, else the earlier run's."""
    for d in (RUNS / run / "kind", KIND_RUN):
        path = d / f"{meeting}__{product}.jsonl"
        if path.exists():
            return path
    return None


def items_of(run: str, x: B.CueUnit) -> list[T.NoteItem]:
    path = kinds_path(run, x.meeting_id, x.product)
    if path is None:
        raise SystemExit(f"no statement-kind labels for {x.key}: run dump-kind and its agents")
    return T.note_items(x.bullets, T.parse_kinds(path.read_text(encoding="utf-8")))


def _prompt(prompt: str) -> str:
    return Path(f"prompts/{prompt}.md").read_text(encoding="utf-8")


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _meta(run: str, model: str, prompts: list[str]) -> dict[str, Any]:
    return {
        "run": run,
        "git_sha": _git_sha(),
        "model": model,
        "prompt_versions": prompts,
        "seed": None,  # nothing is sampled: every unit and every item is used
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "units": [name(p, prod) for p, prod in units()],
        "dev_pairs": sorted(T.DEV_PAIRS),
    }


def dump_kind(run: str) -> None:
    out = JOBS / "kind"
    out.mkdir(parents=True, exist_ok=True)
    text = _prompt(SK.NOTES_PROMPT)
    n = 0
    for meeting, product in sorted({(p.x, prod) for p, prod in units()}):
        if (KIND_RUN / f"{meeting}__{product}.jsonl").exists():
            continue
        x = load(meeting, product)
        (out / f"{meeting}__{product}.md").write_text(SK.notes_job_file(x, text), "utf-8")
        print(
            f"{x.key}\t{len(x.bullets)} bullets\t-> "
            f"{RUNS / run / 'kind'}/{meeting}__{product}.jsonl"
        )
        n += 1
    print(f"{n} kind jobs in {out} (prompt {SK.NOTES_PROMPT})")


def dump(run: str, split: str) -> None:
    for d in ("fates", "writer"):
        (JOBS / d).mkdir(parents=True, exist_ok=True)
    fates, writer = _prompt(T.FATES_PROMPT), _prompt(T.WRITER_PROMPT)
    print("unit\tsplit\tbullets\tdecisions\ttasks\ty_lines")
    for pair, product in chosen(split):
        x, y = load(pair.x, product), load(pair.y, product)
        its = items_of(run, x)
        u = name(pair, product)
        where = str(RUNS / run / "<your reader letter: a or b>" / f"{u}.jsonl")
        body = T.fates_job_file(x, y, its, fates, where)
        (JOBS / "fates" / f"{u}.md").write_text(body, encoding="utf-8")
        body = T.writer_job_file(x, y, writer, str(RUNS / run / "writer" / f"{u}.jsonl"))
        (JOBS / "writer" / f"{u}.md").write_text(body, encoding="utf-8")
        print(
            f"{u}\t{split_of(pair)}\t{len(x.bullets)}\t{sum(i.kind == 'decision' for i in its)}"
            f"\t{sum(i.kind == 'task' for i in its)}\t{len(y.transcript)}"
        )


def read_fates(
    run: str,
) -> tuple[list[dict[str, Any]], dict[str, list[T.NoteItem]], list[str]]:
    """Item rows of every unit both readers answered, each unit's items, and every problem."""
    rows: list[dict[str, Any]] = []
    items: dict[str, list[T.NoteItem]] = {}
    errors: list[str] = []
    for pair, product in units():
        u = name(pair, product)
        x, y = load(pair.x, product), load(pair.y, product)
        if kinds_path(run, pair.x, product) is None:
            errors.append(f"{u}: no statement-kind labels")
            continue
        its = items[u] = items_of(run, x)
        ids = [i.item_id for i in its]
        answers: dict[str, dict[str, F.Answer]] = {}
        for r in (*F.READERS, F.TIEBREAK):
            path = RUNS / run / r / f"{u}.jsonl"
            if not path.exists():
                if r in F.READERS:
                    errors.append(f"{u}: no answer from reader {r}")
                continue
            got, bad = F.parse_answers(path.read_text(encoding="utf-8"), ids, len(y.transcript))
            answers[r] = got
            errors += [f"{u} reader {r}: {e}" for e in bad]
            missing = [i for i in ids if i not in got]
            if r in F.READERS and missing:
                errors.append(f"{u} reader {r}: no valid answer for {missing}")
        if all(r in answers for r in F.READERS):
            rows += [
                {**row, "split": split_of(pair)} for row in T.item_rows(pair, product, its, answers)
            ]
    return rows, items, errors


def disagreements(run: str) -> None:
    rows, items, errors = read_fates(run)
    out = JOBS / "tiebreak" / run
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("*.md"):
        stale.unlink()
    prompt = _prompt(T.FATES_PROMPT)
    names = []
    for pair, product in units():
        u = name(pair, product)
        open_items = [
            r["x_item"]
            for r in rows
            if r["pair"] == pair.name and r["product"] == product and r["fate_a"] != r["fate_b"]
        ]
        if not open_items:
            continue
        names.append(u)
        x, y = load(pair.x, product), load(pair.y, product)
        where = str(RUNS / run / F.TIEBREAK / f"{u}.jsonl")
        body = T.fates_job_file(x, y, items[u], prompt, where, only=open_items)
        (out / f"{u}.md").write_text(body, encoding="utf-8")
        print(f"{u}: {len(open_items)} disputed: {', '.join(open_items)}")
    print(json.dumps(names))
    for e in errors:
        print(f"PROBLEM {e}")


def annotation_rows() -> list[dict[str, Any]]:
    """The earlier annotation-based item rows of the pairs used here (human items, human
    transcript), from the two earlier runs."""
    pairs = {p.name for p, _ in units()}
    out: list[dict[str, Any]] = []
    for d in ANNOTATION_RUNS:
        path = d / "rows.json"
        if path.exists():
            out += [r for r in json.loads(path.read_text())["items"] if r["pair"] in pairs]
    return out


def _print_dist(label: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    d = T.distribution(rows)
    silent = sum(T.is_silent(r) for r in rows)
    shown = ", ".join(f"{k} {v}" for k, v in d.items() if v or k != F.UNCLEAR)
    print(f"  {label}: {len(rows)} items; {shown}; silent {silent}")
    return {"items": len(rows), "fates": d, "silent": silent}


def score(run: str, model: str) -> None:
    rows, _, errors = read_fates(run)
    if not rows:
        raise SystemExit(f"run {run}: no unit has answers from both readers")
    ann = annotation_rows()
    result: dict[str, Any] = {"notes_items": {}, "annotation_items": {}, "per_unit": {}}
    for split in ("dev", "test"):
        print(f"== {split.upper()} ==")
        for product in B.PRODUCTS:
            sub = [r for r in rows if r["split"] == split and r["product"] == product]
            result["notes_items"][f"{split}:{product}"] = _print_dist(
                f"notes items, {product}", sub
            )
        sub = [r for r in ann if ("dev" if r["pair"] in T.DEV_PAIRS else "test") == split]
        result["annotation_items"][split] = _print_dist("annotated items, human transcript", sub)
    print("== per unit ==")
    for pair, product in units():
        sub = [r for r in rows if r["pair"] == pair.name and r["product"] == product]
        if sub:
            result["per_unit"][name(pair, product)] = _print_dist(name(pair, product), sub)
            for r in sub:
                if T.is_silent(r):
                    print(f"      silent {r['x_item']} [{r['fate']}] {r['text'][:110]}")
    for pair in sorted({p for p, _ in units()}, key=lambda p: p.name):
        sub = [r for r in ann if r["pair"] == pair.name]
        result["per_unit"][f"{pair.name}__annotation"] = _print_dist(f"{pair.name} annotated", sub)
    result["reader_agreement"] = {
        "fate": T.agreement(rows, "fate"),
        "needed_followup": T.agreement(rows, "needed"),
        "fate_test_only": T.agreement([r for r in rows if r["split"] == "test"], "fate"),
    }
    print(f"== readers a and b == {json.dumps(result['reader_agreement'])}")
    for e in errors:
        print(f"PROBLEM {e}")
    out = RUNS / run
    meta = {**_meta(run, model, [T.FATES_PROMPT, SK.NOTES_PROMPT]), "problems": errors}
    (out / "score.json").write_text(json.dumps({"meta": meta, "result": result}, indent=1) + "\n")
    (out / "rows.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {out / 'score.json'} and {out / 'rows.json'}")


def read_writer(
    run: str, pair: K.Pair, product: str
) -> tuple[list[dict[str, Any]], list[str]] | None:
    path = RUNS / run / "writer" / f"{name(pair, product)}.jsonl"
    if not path.exists():
        return None
    x, y = load(pair.x, product), load(pair.y, product)
    return T.parse_writer(path.read_text(encoding="utf-8"), [b.id for b in x.bullets], y)


def dump_check(run: str, split: str) -> None:
    out = JOBS / "check" / run
    out.mkdir(parents=True, exist_ok=True)
    prompt = _prompt(T.CHECK_PROMPT)
    print("unit\tlines\tok\tproblems")
    for pair, product in chosen(split):
        u = name(pair, product)
        got = read_writer(run, pair, product)
        if got is None:
            print(f"{u}\tNO WRITER OUTPUT")
            continue
        written, errors = got
        ok = [w["line"] for w in written if w["status"] == "ok"]
        bad = [f"{w['n']}: {w['status']}" for w in written if w["status"] != "ok"] + errors
        print(f"{u}\t{len(written)}\t{len(ok)}\t{'; '.join(bad) or '-'}")
        if not ok:
            continue
        x, y = load(pair.x, product), load(pair.y, product)
        where = str(RUNS / run / "check_<your checker letter: a or b>" / f"{u}.jsonl")
        body = T.check_job_file(x, y, items_of(run, x), ok, prompt, where)
        (out / f"{u}.md").write_text(body, encoding="utf-8")


def score_writer(run: str, model: str) -> None:
    fate_rows, items, errors = read_fates(run)
    rows: list[dict[str, Any]] = []
    for pair, product in units():
        u = name(pair, product)
        got = read_writer(run, pair, product)
        if got is None:
            errors.append(f"{u}: no writer output")
            continue
        written, bad = got
        errors += [f"{u} writer: {e}" for e in bad]
        n_ok = sum(w["status"] == "ok" for w in written)
        verdicts: dict[str, dict[int, T.Verdict]] = {}
        for c in T.CHECKERS:
            path = RUNS / run / f"check_{c}" / f"{u}.jsonl"
            if not path.exists():
                if n_ok:
                    errors.append(f"{u}: no answer from checker {c}")
                continue
            ids = [i.item_id for i in items.get(u, [])]
            v, bad = T.parse_verdicts(path.read_text(encoding="utf-8"), n_ok, ids)
            verdicts[c] = v
            errors += [f"{u} checker {c}: {e}" for e in bad]
        mine = [r for r in fate_rows if r["pair"] == pair.name and r["product"] == product]
        key = f"{pair.name}:{product}"
        rows += [{**r, "split": split_of(pair)} for r in T.line_rows(key, written, verdicts, mine)]
    result = {}
    for split in ("dev", "test"):
        sub = [r for r in rows if r["split"] == split]
        fr = [r for r in fate_rows if r["split"] == split]
        result[split] = T.writer_score(sub, fr)
        print(f"== {split.upper()} == {json.dumps(result[split])}")
        for r in sub:
            print(
                f"  {r['unit']} #{r['n']} [{r['kind']}] status={r['status']} "
                f"a={r['supported_a']} b={r['supported_b']} items={r['x_items']} "
                f"fates={r['item_fates']}\n      {r['sentence']}"
            )
    for e in errors:
        print(f"PROBLEM {e}")
    out = RUNS / run
    meta = {**_meta(run, model, [T.WRITER_PROMPT, T.CHECK_PROMPT]), "problems": errors}
    (out / "writer_score.json").write_text(
        json.dumps({"meta": meta, "result": result}, indent=1) + "\n"
    )
    (out / "writer_rows.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {out / 'writer_score.json'} and {out / 'writer_rows.json'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd in ("dump-kind", "dump", "disagreements", "score", "dump-check", "score-writer"):
        p = sub.add_parser(cmd)
        p.add_argument("--run", required=True)
        if cmd in ("dump", "dump-check"):
            p.add_argument("--split", choices=("dev", "test", "all"), default="all")
        if cmd in ("score", "score-writer"):
            p.add_argument("--model", default="unrecorded", help="the agents' model")
    a = ap.parse_args()
    if a.cmd == "dump-kind":
        dump_kind(a.run)
    elif a.cmd == "dump":
        dump(a.run, a.split)
    elif a.cmd == "disagreements":
        disagreements(a.run)
    elif a.cmd == "score":
        score(a.run, a.model)
    elif a.cmd == "dump-check":
        dump_check(a.run, a.split)
    else:
        score_writer(a.run, a.model)


if __name__ == "__main__":
    main()
