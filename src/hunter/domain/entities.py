"""Core records shared by zones A and B: company, source, evidence, contact, lead.
(S1-05, CONTRACT)

Doc 10 "Сущности". Unknown values are None, never a placeholder. Moments are timezone-aware
UTC datetimes; the event date of a fact is a calendar date with its precision. Each object is
valid or is not created. Deviations from doc 10 are listed in docs/domain-contract.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from urllib.parse import urlsplit

from hunter.domain.catalog import CLOSED_STAGES, Icp, Priority, Product, Stage


class SourceType(StrEnum):
    WEBSITE = "website"
    LISTING = "listing"  # marketplace listing; used only if PM accepts ADR-0001 (S1-11)
    CONVERSATION = "conversation"
    MANUAL = "manual"


class FetchStatus(StrEnum):
    IMPORTED = "imported"  # content came with the import file
    FETCHED = "fetched"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


SUCCESSFUL_FETCH = frozenset({FetchStatus.IMPORTED, FetchStatus.FETCHED})


class DatePrecision(StrEnum):
    DAY = "day"
    MONTH = "month"
    YEAR = "year"
    UNKNOWN = "unknown"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"  # extracted, not checked by a person
    VERIFIED = "verified"  # an operator confirmed the excerpt supports the claim
    REJECTED = "rejected"  # the excerpt does not support the claim


class Channel(StrEnum):
    EMAIL = "email"
    TELEGRAM = "telegram"
    PHONE = "phone"
    WEB = "web"
    PLATFORM = "platform"  # reply through a marketplace listing
    OTHER = "other"


def _utc(value: datetime | None, name: str) -> None:
    if value is not None and value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware UTC datetime")


def _text(value: str | None, name: str) -> None:
    if value is not None and not value.strip():
        raise ValueError(f"{name} must not be blank; use None when unknown")


def _version(value: int) -> None:
    if value < 1:
        raise ValueError("version starts at 1")


@dataclass(frozen=True, slots=True)
class Company:
    id: str
    name: str
    version: int = 1
    normalized_domain: str | None = None
    industry: str | None = None
    size_min: int | None = None
    size_max: int | None = None
    location: str | None = None
    merged_into_id: str | None = None

    def __post_init__(self) -> None:
        _version(self.version)
        if not self.name.strip():
            raise ValueError("company name must not be blank")
        domain = self.normalized_domain
        if domain is not None and (
            domain != domain.lower()
            or "." not in domain
            or any(ch.isspace() or ch in "/:@?#" for ch in domain)
        ):
            raise ValueError("normalized_domain: lower-case host only, e.g. 'example.com'")
        for value, name in ((self.industry, "industry"), (self.location, "location")):
            _text(value, name)
        if any(size is not None and size < 0 for size in (self.size_min, self.size_max)):
            raise ValueError("company size must not be negative")
        if None not in (self.size_min, self.size_max) and self.size_min > self.size_max:
            raise ValueError("size_min must not exceed size_max")
        if self.merged_into_id == self.id:
            raise ValueError("a company cannot be merged into itself")


@dataclass(frozen=True, slots=True)
class Source:
    """A URL does not prove the content was received: fetch_status says what happened."""

    id: str
    canonical_url: str
    source_type: SourceType
    fetch_status: FetchStatus
    fetched_at: datetime | None = None
    content_hash: str | None = None
    snapshot_ref: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        parts = urlsplit(self.canonical_url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("canonical_url must be an absolute http(s) URL")
        _utc(self.fetched_at, "fetched_at")
        if self.fetch_status in SUCCESSFUL_FETCH:
            if self.fetched_at is None or not self.content_hash:
                raise ValueError("a received source keeps fetched_at and content_hash")
            if self.error_code is not None:
                raise ValueError("a received source has no error_code")
        elif not self.error_code:
            raise ValueError("a failed source records an error_code")


@dataclass(frozen=True, slots=True)
class Evidence:
    """A fact about a company with the excerpt that supports it (FR-04)."""

    id: str
    company_id: str
    source_id: str
    claim: str
    excerpt: str
    observed_at: datetime
    event_at: date | None = None
    date_precision: DatePrecision = DatePrecision.UNKNOWN
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED

    def __post_init__(self) -> None:
        _utc(self.observed_at, "observed_at")
        if not self.claim.strip() or not self.excerpt.strip():
            raise ValueError("evidence needs a claim and a non-empty excerpt")
        if (self.event_at is None) != (self.date_precision is DatePrecision.UNKNOWN):
            raise ValueError("event_at is None exactly when date_precision is 'unknown'")


@dataclass(frozen=True, slots=True)
class Contact:
    """Deduplicated by channel + normalized_address, never by name (doc 03, doc 10)."""

    id: str
    company_id: str
    channel: Channel
    normalized_address: str
    version: int = 1
    name: str | None = None
    role: str | None = None
    source_id: str | None = None
    verified_at: datetime | None = None

    def __post_init__(self) -> None:
        _version(self.version)
        if not isinstance(self.channel, Channel):
            raise ValueError("channel must be a Channel")
        address = self.normalized_address
        if not address or address != address.strip():
            raise ValueError("normalized_address must be non-empty and trimmed")
        for value, name in ((self.name, "name"), (self.role, "role")):
            _text(value, name)
        _utc(self.verified_at, "verified_at")


@dataclass(frozen=True, slots=True)
class Lead:
    """One sales opportunity of a company within a campaign (doc 04, doc 10).

    priority is None until the lead is scored; icp and product are None until research.
    """

    id: str
    company_id: str
    campaign_id: str
    stage: Stage
    version: int = 1
    icp: Icp | None = None
    product: Product | None = None
    priority: Priority | None = None
    owner_id: str | None = None
    next_action_at: datetime | None = None
    loss_reason: str | None = None

    def __post_init__(self) -> None:
        _version(self.version)
        if not isinstance(self.stage, Stage):
            raise ValueError("stage must be a CRM Stage, not a priority or message status")
        if self.priority is not None and not isinstance(self.priority, Priority):
            raise ValueError("priority must be a Priority")
        if self.icp is not None and not isinstance(self.icp, Icp):
            raise ValueError("icp must be an Icp")
        if self.product is not None and not isinstance(self.product, Product):
            raise ValueError("product must be a Product")
        _utc(self.next_action_at, "next_action_at")
        _text(self.loss_reason, "loss_reason")
        if (self.stage is Stage.LOST) != (self.loss_reason is not None):
            raise ValueError("loss_reason is required exactly for LOST")

    @property
    def active(self) -> bool:
        """Doc 10: one active opportunity per company + campaign; WON and LOST are closed."""
        return self.stage not in CLOSED_STAGES
