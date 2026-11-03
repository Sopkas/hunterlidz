"""Append source receipts and their exact excerpts (S2-02, FR-03).

The caller opens a transaction AFTER the network read. A repeated unchanged page reuses the
receipt, changed content gets a new id; old evidence never points at a replacement snapshot.
Full page bodies are not stored here: only hash, URL, fetch time and selected excerpts.
"""

from dataclasses import replace

from sqlalchemy import Connection, text

from hunter.application.sources import SourceRead
from hunter.domain.entities import Evidence


class PostgresSources:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def save(
        self,
        read: SourceRead,
        evidence: tuple[Evidence, ...] = (),
        *,
        actor_id: str,
        correlation_id: str,
    ) -> str:
        if not actor_id.strip() or not correlation_id.strip():
            raise ValueError("source write requires actor and correlation id")
        source = read.source
        # Validate all excerpts before the first write. Rejected / invented evidence must not
        # leave a half-written receipt when the caller handles a validation error.
        for item in evidence:
            if item.source_id != source.id:
                raise ValueError("evidence belongs to another source")
            read.evidence(
                evidence_id=item.id,
                company_id=item.company_id,
                claim=item.claim,
                excerpt=item.excerpt,
            )
            if item.version != 1:
                raise ValueError("new evidence starts at version 1")
        params = dict(
            id=source.id,
            url=source.canonical_url,
            kind=source.source_type.value,
            status=source.fetch_status.value,
            at=source.fetched_at,
            hash=source.content_hash,
            ref=source.snapshot_ref,
            error=source.error_code,
        )
        source_id = self._connection.execute(
            text(
                "INSERT INTO sources (id, canonical_url, source_type, fetch_status, fetched_at,"
                " content_hash, snapshot_ref, error_code)"
                " VALUES (:id, :url, :kind, :status, :at, :hash, :ref, :error)"
                " ON CONFLICT (source_type, canonical_url, content_hash) DO NOTHING RETURNING id"
            ),
            params,
        ).scalar_one_or_none()
        if source_id is None:
            source_id = self._connection.execute(
                text(
                    "SELECT id FROM sources WHERE source_type=:kind"
                    " AND canonical_url=:url AND content_hash=:hash"
                ),
                params,
            ).scalar_one()
        for item in evidence:
            item = replace(item, source_id=str(source_id))
            self._connection.execute(
                text(
                    "INSERT INTO evidence (id, company_id, source_id, claim, excerpt, observed_at,"
                    " event_at, date_precision, verification_status)"
                    " VALUES (:id, :company, :source, :claim, :excerpt, :at, :event, :precision,"
                    " :verification)"
                ),
                dict(
                    id=item.id,
                    company=item.company_id,
                    source=item.source_id,
                    claim=item.claim,
                    excerpt=item.excerpt,
                    at=item.observed_at,
                    event=item.event_at,
                    precision=item.date_precision.value,
                    verification=item.verification_status.value,
                ),
            )
        self._connection.execute(
            text(
                "INSERT INTO audit_events (entity_type, entity_id, actor_id, action,"
                " correlation_id, reason) VALUES ('source', :id, :actor, :action, :run, :reason)"
            ),
            dict(
                id=str(source_id),
                actor=actor_id,
                action=f"fetch:{source.fetch_status.value}",
                run=correlation_id,
                reason=source.error_code,
            ),
        )
        return str(source_id)
