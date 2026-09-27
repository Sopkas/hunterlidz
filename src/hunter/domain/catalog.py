"""Shared domain vocabulary: ICP, products, CRM stages, priorities, money. (H-03)

Sources: doc 03 (ICP), doc 02 (products), doc 04 (stages and priority), doc 10 (money as
Decimal with a separate currency).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Icp(StrEnum):
    FOUNDERS = "founders"
    EXISTING_BUSINESS = "existing_business"
    AGENCIES = "agencies"
    UNKNOWN = "unknown"


class Product(StrEnum):
    MVP_LAUNCH = "mvp_launch"
    BUSINESS_AUTOMATION = "business_automation"
    EXTERNAL_PRODUCT_TEAM = "external_product_team"


PRODUCT_FOR_ICP: dict[Icp, Product] = {
    Icp.FOUNDERS: Product.MVP_LAUNCH,
    Icp.EXISTING_BUSINESS: Product.BUSINESS_AUTOMATION,
    Icp.AGENCIES: Product.EXTERNAL_PRODUCT_TEAM,
}


class Stage(StrEnum):
    NEW = "NEW"
    RESEARCHED = "RESEARCHED"
    APPROVED = "APPROVED"
    CONTACTED = "CONTACTED"
    REPLIED = "REPLIED"
    QUALIFIED = "QUALIFIED"
    CALL = "CALL"
    PROPOSAL = "PROPOSAL"
    NEGOTIATION = "NEGOTIATION"
    WON = "WON"
    LOST = "LOST"
    NURTURE = "NURTURE"


CLOSED_STAGES = frozenset({Stage.WON, Stage.LOST})


class Priority(StrEnum):
    HOT = "HOT"
    WARM = "WARM"
    WATCH = "WATCH"
    SKIP = "SKIP"


def priority_for(total: int) -> Priority:
    """Doc 04: 80-100 HOT, 60-79 WARM, 40-59 WATCH, 0-39 SKIP."""
    if not 0 <= total <= 100:
        raise ValueError(f"score out of range: {total}")
    if total >= 80:
        return Priority.HOT
    if total >= 60:
        return Priority.WARM
    if total >= 40:
        return Priority.WATCH
    return Priority.SKIP


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str  # ISO 4217, e.g. "RUB"

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal) or self.amount < 0:
            raise ValueError("money amount must be a non-negative Decimal")
        if len(self.currency) != 3 or not self.currency.isupper():
            raise ValueError("currency must be an ISO 4217 code")
