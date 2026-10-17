"""S4-04 contract: meetings, proposals, orders, payments (docs 04, 06, 07, 10; T-22)."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from hunter.domain.catalog import Product, Stage
from hunter.domain.deals import (
    Meeting,
    MeetingStatus,
    Money,
    Order,
    OrderProposalError,
    OrderStatus,
    Payment,
    PaymentKind,
    Proposal,
    ProposalStatus,
    WonWithoutOrderError,
    calls_held,
    cash_received,
    check_order_proposal,
    check_won,
    contracted_value,
    paid,
    proposal_opportunities,
)
from hunter.domain.entities import Lead

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
NAIVE = datetime(2026, 10, 18, 12, 0)
RUB = lambda amount: Money(Decimal(amount), "RUB")  # noqa: E731

MEETING = Meeting(id="m1", lead_id="l1", scheduled_at=NOW, status=MeetingStatus.SCHEDULED)
PROPOSAL = Proposal(
    id="p1",
    lead_id="l1",
    proposal_version=1,
    artifact_ref="docs/proposals/alpha-v1.pdf",
    sent_at=NOW,
    status=ProposalStatus.SENT,
    product=Product.BUSINESS_AUTOMATION,
    price=RUB("200000"),
    price_max=Decimal("700000"),
)
ORDER = Order(
    id="o1",
    lead_id="l1",
    accepted_at=NOW,
    basis_ref="contract №12 от 18.10.2026",
    amount=RUB("350000"),
    status=OrderStatus.CONFIRMED,
    proposal_id="p1",
)
RECEIPT = Payment(
    id="pay1", order_id="o1", received_at=NOW, amount=RUB("100000"), kind=PaymentKind.RECEIPT
)


def lead(stage: Stage) -> Lead:
    reason = "выбрали другого подрядчика" if stage is Stage.LOST else None
    return Lead(id="l1", company_id="c1", campaign_id="k1", stage=stage, loss_reason=reason)


# --- Money --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "currency"),
    [
        (Decimal("0"), "RUB"),
        (Decimal("-1"), "RUB"),
        (Decimal("NaN"), "RUB"),
        (Decimal("Infinity"), "RUB"),
        (100.0, "RUB"),
        (100, "RUB"),
        (Decimal("1"), "rub"),
        (Decimal("1"), "RUBL"),
        (Decimal("1"), "РУБ"),
        (Decimal("0.001"), "RUB"),
        (Decimal("1E+16"), "RUB"),
        (Decimal("sNaN"), "RUB"),
    ],
    ids=[
        "zero", "negative", "nan", "infinity", "float", "int", "lower-case", "long-code",
        "cyrillic-code", "three-decimals", "too-large", "signalling-nan",
    ],
)  # fmt: skip
def test_bad_money_is_rejected(amount, currency):
    with pytest.raises(ValueError):
        Money(amount, currency)


# --- Meeting ------------------------------------------------------------------------------


def test_held_meeting_needs_held_at():
    with pytest.raises(ValueError):
        replace(MEETING, status=MeetingStatus.HELD)
    held = replace(MEETING, status=MeetingStatus.HELD, held_at=NOW + timedelta(hours=1))
    assert held.held_at is not None


@pytest.mark.parametrize(
    "status", [MeetingStatus.SCHEDULED, MeetingStatus.CANCELLED, MeetingStatus.NO_SHOW]
)
def test_only_a_held_meeting_has_held_at(status):
    with pytest.raises(ValueError):
        replace(MEETING, status=status, held_at=NOW)


@pytest.mark.parametrize(
    "change",
    [
        dict(scheduled_at=NAIVE),
        dict(scheduled_at=None),
        dict(status="held"),
        dict(lead_id=" "),
        dict(outcome="  "),
        dict(version=0),
    ],
    ids=["naive", "no-time", "string-status", "no-lead", "blank-outcome", "version"],
)
def test_invalid_meeting_is_not_created(change):
    with pytest.raises(ValueError):
        replace(MEETING, **change)


def test_calls_held_counts_held_only():
    meetings = [
        replace(MEETING, id="a", status=MeetingStatus.HELD, held_at=NOW),
        replace(MEETING, id="b", status=MeetingStatus.CANCELLED),
        replace(MEETING, id="c", status=MeetingStatus.NO_SHOW),
        replace(MEETING, id="d"),
    ]
    assert calls_held(meetings) == 1


# --- Proposal -----------------------------------------------------------------------------


def test_proposal_versions_are_one_opportunity():
    v2 = replace(PROPOSAL, id="p2", proposal_version=2, artifact_ref="docs/proposals/alpha-v2.pdf")
    other = replace(PROPOSAL, id="p3", lead_id="l2")
    assert proposal_opportunities([PROPOSAL, v2, other]) == 2


def test_proposal_keeps_offer_and_price():
    # What was offered, at what price, and how it ended: the data for margin and estimates.
    assert PROPOSAL.product is Product.BUSINESS_AUTOMATION
    assert (PROPOSAL.price.amount, PROPOSAL.price_max) == (Decimal("200000"), Decimal("700000"))


def test_proposal_without_price_is_allowed():
    assert replace(PROPOSAL, price=None, price_max=None).price is None


@pytest.mark.parametrize(
    "change",
    [
        dict(proposal_version=0),
        dict(artifact_ref=" "),
        dict(sent_at=NAIVE),
        dict(status="sent"),
        dict(product="mvp_launch"),
        dict(price=None),  # price_max without the lower end
        dict(price_max=Decimal("200000")),
        dict(price_max=Decimal("100")),
        dict(price_max=700000.0),
        dict(price_max=Decimal("Infinity")),
        dict(price_max=Decimal("NaN")),
        dict(price_max=Decimal("sNaN")),
        dict(version=0),
    ],
    ids=[
        "version", "no-document", "naive", "string-status", "string-product", "max-alone",
        "max-equal", "max-below", "max-float", "max-infinity", "max-nan", "max-snan", "version",
    ],
)  # fmt: skip
def test_invalid_proposal_is_not_created(change):
    with pytest.raises(ValueError):
        replace(PROPOSAL, **change)


# --- Order and WON ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        dict(basis_ref=" "),
        dict(accepted_at=None),
        dict(accepted_at=NAIVE),
        dict(amount=Decimal("350000")),
        dict(status="confirmed"),
        dict(proposal_id=" "),
        dict(version=0),
    ],
    ids=[
        "no-basis", "no-time", "naive", "bare-amount", "string-status", "blank-proposal",
        "version",
    ],
)  # fmt: skip
def test_invalid_order_is_not_created(change):
    with pytest.raises(ValueError):
        replace(ORDER, **change)


def test_won_without_an_order_is_rejected():
    with pytest.raises(WonWithoutOrderError):
        check_won(lead(Stage.WON), [])


def test_won_with_only_a_cancelled_or_foreign_order_is_rejected():
    orders = [replace(ORDER, status=OrderStatus.CANCELLED), replace(ORDER, id="o2", lead_id="l2")]
    with pytest.raises(WonWithoutOrderError):
        check_won(lead(Stage.WON), orders)


@pytest.mark.parametrize(
    "status", [OrderStatus.CONFIRMED, OrderStatus.IN_PROGRESS, OrderStatus.COMPLETED]
)
def test_won_with_an_active_order_passes(status):
    check_won(lead(Stage.WON), [replace(ORDER, status=status)])


@pytest.mark.parametrize("stage", [Stage.PROPOSAL, Stage.NEGOTIATION, Stage.LOST])
def test_other_stages_need_no_order(stage):
    check_won(lead(stage), [])


def test_won_is_not_payment():
    # Doc 04: an order is enough for WON even before any money arrived.
    check_won(lead(Stage.WON), [ORDER])
    assert paid(ORDER, []) == Decimal(0)


def test_contracted_value_skips_cancelled_and_keeps_currencies_apart():
    orders = [
        ORDER,
        replace(ORDER, id="o2", amount=RUB("150000")),
        replace(ORDER, id="o3", status=OrderStatus.CANCELLED),
        replace(ORDER, id="o4", amount=Money(Decimal("2000"), "USD")),
    ]
    assert contracted_value(orders) == {"RUB": Decimal("500000"), "USD": Decimal("2000")}


# --- Payments -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        dict(amount=Decimal("1")),
        dict(kind="receipt"),
        dict(received_at=NAIVE),
        dict(received_at=None),
        dict(order_id=" "),
        dict(external_reference=" "),
    ],
    ids=["bare-amount", "string-kind", "naive", "no-time", "no-order", "blank-reference"],
)
def test_invalid_payment_is_not_created(change):
    with pytest.raises(ValueError):
        replace(RECEIPT, **change)


def test_refund_is_subtracted():
    refund = replace(RECEIPT, id="pay2", amount=RUB("30000"), kind=PaymentKind.REFUND)
    other_order = replace(RECEIPT, id="pay3", order_id="o9")
    assert paid(ORDER, [RECEIPT, refund, other_order]) == Decimal("70000")


def test_refunds_above_receipts_are_a_records_error():
    refund = replace(RECEIPT, id="pay2", amount=RUB("100001"), kind=PaymentKind.REFUND)
    with pytest.raises(ValueError):
        paid(ORDER, [RECEIPT, refund])


def test_payment_in_another_currency_is_a_records_error():
    usd = replace(RECEIPT, id="pay2", amount=Money(Decimal("100"), "USD"))
    with pytest.raises(ValueError):
        paid(ORDER, [RECEIPT, usd])


def test_cash_received_is_per_currency_and_not_the_order_sum():
    payments = [
        RECEIPT,
        replace(RECEIPT, id="pay2", amount=RUB("20000"), kind=PaymentKind.REFUND),
        replace(RECEIPT, id="pay3", amount=Money(Decimal("500"), "USD")),
    ]
    assert cash_received(payments) == {"RUB": Decimal("80000"), "USD": Decimal("500")}
    assert cash_received(payments) != contracted_value([ORDER])


def test_money_fits_two_decimals():
    assert Money(Decimal("199999.99"), "RUB").amount == Decimal("199999.99")
    assert Money(Decimal("2E+5"), "RUB").amount == Decimal("200000")


def test_proposal_status_changes_with_its_version():
    # Review A on #9: the status changes after creation, so expected_version needs a version.
    accepted = replace(PROPOSAL, status=ProposalStatus.ACCEPTED, version=2)
    assert accepted.version == 2 and accepted.proposal_version == 1


def test_order_names_an_accepted_proposal_of_its_lead():
    check_order_proposal(ORDER, replace(PROPOSAL, status=ProposalStatus.ACCEPTED))


@pytest.mark.parametrize(
    "proposal",
    [
        replace(PROPOSAL, lead_id="l2"),
        replace(PROPOSAL, status=ProposalStatus.DECLINED),
        replace(PROPOSAL, status=ProposalStatus.SUPERSEDED),
        replace(PROPOSAL, id="p9"),
    ],
    ids=["other-lead", "declined", "superseded", "other-proposal"],
)
def test_order_cannot_name_a_wrong_proposal(proposal):
    with pytest.raises(OrderProposalError):
        check_order_proposal(ORDER, proposal)
