# ruff: noqa: E501  (HTML templates)
"""Build the private item-fates human check sheet (data/private/ITEM_FATES_CHECK.html) and its key.

    uv run --no-sync python scripts/item_fates_check_build.py [--seed 42]

20 cards from the item-fates run ``fates_20261110`` (data/private/reader/fates_runs/.../rows.json,
256 items): every item the readers' final fate calls ``changed_silently`` (9), and a seeded
sample of the rest (:data:`N_GROUP`), drawn from pools sorted by pair and item id with
``random.Random(seed)`` and shuffled with the same generator. The page gives no hint of the
group and shows no reader's label, reason or ``what_changed``.

Each card pulls together, from local data only (no LLM, no network): what the two meetings are
(position in the series, roles, the AMI annotators' overview of the earlier meeting), the item
as the annotator wrote it, the passage of the earlier meeting the annotator linked to it, and
the later meeting's human transcript around every line any reader cited (10 turns either side,
cited turns highlighted, words shared with the item marked). When no reader cited a line (the
readers found nothing), the card shows instead the three places in the later meeting that share
the most words with the item, and says so. The whole later transcript is one click away on
every card. The key (pair, item, group, the readers' fates) is a separate JSON. Outputs are
private (data/private is gitignored).
"""

from __future__ import annotations

import argparse
import html
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from e3_check_build import CSS, content_stems, marked, overlap  # noqa: E402

from tenor.ami.corpus import AmiMeeting, load_meeting  # noqa: E402
from tenor.evals import came_back as K  # noqa: E402
from tenor.evals import item_fates as F  # noqa: E402

RUN = ROOT / "data/private/reader/fates_runs/fates_20261110"
OUT_HTML = ROOT / "data/private/ITEM_FATES_CHECK.html"
OUT_KEY = ROOT / "data/private/ITEM_FATES_CHECK_key.json"
PAD = 10  # later-meeting turns either side of a cited turn
N_GUESS = 3  # passages shown when no reader cited a line
# Cards per final fate; ``not_mentioned`` is drawn from items with needed_followup = yes.
N_GROUP = {
    "changed_silently": 9,
    "not_mentioned": 3,
    "changed_openly": 2,
    "reported": 3,
    "returned_unchanged": 2,
    "not_expected": 1,
}
ANSWERS = (
    ("reported", "Done or delivered"),
    ("returned_unchanged", "Came back, as agreed"),
    ("changed_openly", "Changed, and someone said so"),
    ("changed_silently", "Changed, nobody said so"),
    ("not_mentioned", "Never came back"),
    ("not_expected", "Nothing to come back to"),
    ("cant_tell", "Can't tell"),
)
POSITION = {"a": "first", "b": "second", "c": "third", "d": "fourth"}
_LINE = re.compile(r"^([* ] )?([XL])(\d{4}) \[(\d+:\d\d)\] (.*?): (.*)$")


def turns(m: AmiMeeting) -> list[dict[str, Any]]:
    """The meeting's transcript lines as the readers saw them: time, role, words."""
    out = []
    for i in range(len(m.dialogue_acts)):
        hit = _LINE.match(K.line(m, i))
        if not hit:
            raise SystemExit(f"{m.meeting_id}: unparsed line {i}")
        out.append({"t": hit[4], "who": hit[5], "text": hit[6]})
    return out


def windows(idx: list[int], pad: int, n: int) -> list[tuple[int, int]]:
    """Merged [lo, hi) windows around each index."""
    return [(lo, hi + 1) for lo, hi in K.spans(idx, pad, n)]


def turns_html(
    ts: list[dict[str, Any]], spans: list[tuple[int, int]], key: set[int], keys: set[str]
) -> str:
    out = []
    for k, (lo, hi) in enumerate(spans):
        if k:
            out.append('<div class="gap">&hellip; (some lines left out) &hellip;</div>')
        for i in range(lo, hi):
            t = ts[i]
            cls = "line key" if i in key else "line"
            out.append(
                f'<div class="{cls}"><span class="t">{t["t"]}</span><span class="who">'
                f"{html.escape(t['who'])}</span> {marked(t['text'], keys)}</div>"
            )
    return "".join(out)


def best_guesses(ts: list[dict[str, Any]], keys: set[str]) -> list[int]:
    """Up to :data:`N_GUESS` turns sharing the most words with the item (at least two; one when
    the item has at most two), at least 2 * PAD turns apart, in transcript order."""
    need = 1 if len(keys) <= 2 else 2
    scored = sorted(
        (-overlap(keys, t["text"]), i) for i, t in enumerate(ts) if overlap(keys, t["text"]) >= need
    )
    picks: list[int] = []
    for _s, i in scored:
        if all(abs(i - p) > 2 * PAD for p in picks):
            picks.append(i)
        if len(picks) == N_GUESS:
            break
    return sorted(picks)


def passage_html(m: AmiMeeting, item: K.Item, keys: set[str]) -> str:
    """The annotator-linked passage of the earlier meeting (linked lines highlighted)."""
    out = []
    for raw in K.passage(m, item).splitlines():
        hit = _LINE.match(raw)
        if not hit:
            out.append(f'<div class="gap">{html.escape(raw.strip())}</div>')
            continue
        cls = "line key" if (hit[1] or "").startswith("*") else "line"
        out.append(
            f'<div class="{cls}"><span class="t">{hit[4]}</span><span class="who">'
            f"{html.escape(hit[5])}</span> {marked(hit[6], keys)}</div>"
        )
    return "".join(out)


def background_html(x: AmiMeeting, y: AmiMeeting) -> str:
    px, py = POSITION[x.meeting_id[-1]], POSITION[y.meeting_id[-1]]
    abstract = " ".join(x.summary.get("abstract", ()))
    out = (
        f"<p>A team of four (Project Manager, Industrial Designer, User Interface Designer, "
        f"Marketing Expert) designs a new TV remote control over four meetings in one day; it is "
        f"an acted design task from the AMI corpus. The <b>earlier meeting</b> here is their "
        f"{px} meeting and the <b>later meeting</b> is their {py}, the next one. Between "
        f"meetings each person works alone.</p>"
    )
    if abstract:
        out += f"<p><b>What the earlier meeting covered (AMI annotators' overview):</b> {html.escape(abstract)}</p>"
    return out


EXTRA_CSS = """
.claim{font-size:21px;font-weight:600;margin:6px 0 2px}
.cols2{display:grid;grid-template-columns:1fr;gap:14px}@media(min-width:1100px){.cols2{grid-template-columns:2fr 3fr}}
.col{max-height:none}.note{color:var(--warn);margin:0 0 8px}
"""

JS = """
const KEY='itemfatescheck.v1';let A={};try{A=JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){A={}}
function save(){try{localStorage.setItem(KEY,JSON.stringify(A))}catch(e){}prog()}
function prog(){const n=document.querySelectorAll('.card').length,d=Object.values(A).filter(x=>x.answer).length;
document.getElementById('done').textContent=d+' of '+n+' answered';document.getElementById('fill').style.width=(100*d/n)+'%'}
function paint(id){const a=A[id]||{},c=document.getElementById(id);
c.querySelectorAll('button[data-a]').forEach(b=>b.classList.toggle('sel',b.dataset.a===a.answer));
c.querySelectorAll('button[data-n]').forEach(b=>b.classList.toggle('sel',b.dataset.n===a.expected));
if(a.note!==undefined)c.querySelector('textarea').value=a.note}
document.querySelectorAll('.card').forEach(c=>{const id=c.id;
c.querySelectorAll('button[data-a]').forEach(b=>b.onclick=()=>{const a=A[id]||{};a.answer=b.dataset.a;
if(!a.first_answer)a.first_answer=b.dataset.a;a.at=new Date().toISOString();A[id]=a;save();paint(id)});
c.querySelectorAll('button[data-n]').forEach(b=>b.onclick=()=>{const a=A[id]||{};a.expected=b.dataset.n;
if(!a.first_expected)a.first_expected=b.dataset.n;A[id]=a;save();paint(id)});
c.querySelector('textarea').oninput=e=>{const a=A[id]||{};a.note=e.target.value;A[id]=a;save()};paint(id)});
document.getElementById('copy').onclick=()=>{const t=JSON.stringify({sheet:'ITEM_FATES_CHECK',answers:A},null,1);
const done=()=>{document.getElementById('copied').textContent='Copied.'};
if(navigator.clipboard)navigator.clipboard.writeText(t).then(done,()=>{prompt('Copy this:',t)});else prompt('Copy this:',t)};
prog();
"""

INTRO = """
<p><b>What this checks.</b> A team agrees things in one meeting: decisions ("the case will be rubber")
and tasks ("Ana will cost the soups"). <b>What happens to each of them in the team's next meeting?</b>
Automated readers answered that for 256 items. Nobody has checked their answers. This sheet has 20 of
those items for you to judge yourself, without seeing what the readers said. About 3 to 5 minutes per card.</p>
<p><b>Each card shows</b> the two meetings, one item the team agreed in the earlier meeting (as a human
annotator wrote it down), the moment in the earlier meeting where it was agreed, and the parts of the
<b>later</b> meeting to look at. Then two questions.</p>
<p><b>Question 1: what happened to it in the later meeting?</b></p>
<ul>
<li><b>Done or delivered</b>: someone says it was done, or shows the result. Example: "I costed the soups,
both come in under a pound."</li>
<li><b>Came back, as agreed</b>: the later meeting talks about it or relies on it, and it is still what was
agreed. Example: "since the case is rubber, we can skip the grip."</li>
<li><b>Changed, and someone said so</b>: it was changed, dropped or not done, and somebody says so, gives a
reason or asks about it. Example: "I only got one quote, the second firm never rang back."</li>
<li><b>Changed, nobody said so</b>: it comes back different, or with a part missing, and nobody remarks on
it. Example: the agreed menu had four things; the printed menu shown has three and is approved without
anyone mentioning the fourth.</li>
<li><b>Never came back</b>: the later meeting does not touch it at all.</li>
<li><b>Nothing to come back to</b>: the item was only about the earlier meeting itself (e.g. "we will skip
the staffing point today").</li>
<li><b>Can't tell</b>: the card does not give you enough (please add a note on what is missing).</li>
</ul>
<p><b>Question 2: would a member of this team expect to hear about it next time?</b> Yes for a task
someone took away, or a decision the next step builds on. No for something finished in the earlier
meeting or clearly meant for later.</p>
<p><b>How to read the later meeting.</b> Highlighted rows are lines an automated reader pointed at as
relevant; <mark>marked</mark> words are words shared with the item. A card that says "no reader pointed
at any line" shows instead the places that share the most words with the item, which may be unrelated.
Before answering "Never came back" or "Changed, nobody said so", open <b>the whole later meeting</b> at
the bottom of the card and search it (Cmd-F): the missing part may be said elsewhere in other words.
Answers save in this browser; when done, press <b>Copy my answers</b> and paste them back.</p>
"""


def choose(
    rows: list[dict[str, Any]], seed: int
) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, int]]:
    """(group, row) for every card, shuffled, and the size of each pool."""
    order = sorted(rows, key=lambda r: (r["pair"], r["x_item"][0], int(r["x_item"][1:])))
    pools = {
        g: [
            r
            for r in order
            if r["fate"] == g and (g != "not_mentioned" or r["needed_followup"] == "yes")
        ]
        for g in N_GROUP
    }
    rng = random.Random(seed)
    chosen = [
        (g, r) for g, n in N_GROUP.items() for r in rng.sample(pools[g], min(n, len(pools[g])))
    ]
    rng.shuffle(chosen)
    return chosen, {g: len(p) for g, p in pools.items()}


def build(seed: int) -> tuple[str, dict[str, Any]]:
    rows = json.loads((RUN / "rows.json").read_text())["items"]
    chosen, pool_sizes = choose(rows, seed)
    cards, key_rows = [], {}
    for n, (group, r) in enumerate(chosen, 1):
        cid = f"C{n:02d}"
        pair = F.parse_pairs(r["pair"])[0]
        x, y = load_meeting(pair.x), load_meeting(pair.y)
        item = next(i for i in F.items(x) if i.item_id == r["x_item"])
        keys = content_stems(item.text)
        ts = turns(y)
        cited = sorted(
            {
                int(hit[1])
                for ids in r["y_lines"].values()
                for s in ids
                if (hit := K.LINE_ID.match(s)) and int(hit[1]) < len(ts)
            }
        )
        if cited:
            shown, note = cited, ""
        else:
            shown = best_guesses(ts, keys)
            note = (
                '<div class="note">No reader pointed at any line of the later meeting for this '
                "item. "
                + (
                    "The passages below are only the places that share the most words with the "
                    "item; they may be about something else."
                    if shown
                    else "No line of the later meeting shares enough words with the item to show "
                    "a passage."
                )
                + " Open the whole later meeting below before answering.</div>"
            )
        later = turns_html(ts, windows(shown, PAD, len(ts)), set(cited), keys)
        whole = turns_html(ts, [(0, len(ts))], set(cited), keys)
        kind = F.item_kind(item.item_id)
        cards.append(
            f"""<section class="panel card" id="{cid}">
<div class="muted">Card {n} of {len(chosen)}</div>
<div class="bg"><h4>About these two meetings</h4>{background_html(x, y)}</div>
<div class="q">The team agreed this in the earlier meeting ({"a decision" if kind == "decision" else "a task"}):</div>
<div class="claim">&ldquo;{marked(item.text, keys)}&rdquo;</div>
<div class="cols2">
<div class="col"><h4>Earlier meeting: where it was agreed (lines the annotator linked are highlighted)</h4>{passage_html(x, item, keys)}</div>
<div class="col"><h4>Later meeting: the parts to look at</h4>{note}{later}
<details><summary>Show the whole later meeting ({len(ts)} lines)</summary>{whole}</details></div>
</div>
<div class="q">1. What happened to it in the later meeting?</div>
<div class="btns">{"".join(f'<button data-a="{a}">{label}</button>' for a, label in ANSWERS)}</div>
<div class="q">2. Would a member of this team expect to hear about it next time?</div>
<div class="btns"><button data-n="yes">Yes</button><button data-n="no">No</button></div>
<textarea rows="2" placeholder="Optional note"></textarea>
</section>"""
        )
        key_rows[cid] = {
            "pair": r["pair"],
            "x_item": r["x_item"],
            "item": item.text,
            "item_kind": kind,
            "group": group,
            "fate": r["fate"],
            "needed_followup": r["needed_followup"],
            "readers": {k: r[f"fate_{k}"] for k in ("a", "b", "c")},
            "what_changed": r["what_changed"],
            "cited_lines": [K.line_id(i) for i in cited],
            "passages_are_word_overlap_guesses": not cited,
        }
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>What became of it? Follow-through check</title>
<style>{CSS}{EXTRA_CSS}</style></head><body><main>
<div class="top"><b>What became of it?</b><span id="done"></span><div class="bar"><i id="fill"></i></div>
<button id="copy">Copy my answers</button><span id="copied" class="muted"></span></div>
<section class="panel"><h1>What happens in the next meeting to things a team agreed?</h1>{INTRO}</section>
{"".join(cards)}
<section class="panel"><p>That's all. Press <b>Copy my answers</b> at the top and paste the result back. Thank you.</p></section>
</main><script>{JS}</script></body></html>"""
    key = {
        "sheet": "ITEM_FATES_CHECK",
        "seed": seed,
        "source": "data/private/reader/fates_runs/fates_20261110/rows.json (256 items, prompt item_fates.v1)",
        "groups": "final fate (a and b agree, else c); not_mentioned drawn from needed_followup = yes",
        "pool_sizes": pool_sizes,
        "cards_per_group": N_GROUP,
        "selection": f"random.Random({seed}).sample per group in order {list(N_GROUP)}, then .shuffle",
        "cards": key_rows,
    }
    return page, key


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    page, key = build(a.seed)
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(page)
    OUT_KEY.write_text(json.dumps(key, indent=1, sort_keys=True) + "\n")
    groups = Counter(c["group"] for c in key["cards"].values())
    guesses = sum(c["passages_are_word_overlap_guesses"] for c in key["cards"].values())
    print(
        f"{len(key['cards'])} cards {dict(groups)}, pools {key['pool_sizes']}, "
        f"{guesses} with word-overlap passages only -> {OUT_HTML}"
    )


if __name__ == "__main__":
    main()
