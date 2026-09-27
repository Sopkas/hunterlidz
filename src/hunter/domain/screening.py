"""Junk filter for imported listings. (H-03)

A rejected listing is stored with its reason (FR-01), so re-importing it is a "matched" row,
not a new one. Keyword rules are a triage heuristic: rejection reasons from operator Skips
in the pilot should extend these lists in a new SCREENING_VERSION.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from enum import StrEnum

from hunter.domain.listings import Listing

SCREENING_VERSION = "1.0"
MIN_BUDGET_RUB = Decimal(30_000)  # below this the task is not a product-team engagement
STALE_DAYS = 180
MIN_TEXT_LENGTH = 30


class RejectReason(StrEnum):
    EMPTY = "empty"
    SPAM = "spam"
    SUPPRESSED = "suppressed"
    NOT_A_BUYER = "not_a_buyer"
    OUT_OF_SCOPE = "out_of_scope"
    NO_DEV_TASK = "no_dev_task"
    BUDGET_TOO_LOW = "budget_too_low"
    STALE = "stale"
    DUPLICATE_TEXT = "duplicate_text"


SPAM = (
    r"казино",
    r"ставки на спорт",
    r"букмекер",
    r"без вложений",
    r"пассивн\w* доход",
    r"сетев\w* маркетинг",
    r"\b18\+",
    r"интим",
    r"накрутк",
    r"отзыв\w* за деньги",
    r"обнал",
    r"\bдроп(ы|ов|ам|ами|овод\w*)?\b",  # money mules; not «дропдаун»
)
NOT_A_BUYER = (
    r"ищу работу",
    r"мое резюме",
    r"выполню",
    r"сделаю (вам )?сайт",
    r"предлага(ю|ем) (свои )?услуги",
    r"ищу заказ",
    r"возьму (ваш )?заказ",
    r"мое портфолио",
)
OUT_OF_SCOPE = (
    r"\bseo\b",
    r"\bsmm\b",
    r"таргет",
    r"копирайт",
    r"рерайт",
    r"перевод\w* текст",
    r"монтаж\w* видео",
    r"настройк\w* реклам",
    r"контекстн\w* реклам",
    r"обзвон",
    r"логотип",
)
DEV_TASK = (
    r"разработ",
    r"сайт",
    r"веб",
    r"\bweb",
    r"приложени",
    r"\bбот",
    r"telegram",
    r"телеграм",
    r"\bcrm",
    r"\bсрм",
    r"интеграц",
    r"\bapi\b",
    r"бэкенд|backend",
    r"фронтенд|frontend",
    r"\bmvp\b|\bмвп",
    r"автоматизац",
    r"парсер",
    r"личн\w* кабинет",
    r"портал",
    r"ui/ux|\bux\b|интерфейс",
    r"платформ",
    r"программист",
    r"python|django|react|node",
)


def matches(patterns: tuple[str, ...], text: str) -> list[str]:
    return [p for p in patterns if re.search(p, text)]


def is_company_row(listing: Listing) -> bool:
    return not listing.body.strip() and bool(listing.website_host) and bool(listing.company_name)


def screen(listing: Listing, today: date, *, suppressed: bool = False) -> RejectReason | None:
    """First matching reason in a fixed order, or None when the listing goes to scoring.

    A company row (FR-01: a name and a website, no listing text) has nothing to screen by
    text; it goes to research, which reads the website."""
    if suppressed:
        return RejectReason.SUPPRESSED
    if is_company_row(listing):
        return None
    text = listing.text
    if len(text.strip()) < MIN_TEXT_LENGTH:
        return RejectReason.EMPTY
    if matches(SPAM, text):
        return RejectReason.SPAM
    if matches(NOT_A_BUYER, text):
        return RejectReason.NOT_A_BUYER
    if not matches(DEV_TASK, text):
        return (
            RejectReason.OUT_OF_SCOPE if matches(OUT_OF_SCOPE, text) else RejectReason.NO_DEV_TASK
        )
    budget = listing.budget
    if budget is not None and budget.currency == "RUB" and budget.amount < MIN_BUDGET_RUB:
        return RejectReason.BUDGET_TOO_LOW
    if listing.posted_on and (today - listing.posted_on).days > STALE_DAYS:
        return RejectReason.STALE
    return None
