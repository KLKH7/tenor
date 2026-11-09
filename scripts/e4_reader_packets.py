"""Write one markdown packet per (TEST meeting, product) for an E4 reader agent, plus the key.

    uv run --no-sync python scripts/e4_reader_packets.py \
        --candidates data/ami/results/e4/<run_id>.candidates.json \
        [--gold original|errata] [--out data/private/reader/e4_packets/] [--key PATH]

A packet is the E3 reader packet (``scripts/reader_packets.py``: background and roster, gold
items with their AMI passages, the notes as bullets ``B1``.., the product transcript with
``T`` line ids) plus section 6: the unit's candidate items ``K1``.. from every E4 condition,
exact duplicates merged, in a seeded shuffle, with no condition, source or verdict
(``tenor.evals.e4_reader.pool``). The reader follows ``prompts/e4_reader.v1.md``.

The key (default ``data/private/reader/e4_keys/<run_id>.key.json``) maps every K id back to
the conditions and sources that show it; it is kept outside the packet directory and readers
must not open it. ``scripts/e4_reader_score.py`` reads it.

Gold defaults to the original AMI gold (``ami-1.6.2``), the gold of the E3 reader run whose
missing gold E4 is scored against. Local data only: no LLM, no network. Same input, same bytes.
Outputs are private (data/private is gitignored).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "packages")]

from scripts import reader_packets as P  # noqa: E402
from tenor.evals import e4_reader as R  # noqa: E402

OUT_DIR = ROOT / "data/private/reader/e4_packets"
KEY_DIR = ROOT / "data/private/reader/e4_keys"


def render(p: P.PacketInput, unit_key: Mapping[str, Any]) -> str:
    """The E4 packet: a new header, the E3 packet's sections 1-5, then section 6."""
    label = P.PRODUCT_LABEL.get(p.product, p.product)
    body = P.render_packet(p).split("\n", 1)[1]  # drop the E3 title
    head = [
        f"# E4 reader packet: {p.meeting_id} / {label}",
        "",
        f"E4 packet format {R.PACKET_VERSION}, reader instructions `{R.READER_PROMPT}`. "
        f"{len(unit_key['items'])} candidate items (section 6). `K7` = a candidate item. "
        "Sections 1-5 are the E3 reader packet; section 6 is at the end.",
    ]
    return "\n".join([*head, body.rstrip("\n"), "", *R.candidates_section(unit_key["items"])])


def bullets_sha(p: P.PacketInput) -> str:
    text = "\n".join(b.rendered for b in p.bullets)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def build(
    candidates: Mapping[str, Any], inputs: Sequence[P.PacketInput], seed: int
) -> tuple[dict[str, str], dict[str, Any]]:
    """Packet texts by file name and the key's units. Raises SystemExit if a unit's notes
    bullets differ from the ones the candidates were deduplicated against."""
    by = {(p.meeting_id, p.product): p for p in inputs}
    texts: dict[str, str] = {}
    units: dict[str, Any] = {}
    for rec in sorted(candidates["records"], key=lambda r: (r["meeting_id"], r["product"])):
        p = by[(rec["meeting_id"], rec["product"])]
        if bullets_sha(p) != rec["bullets_sha"]:
            raise SystemExit(f"{p.meeting_id}:{p.product}: notes bullets differ from the run's")
        uk = R.key_unit(rec, seed, [b.id for b in p.bullets], [g.id for g in p.gold])
        units[f"{p.meeting_id}:{p.product}"] = uk
        texts[P.packet_name(p.meeting_id, p.product)] = render(p, uk)
    return texts, units


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--candidates", type=Path, required=True)
    ap.add_argument("--gold", choices=("original", "errata"), default="original")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument("--key", type=Path, default=None)
    ap.add_argument("--seed", type=int, default=None, help="Default: the run's seed.")
    a = ap.parse_args(argv)
    raw = a.candidates.read_bytes()
    cands = json.loads(raw)
    seed = a.seed if a.seed is not None else int(cands["meta"]["seed"])
    errata = None
    if a.gold == "errata":
        from tenor.ami.errata import ERRATA_V1, load_errata

        errata = load_errata(ROOT / ERRATA_V1)
    meetings = sorted({r["meeting_id"] for r in cands["records"]})
    texts, units = build(cands, P.load_inputs(meetings, errata), seed)
    a.out.mkdir(parents=True, exist_ok=True)
    for name, text in texts.items():
        (a.out / name).write_text(text, encoding="utf-8")
        sha = hashlib.sha256(text.encode()).hexdigest()[:12]
        print(f"{name}\t{len(text):,} chars\t{len(units[name[:-3].replace('__', ':')]['items'])}"
              f" items\tsha256 {sha}")  # fmt: skip
    from tenor.evals.notes_completeness import gold_version

    run_id = cands["meta"].get("run_id") or a.candidates.name.split(".")[0]
    key = {
        "meta": {
            "key_version": R.KEY_VERSION, "packet_version": R.PACKET_VERSION,
            "reader_prompt": R.READER_PROMPT, "seed": seed, "run_id": run_id,
            "candidates": str(a.candidates), "gold_version": gold_version(errata),
            "candidates_sha256": hashlib.sha256(raw).hexdigest()[:16],
        },
        "units": units,
    }  # fmt: skip
    path = a.key or KEY_DIR / f"{run_id}.key.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(key, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"key: {path} ({len(units)} units)")


if __name__ == "__main__":
    main()
