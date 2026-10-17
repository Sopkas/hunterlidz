"""LLM port contract (S2-01): normalized statuses, usage and cost in every result."""

from decimal import Decimal

import pytest

from hunter.application.llm import (
    RETRYABLE,
    STATUS_FOR_ERROR,
    LlmErrorCode,
    LlmRequest,
    LlmResult,
    LlmStatus,
    Tariff,
    Usage,
)

REQUEST = LlmRequest(
    call_id="run-1:1",
    prompt_version="research_prompt_v1",
    system="rules",
    user="task",
    schema={},
    max_output_tokens=3000,
)
TARIFF = Tariff(Decimal("40"), Decimal("120"), "RUB")
USAGE = Usage(input_tokens=12_000, output_tokens=2_000)


def test_every_error_code_has_one_status():
    assert set(STATUS_FOR_ERROR) == set(LlmErrorCode)
    assert set(STATUS_FOR_ERROR.values()) == set(LlmStatus) - {LlmStatus.SUCCESS}


def test_success_carries_data_usage_and_cost():
    result = LlmResult.success(REQUEST, "local:qwen", {"schema_version": "x"}, USAGE, TARIFF)
    assert result.status is LlmStatus.SUCCESS
    assert result.call_id == "run-1:1"
    assert result.cost == Decimal("0.72")  # 12 000 × 40 / 1M + 2 000 × 120 / 1M
    assert result.currency == "RUB"
    assert not result.retryable and not result.truncated


@pytest.mark.parametrize(
    ("code", "status", "retryable"),
    [
        (LlmErrorCode.REFUSAL, LlmStatus.DENIED, False),
        (LlmErrorCode.AUTH, LlmStatus.DENIED, False),
        (LlmErrorCode.QUOTA, LlmStatus.DENIED, False),
        (LlmErrorCode.NOT_JSON, LlmStatus.INVALID, False),
        (LlmErrorCode.TRUNCATED, LlmStatus.INVALID, False),
        (LlmErrorCode.TIMEOUT, LlmStatus.TIMEOUT, True),
        (LlmErrorCode.UNAVAILABLE, LlmStatus.ERROR, True),
        (LlmErrorCode.RATE_LIMITED, LlmStatus.ERROR, True),
        (LlmErrorCode.BAD_REQUEST, LlmStatus.ERROR, False),
        (LlmErrorCode.UNKNOWN, LlmStatus.ERROR, False),
    ],
)
def test_failure_status_and_retry(code, status, retryable):
    result = LlmResult.failure(REQUEST, "polza:model", code, USAGE, TARIFF)
    assert result.status is status
    assert result.data is None
    assert result.retryable is retryable
    assert (code in RETRYABLE) is retryable


def test_failed_call_still_records_what_was_billed():
    result = LlmResult.failure(REQUEST, "polza:model", LlmErrorCode.TRUNCATED, USAGE, TARIFF)
    assert result.truncated
    assert result.cost == Decimal("0.72")


def test_unknown_tariff_gives_no_cost():
    result = LlmResult.success(REQUEST, "local:qwen", {}, USAGE, None)
    assert result.cost is None and result.currency is None


@pytest.mark.parametrize(
    "fields",
    [
        dict(status=LlmStatus.SUCCESS, data=None),
        dict(status=LlmStatus.SUCCESS, data={}, error_code=LlmErrorCode.UNKNOWN),
        dict(status=LlmStatus.SUCCESS, data=["not", "an", "object"]),
        dict(status=LlmStatus.ERROR, data={}, error_code=LlmErrorCode.UNKNOWN),
        dict(status=LlmStatus.ERROR),
        dict(status=LlmStatus.DENIED, error_code=LlmErrorCode.TIMEOUT),
        dict(status=LlmStatus.SUCCESS, data={}, cost=Decimal("1")),
        dict(status=LlmStatus.SUCCESS, data={}, cost=Decimal("-1"), currency="RUB"),
        dict(status=LlmStatus.SUCCESS, data={}, call_id=" "),
    ],
    ids=[
        "success-no-data",
        "success-with-error",
        "success-not-object",
        "failure-with-data",
        "failure-no-code",
        "code-from-other-status",
        "cost-without-currency",
        "negative-cost",
        "blank-call-id",
    ],
)
def test_inconsistent_result_is_not_created(fields):
    base = dict(call_id="run-1:1", model_id="m", usage=USAGE)
    with pytest.raises(ValueError):
        LlmResult(**{**base, **fields})


@pytest.mark.parametrize(
    "build",
    [
        lambda: Usage(-1, 0),
        lambda: Tariff(Decimal("-1"), Decimal("1"), "RUB"),
        lambda: Tariff(Decimal("1"), Decimal("1"), "rub"),
        lambda: LlmRequest("", "p1", "s", "u", {}, 100),
        lambda: LlmRequest("c", "", "s", "u", {}, 100),
        lambda: LlmRequest("c", "p1", "s", "u", {}, 0),
        lambda: Tariff(0.5, Decimal("1"), "RUB"),
        lambda: Usage(1.5, 0),
    ],
    ids=[
        "negative-tokens",
        "negative-price",
        "currency-case",
        "no-call-id",
        "no-prompt-version",
        "no-output",
        "float-price",
        "float-tokens",
    ],
)
def test_invalid_inputs_are_rejected(build):
    with pytest.raises(ValueError):
        build()


def test_usage_adds_up_over_a_run():
    assert Usage(10, 2) + Usage(5, 1) == Usage(15, 3)


def test_timeout_usage_is_unknown_not_free():
    # Review A on #8: the budget charges the reserve, never zero (S2-07).
    result = LlmResult.failure(REQUEST, "polza:model", LlmErrorCode.TIMEOUT, Usage(0, 0), TARIFF)
    assert result.usage_unknown and result.cost == 0
    other = LlmResult.failure(REQUEST, "polza:model", LlmErrorCode.UNAVAILABLE, USAGE, TARIFF)
    assert not other.usage_unknown


def test_retry_after_travels_with_a_retryable_failure():
    result = LlmResult.failure(
        REQUEST, "polza:model", LlmErrorCode.RATE_LIMITED, USAGE, TARIFF, retry_after_s=30
    )
    assert result.retryable and result.retry_after_s == 30


@pytest.mark.parametrize(
    ("code", "seconds"),
    [(LlmErrorCode.QUOTA, 30), (LlmErrorCode.RATE_LIMITED, -1)],
    ids=["not-retryable", "negative"],
)
def test_retry_after_is_checked(code, seconds):
    with pytest.raises(ValueError):
        LlmResult.failure(REQUEST, "polza:model", code, USAGE, TARIFF, retry_after_s=seconds)
