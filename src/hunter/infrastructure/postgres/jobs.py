"""Job queue in PostgreSQL: atomic claim with a lease, heartbeat, recovery. (S2-06, T-18)

A worker claims a job and gets a lease token; every later write names the token. A worker
that died leaves its lease to expire, and the next claim takes the job over with a new token
and the saved checkpoint, so finished steps are not run again. The old worker's writes then
match no row: it cannot store a conflicting result (doc 09, "Ошибки и повторные
попытки").

The caller finishes the job in the same transaction as the result it writes, and writes the
entity with its version (`WHERE version = :v`): `finish` returning False means "not yours any
more" and the transaction is rolled back. Which failures are worth a retry is the caller's
decision (ADR-0002, "Что повторяет очередь"); the queue only limits the attempts.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Connection, text

# Doc 09: temporary errors get up to two repeats, so three attempts in all.
MAX_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    kind: str
    entity_id: str
    attempt: int
    lease_token: str
    checkpoint: dict | None


class JobQueue:
    def __init__(self, connection: Connection, max_attempts: int = MAX_ATTEMPTS) -> None:
        self._connection = connection
        self._max_attempts = max_attempts

    def enqueue(self, kind: str, entity_id: str, run_after: datetime | None = None) -> str | None:
        """New job id, or None when the same work is already queued or running."""
        return self._connection.execute(
            text(
                "INSERT INTO jobs (id, kind, entity_id, run_after)"
                " VALUES (:id, :kind, :entity, coalesce(:after, now()))"
                " ON CONFLICT (kind, entity_id) WHERE status IN ('queued', 'running') DO NOTHING"
                " RETURNING id"
            ),
            {"id": str(uuid.uuid4()), "kind": kind, "entity": entity_id, "after": run_after},
        ).scalar_one_or_none()

    def claim(self, kind: str, now: datetime, lease: timedelta) -> Job | None:
        """The oldest due job of this kind, or a running one whose lease expired."""
        # A lost lease counts as an attempt: a job that kills its worker every time stops here.
        self._connection.execute(
            text(
                "UPDATE jobs SET status = 'failed', last_error = 'lease_lost',"
                " lease_token = NULL, lease_until = NULL"
                " WHERE kind = :kind AND status = 'running' AND lease_until < :now"
                " AND attempt >= :max"
            ),
            {"kind": kind, "now": now, "max": self._max_attempts},
        )
        row = self._connection.execute(
            text(
                "UPDATE jobs SET status = 'running', attempt = attempt + 1,"
                " lease_token = :token, lease_until = :until"
                " WHERE id = (SELECT id FROM jobs WHERE kind = :kind AND ("
                "   (status = 'queued' AND run_after <= :now)"
                "   OR (status = 'running' AND lease_until < :now))"
                " ORDER BY run_after, created_at, id LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " RETURNING id, kind, entity_id, attempt, lease_token, checkpoint"
            ),
            {"kind": kind, "now": now, "until": now + lease, "token": str(uuid.uuid4())},
        ).one_or_none()
        if row is None:
            return None
        return Job(
            id=str(row.id),
            kind=row.kind,
            entity_id=str(row.entity_id),
            attempt=row.attempt,
            lease_token=str(row.lease_token),
            checkpoint=row.checkpoint,
        )

    def heartbeat(
        self, job: Job, now: datetime, lease: timedelta, checkpoint: dict | None = None
    ) -> bool:
        """Extend the lease and save the finished steps; False when the lease was lost."""
        return self._owned(
            "lease_until = :until, checkpoint = coalesce(CAST(:checkpoint AS jsonb), checkpoint)",
            job,
            until=now + lease,
            checkpoint=None if checkpoint is None else json.dumps(checkpoint),
        )

    def finish(self, job: Job, error: str | None = None) -> bool:
        """Done, or failed with the reason; False when the lease was lost."""
        return self._owned(
            "status = :status, last_error = :error, lease_token = NULL, lease_until = NULL",
            job,
            status="done" if error is None else "failed",
            error=error,
        )

    def retry(self, job: Job, error: str, run_after: datetime) -> bool:
        """Back to the queue after a temporary failure, or failed once attempts run out."""
        return self._owned(
            "status = CASE WHEN attempt >= :max THEN 'failed' ELSE 'queued' END,"
            " last_error = :error, run_after = :after, lease_token = NULL, lease_until = NULL",
            job,
            max=self._max_attempts,
            error=error,
            after=run_after,
        )

    def _owned(self, assignments: str, job: Job, **values) -> bool:
        result = self._connection.execute(
            text(
                f"UPDATE jobs SET {assignments}"
                " WHERE id = :id AND status = 'running' AND lease_token = :token"
            ),
            {"id": job.id, "token": job.lease_token, **values},
        )
        return result.rowcount == 1
