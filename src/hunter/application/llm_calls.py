"""One structured model call under the doc 09 budget. (H-07; extracted from H-05 research)

Shared by research and drafts: reserve the budget before every attempt, retry temporary
provider errors inside the same call limit, allow exactly one format correction, and report
a normalized outcome instead of raising for expected external failures.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from hunter.application.ports import LlmClient, LlmError
from hunter.application.prompts import correction_note
from hunter.domain.budget import BudgetExceeded, RunBudget, estimate_tokens
from hunter.domain.research import SchemaError

RETRY_DELAYS = (2.0, 5.0)  # seconds before the 2nd and 3rd attempt of a temporary failure

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class CallOutcome(Generic[T]):
    status: str  # ok / budget_exceeded / failed / aborted
    value: T | None = None
    error_code: str | None = None


def structured_call(
    llm: LlmClient,
    *,
    system: str,
    user: str,
    schema: dict,
    budget: RunBudget,
    validate: Callable[[object], T],
    sleep: Callable[[float], None],
    elapsed: Callable[[], float],
    before_call: Callable[[], bool] = lambda: True,
) -> CallOutcome[T]:
    note, corrected, failures = "", False, 0
    while True:
        prompt = user + note
        try:
            output_cap = budget.reserve_llm_call(estimate_tokens(system + prompt), elapsed())
        except BudgetExceeded as exc:
            return CallOutcome("budget_exceeded", error_code=exc.reason)
        if not before_call():
            return CallOutcome("aborted", error_code="lease_lost")
        try:
            response = llm.complete_json(system, prompt, schema, output_cap)
        except LlmError as exc:
            budget.record(0, 0)  # a failed attempt still counts as a call
            if exc.retryable and failures < len(RETRY_DELAYS):
                sleep(RETRY_DELAYS[failures])
                failures += 1
                continue
            return CallOutcome("failed", error_code=exc.kind.value)
        budget.record(response.input_tokens, response.output_tokens)
        try:
            if response.data is None:
                raise SchemaError(response.parse_error or "не JSON")
            return CallOutcome("ok", value=validate(response.data))
        except SchemaError as exc:
            if corrected:
                return CallOutcome("failed", error_code="invalid_schema")
            note, corrected = correction_note(str(exc)), True
