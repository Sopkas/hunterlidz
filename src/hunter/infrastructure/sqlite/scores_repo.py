"""Scores and lead scoring fields. (H-06)"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from hunter.domain.scoring import Score
from hunter.infrastructure.sqlite.db import to_db


class ScoresRepo:
    conn: sqlite3.Connection

    def add_score(
        self, score_id: str, lead_id: str, run_id: str | None, score: Score, at: datetime
    ) -> None:
        c = score.components
        self.conn.execute(
            "INSERT INTO scores (id, lead_id, research_run_id, icp_fit, signal, problem_fit, "
            "budget, contactability, total, rules_version, reasons, contradictions, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                score_id,
                lead_id,
                run_id,
                c["icp_fit"],
                c["signal"],
                c["problem_fit"],
                c["budget"],
                c["contactability"],
                score.total,
                score.rules_version,
                json.dumps(dict(score.reasons), ensure_ascii=False),
                json.dumps(list(score.contradictions), ensure_ascii=False),
                to_db(at),
            ),
        )

    def latest_score(self, lead_id: str) -> Score | None:
        row = self.conn.execute(
            "SELECT * FROM scores WHERE lead_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (lead_id,),
        ).fetchone()
        if row is None:
            return None
        return Score(
            components={
                k: row[k] for k in ("icp_fit", "signal", "problem_fit", "budget", "contactability")
            },
            reasons=json.loads(row["reasons"]),
            contradictions=tuple(json.loads(row["contradictions"])),
            rules_version=row["rules_version"],
        )

    def update_lead_score(
        self,
        lead_id: str,
        *,
        total: int,
        priority: str,
        stage: str,
        needs_review: bool,
        expected_version: int,
        at: datetime,
    ) -> bool:
        cursor = self.conn.execute(
            "UPDATE leads SET score_total = ?, priority = ?, stage = ?, needs_review = ?, "
            "version = version + 1, updated_at = ? WHERE id = ? AND version = ?",
            (total, priority, stage, int(needs_review), to_db(at), lead_id, expected_version),
        )
        return cursor.rowcount == 1

    def set_contact_exception(
        self, lead_id: str, reason: str, actor_id: str, expected_version: int, at: datetime
    ) -> bool:
        cursor = self.conn.execute(
            "UPDATE leads SET contact_exception_reason = ?, contact_exception_by = ?, "
            "version = version + 1, updated_at = ? WHERE id = ? AND version = ?",
            (reason, actor_id, to_db(at), lead_id, expected_version),
        )
        return cursor.rowcount == 1

    def contact_exception(self, lead_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT contact_exception_reason FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
        return row["contact_exception_reason"] if row else None

    def leads_in_stage(self, stage: str, limit: int = 200) -> list[str]:
        rows = self.conn.execute(
            "SELECT id FROM leads WHERE stage = ? ORDER BY score_total DESC, created_at LIMIT ?",
            (stage, limit),
        )
        return [r["id"] for r in rows]
