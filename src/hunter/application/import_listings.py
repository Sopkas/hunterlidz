"""Import use case: rows -> created / matched / rejected with a reason. (H-03)

FR-01: every row gets a result and a reason. FR-02: re-importing the same listing, or a
listing of a company that already has an active lead in the campaign, creates nothing new.
The listing itself becomes a source with its text as the snapshot, and each filled field
becomes a quoted evidence item, so later research can only cite what was really imported.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from hunter.application.access import require, require_mode
from hunter.application.context import Deps
from hunter.application.errors import ErrorCode, HunterError
from hunter.domain.catalog import PRODUCT_FOR_ICP, Stage
from hunter.domain.contacts import Address, classify_address, platform_address
from hunter.domain.entities import (
    Contact,
    DatePrecision,
    Evidence,
    FetchStatus,
    Lead,
    Source,
    SourceType,
)
from hunter.domain.listings import Listing, parse_listing
from hunter.domain.modes import Operation
from hunter.domain.prescore import prescore
from hunter.domain.roles import Permission
from hunter.domain.screening import SCREENING_VERSION, RejectReason, screen
from hunter.domain.users import User

ICP_VERSION = "1.0"  # doc 03, three segments
AUTHOR_ROLE = "автор объявления"  # the person who published the need


@dataclass(frozen=True, slots=True)
class RowResult:
    row_no: int
    result: str  # created / matched / rejected
    reason: str | None = None
    lead_id: str | None = None


@dataclass(slots=True)
class ImportReport:
    import_id: str
    rows: list[RowResult] = field(default_factory=list)

    @property
    def counts(self) -> Counter:
        return Counter(r.result for r in self.rows)

    @property
    def reasons(self) -> Counter:
        return Counter(r.reason for r in self.rows if r.reason)


def import_listings(
    deps: Deps,
    actor: User,
    rows: Sequence[Mapping[str, object]],
    *,
    file_name: str,
    campaign: str,
    platform: str,
    currency: str,
    today: date,
) -> ImportReport:
    require(actor, Permission.IMPORT)
    require_mode(deps, Operation.IMPORT)
    if not rows:
        raise HunterError(ErrorCode.INVALID_INPUT, "файл не содержит строк")
    store, now = deps.store, deps.clock.now()
    with store.transaction():
        campaign_id = store.campaign_id(campaign) or _create_campaign(deps, actor, campaign)
        report = ImportReport(import_id=deps.ids.new_id())
        store.add_import(report.import_id, campaign_id, file_name, actor.id, now)
        for row_no, raw in enumerate(rows, start=1):
            result = _import_row(deps, actor, raw, campaign_id, platform, currency, today)
            report.rows.append(RowResult(row_no, *result))
            store.add_import_row(report.import_id, row_no, *result)
    return report


def _create_campaign(deps: Deps, actor: User, name: str) -> str:
    campaign_id = deps.ids.new_id()
    deps.store.add_campaign(campaign_id, name, ICP_VERSION, deps.clock.now())
    deps.audit(actor, "campaign", campaign_id, "create", new_version=1)
    return campaign_id


def _import_row(
    deps: Deps,
    actor: User,
    raw: Mapping[str, object],
    campaign_id: str,
    platform: str,
    currency: str,
    today: date,
) -> tuple[str, str | None, str | None]:
    store = deps.store
    try:
        listing = parse_listing(raw, platform, currency)
    except (ValueError, TypeError) as exc:
        return "rejected", f"invalid_row: {exc}", None

    known = store.listing_by_key(listing.source_key)
    if known is not None:
        return "matched", None, known or None  # '' marks a listing that was rejected before

    reason = screen(listing, today)
    if reason is None and store.listing_text_exists(listing.text_hash):
        reason = RejectReason.DUPLICATE_TEXT
    raw_json = json.dumps(dict(raw), ensure_ascii=False, default=str)
    if reason is not None:
        store.add_listing(
            deps.ids.new_id(), listing, raw_json, deps.clock.now(), SCREENING_VERSION, reason=reason
        )
        return "rejected", reason.value, None

    company_id = _company_for(deps, listing)
    source = _listing_source(deps, company_id, listing)
    direct = _contacts_for(deps, company_id, source.id, listing)
    _evidence_for(deps, company_id, source.id, listing)

    lead = store.active_lead(company_id, campaign_id)
    result = "matched"
    if lead is None:
        lead = _create_lead(deps, actor, company_id, campaign_id, listing, today, direct)
        result = "created"
    store.add_listing(
        deps.ids.new_id(),
        listing,
        raw_json,
        deps.clock.now(),
        SCREENING_VERSION,
        company_id=company_id,
        source_id=source.id,
        lead_id=lead.id,
    )
    return result, None, lead.id


def _company_for(deps: Deps, listing: Listing) -> str:
    if listing.website_host:
        existing = deps.store.company_by_domain(listing.website_host)
        if existing:
            return existing
    # No domain: never merge by name (doc 03, T-02) — a new company per listing.
    company_id = deps.ids.new_id()
    deps.store.add_company(company_id, listing.company_name, listing.website_host, deps.clock.now())
    return company_id


def _listing_source(deps: Deps, company_id: str, listing: Listing) -> Source:
    snapshot = f"{listing.title}\n\n{listing.body}".strip()
    source = Source(
        id=deps.ids.new_id(),
        company_id=company_id,
        source_type=SourceType.LISTING,
        canonical_url=listing.source_url or f"listing:{listing.source_key}",
        fetch_status=FetchStatus.IMPORTED,
        fetched_at=deps.clock.now(),
        content_hash=hashlib.sha256(snapshot.encode()).hexdigest(),
        snapshot=snapshot,
        truncated=listing.body_truncated,
    )
    deps.store.add_source(source, deps.clock.now())
    return source


def _contacts_for(deps: Deps, company_id: str, source_id: str, listing: Listing) -> bool:
    """Store published contacts; returns True when a person's own work channel exists."""
    addresses: list[tuple[Address, str | None]] = []
    if listing.contact_address:
        addresses.append((classify_address(listing.contact_address), listing.contact_name))
    if listing.source_url:
        addresses.append((platform_address(listing.source_url), None))
    direct = False
    for address, name in addresses:
        if deps.store.contact_by_address(address.channel, address.normalized) is None:
            deps.store.add_contact(
                Contact(
                    id=deps.ids.new_id(),
                    company_id=company_id,
                    channel=address.channel,
                    normalized_address=address.normalized,
                    company_wide=address.is_company_wide,
                    name=name,
                    role=None if address.is_company_wide else AUTHOR_ROLE,
                    source_id=source_id,
                    verified_at=deps.clock.now(),  # published by the author in the listing
                ),
                deps.clock.now(),
            )
        direct = direct or not address.is_company_wide
    return direct


def _evidence_for(deps: Deps, company_id: str, source_id: str, listing: Listing) -> None:
    now = deps.clock.now()
    posted = listing.posted_on
    items = [
        ("title", "Заголовок объявления", listing.title),
        ("description", "Текст объявления", listing.body),  # full text, <= 12 000
        ("company", "Заказчик указан в объявлении", listing.company_name),
        ("budget", "Бюджет, заявленный в объявлении", _money(listing)),
        ("posted_at", "Дата публикации объявления", posted.isoformat() if posted else None),
        ("contact", "Контакт, опубликованный автором", _contact_text(listing)),
        ("url", "Адрес объявления", listing.source_url),
    ]
    for field_name, claim, excerpt in items:
        if not excerpt:
            continue
        deps.store.add_evidence(
            Evidence(
                id=deps.ids.new_id(),
                company_id=company_id,
                source_id=source_id,
                field=field_name,
                claim=claim,
                excerpt=excerpt,
                observed_at=now,
                # The publication date dates the whole listing: the event is the stated need.
                event_date=posted,
                date_precision=DatePrecision.DAY if posted else DatePrecision.UNKNOWN,
            )
        )


def _money(listing: Listing) -> str | None:
    budget = listing.budget
    return f"{budget.amount} {budget.currency}" if budget else None


def _contact_text(listing: Listing) -> str | None:
    parts = [listing.contact_name, listing.contact_address]
    return " · ".join(p for p in parts if p) or None


def _create_lead(
    deps: Deps,
    actor: User,
    company_id: str,
    campaign_id: str,
    listing: Listing,
    today: date,
    direct_contact: bool,
) -> Lead:
    triage = prescore(listing, today, has_direct_contact=direct_contact)
    lead = Lead(
        id=deps.ids.new_id(),
        company_id=company_id,
        campaign_id=campaign_id,
        stage=Stage.NEW,
        version=1,
        icp=triage.icp,
        product=PRODUCT_FOR_ICP.get(triage.icp),
        prescore_total=triage.total,
    )
    payload = json.dumps(
        {"components": triage.components, "reasons": triage.reasons, "total": triage.total},
        ensure_ascii=False,
    )
    deps.store.add_lead(lead, payload, triage.version, deps.clock.now())
    deps.audit(actor, "lead", lead.id, "create", new_version=1, reason=f"prescore {triage.total}")
    return lead
