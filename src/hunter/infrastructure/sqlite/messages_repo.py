"""Messages, versions and approvals. (H-07)"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from hunter.domain.messages import (
    Approval,
    Message,
    MessageKind,
    MessageStatus,
    MessageVersion,
    Recipient,
)
from hunter.infrastructure.sqlite.db import dt_from_db, to_db


class MessagesRepo:
    conn: sqlite3.Connection

    def add_message(self, message: Message, at: datetime) -> None:
        self.conn.execute(
            "INSERT INTO messages (id, lead_id, kind, sequence_step, status, current_version, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                message.id,
                message.lead_id,
                message.kind.value,
                message.sequence_step,
                message.status.value,
                message.current_version,
                to_db(at),
                to_db(at),
            ),
        )

    def add_version(self, v: MessageVersion, generation: dict | None = None) -> None:
        self.conn.execute(
            "INSERT INTO message_versions (message_id, version, body, contact_id, channel, "
            "normalized_address, payload_hash, evidence_ids, case_claim_ids, review_reasons, "
            "blockers, generation, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                v.message_id,
                v.version,
                v.body,
                v.recipient.contact_id,
                v.recipient.channel,
                v.recipient.normalized_address,
                v.payload_hash,
                json.dumps(list(v.evidence_ids)),
                json.dumps(list(v.case_claim_ids)),
                json.dumps(list(v.review_reasons), ensure_ascii=False),
                json.dumps(list(v.blockers), ensure_ascii=False),
                json.dumps(generation, ensure_ascii=False) if generation else None,
                v.created_by,
                to_db(v.created_at),
            ),
        )

    def message(self, message_id: str) -> Message | None:
        row = self.conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return _message(row) if row else None

    def message_ids_with_prefix(self, prefix: str) -> list[str]:
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self.conn.execute(
            "SELECT id FROM messages WHERE id LIKE ? ESCAPE '\\' LIMIT 2", (escaped + "%",)
        )
        return [r["id"] for r in rows]

    def version(self, message_id: str, version: int) -> MessageVersion | None:
        row = self.conn.execute(
            "SELECT * FROM message_versions WHERE message_id = ? AND version = ?",
            (message_id, version),
        ).fetchone()
        return _version(row) if row else None

    def generation(self, message_id: str, version: int) -> dict | None:
        row = self.conn.execute(
            "SELECT generation FROM message_versions WHERE message_id = ? AND version = ?",
            (message_id, version),
        ).fetchone()
        return json.loads(row["generation"]) if row and row["generation"] else None

    def set_message_state(
        self,
        message_id: str,
        *,
        status: MessageStatus,
        current_version: int,
        expected_version: int,
        at: datetime,
        skip_reason: str | None = None,
    ) -> bool:
        """Optimistic update: only applies when the stored version is the one the caller saw."""
        cursor = self.conn.execute(
            "UPDATE messages SET status = ?, current_version = ?, skip_reason = ?, updated_at = ? "
            "WHERE id = ? AND current_version = ?",
            (status.value, current_version, skip_reason, to_db(at), message_id, expected_version),
        )
        return cursor.rowcount == 1

    def active_message(self, lead_id: str, kind: MessageKind, step: int) -> Message | None:
        row = self.conn.execute(
            "SELECT * FROM messages WHERE lead_id = ? AND kind = ? AND sequence_step = ? "
            "AND status IN ('draft', 'pending_review', 'approved')",
            (lead_id, kind.value, step),
        ).fetchone()
        return _message(row) if row else None

    def messages_for_lead(self, lead_id: str) -> list[Message]:
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE lead_id = ? ORDER BY created_at, rowid", (lead_id,)
        )
        return [_message(r) for r in rows]

    def review_queue(self, limit: int = 50) -> list[Message]:
        rows = self.conn.execute(
            "SELECT m.* FROM messages m JOIN leads l ON l.id = m.lead_id "
            "WHERE m.status IN ('pending_review', 'draft') "
            "ORDER BY COALESCE(l.score_total, 0) DESC, m.created_at LIMIT ?",
            (limit,),
        )
        return [_message(r) for r in rows]

    def add_approval(self, approval: Approval) -> None:
        self.conn.execute(
            "INSERT INTO approvals (id, message_id, message_version, payload_hash, actor_id, "
            "approved_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                approval.id,
                approval.message_id,
                approval.message_version,
                approval.payload_hash,
                approval.actor_id,
                to_db(approval.approved_at),
                to_db(approval.expires_at),
            ),
        )

    def live_approval(self, message_id: str) -> Approval | None:
        row = self.conn.execute(
            "SELECT * FROM approvals WHERE message_id = ? AND revoked_at IS NULL", (message_id,)
        ).fetchone()
        return _approval(row) if row else None

    def approval(self, approval_id: str) -> Approval | None:
        row = self.conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        return _approval(row) if row else None

    def revoke_approvals(self, message_id: str, at: datetime, reason: str) -> int:
        cursor = self.conn.execute(
            "UPDATE approvals SET revoked_at = ?, revoke_reason = ? "
            "WHERE message_id = ? AND revoked_at IS NULL",
            (to_db(at), reason, message_id),
        )
        return cursor.rowcount

    def set_lead_stage(self, lead_id: str, stage: str, expected_version: int, at: datetime) -> bool:
        cursor = self.conn.execute(
            "UPDATE leads SET stage = ?, version = version + 1, updated_at = ? "
            "WHERE id = ? AND version = ?",
            (stage, to_db(at), lead_id, expected_version),
        )
        return cursor.rowcount == 1


def _message(row: sqlite3.Row) -> Message:
    return Message(
        id=row["id"],
        lead_id=row["lead_id"],
        kind=MessageKind(row["kind"]),
        sequence_step=row["sequence_step"],
        status=MessageStatus(row["status"]),
        current_version=row["current_version"],
        skip_reason=row["skip_reason"],
        sent_at=dt_from_db(row["sent_at"]),
    )


def _version(row: sqlite3.Row) -> MessageVersion:
    return MessageVersion(
        message_id=row["message_id"],
        version=row["version"],
        body=row["body"],
        recipient=Recipient(row["contact_id"], row["channel"], row["normalized_address"]),
        payload_hash=row["payload_hash"],
        evidence_ids=tuple(json.loads(row["evidence_ids"])),
        case_claim_ids=tuple(json.loads(row["case_claim_ids"])),
        review_reasons=tuple(json.loads(row["review_reasons"])),
        blockers=tuple(json.loads(row["blockers"])),
        created_by=row["created_by"],
        created_at=dt_from_db(row["created_at"]),
    )


def _approval(row: sqlite3.Row) -> Approval:
    return Approval(
        id=row["id"],
        message_id=row["message_id"],
        message_version=row["message_version"],
        payload_hash=row["payload_hash"],
        actor_id=row["actor_id"],
        approved_at=dt_from_db(row["approved_at"]),
        expires_at=dt_from_db(row["expires_at"]),
        revoked_at=dt_from_db(row["revoked_at"]),
    )
