"""Anthropic Claude adapter through the official SDK. (H-05)

Structured output via output_config.format (json_schema); the stable system part is marked
for prompt caching. The SDK's own retries are disabled so that retries are counted by the
research budget. The SDK is imported lazily: it is only needed when HUNTER_LLM=claude.
Not verified against a live response in this project (the account had no credit balance).
"""

from __future__ import annotations

import json

from hunter.application.ports import LlmError, LlmErrorKind, LlmResponse

TIMEOUT_SECONDS = 45.0


class ClaudeClient:
    def __init__(self, api_key: str, model: str, timeout: float = TIMEOUT_SECONDS) -> None:
        import anthropic

        if not api_key:
            raise ValueError("Anthropic API key is required")
        self.model = model
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=0)

    def complete_json(
        self, system: str, user: str, schema: dict, max_output_tokens: int
    ) -> LlmResponse:
        sdk = self._anthropic
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=max_output_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": user}],
            )
        except sdk.RateLimitError:
            raise LlmError(LlmErrorKind.QUOTA) from None
        except sdk.APITimeoutError:
            raise LlmError(LlmErrorKind.TIMEOUT) from None
        except (sdk.AuthenticationError, sdk.PermissionDeniedError):
            raise LlmError(LlmErrorKind.AUTH) from None
        except sdk.BadRequestError as exc:
            # A 400 also covers an exhausted credit balance; the text is not forwarded.
            raise LlmError(LlmErrorKind.BAD_REQUEST, f"http_{exc.status_code}") from None
        except sdk.APIStatusError as exc:
            raise LlmError(LlmErrorKind.UNAVAILABLE, f"http_{exc.status_code}") from None
        except sdk.APIConnectionError:
            raise LlmError(LlmErrorKind.UNAVAILABLE, "connection") from None

        usage = response.usage
        input_tokens = (usage.input_tokens or 0) + (usage.cache_read_input_tokens or 0)
        output_tokens = usage.output_tokens or 0
        if response.stop_reason == "refusal":
            raise LlmError(LlmErrorKind.REFUSAL)
        text = "".join(b.text for b in response.content if b.type == "text")
        try:
            return LlmResponse(json.loads(text), input_tokens, output_tokens)
        except json.JSONDecodeError:
            reason = (
                "ответ обрезан по лимиту токенов"
                if response.stop_reason == "max_tokens"
                else "не JSON"
            )
            return LlmResponse(None, input_tokens, output_tokens, parse_error=reason)
