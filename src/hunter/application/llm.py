"""LLM port: one structured-output call with a normalized result. (S2-01, CONTRACT)

Doc 09 "Ход исследования" step 6 and the result envelope: every call ends with a status from
success / denied / invalid / timeout / error, even when nothing was produced, and the envelope
carries call_id, status, data, error_code, retryable and truncated. Adapters (S2-04: an
OpenAI-compatible client for open-source models, then polza.ai; ADR-0002) never raise for
provider failures: they return an LlmResult.

Every result carries usage and cost, so the budget (S2-07) can record what was actually spent,
including for failed calls. Cost is None when the tariff is unknown; the budget then refuses
to start a paid call (doc 09 "Бюджеты и остановка").

What the model receives is the caller's job: contacts are masked before any call (ADR-0002).
Neither errors nor results carry prompts, keys or provider payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

_MILLION = Decimal(1_000_000)


class LlmStatus(StrEnum):
    SUCCESS = "success"  # parsed JSON object in data
    DENIED = "denied"  # the provider refused: safety, auth, quota, model not allowed
    INVALID = "invalid"  # an answer came but it is not a JSON object, or it was cut off
    TIMEOUT = "timeout"  # no answer within the call deadline
    ERROR = "error"  # provider unavailable, rate limited, bad request, anything else


class LlmErrorCode(StrEnum):
    REFUSAL = "refusal"
    AUTH = "auth"
    QUOTA = "quota"
    NOT_JSON = "not_json"
    TRUNCATED = "truncated"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    BAD_REQUEST = "bad_request"
    UNKNOWN = "unknown"


STATUS_FOR_ERROR: dict[LlmErrorCode, LlmStatus] = {
    LlmErrorCode.REFUSAL: LlmStatus.DENIED,
    LlmErrorCode.AUTH: LlmStatus.DENIED,
    LlmErrorCode.QUOTA: LlmStatus.DENIED,
    LlmErrorCode.NOT_JSON: LlmStatus.INVALID,
    LlmErrorCode.TRUNCATED: LlmStatus.INVALID,
    LlmErrorCode.TIMEOUT: LlmStatus.TIMEOUT,
    LlmErrorCode.UNAVAILABLE: LlmStatus.ERROR,
    LlmErrorCode.RATE_LIMITED: LlmStatus.ERROR,
    LlmErrorCode.BAD_REQUEST: LlmStatus.ERROR,
    LlmErrorCode.UNKNOWN: LlmStatus.ERROR,
}
# Worth repeating later with the same request. INVALID is not here: a malformed answer gets one
# format correction with the validation message (doc 11), which is a new request.
RETRYABLE = frozenset({LlmErrorCode.TIMEOUT, LlmErrorCode.UNAVAILABLE, LlmErrorCode.RATE_LIMITED})


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        for count in (self.input_tokens, self.output_tokens):
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ValueError("token counts are non-negative integers")

    def __add__(self, other: Usage) -> Usage:
        """A research run sums its calls, the format correction included (token_usage)."""
        return Usage(
            self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens
        )


@dataclass(frozen=True, slots=True)
class Tariff:
    """Price per million tokens in the accounting currency (doc 12 COST_CURRENCY)."""

    input_per_million: Decimal
    output_per_million: Decimal
    currency: str

    def __post_init__(self) -> None:
        for price in (self.input_per_million, self.output_per_million):
            # Prices come from settings: a float would fail later inside Decimal arithmetic.
            if not isinstance(price, Decimal) or price < 0:
                raise ValueError("a price is a non-negative Decimal")
        if (
            len(self.currency) != 3
            or not (self.currency.isascii() and self.currency.isalpha())
            or not self.currency.isupper()
        ):
            raise ValueError("currency: ISO 4217 code, e.g. 'RUB'")

    def cost(self, usage: Usage) -> Decimal:
        return (
            usage.input_tokens * self.input_per_million
            + usage.output_tokens * self.output_per_million
        ) / _MILLION


@dataclass(frozen=True, slots=True)
class LlmRequest:
    """prompt_version is stored with the run (doc 09, research_runs.prompt_version), so a call
    record says which prompt produced the answer."""

    call_id: str
    prompt_version: str
    system: str
    user: str
    schema: dict
    max_output_tokens: int

    def __post_init__(self) -> None:
        if not self.call_id.strip() or not self.prompt_version.strip():
            raise ValueError("call_id and prompt_version are required")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")


@dataclass(frozen=True, slots=True)
class LlmResult:
    """Outcome of one call. data is set exactly on success; error_code exactly otherwise.

    usage counts what the provider billed, also for failed calls (zero when nothing was
    billed). cost and currency are None together when the tariff is unknown.

    usage_unknown: on timeout the provider may bill without reporting usage, so usage is zero
    but the spend is not. The budget (S2-07) then charges the reserve of the call
    (max_output_tokens), never zero.

    retry_after_s: the provider's own wait (Retry-After) for a retryable failure, so the queue
    (S2-06) does not retry blindly; None when the provider did not say.
    """

    call_id: str
    model_id: str
    status: LlmStatus
    usage: Usage
    data: dict | None = None
    error_code: LlmErrorCode | None = None
    cost: Decimal | None = None
    currency: str | None = None
    retry_after_s: int | None = None

    def __post_init__(self) -> None:
        if not self.call_id.strip() or not self.model_id.strip():
            raise ValueError("call_id and model_id are required")
        if self.status is LlmStatus.SUCCESS:
            if not isinstance(self.data, dict) or self.error_code is not None:
                raise ValueError("success carries a JSON object and no error_code")
        elif self.data is not None or self.error_code is None:
            raise ValueError("a failed call carries an error_code and no data")
        elif STATUS_FOR_ERROR[self.error_code] is not self.status:
            raise ValueError(
                f"error_code {self.error_code} means {STATUS_FOR_ERROR[self.error_code]}"
            )
        if (self.cost is None) != (self.currency is None):
            raise ValueError("cost and currency are set together")
        if self.cost is not None and self.cost < 0:
            raise ValueError("cost must not be negative")
        if self.retry_after_s is not None:
            if self.error_code not in RETRYABLE:
                raise ValueError("retry_after_s belongs to a retryable failure")
            if not isinstance(self.retry_after_s, int) or self.retry_after_s < 0:
                raise ValueError("retry_after_s is a non-negative number of seconds")

    @property
    def retryable(self) -> bool:
        return self.error_code in RETRYABLE

    @property
    def usage_unknown(self) -> bool:
        return self.status is LlmStatus.TIMEOUT

    @property
    def truncated(self) -> bool:
        return self.error_code is LlmErrorCode.TRUNCATED

    @classmethod
    def success(
        cls, request: LlmRequest, model_id: str, data: dict, usage: Usage, tariff: Tariff | None
    ) -> LlmResult:
        return cls(
            request.call_id, model_id, LlmStatus.SUCCESS, usage, data=data, **_priced(usage, tariff)
        )

    @classmethod
    def failure(
        cls,
        request: LlmRequest,
        model_id: str,
        error_code: LlmErrorCode,
        usage: Usage,
        tariff: Tariff | None,
        retry_after_s: int | None = None,
    ) -> LlmResult:
        return cls(
            request.call_id,
            model_id,
            STATUS_FOR_ERROR[error_code],
            usage,
            error_code=error_code,
            retry_after_s=retry_after_s,
            **_priced(usage, tariff),
        )


def _priced(usage: Usage, tariff: Tariff | None) -> dict:
    if tariff is None:
        return {}
    return {"cost": tariff.cost(usage), "currency": tariff.currency}


class LlmClient(Protocol):
    """One provider and model. complete_json never raises for provider failures."""

    @property
    def model_id(self) -> str:
        """Provider and model, e.g. 'polza:<model>'; stored with every research run (doc 09)."""

    @property
    def tariff(self) -> Tariff | None:
        """Known price of this model, or None; the budget checks it before a paid call."""

    def complete_json(self, request: LlmRequest) -> LlmResult: ...
