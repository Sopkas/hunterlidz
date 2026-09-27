"""Source lookup and the case claims registry. (H-04)"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from hunter.domain.cases import CaseClaim, ClaimStatus
from hunter.domain.entities import Source
from hunter.infrastructure.sqlite.db import to_db
from hunter.infrastructure.sqlite.leads_repo import _source


class SourcesRepo:
    conn: sqlite3.Connection

    def source_by_url(self, source_type: str, url: str) -> Source | None:
        row = self.conn.execute(
            "SELECT * FROM sources WHERE source_type = ? AND canonical_url = ?", (source_type, url)
        ).fetchone()
        return _source(row) if row else None

    def replace_source(self, source: Source, at: datetime) -> None:
        """A re-read of the same URL replaces the snapshot and keeps the source id."""
        self.conn.execute(
            "UPDATE sources SET fetch_status = ?, fetched_at = ?, content_hash = ?, snapshot = ?, "
            "truncated = ?, error_code = ? WHERE id = ?",
            (
                source.fetch_status.value,
                to_db(source.fetched_at or at),
                source.content_hash,
                source.snapshot,
                int(source.truncated),
                source.error_code,
                source.id,
            ),
        )

    def active_case_claim(self, case_key: str) -> CaseClaim | None:
        row = self.conn.execute(
            "SELECT * FROM case_claims WHERE case_key = ? AND active = 1", (case_key,)
        ).fetchone()
        return _claim(row) if row else None

    def add_case_claim(self, claim: CaseClaim, at: datetime) -> None:
        self.conn.execute(
            "UPDATE case_claims SET active = 0 WHERE case_key = ? AND active = 1", (claim.case_key,)
        )
        self.conn.execute(
            "INSERT INTO case_claims (id, case_key, claim_text, status, external_allowed, "
            "source_ref, approved_by, claim_version, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                claim.id,
                claim.case_key,
                claim.claim_text,
                claim.status.value,
                int(claim.external_allowed),
                claim.source_ref,
                claim.approved_by,
                claim.claim_version,
                to_db(at),
            ),
        )

    def case_claims(self, *, active_only: bool = True) -> list[CaseClaim]:
        where = "WHERE active = 1" if active_only else ""
        rows = self.conn.execute(f"SELECT * FROM case_claims {where} ORDER BY case_key")
        return [_claim(r) for r in rows]

    def case_claim(self, claim_id: str) -> CaseClaim | None:
        row = self.conn.execute("SELECT * FROM case_claims WHERE id = ?", (claim_id,)).fetchone()
        return _claim(row) if row else None


def _claim(row: sqlite3.Row) -> CaseClaim:
    return CaseClaim(
        id=row["id"],
        case_key=row["case_key"],
        claim_text=row["claim_text"],
        status=ClaimStatus(row["status"]),
        external_allowed=bool(row["external_allowed"]),
        claim_version=row["claim_version"],
        source_ref=row["source_ref"],
        approved_by=row["approved_by"],
    )
