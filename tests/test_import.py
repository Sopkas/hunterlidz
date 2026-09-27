from datetime import date

import pytest

from hunter.application.errors import ErrorCode, HunterError
from hunter.application.import_listings import import_listings
from hunter.domain.catalog import Icp, Stage
from hunter.domain.contacts import Channel
from hunter.domain.listings import parse_listing
from hunter.domain.roles import Role
from hunter.domain.screening import RejectReason, screen
from tests.helpers import add_user, make_deps

TODAY = date(2026, 9, 27)
GOOD = {
    "ext_id": "A1",
    "title": "Нужна CRM для сети автосервисов",
    "body": (
        "Сеть из 4 автосервисов, открываем филиалы. Заявки в Excel теряются. Нужна разработка "
        "CRM: запись клиентов, распределение заявок, отчёты, интеграция с телефонией. " * 2
    ),
    "company": "ООО Тест Авто",
    "website": "https://www.test-auto.example/about",
    "budget": "400 000 ₽",
    "posted_at": "2026-09-20",
    "contact": "@test_auto",
    "contact_name": "Собственник",
    "url": "https://fl-demo.example/tasks/A1",
}


@pytest.fixture
def ctx():
    deps = make_deps()
    return deps, add_user(deps, "op", Role.OPERATOR)


def run(ctx, rows, campaign="pilot"):
    deps, actor = ctx
    return import_listings(
        deps,
        actor,
        rows,
        file_name="f.json",
        campaign=campaign,
        platform="fl-demo",
        currency="RUB",
        today=TODAY,
    )


def test_reimport_matches_same_lead_t01(ctx):
    deps, _ = ctx
    first = run(ctx, [GOOD])
    assert first.rows[0].result == "created"
    again = run(ctx, [GOOD])
    assert again.rows[0].result == "matched"
    assert again.rows[0].lead_id == first.rows[0].lead_id
    assert len(deps.store.list_leads()) == 1


def test_same_domain_new_listing_reuses_active_lead(ctx):
    deps, _ = ctx
    lead_id = run(ctx, [GOOD]).rows[0].lead_id
    other = {**GOOD, "ext_id": "A2", "url": "https://fl-demo.example/tasks/A2"}
    other["body"] = "Ещё: " + GOOD["body"]
    row = run(ctx, [other]).rows[0]
    assert (row.result, row.lead_id) == ("matched", lead_id)
    lead = deps.store.lead(lead_id)
    assert len(deps.store.sources_for(lead.company_id)) == 2


def test_similar_names_without_domain_not_merged_t02(ctx):
    deps, _ = ctx
    a = {**GOOD, "ext_id": "B1", "website": None, "company": "Ромашка"}
    b = {**a, "ext_id": "B2", "url": "https://fl-demo.example/tasks/B2", "contact": "@other_x"}
    b["body"] = "Другая: " + GOOD["body"]
    rows = run(ctx, [a, b]).rows
    assert [r.result for r in rows] == ["created", "created"]
    assert rows[0].lead_id != rows[1].lead_id


def test_lead_gets_prescore_evidence_and_contacts(ctx):
    deps, _ = ctx
    lead = deps.store.lead(run(ctx, [GOOD]).rows[0].lead_id)
    assert lead.stage is Stage.NEW and lead.icp is Icp.EXISTING_BUSINESS
    assert lead.prescore_total == 25 + 30 + 15 + 10 + 10
    assert lead.score_total is None  # the doc 04 score comes only from research (H-06)
    fields = {e.field for e in deps.store.evidence_for(lead.company_id)}
    assert fields == {"title", "description", "company", "budget", "posted_at", "contact", "url"}
    channels = {c.channel for c in deps.store.contacts_for(lead.company_id)}
    assert channels == {Channel.TELEGRAM, Channel.PLATFORM}


def test_rejections_are_stored_and_rematched(ctx):
    spam = {"ext_id": "S1", "title": "Заработок без вложений", "body": "пиши в личку, нужен сайт"}
    report = run(ctx, [spam, {**GOOD, "ext_id": "D1"}, {**GOOD, "ext_id": "D2"}])
    assert [r.reason for r in report.rows] == [RejectReason.SPAM, None, RejectReason.DUPLICATE_TEXT]
    assert run(ctx, [spam]).rows[0].result == "matched"


def test_observer_cannot_import(ctx):
    deps, _ = ctx
    observer = add_user(deps, "watcher", Role.OBSERVER)
    with pytest.raises(HunterError) as exc:
        import_listings(
            deps,
            observer,
            [GOOD],
            file_name="f",
            campaign="c",
            platform="p",
            currency="RUB",
            today=TODAY,
        )
    assert exc.value.code is ErrorCode.FORBIDDEN


@pytest.mark.parametrize(
    ("patch", "reason"),
    [
        ({"body": "Выполню сайт под ключ недорого, опыт 5 лет"}, RejectReason.NOT_A_BUYER),
        (
            {"title": "SEO", "body": "Нужно SEO продвижение магазина и отчёты"},
            RejectReason.OUT_OF_SCOPE,
        ),
        (
            {"title": "Бухгалтер", "body": "Нужен бухгалтер на полставки в офис"},
            RejectReason.NO_DEV_TASK,
        ),
        ({"budget": "5000"}, RejectReason.BUDGET_TOO_LOW),
        ({"posted_at": "2025-01-01"}, RejectReason.STALE),
        ({"title": "", "body": "коротко"}, RejectReason.EMPTY),
    ],
    ids=["not-buyer", "seo", "no-dev", "budget", "stale", "empty"],
)
def test_screening_reasons(patch, reason):
    listing = parse_listing({**GOOD, **patch}, "fl", "RUB")
    assert screen(listing, TODAY) is reason


def test_stale_boundary_180_days():
    fresh = parse_listing({**GOOD, "posted_at": "2026-03-31"}, "fl", "RUB")  # 180 days
    old = parse_listing({**GOOD, "posted_at": "2026-03-30"}, "fl", "RUB")  # 181 days
    assert screen(fresh, TODAY) is None
    assert screen(old, TODAY) is RejectReason.STALE
