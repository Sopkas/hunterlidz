"""One model call with the single format correction of doc 11. (S2-09, review of #15)

Every structured answer goes the same way: call → parse; on a wrong shape one more request with
the validation message → parse again. The research eval, the research use case (S2-10) and the
draft (S3-03) share this loop so that the retry rule exists once. Usage and cost of both calls
are summed: the budget pays for the correction too (S2-07).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Generic, TypeVar

from hunter.application.llm import LlmClient, LlmRequest, LlmStatus, Usage
from hunter.domain.research import SchemaError

T = TypeVar("T")

# The model does not see its previous answer: the correction is a new request with the reason.
CORRECTION = "\n\nОтвет не прошёл проверку формы: {error}. Верни JSON по схеме."


@dataclass(frozen=True, slots=True)
class Parsed(Generic[T]):
    value: T | None  # None: the model failed or answered twice in a wrong shape
    status: LlmStatus  # of the last call
    usage: Usage
    cost: Decimal | None  # None when any call had an unknown tariff
    corrections: int
    error: str | None = None


def call_parsed(client: LlmClient, request: LlmRequest, parse: Callable[[object], T]) -> Parsed[T]:
    """`parse` raises SchemaError for a wrong shape; anything else it raises is a bug, not a
    model answer, and is not caught."""
    usage, cost, corrections = Usage(0, 0), Decimal(0), 0
    error: str | None = None
    status = LlmStatus.ERROR
    for attempt in (1, 2):
        result = client.complete_json(request)
        status = result.status
        usage = usage + result.usage
        cost = None if cost is None or result.cost is None else cost + result.cost
        if result.status is not LlmStatus.SUCCESS:
            error = f"модель: {result.status} ({result.error_code})"
            if result.status is LlmStatus.INVALID and attempt == 1:
                corrections += 1
                request = _corrected(request, f"ответ не JSON ({result.error_code})")
                continue
            break
        try:
            value = parse(result.data)
        except SchemaError as exc:
            error = f"схема: {exc}"
            if attempt == 1:
                corrections += 1
                request = _corrected(request, str(exc))
            continue
        return Parsed(value, status, usage, cost, corrections)
    return Parsed(None, status, usage, cost, corrections, error)


def _corrected(request: LlmRequest, error: str) -> LlmRequest:
    return replace(
        request,
        call_id=request.call_id.rsplit(":", 1)[0] + ":2",
        user=request.user + CORRECTION.format(error=error),
    )
