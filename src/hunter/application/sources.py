"""Source read step for S2-02. Network first; persistence is a separate transaction in S2-10.

Source text is data. A supplied excerpt must occur in the received text (T-06); no claims are
inferred from a failed read. This step does not decide access rights or start a research run.
"""

from dataclasses import dataclass
from datetime import datetime

from hunter.application.ports import SourceFetcher
from hunter.domain.entities import Evidence, FetchStatus, Source, SourceType


@dataclass(frozen=True, slots=True)
class SourceRead:
    source: Source
    text: str | None
    truncated: bool

    def evidence(self, *, evidence_id: str, company_id: str, claim: str, excerpt: str) -> Evidence:
        if self.source.fetch_status is not FetchStatus.FETCHED or not self.text:
            raise ValueError("evidence requires a received source")
        if not excerpt.strip() or excerpt not in self.text:
            raise ValueError("excerpt is not present in the received source")
        return Evidence(
            id=evidence_id,
            company_id=company_id,
            source_id=self.source.id,
            claim=claim,
            excerpt=excerpt,
            observed_at=self.source.fetched_at,
        )


def read_source(fetcher: SourceFetcher, *, url: str, source_id: str, now: datetime) -> SourceRead:
    # Validate the original address before calling an adapter. A denied redirect can have an
    # invalid target: store the valid requested URL with the error, not that target as a Source.
    Source(source_id, url, SourceType.WEBSITE, FetchStatus.ERROR, error_code="pending")
    result = fetcher.fetch(url)
    ok = result.status is FetchStatus.FETCHED
    source = Source(
        id=source_id,
        canonical_url=result.url if ok else url,
        source_type=SourceType.WEBSITE,
        fetch_status=result.status,
        fetched_at=now,
        content_hash=result.content_hash if ok else None,
        error_code=None if ok else result.error_code,
    )
    return SourceRead(source, result.text if ok else None, result.truncated if ok else False)
