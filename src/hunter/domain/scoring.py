"""Lead score by the doc 04 scale and readiness for the message queue. (H-06, FR-05, FR-06)

The model only proposes features with evidence (research_v1.1); this module chooses exactly one
level per component and sums them. Rules, as written in doc 04 and made operational here:

- ICP Fit 0/10/20/25: segment -> + task type -> + scale available to the team.
- Buying Signal 0/10/20/30 by the age of the freshest dated signal: <=30 days 30, 31-90 days 20,
  older 10; an unknown date never gives more than 10 (T-03). For month / year precision the
  conservative bound is used: the first day of the period, i.e. the oldest possible date.
  "Confirmed current need" gives 30 only when the person confirmed it in a conversation;
  a marketplace listing is dated by its publication, so its age decides.
- Problem Fit 0/10/15/20: hypothesis 10, confirmed task 15, plus a *verified* relevant case 20.
  A planned capability (the claims assistant) never counts.
- Budget 0/5/10/15: confirmed_range (15) only when the budget was confirmed in a conversation;
  a range stated in a listing is directional spend (10).
- Contactability 0/5/10 is derived from stored contacts, not from the model: a person's own
  verified work channel 10, a company-wide channel (platform reply, generic mailbox) 5.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from hunter.domain.cases import CaseClaim
from hunter.domain.catalog import Priority, priority_for
from hunter.domain.entities import Contact, DatePrecision, Evidence, SourceType
from hunter.domain.research import BudgetLevel, ContactLevel, Research

RULES_VERSION = "1.0"
AUTO_QUEUE_MIN_SCORE = 80  # doc 12 AUTO_QUEUE_MIN_SCORE

COMPONENTS = ("icp_fit", "signal", "problem_fit", "budget", "contactability")
MAXIMUM = {"icp_fit": 25, "signal": 30, "problem_fit": 20, "budget": 15, "contactability": 10}


@dataclass(frozen=True, slots=True)
class Score:
    components: Mapping[str, int]
    reasons: Mapping[str, str]
    contradictions: tuple[str, ...]
    rules_version: str = RULES_VERSION

    def __post_init__(self) -> None:
        if set(self.components) != set(COMPONENTS):
            raise ValueError("score needs exactly the five doc 04 components")
        for name, value in self.components.items():
            if not 0 <= value <= MAXIMUM[name]:
                raise ValueError(f"{name}={value} outside 0..{MAXIMUM[name]}")

    @property
    def total(self) -> int:
        return sum(self.components.values())

    @property
    def priority(self) -> Priority:
        return priority_for(self.total)


def conservative_date(event_date: date, precision: DatePrecision) -> date:
    if precision is DatePrecision.MONTH:
        return event_date.replace(day=1)
    if precision is DatePrecision.YEAR:
        return event_date.replace(month=1, day=1)
    return event_date


def _from_conversation(
    ids: Sequence[str], evidence: Mapping[str, Evidence], source_types: Mapping[str, SourceType]
) -> bool:
    return any(
        i in evidence and source_types.get(evidence[i].source_id) is SourceType.CONVERSATION
        for i in ids
    )


def compute_score(
    research: Research,
    *,
    today: date,
    evidence: Mapping[str, Evidence],
    source_types: Mapping[str, SourceType],
    claims: Mapping[str, CaseClaim],
    contacts: Sequence[Contact],
) -> Score:
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
    dated = [
        conservative_date(s.event_date, s.date_precision)
        for s in research.signals
        if s.event_date is not None and s.event_date <= today
    ]
    if need.value is True and _from_conversation(need.evidence_ids, evidence, source_types):
        c["signal"], r["signal"] = 30, "текущая потребность подтверждена собеседником"
    elif dated:
        age = (today - max(dated)).days
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

    case = f["relevant_case_confirmed"]
    cited = [claims[i] for i in case.evidence_ids if i in claims]
    verified_case = case.value is True and any(cl.supports_problem_fit_20 for cl in cited)
    if f["problem_confirmed"].value is True and verified_case:
        c["problem_fit"], r["problem_fit"] = 20, "задача подтверждена, есть verified-кейс"
    elif f["problem_confirmed"].value is True:
        note = "; кейс не verified" if case.value is True else ""
        c["problem_fit"], r["problem_fit"] = (
            15,
            "задача подтверждена и соответствует компетенциям" + note,
        )
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
    points = {
        BudgetLevel.UNKNOWN: 0,
        BudgetLevel.INDIRECT: 5,
        BudgetLevel.DIRECTIONAL_SPEND: 10,
        BudgetLevel.CONFIRMED_RANGE: 15,
    }[level]
    c["budget"] = points
    r.setdefault(
        "budget",
        {
            0: "данных о бюджете нет",
            5: "косвенный признак расходов на развитие",
            10: "подтверждённые расходы в направление; бюджет задачи неизвестен",
            15: "собеседник подтвердил совместимый диапазон",
        }[points],
    )

    personal = [x for x in contacts if not x.company_wide and x.verified_at and x.role]
    shared = [x for x in contacts if x.company_wide]
    if personal:
        c["contactability"], r["contactability"] = 10, "роль и её рабочий канал подтверждены"
    elif shared or any(not x.company_wide for x in contacts):
        c["contactability"], r["contactability"] = 5, "доступен общий рабочий канал"
    else:
        c["contactability"], r["contactability"] = 0, "канала нет"
    claimed = f["contact_level"].value
    if claimed is ContactLevel.DECISION_MAKER_CHANNEL and not personal:
        contradictions.append("модель указала канал ЛПР, в базе такого контакта нет")

    return Score(components=c, reasons=r, contradictions=tuple(contradictions))


@dataclass(frozen=True, slots=True)
class Eligibility:
    eligible: bool
    blockers: tuple[str, ...]
    exception_possible: bool  # WARM or needs_review: the operator may allow with a reason


def eligibility(
    score: Score,
    research: Research,
    *,
    contacts: Sequence[Contact],
    suppressed: bool,
    needs_review: bool,
    operator_exception: bool,
) -> Eligibility:
    """Doc 04: standard queue = score >= 80, a confirmed contact, a personalization source,
    a fitting offer and no contact ban. WARM needs a recorded operator exception;
    WATCH and SKIP never enter the message queue. HOT alone is never enough (T-05)."""
    hard: list[str] = []
    if suppressed:
        hard.append("запрет контакта")
    if not any(x.verified_at for x in contacts):
        hard.append("нет подтверждённого контакта")
    if research.insufficient:
        hard.append("нет источника для персонализации")
    if research.recommended_product is None:
        hard.append("нет подходящего оффера")
    if score.priority in (Priority.WATCH, Priority.SKIP):
        hard.append(f"приоритет {score.priority} — не для очереди сообщений")
    soft: list[str] = []
    if score.priority is Priority.WARM:
        soft.append("WARM: нужно решение оператора с обоснованием")
    if needs_review:
        soft.append("research требует проверки")
    blockers = hard + ([] if operator_exception else soft)
    return Eligibility(
        eligible=not blockers,
        blockers=tuple(blockers),
        exception_possible=not hard and bool(soft) and not operator_exception,
    )
