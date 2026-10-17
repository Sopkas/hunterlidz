"""Contact addresses: one spelling per channel, so that a duplicate or a suppression is found
whatever way the address was written (S1-13, doc 03, T-15).

Doc 03: a contact is deduplicated by the normalized address of a specific channel; an equal
name is never enough. Doc 10: channel + normalized_address is unique and enters the approval
hash. Doc 04 contactability: a company-wide channel is worth 5, a person's own work channel 10.

Rules are versioned by CONTACT_RULES_VERSION; docs/dedup.md, "Контакты", lists them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hunter.domain.dedup import UrlError, canonical_url
from hunter.domain.entities import Channel

CONTACT_RULES_VERSION = "contacts_v1"

# What people write in a contact column when there is no contact.
PLACEHOLDERS = frozenset(
    {
        "-",
        "--",
        "—",
        "?",
        "0",
        "n/a",
        "na",
        "none",
        "null",
        "нет",
        "не указан",
        "не указано",
        "отсутствует",
        "см сайт",
        "см. сайт",
        "смотри сайт",
    }
)

# Shared mailboxes of a company (doc 04: contactability 5, not 10).
GENERIC_MAILBOXES = frozenset(
    {
        "admin",
        "contact",
        "contacts",
        "hello",
        "hr",
        "info",
        "mail",
        "manager",
        "office",
        "order",
        "sales",
        "support",
        "zakaz",
    }
)

_DOMAIN_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_EMAIL_LOCAL = re.compile(r"^[^\s@<>()\[\],;:\"\\]{1,64}$")
_NAMED_EMAIL = re.compile(r"<([^<>]+)>\s*$")
_PHONE_CHARS = re.compile(r"^\+?[\d\s().\-]+$")
_EXTENSION = re.compile(r"[,;]?\s*(?:доб\.?|добавочный|ext\.?|x|#)\s*(\d{1,6})\s*$", re.IGNORECASE)
_TG_USERNAME = re.compile(r"^[a-z][a-z0-9_]{3,31}$")
_TG_HOSTS = ("t.me/", "telegram.me/", "telegram.dog/")
_TG_SERVICE_PATHS = frozenset({"joinchat", "addstickers", "addemoji", "share", "proxy", "c"})
_SEVERAL = re.compile(r"[,;]|\s/\s|\s(?:и|или|or|and)\s", re.IGNORECASE)
_WHATSAPP_HOSTS = ("wa.me", "api.whatsapp.com", "whatsapp.com")


class AddressError(ValueError):
    """The value is not a usable address for the channel; code says why."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Address:
    channel: Channel
    normalized: str


def _clean(raw: str) -> str:
    value = " ".join(raw.split())
    if not value:
        raise AddressError("empty", "address is empty")
    if value.casefold().rstrip(".") in PLACEHOLDERS or value.casefold() in PLACEHOLDERS:
        raise AddressError("placeholder", f"{value!r} means there is no contact")
    return value


def normalize_email(raw: str) -> str:
    """Ivan.Petrov+crm@Пример.РФ → ivan.petrov+crm@xn--e1afmkfd.xn--p1ai.

    The local part is lower-cased (mail services treat it so), dots and +tags stay: on another
    server they may be another mailbox. "Name <a@b>" and mailto: are unwrapped.
    """
    value = _clean(raw)
    named = _NAMED_EMAIL.search(value)
    if named:
        value = named.group(1).strip()
    if value.lower().startswith("mailto:"):
        value = value[7:].split("?", 1)[0]
    local, at, domain = value.rpartition("@")
    if not at or not _EMAIL_LOCAL.match(local) or ".." in local or local.strip(".") != local:
        raise AddressError("email", "not an e-mail address")
    domain = domain.strip().rstrip(".").lower()
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError:
        raise AddressError("email", "e-mail domain is not a valid domain name") from None
    labels = domain.split(".")
    if len(labels) < 2 or not all(_DOMAIN_LABEL.match(label) for label in labels):
        raise AddressError("email", "e-mail domain is not a valid domain name")
    return f"{local.lower()}@{domain}"


def normalize_phone(raw: str) -> str:
    """E.164 with an optional extension: 8 (495) 123-45-67 доб. 12 → +74951234567;ext=12.

    Russian numbers: 8XXXXXXXXXX and 7XXXXXXXXXX become +7XXXXXXXXXX; ten digits without a
    country code are taken as Russian (the pilot works in Russia). The extension is part of the
    address: two people behind one switchboard are two contacts.
    """
    value = _clean(raw)
    if value.lower().startswith("tel:"):
        value = value[4:]
    extension = ""
    found = _EXTENSION.search(value)
    if found:
        extension = f";ext={found.group(1)}"
        value = value[: found.start()]
    if not _PHONE_CHARS.match(value.strip()):
        raise AddressError("phone", "not a phone number")
    plus = value.strip().startswith("+")
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11 and (digits[0] in "78" if not plus else digits[:2] == "89"):
        # +89… is a common slip for a Russian mobile: no country code starts with 89.
        digits = "7" + digits[1:]
    elif not plus and len(digits) == 10:
        digits = "7" + digits
    elif not plus:
        raise AddressError("phone", "no country code and not a Russian number")
    if digits.startswith("7") and len(digits) != 11:
        raise AddressError("phone", "a +7 number has ten digits after the code")
    if not 8 <= len(digits) <= 15 or digits[0] == "0":
        raise AddressError("phone", "not an E.164 number")
    return f"+{digits}{extension}"


def normalize_telegram(raw: str) -> str:
    """@Name, t.me/Name, https://telegram.me/Name?start=x, tg://resolve?domain=Name → @name.

    Invite and message links (t.me/+…, t.me/joinchat/…, t.me/c/…) are not a contact. A phone
    number is a valid Telegram address and is stored in the phone form.
    """
    value = _clean(raw)
    if _PHONE_CHARS.match(value) and sum(ch.isdigit() for ch in value) >= 10:
        return normalize_phone(value)
    lowered = value.lower()
    if lowered.startswith("tg://resolve?"):
        params = dict(p.partition("=")[::2] for p in value.split("?", 1)[1].split("&"))
        name = params.get("domain", "")
    elif lowered.startswith("@"):
        name = value[1:]
    else:
        path = re.sub(r"^(?:https?://)?(?:www\.)?", "", value, flags=re.IGNORECASE)
        host = next((h for h in _TG_HOSTS if path.lower().startswith(h)), None)
        if host is None:
            name = value
        else:
            segments = [s for s in re.split(r"[/?#]", path[len(host) :], maxsplit=2)[:2] if s]
            if not segments:
                raise AddressError("telegram", "link has no username")
            if segments[0].startswith("+") or segments[0].lower() in _TG_SERVICE_PATHS:
                raise AddressError("invite", "invite or message link is not a contact")
            name = segments[1] if segments[0].lower() == "s" and len(segments) > 1 else segments[0]
    name = name.lower()
    if not _TG_USERNAME.match(name) or name.endswith("_"):
        raise AddressError("telegram", "not a Telegram username")
    return "@" + name


def normalize_address(channel: Channel, raw: str) -> str:
    """The normalized_address of a Contact for its channel; AddressError says what is wrong."""
    if channel is Channel.EMAIL:
        return normalize_email(raw)
    if channel is Channel.PHONE:
        return normalize_phone(raw)
    if channel is Channel.TELEGRAM:
        return normalize_telegram(raw)
    if channel is Channel.WEB:
        value = _clean(raw)
        try:
            return canonical_url(value)
        except UrlError as error:
            raise AddressError(error.code, str(error)) from None
    # Unknown format: any further change could merge two different addresses.
    return _clean(raw)


def classify_address(raw: str) -> Address:
    """Guess the channel of a bare value from an import column, then normalize it.

    One value is one address: "a@x.ru, b@x.ru" is rejected with code "several", the import
    splits such a cell or asks the operator.
    """
    value = _clean(raw)
    if not _NAMED_EMAIL.search(value) and _SEVERAL.search(_EXTENSION.sub("", value)):
        raise AddressError("several", "several addresses in one value")
    lowered = value.lower()
    if lowered.startswith(("tg://", "@")) or any(
        re.match(rf"^(?:https?://)?(?:www\.)?{re.escape(h)}", lowered) for h in _TG_HOSTS
    ):
        return Address(Channel.TELEGRAM, normalize_telegram(value))
    if lowered.startswith("mailto:") or ("@" in value and "/" not in value):
        return Address(Channel.EMAIL, normalize_email(value))
    if lowered.startswith("tel:") or _PHONE_CHARS.match(_EXTENSION.sub("", value)):
        return Address(Channel.PHONE, normalize_phone(value))
    if "." in value and " " not in value:
        url = normalize_address(Channel.WEB, value)
        host = url.split("://", 1)[1].split("/", 1)[0]
        if host in _WHATSAPP_HOSTS:
            # A person's messenger, not the company's web form; the enum has no WhatsApp yet.
            return Address(Channel.OTHER, url)
        return Address(Channel.WEB, url)
    return Address(Channel.OTHER, value)


def is_company_wide(address: Address) -> bool:
    """A shared channel of the company rather than a person's own (doc 04 contactability)."""
    if address.channel is Channel.WEB:
        return True
    if address.channel is Channel.EMAIL:
        return address.normalized.split("@", 1)[0] in GENERIC_MAILBOXES
    return False
