"""Canonical URLs, company keys and duplicate matching (S1-09, T-01, T-02).

Doc 03 "Дедупликация": the company key is the registrable domain found with the public
suffix list, not the last two labels; protocol, www, path and tracking parameters are dropped;
companies are never merged by a similar name. Doc 10: the domain is unique for an active
company, an ambiguous merge is done by hand; one active lead per company + campaign.

Rules are versioned by NORMALIZATION_VERSION; docs/dedup.md lists them with the reasons.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from urllib.parse import quote, urlsplit

from publicsuffixlist import PublicSuffixList

from hunter.domain.catalog import CLOSED_STAGES
from hunter.domain.entities import Company, Lead

NORMALIZATION_VERSION = "dedup_v1"

# Dropping a parameter that identifies the page would merge two different sources, keeping a
# tracking one only leaves a duplicate source. Ambiguous names (ref, from, source) stay.
TRACKING_PREFIXES = ("utm_", "roistat")
TRACKING_PARAMS = frozenset(
    {
        "_ga",
        "_gl",
        "_openstat",
        "dclid",
        "etext",
        "fbclid",
        "gbraid",
        "gclid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "msclkid",
        "openstat",
        "wbraid",
        "yclid",
        "ysclid",
    }
)

# Site builders and blog hosts missing from the public suffix list: each subdomain is a separate
# site, so "a.tilda.ws" and "b.tilda.ws" are two companies, not one.
SUBDOMAIN_HOSTS = frozenset(
    {
        "3dn.ru",
        "at.ua",
        "beget.tech",
        "bitrix24.ru",
        "blogspot.ru",
        "diafan.ru",
        "do.am",
        "flexbe.ru",
        "insales.ru",
        "livejournal.com",
        "lpmotor.ru",
        "megagroup.ru",
        "mya5.ru",
        "narod.ru",
        "nethouse.ru",
        "nubex.ru",
        "platformalp.ru",
        "taplink.ws",
        "tb.ru",
        "tilda.ws",
        "turbo.site",
        "ucoz.com",
        "ucoz.net",
        "ucoz.ru",
        "ukit.me",
        "umi.ru",
        "weebly.com",
        "wordpress.com",
    }
)

# Platforms where a company has a page under a path (vk.com/name, t.me/name) and registries
# that describe companies. Their domain belongs to nobody we research, so it is not a key.
SHARED_PLATFORMS = frozenset(
    {
        "2gis.com",
        "2gis.ru",
        "avito.ru",
        "checko.ru",
        "dzen.ru",
        "facebook.com",
        "fb.com",
        "github.com",
        "gitlab.com",
        "google.com",
        "habr.com",
        "hh.ru",
        "instagram.com",
        "linkedin.com",
        "linktr.ee",
        "list-org.com",
        "ok.ru",
        "rusprofile.ru",
        "rutube.ru",
        "t.me",
        "taplink.cc",
        "telegram.me",
        "tiktok.com",
        "twitter.com",
        "vc.ru",
        "vk.com",
        "vk.me",
        "vk.ru",
        "wa.me",
        "whatsapp.com",
        "x.com",
        "yandex.com",
        "yandex.ru",
        "youtu.be",
        "youtube.com",
        "zoon.ru",
    }
)

_DEFAULT_PORTS = {"http": 80, "https": 443}
_OTHER_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:(?!\d)", re.IGNORECASE)
_HOST_LABEL = re.compile(r"^(?!-)[a-z0-9_-]{1,63}(?<!-)$")
_PERCENT = re.compile(r"%[0-9a-fA-F]{2}")
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


class UrlError(ValueError):
    """The value is not a usable http(s) address; code says why."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@cache
def _suffix_list() -> PublicSuffixList:
    # The list ships inside the package: no network access at import or lookup.
    return PublicSuffixList()


def _ascii_host(raw: str) -> str:
    host = raw.strip().rstrip(".").lower()
    if not host:
        raise UrlError("host", "address has no host")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return host
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise UrlError("host", "host is not a valid domain name") from None
    if not all(_HOST_LABEL.match(label) for label in host.split(".")):
        raise UrlError("host", "host is not a valid domain name")
    if host.startswith("www.") and host.count(".") >= 2:
        host = host[4:]
    return host


def _is_tracking(pair: str) -> bool:
    name = pair.split("=", 1)[0].lower()
    return name in TRACKING_PARAMS or name.startswith(TRACKING_PREFIXES)


def _escape(text: str) -> str:
    """One spelling per character: non-ASCII as UTF-8 escapes, unreserved ones decoded (%7E → ~),
    the rest of the escapes in upper case: /о-нас == /%D0%BE-%D0%BD%D0%B0%D1%81."""

    def fix(match: re.Match[str]) -> str:
        char = chr(int(match.group(0)[1:], 16))
        return char if char in _UNRESERVED else match.group(0).upper()

    return quote(_PERCENT.sub(fix, text), safe="%/:@!$&'()*+,;=-._~")


def canonical_url(raw: str) -> str:
    """One spelling per page: https://Пример.РФ/a/?utm_source=x → https://xn--e1afmkfd.xn--p1ai/a.

    Kept: scheme, path (case-sensitive) and every query parameter that may identify the page,
    sorted by name. Dropped: www, default port, user info, fragment, trailing slash, tracking
    parameters. Raises UrlError for anything that is not an http(s) address.
    """
    value = raw.strip()
    if not value:
        raise UrlError("empty", "address is empty")
    if value.startswith("//"):
        value = "https:" + value
    elif "://" not in value:
        if _OTHER_SCHEME.match(value):
            raise UrlError("scheme", "only http and https addresses are accepted")
        value = "https://" + value
    parts = urlsplit(value)
    if "\\" in parts.netloc:
        # Browsers read a backslash as "/", urlsplit does not: example.com\@evil.com has two hosts.
        raise UrlError("host", "backslash in the host part")
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise UrlError("scheme", "only http and https addresses are accepted")
    try:
        port = parts.port
    except ValueError:
        port = 0
    if port == 0:
        raise UrlError("port", "port is not a number from 1 to 65535")
    host = _ascii_host(parts.hostname or "")
    if ":" in host:
        host = f"[{host}]"
    netloc = host if port in (None, _DEFAULT_PORTS[scheme]) else f"{host}:{port}"
    path = _escape(parts.path).rstrip("/") or "/"
    pairs = [p for p in parts.query.split("&") if p and not _is_tracking(p)]
    pairs.sort(key=lambda p: p.split("=", 1)[0])
    query = "&".join(_escape(p) for p in pairs)
    return f"{scheme}://{netloc}{path}" + (f"?{query}" if query else "")


def company_domain(raw: str | None) -> str | None:
    """The company key from a site address or a bare domain, or None when there is none.

    None for an empty value, an IP address, a single-label or unknown-TLD host, a public suffix
    itself and shared platforms (vk.com/name is not a company domain). Raises UrlError for an
    address that is not http(s) at all.
    """
    if raw is None or not raw.strip():
        return None
    host = urlsplit(canonical_url(raw)).hostname or ""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return None
    for builder in SUBDOMAIN_HOSTS:
        if host == builder:
            return None
        if host.endswith("." + builder):
            return host[: -len(builder) - 1].rsplit(".", 1)[-1] + "." + builder
    domain = _suffix_list().privatesuffix(host, accept_unknown=False)
    if domain is None or domain in SHARED_PLATFORMS:
        return None
    return domain


_LEGAL_FORMS = frozenset(
    {
        "ано",
        "ао",
        "гк",
        "зао",
        "ип",
        "нко",
        "оао",
        "ооо",
        "пао",
        "gmbh",
        "inc",
        "llc",
        "ltd",
    }
)
_LEGAL_PHRASES = (
    "общество с ограниченной ответственностью",
    "индивидуальный предприниматель",
    "публичное акционерное общество",
    "акционерное общество",
)


def normalize_company_name(name: str) -> str:
    """Name form used only to flag possible duplicates for a person, never to merge (T-02)."""
    text = name.casefold().replace("ё", "е")
    for phrase in _LEGAL_PHRASES:
        text = text.replace(phrase, " ")
    words = re.findall(r"\w+", text)
    return " ".join(w for w in words if w not in _LEGAL_FORMS)


class MatchOutcome(StrEnum):
    MATCHED = "matched"  # an active company with the same domain exists
    NEW = "new"  # no company with this domain; similar names only go to manual review


@dataclass(frozen=True, slots=True)
class CompanyMatch:
    outcome: MatchOutcome
    company_id: str | None = None
    similar_ids: tuple[str, ...] = ()  # active companies with the same normalized name

    @property
    def needs_review(self) -> bool:
        return bool(self.similar_ids)


class DuplicateDomainError(ValueError):
    """Two active companies share a domain: the store broke the doc 10 uniqueness rule."""


def _active_root(company: Company, by_id: dict[str, Company]) -> Company:
    seen = {company.id}
    while company.merged_into_id is not None:
        target = by_id.get(company.merged_into_id)
        if target is None or target.id in seen:
            raise ValueError(f"company {company.id}: merge chain is broken or circular")
        seen.add(target.id)
        company = target
    return company


def match_company(*, name: str, domain: str | None, companies: Iterable[Company]) -> CompanyMatch:
    """Find the company an imported row belongs to (T-01) without merging by name (T-02).

    domain is the result of company_domain(). A match on a merged record resolves to the
    company it was merged into.
    """
    by_id = {c.id: c for c in companies}
    if domain is not None:
        roots = {_active_root(c, by_id).id for c in by_id.values() if c.normalized_domain == domain}
        if len(roots) > 1:
            raise DuplicateDomainError(f"{domain}: active companies {sorted(roots)}")
        if roots:
            return CompanyMatch(MatchOutcome.MATCHED, company_id=roots.pop())
    key = normalize_company_name(name)
    similar = tuple(
        sorted(
            c.id
            for c in by_id.values()
            if c.merged_into_id is None and key and normalize_company_name(c.name) == key
        )
    )
    return CompanyMatch(MatchOutcome.NEW, similar_ids=similar)


def active_lead(*, company_id: str, campaign_id: str, leads: Iterable[Lead]) -> Lead | None:
    """The open opportunity of company + campaign; a closed one stays in history (doc 10)."""
    found = [
        lead
        for lead in leads
        if lead.company_id == company_id
        and lead.campaign_id == campaign_id
        and lead.stage not in CLOSED_STAGES
    ]
    if len(found) > 1:
        raise ValueError(f"company {company_id}, campaign {campaign_id}: several active leads")
    return found[0] if found else None
