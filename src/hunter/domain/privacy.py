"""Masking of personal contact data before any text goes to a language model. (review fix)

The model needs facts about the company and the need, never addresses: a free provider tier
may use requests for training, and doc 09 keeps contacts out of technical channels. The
address stays in the database; the model sees only that a channel exists.
"""

from __future__ import annotations

import re

_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_TELEGRAM_LINK = re.compile(r"(?:https?://)?t\.me/[A-Za-z0-9_]{4,32}")
_HANDLE = re.compile(r"(?<![\w@])@[A-Za-z0-9_]{4,32}\b")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s()\-]{8,}\d(?!\w)")


def mask_contacts(text: str) -> str:
    text = _EMAIL.sub("[email скрыт]", text)
    text = _TELEGRAM_LINK.sub("[telegram скрыт]", text)
    text = _HANDLE.sub("[telegram скрыт]", text)
    return _PHONE.sub("[телефон скрыт]", text)
