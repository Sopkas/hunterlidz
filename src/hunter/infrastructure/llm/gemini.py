"""Google Gemini (AI Studio) adapter over the REST API, standard library only. (H-05)

Schema of the response (candidates / usageMetadata / finishReason) was checked against a
live call on 2026-09-27 with gemini-flash-lite-latest; error bodies are ASSUMED to follow
the documented {"error": {"code", "status"}} shape. The key goes in a header, never in the
URL; environment proxies and redirects are disabled; the answer size is limited. Retries are
decided by the calling use case so they stay inside the run budget.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

from hunter.application.ports import LlmError, LlmErrorKind, LlmResponse

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TIMEOUT_SECONDS = 45  # doc 08: one LLM call is limited to 45 s
MAX_RESPONSE_BYTES = 2_000_000
_REFUSALS = {"SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the key header to another address: refuse it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GeminiClient:
    def __init__(
        self, api_key: str, model: str, timeout: float = TIMEOUT_SECONDS, opener=None
    ) -> None:
        if not api_key:
            raise ValueError("Gemini API key is required")
        self.model = model
        self._key = api_key
        self._timeout = timeout
        self._opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect()
        )

    def complete_json(
        self, system: str, user: str, schema: dict, max_output_tokens: int
    ) -> LlmResponse:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
                "maxOutputTokens": max_output_tokens,
            },
        }
        request = urllib.request.Request(
            ENDPOINT.format(model=self.model),
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": self._key},
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                payload = json.loads(response.read(MAX_RESPONSE_BYTES))
        except urllib.error.HTTPError as exc:
            raise LlmError(_kind_for_status(exc.code), f"http_{exc.code}") from None
        except (TimeoutError, socket.timeout):
            raise LlmError(LlmErrorKind.TIMEOUT) from None
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise LlmError(LlmErrorKind.UNAVAILABLE, type(exc).__name__) from None
        return parse_generate_content(payload)


def _kind_for_status(status: int) -> LlmErrorKind:
    if status == 429:
        return LlmErrorKind.QUOTA
    if status in (401, 403):
        return LlmErrorKind.AUTH
    if status in (400, 404, 413):
        return LlmErrorKind.BAD_REQUEST
    return LlmErrorKind.UNAVAILABLE


def parse_generate_content(payload: dict) -> LlmResponse:
    usage = payload.get("usageMetadata") or {}
    input_tokens = int(usage.get("promptTokenCount") or 0)
    # Thinking tokens are billed as output.
    output_tokens = int(usage.get("candidatesTokenCount") or 0) + int(
        usage.get("thoughtsTokenCount") or 0
    )
    feedback = payload.get("promptFeedback") or {}
    if feedback.get("blockReason"):
        raise LlmError(LlmErrorKind.REFUSAL, str(feedback["blockReason"]))
    candidates = payload.get("candidates") or []
    if not candidates:
        raise LlmError(LlmErrorKind.UNAVAILABLE, "no_candidates")
    candidate = candidates[0]
    finish = candidate.get("finishReason")
    if finish in _REFUSALS:
        raise LlmError(LlmErrorKind.REFUSAL, str(finish))
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        reason = "ответ обрезан по лимиту токенов" if finish == "MAX_TOKENS" else "не JSON"
        return LlmResponse(None, input_tokens, output_tokens, parse_error=reason)
    return LlmResponse(data, input_tokens, output_tokens)
