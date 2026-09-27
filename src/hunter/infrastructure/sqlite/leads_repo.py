"""Campaigns, companies, sources, contacts, evidence, leads, listings, imports. (H-03)"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from hunter.domain.catalog import Icp, Priority, Product, Stage
from hunter.domain.contacts import Channel
from hunter.domain.entities import (
    Contact,
    DatePrecision,
    Evidence,
    FetchStatus,
    Lead,
    Source,
    SourceType,
)
from hunter.domain.listings import Listing
from hunter.domain.screening import RejectReason
from hunter.infrastructure.sqlite.db import date_from_db, dt_from_db, to_db


class LeadsRepo:
    conn: sqlite3.Connection

    # campaigns and imports
    def campaign_id(self, name: str) -> str | None:
        row = self.conn.execute("SELECT id FROM campaigns WHERE name = ?", (name,)).fetchone()
        return row["id"] if row else None

    def add_campaign(self, campaign_id: str, name: str, icp_version: str, at: datetime) -> None:
        self.conn.execute(
            "INSERT INTO campaigns (id, name, icp_version, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (campaign_id, name, icp_version, to_db(at), to_db(at)),
        )

    def add_import(
        self, import_id: str, campaign_id: str, file_name: str, actor_id: str, at: datetime
    ) -> None:
        self.conn.execute(
            "INSERT INTO imports VALUES (?, ?, ?, ?, ?)",
            (import_id, campaign_id, file_name, actor_id, to_db(at)),
        )

    def add_import_row(
        self, import_id: str, row_no: int, result: str, reason: str | None, lead_id: str | None
    ) -> None:
        self.conn.execute(
            "INSERT INTO import_rows (import_id, row_no, result, reason, lead_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (import_id, row_no, result, reason, lead_id),
        )

    # listings
    def listing_by_key(self, source_key: str) -> str | None:
        """Lead id of an already imported listing ('' when it was rejected), None if new."""
        row = self.conn.execute(
            "SELECT lead_id FROM listings WHERE source_key = ?", (source_key,)
        ).fetchone()
        return None if row is None else (row["lead_id"] or "")

    def listing_text_exists(self, text_hash: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM listings WHERE text_hash = ?", (text_hash,))
        return row.fetchone() is not None

    def add_listing(
        self,
        listing_id: str,
        listing: Listing,
        raw_json: str,
        at: datetime,
        screening_version: str,
        *,
        reason: RejectReason | None = None,
        company_id: str | None = None,
        source_id: str | None = None,
        lead_id: str | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO listings (id, source_key, text_hash, platform, ext_id, company_id, "
            "source_id, lead_id, screening_status, reject_reason, screening_version, raw, "
            "imported_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                listing_id,
                listing.source_key,
                listing.text_hash,
                listing.platform,
                listing.ext_id,
                company_id,
                source_id,
                lead_id,
                "rejected" if reason else "accepted",
                reason.value if reason else None,
                screening_version,
                raw_json,
                to_db(at),
            ),
        )

    def screening_summary(self) -> list[tuple[str, str | None, int]]:
        rows = self.conn.execute(
            "SELECT screening_status, reject_reason, COUNT(*) n FROM listings "
            "GROUP BY 1, 2 ORDER BY 1, 3 DESC"
        )
        return [(r["screening_status"], r["reject_reason"], r["n"]) for r in rows]

    # companies
    def company_by_domain(self, domain: str) -> str | None:
        row = self.conn.execute(
            "SELECT id FROM companies WHERE normalized_domain = ? AND merged_into_id IS NULL",
            (domain,),
        ).fetchone()
        return row["id"] if row else None

    def add_company(
        self, company_id: str, name: str | None, domain: str | None, at: datetime
    ) -> None:
        self.conn.execute(
            "INSERT INTO companies (id, name, normalized_domain, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (company_id, name, domain, to_db(at), to_db(at)),
        )

    def company(self, company_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()

    # sources and evidence
    def add_source(self, source: Source, at: datetime) -> None:
        self.conn.execute(
            "INSERT INTO sources (id, company_id, source_type, canonical_url, fetch_status, "
            "fetched_at, content_hash, snapshot, truncated, error_code, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source.id,
                source.company_id,
                source.source_type.value,
                source.canonical_url,
                source.fetch_status.value,
                to_db(source.fetched_at),
                source.content_hash,
                source.snapshot,
                int(source.truncated),
                source.error_code,
                to_db(at),
            ),
        )

    def sources_for(self, company_id: str) -> list[Source]:
        rows = self.conn.execute(
            "SELECT * FROM sources WHERE company_id = ? ORDER BY created_at, id", (company_id,)
        )
        return [_source(r) for r in rows]

    def add_evidence(self, item: Evidence) -> None:
        self.conn.execute(
            "INSERT INTO evidence (id, company_id, source_id, field, claim, excerpt, event_date, "
            "date_precision, observed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item.id,
                item.company_id,
                item.source_id,
                item.field,
                item.claim,
                item.excerpt,
                to_db(item.event_date),
                item.date_precision.value,
                to_db(item.observed_at),
            ),
        )

    def evidence_for(self, company_id: str) -> list[Evidence]:
        rows = self.conn.execute(
            "SELECT * FROM evidence WHERE company_id = ? ORDER BY observed_at, rowid", (company_id,)
        )
        return [
            Evidence(
                id=r["id"],
                company_id=r["company_id"],
                source_id=r["source_id"],
                field=r["field"],
                claim=r["claim"],
                excerpt=r["excerpt"],
                observed_at=dt_from_db(r["observed_at"]),
                event_date=date_from_db(r["event_date"]),
                date_precision=DatePrecision(r["date_precision"]),
            )
            for r in rows
        ]

    # contacts
    def contact_by_address(self, channel: Channel, address: str) -> Contact | None:
        row = self.conn.execute(
            "SELECT * FROM contacts WHERE channel = ? AND normalized_address = ?",
            (channel.value, address),
        ).fetchone()
        return _contact(row) if row else None

    def add_contact(self, contact: Contact, at: datetime) -> None:
        self.conn.execute(
            "INSERT INTO contacts (id, company_id, name, role, channel, normalized_address, "
            "company_wide, source_id, verified_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                contact.id,
                contact.company_id,
                contact.name,
                contact.role,
                contact.channel.value,
                contact.normalized_address,
                int(contact.company_wide),
                contact.source_id,
                to_db(contact.verified_at),
                to_db(at),
            ),
        )

    def contacts_for(self, company_id: str) -> list[Contact]:
        rows = self.conn.execute(
            "SELECT * FROM contacts WHERE company_id = ? ORDER BY company_wide, created_at",
            (company_id,),
        )
        return [_contact(r) for r in rows]

    def contact(self, contact_id: str) -> Contact | None:
        row = self.conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
        return _contact(row) if row else None

    # leads
    def add_lead(self, lead: Lead, prescore_json: str, prescore_version: str, at: datetime) -> None:
        self.conn.execute(
            "INSERT INTO leads (id, company_id, campaign_id, icp, product, stage, priority, "
            "score_total, prescore_total, prescore, prescore_version, needs_review, version, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                lead.id,
                lead.company_id,
                lead.campaign_id,
                lead.icp.value,
                lead.product.value if lead.product else None,
                lead.stage.value,
                lead.priority.value if lead.priority else None,
                lead.score_total,
                lead.prescore_total,
                prescore_json,
                prescore_version,
                int(lead.needs_review),
                lead.version,
                to_db(at),
                to_db(at),
            ),
        )

    def active_lead(self, company_id: str, campaign_id: str) -> Lead | None:
        row = self.conn.execute(
            "SELECT * FROM leads WHERE company_id = ? AND campaign_id = ? "
            "AND stage NOT IN ('WON', 'LOST')",
            (company_id, campaign_id),
        ).fetchone()
        return _lead(row) if row else None

    def lead(self, lead_id: str) -> Lead | None:
        row = self.conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return _lead(row) if row else None

    def set_loss_reason(self, lead_id: str, reason: str) -> None:
        self.conn.execute("UPDATE leads SET loss_reason = ? WHERE id = ?", (reason, lead_id))

    def leads_of_company(self, company_id: str) -> list[Lead]:
        rows = self.conn.execute("SELECT * FROM leads WHERE company_id = ?", (company_id,))
        return [_lead(r) for r in rows]

    def lead_ids_with_prefix(self, prefix: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT id FROM leads WHERE id LIKE ? ESCAPE '\\' LIMIT 2",
            (prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",),
        )
        return [r["id"] for r in rows]

    def lead_prescore(self, lead_id: str) -> dict | None:
        row = self.conn.execute("SELECT prescore FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return json.loads(row["prescore"]) if row and row["prescore"] else None

    def list_leads(
        self, *, stage: Stage | None = None, priority: Priority | None = None, limit: int = 50
    ) -> list[Lead]:
        clauses, params = [], []
        if stage:
            clauses.append("stage = ?")
            params.append(stage.value)
        if priority:
            clauses.append("priority = ?")
            params.append(priority.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM leads {where} "
            "ORDER BY COALESCE(score_total, -1) DESC, prescore_total DESC, created_at LIMIT ?",
            (*params, limit),
        )
        return [_lead(r) for r in rows]

    def listing_title(self, lead_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT s.snapshot FROM listings l JOIN sources s ON s.id = l.source_id "
            "WHERE l.lead_id = ? ORDER BY l.imported_at LIMIT 1",
            (lead_id,),
        ).fetchone()
        return row["snapshot"].split("\n", 1)[0] if row else None


def _lead(row: sqlite3.Row) -> Lead:
    return Lead(
        id=row["id"],
        company_id=row["company_id"],
        campaign_id=row["campaign_id"],
        stage=Stage(row["stage"]),
        version=row["version"],
        icp=Icp(row["icp"]),
        product=Product(row["product"]) if row["product"] else None,
        priority=Priority(row["priority"]) if row["priority"] else None,
        score_total=row["score_total"],
        prescore_total=row["prescore_total"],
        needs_review=bool(row["needs_review"]),
        loss_reason=row["loss_reason"],
    )


def _contact(row: sqlite3.Row) -> Contact:
    return Contact(
        id=row["id"],
        company_id=row["company_id"],
        channel=Channel(row["channel"]),
        normalized_address=row["normalized_address"],
        company_wide=bool(row["company_wide"]),
        name=row["name"],
        role=row["role"],
        source_id=row["source_id"],
        verified_at=dt_from_db(row["verified_at"]),
    )


def _source(row: sqlite3.Row) -> Source:
    return Source(
        id=row["id"],
        company_id=row["company_id"],
        source_type=SourceType(row["source_type"]),
        canonical_url=row["canonical_url"],
        fetch_status=FetchStatus(row["fetch_status"]),
        fetched_at=dt_from_db(row["fetched_at"]),
        content_hash=row["content_hash"],
        snapshot=row["snapshot"],
        truncated=bool(row["truncated"]),
        error_code=row["error_code"],
    )
