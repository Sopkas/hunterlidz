"""Job queue on PostgreSQL: lease, heartbeat, recovery after a worker died. (S2-06, T-18)

Runs against HUNTER_TEST_DATABASE_URL like the other storage tests; skipped without it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from hunter.infrastructure.postgres.jobs import MAX_ATTEMPTS, JobQueue

T0 = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
LEASE = timedelta(minutes=5)
AFTER_LEASE = T0 + LEASE + timedelta(seconds=1)


def new_id() -> str:
    return str(uuid.uuid4())


def status(db, job_id: str) -> str:
    return db.execute(text("SELECT status FROM jobs WHERE id = :id"), {"id": job_id}).scalar_one()


def company(db) -> str:
    company_id = new_id()
    db.execute(text("INSERT INTO companies (id, name) VALUES (:id, 'Альфа')"), {"id": company_id})
    return company_id


def write_result(db, queue: JobQueue, job, company_id: str, version: int, name: str) -> bool:
    """What a worker does with its result: finish the job and write the entity with its
    version in one transaction; anything refused rolls the whole step back."""
    with db.begin_nested() as step:
        updated = db.execute(
            text(
                "UPDATE companies SET name = :n, version = version + 1"
                " WHERE id = :id AND version = :v"
            ),
            {"n": name, "id": company_id, "v": version},
        ).rowcount
        if not (queue.finish(job) and updated == 1):
            step.rollback()
            return False
    return True


def test_t18_a_worker_that_lost_its_lease_cannot_store_a_result(db):
    queue, target = JobQueue(db), company(db)
    queue.enqueue("research", target, run_after=T0)
    first = queue.claim("research", T0, LEASE)
    second = queue.claim("research", AFTER_LEASE, LEASE)  # the first worker hung
    assert second.id == first.id and second.lease_token != first.lease_token
    assert not queue.heartbeat(first, AFTER_LEASE, LEASE)
    assert not write_result(db, queue, first, target, 1, "от первого")
    assert write_result(db, queue, second, target, 1, "от второго")
    row = db.execute(text("SELECT name, version FROM companies WHERE id = :id"), {"id": target})
    assert tuple(row.one()) == ("от второго", 2)
    assert status(db, first.id) == "done"


def test_a_stale_entity_version_rolls_the_finish_back(db):
    queue, target = JobQueue(db), company(db)
    queue.enqueue("research", target, run_after=T0)
    job = queue.claim("research", T0, LEASE)
    assert not write_result(db, queue, job, target, 7, "устаревшая версия")
    assert status(db, job.id) == "running"


def test_a_restart_does_not_lose_the_job_and_keeps_the_saved_step(db):
    queue = JobQueue(db)
    queue.enqueue("research", new_id(), run_after=T0)
    job = queue.claim("research", T0, LEASE)
    assert queue.claim("research", T0 + timedelta(minutes=1), LEASE) is None  # lease still held
    assert queue.heartbeat(job, T0 + timedelta(minutes=4), LEASE, {"research": "done"})
    assert queue.claim("research", AFTER_LEASE, LEASE) is None  # the heartbeat extended it
    resumed = queue.claim("research", T0 + timedelta(minutes=10), LEASE)
    assert resumed.id == job.id and resumed.attempt == 2
    assert resumed.checkpoint == {"research": "done"}


def test_heartbeat_without_a_checkpoint_keeps_the_saved_one(db):
    queue = JobQueue(db)
    queue.enqueue("research", new_id(), run_after=T0)
    job = queue.claim("research", T0, LEASE)
    queue.heartbeat(job, T0, LEASE, {"step": 1})
    queue.heartbeat(job, T0, LEASE)
    stored = db.execute(text("SELECT checkpoint FROM jobs WHERE id = :id"), {"id": job.id})
    assert stored.scalar_one() == {"step": 1}


def test_one_live_job_per_entity_and_kind(db):
    queue, entity = JobQueue(db), new_id()
    first = queue.enqueue("research", entity)
    assert queue.enqueue("research", entity) is None
    assert queue.enqueue("draft", entity) is not None
    job = queue.claim("research", datetime.now(UTC), LEASE)
    queue.finish(job)
    assert queue.enqueue("research", entity) not in (None, first)  # done work can run again


def test_retry_waits_for_run_after_and_stops_after_the_last_attempt(db):
    queue = JobQueue(db)
    job_id = queue.enqueue("research", new_id(), run_after=T0)
    now = T0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        job = queue.claim("research", now, LEASE)
        assert job.attempt == attempt
        now += timedelta(seconds=30)
        assert queue.retry(job, "rate_limited", run_after=now)
        assert queue.claim("research", now - timedelta(seconds=1), LEASE) is None
    assert status(db, job_id) == "failed"
    assert queue.claim("research", now, LEASE) is None


def test_a_job_that_keeps_losing_its_lease_fails(db):
    queue = JobQueue(db)
    job_id = queue.enqueue("research", new_id(), run_after=T0)
    now = T0
    for _ in range(MAX_ATTEMPTS):
        assert queue.claim("research", now, LEASE) is not None
        now += LEASE + timedelta(seconds=1)
    assert queue.claim("research", now, LEASE) is None
    assert db.execute(
        text("SELECT status, last_error FROM jobs WHERE id = :id"), {"id": job_id}
    ).one() == ("failed", "lease_lost")


def test_finish_with_an_error_fails_the_job(db):
    queue = JobQueue(db)
    job_id = queue.enqueue("research", new_id(), run_after=T0)
    assert queue.finish(queue.claim("research", T0, LEASE), "quota")
    assert status(db, job_id) == "failed"


def test_a_running_job_always_has_a_lease(db):
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(
            text("INSERT INTO jobs (id, kind, entity_id, status) VALUES (:id, 'k', :e, 'running')"),
            {"id": new_id(), "e": new_id()},
        )


def test_two_workers_never_claim_the_same_job(pg_engine):
    # Two connections, both transactions open: SKIP LOCKED hands out different jobs.
    kind = f"probe-{new_id()}"
    with pg_engine.begin() as setup:
        for _ in range(2):
            JobQueue(setup).enqueue(kind, new_id(), run_after=T0)
    try:
        with pg_engine.connect() as one, pg_engine.connect() as two:
            with one.begin(), two.begin():
                first = JobQueue(one).claim(kind, T0, LEASE)
                second = JobQueue(two).claim(kind, T0, LEASE)
                assert first and second and first.id != second.id
                assert JobQueue(two).claim(kind, T0, LEASE) is None
    finally:
        with pg_engine.begin() as cleanup:
            cleanup.execute(text("DELETE FROM jobs WHERE kind = :k"), {"k": kind})
