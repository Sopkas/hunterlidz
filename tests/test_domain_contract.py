"""Invariants of the shared domain types (S1-05 contract): an object is valid or not created."""

import dataclasses
from datetime import UTC, date, datetime

import pytest

from hunter.domain.catalog import PRODUCT_FOR_ICP, Icp, Priority, Product, Stage, priority_for
from hunter.domain.entities import (
    Channel,
    Company,
    Contact,
    DatePrecision,
    Evidence,
    FetchStatus,
    Lead,
    Source,
    SourceType,
)
from hunter.domain.modes import RunMode

NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
NAIVE = datetime(2026, 10, 12, 9, 0)


# --- doc 04: priority, stages and their separation ------------------------------------------


@pytest.mark.parametrize(
    ("total", "priority"),
    [(0, Priority.SKIP), (39, Priority.SKIP), (40, Priority.WATCH), (59, Priority.WATCH),
     (60, Priority.WARM), (79, Priority.WARM), (80, Priority.HOT), (100, Priority.HOT)],
    ids=lambda v: str(v),
)  # fmt: skip
def test_priority_boundaries(total, priority):
    assert priority_for(total) is priority


@pytest.mark.parametrize("total", [-1, 101])
def test_priority_out_of_range(total):
    with pytest.raises(ValueError):
        priority_for(total)


def test_stages_follow_doc_04():
    assert [s.value for s in Stage] == [
        "NEW", "RESEARCHED", "APPROVED", "CONTACTED", "REPLIED", "QUALIFIED",
        "CALL", "PROPOSAL", "NEGOTIATION", "WON", "LOST", "NURTURE",
    ]  # fmt: skip


def test_priority_and_stage_do_not_mix():
    assert not {p.value for p in Priority} & {s.value for s in Stage}
    with pytest.raises(ValueError):
        Lead(id="l", company_id="c", campaign_id="k", stage=Priority.HOT)
    with pytest.raises(ValueError):
        Lead(id="l", company_id="c", campaign_id="k", stage="NEW")  # plain text is not a Stage
    with pytest.raises(ValueError):
        Lead(id="l", company_id="c", campaign_id="k", stage=Stage.NEW, priority=Stage.WON)


def test_each_icp_has_one_offer():
    assert set(PRODUCT_FOR_ICP) == set(Icp) and set(PRODUCT_FOR_ICP.values()) == set(Product)


def test_run_modes_v0():
    assert {m.value for m in RunMode} == {"shadow", "manual", "paused"}


# --- company ------------------------------------------------------------------------------


def test_company_minimal_and_frozen():
    company = Company(id="c1", name="Тест Авто")
    assert company.normalized_domain is None and company.version == 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        company.name = "Другое"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "  "},
        {"normalized_domain": "Test-Auto.example"},
        {"normalized_domain": "https://test-auto.example"},
        {"normalized_domain": "test-auto.example/path"},
        {"normalized_domain": "localhost"},
        {"normalized_domain": "пример.рф"},
        {"normalized_domain": "XN--E1AFMKFD.XN--P1AI"},
        {"normalized_domain": "www.example.com"},
        {"normalized_domain": "example.com."},
        {"id": " "},
        {"size_min": 50, "size_max": 10},
        {"size_min": -1},
        {"industry": ""},
        {"merged_into_id": "c1"},
        {"version": 0},
    ],
    ids=["blank-name", "upper", "scheme", "path", "no-dot", "idn", "upper-punycode", "www",
         "trailing-dot", "blank-id", "sizes", "negative", "blank", "self", "version"],
)  # fmt: skip
def test_company_rejects(kwargs):
    with pytest.raises(ValueError):
        Company(**{"id": "c1", "name": "Тест Авто", **kwargs})


@pytest.mark.parametrize("domain", ["example.com", "example.co.uk", "xn--e1afmkfd.xn--p1ai"])
def test_company_accepts_normalized_domain(domain):
    assert Company(id="c1", name="Тест", normalized_domain=domain).normalized_domain == domain


# --- source and evidence ------------------------------------------------------------------


def test_source_received_and_failed():
    ok = Source(
        id="s1",
        canonical_url="https://test-auto.example/news",
        source_type=SourceType.WEBSITE,
        fetch_status=FetchStatus.FETCHED,
        fetched_at=NOW,
        content_hash="sha256:ab",
    )
    assert ok.error_code is None
    failed = Source(
        id="s2",
        canonical_url="https://test-auto.example/",
        source_type=SourceType.WEBSITE,
        fetch_status=FetchStatus.UNAVAILABLE,
        error_code="timeout",
    )
    assert failed.content_hash is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"canonical_url": "ftp://test-auto.example"},
        {"canonical_url": "test-auto.example"},
        {"fetch_status": FetchStatus.FETCHED, "fetched_at": NOW},  # no hash
        {"fetch_status": FetchStatus.FETCHED, "fetched_at": NAIVE, "content_hash": "h"},
        {"fetch_status": FetchStatus.ERROR, "error_code": None},
        {"fetch_status": FetchStatus.FETCHED, "fetched_at": NOW, "content_hash": "h",
         "error_code": "x"},
    ],
    ids=["ftp", "relative", "no-hash", "naive", "no-error", "both"],
)  # fmt: skip
def test_source_rejects(kwargs):
    base = {
        "id": "s",
        "canonical_url": "https://test-auto.example",
        "source_type": SourceType.WEBSITE,
        "fetch_status": FetchStatus.DENIED,
        "error_code": "blocked",
    }
    with pytest.raises(ValueError):
        Source(**{**base, **kwargs})


def test_evidence_unknown_date_is_none_not_guessed():
    fact = Evidence(
        id="e1",
        company_id="c1",
        source_id="s1",
        claim="Открыт второй филиал",
        excerpt="Мы открыли второй сервис",
        observed_at=NOW,
    )
    assert fact.event_at is None and fact.date_precision is DatePrecision.UNKNOWN


@pytest.mark.parametrize(
    ("event_at", "precision"),
    [
        (date(2026, 3, 15), DatePrecision.DAY),
        (date(2026, 3, 1), DatePrecision.MONTH),
        (date(2026, 1, 1), DatePrecision.YEAR),
    ],
    ids=["day", "month", "year"],
)
def test_evidence_period_is_stored_as_its_first_day(event_at, precision):
    fact = Evidence(
        id="e1",
        company_id="c1",
        source_id="s1",
        claim="Открыт второй филиал",
        excerpt="Мы открыли второй сервис",
        observed_at=NOW,
        event_at=event_at,
        date_precision=precision,
    )
    assert fact.event_at == event_at


@pytest.mark.parametrize(
    "kwargs",
    [
        {"event_at": date(2026, 9, 1)},  # date without precision
        {"date_precision": DatePrecision.MONTH},  # precision without date
        {"excerpt": " "},
        {"claim": ""},
        {"observed_at": NAIVE},
        {"event_at": date(2026, 3, 15), "date_precision": DatePrecision.MONTH},
        {"event_at": date(2026, 3, 1), "date_precision": DatePrecision.YEAR},
        {"source_id": ""},
    ],
    ids=["date-only", "precision-only", "excerpt", "claim", "naive", "month-not-1st",
         "year-not-jan-1", "blank-source"],
)  # fmt: skip
def test_evidence_rejects(kwargs):
    base = {
        "id": "e",
        "company_id": "c",
        "source_id": "s",
        "claim": "c",
        "excerpt": "x",
        "observed_at": NOW,
    }
    with pytest.raises(ValueError):
        Evidence(**{**base, **kwargs})


# --- contact and lead ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"normalized_address": ""},
        {"normalized_address": " ops@test-auto.example"},
        {"channel": "email"},
        {"name": ""},
        {"verified_at": NAIVE},
    ],
    ids=["empty", "untrimmed", "plain-channel", "blank-name", "naive"],
)
def test_contact_rejects(kwargs):
    base = {
        "id": "p",
        "company_id": "c",
        "channel": Channel.EMAIL,
        "normalized_address": "ops@test-auto.example",
    }
    Contact(**base)  # the base itself is valid
    with pytest.raises(ValueError):
        Contact(**{**base, **kwargs})


def test_new_lead_has_no_guessed_values():
    lead = Lead(id="l1", company_id="c1", campaign_id="pilot-1", stage=Stage.NEW)
    assert (lead.icp, lead.product, lead.priority, lead.owner_id) == (None, None, None, None)
    assert lead.active


def test_lost_needs_reason_and_is_closed():
    lost = Lead(
        id="l", company_id="c", campaign_id="k", stage=Stage.LOST, loss_reason="нет бюджета"
    )
    assert not lost.active
    with pytest.raises(ValueError):
        Lead(id="l", company_id="c", campaign_id="k", stage=Stage.LOST)
    with pytest.raises(ValueError):
        Lead(id="l", company_id="c", campaign_id="k", stage=Stage.NEW, loss_reason="x")


def test_won_is_closed_and_next_action_must_be_utc():
    assert not Lead(id="l", company_id="c", campaign_id="k", stage=Stage.WON).active
    with pytest.raises(ValueError):
        Lead(id="l", company_id="c", campaign_id="k", stage=Stage.NEW, next_action_at=NAIVE)
