from datetime import date
from decimal import Decimal

import pytest

from hunter.domain.catalog import Money, Priority, priority_for
from hunter.domain.contacts import Channel, classify_address
from hunter.domain.listings import NBSP, normalize_url, parse_budget, parse_date, parse_listing


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("400 000 ₽", Money(Decimal("400000.00"), "RUB")),
        (f"150{NBSP}000,00 руб", Money(Decimal("150000.00"), "RUB")),
        ("до 500к", Money(Decimal("500000.00"), "RUB")),
        ("400-600 тыс", Money(Decimal("600000.00"), "RUB")),
        ("1,5 млн", Money(Decimal("1500000.00"), "RUB")),
        ("$5000", Money(Decimal("5000.00"), "USD")),
        (250000, Money(Decimal("250000.00"), "RUB")),
        ("договорная", None),
        ("", None),
        (None, None),
    ],
    ids=["rub", "nbsp", "k", "range", "mln", "usd", "int", "negotiable", "empty", "none"],
)
def test_parse_budget(raw, expected):
    assert parse_budget(raw, "RUB") == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2026-09-20", date(2026, 9, 20)), ("20.09.2026", date(2026, 9, 20)), ("вчера", None)],
    ids=["iso", "ru", "text"],
)
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


def test_normalize_url_drops_tracking_and_www():
    url = "HTTPS://www.Example.com/tasks/1/?utm_source=x#top"
    assert normalize_url(url) == "https://example.com/tasks/1"
    assert normalize_url("ftp://example.com") is None


@pytest.mark.parametrize(
    ("raw", "channel", "normalized", "company_wide"),
    [
        ("Ops@Test-Biz.example", Channel.EMAIL, "ops@test-biz.example", False),
        ("info@test.example", Channel.EMAIL, "info@test.example", True),
        ("@Test_Founder", Channel.TELEGRAM, "@test_founder", False),
        ("https://t.me/test_founder", Channel.TELEGRAM, "@test_founder", False),
        ("+7 (900) 000-00-00", Channel.PHONE, "+79000000000", False),
    ],
    ids=["email", "generic", "tg", "tg-link", "phone"],
)
def test_classify_address(raw, channel, normalized, company_wide):
    address = classify_address(raw)
    assert (address.channel, address.normalized, address.is_company_wide) == (
        channel,
        normalized,
        company_wide,
    )


def test_unknown_values_are_none_with_reasons():
    listing = parse_listing({"title": "Нужна CRM", "body": "x" * 40}, "fl", "RUB")
    assert listing.budget is None and listing.posted_on is None and listing.contact_address is None
    assert len(listing.missing) == 3


def test_source_key_prefers_platform_id_then_url():
    a = parse_listing({"ext_id": "7", "url": "https://p.example/7", "title": "t"}, "fl", "RUB")
    b = parse_listing({"url": "https://www.p.example/7?utm=1", "title": "t"}, "fl", "RUB")
    assert a.source_key == "fl:7"
    assert b.source_key == "url:p.example/7"


@pytest.mark.parametrize(
    ("total", "priority"),
    [
        (39, Priority.SKIP),
        (40, Priority.WATCH),
        (59, Priority.WATCH),
        (60, Priority.WARM),
        (79, Priority.WARM),
        (80, Priority.HOT),
        (0, Priority.SKIP),
        (100, Priority.HOT),
    ],
)
def test_priority_boundaries_t04(total, priority):
    assert priority_for(total) is priority


@pytest.mark.parametrize("total", [-1, 101])
def test_priority_rejects_out_of_range(total):
    with pytest.raises(ValueError):
        priority_for(total)
