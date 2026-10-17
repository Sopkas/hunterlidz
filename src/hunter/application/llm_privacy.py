"""Contacts never reach the model (S2-04, ADR-0002 point 2).

The model needs facts about a company and its need, not addresses. MaskingClient wraps any
LlmClient and replaces e-mails, phone numbers, Telegram and WhatsApp handles and links in the
system and user text before the call, whatever use case built the prompt. The owner's rule for
the test period (17.10) is stricter still: only public data and synthetic eval cards are sent to
a free provider; masking is the safety net under that rule, not a licence to send leads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from hunter.application.llm import LlmClient, LlmRequest, LlmResult, Tariff

EMAIL_MASK = "[e-mail]"
PHONE_MASK = "[телефон]"
MESSENGER_MASK = "[мессенджер]"

_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.UNICODE)
_MESSENGER_LINK = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog|wa\.me|api\.whatsapp\.com)"
    r"/[^\s)\]>,;]+",
    re.IGNORECASE,
)
_TG_HANDLE = re.compile(r"(?<![\w@])@[A-Za-z][A-Za-z0-9_]{3,31}\b")
# A phone is written with "+" or with separators: +7 916 123-45-67, 8 (495) 123-45-67. A bare
# run of digits is a phone only as 7XXXXXXXXXX or 8XXXXXXXXXX: INN (10 or 12 digits) and OGRN
# (13 or 15) describe a company, they are public research data and stay.
_EXT = r"(?:\s*(?:доб\.?|ext\.?|x|#)\s*\d{1,6})?"
_PHONE_WRITTEN = re.compile(
    r"(?<![\w+])(?:\+\d(?:[\s().\-]*\d){7,14}|\d{1,4}(?:[\s().\-]+\d+){2,}(?<=\d))" + _EXT,
    re.IGNORECASE,
)
_PHONE_BARE = re.compile(r"(?<![\w+])[78]\d{10}(?!\w)")


def _mask_written_phone(match: re.Match[str]) -> str:
    digits = sum(ch.isdigit() for ch in match.group(0))
    return PHONE_MASK if 10 <= digits <= 21 else match.group(0)


def mask_contacts(text: str) -> str:
    """Replace contact addresses with neutral marks; the rest of the text is unchanged."""
    text = _EMAIL.sub(EMAIL_MASK, text)
    text = _MESSENGER_LINK.sub(MESSENGER_MASK, text)
    text = _TG_HANDLE.sub(MESSENGER_MASK, text)
    text = _PHONE_WRITTEN.sub(_mask_written_phone, text)
    return _PHONE_BARE.sub(PHONE_MASK, text)


@dataclass(frozen=True, slots=True)
class MaskingClient:
    """LlmClient that masks contacts in every request before the wrapped client sends it."""

    inner: LlmClient

    @property
    def model_id(self) -> str:
        return self.inner.model_id

    @property
    def tariff(self) -> Tariff | None:
        return self.inner.tariff

    def complete_json(self, request: LlmRequest) -> LlmResult:
        masked = replace(
            request, system=mask_contacts(request.system), user=mask_contacts(request.user)
        )
        return self.inner.complete_json(masked)
