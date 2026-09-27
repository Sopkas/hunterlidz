"""Provider contract examples (quota, timeout, auth, refusal, truncated and changed answers)
for the Claude adapter with an injected fake SDK, and the midnight boundary between UTC and
the business date."""

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from hunter.application.ports import LlmError, LlmErrorKind
from hunter.infrastructure.fetcher import SafeHttpFetcher
from hunter.infrastructure.knowledge import FileKnowledgeBase
from hunter.infrastructure.llm.claude import ClaudeClient
from hunter.runtime.config import Settings
from hunter.runtime.container import App
from tests.helpers import FixedClock, make_deps


class _ApiError(Exception):
    status_code = 0


class _Status(_ApiError):
    pass


class RateLimit(_Status):
    status_code = 429


class BadRequest(_Status):
    status_code = 400


class Auth(_Status):
    status_code = 401


class Overloaded(_Status):
    status_code = 529


class Connection(_ApiError):
    pass


class Timeout(Connection):
    pass


FAKE_SDK = SimpleNamespace(
    RateLimitError=RateLimit,
    APITimeoutError=Timeout,
    AuthenticationError=Auth,
    PermissionDeniedError=Auth,
    BadRequestError=BadRequest,
    APIStatusError=_Status,
    APIConnectionError=Connection,
)


def message(text, stop_reason="end_turn"):
    usage = SimpleNamespace(input_tokens=1000, cache_read_input_tokens=200, output_tokens=300)
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(usage=usage, stop_reason=stop_reason, content=[block])


class FakeMessages:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def call(outcome):
    messages = FakeMessages(outcome)
    client = ClaudeClient(
        "KEY-123", "claude-test", sdk=FAKE_SDK, client=SimpleNamespace(messages=messages)
    )
    return client.complete_json("system", "user", {"type": "object"}, 100), messages


@pytest.mark.parametrize(
    ("outcome", "kind"),
    [
        (RateLimit(), LlmErrorKind.QUOTA),
        (Timeout(), LlmErrorKind.TIMEOUT),
        (Auth(), LlmErrorKind.AUTH),
        (BadRequest(), LlmErrorKind.BAD_REQUEST),
        (Overloaded(), LlmErrorKind.UNAVAILABLE),
        (Connection(), LlmErrorKind.UNAVAILABLE),
    ],
    ids=["quota", "timeout", "auth", "bad-request", "overloaded", "connection"],
)
def test_claude_failures_are_normalized_without_leaking(outcome, kind):
    with pytest.raises(LlmError) as exc:
        call(outcome)
    assert exc.value.kind is kind and "KEY-123" not in str(exc.value)


def test_claude_ok_counts_cached_input_and_sends_schema():
    response, messages = call(message(json.dumps({"a": 1})))
    assert (response.data, response.input_tokens, response.output_tokens) == ({"a": 1}, 1200, 300)
    [kwargs] = messages.calls
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    assert kwargs["max_tokens"] == 100


def test_claude_refusal_is_an_error():
    with pytest.raises(LlmError) as exc:
        call(message("", stop_reason="refusal"))
    assert exc.value.kind is LlmErrorKind.REFUSAL


@pytest.mark.parametrize(
    ("text", "stop", "reason"),
    [('{"a": [1, 2', "max_tokens", "обрезан"), ("not json at all", "end_turn", "не JSON")],
    ids=["truncated", "changed-structure"],
)
def test_unexpected_answer_is_a_format_error_not_a_result(text, stop, reason):
    response, _ = call(message(text, stop_reason=stop))
    assert response.data is None and reason in response.parse_error


@pytest.mark.parametrize(
    ("utc_moment", "business_date"),
    [
        (datetime(2026, 9, 27, 20, 59, tzinfo=UTC), "2026-09-27"),
        (datetime(2026, 9, 27, 21, 0, tzinfo=UTC), "2026-09-28"),
    ],
    ids=["before-msk-midnight", "at-msk-midnight"],
)
def test_business_date_uses_business_zone_not_utc(utc_moment, business_date):
    deps = make_deps(clock=FixedClock(utc_moment))
    settings = Settings(
        actor="a", db_path=Path(":memory:"), business_tz=timezone(timedelta(hours=3))
    )
    app = App(settings, deps.store, deps, SafeHttpFetcher(), FileKnowledgeBase(Path(".")))
    assert app.business_today().isoformat() == business_date
