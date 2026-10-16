"""Case claims registry and the "cases only in approved wording" rule. (S2-03, T-07)

Doc 01 "Реестр доказательств", doc 05 message templates, doc 10 case_claims. A message may
speak about our cases only through the approved wording of an attached claim. The wording of
a done case may sit inside a sentence ("У нас есть опыт разработки CRM для автомобильного
бизнеса. Можем разобрать процесс..."). The wording of a planned case stands alone: nothing may
be added to its sentence or continue it, so planned work is never presented as done. Any
other mention of a registered case blocks approval until the text is edited.

Boundaries: pure text and value checks; no storage, no network, no clock.
Limits: mentions are found by the markers stored with each case, so a paraphrase that avoids
every marker is not detected, and a done-case sentence may still add a module that is not in
the registry and not listed as unconfirmed.
Human approval stays mandatory; this check is a safety net.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum

RULES_VERSION = "case_text_v2"

_NBSP = chr(0x00A0)
_NARROW_NBSP = chr(0x202F)
_SOFT_HYPHEN = chr(0x00AD)
_ZERO_WIDTH = tuple(chr(code) for code in (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF))
_QUOTES = "«»“”„\"'"
_DROPPED = frozenset((_SOFT_HYPHEN, *_ZERO_WIDTH, *_QUOTES))
_SPACES = frozenset((" ", _NBSP, _NARROW_NBSP, "\t"))
_DASHES = frozenset("—–−")
_WORD_CHARS = "а-яa-z0-9"
_WORD = re.compile("[" + _WORD_CHARS + "]")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_PARAGRAPH_BREAK = re.compile(r"\s*\n\s*")
_TERMINATED = re.compile(r"[.!?…][)\]»”\"']*$")
# A sentence that opens with one of these continues the previous one:
# "Он уже умеет ...", "Уже сокращает ..." (subject left out), "Которая ведёт ...".
_CONTINUATION = re.compile(
    r"(?:он|она|оно|они|его|ее|ему|ей|им|их|ими|нем|ней|них|этот|эта|эти|этого|этой|этих|"
    r"такой|такая|такие|который|которая|которое|которые|которого|которой|которых|уже)"
    r"(?![" + _WORD_CHARS + "])"
)
# While the text is split, each quoted wording is replaced by one private-use character.
_PLACEHOLDER_BASE = 0xE000


class ClaimStatus(StrEnum):
    OWNER_REPORTED = "owner_reported"
    VERIFIED = "verified"
    PLANNED = "planned"
    WITHDRAWN = "withdrawn"


class ViolationCode(StrEnum):
    CASE_MENTION = "case_mention"
    CASE_NOT_ATTACHED = "case_not_attached"
    CLAIM_NOT_USABLE = "claim_not_usable"
    CONTINUES_CASE = "continues_case"
    PLANNED_NOT_ALONE = "planned_not_alone"
    NOT_CONFIRMED = "not_confirmed"


def _normalize_mapped(value: str) -> tuple[str, list[int]]:
    """Normalized text and, for each of its characters, its index in `value`."""
    chars: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(value):
        if char in _DROPPED:
            continue
        if char in _SPACES:
            if chars and chars[-1] != " ":
                chars.append(" ")
                origin.append(index)
            continue
        replaced = "-" if char in _DASHES else char.lower().replace("ё", "е")
        chars.extend(replaced)
        origin.extend([index] * len(replaced))
    while chars and chars[-1] == " ":
        chars.pop()
        origin.pop()
    return "".join(chars), origin


def normalize_text(value: str) -> str:
    """Canonical form for comparing wordings: case, ё, spaces, quotes and dashes are ignored."""
    return _normalize_mapped(value)[0].strip()


def _wording_key(text: str) -> str:
    return normalize_text(text).rstrip(" .")


def _starts_word(stem: str) -> re.Pattern[str]:
    return re.compile(r"(?<![" + _WORD_CHARS + "])" + re.escape(stem))


def _whole_phrase(phrase: str) -> re.Pattern[str]:
    return re.compile(
        r"(?<![" + _WORD_CHARS + "])" + re.escape(phrase) + r"(?![" + _WORD_CHARS + "])"
    )


@dataclass(frozen=True, slots=True)
class MentionRule:
    """How a case is recognised in free text.

    A statement mentions the case when it contains one of `phrases`, or one of `objects`
    together with one of `topics`. Phrases are matched as whole words ("приложение для пар"
    does not fire on "приложение для партнёров"). Objects and topics are stems matched at the
    start of a word, so "бот" fires on "ботом" but not on "работы".
    """

    phrases: tuple[str, ...] = ()
    objects: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("phrases", "objects", "topics"):
            cleaned = tuple(normalize_text(marker) for marker in getattr(self, name))
            if any(len(marker) < 3 for marker in cleaned):
                raise ValueError(f"{name}: a marker needs at least 3 characters")
            object.__setattr__(self, name, cleaned)
        if bool(self.objects) != bool(self.topics):
            raise ValueError("objects and topics work as pairs: give both or neither")

    @property
    def is_empty(self) -> bool:
        return not (self.phrases or self.objects)

    def matches(self, statement: str) -> bool:
        """`statement` must already be normalized."""
        if any(_whole_phrase(phrase).search(statement) for phrase in self.phrases):
            return True
        return any(_starts_word(stem).search(statement) for stem in self.objects) and any(
            _starts_word(stem).search(statement) for stem in self.topics
        )


@dataclass(frozen=True, slots=True)
class Case:
    """One of our projects (doc 01) and how free text refers to it.

    `not_confirmed` lists what the case has not proven: planned features of a case in
    development ("Excel", "ИНН") or an achievement without a source (the CareerMate contest).
    A message that quotes the case must not name them anywhere (T-07: "планируемый Excel в
    кейсе назван готовым"). A marker is a whole word or phrase; a trailing "*" makes it a stem
    matched at the start of a word ("отчет*" fires on "отчёты").
    """

    case_key: str
    mention: MentionRule = MentionRule()
    not_confirmed: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.case_key.strip():
            raise ValueError("case_key is required")
        cleaned = tuple(normalize_text(marker) for marker in self.not_confirmed)
        if any(len(marker.rstrip("*")) < 3 for marker in cleaned):
            raise ValueError("not_confirmed: a marker needs at least 3 characters")
        object.__setattr__(self, "not_confirmed", cleaned)

    def unconfirmed_in(self, statement: str) -> bool:
        """`statement` must already be normalized."""
        return any(
            (_starts_word(m[:-1]) if m.endswith("*") else _whole_phrase(m)).search(statement)
            for m in self.not_confirmed
        )


@dataclass(frozen=True, slots=True)
class CaseClaim:
    """One approved way to speak about a case; a case may have several (doc 01, claim_id)."""

    claim_key: str
    case_key: str
    claim_text: str
    status: ClaimStatus
    external_allowed: bool
    claim_version: int
    source_ref: str | None = None
    approved_by: str | None = None

    def __post_init__(self) -> None:
        if not self.claim_key.strip():
            raise ValueError("claim_key is required")
        if not self.case_key.strip():
            raise ValueError("case_key is required")
        if not _wording_key(self.claim_text):
            raise ValueError("claim text is required")
        if "\n" in self.claim_text.strip():
            raise ValueError("claim text is one paragraph: no line breaks")
        if self.claim_version < 1:
            raise ValueError("claim_version starts at 1")

    @property
    def id(self) -> str:
        return f"{self.claim_key}:v{self.claim_version}"

    @property
    def usable_in_messages(self) -> bool:
        """Doc 10: only an approved active version allowed for external use takes part."""
        return (
            self.external_allowed
            and bool(self.approved_by)
            and self.status is not ClaimStatus.WITHDRAWN
        )


@dataclass(frozen=True, slots=True)
class CaseRegistry:
    """Active versions of every case and claim, withdrawn and internal ones included:
    they must not be mentioned either."""

    cases: tuple[Case, ...]
    claims: tuple[CaseClaim, ...]

    def __post_init__(self) -> None:
        by_key = {case.case_key: case for case in self.cases}
        if len(by_key) != len(self.cases):
            raise ValueError("case_key must be unique")
        if len({claim.id for claim in self.claims}) != len(self.claims):
            raise ValueError("claim id must be unique")
        if len({_wording_key(claim.claim_text) for claim in self.claims}) != len(self.claims):
            raise ValueError("two claims have the same wording")
        for claim in self.claims:
            case = by_key.get(claim.case_key)
            if case is None:
                raise ValueError(f"claim {claim.id}: unknown case {claim.case_key!r}")
            if case.mention.is_empty:
                if claim.status is ClaimStatus.PLANNED:
                    raise ValueError(f"case {case.case_key}: a planned claim needs markers (T-07)")
            elif not case.mention.matches(normalize_text(claim.claim_text)):
                # Otherwise the markers describe some other text and the check would see nothing.
                raise ValueError(f"claim {claim.id}: case markers do not recognise its wording")
            if case.unconfirmed_in(normalize_text(claim.claim_text)):
                raise ValueError(f"claim {claim.id}: its wording names what is not confirmed")

    def claim(self, claim_id: str) -> CaseClaim | None:
        return next((claim for claim in self.claims if claim.id == claim_id), None)


@dataclass(frozen=True, slots=True)
class Violation:
    code: ViolationCode
    case_key: str
    fragment: str


def registry_from_mapping(data: Mapping[str, object]) -> CaseRegistry:
    """Build the registry from the registry file; a malformed file raises ValueError."""
    unknown = set(data) - {"cases", "claims"}
    if unknown:
        raise ValueError(f"registry: unknown fields {sorted(unknown)}")
    cases = tuple(_case_from(item) for item in _items(data, "cases"))
    claims = tuple(claim_from_mapping(item) for item in _items(data, "claims"))
    return CaseRegistry(cases=cases, claims=claims)


def claim_from_mapping(item: Mapping[str, object], *, claim_version: int = 1) -> CaseClaim:
    """Build a claim from one registry file entry; a malformed entry raises ValueError."""
    try:
        claim_key = str(item["claim_key"]).strip()
        case_key = str(item["case_key"]).strip()
        claim_text = str(item["claim_text"]).strip()
        status = ClaimStatus(str(item["status"]))
        external_allowed = item["external_allowed"]
    except KeyError as exc:
        raise ValueError(f"missing field {exc}") from None
    if not isinstance(external_allowed, bool):
        raise ValueError("external_allowed must be true or false")
    return CaseClaim(
        claim_key=claim_key,
        case_key=case_key,
        claim_text=claim_text,
        status=status,
        external_allowed=external_allowed,
        claim_version=claim_version,
        source_ref=_optional(item.get("source_ref")),
        approved_by=_optional(item.get("approved_by")),
    )


@dataclass(slots=True)
class _Statement:
    text: str  # quoted wordings are placeholders here
    quoted: list[CaseClaim]
    continued: bool = False  # a sentence after a quoted wording continues it


def check_case_text(
    text: str, *, registry: CaseRegistry, attached_ids: Collection[str] = ()
) -> tuple[Violation, ...]:
    """Find everything that must block approval of `text` (T-07).

    `attached_ids` are the claims this message version refers to (case_claim_ids). The same
    check runs on the model's draft and again on the operator's edit before Approve (S3-04).
    """
    violations: list[Violation] = []
    for claim_id in sorted(set(attached_ids)):
        claim = registry.claim(claim_id)
        if claim is None or not claim.usable_in_messages:
            case_key = claim.case_key if claim else claim_id
            violations.append(Violation(ViolationCode.CLAIM_NOT_USABLE, case_key, claim_id))

    originals: dict[str, str] = {}
    quoted_by: dict[str, CaseClaim] = {}
    statements = _statements(text, registry, originals, quoted_by)
    quoted_cases = {claim.case_key for claim in quoted_by.values()}
    for statement in statements:
        rest = normalize_text("".join(" " if c in quoted_by else c for c in statement.text))
        fragment = "".join(originals.get(c, c) for c in statement.text)
        for claim in dict.fromkeys(statement.quoted):
            if claim.id not in attached_ids:
                violations.append(
                    Violation(ViolationCode.CASE_NOT_ATTACHED, claim.case_key, fragment)
                )
            if claim.status is ClaimStatus.PLANNED and _WORD.search(rest):
                # "Вот что уже работает: <wording>" or "<wording>, и он уже выгружает Excel".
                violations.append(
                    Violation(ViolationCode.PLANNED_NOT_ALONE, claim.case_key, fragment)
                )
        if statement.continued:
            violations.append(
                Violation(ViolationCode.CONTINUES_CASE, statement.quoted[-1].case_key, fragment)
            )
        for case in registry.cases:
            if case.mention.matches(rest):
                violations.append(Violation(ViolationCode.CASE_MENTION, case.case_key, fragment))
            if case.case_key in quoted_cases and case.unconfirmed_in(rest):
                violations.append(Violation(ViolationCode.NOT_CONFIRMED, case.case_key, fragment))
    return tuple(violations)


def _statements(
    text: str,
    registry: CaseRegistry,
    originals: dict[str, str],
    quoted_by: dict[str, CaseClaim],
) -> list[_Statement]:
    """Split `text` into statements with quoted wordings replaced by placeholders.

    One statement may span sentences and lines: "Делаем помощника юристам. Он уже ранжирует
    должников." names the object and the topic in different sentences. A sentence joins the
    previous statement when it opens with a continuation word or the previous one is unfinished.
    """
    statements: list[_Statement] = []
    for paragraph in _PARAGRAPH_BREAK.split(text.strip()):
        masked = _mask_wordings(paragraph, registry, originals, quoted_by)
        for sentence in filter(None, map(str.strip, _SENTENCE_END.split(masked))):
            quoted = [quoted_by[c] for c in sentence if c in quoted_by]
            continues = bool(_CONTINUATION.match(normalize_text(sentence)))
            if statements and (continues or not _finished(statements[-1].text, quoted_by)):
                last = statements[-1]
                last.continued = last.continued or (continues and bool(last.quoted))
                last.text = f"{last.text} {sentence}"
                last.quoted.extend(quoted)
            else:
                statements.append(_Statement(sentence, quoted))
    return statements


def _finished(statement: str, quoted_by: Mapping[str, CaseClaim]) -> bool:
    # A wording that closes its line without a dot still closes the statement.
    return bool(_TERMINATED.search(statement)) or statement[-1] in quoted_by


def _mask_wordings(
    paragraph: str,
    registry: CaseRegistry,
    originals: dict[str, str],
    quoted_by: dict[str, CaseClaim],
) -> str:
    normalized, origin = _normalize_mapped(paragraph)
    found = [
        (match.start(), match.end(), claim)
        for claim in registry.claims
        for match in _whole_phrase(_wording_key(claim.claim_text)).finditer(normalized)
    ]
    found.sort(key=lambda item: (item[0], item[0] - item[1]))  # longest first at one place
    pieces: list[str] = []
    cursor = 0
    for start, end, claim in found:
        begin, finish = origin[start], origin[end - 1] + 1
        if begin < cursor:
            continue  # overlaps a wording already taken
        placeholder = chr(_PLACEHOLDER_BASE + len(originals))
        originals[placeholder] = paragraph[begin:finish]
        quoted_by[placeholder] = claim
        pieces += [paragraph[cursor:begin], placeholder]
        cursor = finish
    pieces.append(paragraph[cursor:])
    return "".join(pieces)


def _items(data: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise ValueError(f"registry: {key} must be a list of objects")
    return value


def _case_from(item: Mapping[str, object]) -> Case:
    unknown = set(item) - {"case_key", "title", "mention", "not_confirmed"}
    if unknown:
        raise ValueError(f"case: unknown fields {sorted(unknown)}")
    if "case_key" not in item:
        raise ValueError("missing field 'case_key'")
    return Case(
        case_key=str(item["case_key"]).strip(),
        mention=_mention_from(item.get("mention")),
        not_confirmed=_strings(item, "not_confirmed", "case"),
    )


def _mention_from(raw: object) -> MentionRule:
    if raw is None:
        return MentionRule()
    if not isinstance(raw, Mapping):
        raise ValueError("mention must be an object")
    unknown = set(raw) - {"phrases", "objects", "topics"}
    if unknown:
        raise ValueError(f"mention: unknown fields {sorted(unknown)}")
    return MentionRule(
        phrases=_strings(raw, "phrases"),
        objects=_strings(raw, "objects"),
        topics=_strings(raw, "topics"),
    )


def _strings(raw: Mapping[str, object], key: str, owner: str = "mention") -> tuple[str, ...]:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ValueError(f"{owner}.{key}: expected a list of strings")
    return tuple(value)


def _optional(value: object) -> str | None:
    return str(value).strip() or None if value is not None else None
