"""Read-only queries for metrics and export. (H-11)"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal

from hunter.infrastructure.sqlite.db import to_db

_REACHED = "lead_id IN (SELECT lead_id FROM messages WHERE kind = 'initial' AND status = 'sent')"


class ReportingRepo:
    conn: sqlite3.Connection

    def _one(self, sql: str, params: tuple = ()) -> int:
        return self.conn.execute(sql, params).fetchone()[0] or 0

    def metric_counts(self, window_start: datetime) -> dict[str, int]:
        one = self._one
        return {
            "leads_discovered": one("SELECT COUNT(*) FROM leads"),
            "rejected_at_import": one(
                "SELECT COUNT(*) FROM listings WHERE screening_status = 'rejected'"
            ),
            "researched": one("SELECT COUNT(DISTINCT lead_id) FROM scores"),
            # doc 07: admitted to outreach by score and checks (HOT, or WARM by operator exception)
            "research_qualified": one(
                "SELECT COUNT(*) FROM leads WHERE score_total >= 80 "
                "OR contact_exception_reason IS NOT NULL"
            ),
            "first_messages_sent": one(
                "SELECT COUNT(*) FROM messages WHERE kind = 'initial' AND status = 'sent'"
            ),
            "follow_ups_sent": one(
                "SELECT COUNT(*) FROM messages WHERE kind = 'follow_up' AND status = 'sent'"
            ),
            "contacts_reached": one(
                "SELECT COUNT(DISTINCT v.channel || ':' || v.normalized_address) "
                "FROM messages m JOIN message_versions v "
                "ON v.message_id = m.id AND v.version = m.current_version "
                "WHERE m.kind = 'initial' AND m.status = 'sent'"
            ),
            "contacts_in_open_window": one(
                "SELECT COUNT(*) FROM messages WHERE kind = 'initial' AND status = 'sent' "
                "AND sent_at > ?",
                (to_db(window_start),),
            ),
            # doc 07: replies and positives are counted only among reached recipients
            "replies": one(
                "SELECT COUNT(DISTINCT lead_id) FROM inbound_messages "
                f"WHERE COALESCE(reply_class, '') != 'auto_reply' AND {_REACHED}"
            ),
            "auto_replies": one(
                "SELECT COUNT(*) FROM inbound_messages WHERE reply_class = 'auto_reply'"
            ),
            "positive_replies": one(
                "SELECT COUNT(DISTINCT lead_id) FROM inbound_messages "
                f"WHERE reply_class = 'positive' AND reviewed_at IS NOT NULL AND {_REACHED}"
            ),
            "opt_outs": one("SELECT COUNT(DISTINCT group_id) FROM suppressions"),
            "approved_cards": one(
                "SELECT COUNT(DISTINCT message_id) FROM approvals a JOIN messages m "
                "ON m.id = a.message_id WHERE m.kind = 'initial'"
            ),
        }

    def ai_cost(self, currency: str) -> Decimal:
        rows = self.conn.execute(
            "SELECT cost FROM research_runs WHERE currency = ? "
            "UNION ALL SELECT cost FROM llm_usage WHERE currency = ?",
            (currency, currency),
        )
        return sum((Decimal(r["cost"]) for r in rows), Decimal(0))

    def export_rows(self) -> list[sqlite3.Row]:
        """One row per lead with its latest score and its first message, if any."""
        return self.conn.execute(
            """
            SELECT l.id AS lead_id, l.version AS lead_version, l.company_id,
                   c.name AS company_name, c.normalized_domain AS company_domain,
                   cp.name AS campaign, l.stage, l.priority, l.icp, l.product,
                   l.score_total, s.rules_version, l.prescore_total, l.prescore_version,
                   l.loss_reason,
                   m.id AS message_id, m.status AS message_status,
                   m.current_version AS message_version, m.sent_at,
                   v.body AS message_body, v.channel AS recipient_channel,
                   v.normalized_address AS recipient_address
            FROM leads l
            JOIN companies c ON c.id = l.company_id
            JOIN campaigns cp ON cp.id = l.campaign_id
            LEFT JOIN scores s ON s.id = (
                SELECT id FROM scores WHERE lead_id = l.id ORDER BY created_at DESC, rowid DESC
                LIMIT 1)
            LEFT JOIN messages m ON m.id = (
                SELECT id FROM messages WHERE lead_id = l.id AND kind = 'initial'
                ORDER BY created_at DESC, rowid DESC LIMIT 1)
            LEFT JOIN message_versions v
                ON v.message_id = m.id AND v.version = m.current_version
            ORDER BY COALESCE(l.score_total, -1) DESC, l.created_at
            """
        ).fetchall()

    def counts_for_check(self) -> dict[str, int]:
        tables = ("companies", "leads", "messages", "suppressions", "audit_events")
        return {t: self._one(f"SELECT COUNT(*) FROM {t}") for t in tables}
