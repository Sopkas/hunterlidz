"""One OpenAI-compatible chat completions client (S2-04, ADR-0002).

Works with any provider that speaks POST {base_url}/chat/completions: OpenRouter for the test
period (owner's decision of 17.10: open-source ":free" models only), a local open-source server,
later polza.ai (S2-05). Every outcome becomes an LlmResult; nothing raises for provider failures,
and no result carries the prompt, the key or the provider's text (S2-01 contract).

The HTTP call goes through `transport`, so tests never reach the network.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from hunter.application.llm import (
    RETRYABLE,
    LlmErrorCode,
    LlmRequest,
    LlmResult,
    Tariff,
    Usage,
)


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class TransportTimeout(Exception):
    """No answer within the deadline."""


class TransportUnavailable(Exception):
    """The provider could not be reached (DNS, refused connection, TLS)."""


Transport = Callable[[str, Mapping[str, str], bytes, float], HttpResponse]


def urllib_transport(
    url: str, headers: Mapping[str, str], body: bytes, timeout: float
) -> HttpResponse:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HttpResponse(response.status, dict(response.headers), response.read())
    except urllib.error.HTTPError as error:
        return HttpResponse(error.code, dict(error.headers or {}), error.read() or b"")
    except (TimeoutError, socket.timeout):
        raise TransportTimeout from None
    except urllib.error.URLError as error:
        if isinstance(error.reason, (TimeoutError, socket.timeout)):
            raise TransportTimeout from None
        raise TransportUnavailable from None
    except OSError:
        raise TransportUnavailable from None


_STATUS_CODES: dict[int, LlmErrorCode] = {
    400: LlmErrorCode.BAD_REQUEST,
    401: LlmErrorCode.AUTH,
    402: LlmErrorCode.QUOTA,  # OpenRouter: no credits; the key has a spend limit of 0
    403: LlmErrorCode.REFUSAL,  # OpenRouter: moderation flagged the input
    408: LlmErrorCode.TIMEOUT,
    413: LlmErrorCode.BAD_REQUEST,
    422: LlmErrorCode.BAD_REQUEST,
    429: LlmErrorCode.RATE_LIMITED,
    502: LlmErrorCode.UNAVAILABLE,
    503: LlmErrorCode.UNAVAILABLE,
    504: LlmErrorCode.TIMEOUT,
}


def _error_code(status: int) -> LlmErrorCode:
    if status in _STATUS_CODES:
        return _STATUS_CODES[status]
    return LlmErrorCode.UNAVAILABLE if status >= 500 else LlmErrorCode.UNKNOWN


def _retry_after(headers: Mapping[str, str]) -> int | None:
    for name, value in headers.items():
        if name.lower() == "retry-after":
            try:
                return max(0, int(float(value)))
            except ValueError:
                return None  # an HTTP date: let the queue use its own backoff
    return None


def _usage(payload: Mapping[str, object]) -> Usage:
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return Usage(0, 0)
    prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if not all(
        isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in (prompt, completion)
    ):
        return Usage(0, 0)
    return Usage(prompt, completion)


@dataclass(frozen=True, slots=True)
class OpenAICompatibleClient:
    """`structured`: "json_schema" sends the schema (strict structured output); "json_object"
    only asks for JSON, for models without schema support. The schema is checked by the caller
    either way (parse_research), never trusted from the provider."""

    base_url: str
    model: str
    api_key: str
    tariff: Tariff | None
    timeout_s: float = 60.0
    structured: str = "json_schema"
    transport: Transport = urllib_transport
    provider: str = "openai-compatible"

    def __post_init__(self) -> None:
        if not self.base_url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise ValueError("base_url: https, or plain http only for a local server")
        if not self.model.strip():
            raise ValueError("model is required")
        if self.structured not in ("json_schema", "json_object"):
            raise ValueError("structured: json_schema or json_object")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be positive")

    @property
    def model_id(self) -> str:
        return f"{self.provider}:{self.model}"

    def complete_json(self, request: LlmRequest) -> LlmResult:
        if self.structured == "json_schema":
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": "result", "strict": True, "schema": request.schema},
            }
        else:
            response_format = {"type": "json_object"}
        body = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user},
                ],
                "max_tokens": request.max_output_tokens,
                "temperature": 0,
                "response_format": response_format,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = self.base_url.rstrip("/") + "/chat/completions"
        try:
            response = self.transport(url, headers, body, self.timeout_s)
        except TransportTimeout:
            return self._failure(request, LlmErrorCode.TIMEOUT, Usage(0, 0))
        except TransportUnavailable:
            return self._failure(request, LlmErrorCode.UNAVAILABLE, Usage(0, 0))
        return self._read(request, response)

    def _failure(
        self,
        request: LlmRequest,
        code: LlmErrorCode,
        usage: Usage,
        retry_after_s: int | None = None,
    ) -> LlmResult:
        return LlmResult.failure(
            request,
            self.model_id,
            code,
            usage,
            self.tariff,
            retry_after_s=retry_after_s if code in RETRYABLE else None,
        )

    def _read(self, request: LlmRequest, response: HttpResponse) -> LlmResult:
        try:
            payload = json.loads(response.body.decode("utf-8")) if response.body else {}
        except (UnicodeDecodeError, ValueError):
            payload = None
        if response.status != 200:
            usage = _usage(payload) if isinstance(payload, Mapping) else Usage(0, 0)
            return self._failure(
                request, _error_code(response.status), usage, _retry_after(response.headers)
            )
        if not isinstance(payload, Mapping):
            return self._failure(request, LlmErrorCode.UNKNOWN, Usage(0, 0))
        usage = _usage(payload)
        error = payload.get("error")
        if isinstance(error, Mapping):
            # OpenRouter may answer 200 with an error object from the upstream provider.
            code = error.get("code")
            status = code if isinstance(code, int) and not isinstance(code, bool) else 0
            return self._failure(
                request, _error_code(status), usage, _retry_after(response.headers)
            )
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            return self._failure(request, LlmErrorCode.UNKNOWN, usage)  # a changed protocol
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, Mapping):
            return self._failure(request, LlmErrorCode.UNKNOWN, usage)
        finish = choice.get("finish_reason")
        if finish == "content_filter" or message.get("refusal"):
            return self._failure(request, LlmErrorCode.REFUSAL, usage)
        if finish == "length":
            return self._failure(request, LlmErrorCode.TRUNCATED, usage)
        content = message.get("content")
        if not isinstance(content, str):
            return self._failure(request, LlmErrorCode.NOT_JSON, usage)
        try:
            data = json.loads(_strip_fence(content))
        except ValueError:
            return self._failure(request, LlmErrorCode.NOT_JSON, usage)
        if not isinstance(data, dict):
            return self._failure(request, LlmErrorCode.NOT_JSON, usage)
        return LlmResult.success(request, self.model_id, data, usage, self.tariff)


def _strip_fence(text: str) -> str:
    """Some models wrap JSON in ```json … ``` despite the instruction; the content is the same."""
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        stripped = stripped[3:-3].strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
    return stripped
