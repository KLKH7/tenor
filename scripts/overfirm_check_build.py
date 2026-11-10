# ruff: noqa: E501  (HTML templates)
"""Build the private over-firm human check sheet (data/private/OVERFIRM_CHECK.html) and its key.

    uv run --no-sync python scripts/overfirm_check_build.py [--seed 42]

16 cards from the over-firm notes check (data/private/reader/overfirm_key.json and the three
agent-reader runs data/private/reader/runs/overfirm_{a,b,c}): 8 bullets the readers' consensus
calls strictly over-firm (bullet claims decided; meeting only discussed / not said), 4 it calls
leaning and 4 controls it calls decided. Groups are sampled from sorted pools with
random.Random(seed) and the cards shuffled with the same generator; the page gives no hint of the
group. Consensus follows data/private/OVERFIRM.md: majority per field, a three-way firmness tie
goes to the middle value.

Each card pulls together, from local data only (no LLM, no network): the meeting's background
(packet section 1 plus the AMI abstract), the notes bullet with its section, parent and
neighbouring bullets (firmness job notes), the AMI human transcript around every line the readers
quoted or cited in a reason (6 turns before to 12 after, cited turns highlighted, words shared
with the bullet marked) and, when the bullet's words come up elsewhere, the best earlier passage
and up to two later ones. After an answer the page reveals the three readers' verdicts and reasons and the generator's flag. The product is
hidden on the page; the key (product, meeting, bullet, group, consensus) is a separate JSON.
Outputs are private (data/private is gitignored).
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
from e3_check_build import BRANDS, CSS, content_stems, marked, mmss, overlap, secs  # noqa: E402

READER = ROOT / "data/private/reader"
KEY_IN = READER / "overfirm_key.json"
RUNS = ("a", "b", "c")
PACKETS = READER / "overfirm"
JOBS = READER / "firmness_jobs"
OUT_HTML = ROOT / "data/private/OVERFIRM_CHECK.html"
OUT_KEY = ROOT / "data/private/OVERFIRM_CHECK_key.json"
BEFORE, AFTER = 6, 12  # transcript turns around each quoted turn
LATER_BEFORE, LATER_AFTER = 3, 6  # around an earlier or later mention of the topic
NEIGHBOURS = 4  # bullets either side of the target, within its section
N_GROUP = {"overfirm": 8, "leaning": 4, "control": 4}
SCALE = ("not_said", "discussed", "leaning", "decided")
ANSWER_OF = {
    "decided": "Decided",
    "leaning": "Leaning",
    "discussed": "Only discussed",
    "not_said": "Not said",
    "not_a_claim": "Not a claim",
}
ANSWERS = ("Decided", "Leaning", "Only discussed", "Not said", "Can't tell")


# -- data -------------------------------------------------------------------------------------


def bid(raw: str) -> str:
    """``granola:B23`` -> ``B23`` (runs differ in whether they prefix the product)."""
    return raw.split(":")[-1]


def load_reads() -> dict[tuple[str, str, str], dict[str, dict[str, Any]]]:
    """(meeting, product, bullet) -> run letter -> that run's row."""
    out: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}
    for run in RUNS:
        for f in sorted((READER / "runs" / f"overfirm_{run}").glob("*.jsonl")):
            for line in f.read_text().splitlines():
                if line.strip():
                    x = json.loads(line)
                    out.setdefault((x["meeting"], x["product"], bid(x["bullet_id"])), {})[run] = x
    return out


def consensus(rows: dict[str, dict[str, Any]]) -> tuple[str, str]:
    """(claims decided, meeting firmness): majority per field; three distinct firmness labels go
    to the middle one on the scale (OVERFIRM.md)."""
    claims = Counter(r["bullet_claims_decided"] for r in rows.values()).most_common(1)[0][0]
    firm = Counter(r["meeting_firmness"] for r in rows.values())
    top, n = firm.most_common(1)[0]
    if n == 1 and len(firm) == 3:
        if any(f not in SCALE for f in firm):
            raise SystemExit(f"tie outside the scale: {dict(firm)}")
        top = sorted(firm, key=SCALE.index)[1]
    return claims, top


def packet(mid: str) -> tuple[str, list[dict[str, Any]]]:
    """(section 1 background text, transcript turns) from the reader packet."""
    text = (PACKETS / f"{mid}.md").read_text()
    bg = text.split("## 1. Meeting background", 1)[1].split("## 2.", 1)[0].strip()
    turns = [
        {"n": int(m[1]), "who": m[2], "t": m[3], "text": m[4]}
        for m in re.finditer(r"^\s*\[L(\d{4}) (.+?) (\d+:\d\d)\] ?(.*)$", text, re.M)
    ]
    return bg, turns


_NOTE = re.compile(r"^(B\d+) \[section: (.*?)\] (?:\(sub-point of (B\d+)\) )?(.*)$")


def notes(mid: str, product: str) -> list[dict[str, Any]]:
    """The product's notes as the firmness jobs saw them: id, section, parent, text."""
    text = (JOBS / f"{mid}__{product}.md").read_text()
    block = (
        text.split("## Notes (shared by every job)", 1)[1]
        .split("<notes>", 1)[1]
        .split("</notes>", 1)[0]
    )
    out = []
    for line in block.strip().splitlines():
        m = _NOTE.match(line.strip())
        if not m:
            raise SystemExit(f"{mid}:{product}: unparsed notes line {line!r}")
        text = re.sub(r"\\([~*_#`\[\]()])", r"\1", m[4])  # markdown escapes
        out.append({"id": m[1], "section": m[2], "parent": m[3], "text": text})
    return out


_LREF = re.compile(r"\bL(\d{4})(?:\s*[-\u2013]\s*L?(\d{4}))?")


def cited_turns(quote: str, turns: list[dict[str, Any]]) -> list[int]:
    """Indices of the transcript turns a reader quote cites (``L0402`` or ``L0402-L0409``); a
    line inside a turn maps to the turn that contains it."""
    starts = [t["n"] for t in turns]

    def at(n: int) -> int:
        i = max((k for k, s in enumerate(starts) if s <= n), default=0)
        return i

    out: set[int] = set()
    for a, b in _LREF.findall(quote):
        out.update(range(at(int(a)), at(int(b or a)) + 1))
    return sorted(out)


# -- card pieces ------------------------------------------------------------------------------


def windows(idx: list[int], before: int, after: int, n: int) -> list[tuple[int, int]]:
    """Merged [lo, hi) windows around each index."""
    out: list[tuple[int, int]] = []
    for i in sorted(idx):
        lo, hi = max(0, i - before), min(n, i + after + 1)
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def turns_html(
    turns: list[dict[str, Any]], spans: list[tuple[int, int]], key: set[int], keys: set[str]
) -> str:
    out = []
    for k, (lo, hi) in enumerate(spans):
        if k:
            out.append('<div class="gap">&hellip; (some lines left out) &hellip;</div>')
        for i in range(lo, hi):
            t = turns[i]
            cls = "line key" if i in key else "line"
            out.append(
                f'<div class="{cls}"><span class="t">{t["t"]}</span><span class="who">'
                f"{html.escape(t['who'])}</span> {marked(t['text'], keys)}</div>"
            )
    return "".join(out)


def returns(
    turns: list[dict[str, Any]], span: range, keys: set[str], skip: set[int], most: int
) -> list[int]:
    """Up to ``most`` turns in ``span`` that use the bullet's words (at least two shared words;
    one when the bullet has at most two), best first, at least 20 turns apart."""
    need = 1 if len(keys) <= 2 else 2
    if not need:
        return []
    scored = sorted(
        (-overlap(keys, turns[i]["text"]), i)
        for i in span
        if i not in skip and overlap(keys, turns[i]["text"]) >= need
    )
    picks: list[int] = []
    for _s, i in scored:
        if all(abs(i - p) > 20 for p in picks):
            picks.append(i)
        if len(picks) == most:
            break
    return sorted(picks)


def side_html(
    turns: list[dict[str, Any]],
    idx: list[int],
    keys: set[str],
    title: str,
    limit: tuple[int, int],
    cls: str,
) -> str:
    """A labelled block around ``idx`` (clipped to ``limit`` so it never repeats the main passage)."""
    if not idx:
        return ""
    spans = [
        (max(lo, limit[0]), min(hi, limit[1]))
        for lo, hi in windows(idx, LATER_BEFORE, LATER_AFTER, len(turns))
    ]
    return f'<div class="{cls}"><h4>{title}</h4>{turns_html(turns, spans, set(idx), keys)}</div>'


def notes_html(bullets: list[dict[str, Any]], target: str, keys: set[str], full: bool) -> str:
    """The target's section (target +-NEIGHBOURS bullets, plus its parent), or all notes."""
    by = {b["id"]: b for b in bullets}
    tb = by[target]
    if full:
        show = bullets
    else:
        sec = [b for b in bullets if b["section"] == tb["section"]]
        k = next(i for i, b in enumerate(sec) if b["id"] == target)
        ids = {b["id"] for b in sec[max(0, k - NEIGHBOURS) : k + NEIGHBOURS + 1]}
        if tb["parent"]:
            ids.add(tb["parent"])
        show = [b for b in bullets if b["id"] in ids]
    out, cur = [], None
    for b in show:
        if b["section"] != cur:
            if cur is not None:
                out.append("</ul>")
            cur = b["section"]
            out.append(f"<h5>{html.escape(cur)}</h5><ul>")
        cls = (
            ' class="target"' if b["id"] == target
            else ' class="parent"' if b["id"] == tb["parent"] else ""
        )  # fmt: skip
        sub = ' style="margin-left:1.4em"' if b["parent"] else ""
        tag = (
            ' <span class="tag">&larr; the bullet to check</span>' if b["id"] == target
            else ' <span class="tag">&larr; its parent bullet</span>' if b["id"] == tb["parent"] else ""
        )  # fmt: skip
        out.append(f"<li{cls}{sub}>{marked(b['text'], keys)}{tag}</li>")
    out.append("</ul>")
    return "".join(out)


def where_phrase(t: float, dur: float) -> str:
    f = t / dur if dur else 0
    part = (
        "the opening minutes" if f < 0.15 else "the first half" if f < 0.45
        else "the middle" if f < 0.6 else "the second half" if f < 0.85 else "the closing minutes"
    )  # fmt: skip
    return f"The readers' quotes start in {part} of the meeting, around {mmss(t)} of {mmss(dur)}"


ROLE_JOB = {
    "Project Manager": "runs the meeting and has the final say",
    "Industrial Designer": "the remote's insides: components, materials, energy",
    "User Interface Designer": "the buttons, screen and how it is used",
    "Marketing Expert": "what users want, trends, and evaluation",
}


def background_html(bg: str, abstract: str) -> str:
    """Packet section 1 as plain prose: where the meeting sits, who is there (role, spoken name,
    what the role does), length, and the AMI annotators' overview."""
    where = bg.splitlines()[0].strip()
    length = re.search(r"Length: ([\d:]+)", bg)
    roles = re.findall(r"^- AMI speaker (\w): (.+)$", bg, re.M)
    names = dict(re.findall(r"([A-Z][A-Za-z ]+?) = ([^;.]+)", bg.split("roster", 1)[-1]))
    people = []
    for spk, role in roles:
        if role == "no scenario role":
            people.append(f"Speaker {spk}")
            continue
        name = f" ({names[role]})" if role in names else ""
        job = f": {ROLE_JOB[role]}" if role in ROLE_JOB else ""
        people.append(f"<b>{html.escape(role)}</b>{html.escape(name)}{job}")
    out = f"<p>{html.escape(where)}" + (f" It runs {length[1]}." if length else "") + "</p>"
    out += f"<p><b>Who is there:</b> {'; '.join(people)}.</p>"
    if all(r == "no scenario role" for _, r in roles):
        out += "<p class='muted'>AMI gives no roles or names for this meeting; the transcript labels people Speaker A to D.</p>"
    if abstract:
        out += f"<p><b>What the meeting covers (AMI annotators' overview):</b> {html.escape(abstract)}</p>"
    return out


# -- page -------------------------------------------------------------------------------------

EXTRA_CSS = """
li.target{background:var(--mark);border-radius:4px;font-weight:600}li.parent{background:var(--em);border-radius:4px}
.tag{font-size:13px;color:var(--muted);font-weight:400}.claim{font-size:21px;font-weight:600;margin:6px 0 2px}
.cols2{display:grid;grid-template-columns:1fr;gap:14px}@media(min-width:1100px){.cols2{grid-template-columns:3fr 2fr}}
.later{margin-top:12px;border-top:2px dashed var(--line);padding-top:8px}
.col.tx{max-height:none}.col.nt{position:sticky;top:64px;align-self:start;max-height:calc(100vh - 80px)}
.earlier{margin-bottom:12px;border-bottom:2px dashed var(--line);padding-bottom:8px}
"""

JS = """
const KEY='overfirmcheck.v1';let A={};try{A=JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){A={}}
function save(){try{localStorage.setItem(KEY,JSON.stringify(A))}catch(e){}prog()}
function prog(){const n=document.querySelectorAll('.card').length,d=Object.values(A).filter(x=>x.answer).length;
document.getElementById('done').textContent=d+' of '+n+' answered';document.getElementById('fill').style.width=(100*d/n)+'%'}
function cmp(mine,v){if(mine==="Can't tell")return '';return mine===v?'<span class="agree">The readers\\' consensus matches your first answer.</span>':
'<span class="diff">The readers\\' consensus differs from your first answer.</span>'}
function paint(id){const a=A[id]||{},c=document.getElementById(id);
c.querySelectorAll('button[data-a]').forEach(b=>b.classList.toggle('sel',b.dataset.a===a.answer));
if(a.note!==undefined)c.querySelector('textarea').value=a.note;
const r=c.querySelector('.reveal');if(a.first_answer){r.classList.add('on');
r.querySelector('.cmp').innerHTML=cmp(a.first_answer,r.dataset.cons)}}
document.querySelectorAll('.card').forEach(c=>{const id=c.id;
c.querySelectorAll('button[data-a]').forEach(b=>b.onclick=()=>{const a=A[id]||{};a.answer=b.dataset.a;
if(!a.first_answer)a.first_answer=b.dataset.a;a.at=new Date().toISOString();A[id]=a;save();paint(id)});
c.querySelector('textarea').oninput=e=>{const a=A[id]||{};a.note=e.target.value;A[id]=a;save()};paint(id)});
document.getElementById('copy').onclick=()=>{const t=JSON.stringify({sheet:'OVERFIRM_CHECK',answers:A},null,1);
const done=()=>{document.getElementById('copied').textContent='Copied.'};
if(navigator.clipboard)navigator.clipboard.writeText(t).then(done,()=>{prompt('Copy this:',t)});else prompt('Copy this:',t)};
prog();
"""

INTRO = """
<p><b>What this checks.</b> Meeting note-taking apps write bullets like "Buttons: rubber, backlit".
Written that way, <b>the notes present this as settled. Was it actually settled in the meeting?</b>
Each card shows one such bullet (with the bullets around it), who was in the meeting and what it was
about, and the human-made transcript of the moment the bullet comes from. Decide how settled the meeting
really was about what the bullet says. About 1 to 2 minutes per card. Which app wrote the notes is hidden.</p>
<ul>
<li><b>Decided</b>: the group settled it, or the person in charge settled it and nobody reopened it.
Example: PM: "So rubber buttons, everyone happy?" All: "Yeah." It is not raised again.</li>
<li><b>Leaning (tentative, likely)</b>: the group clearly favoured it but held back from settling it.
Example: "I think rubber is probably the way to go, but let's see the costs next time." "Mm, yeah."</li>
<li><b>Only discussed</b>: it was raised, suggested or argued about, with no conclusion. Example: one person
suggests rubber buttons; another says plastic is cheaper; they move on.</li>
<li><b>Not said</b>: the meeting never said this, or said something different. Example: buttons never
came up, or they chose plastic.</li>
<li><b>Can't tell</b>: the card does not give you enough to decide (please add a note on what is missing).</li>
</ul>
<p><b>Why it matters.</b> Someone who missed the meeting acts on the notes. A bullet that reads as settled
when the meeting only talked about it can send them off building the wrong thing. Automated readers
estimated how often this happens (roughly 1 in 4 of the bullets an automated check flagged, about 1 in 8
of all "decided" bullets); your answers show whether those readers can be trusted. Some cards are bullets
the readers call over-firm, some they call settled; nothing on the card says which.</p>
<p><b>How to read a card.</b> The bullet to check is highlighted in the notes; its section heading and
parent bullet are shown with it. In the transcript, highlighted rows are the lines the automated readers
quoted or cited, and <mark>marked</mark> words are words shared with the bullet. When the bullet's words also come
up elsewhere, that passage is added, labelled "earlier in the meeting" or "later in the meeting". After you answer, the three readers' verdicts and the
automated check's flag appear; your <b>first</b> answer is what gets scored. Answers save in this browser;
when done, press <b>Copy my answers</b> and paste them back.</p>
"""


def build(seed: int) -> tuple[str, dict[str, Any]]:
    sys.path.insert(0, str(ROOT / "packages"))
    from tenor.ami.corpus import load_meeting

    items = {
        (x["meeting"], x["product"], x["bullet_id"]): x
        for x in json.loads(KEY_IN.read_text())["items"]
    }
    reads = load_reads()
    if set(reads) != set(items):
        raise SystemExit("reader rows and overfirm_key items differ")
    pools: dict[str, list[tuple[str, str, str]]] = {g: [] for g in N_GROUP}
    cons = {}
    for k in sorted(items, key=lambda k: (k[0], k[1], int(k[2][1:]))):
        claims, firm = consensus(reads[k])
        cons[k] = (claims, firm)
        if claims != "yes":
            continue
        if firm in ("discussed", "not_said"):
            pools["overfirm"].append(k)
        elif firm == "leaning":
            pools["leaning"].append(k)
        elif firm == "decided" and items[k]["group"] == "control":
            pools["control"].append(k)
    rng = random.Random(seed)
    chosen = [(g, k) for g, n in N_GROUP.items() for k in rng.sample(pools[g], n)]
    rng.shuffle(chosen)

    mids = sorted({k[0] for _, k in chosen})
    order = list(dict.fromkeys(k[0] for _, k in chosen))
    packs = {mid: packet(mid) for mid in mids}
    abstracts = {mid: " ".join(load_meeting(mid).summary.get("abstract", ())) for mid in mids}
    cards, key_rows, seen = [], {}, set()
    for n, (group, k) in enumerate(chosen, 1):
        cid = f"C{n:02d}"
        mid, prod, b = k
        bg_text, turns = packs[mid]
        bullets = notes(mid, prod)
        by = {x["id"]: x for x in bullets}
        tb = by[b]
        keys = content_stems(tb["text"])
        rr = reads[k]
        cited = sorted(
            {i for r in rr.values() for i in cited_turns(r["quote"] + " " + r["reason"], turns)}
        )
        if not cited:
            raise SystemExit(f"{cid} {k}: no reader quote maps to the transcript")
        spans = windows(cited, BEFORE, AFTER, len(turns))
        shown = {i for lo, hi in spans for i in range(lo, hi)}
        first, last = spans[0][0], spans[-1][1]
        back = returns(turns, range(last, len(turns)), keys, shown, 2)
        early = returns(turns, range(0, first), keys, shown, 1)
        later_html = side_html(
            turns,
            back,
            keys,
            "Later in the meeting (the bullet's words come back)",
            (last, len(turns)),
            "later",
        )
        earlier_html = side_html(
            turns,
            early,
            keys,
            "Earlier in the meeting (where the bullet's words first come up)",
            (0, first),
            "earlier",
        )
        dur = secs(turns[-1]["t"])
        t0 = secs(turns[cited[0]]["t"])

        main_head = "<h4>Around the readers' quotes</h4>" if early else ""
        bg_html = background_html(bg_text, abstracts[mid])
        num = order.index(mid) + 1
        first = mid not in seen
        seen.add(mid)
        bg_block = (
            f'<div class="bg"><h4>About this meeting (meeting {num})</h4>{bg_html}</div>'
            if first
            else f"<details class='bg'><summary>About this meeting (meeting {num}; shown on an earlier card)</summary>{bg_html}</details>"
        )
        parent_line = (
            f"<div class='ctx'>Under the section <b>{html.escape(tb['section'])}</b>, as a sub-point of "
            f"&ldquo;{marked(by[tb['parent']]['text'], keys)}&rdquo;.</div>"
            if tb["parent"]
            else f"<div class='ctx'>Under the section <b>{html.escape(tb['section'])}</b>.</div>"
        )
        c_claims, c_firm = cons[k]
        cons_ans = ANSWER_OF[c_firm]
        reader_li = "".join(
            f"<li><b>Reader {run.upper()}: {ANSWER_OF[r['meeting_firmness']]}</b> "
            f"(bullet claims decided: {r['bullet_claims_decided']}; confidence {r['confidence']}). "
            f"{html.escape(BRANDS.sub('[product]', r['reason']))}"
            f"<div class='muted'>Quote: {html.escape(BRANDS.sub('[product]', r['quote']))}</div></li>"
            for run, r in sorted(rr.items())
        )
        it = items[k]
        gen_q = "; ".join(
            f"{q['line']} &ldquo;{html.escape(q['text'])}&rdquo;" for q in it["quote"]
        )
        flagged = it["group"] == "suspected"
        gen = (
            f"The automated check (firmness run) labelled the notes <b>{it['notes_status']}</b> and the "
            f"meeting <b>{it['meeting_status']}</b>: "
            + ("<b>flagged</b> as firmer in the notes than in the meeting." if flagged
               else "<b>not flagged</b> (both decided; this card is a control).")
            + f"<div class='muted'>Its quotes (from the app's own transcript): {gen_q or '(none)'}</div>"
        )  # fmt: skip
        cards.append(
            f"""<section class="panel card" id="{cid}">
<div class="muted">Card {n} of {len(chosen)}</div>
{bg_block}
<div class="q">The notes present this as settled. Was it actually settled in the meeting?</div>
<div class="claim">&ldquo;{marked(tb["text"], keys)}&rdquo;</div>
{parent_line}
<div class="ctx">{where_phrase(t0, max(dur, t0))}.</div>
<div class="cols2">
<div class="col tx"><h4>The meeting (human transcript, around the lines the readers quoted or cited)</h4>{earlier_html}{main_head}{turns_html(turns, spans, set(cited), keys)}{later_html}</div>
<div class="col nt"><h4>The notes around this bullet</h4>{notes_html(bullets, b, keys, False)}
<details><summary>Show all the notes</summary>{notes_html(bullets, b, keys, True)}</details></div>
</div>
<div class="btns" style="margin-top:14px">{"".join(f'<button data-a="{a}">{a}</button>' for a in ANSWERS)}</div>
<textarea rows="2" placeholder="Optional note"></textarea>
<div class="reveal" data-cons="{cons_ans}">
<div><b>The readers' consensus: {cons_ans}</b> (bullet claims decided: {c_claims}). <span class="cmp"></span></div>
<ul>{reader_li}</ul>
<div>{gen}</div></div>
</section>"""
        )
        key_rows[cid] = {
            "meeting_id": mid,
            "product": prod,
            "bullet_id": b,
            "bullet": tb["text"],
            "group": group,
            "consensus": cons_ans,
            "consensus_claims_decided": c_claims,
            "readers": {run: ANSWER_OF[r["meeting_firmness"]] for run, r in sorted(rr.items())},
            "generator_group": it["group"],
            "generator_notes_status": it["notes_status"],
            "generator_meeting_status": it["meeting_status"],
            "later_passage": bool(back),
            "earlier_passage": bool(early),
        }
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Settled or not? Notes check</title>
<style>{CSS}{EXTRA_CSS}</style></head><body><main>
<div class="top"><b>Settled or not?</b><span id="done"></span><div class="bar"><i id="fill"></i></div>
<button id="copy">Copy my answers</button><span id="copied" class="muted"></span></div>
<section class="panel"><h1>Do the notes make things sound more settled than they were?</h1>{INTRO}</section>
{"".join(cards)}
<section class="panel"><p>That's all. Press <b>Copy my answers</b> at the top and paste the result back. Thank you.</p></section>
</main><script>{JS}</script></body></html>"""
    key = {
        "sheet": "OVERFIRM_CHECK",
        "seed": seed,
        "source": "data/private/reader/overfirm_key.json + runs/overfirm_{a,b,c}",
        "consensus": "majority per field; three-way firmness tie -> middle of not_said<discussed<leaning<decided",
        "groups": {
            "overfirm": "consensus claims decided = yes, firmness discussed/not_said (strict over-firm)",
            "leaning": "consensus claims decided = yes, firmness leaning",
            "control": "generator control group, consensus claims decided = yes, firmness decided",
        },
        "pool_sizes": {g: len(p) for g, p in pools.items()},
        "selection": f"random.Random({seed}).sample per group in order {list(N_GROUP)}, then .shuffle",
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
    groups = Counter(c["group"] for c in key["cards"].values())
    print(f"{len(key['cards'])} cards {dict(groups)}, pools {key['pool_sizes']} -> {OUT_HTML}")


if __name__ == "__main__":
    main()
