"""Contact channels and address normalization. (H-03)

Doc 03: a contact is deduplicated by the normalized address of a specific channel;
an equal name is never enough to merge. Doc 04 contactability: a company-wide channel
(platform reply, generic mailbox) is worth 5, a confirmed role with its own work channel 10.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from hunter.domain.listings import normalize_url

_EMAIL = re.compile(r"^[\w.+\-]+@[\w\-]+(\.[\w\-]+)+$")
_TELEGRAM = re.compile(r"^(?:@|(?:https?://)?t\.me/)([A-Za-z0-9_]{4,32})$")
_GENERIC_MAILBOX = re.compile(r"^(info|office|hello|sales|contact|support|mail|admin)@")


class Channel(StrEnum):
    EMAIL = "email"
    TELEGRAM = "telegram"
    PHONE = "phone"
    WEB = "web"
    PLATFORM = "platform"  # reply through the marketplace listing
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Address:
    channel: Channel
    normalized: str

    @property
    def is_company_wide(self) -> bool:
        """Shared channel of the company rather than a person's own work channel."""
        if self.channel in (Channel.PLATFORM, Channel.WEB):
            return True
        return self.channel is Channel.EMAIL and bool(_GENERIC_MAILBOX.match(self.normalized))


def classify_address(raw: str) -> Address:
    value = raw.strip()
    if _EMAIL.match(value):
        return Address(Channel.EMAIL, value.lower())
    telegram = _TELEGRAM.match(value)
    if telegram:
        return Address(Channel.TELEGRAM, "@" + telegram.group(1).lower())
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 10 and re.fullmatch(r"[\d\s()+\-]+", value):
        return Address(Channel.PHONE, "+" + digits)
    url = normalize_url(value) if "." in value and " " not in value else None
    if url:
        return Address(Channel.WEB, url)
    return Address(Channel.OTHER, value.lower())


def platform_address(listing_url: str) -> Address:
    return Address(Channel.PLATFORM, listing_url)
