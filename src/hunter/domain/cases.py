"""Case claims registry and the "planned is not done" rule. (H-04)

Doc 01 "Реестр доказательств" and doc 10 case_claims: only an active, externally allowed claim
version may be used in messages; a `planned` claim must never be presented as a result.
Only `verified` claims support Problem Fit 20 (doc 04).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from hunter.domain.listings import normalize_text


class ClaimStatus(StrEnum):
    OWNER_REPORTED = "owner_reported"
    VERIFIED = "verified"
    PLANNED = "planned"
    WITHDRAWN = "withdrawn"


@dataclass(frozen=True, slots=True)
class CaseClaim:
    id: str
    case_key: str
    claim_text: str
    status: ClaimStatus
    external_allowed: bool
    claim_version: int
    source_ref: str | None = None
    approved_by: str | None = None

    def __post_init__(self) -> None:
        if not self.claim_text.strip():
            raise ValueError("claim text is required")
        if self.claim_version < 1:
            raise ValueError("claim_version starts at 1")

    @property
    def usable_in_messages(self) -> bool:
        return self.external_allowed and self.status in (
            ClaimStatus.OWNER_REPORTED,
            ClaimStatus.VERIFIED,
            ClaimStatus.PLANNED,  # only in its allowed wording; see presents_planned_as_done
        )

    @property
    def supports_problem_fit_20(self) -> bool:
        return self.status is ClaimStatus.VERIFIED


# Doc 01, case 4: the claims assistant has only a Telegram bot shell; the rest is in development.
_CLAIMS_ASSISTANT = re.compile(r"претензи|должник|взыскан")
_IN_DEVELOPMENT = re.compile(r"в разработке|разрабатываем|оболочк")
_DONE_WORDING = re.compile(
    r"готов\w* (mvp|продукт|решени|сервис|систем)|внедрил|сэконом|экономи[тя]|excel-отчет|"
    r"работающ\w* интеграц|автоматизировал\w* проверк"
)


_OUR_EXPERIENCE = re.compile(
    r"\bмы\b|\bнаш|\bу нас\b|сделали|разработали|запустили|внедрили|реализовали|есть опыт"
)


def presents_planned_as_done(text: str) -> bool:
    """T-07: our planned claims-assistant functionality is described as delivered.

    Only sentences about our own experience count: a message to a law firm may mention
    the prospect's claims work freely.
    """
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", normalize_text(text)):
        if not (_CLAIMS_ASSISTANT.search(sentence) and _OUR_EXPERIENCE.search(sentence)):
            continue
        if not _IN_DEVELOPMENT.search(sentence) or _DONE_WORDING.search(sentence):
            return True
    return False
