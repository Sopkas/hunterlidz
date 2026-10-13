"""Case claims registry and the "cases are quoted verbatim" rule. (S2-03, T-07)

Doc 01 "Реестр доказательств", doc 10 case_claims. A message may speak about our cases only
by quoting the approved wording of an attached claim, alone in its own paragraph, with no
lead-in before it and no continuation after it. Any other mention of a registered case blocks
approval until the text is edited. The rule does not try to judge whether a wording presents
planned work as done: it never has to.

Boundaries: pure text and value checks; no storage, no network, no clock.
Limits: mentions are found by the markers stored with each claim, so a paraphrase that avoids
every marker is not detected. Human approval stays mandatory; this check is a safety net.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

RULES_VERSION = "case_text_v1"

_NBSP = chr(0x00A0)
_NARROW_NBSP = chr(0x202F)
_SOFT_HYPHEN = chr(0x00AD)
_ZERO_WIDTH = tuple(chr(code) for code in (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF))
_QUOTES = "«»“”„\"'"
_DASHES = "—–−"
_WORD_CHARS = "а-яa-z0-9"
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_PARAGRAPH_BREAK = re.compile(r"\s*\n\s*")
_TERMINATED = re.compile(r"[.!?…][)\]»”\"']*$")
# A sentence or paragraph that opens with one of these continues the previous one:
# "Он уже умеет ...", "Уже сокращает ..." (subject left out).
_CONTINUATION = re.compile(
    r"(?:он|она|оно|они|его|ее|ему|ей|им|их|ими|нем|ней|них|этот|эта|эти|этого|этой|этих|"
    r"такой|такая|такие|уже)(?![" + _WORD_CHARS + "])"
)


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
    LEADS_INTO_CASE = "leads_into_case"


def normalize_text(value: str) -> str:
    """Canonical form for comparing wordings: case, ё, spaces, quotes and dashes are ignored."""
    text = value.lower().replace("ё", "е")
    for char in (_SOFT_HYPHEN, *_ZERO_WIDTH):
        text = text.replace(char, "")
    for char in (_NBSP, _NARROW_NBSP, "\t"):
        text = text.replace(char, " ")
    for char in _QUOTES:
        text = text.replace(char, "")
    for char in _DASHES:
        text = text.replace(char, "-")
    return re.sub(r" +", " ", text).strip()


def _wording_key(paragraph: str) -> str:
    return normalize_text(paragraph).rstrip(" .")


def _starts_word(stem: str) -> re.Pattern[str]:
    return re.compile(r"(?<![" + _WORD_CHARS + "])" + re.escape(stem))


def _whole_phrase(phrase: str) -> re.Pattern[str]:
    return re.compile(
        r"(?<![" + _WORD_CHARS + "])" + re.escape(phrase) + r"(?![" + _WORD_CHARS + "])"
    )


@dataclass(frozen=True, slots=True)
class MentionRule:
    """How a case is recognised in free text.

    A sentence mentions the case when it contains one of `phrases`, or one of `objects`
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

    def matches(self, sentence: str) -> bool:
        """`sentence` must already be normalized."""
        if any(_whole_phrase(phrase).search(sentence) for phrase in self.phrases):
            return True
        return any(_starts_word(stem).search(sentence) for stem in self.objects) and any(
            _starts_word(stem).search(sentence) for stem in self.topics
        )


@dataclass(frozen=True, slots=True)
class CaseClaim:
    id: str
    case_key: str
    claim_text: str
    status: ClaimStatus
    external_allowed: bool
    claim_version: int
    mention: MentionRule = MentionRule()
    source_ref: str | None = None
    approved_by: str | None = None

    def __post_init__(self) -> None:
        if not self.case_key.strip():
            raise ValueError("case_key is required")
        if not self.claim_text.strip():
            raise ValueError("claim text is required")
        if "\n" in self.claim_text.strip():
            raise ValueError("claim text is one paragraph: no line breaks")
        if self.claim_version < 1:
            raise ValueError("claim_version starts at 1")
        if self.mention.is_empty:
            if self.status is ClaimStatus.PLANNED:
                raise ValueError("a planned claim needs mention markers (T-07)")
        elif not _mentions(self.mention, self.claim_text):
            # Otherwise the markers describe some other text and the check would see nothing.
            raise ValueError("mention markers do not recognise the claim's own wording")

    @property
    def usable_in_messages(self) -> bool:
        return self.external_allowed and self.status is not ClaimStatus.WITHDRAWN


@dataclass(frozen=True, slots=True)
class Violation:
    code: ViolationCode
    case_key: str
    fragment: str


def claim_from_mapping(item: Mapping[str, object], *, claim_version: int = 1) -> CaseClaim:
    """Build a claim from one registry file entry; a malformed entry raises ValueError."""
    try:
        case_key = str(item["case_key"]).strip()
        claim_text = str(item["claim_text"]).strip()
        status = ClaimStatus(str(item["status"]))
        external_allowed = item["external_allowed"]
    except KeyError as exc:
        raise ValueError(f"missing field {exc}") from None
    if not isinstance(external_allowed, bool):
        raise ValueError("external_allowed must be true or false")
    return CaseClaim(
        id=f"{case_key}:v{claim_version}",
        case_key=case_key,
        claim_text=claim_text,
        status=status,
        external_allowed=external_allowed,
        claim_version=claim_version,
        mention=_mention_from(item.get("mention")),
        source_ref=_optional(item.get("source_ref")),
        approved_by=_optional(item.get("approved_by")),
    )


def check_case_text(
    text: str, *, registry: Sequence[CaseClaim], attached_ids: Collection[str] = ()
) -> tuple[Violation, ...]:
    """Find everything that must block approval of `text` (T-07).

    `registry` holds the active version of every case, withdrawn and internal ones included:
    they must not be mentioned either. `attached_ids` are the claims this message version
    refers to. For text written by the model pass no attached ids: it must not mention cases
    at all, the approved wording is inserted by code.
    """
    by_id = {claim.id: claim for claim in registry}
    by_wording = {_wording_key(claim.claim_text): claim for claim in registry}
    violations: list[Violation] = []
    for claim_id in sorted(set(attached_ids)):
        claim = by_id.get(claim_id)
        if claim is None or not claim.usable_in_messages:
            case_key = claim.case_key if claim else claim_id
            violations.append(Violation(ViolationCode.CLAIM_NOT_USABLE, case_key, claim_id))

    after_case: CaseClaim | None = None
    before_case: str | None = None
    statements: list[str] = []
    open_statement = False
    for paragraph in _PARAGRAPH_BREAK.split(text.strip()):
        quoted = by_wording.get(_wording_key(paragraph))
        if quoted is not None:
            if quoted.id not in attached_ids:
                violations.append(
                    Violation(ViolationCode.CASE_NOT_ATTACHED, quoted.case_key, paragraph)
                )
            # "Вот что у нас уже работает:" before the wording changes what it says.
            if before_case is not None and normalize_text(before_case).endswith(":"):
                violations.append(
                    Violation(ViolationCode.LEADS_INTO_CASE, quoted.case_key, before_case)
                )
            after_case = quoted
            before_case = None
            open_statement = False
            continue
        before_case = paragraph
        if after_case is not None and _CONTINUATION.match(normalize_text(paragraph)):
            violations.append(
                Violation(ViolationCode.CONTINUES_CASE, after_case.case_key, paragraph)
            )
        after_case = None
        for sentence in filter(None, map(str.strip, _SENTENCE_END.split(paragraph))):
            # One statement may span sentences and lines: "Делаем помощника юристам. Он уже
            # ранжирует должников." names the object and the topic in different sentences.
            if open_statement and (
                _CONTINUATION.match(normalize_text(sentence))
                or not _TERMINATED.search(statements[-1])
            ):
                statements[-1] = f"{statements[-1]} {sentence}"
            else:
                statements.append(sentence)
            open_statement = True
    for statement in statements:
        normalized = normalize_text(statement)
        for claim in registry:
            if claim.mention.matches(normalized):
                violations.append(Violation(ViolationCode.CASE_MENTION, claim.case_key, statement))
    return tuple(violations)


def _mentions(rule: MentionRule, text: str) -> bool:
    return any(rule.matches(normalize_text(s)) for s in _SENTENCE_END.split(text))


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


def _strings(raw: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ValueError(f"mention.{key}: expected a list of strings")
    return tuple(value)


def _optional(value: object) -> str | None:
    return str(value).strip() or None if value is not None else None
