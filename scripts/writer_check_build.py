# ruff: noqa: E501  (HTML templates)
"""Build the private blind check sheet for the "since last time" writer (data/private/WRITER_CHECK.html) and its key.

    uv run --no-sync python scripts/writer_check_build.py [--seed 42]

One card per distinct sentence the writer wrote on the 36 item-fates pairs, over both writer runs
(``sw36_v1_20261114``, ``sw36_v2_20261114``). A v2 sentence is merged into a v1 sentence when
both are on the same pair, of the same kind, rest on at least one common notes bullet, and share
at least :data:`MERGE_JACCARD` of their content words; the card keeps the earlier (v1) wording
and the key records the merge. Cards are sorted by pair and run, then shuffled with
``random.Random(seed)``.

Each card shows, from the job files only (no LLM, no network): one line of background, the
sentence, the notes bullet(s) it cites, and the later meeting's human transcript: for a
"changed" sentence the cited lines with :data:`PAD_CHANGED` lines either side; for a "not
mentioned" sentence the :data:`N_CLOSEST` passages where the cited bullets' words come
closest to being raised (:func:`closest`), :data:`PAD_CLOSEST` lines either side, labelled as a
word search. The whole later
transcript is one click away. The page hides the checkers' verdicts, the writer version, the
item fates and the kind; the key (separate JSON) holds them. Outputs are private.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from e3_check_build import CSS, content_stems, marked  # noqa: E402

RUNS = ("sw36_v1_20261114", "sw36_v2_20261114")  # earlier first: its wording is kept
RUN_DIR = ROOT / "data/private/reader/sw_runs"
JOB_DIR = ROOT / "data/private/reader/sw_jobs"
OUT_HTML = ROOT / "data/private/WRITER_CHECK.html"
OUT_KEY = ROOT / "data/private/WRITER_CHECK_key.json"
MERGE_JACCARD = 0.5
PAD_CHANGED = 10
PAD_CLOSEST = 8
N_CLOSEST = 3
POSITION = {"a": "first", "b": "second", "c": "third", "d": "fourth"}
ANSWERS = (("true", "True"), ("partly", "Partly"), ("false", "False"))
MINUTES_PER_CARD = (3, 5)
REACH = 2  # lines either side that count towards a closest-passage score
SPELLING = (
    ("colour", "color"),
    ("recognis", "recogniz"),
    ("favour", "favor"),
    ("centre", "center"),
    ("organis", "organiz"),
)

_BULLET = re.compile(r"^(B\d+) \[section: ([^\]]+)\] (.*)$")
_ACRONYM = re.compile(r"\b(?:[A-Za-z]_){2,}")  # AMI spells acronyms R_S_I_
_LINE = re.compile(r"^(L\d{4}) \[(\d+:\d\d)\] ([^:]+): (.*)$")


# -- pure logic (tested) ---------------------------------------------------------------------


def jaccard(a: str, b: str) -> float:
    """Share of content-word stems the two texts have in common (0 when both are empty)."""
    sa, sb = content_stems(a), content_stems(b)
    return len(sa & sb) / len(sa | sb) if sa | sb else 0.0


def same_claim(x: dict[str, Any], y: dict[str, Any]) -> bool:
    """Whether two sentences make near-identical claims about the same item."""
    return (
        x["unit"] == y["unit"]
        and x["kind"] == y["kind"]
        and bool(set(x["x_bullets"]) & set(y["x_bullets"]))
        and jaccard(x["sentence"], y["sentence"]) >= MERGE_JACCARD
    )


def dedupe(runs: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Distinct claims over the runs, in run order then file order.

    Each claim is ``{"unit", "kind", "sentence", "x_bullets", "y_lines", "versions"}`` where
    ``versions`` lists every (run, sentence) merged into it, the kept one first; ``y_lines`` is
    the sorted union of the merged sentences' cited lines.
    """
    claims: list[dict[str, Any]] = []
    for run, rows in runs.items():
        for r in rows:
            hit = next((c for c in claims if run not in c["runs"] and same_claim(c, r)), None)
            if hit is None:
                claims.append(
                    {
                        "unit": r["unit"],
                        "kind": r["kind"],
                        "sentence": r["sentence"],
                        "x_bullets": list(r["x_bullets"]),
                        "y_lines": sorted(set(r["y_lines"])),
                        "runs": [run],
                        "versions": [r],
                    }
                )
            else:
                hit["runs"].append(run)
                hit["versions"].append(r)
                hit["y_lines"] = sorted(set(hit["y_lines"]) | set(r["y_lines"]))
    return claims


def shuffled(claims: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    """The claims sorted by pair, first run and position in that run, then seeded-shuffled."""
    order = sorted(claims, key=lambda c: (c["unit"], c["runs"][0], c["versions"][0]["n"]))
    random.Random(seed).shuffle(order)
    return order


def stems(text: str) -> set[str]:
    """Content-word stems with British spellings folded to American (colour -> color)."""
    out = set()
    for w in content_stems(_ACRONYM.sub(lambda m: m[0].replace("_", ""), text)):
        w = w.rstrip("'")  # remote's -> remote' -> remote
        for brit, us in SPELLING:
            w = w.replace(brit, us)
        out.add(w)
        out.update(part for part in w.split("-")[1:] if len(part) >= 3)  # anti-rsi -> rsi
    return out


def one_letter_apart(a: str, b: str) -> bool:
    """Whether ``b`` is ``a`` with one letter added or dropped (both at least 5 letters)."""
    if min(len(a), len(b)) < 5 or abs(len(a) - len(b)) != 1:
        return False
    short, long_ = sorted((a, b), key=len)
    return any(long_[:i] + long_[i + 1 :] == short for i in range(len(long_)))


def expand_keys(keys: set[str], lines: list[dict[str, str]]) -> set[str]:
    """``keys`` plus, for each key the transcript never uses, the transcript words one letter
    apart from it (an annotator's "proft" finds "profit")."""
    vocab: set[str] = set().union(*(stems(t["text"]) for t in lines)) if lines else set()
    missing = keys - vocab
    return keys | {w for w in vocab if any(one_letter_apart(k, w) for k in missing)}


def closest(
    lines: list[dict[str, str]], keys: set[str], pad: int, n: int, reach: int = REACH
) -> list[int]:
    """Up to ``n`` line indices where the item comes closest to being raised, in transcript order.

    A line is a candidate when it shares a word with ``keys``. A word's weight is its rarity in
    this transcript (log inverse line frequency), so "lawsuit" counts for more than "remote". The
    picks are greedy: each time, the candidate whose surroundings (``reach`` lines either side)
    hold the most weight in ``keys`` words that no earlier pick's surroundings held, then the most
    weight overall, then the most on the line itself, then the earliest. Picks are more than
    ``2 * pad`` lines apart, so the passages show different places and, where possible,
    different parts of the item.
    """
    sets = [stems(t["text"]) for t in lines]
    df = Counter(k for st in sets for k in st & keys)
    idf = {k: math.log((len(lines) + 1) / df[k]) for k in df}
    near = [set().union(*sets[max(0, i - reach) : i + reach + 1]) & keys for i in range(len(sets))]

    def weight(words: set[str]) -> float:
        return round(sum(idf[k] for k in words), 9)

    cands = [i for i, st in enumerate(sets) if st & keys]
    picks: list[int] = []
    covered: set[str] = set()
    while len(picks) < n:
        free = [i for i in cands if all(abs(i - p) > 2 * pad for p in picks)]
        if not free:
            break
        best = min(
            free,
            key=lambda i: (
                -weight(near[i] - covered),
                -weight(near[i]),
                -weight(sets[i] & keys),
                i,
            ),
        )
        picks.append(best)
        covered |= near[best]
    return sorted(picks)


def windows(idx: list[int], pad: int, n: int) -> list[tuple[int, int]]:
    """Merged half-open [lo, hi) windows of ``pad`` lines either side of each index."""
    out: list[tuple[int, int]] = []
    for i in sorted(idx):
        lo, hi = max(0, i - pad), min(n, i + pad + 1)
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


# -- job files --------------------------------------------------------------------------------


def load_runs() -> dict[str, list[dict[str, Any]]]:
    """Every writer sentence per run, with its pair, position in the file and checker verdicts."""
    runs: dict[str, list[dict[str, Any]]] = {}
    for run in RUNS:
        rows = {
            (r["unit"], r["n"]): r for r in json.loads((RUN_DIR / run / "rows.json").read_text())
        }
        out = []
        for p in sorted((RUN_DIR / run / "writer").glob("*.jsonl")):
            sents = [json.loads(s) for s in p.read_text().splitlines() if s.strip()]
            for n, s in enumerate(sents, 1):
                row = rows[(p.stem, n)]
                if row["sentence"] != s["sentence"]:
                    raise SystemExit(f"{run} {p.stem} #{n}: rows.json and writer output differ")
                out.append({**s, "unit": p.stem, "n": n, "run": run, "row": row})
        if len(out) != len(rows):
            raise SystemExit(f"{run}: {len(out)} sentences but {len(rows)} rows")
        runs[run] = out
    return runs


def read_job(unit: str) -> tuple[dict[str, tuple[str, str]], list[dict[str, str]]]:
    """The earlier meeting's bullets (id -> (section, text)) and the later meeting's lines."""
    text = (JOB_DIR / RUNS[0] / "writer" / f"{unit}.md").read_text()
    bullets: dict[str, tuple[str, str]] = {}
    lines: list[dict[str, str]] = []
    for raw in text.splitlines():
        if b := _BULLET.match(raw):
            bullets[b[1]] = (b[2], b[3])
        elif m := _LINE.match(raw):
            if m[1] != f"L{len(lines):04d}":
                raise SystemExit(f"{unit}: line {m[1]} out of order")
            lines.append({"id": m[1], "t": m[2], "who": m[3], "text": m[4]})
    return bullets, lines


# -- page -------------------------------------------------------------------------------------


def background(unit: str) -> str:
    x, y = unit.split("__")
    return (
        f"A team of four (Project Manager, Industrial Designer, User Interface Designer, Marketing "
        f"Expert) designing a new TV remote control (an acted AMI design task). Earlier meeting: "
        f"their {POSITION[x[-1]]} ({x}); later meeting: their {POSITION[y[-1]]}, the next one ({y})."
    )


def lines_html(
    lines: list[dict[str, str]], spans: list[tuple[int, int]], key: set[int], keys: set[str]
) -> str:
    out = []
    for k, (lo, hi) in enumerate(spans):
        if k:
            out.append('<div class="gap">&hellip; (lines left out) &hellip;</div>')
        for i in range(lo, hi):
            t = lines[i]
            cls = "line key" if i in key else "line"
            out.append(
                f'<div class="{cls}"><span class="t">{t["t"]}</span><span class="who">'
                f"{html.escape(t['who'])}</span> {marked(t['text'], keys)}</div>"
            )
    return "".join(out)


EXTRA_CSS = """
.claim{font-size:21px;font-weight:600;margin:6px 0 10px}
.cols2{display:grid;grid-template-columns:1fr;gap:14px}@media(min-width:1100px){.cols2{grid-template-columns:2fr 3fr}}
.col{max-height:none}.note{color:var(--warn);margin:0 0 8px}.passage{margin:0 0 12px}
.bullet{margin:0 0 8px}
"""

JS = """
const KEY='writercheck.v1';let A={};try{A=JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){A={}}
function save(){try{localStorage.setItem(KEY,JSON.stringify(A))}catch(e){}prog()}
function prog(){const n=document.querySelectorAll('.card').length,d=Object.values(A).filter(x=>x.answer).length;
document.getElementById('done').textContent=d+' of '+n+' answered';document.getElementById('fill').style.width=(100*d/n)+'%'}
function paint(id){const a=A[id]||{},c=document.getElementById(id);
c.querySelectorAll('button[data-a]').forEach(b=>b.classList.toggle('sel',b.dataset.a===a.answer));
if(a.note!==undefined)c.querySelector('textarea').value=a.note}
document.querySelectorAll('.card').forEach(c=>{const id=c.id;
c.querySelectorAll('button[data-a]').forEach(b=>b.onclick=()=>{const a=A[id]||{};a.answer=b.dataset.a;
if(!a.first_answer)a.first_answer=b.dataset.a;a.at=new Date().toISOString();A[id]=a;save();paint(id)});
c.querySelector('textarea').oninput=e=>{const a=A[id]||{};a.note=e.target.value;A[id]=a;save()};paint(id)});
document.getElementById('copy').onclick=()=>{const ids=[...document.querySelectorAll('.card')].map(c=>c.id).filter(id=>A[id]);
const t=ids.map(id=>JSON.stringify({sheet:'WRITER_CHECK',card:id,...A[id]})).join('\\n');
const done=()=>{document.getElementById('copied').textContent='Copied '+ids.length+' answers.'};
if(navigator.clipboard)navigator.clipboard.writeText(t).then(done,()=>{prompt('Copy this:',t)});else prompt('Copy this:',t)};
prog();
"""


def intro(n_cards: int) -> str:
    lo, hi = MINUTES_PER_CARD
    return f"""
<p><b>What this checks.</b> A writer reads the notes of a team's earlier meeting and the transcript
of their next meeting, and writes at most three sentences for the top of the new note about things
from last time that went wrong <i>quietly</i>: something agreed that came back different without
anyone saying so, or a task or decision that never came up at all. Automated checkers have read
these sentences. Nobody has checked them by hand. This sheet has all {n_cards} distinct sentences, in
random order, for you to judge without seeing what anyone else said.</p>
<p><b>Time.</b> About {lo} to {hi} minutes per card, so roughly {lo * n_cards} to {hi * n_cards} minutes
in all. Cards that say a thing never came up take longer if you search the whole later meeting.</p>
<p><b>Each card shows</b> one line about the team, the sentence, the item(s) from the earlier
meeting's notes it rests on (the notes are a human annotator's list of decisions and actions), and
parts of the later meeting's human transcript. Either the lines the sentence points to, highlighted,
with ten lines either side; or, when the sentence says something never came up, the few places that
share the most words with the item, found by a plain word search. <mark>Marked</mark> words are words
shared with the item. The whole later meeting is at the bottom of every card (open it and use Cmd-F).</p>
<p><b>The question: is the sentence true?</b></p>
<ul>
<li><b>True</b>: every part of it holds, including "nobody said so" or "it never came up".</li>
<li><b>Partly</b>: some of it holds and some does not (for example the change is real but someone
did remark on it, or the item came up briefly).</li>
<li><b>False</b>: the main claim does not hold.</li>
</ul>
<p>A note on why helps most for Partly and False. Answers save in this browser; when done, press
<b>Copy my answers</b> (one JSON line per answered card) and paste them back.</p>
"""


def card_html(n: int, total: int, c: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """One card's HTML and what the key records about it besides the hidden verdicts."""
    bullets, lines = read_job(c["unit"])
    cited = [(b, *bullets[b]) for b in c["x_bullets"]]
    keys = expand_keys(set().union(*(stems(text) for _b, _sec, text in cited)), lines)
    shown_keys = keys | {k.replace(us, brit) for k in keys for brit, us in SPELLING}
    items = "".join(
        f'<div class="bullet">&ldquo;{marked(text, shown_keys)}&rdquo; <span class="muted">'
        f"({'a decision' if sec == 'Decisions' else 'an action item'})</span></div>"
        for _b, sec, text in cited
    )
    all_notes = "".join(
        f"<li>{html.escape(text)} <span class='muted'>({html.escape(sec)})</span></li>"
        for sec, text in bullets.values()
    )
    if c["kind"] == "changed":
        idx = [int(s[1:]) for s in c["y_lines"]]
        head = (
            "Later meeting: the lines the sentence points to (highlighted), ten lines either side"
        )
        later = lines_html(lines, windows(idx, PAD_CHANGED, len(lines)), set(idx), shown_keys)
        shown = idx
    else:
        idx = closest(lines, keys, PAD_CLOSEST, N_CLOSEST)
        head = "Later meeting: the closest passages a word search found"
        note = (
            '<div class="note">The sentence says this never came up. These are only the '
            f"{len(idx)} places in the later meeting that share the most words with the item, found "
            "by a word search (eight lines either side, the matching line highlighted). They may be "
            "unrelated, and the item may have been raised in other words: search the whole later "
            "meeting below before answering.</div>"
            if idx
            else '<div class="note">A word search found no line of the later meeting sharing '
            "enough words with the item to show a passage. Search the whole later meeting below "
            "before answering.</div>"
        )
        later = note + lines_html(
            lines, windows(idx, PAD_CLOSEST, len(lines)), set(idx), shown_keys
        )
        shown = idx
    whole = lines_html(lines, [(0, len(lines))], set(shown), shown_keys)
    cid = f"W{n:02d}"
    page = f"""<section class="panel card" id="{cid}">
<div class="muted">Card {n} of {total}</div>
<div class="bg">{html.escape(background(c["unit"]))}</div>
<div class="q">The sentence:</div>
<div class="claim">&ldquo;{html.escape(c["sentence"])}&rdquo;</div>
<div class="cols2">
<div class="col"><h4>Earlier meeting: what the notes recorded</h4>{items}
<details><summary>All the earlier meeting's notes ({len(bullets)} items)</summary><ul>{all_notes}</ul></details></div>
<div class="col"><h4>{head}</h4>{later}
<details><summary>Show the whole later meeting ({len(lines)} lines)</summary>{whole}</details></div>
</div>
<div class="q">Is the sentence true?</div>
<div class="btns">{"".join(f'<button data-a="{a}">{label}</button>' for a, label in ANSWERS)}</div>
<textarea rows="2" placeholder="Optional note"></textarea>
</section>"""
    key = {
        "lines_shown_highlighted": [lines[i]["id"] for i in shown],
        "passages_are_word_search": c["kind"] == "not_mentioned",
    }
    return page, key


def build(seed: int) -> tuple[str, dict[str, Any]]:
    runs = load_runs()
    claims = shuffled(dedupe(runs), seed)
    cards, key_cards = [], {}
    for n, c in enumerate(claims, 1):
        page, extra = card_html(n, len(claims), c)
        cards.append(page)
        key_cards[f"W{n:02d}"] = {
            "pair": c["unit"],
            "kind": c["kind"],
            "sentence_shown": c["sentence"],
            "x_bullets": c["x_bullets"],
            "y_lines_cited": c["y_lines"],
            "merged": len(c["versions"]) > 1,
            "versions": [
                {
                    "run": v["run"],
                    "n": v["n"],
                    "sentence": v["sentence"],
                    "x_bullets": v["x_bullets"],
                    "y_lines": v["y_lines"],
                    "quote": v["quote"],
                    "checker_a": v["row"]["supported_a"],
                    "checker_b": v["row"]["supported_b"],
                    "true_both_checkers": v["row"]["strict"],
                    "x_items": v["row"]["x_items"],
                    "item_fates": v["row"]["item_fates"],
                    "names_silent_item": v["row"]["names_silent_item"],
                }
                for v in c["versions"]
            ],
            "word_overlap_with_kept": [
                round(jaccard(c["sentence"], v["sentence"]), 2) for v in c["versions"][1:]
            ],
            **extra,
        }
    lo, hi = MINUTES_PER_CARD
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Since last time: are the sentences true?</title>
<style>{CSS}{EXTRA_CSS}</style></head><body><main>
<div class="top"><b>Since last time: true?</b><span id="done"></span><div class="bar"><i id="fill"></i></div>
<button id="copy">Copy my answers</button><span id="copied" class="muted"></span></div>
<section class="panel"><h1>Are the "since last time" sentences true?</h1>{intro(len(claims))}</section>
{"".join(cards)}
<section class="panel"><p>That's all. Press <b>Copy my answers</b> at the top and paste the result back. Thank you.</p></section>
</main><script>{JS}</script></body></html>"""
    n_sent = {run: len(rows) for run, rows in runs.items()}
    key = {
        "sheet": "WRITER_CHECK",
        "seed": seed,
        "sources": {
            run: f"data/private/reader/sw_runs/{run}/ (rows.json, writer/*.jsonl); jobs data/private/reader/sw_jobs/{run}/writer/"
            for run in RUNS
        },
        "sentences_per_run": n_sent,
        "cards": len(claims),
        "merge_rule": (
            f"a {RUNS[1]} sentence merges into a {RUNS[0]} sentence on the same pair with the same "
            f"kind, at least one common notes bullet and content-word Jaccard >= {MERGE_JACCARD}; "
            f"the {RUNS[0]} wording is shown; cited lines are the union"
        ),
        "order": f"sorted by (pair, first run, position), then random.Random({seed}).shuffle",
        "minutes_per_card_estimate": list(MINUTES_PER_CARD),
        "minutes_total_estimate": [lo * len(claims), hi * len(claims)],
        "closest_passages": f"not_mentioned cards: up to {N_CLOSEST} lines that share a content word with the cited bullets, ranked by the summed rarity (log inverse line frequency) of the bullet words within +-{REACH} lines, > {2 * PAD_CLOSEST} lines apart, shown +-{PAD_CLOSEST} lines; British spellings folded",
        "cards_by_id": key_cards,
    }
    return page, key


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    page, key = build(a.seed)
    OUT_HTML.write_text(page)
    OUT_KEY.write_text(json.dumps(key, indent=1, sort_keys=True) + "\n")
    merged = sum(c["merged"] for c in key["cards_by_id"].values())
    print(
        f"{key['cards']} cards from {key['sentences_per_run']} sentences ({merged} merged), "
        f"about {key['minutes_total_estimate'][0]}-{key['minutes_total_estimate'][1]} min -> {OUT_HTML}"
    )


if __name__ == "__main__":
    main()
