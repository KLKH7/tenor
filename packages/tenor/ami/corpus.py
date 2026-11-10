"""Parse AMI's manual annotations (NXT XML) straight from ``ami_public_manual_1.6.2.zip``.

The zip is read in place, never extracted. For one meeting we load:

- **speakers** from ``corpusResources/meetings.xml``: NXT agent letter (A-D) -> role, a stable
  participant code and the audio channel. Letters are per meeting, not per person: in IS1008 the
  same person is agent B in one session and C in the next, so cross-meeting code must key on
  ``participant``, never on the letter;
- **words** from ``words/<ID>.<letter>.words.xml``: each spoken word with forced-alignment start
  and end times. Punctuation, truncated fragments ("th-") and non-word events (laughs, gaps,
  disfluency markers) are flagged or dropped here, so scorers see the same reference;
- **summary** from ``abstractive/<ID>.abssumm.xml``: the annotator's ABSTRACT / DECISIONS /
  PROBLEMS / ACTIONS sentences (scenario meetings only; natural meetings have none);
- **dialogue acts** from ``dialogueActs/<ID>.<letter>.dialog-act.xml``: AMI's segmentation of
  each speaker's words into utterance-sized units. Each act points at a range of word ids, at
  its type in ``ontologies/da-types.xml`` (``inf`` Inform, ``sug`` Suggest, ``ass`` Assess, ...;
  :data:`DA_GLOSS`) and, in some meetings, carries an ``addressee`` attribute;
- **summary links** from ``extractive/<ID>.summlink.xml``: for each abstractive sentence, the
  dialogue acts an annotator marked as its support. Not every meeting has them (IB4003 doesn't).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from tenor.core.models import Channel, MeetingNote, Person, Utterance, utterance_id

NITE = "{http://nite.sourceforge.net/}"
DEFAULT_ZIP = Path("data/ami/annotations/ami_public_manual_1.6.2.zip")
_WORDS = re.compile(r"words/(?P<mid>[A-Z]{2}\d{4}[a-z]?)\.(?P<agent>[A-Z])\.words\.xml$")
_ACTS = re.compile(r"dialogueActs/(?P<mid>[A-Z]{2}\d{4}[a-z]?)\.(?P<agent>[A-Z])\.dialog-act\.xml$")
_HREF_IDS = re.compile(r"id\(([^)]+)\)")
DA_TYPES_XML = "ontologies/da-types.xml"
# AMI's dialogue-act types (short name -> gloss), as ontologies/da-types.xml names them.
DA_GLOSS = {
    "bck": "Backchannel",
    "stl": "Stall",
    "fra": "Fragment",
    "inf": "Inform",
    "sug": "Suggest",
    "ass": "Assess",
    "el.inf": "Elicit-Inform",
    "el.sug": "Elicit-Offer-Or-Suggestion",
    "el.ass": "Elicit-Assessment",
    "el.und": "Elicit-Comment-Understanding",
    "off": "Offer",
    "und": "Comment-About-Understanding",
    "be.pos": "Be-Positive",
    "be.neg": "Be-Negative",
    "oth": "Other",
}
DA_MINOR = ("bck", "stl", "fra")  # AMI's "minor" class: no content of their own
SUMMARY_SECTIONS = ("abstract", "decisions", "problems", "actions")
ROLE_NAMES = {
    "PM": "Project Manager",
    "ME": "Marketing Expert",
    "UI": "User Interface Designer",
    "ID": "Industrial Designer",
}
# AMI publishes no meeting dates we rely on; one fixed, obviously synthetic origin keeps
# MeetingNote timestamps deterministic.
_EPOCH = datetime(2005, 1, 1, tzinfo=UTC)


class Speaker(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent: str  # NXT letter, A-D (per meeting)
    role: str | None  # PM / ME / UI / ID in scenario meetings; absent in natural ones
    participant: str  # AMI's anonymised participant code, stable across meetings
    channel: int


class Word(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent: str
    start: float  # seconds from the start of the recording
    end: float
    text: str
    punc: bool = False  # a punctuation token ("." ",")
    trunc: bool = False  # an interrupted fragment ("th", "cou")
    word_id: str = ""  # NXT id ("IS1008a.A.words12"); dialogue acts point at these


class DialogueAct(BaseModel):
    """One AMI dialogue act: a speaker's utterance-sized unit of words."""

    model_config = ConfigDict(frozen=True)

    da_id: str  # NXT id, e.g. "IS1008a.A.dialog-act.s9553330.2"
    agent: str
    start: float  # first word's start time (seconds)
    end: float
    text: str  # its words (no non-word events, no truncated fragments), punctuation attached
    da_type: str = ""  # AMI's short type name ("inf", "sug", "ass", ...; DA_GLOSS); "" if untyped
    addressee: str = ""  # the act's ``addressee`` attribute as given (agent letters); "" if absent


class SummarySentence(BaseModel):
    """One abstractive summary sentence and the dialogue acts an annotator linked to it."""

    model_config = ConfigDict(frozen=True)

    sentence_id: str
    section: str  # one of SUMMARY_SECTIONS
    text: str
    links: tuple[str, ...]  # DialogueAct.da_id values from summlink.xml; empty if none


class AmiMeeting(BaseModel):
    model_config = ConfigDict(frozen=True)

    meeting_id: str
    type: str  # "scenario" or "nonscenario" (AMI's own label)
    duration_s: int
    speakers: tuple[Speaker, ...]
    words: tuple[Word, ...]  # all speakers, sorted by start time
    summary: dict[str, tuple[str, ...]]  # SUMMARY_SECTIONS -> sentences; empty if none
    dialogue_acts: tuple[DialogueAct, ...] = ()  # sorted by start time, then agent
    sentences: tuple[SummarySentence, ...] = ()  # ``summary`` with ids and summary links
    has_summlinks: bool = False  # extractive/<ID>.summlink.xml exists

    def spoken(self) -> list[Word]:
        """Words a listener would hear: no punctuation tokens, no truncated fragments."""
        return [w for w in self.words if not w.punc and not w.trunc]

    def reference_text(self) -> str:
        """The manual transcript as one string, all speakers merged by start time."""
        return " ".join(w.text for w in self.spoken())


def _parse(zf: zipfile.ZipFile, name: str) -> ET.Element:
    return ET.fromstring(zf.read(name))


def _speakers(zf: zipfile.ZipFile, meeting_id: str) -> tuple[str, int, tuple[Speaker, ...]]:
    root = _parse(zf, "corpusResources/meetings.xml")
    for m in root.iter("meeting"):
        if m.get("observation") != meeting_id:
            continue
        speakers = tuple(
            sorted(
                (
                    Speaker(
                        agent=s.get("nxt_agent", ""),
                        role=s.get("role"),
                        participant=s.get("global_name", ""),
                        channel=int(s.get("channel", "-1")),
                    )
                    for s in m.iter("speaker")
                ),
                key=lambda s: s.agent,
            )
        )
        return m.get("type", ""), round(float(m.get("duration", "0"))), speakers
    raise KeyError(f"{meeting_id} not in meetings.xml")


# Per agent, every element of its words file in document order: (NXT id, the Word or None
# for a non-word event). Dialogue-act ranges may start or end on a non-word event.
_Stream = list[tuple[str, Word | None]]


def _word_streams(zf: zipfile.ZipFile, meeting_id: str) -> dict[str, _Stream]:
    streams: dict[str, _Stream] = {}
    for name in sorted(zf.namelist()):
        hit = _WORDS.search(name)
        if not hit or hit["mid"] != meeting_id:
            continue
        stream = streams.setdefault(hit["agent"], [])
        for el in _parse(zf, name):
            wid = el.get(f"{NITE}id", "")
            if el.tag != "w" or not (el.text or "").strip():
                stream.append((wid, None))  # vocalsound, gap, disfmarker: not words
                continue
            word = Word(
                agent=hit["agent"],
                start=float(el.get("starttime", "nan")),
                end=float(el.get("endtime", "nan")),
                text=(el.text or "").strip(),
                punc=el.get("punc") == "true",
                trunc=el.get("trunc") == "true",
                word_id=wid,
            )
            stream.append((wid, word))
    return streams


def _words(streams: dict[str, _Stream]) -> list[Word]:
    words = [w for agent in sorted(streams) for _, w in streams[agent] if w is not None]
    # Stable order: start time, then speaker, then original position (sort is stable).
    words.sort(key=lambda w: (w.start, w.agent))
    return words


def _act_text(words: list[Word]) -> str:
    """Spoken words of an act; punctuation glued to the previous word, fragments dropped."""
    out = ""
    for w in words:
        if w.trunc:
            continue
        out = f"{out}{w.text}" if w.punc else f"{out} {w.text}"
    return out.strip()


def _da_type_names(zf: zipfile.ZipFile) -> dict[str, str]:
    """NXT id of a dialogue-act type (``ami_da_4``) -> its short name (``inf``); empty when the
    zip has no ontology file."""
    if DA_TYPES_XML not in zf.namelist():
        return {}
    return {
        el.get(f"{NITE}id", ""): el.get("name", "")
        for el in _parse(zf, DA_TYPES_XML).iter("da-type")
    }


def _da_type(el: ET.Element, names: dict[str, str]) -> str:
    for p in el.iter(f"{NITE}pointer"):
        if p.get("role") == "da-aspect":
            ids = _HREF_IDS.findall(p.get("href", ""))
            return names.get(ids[0], "") if ids else ""
    return ""


def _dialogue_acts(
    zf: zipfile.ZipFile, meeting_id: str, streams: dict[str, _Stream]
) -> tuple[DialogueAct, ...]:
    """Every dialogue act with at least one spoken word, sorted by start time then agent."""
    acts: list[DialogueAct] = []
    names = _da_type_names(zf)
    for name in sorted(zf.namelist()):
        hit = _ACTS.search(name)
        if not hit or hit["mid"] != meeting_id:
            continue
        stream = streams.get(hit["agent"], [])
        pos = {wid: i for i, (wid, _) in enumerate(stream) if wid}
        for el in _parse(zf, name):
            if el.tag != "dact":
                continue
            words: list[Word] = []
            for child in el.iter(f"{NITE}child"):
                ids = _HREF_IDS.findall(child.get("href", ""))
                if not ids or ids[0] not in pos or ids[-1] not in pos:
                    continue
                span = stream[pos[ids[0]] : pos[ids[-1]] + 1]
                words += [w for _, w in span if w is not None]
            text = _act_text(words)
            if not text:
                continue
            acts.append(
                DialogueAct(
                    da_id=el.get(f"{NITE}id", ""),
                    agent=hit["agent"],
                    start=min(w.start for w in words),
                    end=max(w.end for w in words),
                    text=text,
                    da_type=_da_type(el, names),
                    addressee=el.get("addressee", ""),
                )
            )
    acts.sort(key=lambda a: (a.start, a.agent))
    return tuple(acts)


def _summlinks(zf: zipfile.ZipFile, meeting_id: str) -> dict[str, list[str]] | None:
    """Abstractive sentence id -> linked dialogue-act ids, or None if the file is absent."""
    name = f"extractive/{meeting_id}.summlink.xml"
    if name not in zf.namelist():
        return None
    links: dict[str, list[str]] = {}
    for link in _parse(zf, name).iter("summlink"):
        roles = {
            p.get("role"): _HREF_IDS.findall(p.get("href", "")) for p in link.iter(f"{NITE}pointer")
        }
        for sid in roles.get("abstractive", []):
            for da in roles.get("extractive", []):
                if da not in links.setdefault(sid, []):
                    links[sid].append(da)
    return links


def _sentences(
    zf: zipfile.ZipFile, meeting_id: str, links: dict[str, list[str]]
) -> list[SummarySentence]:
    name = f"abstractive/{meeting_id}.abssumm.xml"
    if name not in zf.namelist():
        return []
    root = _parse(zf, name)
    out: list[SummarySentence] = []
    for section in SUMMARY_SECTIONS:
        el = root.find(section)
        if el is None:
            continue
        for s in el.iter("sentence"):
            if not s.text:
                continue
            sid = s.get(f"{NITE}id", "")
            text = " ".join(s.text.split())
            out.append(
                SummarySentence(
                    sentence_id=sid, section=section, text=text, links=tuple(links.get(sid, ()))
                )
            )
    return out


def _summary(sentences: list[SummarySentence]) -> dict[str, tuple[str, ...]]:
    out: dict[str, tuple[str, ...]] = {}
    for s in sentences:
        out[s.section] = (*out.get(s.section, ()), s.text)
    return out


def load_meeting(meeting_id: str, zip_path: Path = DEFAULT_ZIP) -> AmiMeeting:
    """Load one AMI meeting from the zip: speakers, manual words, dialogue acts, abstractive
    summary and its links to the dialogue acts."""
    with zipfile.ZipFile(zip_path) as zf:
        mtype, duration, speakers = _speakers(zf, meeting_id)
        streams = _word_streams(zf, meeting_id)
        words = _words(streams)
        if not words:
            raise KeyError(f"no word files for {meeting_id} in {zip_path}")
        links = _summlinks(zf, meeting_id)
        sentences = _sentences(zf, meeting_id, links or {})
        return AmiMeeting(
            meeting_id=meeting_id,
            type=mtype,
            duration_s=duration,
            speakers=speakers,
            words=tuple(words),
            summary=_summary(sentences),
            dialogue_acts=_dialogue_acts(zf, meeting_id, streams),
            sentences=tuple(sentences),
            has_summlinks=links is not None,
        )


def meeting_note(m: AmiMeeting) -> MeetingNote:
    """An AMI meeting as a :class:`MeetingNote`: one utterance per dialogue act, in time order.

    Every utterance is channel ``them`` (no one in the room is the note owner). The speaker's
    scenario role ("Project Manager") is given as ``speaker_name`` when AMI has one, else
    "Speaker A".."Speaker D"; the NXT letter goes in ``diarization_label``. A product's
    transcript would usually have neither, so a checker sees more here than it would there.
    Utterance ``i`` is ``m.dialogue_acts[i]``. ``summary_markdown`` is AMI's human summary.
    """
    roles = {s.agent: ROLE_NAMES.get(s.role or "", "") for s in m.speakers}
    utts = tuple(
        Utterance(
            utterance_id=utterance_id(m.meeting_id, i),
            note_id=m.meeting_id,
            index=i,
            channel=Channel.THEM,
            speaker_name=roles.get(a.agent) or f"Speaker {a.agent}",
            diarization_label=a.agent,
            text=a.text,
            start_time=_EPOCH + timedelta(seconds=a.start),
            end_time=_EPOCH + timedelta(seconds=a.end),
        )
        for i, a in enumerate(m.dialogue_acts)
    )
    summary = "\n".join(
        f"## {section.capitalize()}\n" + "\n".join(f"- {t}" for t in m.summary[section])
        for section in SUMMARY_SECTIONS
        if m.summary.get(section)
    )
    return MeetingNote(
        note_id=m.meeting_id,
        title=f"AMI {m.meeting_id}",
        owner=Person(name=None, email="ami@example.invalid"),
        created_at=_EPOCH,
        updated_at=_EPOCH,
        deleted_at=None,
        web_url="",
        event_title=None,
        calendar_event_id=None,
        scheduled_start=None,
        scheduled_end=None,
        organiser_email=None,
        invitee_emails=(),
        attendees=(),
        folders=(),
        space_ids=(),
        summary_markdown=summary or None,
        summary_text="",
        private_notes_markdown=None,
        utterances=utts,
    )
