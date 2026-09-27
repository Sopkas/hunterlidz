"""draft_v1 contract (doc 11 "Персонализация") and its checks. (H-07)

The model may return needs_review=true without text when a needed fact is missing; that is
a valid answer and becomes a draft without an approvable body. References to evidence or case
claims that were not offered are removed and listed as problems.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from hunter.domain.messages import MESSAGE_HARD_LIMIT
from hunter.domain.research import SchemaError

SCHEMA_VERSION = "draft_v1"


@dataclass(frozen=True, slots=True)
class Draft:
    body: str
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
    if not isinstance(payload, Mapping):
        raise SchemaError("ожидается JSON-объект")
    expected = {
        "schema_version",
        "body",
        "evidence_ids",
        "case_claim_ids",
        "needs_review",
        "review_reasons",
    }
    if set(payload) != expected:
        raise SchemaError(f"поля должны быть ровно: {sorted(expected)}")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise SchemaError(f"schema_version должен быть {SCHEMA_VERSION}")
    body = payload["body"]
    if not isinstance(body, str) or len(body) > MESSAGE_HARD_LIMIT:
        raise SchemaError(f"body: строка до {MESSAGE_HARD_LIMIT} символов")
    lists = {}
    for key in ("evidence_ids", "case_claim_ids", "review_reasons"):
        value = payload[key]
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise SchemaError(f"{key}: ожидается массив строк")
        lists[key] = value
    if not isinstance(payload["needs_review"], bool):
        raise SchemaError("needs_review: ожидается boolean")

    reasons = [r.strip() for r in lists["review_reasons"] if r.strip()]
    unknown = [x for x in lists["evidence_ids"] if x not in evidence_aliases]
    if unknown:
        reasons.append(f"ссылки на непереданные факты удалены: {unknown}")
    forbidden = [x for x in lists["case_claim_ids"] if x not in allowed_claim_ids]
    if forbidden:
        reasons.append(f"неразрешённые кейсы удалены: {forbidden}")
    return Draft(
        body=body.strip(),
        evidence_ids=tuple(
            evidence_aliases[x] for x in lists["evidence_ids"] if x in evidence_aliases
        ),
        case_claim_ids=tuple(x for x in lists["case_claim_ids"] if x in allowed_claim_ids),
        needs_review=payload["needs_review"] or bool(unknown or forbidden),
        review_reasons=tuple(reasons),
    )
