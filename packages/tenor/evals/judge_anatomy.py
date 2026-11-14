"""Anatomy of judge errors: where a single-call LLM judge differs from a careful reference.

Pure functions used by ``scripts/judge_anatomy.py``. No LLM, no network, no file I/O.

* :func:`content_overlap` is the mechanical paraphrase measure: the share of an item's content
  words that its best-matching note bullet also has. Low overlap = the notes can only cover the
  item in other words.
* :func:`direction` says whether the judge was harsher or more lenient than the reference on an
  ordered verdict scale.
* :func:`cluster_rate` is a rate with a 95% interval from resampling whole clusters (meetings or
  questions), seeded.
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Sequence
from typing import Literal

COVERAGE: tuple[str, ...] = ("none", "partial", "full")
CORRECTNESS: tuple[str, ...] = ("incorrect", "partially_correct", "correct")

# Cut points set on the 14 DEV items (IS1008a, about a third in each bin) and frozen before TEST
# was run (see data/private/JUDGE_ANATOMY.md, "Paraphrase measure").
OVERLAP_BINS: tuple[tuple[str, float], ...] = (("low", 0.30), ("mid", 0.40), ("high", 1.01))

_STOP_WORDS = (
    "a an and are as at be been being but by can could did do does for from had has have he "
    "her his i if in into is it its may might more not of on or our she should so some than "
    "that the their them then there these they this those to was we were what when where "
    "whether which while who will with would you your about also any each how up out over all"
)
_STOP = frozenset(_STOP_WORDS.split())
_WORD = re.compile(r"[a-z0-9]+")

Direction = Literal["same", "harsh", "lenient"]
Third = Literal["first", "middle", "last"]


def stem(word: str) -> str:
    """A crude stem: drop a plural or verb ending ('buttons' and 'button' then match)."""
    for ending in ("ing", "ed", "es", "s"):
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[: -len(ending)]
    return word


def content_words(text: str) -> frozenset[str]:
    """Lower-cased, stemmed words of ``text`` without stop words."""
    return frozenset(stem(w) for w in _WORD.findall(text.lower()) if w not in _STOP)


def content_overlap(item: str, bullets: Sequence[str]) -> float:
    """Share of the item's content words found in its best-matching bullet (0 if none)."""
    words = content_words(item)
    if not words or not bullets:
        return 0.0
    return max(len(words & content_words(b)) for b in bullets) / len(words)


def overlap_bin(value: float) -> str:
    """'low' (< 0.30), 'mid' (< 0.40) or 'high': the frozen bins of :func:`content_overlap`."""
    for name, upper in OVERLAP_BINS:
        if value < upper:
            return name
    return OVERLAP_BINS[-1][0]


def third(position: int, n: int) -> Third:
    """Which third of a list of ``n`` the 0-based ``position`` falls in."""
    if not 0 <= position < n:
        raise ValueError("position must be within the list")
    k = position * 3 // n
    return "first" if k == 0 else "middle" if k == 1 else "last"


def direction(judge: str, reference: str, scale: Sequence[str]) -> Direction:
    """'harsh' when the judge's verdict is below the reference's on ``scale``, 'lenient' above."""
    j, r = scale.index(judge), scale.index(reference)
    return "same" if j == r else "harsh" if j < r else "lenient"


def median_verdict(passes: Sequence[str], scale: Sequence[str] = COVERAGE) -> str:
    """The middle verdict of an odd number of passes on an ordered scale."""
    if len(passes) % 2 == 0:
        raise ValueError("need an odd number of passes")
    return scale[sorted(scale.index(p) for p in passes)[len(passes) // 2]]


def cluster_rate(
    rows: Sequence[tuple[str, bool]], seed: int, n_boot: int = 2000
) -> tuple[int, int, float | None, float | None]:
    """(hits, n, low, high): a rate over ``(cluster, hit)`` rows with a 95% cluster bootstrap.

    Whole clusters are resampled with replacement and the pooled rate recomputed, so rows from
    one meeting or question stay together. The interval is None with fewer than two clusters.
    """
    by: dict[str, list[bool]] = {}
    for cluster, hit in rows:
        by.setdefault(cluster, []).append(hit)
    hits, n = sum(h for _, h in rows), len(rows)
    if len(by) < 2 or n == 0:
        return hits, n, None, None
    groups = [by[k] for k in sorted(by)]
    rng = random.Random(seed)
    rates = []
    for _ in range(n_boot):
        pick = rng.choices(groups, k=len(groups))
        total = sum(len(g) for g in pick)
        rates.append(sum(sum(g) for g in pick) / total)
    rates.sort()
    return hits, n, rates[int(0.025 * n_boot)], rates[int(0.975 * n_boot) - 1]


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Spearman rank correlation with average ranks for ties; None when either side is constant."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None

    def ranks(values: Sequence[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx == 0 or syy == 0:
        return None
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    return float(cov / math.sqrt(sxx * syy))
