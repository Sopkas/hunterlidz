"""Idempotency keys, send attempts, tasks, inbound replies and contact bans. (H-09)"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from hunter.application.ports import InboundRow, TaskRow
from hunter.infrastructure.sqlite.db import dt_from_db, to_db


class OutreachRepo:
    conn: sqlite3.Connection

    # idempotency
    def idempotent_result(self, key: str) -> tuple[str, str] | None:
        row = self.conn.execute(
            "SELECT request_hash, result FROM idempotency_keys WHERE key = ?", (key,)
        ).fetchone()
        return (row["request_hash"], row["result"]) if row else None

    def save_idempotent(
        self, key: str, scope: str, request_hash: str, result: str, at: datetime
    ) -> None:
        self.conn.execute(
            "INSERT INTO idempotency_keys VALUES (?, ?, ?, ?, ?)",
            (key, scope, request_hash, result, to_db(at)),
        )

    # sending
    def add_send_attempt(
        self,
        attempt_id: str,
        *,
        message_id: str,
        version: int,
        approval_id: str,
        key: str,
        sent_at: datetime,
        actor_id: str,
        at: datetime,
    ) -> None:
        self.conn.execute(
            "INSERT INTO send_attempts VALUES (?, ?, ?, ?, ?, 'manual', 'sent', ?, ?, ?)",
            (
                attempt_id,
                message_id,
                version,
                approval_id,
                key,
                to_db(sent_at),
                actor_id,
                to_db(at),
            ),
        )

    def mark_message_sent(self, message_id: str, sent_at: datetime, at: datetime) -> bool:
        cursor = self.conn.execute(
            "UPDATE messages SET status = 'sent', sent_at = ?, updated_at = ? "
            "WHERE id = ? AND status = 'approved'",
            (to_db(sent_at), to_db(at), message_id),
        )
        return cursor.rowcount == 1

    def first_sent_at(self, lead_id: str) -> datetime | None:
        row = self.conn.execute(
            "SELECT MIN(sent_at) AS s FROM messages WHERE lead_id = ? AND status = 'sent'",
            (lead_id,),
        ).fetchone()
        return dt_from_db(row["s"])

    def cancel_chain_messages(self, lead_id: str, at: datetime) -> list[str]:
        rows = self.conn.execute(
            "SELECT id FROM messages WHERE lead_id = ? "
            "AND status IN ('draft', 'pending_review', 'approved')",
            (lead_id,),
        ).fetchall()
        ids = [r["id"] for r in rows]
        for message_id in ids:
            self.conn.execute(
                "UPDATE approvals SET revoked_at = ?, revoke_reason = 'chain paused' "
                "WHERE message_id = ? AND revoked_at IS NULL",
                (to_db(at), message_id),
            )
            self.conn.execute(
                "UPDATE messages SET status = 'cancelled', updated_at = ? WHERE id = ?",
                (to_db(at), message_id),
            )
        return ids

    # tasks
    def add_task(
        self,
        task_id: str,
        lead_id: str,
        task_type: str,
        step: int,
        due_at: datetime,
        at: datetime,
        note: str | None = None,
    ) -> bool:
        try:
            self.conn.execute(
                "INSERT INTO tasks (id, lead_id, type, sequence_step, due_at, status, note, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?)",
                (task_id, lead_id, task_type, step, to_db(due_at), note, to_db(at), to_db(at)),
            )
        except sqlite3.IntegrityError:
            return False  # the same open task already exists
        return True

    def open_tasks(self, lead_id: str | None = None, due_before: datetime | None = None):
        clauses, params = ["status = 'open'"], []
        if lead_id:
            clauses.append("lead_id = ?")
            params.append(lead_id)
        if due_before:
            clauses.append("due_at <= ?")
            params.append(to_db(due_before))
        rows = self.conn.execute(
            f"SELECT * FROM tasks WHERE {' AND '.join(clauses)} ORDER BY due_at, rowid", params
        )
        return [_task(r) for r in rows]

    def task(self, task_id: str) -> TaskRow | None:
        row = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _task(row) if row else None

    def close_task(self, task_id: str, status: str, at: datetime, reason: str | None) -> bool:
        cursor = self.conn.execute(
            "UPDATE tasks SET status = ?, cancel_reason = ?, updated_at = ? "
            "WHERE id = ? AND status = 'open'",
            (status, reason, to_db(at), task_id),
        )
        return cursor.rowcount == 1

    def cancel_open_tasks(self, lead_id: str, at: datetime, reason: str) -> int:
        cursor = self.conn.execute(
            "UPDATE tasks SET status = 'cancelled', cancel_reason = ?, updated_at = ? "
            "WHERE lead_id = ? AND status = 'open'",
            (reason, to_db(at), lead_id),
        )
        return cursor.rowcount

    # inbound
    def add_inbound(
        self,
        inbound_id: str,
        *,
        lead_id: str,
        contact_id: str | None,
        channel: str,
        external_event_id: str,
        body: str,
        received_at: datetime,
        at: datetime,
    ) -> bool:
        try:
            self.conn.execute(
                "INSERT INTO inbound_messages (id, lead_id, contact_id, channel, "
                "external_event_id, body, received_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    inbound_id,
                    lead_id,
                    contact_id,
                    channel,
                    external_event_id,
                    body,
                    to_db(received_at),
                    to_db(at),
                ),
            )
        except sqlite3.IntegrityError:
            return False
        return True

    def inbound_by_event(self, channel: str, external_event_id: str) -> InboundRow | None:
        row = self.conn.execute(
            "SELECT * FROM inbound_messages WHERE channel = ? AND external_event_id = ?",
            (channel, external_event_id),
        ).fetchone()
        return _inbound(row) if row else None

    def inbound(self, inbound_id: str) -> InboundRow | None:
        row = self.conn.execute(
            "SELECT * FROM inbound_messages WHERE id = ?", (inbound_id,)
        ).fetchone()
        return _inbound(row) if row else None

    def inbound_ids_with_prefix(self, prefix: str) -> list[str]:
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self.conn.execute(
            "SELECT id FROM inbound_messages WHERE id LIKE ? ESCAPE '\\' LIMIT 2", (escaped + "%",)
        )
        return [r["id"] for r in rows]

    def unreviewed_inbound(self, lead_id: str | None = None) -> list[InboundRow]:
        where, params = "reviewed_at IS NULL", []
        if lead_id:
            where += " AND lead_id = ?"
            params.append(lead_id)
        rows = self.conn.execute(
            f"SELECT * FROM inbound_messages WHERE {where} ORDER BY received_at", params
        )
        return [_inbound(r) for r in rows]

    def set_inbound_proposal(self, inbound_id: str, proposal: dict) -> None:
        self.conn.execute(
            "UPDATE inbound_messages SET proposal = ? WHERE id = ?",
            (json.dumps(proposal, ensure_ascii=False), inbound_id),
        )

    def review_inbound(
        self,
        inbound_id: str,
        *,
        reply_class: str,
        action: str,
        actor_id: str,
        expected_version: int,
        at: datetime,
    ) -> bool:
        cursor = self.conn.execute(
            "UPDATE inbound_messages SET reply_class = ?, action = ?, reviewed_by = ?, "
            "reviewed_at = ?, version = version + 1 WHERE id = ? AND version = ? "
            "AND reviewed_at IS NULL",
            (reply_class, action, actor_id, to_db(at), inbound_id, expected_version),
        )
        return cursor.rowcount == 1

    # suppressions
    def add_suppression(
        self,
        suppression_id: str,
        *,
        group_id: str,
        scope_type: str,
        scope_id: str,
        match_domain: str | None,
        match_address: str | None,
        reason: str,
        source_event_id: str | None,
        actor_id: str,
        at: datetime,
    ) -> None:
        self.conn.execute(
            "INSERT INTO suppressions (id, group_id, scope_type, scope_id, match_domain, "
            "match_address, reason, source_event_id, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                suppression_id,
                group_id,
                scope_type,
                scope_id,
                match_domain,
                match_address,
                reason,
                source_event_id,
                actor_id,
                to_db(at),
            ),
        )

    def company_suppressed(self, company_id: str) -> bool:
        """A company ban, or every stored contact of the company is banned individually."""
        company_ban = self.conn.execute(
            "SELECT 1 FROM suppressions WHERE lifted_at IS NULL AND scope_type = 'company' "
            "AND scope_id = ?",
            (company_id,),
        ).fetchone()
        if company_ban:
            return True
        row = self.conn.execute(
            "SELECT COUNT(*) AS total, SUM(EXISTS (SELECT 1 FROM suppressions s "
            "WHERE s.lifted_at IS NULL AND s.scope_type = 'contact' AND s.scope_id = c.id)) "
            "AS banned FROM contacts c WHERE c.company_id = ?",
            (company_id,),
        ).fetchone()
        return bool(row["total"]) and row["banned"] == row["total"]

    def contact_blocked(self, company_id: str, contact_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM suppressions WHERE lifted_at IS NULL AND ("
            "(scope_type = 'company' AND scope_id = ?) OR "
            "(scope_type = 'contact' AND scope_id = ?))",
            (company_id, contact_id),
        ).fetchone()
        return row is not None

    def suppressed_match(self, domain: str | None, address: str | None) -> bool:
        """For imports: a new listing of a banned company or contact is screened out."""
        if not domain and not address:
            return False
        row = self.conn.execute(
            "SELECT 1 FROM suppressions WHERE lifted_at IS NULL AND ("
            "(match_domain IS NOT NULL AND match_domain = ?) OR "
            "(match_address IS NOT NULL AND match_address = ?))",
            (domain, address),
        ).fetchone()
        return row is not None

    def active_suppressions(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM suppressions WHERE lifted_at IS NULL AND id = group_id "
            "ORDER BY created_at"
        ).fetchall()

    def lift_suppression(
        self, suppression_id: str, actor_id: str, reason: str, at: datetime
    ) -> bool:
        cursor = self.conn.execute(
            "UPDATE suppressions SET lifted_at = ?, lifted_by = ?, lift_reason = ? "
            "WHERE group_id = ? AND lifted_at IS NULL",
            (to_db(at), actor_id, reason, suppression_id),
        )
        return cursor.rowcount > 0  # the whole group of one ban decision


def _task(row: sqlite3.Row) -> TaskRow:
    return TaskRow(
        id=row["id"],
        lead_id=row["lead_id"],
        type=row["type"],
        sequence_step=row["sequence_step"],
        due_at=dt_from_db(row["due_at"]),
        status=row["status"],
        note=row["note"],
    )


def _inbound(row: sqlite3.Row) -> InboundRow:
    return InboundRow(
        id=row["id"],
        lead_id=row["lead_id"],
        contact_id=row["contact_id"],
        body=row["body"],
        received_at=dt_from_db(row["received_at"]),
        proposal=json.loads(row["proposal"]) if row["proposal"] else None,
        reply_class=row["reply_class"],
        action=row["action"],
        version=row["version"],
    )
