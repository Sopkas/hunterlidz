"""Listing normalization: one row of a marketplace export -> a validated Listing. (H-03)

Covers FR-01 (company name, website or source URL) and the normalization part of FR-02.
Unknown values stay None and the reason is recorded in `missing`; nothing is guessed.
Company identity uses the normalized host (lowercase, no scheme, no "www."). Merging
subdomains into one registrable domain needs a public-suffix list (doc 03); until that
dependency is approved we under-merge rather than merge different organisations.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from hunter.domain.catalog import Money

NBSP = chr(0x00A0)
NARROW_NBSP = chr(0x202F)
SOURCE_TEXT_LIMIT = 12_000  # doc 09: read_source returns up to 12 000 characters

_CURRENCY_MARKS = (
    ("RUB", ("₽", "руб", "rub", "р.")),
    ("USD", ("$", "usd", "долл")),
    ("EUR", ("€", "eur", "евро")),
)
_MULTIPLIERS = {
    "млн": Decimal(1_000_000),
    "тыс": Decimal(1000),
    "к": Decimal(1000),
    "k": Decimal(1000),
}
_NUMBER = re.compile(r"(\d+(?:[.,]\d+)?)(млн|тыс|к|k)?")


@dataclass(frozen=True, slots=True)
class Listing:
    platform: str
    ext_id: str | None
    title: str
    body: str
    company_name: str | None
    website_host: str | None
    source_url: str | None
    budget: Money | None
    posted_on: date | None
    contact_address: str | None
    contact_name: str | None
    body_truncated: bool
    missing: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.platform.strip():
            raise ValueError("platform is required")
        if len(self.body) > SOURCE_TEXT_LIMIT:
            raise ValueError("body exceeds the source text limit")

    @property
    def text(self) -> str:
        """Normalized search text: lowercase, 'ё' folded to 'е'."""
        return normalize_text(f"{self.title}\n{self.body}")

    @property
    def text_hash(self) -> str:
        return hashlib.sha256(re.sub(r"\W+", " ", self.text).strip().encode()).hexdigest()

    @property
    def source_key(self) -> str:
        """Stable identity of the listing across re-imports."""
        if self.ext_id:
            return f"{self.platform}:{self.ext_id}"
        if self.source_url:
            return f"url:{url_key(self.source_url)}"
        return f"text:{self.text_hash}"


def normalize_text(value: str) -> str:
    return value.replace("ё", "е").replace("Ё", "Е").lower()


def normalize_url(raw: str | None) -> str | None:
    """Canonical http(s) URL without query string or fragment (tracking parameters)."""
    if not raw or not str(raw).strip():
        return None
    value = str(raw).strip()
    if "://" not in value:
        value = "https://" + value
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower().removeprefix("www.")
    port = f":{parts.port}" if parts.port else ""
    path = parts.path.rstrip("/")
    return f"{parts.scheme}://{host}{port}{path}"


def url_key(url: str) -> str:
    return url.split("://", 1)[-1]


def host_of(raw: str | None) -> str | None:
    url = normalize_url(raw)
    return urlsplit(url).hostname if url else None


def parse_budget(raw: object, default_currency: str) -> Money | None:
    """'400 000 ₽' / 'до 500к' / '400-600 тыс' -> upper bound as Money; 'договорная' -> None."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float, Decimal)):
        return Money(Decimal(str(raw)), default_currency) if raw >= 0 else None
    text = normalize_text(str(raw))
    currency = next(
        (code for code, marks in _CURRENCY_MARKS if any(m in text for m in marks)), default_currency
    )
    compact = re.sub(rf"[\s{NBSP}{NARROW_NBSP}]", "", text)
    values = []
    for number, unit in _NUMBER.findall(compact):
        try:
            values.append(Decimal(number.replace(",", ".")) * _MULTIPLIERS.get(unit, Decimal(1)))
        except InvalidOperation:
            continue
    return Money(max(values).quantize(Decimal("0.01")), currency) if values else None


def parse_date(raw: object) -> date | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_listing(raw: Mapping[str, object], platform_default: str, currency: str) -> Listing:
    """Build a Listing from canonical field names (see infrastructure.listing_files.ALIASES)."""
    missing: list[str] = []
    title = _clean(raw.get("title")) or ""
    body = _clean(raw.get("body")) or ""
    budget = parse_budget(raw.get("budget"), currency)
    if budget is None:
        missing.append("budget: не указан или не распознан")
    posted_raw = raw.get("posted_at")
    posted_on = parse_date(posted_raw)
    if posted_on is None:
        missing.append(
            "posted_at: не указана" if not posted_raw else "posted_at: формат не распознан"
        )
    contact = _clean(raw.get("contact"))
    if contact is None:
        missing.append("contact: нет прямого контакта")
    return Listing(
        platform=_clean(raw.get("platform")) or platform_default,
        ext_id=_clean(raw.get("ext_id")),
        title=title,
        body=body[:SOURCE_TEXT_LIMIT],
        company_name=_clean(raw.get("company")),
        website_host=host_of(_clean(raw.get("website"))),
        source_url=normalize_url(_clean(raw.get("url"))),
        budget=budget,
        posted_on=posted_on,
        contact_address=contact,
        contact_name=_clean(raw.get("contact_name")),
        body_truncated=len(body) > SOURCE_TEXT_LIMIT,
        missing=tuple(missing),
    )
