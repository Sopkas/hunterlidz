"""Core records: company, source, contact, evidence, lead. (H-03)

Doc 10 "Сущности". A fact always references an available source; the event date may be
unknown (None with precision "unknown"), and the observation time is a separate UTC moment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from hunter.domain.catalog import Icp, Priority, Product, Stage
from hunter.domain.contacts import Channel


class SourceType(StrEnum):
    LISTING = "listing"
    WEBSITE = "website"
    CONVERSATION = "conversation"
    MANUAL = "manual"


class FetchStatus(StrEnum):
    IMPORTED = "imported"  # content came with the import file
    FETCHED = "fetched"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class DatePrecision(StrEnum):
    DAY = "day"
    MONTH = "month"
    YEAR = "year"
    UNKNOWN = "unknown"


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware UTC")


@dataclass(frozen=True, slots=True)
class Source:
    id: str
    company_id: str
    source_type: SourceType
    canonical_url: str
    fetch_status: FetchStatus
    fetched_at: datetime | None
    content_hash: str | None
    snapshot: str | None
    truncated: bool = False
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.fetched_at is not None:
            _aware(self.fetched_at, "fetched_at")
        ok = self.fetch_status in (FetchStatus.IMPORTED, FetchStatus.FETCHED)
        if ok and self.snapshot is None:
            raise ValueError("a successful source must keep its snapshot")
        if not ok and self.error_code is None:
            raise ValueError("a failed source must record an error code")


@dataclass(frozen=True, slots=True)
class Contact:
    id: str
    company_id: str
    channel: Channel
    normalized_address: str
    company_wide: bool
    name: str | None = None
    role: str | None = None
    source_id: str | None = None
    verified_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    id: str
    company_id: str
    source_id: str
    field: str
    claim: str
    excerpt: str
    observed_at: datetime
    event_date: date | None = None
    date_precision: DatePrecision = DatePrecision.UNKNOWN

    def __post_init__(self) -> None:
        _aware(self.observed_at, "observed_at")
        if (self.event_date is None) != (self.date_precision is DatePrecision.UNKNOWN):
            raise ValueError("event_date is null exactly when date_precision is 'unknown'")
        if not self.excerpt.strip():
            raise ValueError("evidence needs a non-empty excerpt")


@dataclass(frozen=True, slots=True)
class Lead:
    id: str
    company_id: str
    campaign_id: str
    stage: Stage
    version: int
    icp: Icp = Icp.UNKNOWN
    product: Product | None = None
    priority: Priority | None = None
    score_total: int | None = None
    prescore_total: int | None = None
    needs_review: bool = False
    loss_reason: str | None = None

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("version starts at 1")
        if self.score_total is not None and not 0 <= self.score_total <= 100:
            raise ValueError("score_total must be within 0..100")
