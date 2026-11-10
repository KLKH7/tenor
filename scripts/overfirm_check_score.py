r"""Score the over-firm human check sheet: Kian vs the agent readers' consensus.

    python3 -I scripts/overfirm_check_score.py answers.json \
        data/private/OVERFIRM_CHECK_key.json [--final]

``answers.json`` is what the sheet's "Copy my answers" button gives. By default each card's
first answer (given before the readers' verdicts were revealed) is scored; ``--final`` scores the
last. "Can't tell" answers are counted and left out of agreement and kappa. Prints agreement and
Cohen's kappa on the four-way firmness label and on the strict over-firm verdict (Only discussed /
Not said vs the rest); agreement per card group (over-firm, leaning, control; no kappa there, as
each group's consensus is a single label); Kian vs each single reader; a confusion table; and the
cards where Kian and the consensus differ.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Sequence

LABELS = ("Decided", "Leaning", "Only discussed", "Not said")
GROUPS = ("overfirm", "leaning", "control")


def kappa(pairs: Sequence[tuple[str, str]]) -> float | None:
    """Cohen's kappa over (a, b) label pairs; None if undefined (no pairs, or chance = 1)."""
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return None if pe == 1 else (po - pe) / (1 - pe)


def fmt(pairs: Sequence[tuple[str, str]], with_kappa: bool = True) -> str:
    if not pairs:
        return "n = 0"
    agree = sum(a == b for a, b in pairs)
    if not with_kappa:
        return f"{agree}/{len(pairs)} agree ({agree / len(pairs):.0%})"
    k = kappa(pairs)
    ks = "n/a" if k is None else f"{k:.2f}"
    return f"{agree}/{len(pairs)} agree ({agree / len(pairs):.0%}), kappa {ks}"


def strict(label: str) -> str:
    """The strict over-firm verdict implied by a firmness label (every card claims decided)."""
    return "over-firm" if label in ("Only discussed", "Not said") else "not over-firm"


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
    rows, cant, missing = [], [], []
    for cid, c in sorted(cards.items()):
        got = answers.get(cid) or {}
        ans = got.get(field) or got.get("answer")
        if not ans:
            missing.append(cid)
        elif ans not in LABELS:
            cant.append(cid)
        else:
            rows.append((cid, c, ans))
    which = "final" if a.final else "first"
    print(f"Scoring {which} answers: {len(rows)} cards scored, {len(cant)} can't tell {cant or ''}")
    print(f"Unanswered: {len(missing)} {missing or ''}")

    pairs = [(ans, c["consensus"]) for _, c, ans in rows]
    print("\nKian vs the readers' consensus")
    print(f"  firmness (4 labels): {fmt(pairs)}")
    print(f"  strict over-firm (yes/no): {fmt([(strict(x), strict(y)) for x, y in pairs])}")
    for g in GROUPS:
        sub = [(ans, c["consensus"]) for _, c, ans in rows if c["group"] == g]
        sub_s = [(strict(x), strict(y)) for x, y in sub]
        print(f"  group {g}: firmness {fmt(sub, False)}; strict {fmt(sub_s, False)}")
        if sub:
            print(f"    Kian's answers: {dict(Counter(x for x, _ in sub))}")

    runs = sorted({r for _, c, _ in rows for r in c["readers"]})
    print("\nKian vs each reader (firmness)")
    for r in runs:
        sub = [(ans, c["readers"][r]) for _, c, ans in rows if r in c["readers"]]
        print(f"  reader {r.upper()}: {fmt(sub)}")

    print("\nConfusion (rows Kian, columns consensus)")
    cols = [lab for lab in LABELS if any(y == lab for _, y in pairs)]
    print("  " + " " * 16 + "".join(f"{lab[:14]:>16}" for lab in cols))
    for x in LABELS:
        line = "".join(f"{sum(p == (x, y) for p in pairs):>16}" for y in cols)
        print(f"  {x:<16}{line}")

    diff = [(cid, c, ans) for cid, c, ans in rows if ans != c["consensus"]]
    if diff:
        print("\nKian differs from the consensus")
        for cid, c, ans in diff:
            print(f"  {cid} [{c['group']}] {c['meeting_id']} {c['product']} {c['bullet_id']}: "
                  f"Kian {ans}, consensus {c['consensus']} {c['readers']}")  # fmt: skip
    notes = {cid: (answers.get(cid) or {}).get("note") for cid in cards}
    notes = {k: v for k, v in notes.items() if v}
    if notes:
        print("\nNotes:")
        for cid, v in sorted(notes.items()):
            print(f"  {cid}: {v}")


if __name__ == "__main__":
    main()
