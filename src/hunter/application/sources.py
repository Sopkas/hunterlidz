"""Reading allowed public sources into snapshots and evidence. (H-04, FR-03)

A URL alone never proves a successful read: the source keeps its fetch status, and a failure
is stored with its error code so research can report insufficient data instead of guessing
(doc 08 "Нет источника"). Page text is stored as data; it never becomes an instruction.
"""

from __future__ import annotations

from dataclasses import dataclass

from hunter.application.access import require
from hunter.application.context import Deps
from hunter.application.errors import ErrorCode, HunterError
from hunter.application.ports import SourceFetcher
from hunter.domain.entities import Evidence, FetchStatus, Source, SourceType
from hunter.domain.roles import Permission
from hunter.domain.users import User

MAX_SOURCES_PER_COMPANY = 6  # doc 09: up to 6 source reads per research run
WEBSITE_EXCERPT_LIMIT = 2_000


@dataclass(frozen=True, slots=True)
class SourceReport:
    url: str
    status: FetchStatus
    error_code: str | None


def fetch_company_website(
    deps: Deps, actor: User, lead_id: str, fetcher: SourceFetcher
) -> SourceReport | None:
    """Read the company home page if the company has a known domain. None when there is none."""
    require(actor, Permission.WRITE_RESEARCH)
    store = deps.store
    lead = store.lead(lead_id)
    if lead is None:
        raise HunterError(ErrorCode.NOT_FOUND, "лид не найден")
    company = store.company(lead.company_id)
    domain = company["normalized_domain"] if company else None
    if not domain:
        return None
    if len(store.sources_for(lead.company_id)) >= MAX_SOURCES_PER_COMPANY:
        raise HunterError(ErrorCode.BUDGET_EXCEEDED, "достигнут лимит источников компании")

    url = f"https://{domain}/"
    result = fetcher.fetch(url)  # network outside the transaction
    now = deps.clock.now()
    ok = result.status is FetchStatus.FETCHED
    with store.transaction():
        existing = store.source_by_url(SourceType.WEBSITE, url)
        source = Source(
            id=existing.id if existing else deps.ids.new_id(),
            company_id=lead.company_id,
            source_type=SourceType.WEBSITE,
            canonical_url=url,
            fetch_status=result.status,
            fetched_at=now,
            content_hash=result.content_hash,
            snapshot=result.text if ok else None,
            truncated=result.truncated,
            error_code=None if ok else result.error_code,
        )
        if existing:
            store.replace_source(source, now)
        else:
            store.add_source(source, now)
        if ok and result.text and result.text.strip():
            store.add_evidence(
                Evidence(
                    id=deps.ids.new_id(),
                    company_id=lead.company_id,
                    source_id=source.id,
                    field="website",
                    claim="Текст главной страницы сайта компании",
                    excerpt=result.text[:WEBSITE_EXCERPT_LIMIT],
                    observed_at=now,
                )
            )
        deps.audit(actor, "source", source.id, f"fetch:{result.status}", reason=result.error_code)
    return SourceReport(url=url, status=result.status, error_code=result.error_code)
