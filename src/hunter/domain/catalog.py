"""Shared vocabulary: ICP, products, CRM stages, research priority. (S1-05, CONTRACT)

Sources: doc 03 (ICP), doc 02 (products), doc 04 (stages, priority).
Priority (HOT / WARM / WATCH / SKIP) is the research priority from the score; Stage is the CRM
stage. They are different types on purpose: HOT is never a stage and a stage is never derived
from the score alone (doc 04). Message status (draft / approved / sent) is a third, separate
type defined with messages (S3-01).
"""

from __future__ import annotations

from enum import StrEnum


class Icp(StrEnum):
    FOUNDERS = "founders"
    EXISTING_BUSINESS = "existing_business"
    AGENCIES = "agencies"


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
    """Doc 04: NEW → RESEARCHED → APPROVED → CONTACTED → REPLIED → QUALIFIED → CALL → PROPOSAL →
    NEGOTIATION → WON / LOST; NURTURE when the client postponed. Transitions come later."""

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
