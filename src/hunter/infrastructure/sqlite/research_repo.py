"""Jobs with leases and research runs. (H-05)

A job is claimed atomically: a queued job, or a running one whose lease has expired (the
worker died). Every write of a result checks the lease token first, so a worker that lost its
lease cannot store a conflicting result (T-18).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal

from hunter.application.ports import Job, RunRecord
from hunter.infrastructure.sqlite.db import to_db


class ResearchRepo:
    conn: sqlite3.Connection

    # jobs
    def add_job(self, job_id: str, kind: str, entity_id: str, at: datetime) -> bool:
        """False when an active job for the entity already exists."""
        try:
            self.conn.execute(
                "INSERT INTO jobs (id, kind, entity_id, status, run_after, created_at, updated_at) "
                "VALUES (?, ?, ?, 'queued', ?, ?, ?)",
                (job_id, kind, entity_id, to_db(at), to_db(at), to_db(at)),
            )
        except sqlite3.IntegrityError:
            return False
        return True

    def claim_job(self, kind: str, token: str, now: datetime, lease_until: datetime) -> Job | None:
        row = self.conn.execute(
            "SELECT id FROM jobs WHERE kind = ? AND run_after <= ? AND (status = 'queued' "
            "OR (status = 'running' AND lease_until < ?)) ORDER BY created_at, id LIMIT 1",
            (kind, to_db(now), to_db(now)),
        ).fetchone()
        if row is None:
            return None
        self.conn.execute(
            "UPDATE jobs SET status = 'running', attempt = attempt + 1, lease_token = ?, "
            "lease_until = ?, updated_at = ? WHERE id = ?",
            (token, to_db(lease_until), to_db(now), row["id"]),
        )
        return self.job(row["id"])

    def job(self, job_id: str) -> Job | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return Job(
            id=row["id"],
            kind=row["kind"],
            entity_id=row["entity_id"],
            attempt=row["attempt"],
            lease_token=row["lease_token"],
            checkpoint=row["checkpoint"],
        )

    def job_owned(self, job_id: str, token: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM jobs WHERE id = ? AND status = 'running' AND lease_token = ?",
            (job_id, token),
        ).fetchone()
        return row is not None

    def extend_lease(self, job_id: str, token: str, until: datetime, checkpoint: str) -> bool:
        cursor = self.conn.execute(
            "UPDATE jobs SET lease_until = ?, checkpoint = ? "
            "WHERE id = ? AND status = 'running' AND lease_token = ?",
            (to_db(until), checkpoint, job_id, token),
        )
        return cursor.rowcount == 1

    def finish_job(
        self, job_id: str, token: str, status: str, at: datetime, error: str | None = None
    ) -> bool:
        cursor = self.conn.execute(
            "UPDATE jobs SET status = ?, last_error = ?, lease_token = NULL, lease_until = NULL, "
            "updated_at = ? WHERE id = ? AND status = 'running' AND lease_token = ?",
            (status, error, to_db(at), job_id, token),
        )
        return cursor.rowcount == 1

    def active_jobs(self, kind: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE kind = ? AND status IN ('queued', 'running')", (kind,)
        )
        return row.fetchone()[0]

    # research runs
    def research_candidates(self, min_prescore: int, limit: int) -> list[str]:
        """NEW leads without a finished research run and without an active job."""
        rows = self.conn.execute(
            "SELECT l.id FROM leads l WHERE l.stage = 'NEW' AND l.prescore_total >= ? "
            "AND NOT EXISTS (SELECT 1 FROM research_runs r WHERE r.lead_id = l.id "
            "  AND r.status IN ('completed', 'needs_review', 'insufficient_evidence')) "
            "AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.kind = 'research' AND j.entity_id = l.id "
            "  AND j.status IN ('queued', 'running')) "
            "ORDER BY l.prescore_total DESC, l.created_at LIMIT ?",
            (min_prescore, limit),
        )
        return [r["id"] for r in rows]

    def add_run(
        self,
        run_id: str,
        lead_id: str,
        job_id: str,
        model_id: str,
        prompt_version: str,
        schema_version: str,
        currency: str,
        at: datetime,
    ) -> None:
        self.conn.execute(
            "INSERT INTO research_runs (id, lead_id, job_id, status, model_id, prompt_version, "
            "schema_version, currency, started_at) VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?)",
            (
                run_id,
                lead_id,
                job_id,
                model_id,
                prompt_version,
                schema_version,
                currency,
                to_db(at),
            ),
        )

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        llm_calls: int,
        input_tokens: int,
        output_tokens: int,
        cost: Decimal,
        at: datetime,
        error_code: str | None = None,
        result: str | None = None,
        problems: str | None = None,
    ) -> None:
        self.conn.execute(
            "UPDATE research_runs SET status = ?, llm_calls = ?, input_tokens = ?, "
            "output_tokens = ?, cost = ?, error_code = ?, result = ?, problems = ?, "
            "finished_at = ? WHERE id = ?",
            (
                status,
                llm_calls,
                input_tokens,
                output_tokens,
                str(cost),
                error_code,
                result,
                problems,
                to_db(at),
                run_id,
            ),
        )

    def finished_run_for_job(self, job_id: str) -> RunRecord | None:
        row = self.conn.execute(
            "SELECT * FROM research_runs WHERE job_id = ? AND status != 'running'", (job_id,)
        ).fetchone()
        return _run(row) if row else None

    def latest_run(self, lead_id: str) -> RunRecord | None:
        row = self.conn.execute(
            "SELECT * FROM research_runs WHERE lead_id = ? AND status != 'running' "
            "ORDER BY started_at DESC, rowid DESC LIMIT 1",
            (lead_id,),
        ).fetchone()
        return _run(row) if row else None

    def cost_since(self, since: datetime, currency: str) -> Decimal:
        rows = self.conn.execute(
            "SELECT cost FROM research_runs WHERE started_at >= ? AND currency = ? "
            "UNION ALL SELECT cost FROM llm_usage WHERE at >= ? AND currency = ?",
            (to_db(since), currency, to_db(since), currency),
        )
        return sum((Decimal(r["cost"]) for r in rows), Decimal(0))

    def add_usage(
        self,
        usage_id: str,
        *,
        purpose: str,
        lead_id: str | None,
        model_id: str,
        status: str,
        llm_calls: int,
        input_tokens: int,
        output_tokens: int,
        cost: Decimal,
        currency: str,
        at: datetime,
    ) -> None:
        self.conn.execute(
            "INSERT INTO llm_usage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                usage_id,
                purpose,
                lead_id,
                model_id,
                status,
                llm_calls,
                input_tokens,
                output_tokens,
                str(cost),
                currency,
                to_db(at),
            ),
        )

    def update_lead_research(
        self, lead_id: str, *, icp: str, product: str | None, needs_review: bool, at: datetime
    ) -> None:
        self.conn.execute(
            "UPDATE leads SET icp = ?, product = ?, needs_review = ?, version = version + 1, "
            "updated_at = ? WHERE id = ?",
            (icp, product, int(needs_review), to_db(at), lead_id),
        )


def _run(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        id=row["id"],
        lead_id=row["lead_id"],
        job_id=row["job_id"],
        status=row["status"],
        model_id=row["model_id"],
        llm_calls=row["llm_calls"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cost=Decimal(row["cost"]),
        currency=row["currency"],
        error_code=row["error_code"],
        result=row["result"],
        problems=row["problems"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )
