"""draft_v1 (doc 11 «Персонализация») and the checks that block Approve. (S3-03, FR-07)

The model returns a body with the evidence it used and the case claims it quoted, or
needs_review without a text when a fact is missing; that is a valid answer. References that
were not offered are dropped and listed. The text checks run on every version, the model's and
the operator's edit alike (S3-04); a blocker forbids approval until the text is fixed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from hunter.domain.cases import CaseRegistry, check_case_text
from hunter.domain.messages import MessageKind
from hunter.domain.research import SchemaError

SCHEMA_VERSION = "draft_v1"
FIRST_MESSAGE_LIMIT = 900  # doc 11 personalization prompt
FOLLOW_UP_LIMIT = 900
MESSAGE_HARD_LIMIT = 1_500  # doc 09 save_draft
MAX_REASONS = 5

_FIELDS = frozenset(
    {"schema_version", "body", "evidence_ids", "case_claim_ids", "needs_review", "review_reasons"}
)
# Template fields left unfilled: {company_name}, {{name}}, [Имя], <компания>.
_PLACEHOLDER = re.compile(
    r"\{\{?[^{}\n]{1,40}\}?\}|\[(?:имя|компания|должность|name)\]|<[^<>\n]{1,30}>", re.I
)
# Service references leaked into the text: E3, [F1], auto_crm.experience:v1, a document name.
_REFERENCE = re.compile(
    r"\[[^\]\n]*\]|(?<![\w-])E\d{1,4}(?![\w-])|\b[a-z_]+\.[a-z_]+:v\d+\b|\b\d\d-[a-z-]+\.md\b"
)


@dataclass(frozen=True, slots=True)
class Draft:
    body: str | None  # None: the model asked for review without a text
    evidence_ids: tuple[str, ...]
    case_claim_ids: tuple[str, ...]
    needs_review: bool
    review_reasons: tuple[str, ...]


def parse_draft(
    payload: object,
    *,
    evidence_aliases: Mapping[str, str],
    allowed_claim_ids: frozenset[str],
) -> Draft:
    """SchemaError for a wrong shape (one format correction, doc 11); references that were not
    offered are dropped and listed, and the draft needs review."""
    if not isinstance(payload, Mapping):
        raise SchemaError("ожидается JSON-объект")
    if set(payload) != _FIELDS:
        raise SchemaError(f"ожидаются поля {sorted(_FIELDS)}")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise SchemaError(f"schema_version: ожидается {SCHEMA_VERSION}")
    body = payload["body"]
    if body is not None and not isinstance(body, str):
        raise SchemaError("body: строка или null")
    if isinstance(body, str) and len(body) > MESSAGE_HARD_LIMIT:
        raise SchemaError(f"body: длиннее {MESSAGE_HARD_LIMIT} символов")
    lists: dict[str, list[str]] = {}
    for key in ("evidence_ids", "case_claim_ids", "review_reasons"):
        value = payload[key]
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise SchemaError(f"{key}: ожидается массив строк")
        lists[key] = value
    if len(lists["review_reasons"]) > MAX_REASONS:
        raise SchemaError(f"review_reasons: больше {MAX_REASONS} элементов")
    needs_review = payload["needs_review"]
    if not isinstance(needs_review, bool):
        raise SchemaError("needs_review: ожидается true или false")

    reasons = [r.strip()[:300] for r in lists["review_reasons"] if r.strip()]
    unknown = [x for x in lists["evidence_ids"] if x not in evidence_aliases]
    if unknown:
        reasons.append(f"ссылки на непереданные факты удалены: {len(unknown)}")
    forbidden = [x for x in lists["case_claim_ids"] if x not in allowed_claim_ids]
    if forbidden:
        reasons.append(f"неразрешённые кейсы удалены: {len(forbidden)}")
    text = body.strip() if isinstance(body, str) and body.strip() else None
    if text is None and not needs_review:
        reasons.append("нет текста без запроса проверки")
    return Draft(
        body=text,
        evidence_ids=tuple(
            dict.fromkeys(
                evidence_aliases[x] for x in lists["evidence_ids"] if x in evidence_aliases
            )
        ),
        case_claim_ids=tuple(
            dict.fromkeys(x for x in lists["case_claim_ids"] if x in allowed_claim_ids)
        ),
        needs_review=needs_review or bool(unknown or forbidden) or text is None,
        review_reasons=tuple(reasons),
    )


def body_blockers(
    body: str | None,
    *,
    kind: MessageKind,
    registry: CaseRegistry,
    case_claim_ids: tuple[str, ...],
) -> tuple[str, ...]:
    """What forbids Approve until the text is fixed (doc 11 checks 6–7, doc 05, T-07)."""
    if body is None or not body.strip():
        return ("нет текста сообщения",)
    problems: list[str] = []
    limit = FIRST_MESSAGE_LIMIT if kind is MessageKind.INITIAL else FOLLOW_UP_LIMIT
    if len(body) > limit:
        problems.append(f"текст длиннее {limit} символов ({len(body)})")
    if _PLACEHOLDER.search(body):
        problems.append("в тексте остались незаполненные поля")
    if _REFERENCE.search(body):
        problems.append("в тексте остались служебные ссылки (E1, [..], ID кейса, имя документа)")
    for violation in check_case_text(body, registry=registry, attached_ids=case_claim_ids):
        problems.append(f"кейс {violation.case_key}: {violation.code.value}")
    return tuple(dict.fromkeys(problems))
