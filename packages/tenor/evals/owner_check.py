"""Owner check: when meeting notes assign an action to a person, how often is the owner wrong,
and can a cheap check flag wrong owners? (data/private/FEATURE_CANDIDATES.md, experiment 3;
docs/tracks/NOTES.md, "Owner check").

**Dataset (no LLM).** From E3's saved judge passes (``notes_completeness``): every notes bullet
whose majority kind is *action* and that the majority of passes links to at least one gold
ACTION item. For each bullet:

* the **stated owner**: the owner span the notes give, found by position rules
  (:func:`stated_owner`): a leading ``(X)``, a leading ``X:``, a leading ``X to <verb>``, a
  parenthesis holding only names or roles, ``by X``, ``X to <verb>`` in the bullet's detail, or a
  group phrase ("each member"). A vague span ("one person") counts as no owner;
* the **gold owner**: the subject of each linked AMI action sentence ("The Industrial Designer
  will ..."), as roles (PM / ID / UI / ME), ``group``, ``external`` (a coach) or ``unspecified``
  ("one team member"); "he / she" takes the previous sentence's owner (:func:`gold_owner`);
* names resolved to roles with the meeting's **roster**: self-introductions across the whole AMI
  series (:func:`series_roster`; participant codes are stable across a series), close spellings
  matched (:func:`match_name`), plus :data:`HAND_NAMES` (names no self-introduction gives,
  resolved by hand from AMI's transcript; used for the truth labels only, never by a check).

**Label** (:func:`label`): ``correct`` (stated roles = gold roles, the union over linked
actions), ``wrong`` (no overlap), ``partial`` (overlap, not equal), ``missing`` (no owner
stated), ``ambiguous`` (a ``Speaker N`` label or a name the roster cannot resolve), or
``no_gold_owner`` (gold owner external or unspecified).

**Checks**, on bullets with a stated owner:

* **rule** (:func:`rule_flag`): flag when an owner token is not a roster name (close spelling
  allowed), a role, or a group word. ``Speaker N`` is not a roster name, so it is flagged;
* **LLM** (:data:`PROMPT`, Haiku and Sonnet): the bullet, the stated owner, the roster and ±
  :data:`WINDOW` transcript lines around the :data:`ANCHORS` lines that best match the bullet
  (lexical, no gold). Two transcripts: the **product's** own (Granola's lines are all "Them")
  and AMI's human transcript with role labels (the **oracle** variant: speaker identities no
  product has). Output: correct / wrong / can't tell + suggested owner roles.

**Metrics** over bullets labelled correct or wrong: recall (wrong owners flagged), precision
(flags that are wrong owners), false-flag rate (correct owners flagged), and suggested-owner
accuracy on wrong (and missing) owners. Go: recall ≥ 0.60, false flags ≤ 0.10, beats the rule.
"""

from __future__ import annotations

import difflib
import functools
import math
import re
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from tenor.evals.list_check import Line, wilson_ci
from tenor.llm.client import (
    LLMClient,
    LLMRequest,
    PromptShape,
    cost_usd,
    load_prompt,
    simulate_prompt_cache,
)

# v1 (pilot, DEV IS1008a): a generic "Speaker N" owner got "wrong" from some calls and "cant_tell"
# from others, and one Haiku call said "wrong" while suggesting the stated owner. v2 says a generic
# label is "cant_tell" (owner in suggested_owner) and that verdict and suggestion must agree.
PROMPT = "owner_check.v2"
STAGE = "owner_check"
MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5"}
VARIANTS = ("product", "oracle")  # which transcript the check reads
WINDOW = 8  # transcript lines either side of each anchor
ANCHORS = 2  # best-matching lines per bullet
MAX_TOKENS = 4000
OVERHEAD = 200  # structured-output tokens per call (ASSUMED, as in other checks)
# Output tokens per call (expected, high), ASSUMED until the pilot's ledger: a short reason,
# a verdict and up to 2 roles; low effort.
ASSUMED_OUT = (150.0, 400.0)
MARGIN = 1.5
PILOT_MEETING = "IS1008a"  # DEV
PILOT_MAX_USD = 0.20
GO_RECALL = 0.60
GO_FALSE_FLAGS = 0.10
# E3's full runs (notes_completeness.v4, 3 passes): the saved judge passes the links come from.
E3_RUNS = ("20261109T014826Z", "20261109T063610Z", "20261109T072942Z")

ROLES = ("PM", "ID", "UI", "ME")
GROUP = "group"
ROLE_LABEL = {
    "PM": "Project Manager",
    "ID": "Industrial Designer",
    "UI": "User Interface Designer",
    "ME": "Marketing Expert",
    GROUP: "whole group",
}
LABEL_ROLE = {v: k for k, v in ROLE_LABEL.items()}
Label = Literal["correct", "wrong", "partial", "missing", "ambiguous", "no_gold_owner"]
LABELS = ("correct", "wrong", "partial", "missing", "ambiguous", "no_gold_owner")

# Names no self-introduction in AMI gives, resolved by hand (truth labels only). Key: AMI series.
HAND_NAMES: dict[str, dict[str, str]] = {
    # Granola's TS3009c notes give "Thijs (industrial design)"; AMI's transcript has the PM
    # assigning the prototype drawing to the industrial designer and to Mike (the UI designer).
    "TS3009": {"thijs": "ID"},
}

# -- owner vocabulary -------------------------------------------------------------------------

_ROLE_PHRASES: tuple[tuple[str, frozenset[str]], ...] = (
    (r"(?:both|the two|two) designers", frozenset({"ID", "UI"})),
    (r"industrial and (?:user )?interface designers", frozenset({"ID", "UI"})),
    (r"project manager|project management|\bpm\b", frozenset({"PM"})),
    (r"industrial design(?:er)?s?|industry designer|\bindustrial\b", frozenset({"ID"})),
    (
        r"user interface design(?:er)?|(?:user )?interface (?:designer|specialist)|"
        r"\bui designer|\bui\b|\buser interface\b",
        frozenset({"UI"}),
    ),
    (r"marketing expert|\bmarketing\b|\bmarketer\b|\bmarket(?:ing)? research\b", frozenset({"ME"})),
)
_GROUP_PHRASES = (
    r"\beveryone\b|\beverybody\b|\ball (?:team )?members\b|\beach (?:team )?member\b|"
    r"\beach attendee\b|\bthe (?:whole )?team\b|\bteam members\b|"
    r"\bgroup members\b|\bthe group\b|\ball participants\b"
)
_VAGUE = re.compile(r"^(?:one|a|some) (?:person|member|team member|of us)\b|^someone\b", re.I)
_SPEAKER = re.compile(r"\bspeaker \d+\b", re.I)
_NAME = re.compile(r"\b[A-Z][a-z]+(?:-[A-Z][a-z]+)?\b")
_STOP_CAPS = {
    "And",
    "The",
    "Both",
    "Each",
    "All",
    "Everyone",
    "Speaker",
    "Team",
    "With",
    "By",
    "To",
    "Industrial",
    "Interface",
    "User",
    "Project",
    "Manager",
    "Marketing",
    "Expert",
    "Designer",
    "Designers",
    "Design",
    "Market",
    "Group",
    "Members",
    "Member",
    "Industry",
    "Specialist",
}


def _roles_in(span: str) -> frozenset[str]:
    """Roles named in ``span`` (phrases tried in order; a matched phrase is consumed)."""
    low = span.lower()
    found: set[str] = set()
    for pat, roles in _ROLE_PHRASES:
        if re.search(pat, low):
            found |= roles
            low = re.sub(pat, " ", low)
    return frozenset(found)


def _names_in(span: str) -> tuple[str, ...]:
    """Capitalised words in ``span`` that are not role or connective words."""
    return tuple(dict.fromkeys(w for w in _NAME.findall(span) if w not in _STOP_CAPS))


def _is_owner_span(span: str, roster_names: Iterable[str], bare_names: bool = False) -> bool:
    """Whether ``span`` reads as an owner: it names a role, a group, a ``Speaker N`` or a roster
    name, or (``bare_names``: inside parentheses, where a capitalised word is not a sentence
    start) it consists of capitalised names joined by commas / "and" / "&"."""
    s = span.strip()
    if not s or len(s) > 60:
        return False
    if _roles_in(s) or re.search(_GROUP_PHRASES, s, re.I) or _SPEAKER.search(s):
        return True
    if _VAGUE.search(s):
        return True
    if any(match_name(n, roster_names) for n in _names_in(s)):
        return True
    if not bare_names:
        return False
    words = re.split(r"\s*(?:,|&|\band\b)\s*", s)
    return all(re.fullmatch(r"[A-Z][a-z]+(?:[- ][A-Z][a-z]+)?", w) for w in words if w)


# -- the roster -------------------------------------------------------------------------------


def series_of(meeting_id: str) -> str:
    """AMI series: ``IS1008b`` -> ``IS1008``; a natural meeting (``IB4003``) is its own."""
    return meeting_id[:-1] if meeting_id[-1].islower() else meeting_id


@functools.lru_cache(maxsize=32)
def series_roster(meeting_id: str) -> tuple[tuple[str, str], ...]:
    """(role, name) for each role in ``meeting_id`` whose holder introduced themselves anywhere
    in the series (sessions a-d present in the annotations zip). Roles: PM / ID / UI / ME;
    natural meetings have none, so an empty roster."""
    from tenor.ami.corpus import load_meeting
    from tenor.ami.facts import spoken_names

    series = series_of(meeting_id)
    ids = [series + s for s in "abcd"] if series != meeting_id else [meeting_id]
    meetings = []
    for mid in ids:
        try:
            meetings.append(load_meeting(mid))
        except KeyError:
            continue
    names = spoken_names(meetings)
    target = next(m for m in meetings if m.meeting_id == meeting_id)
    return tuple(
        (s.role, names[s.participant])
        for s in target.speakers
        if s.role in ROLES and s.participant in names
    )


def roster_text(roster: Sequence[tuple[str, str]]) -> str:
    """The roster as the check reads it: every role, with the name when known."""
    known = dict(roster)
    return "; ".join(
        f"{ROLE_LABEL[r]} = {known[r]}" if r in known else f"{ROLE_LABEL[r]} = (name not known)"
        for r in ROLES
    )


def match_name(name: str, names: Iterable[str]) -> str | None:
    """The roster name ``name`` refers to, allowing close spellings: same first letter and a
    first-name similarity ≥ 0.6 (difflib), or one first name a prefix of the other (≥ 4
    letters). None if no roster name matches."""
    a = name.lower().split()[0] if name.strip() else ""
    if not a:
        return None
    for full in names:
        b = full.lower().split()[0]
        if a == b:
            return full
        if a[0] == b[0] and (
            difflib.SequenceMatcher(None, a, b).ratio() >= 0.6
            or (min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)))
        ):
            return full
    return None


# -- stated owner -----------------------------------------------------------------------------


@dataclass(frozen=True)
class StatedOwner:
    span: str | None  # the owner text the notes give; None if none
    rule: str  # which position rule found it ("none" if none)


_LEAD_PAREN = re.compile(r"^\(([^)]{1,60})\)\s*")
_LEAD_COLON = re.compile(r"^([^:—]{1,45}):\s+")
_LEAD_TO = re.compile(r"^([^,;:—()]{1,45}?)\s+(?:will |offered |agreed |is |are )?to (?!be\b)\w+")
_PAREN = re.compile(r"\(([^)]{1,60})\)")
_BY = re.compile(r"\bby ([A-Z][a-z]+(?: \([^)]*\))?(?:(?:,| and) [A-Z][a-z]+(?: \([^)]*\))?)*)")


def stated_owner(text: str, roster_names: Sequence[str] = ()) -> StatedOwner:
    """The owner span a notes bullet states, by position (module docstring). ``text`` is the
    bullet as E3 split it: the head, then any detail after " — "."""
    head, _, detail = text.partition(" — ")
    m = _LEAD_PAREN.match(head)
    if m and _is_owner_span(m.group(1), roster_names, bare_names=True):
        return StatedOwner(m.group(1).strip(), "lead_paren")
    m = _LEAD_COLON.match(head)
    if m and _is_owner_span(m.group(1), roster_names):
        return StatedOwner(m.group(1).strip(), "lead_colon")
    m = _LEAD_TO.match(head)
    if m and _is_owner_span(m.group(1), roster_names):
        return StatedOwner(m.group(1).strip(), "lead_to")
    m = _BY.search(head)
    if m and _names_in(m.group(1)):
        return StatedOwner(m.group(1).strip(), "by")
    for p in _PAREN.findall(head):
        words = [w for w in re.split(r"\s*(?:,|&|\band\b)\s*", p.strip()) if w]
        if words and all(_is_owner_span(w, roster_names, bare_names=True) for w in words):
            return StatedOwner(p.strip(), "paren")
    for sent in re.split(r"[;.]\s+|\s+—\s+", detail):
        m = _LEAD_TO.match(sent.strip())
        if m and _is_owner_span(m.group(1), roster_names):
            return StatedOwner(m.group(1).strip(), "detail_to")
    g = re.search(_GROUP_PHRASES, text, re.I)
    if g:
        return StatedOwner(g.group(0), "group_phrase")
    return StatedOwner(None, "none")


@dataclass(frozen=True)
class Resolved:
    roles: frozenset[str]  # PM / ID / UI / ME / group
    unresolved: tuple[str, ...]  # Speaker N labels and names the roster cannot resolve
    vague: bool  # "one person", "someone": no identifiable owner


def resolve(
    span: str | None, roster: Sequence[tuple[str, str]], extra: Mapping[str, str] | None = None
) -> Resolved:
    """Roles a stated owner span refers to: role phrases, group phrases, roster names (close
    spellings), and ``extra`` (lower-case first name -> role; truth labels only)."""
    if not span:
        return Resolved(frozenset(), (), False)
    if _VAGUE.search(span.strip()):
        return Resolved(frozenset(), (), True)
    roles = set(_roles_in(span))
    if re.search(_GROUP_PHRASES, span, re.I) and not roles:
        roles.add(GROUP)
    unresolved = [m.group(0) for m in _SPEAKER.finditer(span)]
    by_name = {n: r for r, n in roster}
    rest = _SPEAKER.sub(" ", span)
    for n in _names_in(rest):
        hit = match_name(n, by_name)
        if hit:
            roles.add(by_name[hit])
        elif extra and n.lower() in extra:
            roles.add(extra[n.lower()])
        else:
            unresolved.append(n)
    return Resolved(frozenset(roles), tuple(unresolved), False)


# -- gold owner -------------------------------------------------------------------------------


def gold_owner(text: str, previous: frozenset[str] | str | None = None) -> frozenset[str] | str:
    """The owner of one AMI action sentence: roles (and/or ``group``), or ``external`` /
    ``unspecified``. The subject is the text before " will " (else the first 6 words)."""
    subj = re.split(r"\s+will\s+", text, maxsplit=1)[0] if " will " in text else text
    low = subj.lower()
    if re.search(r"\bcoach", low):
        return "external"
    if re.search(r"\bone (?:team )?member\b|\bsomeone\b", low):
        return "unspecified"
    if re.fullmatch(r"\s*(?:he|she)(?: also)?\s*", low):
        return previous if previous is not None else "unspecified"
    roles = _roles_in(low)
    if roles:
        return roles
    if re.search(r"\b(?:they|team|group|members?|everyone|each|all)\b", low):
        return frozenset({GROUP})
    return "unspecified"


def gold_owners(actions: Sequence[Any]) -> dict[str, frozenset[str] | str]:
    """Gold action id -> owner (:func:`gold_owner`), in document order (for "he / she")."""
    out: dict[str, frozenset[str] | str] = {}
    prev: frozenset[str] | str | None = None
    for g in actions:
        prev = out[g.id] = gold_owner(g.text, prev)
    return out


def label(resolved: Resolved, gold: frozenset[str] | str) -> Label:
    """The truth label of one bullet (module docstring). ``gold`` is the union of the linked
    actions' owners, or ``external`` / ``unspecified`` if any linked action has no person."""
    if isinstance(gold, str):
        return "no_gold_owner"
    if not resolved.roles and not resolved.unresolved:
        return "missing"
    if resolved.unresolved:
        return "ambiguous"
    if resolved.roles == gold:
        return "correct"
    return "partial" if resolved.roles & gold else "wrong"


# -- the dataset ------------------------------------------------------------------------------


@dataclass(frozen=True)
class OwnerItem:
    meeting_id: str
    product: str
    split: str
    bullet_id: str
    text: str  # the notes bullet (local only)
    gold_ids: tuple[str, ...]  # linked gold actions
    stated: str | None  # owner span
    stated_rule: str
    stated_roles: tuple[str, ...]
    unresolved: tuple[str, ...]
    gold_roles: tuple[str, ...]  # union; ("external",) / ("unspecified",) if no person
    label: str

    @property
    def key(self) -> str:
        return f"{self.meeting_id}:{self.product}:{self.bullet_id}"


def _union(owners: Iterable[frozenset[str] | str]) -> frozenset[str] | str:
    acc: set[str] = set()
    for o in owners:
        if isinstance(o, str):
            return o
        acc |= o
    if GROUP in acc:  # a bullet for a whole-group task and role tasks: the group
        return frozenset({GROUP})
    return frozenset(acc)


def build_items(unit: Any, record: Mapping[str, Any]) -> list[OwnerItem]:
    """The owner dataset for one E3 unit (``notes_completeness.Unit``) from its saved record:
    majority-vote bullets of kind action linked to a gold action."""
    from tenor.evals import notes_completeness as N

    ids = [b["id"] for b in record["passes"][0]["bullet_verdicts"]]
    if ids != [b.id for b in unit.bullets]:
        raise ValueError(f"{unit.key}: notes split differs from the E3 record")
    maj = N.majority_verdicts(record["passes"])
    actions = [g for g in unit.gold if g.category == "action"]
    owners = gold_owners(actions)
    roster = series_roster(unit.meeting_id)
    names = [n for _, n in roster]
    extra = HAND_NAMES.get(series_of(unit.meeting_id), {})
    text = {b.id: b.text for b in unit.bullets}
    out: list[OwnerItem] = []
    for b in maj.bullets:
        linked = tuple(g for g in b.gold if g in owners)
        if b.kind != "action" or not linked:
            continue
        st = stated_owner(text[b.id], names)
        res = resolve(st.span, roster, extra)
        gold = _union(owners[g] for g in linked)
        out.append(
            OwnerItem(
                meeting_id=unit.meeting_id,
                product=unit.product,
                split=unit.split,
                bullet_id=b.id,
                text=text[b.id],
                gold_ids=linked,
                stated=None if res.vague else st.span,
                stated_rule=st.rule,
                stated_roles=tuple(sorted(res.roles)),
                unresolved=res.unresolved,
                gold_roles=(gold,) if isinstance(gold, str) else tuple(sorted(gold)),
                label=label(res, gold),
            )
        )
    return out


def excluded_links(unit: Any, record: Mapping[str, Any]) -> int:
    """Bullets linked to a gold action whose majority kind is not action (left out)."""
    from tenor.evals import notes_completeness as N

    maj = N.majority_verdicts(record["passes"])
    return sum(b.kind != "action" and any(g.startswith("A") for g in b.gold) for b in maj.bullets)


def label_counts(items: Sequence[OwnerItem]) -> dict[str, dict[str, int]]:
    """Label counts per product and overall."""
    out: dict[str, dict[str, int]] = {}
    for key in (*sorted({i.product for i in items}), "all"):
        rows = [i for i in items if key in ("all", i.product)]
        c = Counter(i.label for i in rows)
        out[key] = {"bullets": len(rows), **{lab: c.get(lab, 0) for lab in LABELS}}
    return out


# -- rule check -------------------------------------------------------------------------------


def rule_flag(item: OwnerItem, roster: Sequence[tuple[str, str]]) -> bool:
    """Flag when the stated owner holds a token that is not a roster name (close spelling
    allowed), a role or a group word: ``Speaker N`` labels and unknown names. No hand names."""
    if not item.stated:
        return False
    return bool(resolve(item.stated, roster).unresolved)


# -- LLM check --------------------------------------------------------------------------------

_WORD = re.compile(r"[a-z][a-z']+")
_STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "by",
        "at",
        "from",
        "as",
        "is",
        "are",
        "be",
        "will",
        "was",
        "were",
        "this",
        "that",
        "it",
        "its",
        "their",
        "them",
        "they",
        "he",
        "she",
        "we",
        "you",
        "i",
        "our",
        "your",
        "his",
        "her",
        "about",
        "into",
        "after",
        "before",
        "next",
        "meeting",
        "work",
        "working",
        "make",
        "new",
        "also",
        "all",
        "each",
        "one",
        "two",
        "get",
        "put",
        "use",
    ]
)


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if len(w) >= 3 and w not in _STOPWORDS]


def anchors(text: str, lines: Sequence[Line], k: int = ANCHORS, gap: int = WINDOW) -> list[int]:
    """Positions (into ``lines``) of the ``k`` lines that best match ``text``: each line scored
    with its neighbours (a 3-line window, as lines are short) by the IDF-weighted overlap of
    content words; later anchors at least ``gap`` lines from earlier ones. Ties: earlier line.
    Deterministic; no gold."""
    if not lines:
        return []
    toks = [set(_tokens(x.text)) for x in lines]
    df = Counter(w for t in toks for w in t)
    n = len(lines)
    want = set(_tokens(text))
    idf = {w: math.log((n + 1) / (df.get(w, 0) + 0.5)) for w in want}
    scores = []
    for i in range(n):
        near = set().union(*toks[max(0, i - 1) : i + 2])
        scores.append(sum(idf[w] for w in want & near))
    order = sorted(range(n), key=lambda i: (-scores[i], i))
    picked: list[int] = []
    for i in order:
        if len(picked) == k:
            break
        if all(abs(i - p) > gap for p in picked):
            picked.append(i)
    return sorted(picked)


def window(lines: Sequence[Line], at: Sequence[int], k: int = WINDOW) -> list[Line]:
    """Lines within ``k`` positions of any anchor, in order."""
    keep = {j for a in at for j in range(a - k, a + k + 1)}
    return [x for j, x in enumerate(lines) if j in keep]


class CheckOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str
    verdict: Literal["correct", "wrong", "cant_tell"]
    suggested_owner: list[
        Literal[
            "Project Manager",
            "Industrial Designer",
            "User Interface Designer",
            "Marketing Expert",
            "whole group",
        ]
    ]


SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "verdict": {"type": "string", "enum": ["correct", "wrong", "cant_tell"]},
        "suggested_owner": {
            "type": "array",
            "items": {"type": "string", "enum": [ROLE_LABEL[r] for r in (*ROLES, GROUP)]},
        },
    },
    "required": ["reason", "verdict", "suggested_owner"],
    "additionalProperties": False,
}

_TRANSCRIPT_NOTE = {
    "product": (
        "Transcript excerpt from the note-taker's own transcript. Speaker labels are audio "
        'channels ("Them") or the note-taker\'s guesses, not identities.'
    ),
    "oracle": (
        "Transcript excerpt from the human reference transcript. Each line is labelled with the "
        "speaker's role."
    ),
}


def check_user(
    item: OwnerItem, roster: Sequence[tuple[str, str]], shown: Sequence[Line], variant: str
) -> str:
    """The check's user text: roster, bullet, stated owner, transcript excerpt."""
    body = "\n".join(x.render() for x in shown) or "(no transcript lines)"
    return (
        f"Speaker roster: {roster_text(roster)}.\n\n"
        f"Action item from the notes: {item.text}\n\n"
        f"Owner the notes state: {item.stated or '(none stated)'}\n\n"
        f"{_TRANSCRIPT_NOTE[variant]}\n{body}"
    )


def check_request(
    item: OwnerItem, roster: Sequence[tuple[str, str]], lines: Sequence[Line], variant: str
) -> LLMRequest:
    """One check call. The system prompt (+ schema) is the cache breakpoint shared by every
    call; the rest differs per bullet."""
    shown = window(lines, anchors(item.text, lines))
    return LLMRequest(
        stage=STAGE,
        prompt_id=PROMPT,
        system=load_prompt(PROMPT),
        user=check_user(item, roster, shown, variant),
        schema=SCHEMA,
        max_tokens=MAX_TOKENS,
        effort="low",
    )


@dataclass(frozen=True)
class CheckVerdict:
    verdict: str  # correct / wrong / cant_tell
    suggested: tuple[str, ...]  # roles (PM / ID / UI / ME / group)
    reason: str

    @property
    def flag(self) -> bool:
        return self.verdict == "wrong"


def run_check(client: LLMClient, req: LLMRequest) -> CheckVerdict:
    out = client.structured(req, CheckOut)
    return CheckVerdict(
        out.verdict, tuple(sorted({LABEL_ROLE[s] for s in out.suggested_owner})), out.reason
    )


# -- metrics ----------------------------------------------------------------------------------


def _rate(k: int, n: int) -> dict[str, Any]:
    return {"k": k, "n": n, "rate": (k / n) if n else None, "ci95": wilson_ci(k, n)}


def suggested_right(item: Mapping[str, Any], suggested: Sequence[str]) -> bool:
    """The suggestion names exactly the gold owner roles."""
    return tuple(sorted(suggested)) == tuple(item["gold_roles"])


def score(rows: Sequence[Mapping[str, Any]], flag_key: str, sugg_key: str | None) -> dict[str, Any]:
    """Recall / precision / false flags over rows labelled correct or wrong; flags on the other
    labels counted apart; suggested-owner accuracy on wrong and missing rows."""
    cw = [r for r in rows if r["label"] in ("correct", "wrong")]
    wrong = [r for r in cw if r["label"] == "wrong"]
    right = [r for r in cw if r["label"] == "correct"]
    flagged = [r for r in cw if r[flag_key]]
    tp = sum(r[flag_key] for r in wrong)
    out: dict[str, Any] = {
        "recall": _rate(tp, len(wrong)),
        "precision": _rate(tp, len(flagged)),
        "false_flags": _rate(sum(r[flag_key] for r in right), len(right)),
        "flagged_by_label": {
            lab: _rate(
                sum(bool(r.get(flag_key)) for r in rows if r["label"] == lab),
                sum(r["label"] == lab for r in rows),
            )
            for lab in LABELS
        },
    }
    if sugg_key:
        for lab in ("wrong", "missing", "ambiguous", "correct"):
            rs = [r for r in rows if r["label"] == lab and r.get(sugg_key) is not None]
            given = [r for r in rs if r[sugg_key]]
            out[f"suggested_accuracy_{lab}"] = _rate(
                sum(suggested_right(r, r[sugg_key]) for r in rs), len(rs)
            )
            # among the rows where the check named an owner (it may leave the field empty)
            out[f"suggested_precision_{lab}"] = _rate(
                sum(suggested_right(r, r[sugg_key]) for r in given), len(given)
            )
    return out


def go(cond: Mapping[str, Any], rule: Mapping[str, Any]) -> dict[str, Any]:
    """The go thresholds for one condition vs the rule."""
    rec = cond["recall"]["rate"]
    ff = cond["false_flags"]["rate"]
    rrec = rule["recall"]["rate"] or 0.0
    rff = rule["false_flags"]["rate"] or 0.0
    beats = rec is not None and ff is not None and (rec > rrec and ff <= max(rff, GO_FALSE_FLAGS))
    ok = rec is not None and ff is not None
    return {
        "recall_ge_0.60": ok and rec >= GO_RECALL,
        "false_flags_le_0.10": ok and ff <= GO_FALSE_FLAGS,
        "beats_rule": beats,
        "go": ok and rec >= GO_RECALL and ff <= GO_FALSE_FLAGS and beats,
    }


# -- quote ------------------------------------------------------------------------------------


@dataclass
class Quote:
    calls: int = 0
    cached: int = 0
    input_tokens: int = 0
    usd_expected: float = 0.0
    usd_high: float = 0.0
    usd_expected_cached: float = 0.0
    usd_high_cached: float = 0.0
    per_model: dict[str, dict[str, float]] = field(default_factory=dict)


def quote(
    jobs: Sequence[tuple[str, LLMRequest]],
    clients: Mapping[str, LLMClient],
    counter: Any,
    out: tuple[float, float] = ASSUMED_OUT,
) -> Quote:
    """Price ``jobs`` ((model key, request)). Input exact (``counter``) + :data:`OVERHEAD`;
    output ASSUMED. Cached calls cost $0. The cached price replays the uncached calls of each
    model through a warm prompt cache (system + schema shared)."""
    q = Quote()
    sys_tokens = counter([load_prompt(PROMPT)])[0] + OVERHEAD
    for key in dict.fromkeys(k for k, _ in jobs):
        c = clients[key]
        reqs = [r for k, r in jobs if k == key]
        todo = [r for r in reqs if not c.is_cached(r)]
        q.calls += len(reqs)
        q.cached += len(reqs) - len(todo)
        tin = sum(counter([f"{r.system}\n\n{r.user}" for r in todo])) + OVERHEAD * len(todo)
        lo = cost_usd(c.model, tin, round(out[0] * len(todo)))
        hi = cost_usd(c.model, tin, round(out[1] * len(todo)))
        est = simulate_prompt_cache(c.model, [PromptShape((("sys", sys_tokens),))] * len(todo))
        saving = est.saving_usd(c.model)
        q.input_tokens += tin
        q.usd_expected += lo
        q.usd_high += hi
        q.usd_expected_cached += lo - saving
        q.usd_high_cached += hi - saving
        q.per_model[key] = {
            "calls": len(reqs),
            "uncached": len(todo),
            "input_tokens": tin,
            "usd_expected": lo,
            "usd_high": hi,
            "usd_high_cached": hi - saving,
        }
    return q


def reprice(
    ledger: Sequence[Mapping[str, Any]],
    uncached: Mapping[str, int],
    models: Mapping[str, str],
    margin: float = MARGIN,
) -> dict[str, Any]:
    """Full-run price from the pilot ledger: mean recorded cost per call of each model times
    that model's uncached calls in the full run, times ``margin``."""
    out: dict[str, Any] = {"margin": margin, "per_model": {}}
    total = 0.0
    for key, model in models.items():
        costs = [
            float(r["cost_usd"]) for r in ledger if r["stage"] == STAGE and r["model"] == model
        ]
        per = statistics.fmean(costs) if costs else 0.0
        usd = per * uncached.get(key, 0)
        out["per_model"][key] = {
            "pilot_calls": len(costs),
            "usd_per_call": per,
            "full_uncached_calls": uncached.get(key, 0),
            "full_usd": usd,
        }
        total += usd
    out["full_usd"] = total
    out["full_usd_with_margin"] = total * margin
    return out


# -- spot-check sensitivity ------------------------------------------------------------------

# Gold owners the spot check (10 labels read against AMI's transcript) found wrong. Primary labels
# use AMI's original gold; these give a sensitivity label only. (meeting, gold action) -> roles.
SPOT_CHECK_GOLD: dict[tuple[str, str], frozenset[str]] = {
    # ES2006a: the gold swaps the designers' tasks. The UI designer says "at first I will be
    # responsible for ... designing the technical functions"; the ID answers "Alright" to the
    # PM's "you're looking at the working design".
    ("ES2006a", "A2"): frozenset({"ID"}),
    ("ES2006a", "A3"): frozenset({"UI"}),
}
# IS1000a A1/A2 may be the same swap (back-channels only: ID "Mm-hmm" after "work on a design,
# keep it general"; UI "Yeah, sure" after "technical function design"); left as in the gold and
# reported as doubtful.
DOUBTFUL_GOLD = {("IS1000a", "A1"), ("IS1000a", "A2")}


def spot_label(item: OwnerItem) -> str:
    """The item's label with :data:`SPOT_CHECK_GOLD` applied (its own label otherwise)."""
    hits = [(item.meeting_id, g) for g in item.gold_ids]
    if not any(h in SPOT_CHECK_GOLD for h in hits) or item.label in ("missing", "ambiguous"):
        return item.label
    if item.gold_roles and item.gold_roles[0] in ("external", "unspecified"):
        return item.label
    gold: set[str] = set()
    for h in hits:
        if h in SPOT_CHECK_GOLD:
            gold |= SPOT_CHECK_GOLD[h]
        else:  # an unchanged linked action keeps its owner: not tracked per action here
            return item.label
    res = Resolved(frozenset(item.stated_roles), item.unresolved, False)
    return label(res, frozenset(gold))


# -- the run ----------------------------------------------------------------------------------


def load_e3_records(results_dir: Any, runs: Sequence[str] = E3_RUNS) -> dict[tuple[str, str], Any]:
    """(meeting, product) -> E3 record from the saved full runs (later runs win)."""
    import json
    from pathlib import Path

    out: dict[tuple[str, str], Any] = {}
    for r in runs:
        for rec in json.loads((Path(results_dir) / f"{r}.json").read_text())["records"]:
            out[(rec["meeting_id"], rec["product"])] = rec
    return out


def build_dataset(
    units: Sequence[Any], records: Mapping[tuple[str, str], Any]
) -> tuple[list[OwnerItem], int]:
    """Owner items of every unit with an E3 record, and the count of action-linked bullets left
    out because their majority kind is not action."""
    items: list[OwnerItem] = []
    excluded = 0
    for u in units:
        rec = records.get((u.meeting_id, u.product))
        if rec is None:
            continue
        items += build_items(u, rec)
        excluded += excluded_links(u, rec)
    return items, excluded


@dataclass(frozen=True)
class Job:
    model: str  # MODELS key
    variant: str  # VARIANTS
    item: OwnerItem
    request: LLMRequest


def make_jobs(
    items: Sequence[OwnerItem],
    units: Mapping[str, Any],
    models: Sequence[str],
    variants: Sequence[str] = VARIANTS,
) -> list[Job]:
    """One check call per item x variant x model, ordered by meeting, product, variant, model
    (calls on one meeting run back to back). ``units``: unit key -> ``notes_completeness.Unit``;
    the product variant reads ``unit.transcript``, the oracle ``unit.reference``."""
    jobs: list[Job] = []
    for it in items:
        u = units[f"{it.meeting_id}:{it.product}"]
        roster = series_roster(it.meeting_id)
        for v in variants:
            lines = u.transcript if v == "product" else u.reference
            req = check_request(it, roster, lines, v)
            jobs += [Job(m, v, it, req) for m in models]
    return jobs


def condition(model: str, variant: str) -> str:
    return f"{model}_{variant}"


def run_jobs(jobs: Sequence[Job], clients: Mapping[str, LLMClient]) -> dict[str, dict[str, Any]]:
    """Run every job; item key -> condition -> verdict fields. Raises BudgetExceeded from the
    client when the cap is reached (completed calls stay in the disk cache)."""
    out: dict[str, dict[str, Any]] = {}
    for j in jobs:
        v = run_check(clients[j.model], j.request)
        out.setdefault(j.item.key, {})[condition(j.model, j.variant)] = {
            "verdict": v.verdict,
            "suggested": list(v.suggested),
            "reason": v.reason,
        }
    return out


def records_of(
    items: Sequence[OwnerItem], verdicts: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """One JSON-ready row per item (holds notes text: local only): the item, its spot-check
    label, the rule flag, and each LLM condition's verdict."""
    rows = []
    for it in items:
        row = asdict(it)
        row["spot_label"] = spot_label(it)
        row["doubtful_gold"] = any((it.meeting_id, g) in DOUBTFUL_GOLD for g in it.gold_ids)
        row["rule_flag"] = rule_flag(it, series_roster(it.meeting_id))
        row["checks"] = dict(verdicts.get(it.key, {}))
        rows.append(row)
    return rows


def _flat(rows: Sequence[Mapping[str, Any]], cond: str, label_key: str) -> list[dict[str, Any]]:
    """Rows as :func:`score` reads them for one condition (``rule`` or an LLM condition)."""
    out = []
    for r in rows:
        base = {"label": r[label_key], "gold_roles": r["gold_roles"]}
        if cond == "rule":
            out.append({**base, "flag": r["rule_flag"], "flag_soft": r["rule_flag"], "sugg": None})
            continue
        c = r["checks"].get(cond)
        if c is None:
            continue
        out.append(
            {
                **base,
                "flag": c["verdict"] == "wrong",
                "flag_soft": c["verdict"] != "correct",
                "sugg": c["suggested"],
            }
        )
    return out


def summarize(rows: Sequence[Mapping[str, Any]], conditions: Sequence[str]) -> dict[str, Any]:
    """Per label set (original gold, spot-check) x product (and all) x condition: :func:`score`
    with flag = "wrong" verdict, plus ``soft`` (flag = wrong or can't tell); go vs the rule."""
    out: dict[str, Any] = {}
    for label_key in ("label", "spot_label"):
        per: dict[str, Any] = {}
        for prod in (*sorted({r["product"] for r in rows}), "all"):
            sub = [r for r in rows if prod in ("all", r["product"])]
            res: dict[str, Any] = {}
            for cond in ("rule", *conditions):
                flat = _flat(sub, cond, label_key)
                if not flat:
                    continue
                res[cond] = {
                    "n_rows": len(flat),
                    "strict": score(flat, "flag", None if cond == "rule" else "sugg"),
                    "soft": score(flat, "flag_soft", None),
                }
            for cond in conditions:
                if cond in res:
                    res[cond]["go"] = go(res[cond]["strict"], res["rule"]["strict"])
            per[prod] = res
        out[label_key] = per
    return out


def private_texts(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Notes text and model reasons that must not reach a committed file."""
    out = [r["text"] for r in rows]
    out += [c["reason"] for r in rows for c in r["checks"].values()]
    return out


def main(
    units: Sequence[Any],
    *,
    results_dir: Any,
    out_dir: Any,
    quote_only: bool,
    pilot: bool,
    max_usd: float | None,
    models: Sequence[str],
    sonnet_items: int | None,
    seed: int,
    confirm: Any,
    echo: Any,
    commit_to: Any = None,
) -> int:
    """The ``tenor ami owner-check`` run: dataset, quote, (pilot or full) checks, result and
    numbers-only aggregate. ``confirm(text) -> bool``; ``echo(text)``. Returns an exit code."""
    import functools
    from datetime import UTC, datetime
    from pathlib import Path

    from tenor.blackbox.kit import check_committable
    from tenor.evals.list_check import write_json
    from tenor.evals.runner import _git
    from tenor.llm.client import BudgetExceeded, Ledger, count_tokens

    records = load_e3_records(results_dir)
    items, excluded = build_dataset(units, records)
    by_key = {u.key: u for u in units}
    test = [i for i in items if i.split == "test"]
    dev = [i for i in items if i.meeting_id == PILOT_MEETING]
    echo(
        f"owner dataset: {len(test)} TEST bullets, {len(dev)} DEV ({PILOT_MEETING}); "
        f"{excluded} action-linked bullets of another kind left out"
    )
    for name, xs in (("TEST", test), ("DEV", dev)):
        echo(f"  {name}: {label_counts(xs)}")
    cap = max_usd if max_usd is not None else (PILOT_MAX_USD if pilot else None)
    if cap is None and not quote_only:
        echo("paid runs need --max-usd (the approved amount); --pilot first")
        return 2
    ledger = Ledger()
    clients = {
        k: LLMClient(model=MODELS[k], ledger=ledger, max_usd=cap, offline=quote_only)
        for k in models
    }
    any_c = next(iter(clients.values()))
    counter = functools.partial(count_tokens, model=MODELS["sonnet"], cache_dir=any_c.cache_dir)

    def jobs_for(xs: Sequence[OwnerItem]) -> list[Job]:
        js = make_jobs(xs, by_key, models)
        if sonnet_items is not None:  # Sonnet on the first N items only (meeting order)
            keep = {i.key for i in xs[:sonnet_items]}
            js = [j for j in js if j.model != "sonnet" or j.item.key in keep]
        return js

    full_jobs, pilot_jobs = jobs_for(test), make_jobs(dev, by_key, models)
    fq = quote([(j.model, j.request) for j in full_jobs], clients, counter)
    pq = quote([(j.model, j.request) for j in pilot_jobs], clients, counter)
    for lab, q in (("full TEST", fq), ("pilot (DEV)", pq)):
        echo(
            f"{lab}: {q.calls} calls ({q.cached} cached), {q.input_tokens:,} input tokens "
            f"(exact, counted with the Sonnet tokenizer). ${q.usd_expected:.3f} expected, "
            f"${q.usd_high:.3f} high; with prompt cache ${q.usd_high_cached:.3f} high "
            f"(output ASSUMED {ASSUMED_OUT}/call). Per model: "
            + ", ".join(
                f"{k} {v['uncached']:.0f} calls ${v['usd_high']:.3f} high"
                for k, v in q.per_model.items()
            )
        )
    if quote_only:
        echo("no money spent (token counting is free)")
        return 0
    assert cap is not None
    run, q, run_items = (pilot_jobs, pq, dev) if pilot else (full_jobs, fq, test)
    if q.usd_high > cap:
        echo(f"high estimate ${q.usd_high:.2f} exceeds the cap ${cap:.2f}")
        return 1
    if not confirm(
        f"{'PILOT: ' if pilot else ''}{len(run)} owner-check calls (hard cap "
        f"${cap:.2f}, quoted high ${q.usd_high:.2f})?"
    ):
        return 1
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    stopped = None
    verdicts: dict[str, dict[str, Any]] = {}
    try:
        verdicts = run_jobs(run, clients)
    except BudgetExceeded as e:
        stopped = str(e)
        verdicts = run_jobs(
            [j for j in run if clients[j.model].is_cached(j.request)],
            {k: LLMClient(model=MODELS[k], ledger=Ledger(), offline=True) for k in models},
        )
    conds = [condition(m, v) for v in VARIANTS for m in models]
    rows = records_of(run_items, verdicts)
    summary = summarize(rows, conds)
    reprice_full = None
    if pilot:
        uncached = Counter(j.model for j in full_jobs if not clients[j.model].is_cached(j.request))
        reprice_full = reprice(ledger.to_json(), uncached, {k: MODELS[k] for k in models})
    meta = {
        "date": stamp,
        "git": _git(),
        "seed": seed,
        "models": {k: MODELS[k] for k in models},
        "prompt": PROMPT,
        "e3_runs": list(E3_RUNS),
        "e3_prompt": "notes_completeness.v4",
        "gold_version": "ami-1.6.2 (original AMI gold; no errata file existed when this "
        "experiment started)",
        "spot_check_gold": "SPOT_CHECK_GOLD (2 owner corrections, ES2006a), sensitivity only",
        "window": WINDOW,
        "anchors": ANCHORS,
        "pilot": pilot,
        "sonnet_items": sonnet_items,
        "max_usd": cap,
        "stopped_early": stopped,
        "spent_usd": ledger.spent_usd(),
        # every call the result rests on at its original price (cached replays included)
        "cost_all_calls_usd": sum(float(r["cost_usd"]) for r in ledger.to_json()),
        "ledger_by_stage": ledger.by_stage(),
        "quote": {
            "usd_expected": q.usd_expected,
            "usd_high": q.usd_high,
            "usd_high_cached": q.usd_high_cached,
        },
        "reprice": reprice_full,
    }
    result = {"meta": meta, "summary": summary, "records": rows, "ledger": ledger.to_json()}
    out_dir = Path(out_dir)
    suffix = ".pilot" if pilot else ""
    write_json(out_dir / f"{stamp}{suffix}.json", result)
    agg = {
        "meta": {k: v for k, v in meta.items()},
        "label_counts": {"test": label_counts(test), "dev": label_counts(dev)},
        "spot_label_counts": dict(Counter(r["spot_label"] for r in rows)),
        "excluded_non_action_links": excluded,
        "summary": summary,
    }
    check_committable(agg, private_texts(rows))
    write_json(out_dir / f"{stamp}{suffix}.aggregate.json", agg)
    echo(f"spent ${ledger.spent_usd():.4f} (cap ${cap:.2f}) -> {out_dir / (stamp + suffix)}.json")
    if commit_to is not None and not pilot and not stopped:
        write_json(Path(commit_to), agg)
        echo(f"aggregate (numbers only): {commit_to}")
    if reprice_full:
        echo(
            f"full run re-priced from the pilot ledger: ${reprice_full['full_usd']:.3f}, "
            f"${reprice_full['full_usd_with_margin']:.3f} with x{MARGIN} margin"
        )
    if stopped:
        echo(f"STOPPED: {stopped}")
        return 1
    return 0
