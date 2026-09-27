"""Pilot metrics vocabulary (doc 07). (H-11)

Rates are None (shown as N/A) when the denominator is zero — never 0% (T-21). Counts are
of unique recipients / leads as doc 07 defines them; a found address or a repeated touch does
not increase "contacts reached".
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

REPLY_WINDOW_DAYS = 30  # doc 07: replies are measured 30 calendar days from the first message


def rate(numerator: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    return (Decimal(numerator) * 100 / Decimal(denominator)).quantize(Decimal("0.1"))


def show_rate(value: Decimal | None) -> str:
    return "N/A" if value is None else f"{value}%"


@dataclass(frozen=True, slots=True)
class PilotMetrics:
    leads_discovered: int
    rejected_at_import: int
    researched: int
    research_qualified: int
    first_messages_sent: int
    follow_ups_sent: int
    contacts_reached: int
    contacts_in_open_window: int
    replies: int
    auto_replies: int
    positive_replies: int
    opt_outs: int
    approved_cards: int
    ai_cost: Decimal
    cost_currency: str

    @property
    def reply_rate(self) -> Decimal | None:
        return rate(self.replies, self.contacts_reached)

    @property
    def positive_reply_rate(self) -> Decimal | None:
        return rate(self.positive_replies, self.contacts_reached)

    @property
    def cost_per_accepted_card(self) -> Decimal | None:
        if self.approved_cards == 0:
            return None
        return (self.ai_cost / self.approved_cards).quantize(Decimal("0.0001"))
