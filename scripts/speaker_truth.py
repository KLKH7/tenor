"""True speaker of every product transcript line, from AMI's human transcript (no LLM).

    uv run --no-sync python scripts/speaker_truth.py [--split dev|test|all] [--show N]

For each (meeting, product) of the split, aligns the product's transcript lines to AMI's timed,
speaker-attributed words (``tenor.evals.speaker_naming.align``: word anchors, an estimated
replay offset, local matches) and writes
``data/private/reader/speaker_truth/<meeting>__<product>.json``: per ``T`` line the true speaker
(AMI id, role, name where the series' introductions give one), confidence (the speaker's share
of the line's words), ``mixed`` when two speakers share the line, and the dialogue acts its
words belong to. Prints per unit: lines, confident (>= 0.7, not mixed), low, mixed, unaligned,
the replay offset and its spread. ``--show N`` prints N evenly spaced lines per unit with their
truth, for a spot check by reading (local only: transcript text).

Line ids are the reader packets' (``data/private/reader/packets``): the script stops if a
packet's transcript differs. Local data only; same input, same bytes. Outputs are private.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tenor.ami.corpus import load_meeting
from tenor.evals import bullet_cues as B
from tenor.evals import speaker_naming as S

PACKETS = S.READERS / "packets"


def check_packet(meeting_id: str, product: str, lines: list[S.ProdLine]) -> None:
    """Stop unless the packet's section 5 has the same lines, so ``T`` ids mean the same."""
    path = PACKETS / f"{S.unit_name(meeting_id, product)}.md"
    if not path.exists():
        raise SystemExit(f"{path}: no reader packet")
    unit = B.parse_packet(path.read_text(encoding="utf-8"), meeting_id, product, "")
    if [" ".join(x.text.split()) for x in unit.transcript] != [
        " ".join(x.text.split()) for x in lines
    ]:
        raise SystemExit(f"{meeting_id}:{product}: transcript differs from the reader packet")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--split", choices=("dev", "test", "all"), default="all")
    ap.add_argument("--out", type=Path, default=S.TRUTH_DIR)
    ap.add_argument("--show", type=int, default=0, help="Print N evenly spaced lines per unit.")
    a = ap.parse_args()
    splits = ("dev", "test") if a.split == "all" else (a.split,)
    meetings = [m for s in splits for m in S.split_meetings(s)]
    notes = S.load_notes(meetings)
    a.out.mkdir(parents=True, exist_ok=True)
    print("unit\tsplit\tlines\tconfident\tlow\tmixed\tunaligned\toffset_s\tiqr_s")
    for mid in meetings:
        meeting = load_meeting(mid)
        ref = S.ref_words(meeting)
        people = S.attendees_of(meeting)
        for product in S.PRODUCTS:
            if (mid, product) not in notes:
                print(f"{mid}:{product}\tnot recorded")
                continue
            lines = S.product_lines(notes[(mid, product)])
            check_packet(mid, product, lines)
            result = S.align(ref, lines)
            payload = S.truth_payload(mid, product, people, lines, result)
            path = a.out / f"{S.unit_name(mid, product)}.json"
            path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
            c = S.alignment_counts(result.lines)
            split = "dev" if mid in S.DEV_MEETINGS else "test"
            print(
                f"{mid}:{product}\t{split}\t{c['lines']}\t{c['confident']}\t{c['low']}\t"
                f"{c['mixed']}\t{c['unaligned']}\t{result.offset_s}\t{result.offset_iqr_s}"
            )
            if a.show:
                step = max(1, len(lines) // a.show)
                for ln, t in list(zip(lines, result.lines, strict=True))[::step]:
                    print(
                        f"   T{t.index:04d} {t.status} {t.speaker} {t.confidence} "
                        f"{dict(t.words)} | {ln.text[:110]}"
                    )


if __name__ == "__main__":
    main()
