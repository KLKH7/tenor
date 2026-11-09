# ruff: noqa: E501  (HTML templates)
"""Build the private E3 human check sheet (data/private/E3_CHECK.html) and its key.

    uv run --no-sync python scripts/e3_check_build.py [--seed 42]

One card per item flagged for human review in data/private/E3_ADJUDICATION.jsonl (the
adjudicating agent's second reading of E3's judge verdicts), in a seeded order. Each card pulls
together, from local data only (no LLM, no network): the annotators' summary sentence, the AMI
human transcript passage linked to it (summlink; else the nearest passage by word overlap), the
product's own transcript at the same moment, and the product's notes. The product name is hidden
on the page. After an answer the page reveals the judge's verdict (majority of 3 passes) and the
agent's, each with its reasons. Cards where the agent thinks the annotators' summary names the
wrong owner ask a second question about the summary itself. The key (product, meeting, item,
both verdicts) is a separate JSON. Outputs are private (data/private is gitignored).
"""

from __future__ import annotations

import argparse
import html
import json
import random
import re
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "data/ami/results/e3"
POOLED = ROOT / "evals/reports/ami/notes_completeness.json"
OUT_HTML = ROOT / "data/private/E3_CHECK.html"
OUT_KEY = ROOT / "data/private/E3_CHECK_key.json"
ADJUDICATION = ROOT / "data/private/E3_ADJUDICATION.jsonl"
CONTEXT = 3  # utterances / lines either side
STRATA = ("full", "partial", "none")
ANSWER_OF = {"full": "Fully", "partial": "Partly", "none": "No"}
SECTION_WORD = {"decision": "decisions", "action": "action items", "problem": "problems"}
STAGES = {
    "a": "the first of four meetings (the kick-off: getting to know the task and each other)",
    "b": "the second of four meetings (functional design: what the remote must do)",
    "c": "the third of four meetings (conceptual design: choosing components and materials)",
    "d": "the fourth of four meetings (detailed design: the final prototype and evaluation)",
}
_STOP_WORDS = """a an the and or but if of to in on at by for with from as is are was were be been being
    it its this that these those there their they them he she his her we our you your i me my
    will would should could can may might must shall do does did done have has had not no yes
    so then than also about into over under up down out more most less very just only all any
    each some such what which who whom when where why how both other same new next one two
    three group team meeting discuss discussed discussion said says agreed decided decide
    going get got make made thing things think like well really want wanted need needs"""
STOP = set(_STOP_WORDS.split())
BRANDS = re.compile(r"\b(granola|wispr(?:\s+flow)?)\b", re.I)


# -- text helpers -----------------------------------------------------------------------------


def stem(word: str) -> str:
    w = word.lower().strip("'")
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


_TOKEN = re.compile(r"[A-Za-z][A-Za-z'\-]*|\d+")


def content_stems(text: str) -> set[str]:
    return {stem(t) for t in _TOKEN.findall(text) if len(t) >= 3 and t.lower() not in STOP}


def overlap(a: set[str], text: str) -> int:
    return len(a & content_stems(text))


def marked(text: str, keys: set[str]) -> str:
    """HTML-escaped ``text`` with words whose stem is in ``keys`` wrapped in <mark>."""
    text = BRANDS.sub("[product]", text)
    out, pos = [], 0
    for m in _TOKEN.finditer(text):
        out.append(html.escape(text[pos : m.start()]))
        tok = m.group(0)
        hit = len(tok) >= 3 and tok.lower() not in STOP and stem(tok) in keys
        out.append(f"<mark>{html.escape(tok)}</mark>" if hit else html.escape(tok))
        pos = m.end()
    out.append(html.escape(text[pos:]))
    return "".join(out)


def mmss(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60}:{s % 60:02d}"


def secs(start: str) -> float:
    if not start:
        return 0.0
    m, s = start.split(":")
    return int(m) * 60 + int(s)


def norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9']+", " ", text.lower().replace("\u2019", "'")).split())


# -- data -------------------------------------------------------------------------------------


def load_records() -> list[dict[str, Any]]:
    runs = json.loads(POOLED.read_text())["pooled"]["meta"]["runs"]
    out = []
    for stamp in runs:
        out += json.loads((RESULTS / f"{stamp}.json").read_text())["records"]
    return [r for r in out if r["split"] == "test"]


def majority(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """Per gold item: the majority verdict (E3's own rule) plus every pass's label, reason and
    quote, and the bullets any pass linked."""
    from tenor.evals import notes_completeness as N

    v = N.majority_verdicts(rec["passes"])
    out = []
    for i, g in enumerate(v.gold):
        ps = [p["gold_verdicts"][i] for p in rec["passes"]]
        out.append(
            {
                "id": g.id,
                "category": g.category,
                "text": rec["passes"][0]["gold"][i]["text"],
                "coverage": g.coverage,
                "heard": g.heard,
                "labels": [p["coverage"] for p in ps],
                "reasons": [p["reason"] for p in ps],
                "quotes": [p["quote"] for p in ps if p["quote"] and p["quote_found"]],
                "bullets_any": sorted(
                    {b for p in ps for b in p["bullets"]}, key=lambda b: int(b[1:])
                ),
            }
        )
    return out


def flagged() -> list[dict[str, Any]]:
    """The adjudication rows flagged for human review, in file order."""
    rows = [json.loads(x) for x in ADJUDICATION.read_text().splitlines() if x.strip()]
    return [r for r in rows if r.get("flag_for_review")]


_LREF = re.compile(r"\bL(\d{4})(?:\s*[-\u2013]\s*(\d{4}))?")


def cited_lines(text: str) -> list[int]:
    """AMI reference-line numbers cited as ``L0189`` or ``L0590-0592`` (= dialogue-act index)."""
    out: list[int] = []
    for a, b in _LREF.findall(text):
        out += list(range(int(a), int(b or a) + 1))
    return out


def series_names(mid: str) -> dict[str, str]:
    from tenor.ami.corpus import load_meeting
    from tenor.ami.facts import spoken_names

    ms = []
    for letter in "abcd" if mid[-1] in "abcd" else "":
        try:
            ms.append(load_meeting(mid[:-1] + letter))
        except (KeyError, OSError):
            continue
    if not ms:
        ms = [load_meeting(mid)]
    return spoken_names(ms)


def background(mid: str, m: Any, names: dict[str, str]) -> dict[str, Any]:
    from tenor.ami.corpus import ROLE_NAMES

    people = []
    for s in sorted(m.speakers, key=lambda s: s.agent):
        role = ROLE_NAMES.get(s.role or "", f"Speaker {s.agent}")
        name = names.get(s.participant)
        people.append(f"{role} ({name})" if name else role)
    if m.type == "scenario":
        where = (
            f"This is {STAGES.get(mid[-1], 'one meeting')} in which a team designs a new TV "
            "remote control (an acted design task used by the AMI corpus)."
        )
    else:
        where = "This is a real (not acted) working meeting from the AMI corpus."
    abstract = " ".join(m.summary.get("abstract", ())[:2])
    return {
        "people": people,
        "where": where,
        "abstract": abstract,
        "duration": mmss(m.duration_s),
    }


# -- one card ---------------------------------------------------------------------------------


def ami_passage(
    m: Any, gold: Any, keys: set[str], quotes: Sequence[str] = ()
) -> tuple[str, list[dict[str, Any]], float]:
    """(kind, rows, AMI time): the linked dialogue acts with context, else the nearest passage
    (most words shared with the summary line, and with the judge's verified quotes from the
    product transcript, which count double)."""
    from tenor.ami.corpus import ROLE_NAMES

    das = list(m.dialogue_acts)
    idx = {d.da_id: i for i, d in enumerate(das)}
    linked = sorted(idx[x] for x in gold.links if x in idx)
    kind = "linked"
    if not linked:
        kind = "nearest"
        best, at = -1, 0
        qkeys = set().union(*(content_stems(q) for q in quotes)) if quotes else set()
        for i in range(len(das)):
            window = " ".join(d.text for d in das[i : i + 3])
            score = overlap(keys, window) + 2 * overlap(qkeys, window)
            if score > best:
                best, at = score, i
        linked = [at, at + 1, at + 2][: len(das) - at]
    # collapse to at most two clusters of linked acts so the block stays short
    clusters: list[list[int]] = []
    for i in linked:
        if clusters and i - clusters[-1][-1] <= 2 * CONTEXT:
            clusters[-1].append(i)
        else:
            clusters.append([i])
    clusters.sort(key=len, reverse=True)
    clusters = sorted(clusters[:2], key=lambda c: c[0])
    roles = {s.agent: ROLE_NAMES.get(s.role or "", f"Speaker {s.agent}") for s in m.speakers}
    rows: list[dict[str, Any]] = []
    for c in clusters:
        lo, hi = max(0, c[0] - CONTEXT), min(len(das), c[-1] + CONTEXT + 1)
        if rows:
            rows.append({"gap": True})
        for i in range(lo, hi):
            d = das[i]
            rows.append(
                {"t": mmss(d.start), "who": roles.get(d.agent, d.agent), "text": d.text,
                 "key": i in linked}
            )  # fmt: skip
    return kind, rows, das[clusters[0][0]].start


def product_window(
    lines: Sequence[Any], shift: float | None, t_ami: float, dur: float, keys: set[str],
    quotes: Sequence[str], passage: str,
) -> tuple[str, list[dict[str, Any]], int]:  # fmt: skip
    """The product's transcript lines for this moment: the judge's verified quote if any (so
    the quoted line shows with context), else the best word-overlap match with the AMI passage
    near the same time (Granola: AMI time via the per-meeting shift; Wispr's times are synthetic,
    so position in the transcript stands in)."""
    if not lines:
        return "empty", [], -1
    centre, how = -1, "aligned"
    joined = [norm(x.text) for x in lines]
    for q in quotes:
        nq = norm(q)
        for i in range(len(lines)):
            if nq and nq in " ".join(joined[i : i + 4]):
                centre, how = i, "quote"
                break
        if centre >= 0:
            break
    if centre < 0:
        pkeys = content_stems(passage) | keys
        if shift is not None:
            near = [i for i, x in enumerate(lines) if abs(secs(x.start) + shift - t_ami) <= 90]
        else:
            frac = t_ami / dur if dur else 0
            c = int(frac * len(lines))
            span = max(8, len(lines) // 8)
            near = list(range(max(0, c - span), min(len(lines), c + span)))
        near = near or list(range(len(lines)))
        centre = max(
            near,
            key=lambda i: (
                overlap(pkeys, " ".join(x.text for x in lines[i : i + 2])),
                -abs(i - near[len(near) // 2]),
            ),
        )
    lo, hi = max(0, centre - CONTEXT), min(len(lines), centre + CONTEXT + 1)
    rows = []
    for i in range(lo, hi):
        x = lines[i]
        t = mmss(secs(x.start) + shift) if shift is not None else ""
        rows.append({"t": t, "who": x.speaker, "text": x.text, "key": i == centre})
    return how, rows, centre


def notes_block(
    bullets: Sequence[dict[str, Any]], judge_ids: Sequence[str], keys: set[str]
) -> tuple[list[str], set[str]]:
    """Sections to show and the bullets to emphasise: the judge's linked bullets (any pass)
    plus the three with most word overlap, mixed so emphasis does not reveal the judge."""
    scored = sorted(bullets, key=lambda b: (-overlap(keys, b["text"]), int(b["id"][1:])))
    close = [b["id"] for b in scored[:3] if overlap(keys, b["text"]) > 0]
    emph = set(judge_ids) | set(close)
    by_id = {b["id"]: b for b in bullets}
    sections = list(
        dict.fromkeys(
            by_id[i]["section"] for i in sorted(emph, key=lambda x: int(x[1:])) if i in by_id
        )
    )
    return sections, emph


def render_notes(
    bullets: Sequence[dict[str, Any]],
    sections: Sequence[str] | None,
    emph: set[str],
    keys: set[str],
) -> str:
    out, cur = [], None
    for b in bullets:
        if sections is not None and b["section"] not in sections:
            continue
        if b["section"] != cur:
            if cur is not None:
                out.append("</ul>")
            cur = b["section"]
            label = "(before the first heading)" if cur == "(top)" else cur
            out.append(f"<h5>{marked(label, keys)}</h5><ul>")
        cls = ' class="em"' if b["id"] in emph else ""
        sub = ' style="margin-left:1.2em"' if b.get("parent") else ""
        out.append(f"<li{cls}{sub}>{marked(b['text'], keys)}</li>")
    if cur is not None:
        out.append("</ul>")
    return "".join(out) or "<p class='muted'>(the notes are empty)</p>"


def rows_html(rows: Iterable[dict[str, Any]], keys: set[str]) -> str:
    out = []
    for r in rows:
        if r.get("gap"):
            out.append('<div class="gap">&hellip;</div>')
            continue
        cls = "line key" if r["key"] else "line"
        t = f'<span class="t">{r["t"]}</span>' if r["t"] else ""
        out.append(
            f'<div class="{cls}">{t}<span class="who">{html.escape(r["who"])}</span> '
            f"{marked(r['text'], keys)}</div>"
        )
    return "".join(out)


def where_phrase(t: float, dur: float) -> str:
    f = t / dur if dur else 0
    part = (
        "the opening minutes"
        if f < 0.15
        else "the first half"
        if f < 0.45
        else ("the middle" if f < 0.6 else "the second half" if f < 0.85 else "the closing minutes")
    )
    return f"Said in {part} of the meeting, around {mmss(t)} of {mmss(dur)}"


# -- page -------------------------------------------------------------------------------------

CSS = """
:root{--bg:#f7f6f2;--card:#fff;--ink:#22231f;--muted:#6b6c66;--line:#e3e1da;--mark:#fdeaa8;
--key:#eef3fb;--em:#f1f7ee;--acc:#2f5d8a;--ok:#2e7d4f;--warn:#9a5b13}
@media (prefers-color-scheme:dark){:root{--bg:#1b1c1a;--card:#242522;--ink:#ecebe6;--muted:#a3a39c;
--line:#3a3b37;--mark:#6b5a1c;--key:#23303f;--em:#253325;--acc:#8fb7e0;--ok:#7cc79a;--warn:#e0b070}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:18px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1500px;margin:0 auto;padding:16px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px 22px;margin:0 0 18px}
h1{font-size:26px;margin:4px 0 8px}h2{font-size:20px;margin:0 0 6px}h4{margin:0 0 6px;font-size:15px;
text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}h5{margin:10px 0 2px;font-size:16px}
.muted{color:var(--muted)}mark{background:var(--mark);color:inherit;border-radius:3px;padding:0 2px}
.top{position:sticky;top:0;z-index:5;background:var(--bg);padding:8px 0;display:flex;gap:12px;
align-items:center;flex-wrap:wrap;border-bottom:1px solid var(--line);margin-bottom:14px}
.bar{flex:1;min-width:160px;height:8px;background:var(--line);border-radius:4px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--acc)}
.item{font-size:21px;font-weight:600;margin:6px 0}.ctx{color:var(--muted);margin:0 0 10px}
.cols{display:grid;grid-template-columns:1fr;gap:14px}@media(min-width:1100px){.cols{grid-template-columns:1fr 1fr 1fr}}
.col{border:1px solid var(--line);border-radius:8px;padding:10px 12px;font-size:16px;max-height:560px;overflow:auto}
.line{padding:3px 4px;border-radius:4px}.line.key{background:var(--key)}.t{color:var(--muted);
font-variant-numeric:tabular-nums;margin-right:6px}.who{font-weight:600}.gap{color:var(--muted);text-align:center}
li.em{background:var(--em);border-radius:4px}ul{margin:2px 0 8px;padding-left:1.2em}
.q{font-size:20px;font-weight:600;margin:14px 0 8px}.btns{display:flex;gap:10px;flex-wrap:wrap}
button{font:inherit;font-size:17px;padding:9px 18px;border-radius:8px;border:1px solid var(--line);
background:var(--card);color:var(--ink);cursor:pointer}button.sel{background:var(--acc);color:#fff;border-color:var(--acc)}
textarea{width:100%;font:inherit;font-size:16px;margin-top:10px;padding:8px;border-radius:8px;
border:1px solid var(--line);background:var(--card);color:var(--ink)}
.reveal{margin-top:12px;padding:10px 14px;border-left:4px solid var(--acc);background:var(--key);border-radius:4px;display:none}
.reveal.on{display:block}.agree{color:var(--ok);font-weight:600}.diff{color:var(--warn);font-weight:600}
details summary{cursor:pointer;color:var(--acc)}.bg{border-left:4px solid var(--line);padding-left:12px;margin:0 0 12px}
"""

JS = """
const KEY='e3check.v2';let A={};try{A=JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){A={}}
function save(){try{localStorage.setItem(KEY,JSON.stringify(A))}catch(e){}prog()}
function prog(){const n=document.querySelectorAll('.card').length,d=Object.values(A).filter(x=>x.answer).length;
document.getElementById('done').textContent=d+' of '+n+' answered';document.getElementById('fill').style.width=(100*d/n)+'%'}
function cmp(mine,v,who){if(mine==="Can't tell")return '';return mine===v?'<span class="agree">'+who+' matches your first answer.</span>':
'<span class="diff">'+who+' differs from your first answer.</span>'}
function paint(id){const a=A[id]||{},c=document.getElementById(id);
c.querySelectorAll('button[data-a]').forEach(b=>b.classList.toggle('sel',b.dataset.a===a.answer));
c.querySelectorAll('button[data-g]').forEach(b=>b.classList.toggle('sel',b.dataset.g===a.gold_answer));
if(a.note!==undefined)c.querySelector('textarea').value=a.note;
const r=c.querySelector('.reveal');if(a.first_answer){r.classList.add('on');
r.querySelector('.cmpj').innerHTML=cmp(a.first_answer,r.dataset.judge,'The judge');
r.querySelector('.cmpa').innerHTML=cmp(a.first_answer,r.dataset.agent,'The second reader')}}
document.querySelectorAll('.card').forEach(c=>{const id=c.id;
c.querySelectorAll('button[data-a]').forEach(b=>b.onclick=()=>{const a=A[id]||{};a.answer=b.dataset.a;
if(!a.first_answer)a.first_answer=b.dataset.a;a.at=new Date().toISOString();A[id]=a;save();paint(id)});
c.querySelectorAll('button[data-g]').forEach(b=>b.onclick=()=>{const a=A[id]||{};a.gold_answer=b.dataset.g;
if(!a.first_gold_answer)a.first_gold_answer=b.dataset.g;A[id]=a;save();paint(id)});
c.querySelector('textarea').oninput=e=>{const a=A[id]||{};a.note=e.target.value;A[id]=a;save()};paint(id)});
document.getElementById('copy').onclick=()=>{const t=JSON.stringify({sheet:'E3_CHECK',answers:A},null,1);
const done=()=>{document.getElementById('copied').textContent='Copied.'};
if(navigator.clipboard)navigator.clipboard.writeText(t).then(done,()=>{prompt('Copy this:',t)});else prompt('Copy this:',t)};
prog();
"""

INTRO = """
<p><b>What this checks.</b> Two meeting note-taking apps listened to the same recorded meetings and
wrote notes. For each meeting, human annotators (from the AMI corpus) wrote a short summary of what was
decided, who was asked to do what, and which problems came up. An automated judge then read each app's
notes and said, for every line of the annotators' summary, whether the notes contain it.</p>
<p><b>Your job.</b> Each card shows one line of the annotators' summary, where it was said in the meeting
(the human transcript), what the app heard (its own transcript, same moment), and the app's notes. Decide:
<b>is this item in the notes?</b> About 1 minute per card. Which app wrote the notes is hidden.</p>
<ul>
<li><b>Fully</b>: the notes say the same thing, in any words. Example: summary "The remote will have a
rubber case"; notes "Decided: rubber casing". For a task, the notes must also say who does it.</li>
<li><b>Partly</b>: the notes have some of it, or say it more weakly. Example: notes "Discussed
materials for the case" (topic, not what was settled), or a task with no one named.</li>
<li><b>No</b>: nothing in the notes says it. Example: the notes never mention the case material.
Use "Show all the notes" before answering No.</li>
<li><b>Can't tell</b>: the card does not give you enough to decide.</li>
</ul>
<p><b>Why it matters.</b> These are the cards where the automated judge and a second automated reader
disagreed (or where the annotators' summary itself may be wrong), so your answers settle them and show
how often each one is right. After you answer a card, both verdicts appear with their reasons; your first
answer is what gets scored. A few cards ask a second question, about the annotators' summary itself.
Highlighted words are words the summary line shares with the other blocks. Answers save in this browser;
when done, press <b>Copy my answers</b> and paste them back.</p>
"""


def build(seed: int) -> tuple[str, dict[str, Any]]:
    sys.path.insert(0, str(ROOT / "packages"))
    from tenor.ami.corpus import ROLE_NAMES, load_meeting
    from tenor.ami.detector import estimate_shift
    from tenor.cli import FACTS_DBS, FACTS_OVERRIDES, _e3_units

    verdicts = {}
    for r in load_records():
        for g in majority(r):
            verdicts[(r["meeting_id"], r["product"], g["id"])] = {**g, "rec": r}
    chosen = []
    for row in flagged():
        v = verdicts[(row["meeting"], row["product"], row["gold_id"])]
        if v["coverage"] != row["judge_majority"]:
            raise SystemExit(
                f"{row['meeting']} {row['gold_id']}: judge verdict differs from the file"
            )
        chosen.append({**v, "meeting_id": row["meeting"], "product": row["product"], "adj": row})
    random.Random(seed).shuffle(chosen)
    mids = sorted({x["meeting_id"] for x in chosen})
    order = list(dict.fromkeys(x["meeting_id"] for x in chosen))  # meeting numbers by first card
    units, _ = _e3_units(FACTS_DBS["granola"], FACTS_DBS["wispr"], FACTS_OVERRIDES, mids)
    unit = {(u.meeting_id, u.product): u for u in units}
    meetings = {mid: load_meeting(mid) for mid in mids}
    bgs = {mid: background(mid, meetings[mid], series_names(mid)) for mid in mids}
    shifts: dict[tuple[str, str], float | None] = {}
    for key, u in unit.items():
        shift = None
        if key[1] == "granola" and u.transcript:
            try:
                shift, _n = estimate_shift(
                    meetings[key[0]].spoken(), [(secs(x.start), x.text) for x in u.transcript]
                )
            except ValueError:
                shift = None
        shifts[key] = shift

    cards, key_rows, seen_meeting = [], {}, set()
    for n, x in enumerate(chosen, 1):
        cid = f"C{n:02d}"
        mid, prod, rec, adj = x["meeting_id"], x["product"], x["rec"], x["adj"]
        m, u = meetings[mid], unit[(mid, prod)]
        bullets = rec["passes"][0]["bullets"]
        if [b.text for b in u.bullets] != [b["text"] for b in bullets]:
            raise SystemExit(f"{mid}:{prod}: notes changed since the E3 run")
        gold = next(s for s in m.sentences if " ".join(s.text.split()) == x["text"])
        keys = content_stems(x["text"])
        kind, arows, t_ami = ami_passage(m, gold, keys, x["quotes"])
        passage = " ".join(r["text"] for r in arows if not r.get("gap"))
        how, prows, _c = product_window(
            u.transcript, shifts[(mid, prod)], t_ami, m.duration_s, keys, x["quotes"], passage
        )
        agent_bullets = re.findall(r"\bB\d+\b", adj["evidence"] or "")
        known = {b["id"] for b in bullets}
        sections, emph = notes_block(
            bullets,
            [b for b in dict.fromkeys(x["bullets_any"] + agent_bullets) if b in known],
            keys,
        )
        bg = bgs[mid]
        num = order.index(mid) + 1
        bg_html = (
            f"<p><b>Who is there:</b> {html.escape(', '.join(bg['people']))}.</p>"
            f"<p>{html.escape(bg['where'])} The meeting runs {bg['duration']}.</p>"
            + (
                f"<p><b>The annotators' overview:</b> {html.escape(bg['abstract'])}</p>"
                if bg["abstract"]
                else ""
            )
        )
        first = mid not in seen_meeting
        seen_meeting.add(mid)
        bg_block = (
            f'<div class="bg"><h4>About this meeting (meeting {num})</h4>{bg_html}</div>'
            if first
            else f"<details class='bg'><summary>About this meeting (meeting {num}; seen on an earlier card)</summary>{bg_html}</details>"
        )
        ami_title = "Where it was said (human transcript" + (
            ", passage the annotators linked to this line)" if kind == "linked"
            else ", nearest passage: the annotators linked no passage)"
        )  # fmt: skip
        prod_title = "What the app heard (its own transcript" + (
            ", around the line the judge quoted)" if how == "quote" else ", same moment)"
        )
        if shifts[(mid, prod)] is None and prows:
            prod_title += " <span class='muted'>(times not available for this app; lines matched by wording)</span>"
        judge = ANSWER_OF[x["coverage"]]
        agent = ANSWER_OF[adj["your_notes_verdict"]]
        reasons = "".join(
            f"<li>Pass {i + 1}: {ANSWER_OF[lab]} &mdash; {html.escape(BRANDS.sub('[product]', rs))}</li>"
            for i, (lab, rs) in enumerate(zip(x["labels"], x["reasons"], strict=True))
        )
        agent_reason = BRANDS.sub("[product]", adj["review_comment"] or adj["note"] or "")
        agent_ev = BRANDS.sub("[product]", adj["evidence"] or "")
        das = list(m.dialogue_acts)
        roles = {s.agent: ROLE_NAMES.get(s.role or "", f"Speaker {s.agent}") for s in m.speakers}
        cited = cited_lines(
            " ".join([adj["review_comment"] or "", adj["note"] or "", adj["evidence"] or ""])
        )
        gold_error = (adj.get("disagreement_type") or "").startswith("gold error")
        if gold_error and not cited:  # "Same as A2": the line cited on the same meeting's card
            cited = next(
                (cited_lines(r["review_comment"] or "") for r in flagged()
                 if r["meeting"] == mid and cited_lines(r["review_comment"] or "")), []
            )  # fmt: skip
        turn = set()  # a cited line is a merged turn: the act and the same speaker's next acts
        for i in cited:
            j = i
            while j < len(das) and j - i < 8 and das[j].agent == das[i].agent:
                turn.add(j)
                j += 1
        cited = sorted(turn)
        show = sorted({j for i in cited for j in range(i - 1, i + 2) if 0 <= j < len(das)})
        cited_html = rows_html(
            [{"t": mmss(das[j].start), "who": roles.get(das[j].agent, das[j].agent),
              "text": das[j].text, "key": j in cited} for j in show], keys
        )  # fmt: skip
        gold_q = extra = ""
        if gold_error:
            gold_q = f"""<div class="q" style="margin-top:22px">Second question: is the annotators' summary itself correct about who got this task?</div>
<div class="col" style="max-height:none"><h4>Human transcript: the lines about who takes this task</h4>{cited_html}</div>
<div class="btns" style="margin-top:10px"><button data-g="Yes">Yes</button><button data-g="No">No</button><button data-g="Can't tell">Can't tell</button></div>"""
        elif show:
            extra = f"""<div class="col" style="max-height:none;margin-top:14px"><h4>Also from the human transcript (helps tell who is who)</h4>{cited_html}</div>"""
        cards.append(
            f"""<section class="panel card" id="{cid}">
<div class="muted">Card {n} of {len(chosen)}</div>
{bg_block}
<div class="q">Question: is this item in the notes?</div>
<div class="ctx">{where_phrase(t_ami, max(m.duration_s, t_ami))}. The annotators listed it under {SECTION_WORD[x["category"]]}.</div>
<div class="item">&ldquo;{marked(x["text"], keys)}&rdquo;</div>
<div class="cols">
<div class="col"><h4>{ami_title}</h4>{rows_html(arows, keys)}</div>
<div class="col"><h4>{prod_title}</h4>{rows_html(prows, keys) or "<p class='muted'>(no transcript)</p>"}</div>
<div class="col"><h4>What the app wrote (notes: the most relevant sections)</h4>{render_notes(bullets, sections, emph, keys)}
<details><summary>Show all the notes</summary>{render_notes(bullets, None, emph, keys)}</details></div>
</div>
{extra}
<div class="btns" style="margin-top:14px">
<button data-a="Fully">Fully</button><button data-a="Partly">Partly</button><button data-a="No">No</button><button data-a="Can't tell">Can't tell</button>
</div>
{gold_q}
<textarea rows="2" placeholder="Optional note"></textarea>
<div class="reveal" data-judge="{judge}" data-agent="{agent}">
<div><b>The judge said: {judge}</b> (passes: {", ".join(ANSWER_OF[lab] for lab in x["labels"])}). <span class="cmpj"></span></div>
<ul>{reasons}</ul>
<div><b>The second reader said: {agent}.</b> <span class="cmpa"></span></div>
<ul><li>{html.escape(agent_reason)}</li><li class="muted">Evidence: {html.escape(agent_ev)}</li></ul></div>
</section>"""
        )
        key_rows[cid] = {
            "meeting_id": mid,
            "product": prod,
            "item_id": x["id"],
            "category": x["category"],
            "judge": judge,
            "judge_labels": x["labels"],
            "agent": agent,
            "disagreement_type": adj.get("disagreement_type"),
            "gold_question": bool(gold_q),
            "ami_passage": kind,
            "product_window": how,
        }
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Notes Check</title>
<style>{CSS}</style></head><body><main>
<div class="top"><b>Notes check</b><span id="done"></span><div class="bar"><i id="fill"></i></div>
<button id="copy">Copy my answers</button><span id="copied" class="muted"></span></div>
<section class="panel"><h1>Do the notes contain what the meeting decided?</h1>{INTRO}</section>
{"".join(cards)}
<section class="panel"><p>That's all. Press <b>Copy my answers</b> at the top and paste the result back. Thank you.</p></section>
</main><script>{JS}</script></body></html>"""
    key = {
        "sheet": "E3_CHECK",
        "seed": seed,
        "source_runs": json.loads(POOLED.read_text())["pooled"]["meta"]["runs"],
        "selection": "items with flag_for_review in data/private/E3_ADJUDICATION.jsonl",
        "judge_verdict": "majority over 3 passes (notes_completeness.majority_verdicts)",
        "agent_verdict": "your_notes_verdict in E3_ADJUDICATION.jsonl",
        "cards": key_rows,
    }
    return page, key


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    page, key = build(a.seed)
    if BRANDS.search(re.sub(r"<[^>]+>", " ", page)):
        raise SystemExit("a product name leaked into the page")
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(page)
    OUT_KEY.write_text(json.dumps(key, indent=1, sort_keys=True) + "\n")
    cards = key["cards"].values()
    prods = {p: sum(c["product"] == p for c in cards) for p in ("granola", "wispr")}
    gq = sum(c["gold_question"] for c in cards)
    print(f"{len(key['cards'])} cards {prods}, {gq} with the summary question -> {OUT_HTML}")


if __name__ == "__main__":
    main()
