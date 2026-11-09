"""Write one markdown packet per (AMI meeting, product) for an E3 "second reader" agent.

    uv run --no-sync python scripts/reader_packets.py --meetings IS1008a \
        [--gold original|errata|errata2] [--out data/private/reader/packets/] [--e3-results DIR]

A packet holds everything the reader (prompts/notes_reader.v1.md) needs to decide, per gold
item, whether the product's notes cover it and whether the product's transcript has it:

1. meeting background (AMI speakers and roles, the roster the E3 judge saw, meeting stage);
2. the gold items (id, category, text): AMI 1.6.2 as published by default, or with
   ``data/ami_questions/gold_errata.v1.json`` (``--gold errata``, gold ami-1.6.2+errata.v1)
   or ``gold_errata.v2.json`` (``--gold errata2``, gold ami-1.6.2+errata.v2);
3. per gold item, the AMI reference-transcript passage its summary links point to: the turns
   holding a linked dialogue act (marked ``>>``), 3 turns before and 10 after. A meeting whose
   items have no links (IB4003) says so and gets the full AMI reference transcript once;
4. the product's notes split into bullets B1.. exactly as the E3 judge saw them
   (``notes_completeness.split_notes`` / ``Bullet.render``);
5. the product's full transcript, one numbered line per utterance (``T0000``..), so the reader
   can grep it.

AMI line ids ``L0189`` are the E3 judge's reference-transcript ids (dialogue-act index of the
first act in a merged same-speaker turn; the errata file uses the same ids). No judge or reader
verdicts go into a packet. Local data only: no LLM, no network. Same input, same bytes.
Outputs are private (data/private is gitignored).
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data/private/reader/packets"
PACKET_VERSION = "reader_packet.v1"
BEFORE = 3  # reference turns shown before a linked turn
AFTER = 10  # turns shown after: enough to reach the answer to a question
PRODUCT_LABEL = {"granola": "Granola", "wispr": "Wispr Flow"}
STAGES = {
    "a": "the first of four meetings (kick-off: getting to know the task and each other)",
    "b": "the second of four meetings (functional design: what the remote must do)",
    "c": "the third of four meetings (conceptual design: choosing components and materials)",
    "d": "the fourth of four meetings (detailed design: the final prototype and evaluation)",
}


# -- the packet's inputs (plain data, so rendering is testable without AMI) ------------------


@dataclass(frozen=True)
class Turn:
    """One line of a transcript as shown in a packet."""

    index: int  # AMI: dialogue-act index of the turn's first act; product: utterance index
    speaker: str
    start: str  # mm:ss
    text: str


@dataclass(frozen=True)
class PacketGold:
    id: str  # D1, A1, P1
    category: str  # decision / action / problem
    text: str
    links: tuple[int, ...] = ()  # dialogue-act indexes AMI's annotators linked to it

    @property
    def empty(self) -> bool:
        """AMI gives no content for the item (ES2006c P1 is ``*NA*.``)."""
        return self.text.strip("*. ").upper() in {"NA", "N/A", ""}


@dataclass(frozen=True)
class PacketBullet:
    id: str
    rendered: str  # Bullet.render(): what the E3 judge saw


@dataclass
class PacketInput:
    meeting_id: str
    product: str
    gold_version: str
    background: list[str]  # lines of prose / list items
    gold: list[PacketGold]
    reference: list[Turn]  # AMI reference transcript, merged turns
    bullets: list[PacketBullet]
    transcript: list[Turn]  # the product's own transcript
    notes: list[str] = field(default_factory=list)  # data remarks shown to the reader


# -- passages ---------------------------------------------------------------------------------


def turn_of(reference: Sequence[Turn], da_index: int) -> int:
    """Position in ``reference`` of the merged turn holding dialogue act ``da_index``."""
    starts = [t.index for t in reference]
    return max(0, bisect.bisect_right(starts, da_index) - 1)


def windows(
    positions: Iterable[int], n: int, before: int = BEFORE, after: int = AFTER
) -> list[tuple[int, int]]:
    """Inclusive ``(lo, hi)`` ranges of turn positions: each linked position with ``before``
    turns before and ``after`` after, clipped to ``[0, n)``, overlapping or adjacent ranges
    merged, in order."""
    out: list[tuple[int, int]] = []
    for p in sorted(set(positions)):
        lo, hi = max(0, p - before), min(n - 1, p + after)
        if out and lo <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def render_turn(t: Turn, prefix: str, mark: bool = False) -> str:
    return f"{'>> ' if mark else '   '}[{prefix}{t.index:04d} {t.speaker} {t.start}] {t.text}"


def passage(reference: Sequence[Turn], links: Sequence[int]) -> list[str]:
    """The linked turns of one gold item with context, ``...`` between separate windows."""
    linked = {turn_of(reference, i) for i in links}
    out: list[str] = []
    for lo, hi in windows(linked, len(reference)):
        if out:
            out.append("   ...")
        out += [render_turn(reference[p], "L", p in linked) for p in range(lo, hi + 1)]
    return out


# -- rendering --------------------------------------------------------------------------------


def render_packet(p: PacketInput) -> str:
    """The packet's markdown. Deterministic: depends only on ``p``."""
    label = PRODUCT_LABEL.get(p.product, p.product)
    any_links = any(g.links for g in p.gold)
    out = [
        f"# Reader packet: {p.meeting_id} / {label}",
        "",
        f"Packet format {PACKET_VERSION}. Gold version {p.gold_version}. Meeting "
        f"{p.meeting_id}, product `{p.product}`. {len(p.gold)} gold items, {len(p.bullets)} "
        f"note bullets, {len(p.transcript)} product transcript lines.",
        "",
        "Line ids: `B12` = a bullet of the product's notes (section 4); `T0042` = a line of the "
        "product's own transcript (section 5); `L0189` = a turn of AMI's human reference "
        "transcript (section 3). `>>` marks a turn that holds a dialogue act AMI's annotators "
        "linked to the gold item.",
        "",
        "## 1. Meeting background",
        "",
        *p.background,
        "",
    ]
    if p.notes:
        out += ["Data remarks:", "", *(f"- {n}" for n in p.notes), ""]
    out += ["## 2. Gold items", ""]
    for cat in ("decision", "action", "problem"):
        rows = [g for g in p.gold if g.category == cat]
        out.append(f"{cat.capitalize()}s:")
        out += [f"- {g.id} ({g.category}): {g.text}" for g in rows] or ["- (none)"]
        out.append("")
    out += ["## 3. Where each gold item was said (AMI human reference transcript)", ""]
    for g in p.gold:
        out += [f"### {g.id} ({g.category}): {g.text}", ""]
        if g.links:
            out += ["```", *passage(p.reference, g.links), "```", ""]
        elif g.empty:
            out += ["AMI gives no content for this item, so there is no passage to find.", ""]
        else:
            out += [
                "AMI's annotators linked no passage to this item. Find it yourself in the full "
                "AMI reference transcript (section 3b).",
                "",
            ]
    if not all(g.links or g.empty for g in p.gold):
        why = (
            "This meeting has no summary links in AMI"
            if not any_links
            else "Some gold items have no summary links"
        )
        out += [
            "### 3b. Full AMI reference transcript",
            "",
            f"{why}, so the whole human transcript follows once.",
            "",
            "```",
            *(render_turn(t, "L") for t in p.reference),
            "```",
            "",
        ]
    out += [
        "## 4. The product's notes, split into bullets",
        "",
        "Exactly the bullets the E3 judge saw: `id [section: heading] (sub-point of parent) "
        "text`. Read a sub-point together with its parent.",
        "",
        "```",
        *([b.rendered for b in p.bullets] or ["(the notes are empty)"]),
        "```",
        "",
        "## 5. The product's own transcript",
        "",
        "Machine transcript, one line per utterance. Speaker labels are the product's own "
        "(Granola: `Them` = system audio; Wispr Flow: its own labels) and may be generic or "
        "wrong; times are as the product gives them.",
        "",
        "```",
        *([render_turn(t, "T").lstrip() for t in p.transcript] or ["(no transcript)"]),
        "```",
        "",
    ]
    return "\n".join(out)


def packet_name(meeting_id: str, product: str) -> str:
    return f"{meeting_id}__{product}.md"


# -- loading from local AMI data and the synced product DBs -----------------------------------


def gold_with_links(
    sentences: Sequence[Any], gold: Sequence[Any], da_index: Mapping[str, int]
) -> list[PacketGold]:
    """E3's gold items (``notes_completeness.gold_items`` output, errata already applied) with
    each item's AMI summary links as dialogue-act indexes (``da_index``: id -> index), matched
    by E3's numbering (per category, in summary order, before errata, so a dropped item does
    not renumber the others). A link to an unknown dialogue act raises ``SystemExit``."""
    from tenor.evals.notes_completeness import SECTIONS

    links: dict[str, tuple[int, ...]] = {}
    count: dict[str, int] = {}
    for s in sentences:
        if s.section not in SECTIONS:
            continue
        cat, prefix = SECTIONS[s.section]
        count[cat] = count.get(cat, 0) + 1
        if bad := [x for x in s.links if x not in da_index]:
            raise SystemExit(f"{s.sentence_id}: links to unknown dialogue acts {bad}")
        links[f"{prefix}{count[cat]}"] = tuple(sorted(da_index[x] for x in s.links))
    return [PacketGold(g.id, g.category, g.text, links[g.id]) for g in gold]


def background(meeting: Any, roster: str) -> list[str]:
    from tenor.ami.corpus import ROLE_NAMES

    mid = meeting.meeting_id
    if meeting.type == "scenario":
        where = (
            f"This is {STAGES.get(mid[-1], 'one meeting')} in which a team of four designs a new "
            "TV remote control (an acted design task from the AMI corpus)."
        )
    else:
        where = "This is a real (not acted) working meeting from the AMI corpus."
    dur = int(meeting.duration_s)
    people = [
        f"- AMI speaker {s.agent}: {ROLE_NAMES.get(s.role or '', 'no scenario role')}"
        for s in sorted(meeting.speakers, key=lambda s: s.agent)
    ]
    out = [where, f"Length: {dur // 60}:{dur % 60:02d}.", "", "Participants:", *people]
    out += ["", roster] if roster else ["", "Roster: none (AMI has no roles or introductions)."]
    return out


def load_inputs(meetings: Sequence[str], errata: Any = None) -> list[PacketInput]:
    """Packets' inputs for ``meetings`` x both products, built exactly as E3 builds its units
    (``tenor.cli._e3_units``: same DBs, overrides, notes split, product transcript)."""
    sys.path.insert(0, str(ROOT / "packages"))
    from tenor.ami.corpus import load_meeting
    from tenor.cli import FACTS_DBS, FACTS_OVERRIDES, _e3_units
    from tenor.evals import notes_completeness as N

    units, skipped = _e3_units(
        FACTS_DBS["granola"], FACTS_DBS["wispr"], FACTS_OVERRIDES, list(meetings), errata
    )
    if skipped:
        raise SystemExit(f"skipped by E3's unit builder: {skipped}")
    out = []
    for u in units:
        m = load_meeting(u.meeting_id)
        gold = gold_with_links(
            m.sentences, u.gold, {d.da_id: i for i, d in enumerate(m.dialogue_acts)}
        )
        remarks = [
            f"{g.id}'s text is `{g.text}`: AMI gives no content for it." for g in gold if g.empty
        ]
        out.append(
            PacketInput(
                meeting_id=u.meeting_id,
                product=u.product,
                gold_version=N.gold_version(errata),
                background=background(m, u.roster),
                gold=gold,
                reference=[Turn(x.index, x.speaker, x.start, x.text) for x in u.reference],
                bullets=[PacketBullet(b.id, b.render()) for b in u.bullets],
                transcript=[Turn(x.index, x.speaker, x.start, x.text) for x in u.transcript],
                notes=remarks,
            )
        )
    return out


def verify_bullets(inputs: Sequence[PacketInput], results: Path, runs: Sequence[str]) -> int:
    """Check each packet's bullets equal the bullets in the E3 result files ``runs`` (every
    pass). Returns the number of units checked; raises SystemExit on a mismatch."""
    seen = 0
    want = {(p.meeting_id, p.product): [b.rendered for b in p.bullets] for p in inputs}
    for stamp in runs:
        for rec in json.loads((results / f"{stamp}.json").read_text())["records"]:
            key = (rec["meeting_id"], rec["product"])
            if key not in want:
                continue
            for ps in rec["passes"]:
                got = [_render_bullet(b) for b in ps["bullets"]]
                if got != want[key]:
                    raise SystemExit(f"{key}: bullets differ from E3 run {stamp}")
            seen += 1
    return seen


def _render_bullet(b: Mapping[str, Any]) -> str:
    sub = f" (sub-point of {b['parent']})" if b.get("parent") else ""
    return f"{b['id']} [section: {b['section']}]{sub} {b['text']}"


# -- main -------------------------------------------------------------------------------------


def write_packets(inputs: Sequence[PacketInput], out_dir: Path) -> list[tuple[str, int, str]]:
    """Write every packet; returns (file name, chars, sha256[:12]) per packet, in order."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in sorted(inputs, key=lambda p: (p.meeting_id, p.product)):
        text = render_packet(p)
        name = packet_name(p.meeting_id, p.product)
        (out_dir / name).write_text(text, encoding="utf-8")
        rows.append((name, len(text), hashlib.sha256(text.encode()).hexdigest()[:12]))
    return rows


ERRATA_FILES = {"errata": "ERRATA_V1", "errata2": "ERRATA_V2"}  # --gold -> tenor.ami.errata name


def load_gold_errata(gold: str) -> Any:
    """The errata for ``--gold`` (``None`` for ``original``): v1 for ``errata``, v2 for
    ``errata2``."""
    if gold == "original":
        return None
    sys.path.insert(0, str(ROOT / "packages"))
    from tenor.ami import errata as E

    return E.load_errata(ROOT / getattr(E, ERRATA_FILES[gold]))


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--meetings", required=True, help="Comma-separated AMI meeting ids.")
    ap.add_argument("--gold", choices=("original", *ERRATA_FILES), default="original")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument(
        "--e3-results",
        type=Path,
        default=None,
        help="Directory of E3 result JSONs: check bullets against the v4 TEST/DEV runs.",
    )
    ap.add_argument(
        "--runs",
        default="20261109T014826Z,20261109T063610Z,20261109T072942Z",
        help="E3 run stamps to check bullets against (with --e3-results).",
    )
    a = ap.parse_args(argv)
    errata = load_gold_errata(a.gold)
    meetings = sorted({m.strip() for m in a.meetings.split(",") if m.strip()})
    inputs = load_inputs(meetings, errata)
    if a.e3_results is not None:
        n = verify_bullets(inputs, a.e3_results, [r for r in a.runs.split(",") if r])
        print(f"bullets identical to the E3 runs for {n} unit records")
    for name, chars, sha in write_packets(inputs, a.out):
        print(f"{name}\t{chars:,} chars\tsha256 {sha}")


if __name__ == "__main__":
    main()
