"""Match product notes to AMI meetings and score their transcripts against the manual words.

Each playback gets a calendar event titled ``AMI <meeting id>`` (docs/method/AMI_REPLAY.md
§3), so a synced note is matched by that tag in its title or event title. Notes come from a
dedicated AMI account synced into its own database (``data/ami_<product>.db``), never from the
author's real meetings.

AMI is played in digitally with the speakers muted, so the microphone should hear nothing;
anything it does hear is the room, not AMI. Granola records the capture channel itself, so its
microphone turns are dropped (``drop_mic=True``) and counted in ``mic_utterances``: a non-zero
value flags a contaminated recording. Wispr has no capture channel: our "me" there is only a
guess from the speaker name (sources/wispr_mcp.py), and on AMI Wispr can name strangers after
the account holder. Dropping those turns would delete real AMI speech, so Wispr is scored with
``drop_mic=False`` and its room protection is physical (muted speakers, silent room).
``owner_labelled`` counts turns attributed to the account holder: on AMI every one is wrong.

Results hold numbers only: no transcript text, note IDs or titles. Raw product output stays
local (Kian, 2026-11-04: publish scores only).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from tenor.ami.corpus import DEFAULT_ZIP, load_meeting
from tenor.ami.wer import WerScore, score
from tenor.core.models import Channel, MeetingNote

log = logging.getLogger(__name__)

# The original replay set. Every scored command (wer, coverage, detector, stress, wrong facts)
# runs on exactly these seven, so published scores do not move when meetings are added.
AMI_SET = ("IS1008a", "IS1008b", "IS1008c", "IS1008d", "IB4003", "EN2002b", "ES2006a")
# Seven more meetings for the overnight replay (2026-11-05; docs/method/AMI_REPLAY.md §11).
# One per scenario team, so no cross-meeting question skips a session.
AMI_EXTENSION = ("TS3009c", "ES2015c", "ES2008b", "ES2006c", "TS3006b", "ES2007c", "IS1000a")
AMI_EXTENDED = AMI_SET + AMI_EXTENSION
AUDIO_DIR = Path("data/ami")
_TAG = re.compile(r"\bAMI[\s_-]+([A-Z]{2}\d{4}[a-z]?)\b")
# A stress-condition recording ("AMI ES2006a A1", docs/tracks/CAPTURE.md) is not a baseline one.
_STRESS = re.compile(r"\bAMI[\s_-]+([A-Z]{2}\d{4}[a-z]?)[\s_-]+(A\d+[a-z]?)\b")


class MeetingWer(BaseModel):
    model_config = ConfigDict(frozen=True)

    meeting_id: str
    product: str
    score: WerScore
    product_speakers: int  # distinct speaker labels the product gave (0 if none)
    mic_utterances: int  # microphone turns dropped before scoring; > 0 = room leaked in
    owner_labelled: int  # turns attributed to the account holder, who never speaks in AMI
    reference_speakers: int


def audio_path(meeting_id: str, audio_dir: Path = AUDIO_DIR) -> Path:
    """Where a meeting's headset-mix WAV lives once downloaded (gitignored)."""
    return audio_dir / meeting_id / f"{meeting_id}.Mix-Headset.wav"


def replay_state(downloaded: bool, recorded_in: Iterable[str]) -> str:
    """One status word for a meeting: recorded where, else downloaded or not."""
    where = sorted(recorded_in)
    if where:
        return f"recorded ({', '.join(where)})"
    return "downloaded, not recorded" if downloaded else "not downloaded"


def stress_tag(note: MeetingNote) -> tuple[str, str] | None:
    """(AMI meeting ID, condition) for a stress recording titled like ``AMI ES2006a A1``."""
    for text in (note.title, note.event_title):
        hit = _STRESS.search(text or "")
        if hit:
            return hit[1], hit[2]
    return None


def ami_tag(note: MeetingNote) -> str | None:
    """Return the AMI meeting ID tagged in a note's title or calendar event, if any.

    Stress recordings (:func:`stress_tag`) are not baseline recordings and return None.
    """
    if stress_tag(note) is not None:
        return None
    for text in (note.title, note.event_title):
        hit = _TAG.search(text or "")
        if hit:
            return hit[1]
    return None


def match_notes(notes: Iterable[MeetingNote]) -> dict[str, MeetingNote]:
    """Map AMI meeting ID -> note. Raises if one meeting was recorded more than once."""
    out: dict[str, MeetingNote] = {}
    for note in notes:
        mid = ami_tag(note)
        if mid is None:
            continue
        if mid in out:
            raise ValueError(f"{mid} matched two notes; delete or retitle one recording")
        out[mid] = note
    return out


_BARE_ID = re.compile(r"[A-Z]{2}\d{4}[a-z]?")


_PIN = re.compile(r"([a-z][a-z0-9_]*)=(\S+)")


def read_overrides(path: Path) -> dict[str, dict[str, str]]:
    """Read manual recording IDs: product -> AMI meeting ID -> note ID.

    A pin line starts with a meeting ID followed by ``<product>=<note id>`` tokens, e.g.
    ``IB4003 wispr=<uuid> granola=<uuid>``. A stress recording pins under
    ``<product>_<condition>`` in lower case (``granola_a1=<uuid>``). Anything else is a human
    note and is ignored: other words on a pin line ("(untitled)"), lines not starting with a
    bare meeting ID, and text after ``#``. A missing file means no overrides.
    """
    out: dict[str, dict[str, str]] = {}
    if not path.is_file():
        return out
    for raw in path.read_text().splitlines():
        parts = raw.split("#", 1)[0].split()
        if not parts or not _BARE_ID.fullmatch(parts[0]):
            continue
        for tok in parts[1:]:
            pin = _PIN.fullmatch(tok)
            if pin:
                out.setdefault(pin[1], {})[parts[0]] = pin[2]
    return out


def match_recordings(
    notes: Iterable[MeetingNote], overrides: dict[str, str] | None = None
) -> dict[str, MeetingNote]:
    """Map AMI meeting ID -> note, for one product's notes.

    Like :func:`match_notes`, plus a note whose title is exactly a bare meeting ID
    ("IB4003": Wispr titles can lack the "AMI " prefix), and ``overrides`` (meeting ID -> note
    ID, from :func:`read_overrides`), which win over any title match. A pinned meeting whose
    note isn't synced yet is left unmatched (with a warning). Raises if one meeting still
    matches two notes.
    """
    pinned = overrides or {}
    by_id: dict[str, MeetingNote] = {}
    out: dict[str, MeetingNote] = {}
    for note in notes:
        by_id[note.note_id] = note
        bare = (note.title or "").strip()
        mid = ami_tag(note) or (bare if _BARE_ID.fullmatch(bare) else None)
        if mid is None or mid in pinned:
            continue
        if mid in out:
            raise ValueError(f"{mid} matched two notes; retitle one or pin it in the overrides")
        out[mid] = note
    for mid, note_id in pinned.items():
        if note_id in by_id:
            out[mid] = by_id[note_id]
        else:
            log.warning("override for %s names a note that isn't synced yet; skipped", mid)
    return out


def match_stress(
    notes: Iterable[MeetingNote], meeting_id: str, condition: str, pinned: str | None = None
) -> MeetingNote | None:
    """The one note recording ``meeting_id`` under ``condition``: a pinned note ID wins, else
    the note titled ``AMI <meeting_id> <condition>``. Raises if two notes match."""
    found: list[MeetingNote] = []
    for note in notes:
        if pinned is not None:
            if note.note_id == pinned:
                return note
            continue
        if stress_tag(note) == (meeting_id, condition):
            found.append(note)
    if len(found) > 1:
        raise ValueError(f"{meeting_id} {condition} matched {len(found)} notes; pin one")
    return found[0] if found else None


def transcript_text(note: MeetingNote, *, drop_mic: bool = True) -> str:
    """The product's transcript as one string, in its utterance order, mic turns optional."""
    utts = sorted(note.utterances, key=lambda u: u.index)
    return " ".join(
        u.text
        for u in utts
        if not (drop_mic and u.channel is Channel.ME) and not _is_banner(u.text)
    )


def _is_banner(text: str) -> bool:
    """Wispr prepends a ``<<<…>>>`` safety banner as utterance 0; it is not speech."""
    t = text.strip()
    return t.startswith("<<<") and t.endswith(">>>")


def score_note(
    meeting_id: str,
    note: MeetingNote,
    product: str,
    zip_path: Path = DEFAULT_ZIP,
    *,
    drop_mic: bool = True,
) -> MeetingWer:
    """Score one product note against the AMI manual transcript of ``meeting_id``.

    ``drop_mic`` only for products whose microphone channel is a real capture channel (Granola).
    """
    meeting = load_meeting(meeting_id, zip_path)
    mine = [u for u in note.utterances if u.channel is Channel.ME]
    system = [u for u in note.utterances if not (drop_mic and u.channel is Channel.ME)]
    labels = {u.speaker_name or u.diarization_label for u in system} - {None}
    return MeetingWer(
        meeting_id=meeting_id,
        product=product,
        score=score(meeting.reference_text(), transcript_text(note, drop_mic=drop_mic)),
        product_speakers=len(labels),
        mic_utterances=len(note.utterances) - len(system),
        owner_labelled=len(mine),
        reference_speakers=len(meeting.speakers),
    )


def wer_report(by_product: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Numbers-only WER summary across products: per meeting and pooled.

    ``by_product`` maps a product label to its ``wer_<product>.json`` payload (``meta`` and
    ``meetings``). Pooled WER is total edits over total reference words; pooled content-word
    recall is total content words found over total reference content words. No text or IDs
    beyond the AMI meeting ID are carried over.
    """
    products: dict[str, Any] = {}
    for name, payload in sorted(by_product.items()):
        rows = [MeetingWer.model_validate(m) for m in payload["meetings"]]
        meta = payload["meta"]
        ref = sum(r.score.ref_words for r in rows)
        edits = sum(r.score.edits for r in rows)
        cw = sum(r.score.content_words for r in rows)
        cf = sum(r.score.content_found for r in rows)
        wers = [r.score.wer for r in rows]
        recalls = [r.score.content_recall for r in rows]
        products[name] = {
            "meta": meta,
            "n_meetings": len(rows),
            "pooled_wer": round(edits / ref, 4) if ref else None,
            "wer_range": [round(min(wers), 4), round(max(wers), 4)] if rows else None,
            "pooled_content_recall": round(cf / cw, 4) if cw else None,
            "content_recall_range": [round(min(recalls), 4), round(max(recalls), 4)]
            if rows
            else None,
            "meetings": [
                {
                    "meeting_id": r.meeting_id,
                    "ref_words": r.score.ref_words,
                    "hyp_words": r.score.hyp_words,
                    "edits": r.score.edits,
                    "wer": round(r.score.wer, 4),
                    "content_words": r.score.content_words,
                    "content_found": r.score.content_found,
                    "content_recall": round(r.score.content_recall, 4),
                    "mic_utterances_dropped": r.mic_utterances,
                    "product_speakers": r.product_speakers,
                    "reference_speakers": r.reference_speakers,
                }
                for r in rows
            ],
        }
    return {"products": products}
