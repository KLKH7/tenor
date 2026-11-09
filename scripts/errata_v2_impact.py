"""Recompute the judge re-read's reference headlines (E1, E2, deletion study) under AMI's
original gold and under gold errata v2, side by side. Offline: no LLM, no network.

    uv run --no-sync python scripts/errata_v2_impact.py [--out data/private/ERRATA_V2_IMPACT.md]

Inputs: the re-read's per-row verdicts (``data/private/JUDGE_REREAD_e1.jsonl``,
``JUDGE_REREAD_deletion.jsonl``; private), E1/E2/deletion result files under
``data/ami/results/`` and ``data/ami_questions/gold_errata.v2.json``. Under v2 the errata's
``question_rules`` apply: excluded questions leave every mean and paired test; regolded questions
keep their verdicts except where :data:`E1_OVERRIDES` / :data:`DELETION_V2` re-score them
against the new gold (each with its reason). Prints Markdown; ``--out`` also writes it.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from math import comb
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "packages")]

from tenor.ami.errata import ERRATA_V2, Errata, load_errata  # noqa: E402

E1_ROWS = ROOT / "data/private/JUDGE_REREAD_e1.jsonl"
DEL_ROWS = ROOT / "data/private/JUDGE_REREAD_deletion.jsonl"
E1_RESULT = ROOT / "data/ami/results/e1/20261106T002704Z.json"
E2_RESULT = ROOT / "data/ami/results/e2/20261106T022822Z.json"
DEL_RESULT = ROOT / "data/ami/results/deletion/20261105T211504Z.jsonl"
QUESTIONS = ROOT / "data/ami_questions/questions.jsonl"

# E1 item re-scores under v2 (reference side): key -> (gold ids, right, reason). Every other item
# of a regolded question keeps its reference verdict, minus dropped gold ids; an item credited
# only through a dropped gold item then keeps "right" only if the reference called it true.
E1_OVERRIDES: dict[str, tuple[list[str], bool, str]] = {
    "ami-IB4003-decision-01|L|I20": (
        [],
        False,
        "credited only via G2 ('no cafeteria'), dropped as a fact rather than a decision",
    ),
    "ami-IS1008d-decision-03|L|I9": (
        ["G1"],
        True,
        "'launch after evaluation approval' is now part of the corrected G1 (L0394)",
    ),
}
# Checked, unchanged under v2 (the reference already mapped them against the transcript):
E1_CHECKED = (
    "IS1008d decision-02 (L I1/I2 still state the corrected G1), action_what-03 (C A2, N A2, "
    "L I6/I7 state the prototype evaluation), problem-01 (G1-mapped items state the near-budget "
    "LCD cost or the profit goal, both in the corrected G1), IS1008c decision-01 (no item mapped "
    "to the dropped G3)."
)

# Deletion-study verdicts against the v2 gold for the regolded questions (judge.v1 rubric, all
# five systems). Each equals the re-read's v_adj verdict, checked against the v2 gold text.
_ALL = ("granola_chat", "notes_only", "notes_only_wispr", "notes_facts", "notes_facts_fallback")
_P, _C, _I = "partially_correct", "correct", "incorrect"
DELETION_V2: dict[str, tuple[dict[str, str], str]] = {
    "ami-IS1008d-decision-02": (
        dict(zip(_ALL, (_P, _I, _I, _I, _P), strict=True)),
        "same as v_gold: A has G2 only; D states both, unattributed; B/B'/C abstain",
    ),
    "ami-IS1008d-decision-03": (
        dict(zip(_ALL, (_C, _C, _C, _C, _C), strict=True)),
        "all state the 2-4 week prototype evaluation outside the factory (the corrected gold)",
    ),
    "ami-IS1008d-action_what-03": (
        dict(zip(_ALL, (_C, _C, _I, _C, _C), strict=True)),
        "all but B' list the prototype evaluation; B' says no group items",
    ),
    "ami-IS1008d-problem-01": (
        dict(zip(_ALL, (_C, _P, _C, _P, _P), strict=True)),
        "same as v_gold: B/C/D miss the profit-goal half",
    ),
    "ami-IB4003-decision-01": (
        dict(zip(_ALL, (_C, _P, _C, _P, _P), strict=True)),
        "G1 only: A/B' correct; B/C/D add a wrong next-meeting day (Thursday, not Tuesday)",
    ),
    "ami-IS1008c-decision-01": (
        dict(zip(_ALL, (_C, _C, _C, _C, _C), strict=True)),
        "all state the features and no display until costed; the manufacturing item is dropped",
    ),
}
DEL_NAME = {
    "granola_chat": "A",
    "notes_only": "B",
    "notes_only_wispr": "B'",
    "notes_facts": "C",
    "notes_facts_fallback": "D",
}


def binom_p(wins: int, losses: int) -> float:
    """Two-sided exact sign test."""
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    return min(1.0, 2.0 * sum(comb(n, i) for i in range(k + 1)) / float(2**n))


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def _mean(xs: Iterable[float | None]) -> float:
    vals = [x for x in xs if x is not None]
    return float(statistics.fmean(vals)) if vals else float("nan")


# -- E1 / E2 ----------------------------------------------------------------------------------


def dropped_gold_ids(
    errata: Errata, questions: Mapping[str, Mapping[str, Any]]
) -> dict[str, set[str]]:
    """Question id -> the G ids (position in gold_sentence_ids) that v2 drops."""
    drops = {c.sentence_id for c in errata.corrections if c.action == "drop"}
    out: dict[str, set[str]] = {}
    for qid, q in questions.items():
        ids = {f"G{i}" for i, s in enumerate(q["gold_sentence_ids"], 1) if s in drops}
        if ids:
            out[qid] = ids
    return out


def e1_items(
    rows: Sequence[Mapping[str, Any]],
    excluded: set[str],
    dropped: Mapping[str, set[str]],
    v2: bool,
) -> list[dict[str, Any]]:
    """One record per answer item: judge and reference gold ids / right, under the original gold
    or (``v2``) under errata v2."""
    out = []
    for r in rows:
        qid = r["qid"]
        if v2 and qid in excluded:
            continue
        drop = dropped.get(qid, set()) if v2 else set()
        j_gold = sorted(set(r["judge"]["gold"]) - drop)
        j_right = bool(j_gold) or bool(r["judge"]["in_transcript"])
        ref = r["reference"]
        r_gold = sorted(set(ref["gold"]) - drop)
        r_right = (
            bool(ref["right"])
            if not (set(ref["gold"]) & drop)
            else (bool(r_gold) or bool(ref["true"]))
        )
        if v2 and r["key"] in E1_OVERRIDES:
            r_gold, r_right, _ = E1_OVERRIDES[r["key"]]
        out.append(
            {
                "key": r["key"],
                "qid": qid,
                "cond": r["condition"],
                "item": r["key"].split("|")[2],
                "j_gold": j_gold,
                "j_right": j_right,
                "r_gold": list(r_gold),
                "r_right": r_right,
            }
        )
    return out


def e1_scores(
    items: Sequence[Mapping[str, Any]],
    records: Mapping[str, Mapping[str, Any]],
    excluded: set[str],
    dropped: Mapping[str, set[str]],
    v2: bool,
) -> dict[tuple[str, str], dict[str, dict[str, float | None]]]:
    """(condition, judge|ref) -> qid -> recall / lenient precision / strict precision / F1."""
    by: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for x in items:
        by.setdefault((x["qid"], x["cond"]), []).append(x)
    res: dict[tuple[str, str], dict[str, dict[str, float | None]]] = {}
    for qid, rec in records.items():
        if v2 and qid in excluded:
            continue
        gids = {g["id"] for g in rec["gold"]} - (dropped.get(qid, set()) if v2 else set())
        conf = rec.get("confirmed", {})
        for cond in ("C", "N", "L", "LD"):
            its = by.get((qid, "L" if cond == "LD" else cond), [])
            if cond == "LD":
                its = [x for x in its if conf.get(x["item"])]
            for who in ("judge", "ref"):
                g, a = ("j_gold", "j_right") if who == "judge" else ("r_gold", "r_right")
                covered = {i for x in its for i in x[g]} & gids
                rec_ = len(covered) / len(gids)
                n = len(its)
                pl = sum(bool(x[a]) for x in its) / n if n else None
                ps = sum(bool(x[g]) for x in its) / n if n else None
                f1 = 0.0 if not pl or not rec_ else 2 * pl * rec_ / (pl + rec_)
                res.setdefault((cond, who), {})[qid] = {
                    "recall": rec_,
                    "p_len": pl,
                    "p_str": ps,
                    "f1": f1,
                }
    return res


def e1_means(
    res: Mapping[tuple[str, str], Mapping[str, Mapping[str, float | None]]],
    records: Mapping[str, Mapping[str, Any]],
    split: str,
) -> dict[tuple[str, str, str], float]:
    out = {}
    for (cond, who), per_q in res.items():
        qs = [v for q, v in per_q.items() if split == "all" or records[q]["split"] == split]
        for m in ("recall", "p_len", "p_str", "f1"):
            out[(cond, who, m)] = _mean(v[m] for v in qs)
        out[(cond, who, "n")] = len(qs)
    return out


def e1_paired(
    res: Mapping[tuple[str, str], Mapping[str, Mapping[str, float | None]]],
    a: str,
    b: str,
    who: str,
    metric: str,
) -> tuple[int, int, float]:
    """Questions where ``b`` beats / loses to ``a`` on ``metric``, and the sign-test p."""
    w = loss = 0
    for q, x in res[(a, who)].items():
        y = res[(b, who)][q]
        if x[metric] is None or y[metric] is None:
            continue
        w += y[metric] > x[metric]  # type: ignore[operator]
        loss += y[metric] < x[metric]  # type: ignore[operator]
    return w, loss, binom_p(w, loss)


def e2_rates(
    e2: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
    excluded: set[str],
    v2: bool,
    split: str,
) -> dict[str, tuple[int, int, int]]:
    """kept / dropped extras: (n, judge right, reference right)."""
    idx = {x["key"]: x for x in items}
    out = {"kept": [0, 0, 0], "dropped": [0, 0, 0]}
    for r in e2["records"]:
        if split != "all" and r["split"] != split:
            continue
        if v2 and r["qid"] in excluded:
            continue
        kept = set(r["variants"]["A"]["extras"])
        for iid in r["variants"]["U"]["extras"]:
            x = idx[f"{r['qid']}|L|{iid}"]
            t = out["kept" if iid in kept else "dropped"]
            t[0] += 1
            t[1] += bool(x["j_right"])
            t[2] += bool(x["r_right"])
    return {k: (v[0], v[1], v[2]) for k, v in out.items()}


# -- deletion ---------------------------------------------------------------------------------


def deletion_table(
    rows: Sequence[Mapping[str, Any]], excluded: set[str], v2: bool
) -> dict[tuple[str, str], dict[str, str]]:
    """(qid, system) -> {judge, ref}: the reference is v_gold, or the v2 re-score."""
    out = {}
    for r in rows:
        if v2 and r["qid"] in excluded:
            continue
        ref = r["ref_v_gold"]
        if v2 and r["qid"] in DELETION_V2:
            ref = DELETION_V2[r["qid"]][0][r["system"]]
        out[(r["qid"], r["system"])] = {"judge": r["judge"], "ref": ref}
    return out


def deletion_paired(
    tab: Mapping[tuple[str, str], Mapping[str, str]],
    a: str,
    b: str,
    who: str,
    ok: Callable[[str], bool],
) -> tuple[int, int, float]:
    qids = sorted({q for q, _ in tab})
    w = sum(ok(tab[(q, a)][who]) and not ok(tab[(q, b)][who]) for q in qids)
    loss = sum(ok(tab[(q, b)][who]) and not ok(tab[(q, a)][who]) for q in qids)
    return w, loss, binom_p(w, loss)


def _correct(v: str) -> bool:
    return v == "correct"


def _not_wrong(v: str) -> bool:
    return v != "incorrect"


# -- report -----------------------------------------------------------------------------------

Table = Mapping[tuple[str, str], Mapping[str, str]]
Paired = tuple[int, int, float]


def _pct(x: float) -> str:
    return f"{x:.0%}"


def _wl(t: Paired) -> str:
    ps = "p < 0.001" if t[2] < 0.001 else f"p = {t[2]:.3f}"
    return f"{t[0]}-{t[1]}, {ps}"


def _flag(old: Paired, new: Paired) -> str:
    """The winner flips, a tie appears or breaks, or the result crosses p = 0.05."""
    s_old = (old[0] > old[1]) - (old[0] < old[1])
    s_new = (new[0] > new[1]) - (new[0] < new[1])
    if s_old and s_new and s_old != s_new:
        return "**direction changes**"
    if s_old != s_new:
        return "tie broken" if not s_old else "becomes a tie"
    if (old[2] < 0.05) != (new[2] < 0.05):
        return "**crosses p = 0.05**"
    return ""


def _row(*cells: object) -> str:
    return "| " + " | ".join(str(c) for c in cells) + " |"


def _header(*cols: str) -> list[str]:
    return [_row(*cols), _row(*("---" for _ in cols))]


def e1_section(
    items0: Sequence[Mapping[str, Any]],
    items2: Sequence[Mapping[str, Any]],
    records: Mapping[str, Mapping[str, Any]],
    excluded: set[str],
    dropped: Mapping[str, set[str]],
) -> list[str]:
    res0 = e1_scores(items0, records, excluded, dropped, v2=False)
    res2 = e1_scores(items2, records, excluded, dropped, v2=True)
    out = ["## E1: list answers (reference verdicts; mean per question)\n"]
    out += _header("Split", "Cond", "Recall", "Lenient P", "Strict P", "F1", "Questions")
    for split in ("all", "test"):
        m0, m2 = e1_means(res0, records, split), e1_means(res2, records, split)
        for cond in ("C", "N", "L", "LD"):
            cells = [
                f"{m0[(cond, 'ref', m)]:.2f} → {m2[(cond, 'ref', m)]:.2f}"
                for m in ("recall", "p_len", "p_str", "f1")
            ]
            n = f"{m0[(cond, 'ref', 'n')]} → {m2[(cond, 'ref', 'n')]}"
            out.append(_row(split, cond, *cells, n))
    out += ["", "Each cell: original gold → errata v2. Judge verdicts, same questions (all):\n"]
    out += _header("Cond", "Recall", "Lenient P", "F1")
    m0, m2 = e1_means(res0, records, "all"), e1_means(res2, records, "all")
    for cond in ("C", "N", "L", "LD"):
        cells = [
            f"{m0[(cond, 'judge', m)]:.2f} → {m2[(cond, 'judge', m)]:.2f}"
            for m in ("recall", "p_len", "f1")
        ]
        out.append(_row(cond, *cells))
    out += ["", "Paired tests (b vs a, wins-losses over questions, reference verdicts):\n"]
    out += _header("Pair", "Metric", "Original gold", "Errata v2", "Flag")
    for a, b in (("C", "L"), ("C", "LD"), ("N", "L"), ("L", "LD"), ("N", "C")):
        for metric, name in (("recall", "recall"), ("p_len", "lenient precision"), ("f1", "F1")):
            x0, x2 = e1_paired(res0, a, b, "ref", metric), e1_paired(res2, a, b, "ref", metric)
            out.append(_row(f"{b} vs {a}", name, _wl(x0), _wl(x2), _flag(x0, x2)))
    return [*out, ""]


def e2_section(
    e2: Mapping[str, Any],
    items0: Sequence[Mapping[str, Any]],
    items2: Sequence[Mapping[str, Any]],
    excluded: set[str],
) -> list[str]:
    out = ["## E2: extras kept vs dropped (share right, reference verdicts)\n"]
    out += _header("Split", "Kept: n, right (orig → v2)", "Dropped: n, right (orig → v2)")
    for split in ("test", "all"):
        r0 = e2_rates(e2, items0, excluded, False, split)
        r2 = e2_rates(e2, items2, excluded, True, split)
        cells = []
        for k in ("kept", "dropped"):
            (n0, _, x0), (n2, _, x2) = r0[k], r2[k]
            cells.append(f"{n0} → {n2}: {_pct(x0 / n0)} → {_pct(x2 / n2)}")
        out.append(_row(split, *cells))
    return [*out, ""]


def _rate(tab: Table, system: str, ok: Callable[[str], bool]) -> str:
    vals = [ok(v["ref"]) for (_, sy), v in tab.items() if sy == system]
    return _pct(sum(vals) / len(vals))


def _confident(tab: Table, system: str, abst: Mapping[tuple[str, str], bool]) -> int:
    """Incorrect and not abstaining (for Granola Chat every incorrect answer counts)."""
    return sum(
        v["ref"] == "incorrect" and (system == "granola_chat" or not abst[(q, sy)])
        for (q, sy), v in tab.items()
        if sy == system
    )


DEL_PAIRS = (
    ("granola_chat", "notes_only"),
    ("notes_facts_fallback", "notes_facts"),
    ("granola_chat", "notes_facts_fallback"),
    ("notes_only", "notes_only_wispr"),
    ("notes_facts", "notes_only"),
)


def deletion_section(
    rows: Sequence[Mapping[str, Any]], excluded: set[str], abst: Mapping[tuple[str, str], bool]
) -> list[str]:
    t0 = deletion_table(rows, excluded, v2=False)
    t2 = deletion_table(rows, excluded, v2=True)
    nq0, nq2 = len({q for q, _ in t0}), len({q for q, _ in t2})
    out = [f"## Deletion study (reference: v_gold → v2; {nq0} → {nq2} questions)\n"]
    out += _header("Condition", "Correct", "Not wrong", "Confident errors")
    for s in _ALL:
        out.append(
            _row(
                DEL_NAME[s],
                f"{_rate(t0, s, _correct)} → {_rate(t2, s, _correct)}",
                f"{_rate(t0, s, _not_wrong)} → {_rate(t2, s, _not_wrong)}",
                f"{_confident(t0, s, abst)} → {_confident(t2, s, abst)}",
            )
        )
    out += ["", "Paired tests (wins-losses over questions):\n"]
    out += _header("Pair", "Metric", "Original gold (v_gold)", "Errata v2", "Flag")
    for who in ("ref", "judge"):
        if who == "judge":
            out += [
                "",
                "Judge verdicts (v2 column: excluded questions removed; regolded questions keep "
                "the judge's original-gold verdict, since the judge was not re-run):\n",
            ]
            out += _header("Pair", "Metric", "Judge, all 68", "Judge, v2 question set", "Flag")
        for a, b in DEL_PAIRS:
            for ok, name in ((_correct, "correct"), (_not_wrong, "not wrong")):
                x0 = deletion_paired(t0, a, b, who, ok)
                x2 = deletion_paired(t2, a, b, who, ok)
                pair = f"{DEL_NAME[a]} vs {DEL_NAME[b]}"
                out.append(_row(pair, name, _wl(x0), _wl(x2), _flag(x0, x2)))
    return [*out, ""]


def intro(errata: Errata, e1q: set[str], delq: set[str]) -> list[str]:
    excluded = {c.qid for c in errata.question_changes if c.effect == "exclude"}
    regold = {c.qid for c in errata.question_changes if c.effect == "regold"}
    out = [
        "# Errata v2: impact on the judge re-read's reference headlines\n",
        f"Gold: AMI 1.6.2 as published vs `{errata.gold_version}` "
        "(`data/ami_questions/gold_errata.v2.json`). Offline recomputation from the re-read's "
        "per-row reference verdicts (`JUDGE_REREAD_*.jsonl`); no LLM calls. Generated by "
        "`scripts/errata_v2_impact.py`.\n",
        "## What v2 changes in these studies\n",
        f"- E1 (30 questions): excluded {sorted(excluded & e1q)}; regolded {sorted(regold & e1q)}.",
        f"- Deletion study (68 questions): excluded {sorted(excluded & delq)}; "
        f"regolded {sorted(regold & delq)}.",
        "- E2 uses E1's items: its excluded questions' extras leave E2 too.",
        "- Rules (`question_rules` in the errata file): excluded questions leave every mean and "
        "paired test; regolded questions are re-scored against the new gold.",
        "- E1 re-scores (reference side):",
    ]
    for k, (g, r, why) in E1_OVERRIDES.items():
        out.append(f"  - `{k}`: gold {g or '[]'}, right {r}. {why}.")
    out.append(f"  - Checked and unchanged: {E1_CHECKED}")
    out.append(
        "- Deletion re-scores (all equal the re-read's `v_adj`, checked against the v2 gold):"
    )
    for q, (v, why) in DELETION_V2.items():
        vs = ", ".join(f"{DEL_NAME[s]} {v[s].replace('_correct', '')}" for s in _ALL)
        out.append(f"  - `{q}`: {vs}. {why}.")
    out.append(
        "- **Caveat:** the re-read's E1 reference already mapped items against the transcript "
        "where the gold was loose (e.g. IS1008d decision-03, problem-01), so most of v2's "
        "content corrections were in effect already in the reference. E1's moves below come "
        "from the two excluded questions and the two dropped items. The judge columns under v2 "
        "drop the excluded questions and dropped gold ids mechanically; the judge was not re-run "
        "on the corrected texts.\n"
    )
    return out


def report(errata: Errata) -> str:
    """The side-by-side as Markdown."""
    questions = {q["id"]: q for q in _jsonl(QUESTIONS)}
    excluded = {c.qid for c in errata.question_changes if c.effect == "exclude"}
    dropped = dropped_gold_ids(errata, questions)
    e1_rows = _jsonl(E1_ROWS)
    records = {r["qid"]: r for r in json.loads(E1_RESULT.read_text())["records"]}
    del_rows = _jsonl(DEL_ROWS)
    abst = {(r["qid"], r["system"]): bool(r["abstained"]) for r in _jsonl(DEL_RESULT)}
    items0 = e1_items(e1_rows, excluded, dropped, v2=False)
    items2 = e1_items(e1_rows, excluded, dropped, v2=True)
    out = intro(errata, set(records), {r["qid"] for r in del_rows})
    out += e1_section(items0, items2, records, excluded, dropped)
    out += e2_section(json.loads(E2_RESULT.read_text()), items0, items2, excluded)
    out += deletion_section(del_rows, excluded, abst)
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    ap.add_argument("--errata", type=Path, default=ROOT / ERRATA_V2)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    text = report(load_errata(args.errata))
    print(text)
    if args.out:
        args.out.write_text(text + "\n")


if __name__ == "__main__":
    main()
