"""S2-02: a read is traceable, failures explicit, receipts never replaced."""

import hashlib
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from hunter.application.ports import FetchResult
from hunter.application.sources import read_source
from hunter.domain.entities import FetchStatus
from hunter.infrastructure.postgres.sources import PostgresSources

NOW = datetime(2026, 11, 3, tzinfo=UTC)
URL = "https://example.com/"


def uid():
    return str(uuid.uuid4())


class FakeFetcher:
    def __init__(self, body=None, code=None):
        self.body, self.code = body, code

    def fetch(self, url):
        return FetchResult(
            url,
            FetchStatus.FETCHED if self.code is None else FetchStatus.UNAVAILABLE,
            self.body,
            hashlib.sha256(self.body.encode()).hexdigest() if self.body else None,
            self.code,
        )


def read(body="Два новых филиала", code=None):
    return read_source(FakeFetcher(body, code), url=URL, source_id=uid(), now=NOW)


class Store(PostgresSources):
    def save(self, read, evidence=()):
        return super().save(read, evidence, actor_id="worker", correlation_id="test-run")


def company(db):
    i = uid()
    db.execute(text("INSERT INTO companies (id,name) VALUES (:id,'Пример')"), {"id": i})
    return i


def test_exact_excerpt_is_required():
    r = read()
    with pytest.raises(ValueError, match="not present"):
        r.evidence(evidence_id=uid(), company_id=uid(), claim="Рост", excerpt="Выручка выросла")
    e = r.evidence(evidence_id=uid(), company_id=uid(), claim="Рост", excerpt="Два новых филиала")
    assert e.source_id == r.source.id and e.observed_at == NOW
    assert r.source.fetched_at == NOW and r.source.content_hash


def test_failed_read_does_not_create_evidence():
    r = read(None, "timeout")
    assert r.source.error_code == "timeout" and r.text is None
    with pytest.raises(ValueError, match="received"):
        r.evidence(evidence_id=uid(), company_id=uid(), claim="Рост", excerpt="Вымысел")


def test_bad_redirect_url_is_recorded_as_failure_of_requested_source():
    class Denied:
        def fetch(self, url):
            return FetchResult(
                "file:///etc/passwd", FetchStatus.DENIED, error_code="scheme_not_allowed"
            )

    r = read_source(Denied(), url=URL, source_id=uid(), now=NOW)
    assert r.source.canonical_url == URL and r.source.error_code == "scheme_not_allowed"


def test_receipts_keep_evidence_linked_to_original_text(db):
    owner = company(db)
    first = read()
    e = first.evidence(evidence_id=uid(), company_id=owner, claim="Расширение", excerpt=first.text)
    repo = Store(db)
    old_id = repo.save(first, (e,))
    assert repo.save(read()) == old_id  # unchanged content is a receipt hit
    new_id = repo.save(read("Три новых филиала"))
    assert new_id != old_id
    row = db.execute(
        text("SELECT source_id,excerpt FROM evidence WHERE id=:id"), {"id": e.id}
    ).one()
    assert str(row.source_id) == old_id and row.excerpt == first.text
    assert db.execute(text("SELECT count(*) FROM sources")).scalar_one() == 2
    assert db.execute(text("SELECT count(*) FROM audit_events")).scalar_one() == 3


def test_failed_fetches_without_hash_remain_separate_and_keep_reason(db):
    repo = Store(db)
    assert repo.save(read(None, "timeout")) != repo.save(read(None, "timeout"))
    rows = db.execute(text("SELECT error_code,content_hash,fetched_at FROM sources")).all()
    assert len(rows) == 2 and all(tuple(r) == ("timeout", None, NOW) for r in rows)


def test_untraceable_evidence_is_rejected_before_any_write(db):
    from dataclasses import replace

    r = read()
    e = r.evidence(evidence_id=uid(), company_id=company(db), claim="Рост", excerpt=r.text)
    with pytest.raises(ValueError, match="not present"):
        Store(db).save(r, (replace(e, excerpt="Выдумано"),))
    assert db.execute(text("SELECT count(*) FROM sources")).scalar_one() == 0
