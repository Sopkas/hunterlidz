"""Pre-score: cheap keyword triage of every accepted listing before any AI call. (H-03)

This is NOT the doc 04 score. It only ranks listings so the AI budget goes to the best ones.
It mirrors the doc 04 component scale, but its inputs are keyword guesses, so the result is
stored separately (prescore_*) and never shown as the lead score. The authoritative score
is computed in domain.scoring from evidence-backed features (H-06).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from hunter.domain.catalog import Icp
from hunter.domain.listings import Listing
from hunter.domain.screening import DEV_TASK, matches

PRESCORE_VERSION = "1.0"
PRODUCT_MIN_BUDGET_RUB = Decimal(150_000)  # doc 02: lower bound of the product line
SHORT_BODY = 200

ICP_KEYWORDS: dict[Icp, tuple[str, ...]] = {
    Icp.FOUNDERS: (
        r"стартап",
        r"\bmvp\b|\bмвп",
        r"иде[яиюе]\b",
        r"с нуля",
        r"прототип",
        r"основател",
        r"инвест",
        r"акселератор",
        r"\bcto\b",
        r"запуск",
        r"waitlist|лист ожидания",
        r"грант",
    ),
    Icp.EXISTING_BUSINESS: (
        r"\bcrm|\bсрм",
        r"автоматизац",
        r"филиал",
        r"\bсет(ь|и)\b",
        r"учет",
        r"склад",
        r"логистик",
        r"автосервис|автосалон|дилер",
        r"юрид|юрист",
        r"обучени|школ",
        r"\bhr\b|персонал|рекрут",
        r"производств",
        r"заяв[ко]",
        r"excel|таблиц",
        r"внутренн",
        r"процесс",
        r"сотрудник",
    ),
    Icp.AGENCIES: (
        r"агентств",
        r"студи[яиюе]\b",
        r"white.?label",
        r"субподряд",
        r"наш\w* клиент",
        r"аутстаф|outstaff",
        r"усилени\w* команд",
    ),
}


@dataclass(frozen=True, slots=True)
class Prescore:
    icp: Icp
    components: dict[str, int]
    reasons: dict[str, str]
    version: str = PRESCORE_VERSION

    @property
    def total(self) -> int:
        return sum(self.components.values())


def guess_icp(text: str) -> Icp:
    counts = {icp: len(matches(words, text)) for icp, words in ICP_KEYWORDS.items()}
    best = max(counts.values())
    leaders = [icp for icp, n in counts.items() if n == best]
    return leaders[0] if best and len(leaders) == 1 else Icp.UNKNOWN


def _rub(listing: Listing) -> Decimal | None:
    budget = listing.budget
    return budget.amount if budget is not None and budget.currency == "RUB" else None


def prescore(listing: Listing, today: date, *, has_direct_contact: bool) -> Prescore:
    text = listing.text
    icp = guess_icp(text)
    dev = bool(matches(DEV_TASK, text))
    rub = _rub(listing)
    c: dict[str, int] = {}
    r: dict[str, str] = {}

    if icp is Icp.UNKNOWN:
        c["icp_fit"], r["icp_fit"] = 0, "сегмент не определён"
    elif not dev:
        c["icp_fit"], r["icp_fit"] = 10, f"похоже на {icp}, задача неясна"
    elif rub is not None and rub >= PRODUCT_MIN_BUDGET_RUB:
        c["icp_fit"], r["icp_fit"] = 25, f"похоже на {icp}, разработка, бюджет в нашем масштабе"
    else:
        c["icp_fit"], r["icp_fit"] = 20, f"похоже на {icp}, разработка; масштаб не подтверждён"

    age = (today - listing.posted_on).days if listing.posted_on else None
    if age is None or age < 0:
        c["signal"], r["signal"] = 10, "дата неизвестна или в будущем — не выше 10"
    elif age <= 30:
        c["signal"], r["signal"] = 30, f"объявление {age} дн. назад"
    elif age <= 90:
        c["signal"], r["signal"] = 20, f"объявление {age} дн. назад"
    else:
        c["signal"], r["signal"] = 10, f"объявление старше 90 дн. ({age})"

    if not dev:
        c["problem_fit"], r["problem_fit"] = 0, "нет задачи разработки"
    elif len(listing.body) < SHORT_BODY:
        c["problem_fit"], r["problem_fit"] = 10, "задача описана кратко"
    else:
        c["problem_fit"], r["problem_fit"] = 15, "задача описана и близка к компетенциям"

    if listing.budget is None:
        c["budget"], r["budget"] = 0, "бюджет не указан"
    elif rub is None:
        c["budget"], r["budget"] = 5, f"бюджет в {listing.budget.currency}, курс не задан"
    elif rub >= PRODUCT_MIN_BUDGET_RUB:
        c["budget"], r["budget"] = 10, "заявлен бюджет в диапазоне линейки"
    else:
        c["budget"], r["budget"] = 5, "заявлен бюджет ниже линейки"

    if has_direct_contact and listing.contact_name:
        c["contactability"], r["contactability"] = 10, "автор объявления и его канал"
    elif has_direct_contact or listing.source_url:
        c["contactability"], r["contactability"] = 5, "общий канал или отклик на площадке"
    else:
        c["contactability"], r["contactability"] = 0, "канала нет"

    return Prescore(icp=icp, components=c, reasons=r)
