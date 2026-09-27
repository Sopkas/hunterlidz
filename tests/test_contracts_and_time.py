"""Provider contract examples (quota, timeout, redirect, changed structure) and the
midnight boundary between UTC and the business date."""

import io
import json
import socket
import urllib.error
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from hunter.application.ports import LlmError, LlmErrorKind
from hunter.infrastructure.fetcher import SafeHttpFetcher
from hunter.infrastructure.knowledge import FileKnowledgeBase
from hunter.infrastructure.llm.gemini import GeminiClient, parse_generate_content
from hunter.runtime.config import Settings
from hunter.runtime.container import App
from tests.helpers import FixedClock, make_deps

FIXTURES = Path(__file__).parent / "fixtures"


class FakeOpener:
    """Stands in for urllib's opener: either raises or returns a canned body."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return io.BytesIO(json.dumps(self.outcome).encode())


def call(outcome):
    opener = FakeOpener(outcome)
    client = GeminiClient("KEY-123", "gemini-test", opener=opener)
    return client.complete_json("system", "user", {"type": "object"}, 100), opener


def http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(b"{}"))


@pytest.mark.parametrize(
    ("outcome", "kind"),
    [
        (http_error(429), LlmErrorKind.QUOTA),
        (http_error(503), LlmErrorKind.UNAVAILABLE),
        (http_error(401), LlmErrorKind.AUTH),
        (http_error(302), LlmErrorKind.UNAVAILABLE),  # a redirect is refused, not followed
        (socket.timeout("timed out"), LlmErrorKind.TIMEOUT),
    ],
    ids=["quota", "unavailable", "auth", "redirect", "timeout"],
)
def test_gemini_failures_are_normalized_without_leaking(outcome, kind):
    with pytest.raises(LlmError) as exc:
        call(outcome)
    assert exc.value.kind is kind and "KEY-123" not in str(exc.value)


def test_gemini_key_in_header_not_url():
    _, opener = call(json.loads((FIXTURES / "gemini_ok.json").read_text(encoding="utf-8")))
    [request] = opener.requests
    assert "KEY-123" not in request.full_url
    assert request.get_header("X-goog-api-key") == "KEY-123"


def test_changed_response_structure_is_a_format_error_not_a_result():
    payload = json.loads((FIXTURES / "gemini_changed_structure.json").read_text(encoding="utf-8"))
    response = parse_generate_content(payload)
    assert response.data is None and response.parse_error


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
