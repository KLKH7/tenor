"""Score the E3 human check sheet: Kian vs the judge and Kian vs the adjudicating agent.

    python3 -I scripts/e3_check_score.py answers.json data/private/E3_CHECK_key.json [--final]

``answers.json`` is what the sheet's "Copy my answers" button gives. By default each card's
first answer (given before the verdicts were revealed) is scored; ``--final`` scores the last.
"Can't tell" answers are counted and left out of agreement and kappa. Prints agreement overall,
by the judge's verdict (stratum), by product and by the reason the card was flagged, Cohen's
kappa, the cards where Kian and each automated reader disagree, and the answers to the
"is the summary itself correct?" question. Standard library only.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Sequence

LABELS = ("Fully", "Partly", "No")


def kappa(pairs: Sequence[tuple[str, str]]) -> float | None:
    """Cohen's kappa over (a, b) label pairs; None if undefined (no pairs, or chance = 1)."""
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return None if pe == 1 else (po - pe) / (1 - pe)


def fmt(pairs: Sequence[tuple[str, str]]) -> str:
    if not pairs:
        return "n = 0"
    agree = sum(a == b for a, b in pairs)
    k = kappa(pairs)
    ks = "n/a" if k is None else f"{k:.2f}"
    return f"{agree}/{len(pairs)} agree ({agree / len(pairs):.0%}), kappa {ks}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("answers")
    ap.add_argument("key")
    ap.add_argument("--final", action="store_true", help="Score the last answer, not the first.")
    a = ap.parse_args()
    with open(a.answers) as f:
        raw = json.load(f)
    answers = raw.get("answers", raw)
    with open(a.key) as f:
        cards = json.load(f)["cards"]

    field = "answer" if a.final else "first_answer"
    rows = []
    cant, missing = [], []
    for cid, c in sorted(cards.items()):
        ans = (answers.get(cid) or {}).get(field) or (answers.get(cid) or {}).get("answer")
        if not ans:
            missing.append(cid)
        elif ans not in LABELS:
            cant.append(cid)
        else:
            rows.append((cid, c, ans))
    which = "final" if a.final else "first"
    print(f"Scoring {which} answers: {len(rows)} cards scored, {len(cant)} can't tell {cant or ''}")
    print(f"Unanswered: {len(missing)} {missing or ''}")

    for who in ("judge", "agent"):
        name = "judge" if who == "judge" else "adjudicating agent"
        print(f"\nKian vs the {name}")
        print(f"  overall: {fmt([(ans, c[who]) for _, c, ans in rows])}")
        for label in LABELS:
            sub = [(ans, c[who]) for _, c, ans in rows if c[who] == label]
            if sub:
                print(f"  {name} said {label}: {fmt(sub)}")
        for p in sorted({c["product"] for _, c, _ in rows}):
            print(
                f"  product {p}: {fmt([(ans, c[who]) for _, c, ans in rows if c['product'] == p])}"
            )
        diff = [(cid, c, ans) for cid, c, ans in rows if ans != c[who]]
        for cid, c, ans in diff:
            print(f"  differs {cid}: {c['meeting_id']} {c['product']} {c['item_id']}: "
                  f"Kian {ans}, {who} {c[who]}")  # fmt: skip

    print(f"\nJudge vs agent on these cards: {fmt([(c['judge'], c['agent']) for _, c, _ in rows])}")
    by_type: dict[str, list[tuple[str, str, str]]] = {}
    for _, c, ans in rows:
        by_type.setdefault(c.get("disagreement_type") or "-", []).append(
            (ans, c["judge"], c["agent"])
        )
    print("By flag reason (Kian = judge / Kian = agent):")
    for t, xs in sorted(by_type.items()):
        j, g = sum(k == x for k, x, _ in xs), sum(k == y for k, _, y in xs)
        print(f"  {t}: n = {len(xs)}, judge {j}, agent {g}")

    gq = [(cid, c) for cid, c in sorted(cards.items()) if c.get("gold_question")]
    if gq:
        gfield = "gold_answer" if a.final else "first_gold_answer"
        print("\nIs the annotators' summary correct about who got the task?")
        for cid, c in gq:
            got = (answers.get(cid) or {}).get(gfield) or (answers.get(cid) or {}).get(
                "gold_answer"
            )
            print(f"  {cid} {c['meeting_id']} {c['product']} {c['item_id']}: {got or 'unanswered'}")
    notes = {cid: (answers.get(cid) or {}).get("note") for cid in cards}
    notes = {k: v for k, v in notes.items() if v}
    if notes:
        print("\nNotes:")
        for cid, v in sorted(notes.items()):
            print(f"  {cid}: {v}")


if __name__ == "__main__":
    main()
