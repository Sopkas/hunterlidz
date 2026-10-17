"""Lead score by the doc 04 scale and readiness for the message queue. (S2-08, T-03…T-05)

The model only proposes features with evidence (research_v1.1, S2-01). This module picks
exactly one level per component, with a reason, and sums them. Unknown data gives 0 and a
reason, never a negative conclusion about the company (doc 04).

- ICP Fit 0/10/20/25: segment → + task type → + scale available to the team.
- Buying Signal 0/10/20/30 by the age of the freshest dated signal: up to 30 days 30, 31–90
  days 20, older 10. An unknown date never gives more than 10 (T-03). A month or a year is
  dated by its first day (S2-01), the oldest possible date. A confirmed current need gives 30
  only when a person confirmed it in a conversation.
- Problem Fit 0/10/15/20: hypothesis 10, confirmed task 15, plus a confirmed relevant case 20.
  A planned, withdrawn or unapproved case claim never counts (doc 04).
- Budget 0/5/10/15: confirmed_range (15) only when a person confirmed it in a conversation.
- Contactability 0/5/10 comes from stored contacts, not from the model.

Contradicting features do not change the levels; they make the result need review (doc 11).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType

from hunter.domain.cases import CaseClaim, ClaimStatus
from hunter.domain.catalog import Priority, priority_for
from hunter.domain.contacts import Address, is_company_wide
from hunter.domain.entities import Contact, Evidence, SourceType
from hunter.domain.research import BudgetLevel, ContactLevel, Research

RULES_VERSION = "scoring_v1"

COMPONENTS = ("icp_fit", "signal", "problem_fit", "budget", "contactability")
LEVELS: Mapping[str, tuple[int, ...]] = MappingProxyType(
    {
        "icp_fit": (0, 10, 20, 25),
        "signal": (0, 10, 20, 30),
        "problem_fit": (0, 10, 15, 20),
        "budget": (0, 5, 10, 15),
        "contactability": (0, 5, 10),
    }
)
_BUDGET_POINTS = {
    BudgetLevel.UNKNOWN: 0,
    BudgetLevel.INDIRECT: 5,
    BudgetLevel.DIRECTIONAL_SPEND: 10,
    BudgetLevel.CONFIRMED_RANGE: 15,
}
_BUDGET_REASONS = {
    0: "данных о бюджете нет",
    5: "косвенный признак расходов на развитие",
    10: "подтверждённые расходы в направлении; бюджет задачи неизвестен",
    15: "собеседник подтвердил совместимый диапазон",
}


@dataclass(frozen=True, slots=True)
class Score:
    """Doc 10 scores: five components, total, rules_version and a reason per component."""

    components: Mapping[str, int]
    reasons: Mapping[str, str]
    contradictions: tuple[str, ...] = ()
    rules_version: str = RULES_VERSION

    def __post_init__(self) -> None:
        if set(self.components) != set(COMPONENTS) or set(self.reasons) != set(COMPONENTS):
            raise ValueError("a score has the five doc 04 components, each with a reason")
        for name, value in self.components.items():
            if value not in LEVELS[name]:
                raise ValueError(f"{name}={value}: one of the levels {LEVELS[name]}")
        if any(not reason.strip() for reason in self.reasons.values()):
            raise ValueError("every component needs a reason")
        object.__setattr__(self, "components", MappingProxyType(dict(self.components)))
        object.__setattr__(self, "reasons", MappingProxyType(dict(self.reasons)))

    @property
    def total(self) -> int:
        return sum(self.components.values())

    @property
    def priority(self) -> Priority:
        return priority_for(self.total)

    @property
    def needs_review(self) -> bool:
        return bool(self.contradictions)


def _from_conversation(
    ids: Sequence[str], evidence: Mapping[str, Evidence], source_types: Mapping[str, SourceType]
) -> bool:
    return any(
        i in evidence and source_types.get(evidence[i].source_id) is SourceType.CONVERSATION
        for i in ids
    )


def confirmed_case(claim: CaseClaim) -> bool:
    """A case that may give Problem Fit 20: approved, allowed and done (doc 04)."""
    return claim.usable_in_messages and claim.status in (
        ClaimStatus.OWNER_REPORTED,
        ClaimStatus.VERIFIED,
    )


def _personal(contact: Contact) -> bool:
    return not is_company_wide(Address(contact.channel, contact.normalized_address))


def compute_score(
    research: Research,
    *,
    today: date,
    evidence: Mapping[str, Evidence],
    source_types: Mapping[str, SourceType],
    claims: Mapping[str, CaseClaim],
    contacts: Sequence[Contact],
) -> Score:
    """`evidence` and `source_types` describe the evidence the research cites (by id);
    `claims` are case claims by id; `contacts` are the stored contacts of the company.

    `today` is the business date (HUNTER_BUSINESS_UTC_OFFSET), the same one parse_research got:
    with two different dates a signal of today would look like a future one and raise.
    """
    f = research.features
    c: dict[str, int] = {}
    r: dict[str, str] = {}
    contradictions: list[str] = []

    segment, task, scale = (f[n].value for n in ("segment_match", "task_match", "scale_match"))
    if segment is not True:
        c["icp_fit"], r["icp_fit"] = 0, "соответствие сегменту не установлено"
        if task is True or scale is True:
            contradictions.append("задача или масштаб подтверждены без совпадения сегмента")
    elif task is not True:
        c["icp_fit"], r["icp_fit"] = 10, "сегмент совпадает, задача неясна"
    elif scale is not True:
        c["icp_fit"], r["icp_fit"] = 20, "совпадают сегмент и тип задачи; масштаб не подтверждён"
    else:
        c["icp_fit"], r["icp_fit"] = 25, "сегмент, задача и масштаб, доступный команде"

    need = f["current_need_confirmed"]
    dated = [s.event_date for s in research.signals if s.event_date is not None]
    if need.value is True and _from_conversation(need.evidence_ids, evidence, source_types):
        c["signal"], r["signal"] = 30, "текущая потребность подтверждена собеседником"
    elif dated:
        age = (today - max(dated)).days
        if age < 0:
            raise ValueError("a signal in the future: parse_research drops those")
        if age <= 30:
            c["signal"], r["signal"] = 30, f"событие {age} дн. назад"
        elif age <= 90:
            c["signal"], r["signal"] = 20, f"событие {age} дн. назад"
        else:
            c["signal"], r["signal"] = 10, f"событие старше 90 дн. ({age})"
    elif research.signals:
        c["signal"], r["signal"] = 10, "дата сигнала неизвестна — не выше 10"
    else:
        c["signal"], r["signal"] = 0, "нет проверяемого сигнала"

    problem = f["problem_confirmed"].value is True
    case = f["relevant_case_confirmed"]
    cited = [claims[i] for i in case.evidence_ids if i in claims]
    if problem and case.value is True and any(confirmed_case(claim) for claim in cited):
        c["problem_fit"], r["problem_fit"] = 20, "задача подтверждена, есть подтверждённый кейс"
    elif problem:
        note = "; кейс не подтверждён или планируемый" if case.value is True else ""
        c["problem_fit"] = 15
        r["problem_fit"] = "задача подтверждена и соответствует компетенциям" + note
    elif research.problem_hypothesis:
        c["problem_fit"], r["problem_fit"] = 10, "правдоподобная гипотеза"
    else:
        c["problem_fit"], r["problem_fit"] = 0, "связи с компетенциями нет"

    budget = f["budget_evidence_level"]
    level = budget.value
    if level is BudgetLevel.CONFIRMED_RANGE and not _from_conversation(
        budget.evidence_ids, evidence, source_types
    ):
        level = BudgetLevel.DIRECTIONAL_SPEND
        r["budget"] = "диапазон указан в источнике, но не подтверждён собеседником"
    c["budget"] = _BUDGET_POINTS[level]
    r.setdefault("budget", _BUDGET_REASONS[c["budget"]])

    personal = [x for x in contacts if _personal(x) and x.verified_at and x.role]
    if personal:
        c["contactability"], r["contactability"] = 10, "роль и её рабочий канал подтверждены"
    elif contacts:
        c["contactability"], r["contactability"] = 5, "доступен общий рабочий канал"
    else:
        c["contactability"], r["contactability"] = 0, "канала нет"
    if f["contact_level"].value is ContactLevel.DECISION_MAKER_CHANNEL and not personal:
        contradictions.append("модель указала канал ЛПР, в базе такого контакта нет")

    return Score(components=c, reasons=r, contradictions=tuple(contradictions))


@dataclass(frozen=True, slots=True)
class OperatorException:
    """A recorded decision of an operator: WARM or a result under review goes to the queue.

    It is given for one score of one research result: a new result or a new score needs a new
    decision (the use case, S2-10, ties it to them), so an old WARM decision does not let a
    later result under review through.

    It never brings back what parse_research dropped: a fact with an invented evidence id stays
    out (decision of 13.10, T-06). To use such a fact the operator adds a real source and the
    fact goes through the same check.
    """

    actor_id: str
    reason: str

    def __post_init__(self) -> None:
        if not self.actor_id.strip() or not self.reason.strip():
            raise ValueError("an exception records who allowed it and why")


@dataclass(frozen=True, slots=True)
class Eligibility:
    eligible: bool
    blockers: tuple[str, ...]
    exception_possible: bool  # only soft blockers left: an operator may allow with a reason


def eligibility(
    score: Score,
    research: Research,
    *,
    contacts: Sequence[Contact],
    suppressed: bool,
    exception: OperatorException | None = None,
) -> Eligibility:
    """Doc 04 standard queue: score ≥ 80, a confirmed contact, a personalization source, a
    fitting offer and no contact ban. WARM and results that need review go only with a recorded
    operator exception; WATCH and SKIP never enter the message queue. HOT alone is never
    enough (T-05).

    `contacts` are the contacts that may be written to: the caller leaves out contacts under a
    ban (suppression_key, S1-13). `suppressed` is a ban on the whole company. The ≥ 80 threshold
    is HOT in priority_for (doc 12 AUTO_QUEUE_MIN_SCORE equals it in v1).
    """
    hard: list[str] = []
    if suppressed:
        hard.append("запрет контакта")
    if not any(x.verified_at for x in contacts):
        hard.append("нет подтверждённого контакта")
    if research.insufficient_evidence:
        hard.append("нет источника для персонализации")
    if research.recommended_product is None:
        hard.append("нет подходящего оффера")
    if score.priority in (Priority.WATCH, Priority.SKIP):
        hard.append(f"приоритет {score.priority} — не для очереди сообщений")
    soft: list[str] = []
    if score.priority is Priority.WARM:
        soft.append("WARM: нужно решение оператора с обоснованием")
    if research.needs_review or score.needs_review:
        soft.append("результат исследования требует проверки")
    blockers = hard + ([] if exception else soft)
    return Eligibility(
        eligible=not blockers,
        blockers=tuple(blockers),
        exception_possible=not hard and bool(soft) and exception is None,
    )
