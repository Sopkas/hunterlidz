"""Meetings, proposals, orders and payments: the deal after the first reply. (S4-04, CONTRACT)

Doc 10 "Сущности" (meetings, proposals, orders, payments), doc 04 (WON is a confirmed order,
not a payment; CALL is not a held meeting), doc 06 (proposal versions, price or range, basis of
WON), doc 07 (Calls held, Proposals, Won, Contracted value, Cash received). Deviations from
doc 10 are listed in docs/deals-contract.md.

Money is a Decimal amount with its own currency; amounts of different currencies are never
added up (doc 07). Moments are timezone-aware UTC datetimes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from hunter.domain.catalog import Product, Stage
from hunter.domain.entities import Lead, _id, _text, _utc, _version


def _moment(value: datetime | None, name: str) -> None:
    if value is None:
        raise ValueError(f"{name} is required")
    _utc(value, name)


# numeric(18, 2) in storage (S4-05): kopecks and cents, up to 16 digits before the point.
MONEY_DECIMALS = 2
MONEY_LIMIT = Decimal(10) ** 16


def _currency(value: str) -> None:
    # isascii: "РУБ" is upper-case letters too, and would split the metrics into two keys.
    if len(value) != 3 or not value.isascii() or not value.isalpha() or not value.isupper():
        raise ValueError("currency: ISO 4217 code, e.g. 'RUB'")


def _amount(value: object, name: str) -> None:
    """A positive finite Decimal that fits numeric(18, 2); otherwise ValueError, never
    decimal.InvalidOperation (NaN would raise that on the first comparison)."""
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if value <= 0 or value >= MONEY_LIMIT:
        raise ValueError(f"{name} must be positive and below 10^16")
    if value.as_tuple().exponent < -MONEY_DECIMALS:
        raise ValueError(f"{name}: at most {MONEY_DECIMALS} digits after the point")


@dataclass(frozen=True, slots=True)
class Money:
    """A positive amount in one currency. A refund is a positive payment of kind REFUND."""

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        _amount(self.amount, "amount")
        _currency(self.currency)


class MeetingStatus(StrEnum):
    SCHEDULED = "scheduled"
    HELD = "held"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


@dataclass(frozen=True, slots=True)
class Meeting:
    """A call with the client. Only HELD counts in Calls held (doc 07)."""

    id: str
    lead_id: str
    scheduled_at: datetime
    status: MeetingStatus
    version: int = 1
    held_at: datetime | None = None
    outcome: str | None = None

    def __post_init__(self) -> None:
        _id(self.id)
        _id(self.lead_id, "lead_id")
        _version(self.version)
        if not isinstance(self.status, MeetingStatus):
            raise ValueError("status must be a MeetingStatus")
        _moment(self.scheduled_at, "scheduled_at")
        _utc(self.held_at, "held_at")
        if (self.status is MeetingStatus.HELD) != (self.held_at is not None):
            raise ValueError("held_at is set exactly for a held meeting")
        _text(self.outcome, "outcome")


class ProposalStatus(StrEnum):
    SENT = "sent"  # handed to the client and waiting for a decision
    ACCEPTED = "accepted"
    DECLINED = "declined"
    SUPERSEDED = "superseded"  # a newer version was sent


@dataclass(frozen=True, slots=True)
class Proposal:
    """One version of a proposal handed to the client (doc 06: every version has a number and
    a date). Versions of one lead are one opportunity in the Proposals metric.

    price is the amount, or the lower end when price_max gives a range in the same currency
    (doc 06: "сумма или обоснованный диапазон"). product is the offer of doc 02 it sells.

    Only the status changes after creation (the client answered, a newer version went out), so
    the record has a version for expected_version; new text or price is a new proposal_version.
    """

    id: str
    lead_id: str
    proposal_version: int
    artifact_ref: str
    sent_at: datetime
    status: ProposalStatus
    product: Product | None = None
    price: Money | None = None
    price_max: Decimal | None = None
    version: int = 1

    def __post_init__(self) -> None:
        _id(self.id)
        _id(self.lead_id, "lead_id")
        _version(self.version)
        if self.proposal_version < 1:
            raise ValueError("proposal_version starts at 1")
        if not self.artifact_ref.strip():
            raise ValueError("artifact_ref: the proposal document is required")
        _moment(self.sent_at, "sent_at")
        if not isinstance(self.status, ProposalStatus):
            raise ValueError("status must be a ProposalStatus")
        if self.product is not None and not isinstance(self.product, Product):
            raise ValueError("product must be a Product")
        if self.price_max is not None:
            if self.price is None:
                raise ValueError("price_max needs price as the lower end of the range")
            _amount(self.price_max, "price_max")
            if self.price_max <= self.price.amount:
                raise ValueError("price_max must be above price")


class OrderStatus(StrEnum):
    CONFIRMED = "confirmed"  # accepted by the client, work not started
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


ACTIVE_ORDER = frozenset({OrderStatus.CONFIRMED, OrderStatus.IN_PROGRESS, OrderStatus.COMPLETED})


@dataclass(frozen=True, slots=True)
class Order:
    """A confirmed order: the basis of WON (doc 04). It is not a payment."""

    id: str
    lead_id: str
    accepted_at: datetime
    basis_ref: str
    amount: Money
    status: OrderStatus
    version: int = 1
    proposal_id: str | None = None

    def __post_init__(self) -> None:
        _id(self.id)
        _id(self.lead_id, "lead_id")
        _version(self.version)
        _moment(self.accepted_at, "accepted_at")
        if not self.basis_ref.strip():
            raise ValueError("basis_ref: the confirming document or accepted order is required")
        if not isinstance(self.amount, Money):
            raise ValueError("amount must be Money")
        if not isinstance(self.status, OrderStatus):
            raise ValueError("status must be an OrderStatus")
        if self.proposal_id is not None:
            _id(self.proposal_id, "proposal_id")


class PaymentKind(StrEnum):
    RECEIPT = "receipt"
    REFUND = "refund"


@dataclass(frozen=True, slots=True)
class Payment:
    """Money actually received or returned for an order (doc 07 Cash received)."""

    id: str
    order_id: str
    received_at: datetime
    amount: Money
    kind: PaymentKind
    external_reference: str | None = None

    def __post_init__(self) -> None:
        _id(self.id)
        _id(self.order_id, "order_id")
        _moment(self.received_at, "received_at")
        if not isinstance(self.amount, Money):
            raise ValueError("amount must be Money")
        if not isinstance(self.kind, PaymentKind):
            raise ValueError("kind must be a PaymentKind")
        _text(self.external_reference, "external_reference")


class WonWithoutOrderError(ValueError):
    """Doc 04: a lead becomes WON only with a confirmed order and its basis recorded."""


def check_won(lead: Lead, orders: Iterable[Order]) -> None:
    """Raise unless a WON lead has an active order. Other stages pass unchecked."""
    if lead.stage is not Stage.WON:
        return
    if not any(o.lead_id == lead.id and o.status in ACTIVE_ORDER for o in orders):
        raise WonWithoutOrderError(f"lead {lead.id}: WON requires a confirmed order")


class OrderProposalError(ValueError):
    """The order names a proposal of another lead, or one the client did not accept."""


def check_order_proposal(order: Order, proposal: Proposal) -> None:
    """Order.proposal_id must point to a proposal of the same lead that was not declined or
    replaced; the price the order was sold at comes from it (doc 07 Proposal → Won)."""
    if order.proposal_id != proposal.id:
        raise OrderProposalError(f"order {order.id} names proposal {order.proposal_id}")
    if proposal.lead_id != order.lead_id:
        raise OrderProposalError(f"proposal {proposal.id} belongs to another lead")
    if proposal.status in (ProposalStatus.DECLINED, ProposalStatus.SUPERSEDED):
        raise OrderProposalError(f"proposal {proposal.id} is {proposal.status}")


def paid(order: Order, payments: Iterable[Payment]) -> Decimal:
    """Net amount received for `order`: receipts minus refunds, in the order's currency.

    A payment in another currency or refunds above receipts mean the records are wrong:
    ValueError, not a silently negative or mixed balance.
    """
    total = Decimal(0)
    for payment in payments:
        if payment.order_id != order.id:
            continue
        if payment.amount.currency != order.amount.currency:
            raise ValueError(f"payment {payment.id}: currency differs from order {order.id}")
        sign = 1 if payment.kind is PaymentKind.RECEIPT else -1
        total += sign * payment.amount.amount
    if total < 0:
        raise ValueError(f"order {order.id}: refunds exceed receipts")
    return total


def contracted_value(orders: Iterable[Order]) -> dict[str, Decimal]:
    """Doc 07 Contracted value per currency; cancelled orders do not count. Sums whatever it is
    given: the report (S4-03) selects the period by accepted_at before calling it."""
    totals: dict[str, Decimal] = {}
    for order in orders:
        if order.status in ACTIVE_ORDER:
            currency = order.amount.currency
            totals[currency] = totals.get(currency, Decimal(0)) + order.amount.amount
    return totals


def cash_received(payments: Iterable[Payment]) -> dict[str, Decimal]:
    """Doc 07 Cash received per currency: receipts minus refunds, never mixed with orders.
    The period is selected by received_at before calling it (S4-03)."""
    totals: dict[str, Decimal] = {}
    for payment in payments:
        sign = 1 if payment.kind is PaymentKind.RECEIPT else -1
        currency = payment.amount.currency
        totals[currency] = totals.get(currency, Decimal(0)) + sign * payment.amount.amount
    return totals


def proposal_opportunities(proposals: Iterable[Proposal]) -> int:
    """Doc 07 Proposals: leads with a proposal handed over; versions do not add up."""
    return len({proposal.lead_id for proposal in proposals})


def calls_held(meetings: Iterable[Meeting]) -> int:
    """Doc 07 Calls held: held meetings only, without cancellations and no-shows."""
    return sum(1 for meeting in meetings if meeting.status is MeetingStatus.HELD)
