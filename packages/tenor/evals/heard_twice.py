"""Heard twice: do two independent hearings of the same audio agree on the hard values? (no LLM)

The idea under test (``data/private/IDEAS_P4_P7.md``, 4.1): a notes app keeps no audio, so a user
cannot check whether a number was heard right. If the audio were transcribed twice, values on
which the two hearings agree could be trusted and values on which they differ could carry a mark
("heard two ways: 15 / 50"). For the AMI meetings we hold three texts of the same audio: AMI's
human transcript (truth), Granola's transcript and Wispr Flow's transcript (two hearings).

Pipeline, all deterministic:

* :func:`extract` finds the *hard values* in a word stream: cardinal and ordinal numbers (digits
  or words, multi-word numbers joined: "twenty five", "a hundred and fifty", "twelve fifty"),
  money, percentages, clock times, durations ("thirty minutes"), dates (weekdays, months,
  today / tomorrow / yesterday / tonight) and negations (not, n't, no, never, without). A value
  has a ``unit`` and a set of numeric readings (``vals``); two values are the same when the unit
  is the same and a reading is shared (:func:`same`), so "twenty five Euros" = "€25" and
  "twelve fifty" = "12.50" = "1250". Uses that are not values are flagged, not dropped
  (``excluded``): "one" as a pronoun ("this one", "one of them"), "second" as a time unit,
  "first of all", a number said twice in a restart ("twelve twelve and a half"), "Speaker 2".
* :func:`anchors` aligns a product's words to the human words (longest matching blocks, then a
  time check against the running replay offset, as ``speaker_naming.align`` does).
* :func:`classify` gives, for every human value, what the product heard at that position:
  ``correct`` (the same value inside the aligned window), ``different`` (another value of the
  same family in the tight window) or ``missing`` (no value there).
* :func:`two_hearings` puts the two products side by side per human value; :func:`summarise`
  pools the classes with meeting-cluster bootstrap CIs and checks the pre-stated stop rules.
* :func:`note_values` does the note-level check: hard values in a product's note bullets,
  matched (number only) to the human transcript at the bullet's aligned moment.

Known limits (stated in the write-up): AMI alignment errors count as product errors; values a
product invents where the human transcript has none are not scored (the unit is the human
value); negation matching is by word class only.
"""

from __future__ import annotations

import bisect
import difflib
import random
import re
import statistics
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from tenor.evals import speaker_naming as S
from tenor.evals.coverage_e4 import ratio_ci

VERSION = "heard_twice.v1"
PRODUCTS = ("granola", "wispr")
KINDS = ("number", "ordinal", "money", "percent", "time", "duration", "date", "negation")
NUMERIC = ("number", "ordinal", "money", "percent", "time", "duration")
TIGHT = 2  # words either side of the expected position in which another value is "different"
WIDE = 10  # words either side in which the same value still counts as heard (crosstalk order)
SMOOTH = 51  # anchors in the running median of the replay offset
TOLERANCE_S = 6.0  # an anchor further than this from the running offset is dropped ...
LONG_BLOCK = 4  # ... unless it sits in a run of this many matching tokens
NOTE_PAD = 40  # human words either side of a bullet's aligned passage
STOP_AGREED_WRONG = 0.03  # stop if agreed values are wrong more often than this
STOP_CATCH = 0.5  # stop if a disagreement flag catches less than this share of wrong values
STOP_PER_MEETING = 1.0  # stop if there are fewer disagreed values per meeting than this
NOISE_SHARE = 0.10  # the ideas file's extra rule: more than this share marked is too noisy


def _words(text: str) -> list[str]:
    return text.split()


_FILLERS = frozenset(_words("uh um uhm erm er ah eh hmm hm mm mmm huh mhm mhmm mmhmm mmhm uhhuh"))
_ONES = _words(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen"
)
_ORD_ONES = _words(
    "zeroth first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth "
    "thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth"
)
_TENS_W = _words("twenty thirty forty fifty sixty seventy eighty ninety")
_ORD_TENS_W = _words(
    "twentieth thirtieth fortieth fiftieth sixtieth seventieth eightieth ninetieth"
)
UNITS = {w: i for i, w in enumerate(_ONES)}
ORD_UNITS = {w: i for i, w in enumerate(_ORD_ONES) if i}
TENS = {w: 10 * (i + 2) for i, w in enumerate(_TENS_W)}
ORD_TENS = {w: 10 * (i + 2) for i, w in enumerate(_ORD_TENS_W)}
BIG = {"thousand": 1000, "million": 1_000_000, "billion": 1_000_000_000}
CURRENCY = {
    **dict.fromkeys(("euro", "euros"), "euro"),
    **dict.fromkeys(("dollar", "dollars", "buck", "bucks"), "dollar"),
    **dict.fromkeys(("pound", "pounds", "quid"), "pound"),
    **dict.fromkeys(("cent", "cents"), "cent"),
    **dict.fromkeys(("franc", "francs"), "franc"),
    "yen": "yen",
}
SYMBOL = {"€": "euro", "$": "dollar", "£": "pound"}
DURATION = {
    f"{u}{s}": u
    for u in ("second", "minute", "hour", "day", "week", "month", "year")
    for s in ("", "s")
}
DURATION |= {"min": "minute", "mins": "minute", "hr": "hour", "hrs": "hour"}
DURATION |= {"sec": "second", "secs": "second"}
PREFIX = {"eur": "euro", "usd": "dollar", "gbp": "pound"}  # "EUR 50,000,000"
CLOCK = frozenset(("o'clock", "oclock", "am", "pm", "a.m", "p.m"))
DATES = frozenset(
    _words(
        "monday tuesday wednesday thursday friday saturday sunday january february march april "
        "june july august september october november december today tomorrow yesterday "
        "tonight"
    )
)  # "may" is left out: it is nearly always the modal verb
NEGATIONS = {
    "not": "not",
    "cannot": "not",
    "no": "no",
    "nope": "no",
    "nah": "no",
    "never": "never",
    "without": "without",
}
# "one" after these is a pronoun ("this one", "the other one", "a new one"), not a count
ONE_PREV = frozenset(
    _words(
        "this that the which each another any every no some other same only right wrong new old "
        "big small little good bad best better main first second third last next single different "
        "particular specific whole simple nice normal basic original real really one's"
    )
)
ONE_NEXT = frozenset(_words("of another can could should would might must"))
SECOND_PREV = frozenset(_words("a one per each half split every just"))

_DIGITS = re.compile(r"^([€£$])?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(%|st|nd|rd|th|s)?$")
_HHMM = re.compile(r"^(\d{1,2}):(\d{2})$")
_KEY = re.compile(r"^(?:([a-z]+)(\d+)|(\d+)([a-rt-z]))$")  # "f5", "1p": a letter and a number
_EDGE = ".,!?;:\"()[]{}…'`*-"
_ENDS = re.compile(r"[.,!?;:…][\"')\]]*$")


# -- words ------------------------------------------------------------------------------------


def clean_words(text: str) -> list[tuple[str, bool]]:
    """Lower-cased words of ``text`` for value extraction, each with ``brk``: punctuation follows
    it, so a number cannot run past it. Hyphens and slashes split ("twenty-five", "5-10");
    fillers (uh, um, mm) are dropped; "€12.50", "25%", "3:30", "1st" and "don't" stay whole; a
    letter stuck to a number is split off ("f5", "1p")."""
    out: list[tuple[str, bool]] = []
    text = re.sub("[\u2018\u2019]", "'", text)
    text = re.sub("[\u201c\u201d]", '"', text)
    for raw in re.sub("[\u2013\u2014]", " \u2014 ", text).split():
        brk = bool(_ENDS.search(raw)) or raw == "\u2014"
        parts = [p.strip(_EDGE) for p in re.split(r"[-/]", raw.lower())]
        parts = [p for p in parts if p and p not in _FILLERS]
        parts = [x for p in parts for x in (_split_key(p))]
        if not parts:
            if brk and out:
                out[-1] = (out[-1][0], True)
            continue
        out += [(p, False) for p in parts[:-1]]
        out.append((parts[-1], brk))
    return out


def _split_key(word: str) -> list[str]:
    m = _KEY.match(word)
    return [g for g in m.groups() if g] if m else [word]


@dataclass(frozen=True)
class Stream:
    """One transcript as cleaned words. ``line`` is the product's ``T`` line (-1: human);
    ``agent`` the AMI speaker ("" for a product)."""

    words: tuple[str, ...]
    brk: tuple[bool, ...]
    time: tuple[float, ...]  # seconds on the source's own clock
    line: tuple[int, ...]
    agent: tuple[str, ...]


def human_stream(words: Sequence[Any]) -> Stream:
    """AMI's words (``corpus.Word``, all speakers by start time, punctuation tokens included) as
    a stream. A punctuation token or an interrupted fragment sets ``brk`` on that speaker's
    previous word."""
    w: list[str] = []
    brk: list[bool] = []
    time: list[float] = []
    agent: list[str] = []
    last: dict[str, int] = {}
    for x in words:
        if x.punc or x.trunc:
            if x.agent in last:
                brk[last[x.agent]] = True
            continue
        for tok, b in clean_words(x.text):
            w.append(tok)
            brk.append(b)
            time.append(float(x.start))
            agent.append(str(x.agent))
            last[x.agent] = len(w) - 1
    return Stream(tuple(w), tuple(brk), tuple(time), (-1,) * len(w), tuple(agent))


def product_stream(lines: Sequence[S.ProdLine]) -> Stream:
    """A product transcript as a stream: word times spread evenly over the line's span; the last
    word of every line breaks a number."""
    w: list[str] = []
    brk: list[bool] = []
    time: list[float] = []
    line: list[int] = []
    for ln in lines:
        toks = clean_words(ln.text)
        span = max(0.0, ln.end - ln.start)
        for k, (tok, b) in enumerate(toks):
            w.append(tok)
            brk.append(b or k == len(toks) - 1)
            time.append(ln.start + span * (k + 0.5) / len(toks))
            line.append(ln.index)
    return Stream(tuple(w), tuple(brk), tuple(time), tuple(line), ("",) * len(w))


# -- values -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Value:
    """One hard value in a word stream (``start``/``end``: word span, end exclusive)."""

    kind: str  # one of KINDS
    unit: str  # "" number, "ord", "%", "clock", a currency, a duration unit, "date", "neg"
    vals: frozenset[str]  # numeric readings (as fractions) or the date / negation word
    start: int
    end: int
    text: str
    parts: tuple[str, ...] = ()  # "twelve fifty": ("12", "50"), also accepted as two numbers
    excluded: str = ""  # why this use is not counted as a value ("" = counted)

    @property
    def family(self) -> str:
        return "numeric" if self.kind in NUMERIC else self.kind

    @property
    def shown(self) -> str:
        """The value as a short label: "25 euro", "12.5|1250", "not"."""
        if self.family != "numeric":
            return "|".join(sorted(self.vals))
        nums = "|".join(_fmt(Fraction(v)) for v in sorted(self.vals, key=Fraction))
        return f"{nums} {self.unit}".strip()


def _fmt(f: Fraction) -> str:
    return str(f.numerator) if f.denominator == 1 else f"{float(f):g}"


def same(a: Value, b: Value) -> bool:
    """The same value: same unit and a shared reading. A clock time and a bare number are one
    unit ("at five" = "5:00", "two o'clock" = "2")."""
    ua, ub = ("" if u == "clock" else u for u in (a.unit, b.unit))
    return ua == ub and bool(a.vals & b.vals)


def same_number(a: Value, b: Value) -> bool:
    """The same number whatever the unit (the note-level check: "€25" for "twenty five")."""
    return a.family == b.family == "numeric" and bool(a.vals & b.vals)


class _Parser:
    """Number grammar over one stream (:func:`extract`)."""

    def __init__(self, words: Sequence[str], brk: Sequence[bool]) -> None:
        self.w = words
        self.brk = brk
        self.n = len(words)

    def joins(self, j: int) -> bool:
        """Word ``j`` may continue a number that ends at ``j - 1``."""
        return 0 < j < self.n and not self.brk[j - 1]

    def group(self, i: int) -> tuple[int, int, bool] | None:
        """A two-digit group at ``i``: (value, next index, is ordinal)."""
        if i >= self.n:
            return None
        w = self.w[i]
        if w in UNITS:
            return UNITS[w], i + 1, False
        if w in ORD_UNITS:
            return ORD_UNITS[w], i + 1, True
        if w in ORD_TENS:
            return ORD_TENS[w], i + 1, True
        if w in TENS:
            if self.joins(i + 1):
                nxt = self.w[i + 1]
                if 0 < UNITS.get(nxt, 0) < 10:
                    return TENS[w] + UNITS[nxt], i + 2, False
                if 0 < ORD_UNITS.get(nxt, 0) < 10:
                    return TENS[w] + ORD_UNITS[nxt], i + 2, True
            return TENS[w], i + 1, False
        return None

    def tail(self, j: int) -> tuple[int, int, bool] | None:
        """An optional "and" then a two-digit group continuing a number at ``j``."""
        if not self.joins(j):
            return None
        if self.w[j] == "and" and self.joins(j + 1):
            return self.group(j + 1)
        return self.group(j)

    def below_thousand(self, i: int) -> tuple[int, int, bool, bool] | None:
        """``[group | a] [hundred [and] [group]]`` at ``i``: (value, next, ordinal, plain);
        ``plain`` = a bare two-digit group."""
        if i >= self.n:
            return None
        g = self.group(i)
        if g is None:
            lead = self.w[i] in ("a", "an") and self.joins(i + 1)
            at = i + 1 if lead else i
            if at < self.n and self.w[at] == "hundred":
                cur, j, plain = 1, at, False
            elif lead and self.w[at] in BIG:
                return 1, at, False, False
            else:
                return None
        else:
            cur, j, ordinal = g
            if ordinal:
                return cur, j, True, True
            plain = True
        if self.joins(j) and self.w[j] in ("hundred", "hundredth"):
            if self.w[j] == "hundredth":
                return cur * 100, j + 1, True, False
            cur, j, plain = cur * 100, j + 1, False
            t = self.tail(j)
            if t is not None:
                return cur + t[0], t[1], t[2], False
        elif g is None:
            return None
        return cur, j, False, plain

    def word_number(self, i: int) -> tuple[frozenset[Fraction], int, bool, tuple[str, ...]] | None:
        """A number in words at ``i``: (readings, next index, is ordinal, pair parts)."""
        first = self.below_thousand(i)
        if first is None:
            if self.w[i] in BIG:  # bare "thousand"
                return frozenset({Fraction(BIG[self.w[i]])}), i + 1, False, ()
            return None
        cur, j, ordinal, plain = first
        total = 0
        while not ordinal and self.joins(j) and self.w[j] in BIG:
            total, cur, plain = total + cur * BIG[self.w[j]], 0, False
            j += 1
            k = j + 1 if self.joins(j) and self.w[j] == "and" else j
            nxt = self.below_thousand(k) if self.joins(k) and self.w[k] not in ("a", "an") else None
            if nxt is not None:
                cur, j, ordinal = nxt[0], nxt[1], nxt[2]
        value = Fraction(total + cur)
        if ordinal:
            return frozenset({value}), j, True, ()
        if plain and 1 <= value <= 99 and self.joins(j):
            g2 = self.group(j)
            more = g2 is not None and g2[1] < self.n and not self.brk[g2[1] - 1]
            if (
                g2 is not None
                and not g2[2]
                and g2[0] >= 20
                and g2[0] != value
                and not (more and self.w[g2[1]] in ("hundred", *BIG))
            ):  # "twelve fifty", "nineteen ninety nine": 1250 or 12.50
                pair = frozenset({value * 100 + g2[0], value + Fraction(g2[0], 100)})
                return pair, g2[1], False, (str(value), str(Fraction(g2[0])))
        if self.joins(j) and self.w[j] == "point":
            k = j + 1
            digits = ""
            while k < self.n and 0 <= UNITS.get(self.w[k], 99) < 10 and not self.brk[k - 1]:
                digits += str(UNITS[self.w[k]])
                k += 1
            if digits:
                return frozenset({value + Fraction(int(digits), 10 ** len(digits))}), k, False, ()
        return frozenset({value}), j, False, ()

    def digit_number(self, i: int) -> tuple[frozenset[Fraction], int, str] | None:
        """A number in digits at ``i``: (readings, next index, unit from the token itself)."""
        if m := _HHMM.match(self.w[i]):  # "11:00"; other clock times are split in ``extract``
            return frozenset({Fraction(int(m.group(1)))}), i + 1, "clock"
        m = _DIGITS.match(self.w[i])
        if m is None:
            return None
        symbol, whole, frac, suffix = m.groups()
        value = Fraction(int(whole.replace(",", "")))
        if frac:
            value += Fraction(int(frac), 10 ** len(frac))
        j = i + 1
        if not suffix and self.joins(j) and self.w[j] in ("hundred", *BIG):
            value *= 100 if self.w[j] == "hundred" else BIG[self.w[j]]
            j += 1
        unit = SYMBOL.get(symbol or "", "")
        if suffix == "%":
            unit = "%"
        elif suffix in ("st", "nd", "rd", "th"):
            unit = "ord"
        return frozenset({value}), j, unit

    def unit_after(self, j: int, vals: frozenset[Fraction]) -> tuple[str, int, frozenset[Fraction]]:
        """A unit word after a number that ends at ``j``: (unit, next index, readings)."""
        if not self.joins(j):
            return "", j, vals
        w = self.w[j]
        if w in ("percent", "%"):
            return "%", j + 1, vals
        if w == "per" and self.joins(j + 1) and self.w[j + 1] == "cent":
            return "%", j + 2, vals
        if w in CURRENCY:
            unit, j = CURRENCY[w], j + 1
            k = j + 1 if self.joins(j + 1) and self.w[j] == "and" else j
            g = self.group(k) if self.joins(k) and unit != "cent" else None
            if g is not None and not g[2] and len(vals) == 1:
                after = self.w[g[1]] if g[1] < self.n and not self.brk[g[1] - 1] else ""
                if after in ("cent", "cents") or (k == j and g[0] >= 10 and not _is_unit(after)):
                    # "twelve Euros fifty [cents]"
                    (whole,) = vals
                    j = g[1] + (after in ("cent", "cents"))
                    return unit, j, frozenset({whole + Fraction(g[0], 100)})
            return unit, j, vals
        if w in DURATION:
            return DURATION[w], j + 1, vals
        if w in CLOCK:
            return "clock", j + 1, vals
        return "", j, vals


def _is_unit(word: str) -> bool:
    return word in CURRENCY or word in DURATION or word in CLOCK or word in ("percent", "%", "per")


def _kind(unit: str) -> str:
    if unit in ("", "ord", "%", "clock"):
        return {"": "number", "ord": "ordinal", "%": "percent", "clock": "time"}[unit]
    return "money" if unit in CURRENCY.values() else "duration"


def _excluded(v: Value, words: Sequence[str]) -> str:
    """Why a value is a figure of speech and not a hard value ("" = it counts)."""
    prev = words[v.start - 1] if v.start else ""
    nxt = words[v.end] if v.end < len(words) else ""
    if v.text == "one" and v.kind == "number" and (prev in ONE_PREV or nxt in ONE_NEXT):
        return "pronoun_one"
    if v.kind == "number" and prev in ("speaker", "participant"):
        return "speaker_label"  # "Speaker 2" in a product's notes or transcript
    if v.text == "second" and prev in SECOND_PREV:
        return "second_time_unit"
    if v.text == "first" and (
        prev == "at"
        or nxt == "off"
        or (nxt == "of" and v.end + 1 < len(words) and words[v.end + 1] == "all")
    ):
        return "first_discourse"
    return ""


def extract(words: Sequence[str], brk: Sequence[bool]) -> list[Value]:
    """Every hard value of a word stream, in order (module docstring). Figures of speech are
    returned with ``excluded`` set, so both sides of a comparison can still be matched."""
    p = _Parser(words, brk)
    out: list[Value] = []
    i = 0
    while i < len(words):
        w = words[i]
        found: Value | None = None
        if w in NEGATIONS or (w.endswith("n't") and len(w) > 3) or w == "ain't":
            found = Value("negation", "neg", frozenset({NEGATIONS.get(w, "not")}), i, i + 1, w)
        elif w in DATES:
            found = Value("date", "date", frozenset({w}), i, i + 1, w)
        elif (m := _HHMM.match(w)) and int(m.group(2)):
            # "12:13" is said "twelve thirteen": two numbers, so both ways of writing match
            out.append(Value("number", "", frozenset({str(int(m.group(1)))}), i, i + 1, w))
            found = Value("number", "", frozenset({str(int(m.group(2)))}), i, i + 1, w)
        else:
            unit = ""
            parts: tuple[str, ...] = ()
            digit = p.digit_number(i)
            if digit is not None:
                vals, j, unit = digit
            else:
                word = p.word_number(i)
                if word is None:
                    i += 1
                    continue
                vals, j, ordinal, parts = word
                unit = "ord" if ordinal else ""
            if not unit and i and words[i - 1] in PREFIX and not brk[i - 1]:
                unit = PREFIX[words[i - 1]]
            if not unit:
                if (
                    len(vals) == 1
                    and j + 2 < len(words)
                    and tuple(words[j : j + 3]) == ("and", "a", "half")
                    and not any(brk[j - 1 : j + 2])
                ):  # "twelve and a half"
                    vals, j = frozenset(v + Fraction(1, 2) for v in vals), j + 3
                    parts = ()
                unit, j, vals = p.unit_after(j, vals)
            elif unit != "ord" and p.joins(j) and words[j] in CURRENCY:
                j += 1  # "€12 euros"
            kind = _kind(unit)
            if kind != "number":
                parts = ()
            found = Value(
                kind,
                unit,
                frozenset(str(v) for v in vals),
                i,
                j,
                " ".join(words[i:j]),
                parts,
            )
        reason = _excluded(found, words)
        if reason:
            found = Value(
                found.kind, found.unit, found.vals, found.start, found.end, found.text,
                found.parts, reason,
            )  # fmt: skip
        out.append(found)
        i = found.end
    for k in range(len(out) - 1):
        a, b = out[k], out[k + 1]
        if (
            a.end == b.start
            and a.kind == "number"
            and not a.excluded
            and b.family == "numeric"
            and b.text.split()[0] == a.text
            and not brk[a.end - 1]
        ):  # "twelve twelve and a half", "eight eight million": a restart, said once
            out[k] = Value(a.kind, a.unit, a.vals, a.start, a.end, a.text, a.parts, "restart")
    return out


def human_values(stream: Stream) -> list[Value]:
    """Hard values of the human transcript, in time order. Numbers are read per speaker, so
    another speaker's word in between ("twenty" / "yeah" / "five") does not split one; spans are
    in the merged stream's word positions."""
    by_agent: dict[str, list[int]] = {}
    for i, a in enumerate(stream.agent):
        by_agent.setdefault(a, []).append(i)
    out: list[Value] = []
    for idx in by_agent.values():
        words = [stream.words[i] for i in idx]
        for v in extract(words, [stream.brk[i] for i in idx]):
            out.append(
                Value(v.kind, v.unit, v.vals, idx[v.start], idx[v.end - 1] + 1, v.text,
                      v.parts, v.excluded)
            )  # fmt: skip
    return sorted(out, key=lambda v: (v.start, v.end))


def product_values(stream: Stream) -> list[Value]:
    return extract(stream.words, stream.brk)


# -- alignment --------------------------------------------------------------------------------


def _running_median(values: Sequence[float], width: int) -> list[float]:
    half = width // 2
    return [statistics.median(values[max(0, i - half) : i + half + 1]) for i in range(len(values))]


def anchors(ref: Stream, hyp: Stream) -> list[tuple[int, int]]:
    """Word pairs (human position, product position) the two transcripts share, increasing in
    both. Words are compared as ``speaker_naming.tokens`` (numbers spelled out); a match must
    agree with the running replay offset within :data:`TOLERANCE_S` or sit in a run of
    :data:`LONG_BLOCK` matching tokens."""

    def spell(stream: Stream) -> tuple[list[str], list[int]]:
        toks: list[str] = []
        owner: list[int] = []
        for i, w in enumerate(stream.words):
            for t in S.tokens(w):
                toks.append(t)
                owner.append(i)
        return toks, owner

    rt, ro = spell(ref)
    ht, ho = spell(hyp)
    sm = difflib.SequenceMatcher(None, rt, ht, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    pairs = [(b.a + k, b.b + k) for b in blocks for k in range(b.size)]
    if not pairs:
        return []
    long = [b.size >= LONG_BLOCK for b in blocks for _ in range(b.size)]
    diffs = [hyp.time[ho[h]] - ref.time[ro[r]] for r, h in pairs]
    smooth = _running_median(diffs, SMOOTH)
    out: list[tuple[int, int]] = []
    for (r, h), d, s, run in zip(pairs, diffs, smooth, long, strict=True):
        pair = (ro[r], ho[h])
        if (abs(d - s) <= TOLERANCE_S or run) and (
            not out or (pair[0] > out[-1][0] and pair[1] > out[-1][1])
        ):
            out.append(pair)
    return out


def expected_span(
    start: int, end: int, pairs: Sequence[tuple[int, int]], n_hyp: int
) -> tuple[int, int, int, int]:
    """Where human words ``[start, end)`` should sit in the product stream: (lo, hi, floor,
    ceiling). ``[lo, hi)`` is the span of the anchors inside the value, or the point between the
    neighbouring anchors in proportion; (floor, ceiling) are those neighbours' positions."""
    refs = [r for r, _ in pairs]
    a, b = bisect.bisect_left(refs, start), bisect.bisect_left(refs, end)
    floor = pairs[a - 1][1] if a else -1
    ceiling = pairs[b][1] if b < len(pairs) else n_hyp
    if a < b:
        return pairs[a][1], pairs[b - 1][1] + 1, floor, ceiling
    r0 = pairs[a - 1][0] if a else -1
    r1 = pairs[b][0] if b < len(pairs) else r0 + (end - start) + 1
    gap = ceiling - floor - 1
    lo = floor + 1 + round(gap * (start - r0 - 1) / max(1, r1 - r0 - 1))
    return lo, min(ceiling, lo + (end - start)), floor, ceiling


@dataclass(frozen=True)
class Hearing:
    """What one product heard where the human transcript has a value."""

    outcome: str  # correct | different | missing
    heard: str  # the product's value as a label ("" when missing)
    text: str  # the product's words for it
    at: int  # product word position (of the value, or of the expected spot)
    number_same: bool = False  # different, but only the unit differs


def _distance(m: Value, lo: int, hi: int) -> int:
    return max(0, lo - m.end + 1, m.start - hi + 1)


def classify(
    human: Sequence[Value], product: Sequence[Value], pairs: Sequence[tuple[int, int]], n_hyp: int
) -> list[Hearing]:
    """One :class:`Hearing` per human value (figures of speech included, so they claim their
    own product word). Three passes, each product value used once: the same value in the tight
    window; the same value in the wide window; then another counted value of the same family in
    the tight window (``different``). Whatever is left is ``missing``."""
    return [h for h, _ in _match(human, product, pairs, n_hyp)]


def _match(
    human: Sequence[Value],
    product: Sequence[Value],
    pairs: Sequence[tuple[int, int]],
    n_hyp: int,
    eq: Callable[[Value, Value], bool] | None = None,
) -> list[tuple[Hearing, tuple[int, ...]]]:
    """:func:`classify`, with the positions in ``product`` of the value(s) each hearing used.
    ``eq`` replaces :func:`same` as the test that two values are one."""
    eq = eq or same
    spans = [expected_span(h.start, h.end, pairs, n_hyp) for h in human]
    used: set[int] = set()
    result: dict[int, Hearing] = {}
    took: dict[int, tuple[int, ...]] = {}

    def near(i: int, pad: int, clip: bool) -> list[int]:
        lo, hi, floor, ceiling = spans[i]
        a, b = lo - pad, hi + pad
        if clip:
            a, b = max(a, floor + 1), min(b, ceiling)
        cands = [k for k, m in enumerate(product) if k not in used and m.start < b and m.end > a]
        return sorted(cands, key=lambda k: (_distance(product[k], lo, hi), k))

    def take_same(pad: int, clip: bool) -> None:
        for i, h in enumerate(human):
            if i in result:
                continue
            cands = near(i, pad, clip)
            hit = next((k for k in cands if eq(h, product[k]) or _part_of(h, product[k])), None)
            if hit is not None:
                m = product[hit]
                used.add(hit)
                took[i] = (hit,)
                result[i] = Hearing("correct", m.shown, m.text, m.start)
            elif h.parts:  # "twelve fifty" written as two numbers, in order
                two = _two_parts(h, [k for k in sorted(cands)], product)
                if two is not None:
                    used.update(two)
                    took[i] = two
                    text = " ".join(product[k].text for k in two)
                    result[i] = Hearing("correct", h.shown, text, product[two[0]].start)

    take_same(TIGHT, True)
    take_same(WIDE, False)
    for i, h in enumerate(human):
        if i in result:
            continue
        cands = [
            k
            for k in near(i, TIGHT, True)
            if product[k].family == h.family and not product[k].excluded
        ]
        if cands:
            m = product[cands[0]]
            used.add(cands[0])
            took[i] = (cands[0],)
            result[i] = Hearing(
                "different", m.shown, m.text, m.start, number_same=bool(h.vals & m.vals)
            )
        else:
            result[i] = Hearing("missing", "", "", spans[i][0])
    return [(result[i], took.get(i, ())) for i in range(len(human))]


def _part_of(h: Value, m: Value) -> bool:
    """``h`` is one half of a product's two-number run ("or ninety" heard as "four ninety")."""
    return h.unit == "" and not h.parts and bool(h.vals & set(m.parts))


def _two_parts(h: Value, cands: Sequence[int], product: Sequence[Value]) -> tuple[int, int] | None:
    for x in cands:
        for y in cands:
            if (
                y > x
                and product[x].unit == product[y].unit == ""
                and h.parts[0] in product[x].vals
                and h.parts[1] in product[y].vals
            ):
                return x, y
    return None


# -- two hearings -----------------------------------------------------------------------------

CLASSES = ("agree_right", "agree_wrong", "disagree_one_right", "disagree_both_wrong", "missing")


def two_hearings(a: Hearing, b: Hearing) -> str:
    """The class of one human value given both products' hearings (one of :data:`CLASSES`).
    Two correct hearings agree by definition, even if written differently ("1250" / "12.50")."""
    if "missing" in (a.outcome, b.outcome):
        return "missing"
    right = (a.outcome == "correct") + (b.outcome == "correct")
    if right == 2:
        return "agree_right"
    if right == 1:
        return "disagree_one_right"
    return "agree_wrong" if a.heard == b.heard else "disagree_both_wrong"


def flagged(cls: str, a: Hearing, b: Hearing, broad: bool) -> bool:
    """Would a product that compares its two hearings mark this value? Strict: both hearings
    have a value here and the values differ. Broad: also when exactly one hearing has a value."""
    if cls.startswith("disagree"):
        return True
    return broad and cls == "missing" and (a.outcome == "missing") != (b.outcome == "missing")


def value_rows(
    meeting_id: str,
    human: Stream,
    values: Sequence[Value],
    hearings: Mapping[str, Sequence[Hearing]],
    streams: Mapping[str, Stream],
    context: int = 6,
) -> list[dict[str, Any]]:
    """One JSON row per human value: the value, its time and speaker, the human words around it
    (that speaker's own), each product's hearing with its words around the spot, and the
    two-hearings class."""
    by_agent: dict[str, list[int]] = {}
    for i, ag in enumerate(human.agent):
        by_agent.setdefault(ag, []).append(i)
    rows = []
    for n, v in enumerate(values):
        agent = human.agent[v.start]
        own = by_agent[agent]
        k = bisect.bisect_left(own, v.start)
        k2 = bisect.bisect_left(own, v.end - 1)
        said = " ".join(human.words[i] for i in own[max(0, k - context) : k2 + 1 + context])
        row: dict[str, Any] = {
            "id": f"{meeting_id}:{n:04d}",
            "meeting": meeting_id,
            "kind": v.kind,
            "value": v.shown,
            "text": v.text,
            "time_s": round(human.time[v.start], 1),
            "agent": agent,
            "human_words": said,
            "excluded": v.excluded,
        }
        for p in PRODUCTS:
            h = hearings[p][n]
            st = streams[p]
            width = max(1, len(h.text.split()))
            lo, hi = max(0, h.at - context), min(len(st.words), h.at + width + context)
            row[p] = {
                "outcome": h.outcome,
                "heard": h.heard,
                "text": h.text,
                "number_same": h.number_same,
                "words": " ".join(st.words[lo:hi]),
                "line": st.line[min(h.at, len(st.line) - 1)] if st.line else -1,
            }
        g, w = hearings[PRODUCTS[0]][n], hearings[PRODUCTS[1]][n]
        row["class"] = two_hearings(g, w)
        row["flag_strict"] = flagged(row["class"], g, w, broad=False)
        row["flag_broad"] = flagged(row["class"], g, w, broad=True)
        rows.append(row)
    return rows


# -- summary ----------------------------------------------------------------------------------


def _rate(
    rows: Sequence[Mapping[str, Any]],
    meetings: Sequence[str],
    num: Callable[[Mapping[str, Any]], float],
    den: Callable[[Mapping[str, Any]], float],
    seed: int,
) -> dict[str, Any]:
    """sum(num) / sum(den) over rows, with a percentile bootstrap CI that resamples meetings."""
    per: dict[str, list[float]] = {m: [0.0, 0.0] for m in meetings}
    for r in rows:
        per[r["meeting"]][0] += num(r)
        per[r["meeting"]][1] += den(r)
    pairs = [(a, b) for a, b in per.values()]
    n, d = sum(a for a, _ in pairs), sum(b for _, b in pairs)
    ci = ratio_ci(pairs, seed)
    return {
        "num": round(n, 4),
        "den": round(d, 4),
        "rate": round(n / d, 4) if d else None,
        "ci": list(ci) if ci else None,
    }


def _wrong(r: Mapping[str, Any], broad: bool) -> int:
    bad = ("different", "missing") if broad else ("different",)
    return sum(r[p]["outcome"] in bad for p in PRODUCTS)


def _block(rows: Sequence[Mapping[str, Any]], meetings: Sequence[str], seed: int) -> dict[str, Any]:
    """All measures for one set of counted value rows (one kind, or pooled)."""
    one = 1.0
    cls = Counter(r["class"] for r in rows)
    agree = [r for r in rows if r["class"].startswith("agree")]
    dis = [r for r in rows if r["class"].startswith("disagree")]
    out: dict[str, Any] = {"n": len(rows), "classes": {c: cls.get(c, 0) for c in CLASSES}}
    for p in PRODUCTS:
        oc = Counter(r[p]["outcome"] for r in rows)
        out[p] = {
            "correct": oc.get("correct", 0),
            "different": oc.get("different", 0),
            "missing": oc.get("missing", 0),
            "unit_only": sum(r[p]["number_same"] for r in rows),
            "accuracy": _rate(rows, meetings, lambda r, p=p: r[p]["outcome"] == "correct",  # type: ignore[misc]
                              lambda r: one, seed),
        }  # fmt: skip
    out["agreed_right"] = _rate(
        agree, meetings, lambda r: r["class"] == "agree_right", lambda r: one, seed
    )
    out["agreed_wrong"] = _rate(
        agree, meetings, lambda r: r["class"] == "agree_wrong", lambda r: one, seed
    )
    out["disagreed_hearing_right"] = _rate(
        dis, meetings, lambda r: r["class"] == "disagree_one_right", lambda r: 2.0, seed
    )
    out["disagreed_one_right"] = _rate(
        dis, meetings, lambda r: r["class"] == "disagree_one_right", lambda r: one, seed
    )
    out["catch_strict"] = _rate(
        rows,
        meetings,
        lambda r: _wrong(r, False) if r["flag_strict"] else 0,
        lambda r: _wrong(r, False),
        seed,
    )
    out["catch_broad"] = _rate(
        rows,
        meetings,
        lambda r: _wrong(r, True) if r["flag_broad"] else 0,
        lambda r: _wrong(r, True),
        seed,
    )
    for name in ("flag_strict", "flag_broad"):
        out[f"{name}_share"] = _rate(rows, meetings, lambda r, k=name: bool(r[k]),  # type: ignore[misc]
                                     lambda r: one, seed)  # fmt: skip
        per = Counter(r["meeting"] for r in rows if r[name])
        counts = [per.get(m, 0) for m in meetings]
        ci = ratio_ci([(float(c), 1.0) for c in counts], seed)
        out[f"{name}_per_meeting"] = {
            "mean": round(sum(counts) / len(counts), 2) if counts else None,
            "min": min(counts, default=0),
            "max": max(counts, default=0),
            "ci": list(ci) if ci else None,
            "by_meeting": dict(zip(meetings, counts, strict=True)),
        }
    return out


def stop_rules(pooled: Mapping[str, Any]) -> dict[str, Any]:
    """The three pre-stated stop rules (plus the ideas file's noise rule) on a pooled block.
    ``stop`` is true when the rule says stop."""
    wrong = pooled["agreed_wrong"]["rate"]
    strict, broad = pooled["catch_strict"]["rate"], pooled["catch_broad"]["rate"]
    per = pooled["flag_strict_per_meeting"]["mean"]
    share = pooled["flag_strict_share"]["rate"]
    return {
        "agreed_wrong": {
            "value": wrong,
            "threshold": STOP_AGREED_WRONG,
            "stop": wrong is not None and wrong > STOP_AGREED_WRONG,
        },
        "catch": {
            "value": strict,
            "value_broad": broad,
            "threshold": STOP_CATCH,
            "stop": strict is not None and strict < STOP_CATCH,
            "stop_broad": broad is not None and broad < STOP_CATCH,
        },
        "disagreed_per_meeting": {
            "value": per,
            "threshold": STOP_PER_MEETING,
            "stop": per is not None and per < STOP_PER_MEETING,
        },
        "noise_share": {
            "value": share,
            "value_broad": pooled["flag_broad_share"]["rate"],
            "threshold": NOISE_SHARE,
            "stop": share is not None and share > NOISE_SHARE,
        },
    }


def summarise(
    rows: Sequence[Mapping[str, Any]], meetings: Sequence[str], seed: int
) -> dict[str, Any]:
    """Counts per kind per meeting, per-product accuracy, the two-hearings classes and the stop
    rules, per kind and pooled (pooled also without negations). Excluded uses are counted apart
    and enter no rate."""
    counted = [r for r in rows if not r["excluded"]]
    counts = {
        m: {k: sum(r["meeting"] == m and r["kind"] == k for r in counted) for k in KINDS}
        for m in meetings
    }
    by_kind = {
        k: _block([r for r in counted if r["kind"] == k], meetings, seed)
        for k in KINDS
        if any(r["kind"] == k for r in counted)
    }
    pooled = _block(counted, meetings, seed)
    hard = _block([r for r in counted if r["kind"] != "negation"], meetings, seed)
    return {
        "version": VERSION,
        "seed": seed,
        "meetings": list(meetings),
        "counts": counts,
        "excluded": dict(Counter(r["excluded"] for r in rows if r["excluded"])),
        "by_kind": by_kind,
        "pooled": pooled,
        "pooled_no_negation": hard,
        "stop_rules": stop_rules(pooled),
        "stop_rules_no_negation": stop_rules(hard),
    }


def examples(rows: Sequence[Mapping[str, Any]], n: int, seed: int) -> list[Mapping[str, Any]]:
    """``n`` seeded disagreed values (strict flag), in meeting and time order. When fewer than
    ``n`` exist, all of them, then seeded values that only one hearing has (broad flag)."""
    rng = random.Random(f"{seed}:heard_twice.examples")
    counted = [r for r in rows if not r["excluded"]]
    strict = [r for r in counted if r["flag_strict"]]
    picked = rng.sample(strict, min(n, len(strict)))
    rest = [r for r in counted if r["flag_broad"] and not r["flag_strict"]]
    fill = rng.sample(rest, min(n - len(picked), len(rest)))
    return sorted(picked, key=lambda r: r["id"]) + sorted(fill, key=lambda r: r["id"])


# -- note-level check -------------------------------------------------------------------------


def note_values(
    meeting_id: str,
    product: str,
    bullets: Sequence[tuple[str, str]],
    spans: Mapping[str, Sequence[tuple[int, int]]],
    human: Stream,
    human_vals: Sequence[Value],
    own: Stream,
    own_vals: Sequence[Value],
    pairs: Sequence[tuple[int, int]],
) -> list[dict[str, Any]]:
    """Hard values in a product's note bullets (``(id, text)``), each checked against the human
    transcript. ``spans`` gives a bullet's product ``T`` line ranges (word-overlap alignment);
    the aligned moment is the human words anchored to those lines, :data:`NOTE_PAD` either side.

    Numeric values are matched on the number alone (units ignored): ``at_moment`` (the human
    transcript has the number in the aligned moment), ``elsewhere`` (only elsewhere in the
    meeting: ambiguous), ``absent`` (nowhere in the human transcript: worked out by the
    note-writer, misheard, or a matching failure: ambiguous), ``no_moment`` when the bullet has
    no aligned passage and the number is somewhere in the meeting. Dates are matched by word.
    Negations are counted, not matched: a note's "not" is the writer's wording."""
    rows: list[dict[str, Any]] = []
    for bid, text in bullets:
        toks = clean_words(text)
        vals = extract([t for t, _ in toks], [b for _, b in toks])
        lines = {i for a, b in spans.get(bid, ()) for i in range(a, b + 1)}
        hyp_pos = [i for i, ln in enumerate(own.line) if ln in lines]
        window: tuple[int, int] | None = None
        if hyp_pos:
            lo, hi = hyp_pos[0], hyp_pos[-1]
            refs = [r for r, h in pairs if lo <= h <= hi]
            if refs:
                window = (refs[0] - NOTE_PAD, refs[-1] + NOTE_PAD)
        for v in vals:
            row: dict[str, Any] = {
                "meeting": meeting_id,
                "product": product,
                "bullet": bid,
                "kind": v.kind,
                "value": v.shown,
                "text": v.text,
                "excluded": v.excluded,
                "bullet_text": text,
            }
            if v.kind == "negation" or v.excluded:
                row["match"] = "not_matched"
                rows.append(row)
                continue

            def eq(x: Value, v: Value = v) -> bool:
                return same(x, v) if v.kind == "date" else same_number(x, v)

            there = [h for h in human_vals if eq(h)]
            at = [h for h in there if window and window[0] <= h.start <= window[1]]
            own_there = [m for m in own_vals if eq(m)]
            own_at = [m for m in own_there if hyp_pos and hyp_pos[0] <= m.start <= hyp_pos[-1]]
            if at:
                row["match"] = "at_moment"
            elif there:
                row["match"] = "elsewhere" if window else "no_moment"
            else:
                row["match"] = "absent"
            row["own_transcript"] = (
                "at_moment" if own_at else "elsewhere" if own_there else "absent"
            )
            row["same_unit_in_human"] = any(same(h, v) for h in there)
            if there and not at:
                row["human_times_s"] = [round(human.time[h.start], 1) for h in there[:5]]
            rows.append(row)
    return rows


def note_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per product: note values by kind, and the match classes of the matched ones."""
    out: dict[str, Any] = {}
    for p in PRODUCTS:
        mine = [r for r in rows if r["product"] == p]
        matched = [r for r in mine if r["match"] != "not_matched"]
        out[p] = {
            "values": sum(not r["excluded"] for r in mine),
            "by_kind": dict(Counter(r["kind"] for r in mine if not r["excluded"])),
            "excluded": sum(bool(r["excluded"]) for r in mine),
            "matched": len(matched),
            "match": dict(Counter(r["match"] for r in matched)),
            "elsewhere_same_unit": sum(
                r["match"] in ("elsewhere", "no_moment") and r["same_unit_in_human"]
                for r in matched
            ),
            "own_transcript_when_not_at_moment": dict(
                Counter(r["own_transcript"] for r in matched if r["match"] != "at_moment")
            ),
            "per_meeting": dict(Counter(r["meeting"] for r in matched)),
        }
    return out


# -- truth-free mode: two hearings against each other -----------------------------------------
#
# Where no human transcript exists, the two hearings are compared directly. Hearing A plays the
# part the human transcript plays above (its values are the units, B's words are aligned to
# its words), so the same matcher gives: the same value in both (``equal``), another value of
# the same family at the same spot (``different``: the "heard two ways" mark), or a value in
# one hearing only. Nothing here says which hearing is right.

PAIR_OUTCOMES = ("equal", "different", "only_a", "only_b")
DIGIT_RUN = 3  # numbers said in a row that make one digit string ("one two one two eight one")


def _spoken_digits(v: Value) -> str | None:
    """The digits of a number word that can be part of a digit string: a single digit ("two"),
    a two-digit group ("eighty three") or a run of two ("two eighty three")."""
    if v.kind != "number" or not v.text.replace(" ", "").isalpha():
        return None
    if v.parts:
        return "".join(str(int(Fraction(x))) for x in v.parts)
    (val,) = v.vals if len(v.vals) == 1 else ("",)
    return val if val.isdigit() and int(val) < 100 else None


def digit_strings(values: Sequence[Value], brk: Sequence[bool]) -> list[Value]:
    """``values`` with every digit string joined into one number: :data:`DIGIT_RUN` or more
    number words below 100 said one after another with no punctuation between, at least two of
    them single digits. A bill number read out ("one two one two eight one", "one two one two
    eighty three") is then the value 121281 or 121283, as the other hearing may write it. Used
    on both hearings in the truth-free mode only."""
    out: list[Value] = []
    i = 0
    while i < len(values):
        j = i
        if _spoken_digits(values[i]) is not None:
            while (
                j + 1 < len(values)
                and _spoken_digits(values[j + 1]) is not None
                and values[j + 1].start == values[j].end
                and not brk[values[j].end - 1]
            ):
                j += 1
        run = values[i : j + 1]
        spoken = [str(_spoken_digits(v)) for v in run]
        if len(run) >= DIGIT_RUN and sum(len(d) == 1 for d in spoken) >= 2:
            number = str(int("".join(spoken)))
            text = " ".join(v.text for v in run)
            out.append(Value("number", "", frozenset({number}), run[0].start, run[-1].end, text))
            i = j + 1
        else:
            out.append(values[i])
            i += 1
    return out


def same_written(a: Value, b: Value) -> bool:
    """:func:`same`, and a cardinal and an ordinal of one number are one value too ("September
    19" / "September 19th"): between two hearings that is a way of writing, not a hearing."""
    plain = ("", "ord", "clock")
    return same(a, b) or (a.unit in plain and b.unit in plain and bool(a.vals & b.vals))


def hearing_values(stream: Stream) -> list[Value]:
    """A product transcript's hard values for the truth-free mode (digit strings joined)."""
    return digit_strings(product_values(stream), stream.brk)


def _context(stream: Stream, start: int, end: int, pad: int) -> str:
    return " ".join(stream.words[max(0, start - pad) : min(len(stream.words), end + pad)])


def _mark_type(a: Value, b: Value) -> str:
    """What differs in a ``different`` pair: only the unit, the negation word, the date, or the
    number itself."""
    if a.family == "numeric":
        return "unit_only" if a.vals & b.vals else "number"
    return "negation_word" if a.family == "negation" else "date"


def pair_rows(
    recording: str, a: Stream, b: Stream, context: int = 6
) -> tuple[list[dict[str, Any]], int]:
    """The truth-free comparison of two hearings of one recording: one JSON row per value of
    hearing ``a`` (``equal`` / ``different`` / ``only_a``) and per value of hearing ``b`` that
    no value of ``a`` was matched to (``only_b``), and the number of alignment anchors.

    ``b``'s words are aligned to ``a``'s by shared word runs checked against the running time
    offset (:func:`anchors`); values are matched as :func:`classify` matches a product to the
    human transcript. A row is ``counted`` unless a value in it is a figure of speech
    (``excluded``). ``mark`` on a ``different`` row says what differs (:func:`_mark_type`).
    Times are seconds on each hearing's own clock."""
    va, vb = hearing_values(a), hearing_values(b)
    pairs = anchors(a, b)
    rows: list[dict[str, Any]] = []
    used: set[int] = set()

    def side(stream: Stream, v: Value) -> dict[str, Any]:
        return {
            "value": v.shown,
            "text": v.text,
            "kind": v.kind,
            "at": v.start,
            "time_s": round(stream.time[v.start], 1),
            "line": stream.line[v.start],
            "words": _context(stream, v.start, v.end, context),
        }

    matched = _match(va, vb, pairs, len(b.words), same_written)
    used.update(k for _, took in matched for k in took)
    apart_a, apart_b = _written_apart(va, vb, matched, used)
    used.update(apart_b)
    for i, (v, (h, took)) in enumerate(zip(va, matched, strict=True)):
        if i in apart_a and h.outcome == "missing":
            continue  # the rest of a number written apart: part of the row before
        if i in apart_a or (took and took[0] in apart_b):
            h = Hearing("correct", h.heard, h.text, h.at)
        row: dict[str, Any] = {"recording": recording, "kind": v.kind, "a": side(a, v), "b": None}
        theirs = [vb[k] for k in took]
        row["outcome"] = {"correct": "equal", "different": "different", "missing": "only_a"}[
            h.outcome
        ]
        row["excluded"] = v.excluded or next((m.excluded for m in theirs if m.excluded), "")
        if theirs:
            row["b"] = side(b, theirs[0])
            row["b"]["text"] = h.text
            row["b"]["words"] = _context(b, theirs[0].start, theirs[-1].end, context)
        else:
            row["b_words"] = _context(b, h.at, h.at + 1, context)
        if h.outcome == "different":
            row["mark"] = _mark_type(v, theirs[0])
        rows.append(row)
    for k, m in enumerate(vb):
        if k not in used:
            a_at = _expected_in_ref(m.start, pairs)
            rows.append(
                {"recording": recording, "kind": m.kind, "a": None, "b": side(b, m),
                 "outcome": "only_b", "excluded": m.excluded,
                 "a_words": _context(a, a_at, a_at + 1, context) if a.words else ""}
            )  # fmt: skip
    for n, row in enumerate(rows):
        row["id"] = f"{recording}:p{n:04d}"
        row["counted"] = not row["excluded"]
    return rows, len(pairs)


def _written_apart(
    va: Sequence[Value],
    vb: Sequence[Value],
    matched: Sequence[tuple[Hearing, tuple[int, ...]]],
    used: set[int],
) -> tuple[set[int], set[int]]:
    """One hearing writes a long number in pieces ("3018 378" for "3018378"). For every
    ``different`` pair, the value on one side and the unmatched values right after it are
    joined; when their digits are the other side's number, the pair is one value. Returns the
    positions in ``va`` (the pair's value and the pieces) and in ``vb`` (likewise)."""

    def digits(v: Value) -> str | None:
        (val,) = v.vals if len(v.vals) == 1 else ("",)
        return v.text if v.text.isdigit() else val if val.isdigit() else None

    def pieces(vals: Sequence[Value], first: int, free: Callable[[int], bool], whole: str) -> int:
        """How many values from ``first`` on spell ``whole`` (0: none do)."""
        text, k = digits(vals[first]) or "", first
        while text and text != whole and whole.startswith(text) and k + 1 < len(vals):
            nxt = digits(vals[k + 1])
            if nxt is None or vals[k + 1].start != vals[k].end or not free(k + 1):
                break
            text, k = text + nxt, k + 1
        return k - first + 1 if text == whole and k > first else 0

    in_a: set[int] = set()
    in_b: set[int] = set()
    for i, (h, took) in enumerate(matched):
        if h.outcome != "different":
            continue
        k = took[0]
        da, db = digits(va[i]), digits(vb[k])
        if da is None or db is None:
            continue
        n = pieces(va, i, lambda x: matched[x][0].outcome == "missing", db)
        m = pieces(vb, k, lambda x: x not in used and x not in in_b, da)
        if n:
            in_a.update(range(i, i + n))
        elif m:
            in_b.update(range(k, k + m))
    return in_a, in_b


def _expected_in_ref(hyp_pos: int, pairs: Sequence[tuple[int, int]]) -> int:
    """The position in hearing A nearest to position ``hyp_pos`` of hearing B (last anchor at
    or before it, moved on by the same number of words)."""
    if not pairs:
        return 0
    k = bisect.bisect_right([h for _, h in pairs], hyp_pos) - 1
    if k < 0:
        return max(0, pairs[0][0] - (pairs[0][1] - hyp_pos))
    return pairs[k][0] + (hyp_pos - pairs[k][1])


def pair_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts for one set of pair rows: values in each hearing (counted rows that hold one),
    the four outcomes, what differs in the marks, and the outcomes per kind."""
    counted = [r for r in rows if r["counted"]]
    oc = Counter(r["outcome"] for r in counted)
    marks = Counter(r["mark"] for r in counted if r["outcome"] == "different")
    by_kind = {
        k: {o: sum(r["kind"] == k and r["outcome"] == o for r in counted) for o in PAIR_OUTCOMES}
        for k in KINDS
        if any(r["kind"] == k for r in counted)
    }
    return {
        "values_a": oc["equal"] + oc["different"] + oc["only_a"],
        "values_b": oc["equal"] + oc["different"] + oc["only_b"],
        **{o: oc.get(o, 0) for o in PAIR_OUTCOMES},
        "marks": dict(sorted(marks.items())),
        "not_counted": len(rows) - len(counted),
        "by_kind": by_kind,
    }


def pair_summary(
    rows: Sequence[Mapping[str, Any]], durations: Mapping[str, float], seed: int
) -> dict[str, Any]:
    """Per recording and pooled: :func:`pair_counts`, plus marks (``different`` rows) per
    recording and per 10 minutes of audio, with a recording-cluster bootstrap CI when pooled.
    ``durations`` (seconds of audio) fixes the recordings and their order."""
    per: dict[str, Any] = {}
    for rec, dur in durations.items():
        c = pair_counts([r for r in rows if r["recording"] == rec])
        c["duration_s"] = round(dur, 1)
        c["marks_per_10min"] = round(c["different"] * 600 / dur, 2) if dur else None
        per[rec] = c
    pooled = pair_counts(rows)
    total = sum(durations.values())
    n = len(durations)
    marks = [float(per[r]["different"]) for r in durations]
    hard = [float(sum(v for k, v in per[r]["marks"].items() if k != "negation_word"))
            for r in durations]  # fmt: skip
    pooled["duration_s"] = round(total, 1)
    pooled["recordings"] = n
    pooled["marks_per_recording"] = {
        "mean": round(sum(marks) / n, 2) if n else None,
        "min": min(marks, default=0.0),
        "max": max(marks, default=0.0),
        "ci": _ci(ratio_ci([(m, 1.0) for m in marks], seed)),
    }
    pooled["marks_per_10min"] = {
        "rate": round(sum(marks) * 600 / total, 2) if total else None,
        "ci": _ci(ratio_ci([(m * 600, d) for m, d in zip(marks, durations.values(), strict=True)],
                           seed)),
    }  # fmt: skip
    pooled["marks_no_negation_per_10min"] = {
        "rate": round(sum(hard) * 600 / total, 2) if total else None,
        "ci": _ci(ratio_ci([(m * 600, d) for m, d in zip(hard, durations.values(), strict=True)],
                           seed)),
    }  # fmt: skip
    return {"recordings": per, "pooled": pooled}


def _ci(ci: tuple[float, float] | None) -> list[float] | None:
    return [round(x, 2) for x in ci] if ci else None


def truth_links(
    values: Sequence[Value], hearings: Mapping[str, Sequence[Hearing]]
) -> list[dict[str, Any]]:
    """Per human value, what :func:`validate_pairs` needs of the truth-based result: the
    two-hearings class and where each product's value sits (``None`` when it heard none)."""
    a, b = (hearings[p] for p in PRODUCTS)
    return [
        {
            "class": two_hearings(g, w),
            "excluded": v.excluded,
            "kind": v.kind,
            "a_at": None if g.outcome == "missing" else g.at,
            "b_at": None if w.outcome == "missing" else w.at,
        }
        for v, g, w in zip(values, a, b, strict=True)
    ]


def validate_pairs(
    truth: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """The truth-free rows of one recording against the truth-based result (:func:`truth_links`,
    hearing A = the first of :data:`PRODUCTS`). A human value and a pair row are linked when a
    product's value for the human value is the value in the row.

    ``truth``: per two-hearings class of the counted human values, the outcome of the linked
    pair row (``split``: the two products' values sit in two rows; ``none``: neither product
    had a value). ``marks``: every counted ``different`` row by the class of the human value
    it is linked to (``no_human_value``: nobody said a value there according to the human
    transcript; ``figure_of_speech``: the human value is excluded). Also sets ``truth_class``
    on each linked row's copy in ``marks_rows``."""
    by_a = {r["a"]["at"]: r for r in rows if r["a"]}
    by_b = {r["b"]["at"]: r for r in rows if r["b"]}
    linked: dict[str, str] = {}
    table: dict[str, Counter[str]] = {c: Counter() for c in CLASSES}
    for t in truth:
        ra = by_a.get(t["a_at"]) if t["a_at"] is not None else None
        rb = by_b.get(t["b_at"]) if t["b_at"] is not None else None
        label = "figure_of_speech" if t["excluded"] else t["class"]
        for r in (ra, rb):
            # a disagreed human value outranks any other link to the same row
            if r is not None and (label.startswith("disagree") or r["id"] not in linked):
                linked[r["id"]] = label
        if t["excluded"]:
            continue
        if ra is None and rb is None:
            seen = "none"
        elif ra is not None and rb is not None and ra["id"] != rb["id"]:
            seen = "split"
        else:
            row = ra if ra is not None else rb
            assert row is not None
            seen = row["outcome"] if row["counted"] else "not_counted"
        table[t["class"]][seen] += 1
    marks = [r for r in rows if r["counted"] and r["outcome"] == "different"]
    mark_rows = [{**r, "truth_class": linked.get(r["id"], "no_human_value")} for r in marks]
    by_class = Counter(r["truth_class"] for r in mark_rows)
    return {
        "truth": {c: dict(sorted(table[c].items())) for c in CLASSES},
        "marks": dict(sorted(by_class.items())),
        "marks_rows": mark_rows,
    }


# -- official values (council record) ---------------------------------------------------------

NEAR_PAD = 6  # words between a small official number and the word that says what it counts
CUE_PAD = 2  # words between a cue word and another number that would be the wrong value
NO_COUNT = frozenset(("none", "zero", "nobody"))  # a count of nothing before "in favor"


@dataclass(frozen=True)
class Target:
    """One checkable value of an official record. A small number (a vote count, a day of the
    month) is only ``right`` beside one of the ``near`` words; ``after`` / ``before`` are cue
    words a competing number follows ("bill 121281") or precedes ("8 in favor")."""

    label: str
    shown: str
    vals: frozenset[str]  # numeric readings as fractions
    near: frozenset[str] = frozenset()
    after: frozenset[str] = frozenset()
    before: frozenset[str] = frozenset()


def find_target(
    target: Target, words: Sequence[str], values: Sequence[Value], context: int = 8
) -> dict[str, Any]:
    """Whether a text (its cleaned ``words`` and their ``values``) has an official value:
    ``right`` (the number is there; beside a ``near`` word when the target has any), ``wrong``
    (it is not, and another number sits within :data:`CUE_PAD` words after an ``after`` cue or
    before a ``before`` cue, or "none" does), else ``absent``. ``hits`` and ``others`` quote
    every place, so a reader can check each case; ``others`` is listed even when the status is
    ``right``. A number written in two pieces ("127 362") counts as the number."""

    def cue(v: Value, cues: frozenset[str], lo: int, hi: int) -> bool:
        return any(w in cues for w in words[max(0, v.start - lo) : v.end + hi])

    def quote(v: Value) -> str:
        return " ".join(words[max(0, v.start - context) : v.end + context])

    def apart(k: int) -> bool:
        """``nums[k]`` and the number right after it spell the target ("127 362")."""
        if k + 1 >= len(nums) or nums[k + 1].start != nums[k].end:
            return False
        text = nums[k].text + nums[k + 1].text
        return text.isdigit() and str(Fraction(int(text))) in target.vals

    nums = [v for v in values if v.family == "numeric" and v.excluded != "speaker_label"]
    joined = {j for k in range(len(nums)) if apart(k) for j in (k, k + 1)}
    hits = [
        v
        for k, v in enumerate(nums)
        if (v.vals & target.vals or k in joined)
        and (not target.near or cue(v, target.near, NEAR_PAD, NEAR_PAD))
    ]
    others = [
        v
        for k, v in enumerate(nums)
        if not (v.vals & target.vals or k in joined)
        and (
            (bool(target.after) and cue(v, target.after, CUE_PAD, 0))
            or (bool(target.before) and cue(v, target.before, 0, CUE_PAD))
        )
    ]
    none = [
        i
        for i, w in enumerate(words)
        if w in NO_COUNT and any(x in target.before for x in words[i + 1 : i + 1 + CUE_PAD])
    ]  # "none in favor" where the record has a count
    status = "right" if hits else "wrong" if others or none else "absent"
    return {
        "status": status,
        "hits": [quote(v) for v in hits],
        "others": [{"value": v.shown, "words": quote(v)} for v in others]
        + [
            {"value": words[i], "words": " ".join(words[max(0, i - context) : i + 1 + context])}
            for i in none
        ],
    }


def text_values(text: str) -> tuple[list[str], list[Value]]:
    """Cleaned words and hard values (digit strings joined) of free text (notes, a title)."""
    toks = clean_words(text)
    words, brk = [t for t, _ in toks], [b for _, b in toks]
    return words, digit_strings(extract(words, brk), brk)


MONTHS = frozenset(
    _words("january february march april june july august september october november december")
)
_FAVOR = frozenset(_words("favor favour"))
_OPPOSED = frozenset(_words("opposed against"))
_VOTE = frozenset(_words("vote votes voted passed passes adopted unanimous unanimously tally"))


def official_targets(
    matter_file: str, title: str, action_text: str, votes: Mapping[str, int]
) -> tuple[list[Target], list[str]]:
    """The checkable values of one Legistar agenda item, and the title values left unchecked.

    * the matter number ("CB 121282": 121282), competing with any number after "bill" /
      "resolution";
    * the vote counts (``votes``: "In Favor" / "Opposed" -> count; a zero count is not a
      target), each only beside a voting word, competing with a number before "in favor" /
      "opposed";
    * every number in the title and the action text: a day of the month beside its month; a
      number of three digits or more anywhere ("Initiative 137", "Ordinance 127362"); a
      smaller number beside the word before it. The year of a date and a smaller number with
      no word before it ("3/4") are returned in the second list, unchecked."""
    out: list[Target] = []
    skipped: list[str] = []
    kind, _, number = matter_file.partition(" ")
    bill = frozenset({str(Fraction(int(number)))})
    cues = frozenset(_words("bill cb resolution res"))
    out.append(Target(f"matter number ({kind})", matter_file, bill, after=cues))
    for name, near, before in (("In Favor", _FAVOR | _VOTE | frozenset(_words("yes aye ayes")),
                                _FAVOR),
                               ("Opposed", _OPPOSED | _VOTE | frozenset(_words("no nay nays")),
                                _OPPOSED)):  # fmt: skip
        if votes.get(name):
            vals = frozenset({str(Fraction(votes[name]))})
            out.append(Target(f"votes {name.lower()}", str(votes[name]), vals, near, before=before))
    seen = {bill}
    for source, text in (("title", title), ("action text", action_text)):
        words, values = text_values(text)
        for v in values:
            if v.family != "numeric" or v.vals in seen:
                continue
            seen.add(v.vals)
            prev = words[v.start - 1] if v.start else ""
            cue = frozenset({prev}) if prev.isalpha() and len(prev) > 3 else frozenset()
            big = any(Fraction(x) >= 100 or Fraction(x).denominator != 1 for x in v.vals)
            if v.start > 1 and words[v.start - 2] in MONTHS and prev.isdigit():
                skipped.append(f"{source}: year of the date {' '.join(words[v.start - 2 : v.end])}")
            elif prev in MONTHS:
                out.append(Target(f"date in {source}", f"{prev} {v.text}", v.vals, cue, cue))
            elif big:
                shown = f"{prev} {v.text}" if cue else v.text
                out.append(Target(f"number in {source}", shown, v.vals, after=cue))
            elif cue:
                out.append(Target(f"number in {source}", f"{prev} {v.text}", v.vals, cue, cue))
            else:
                skipped.append(f"{source}: {' '.join(words[max(0, v.start - 2) : v.end + 2])}")
    return out, skipped
