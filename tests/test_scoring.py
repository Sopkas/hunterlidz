"""S2-08: doc 04 score, priority and queue readiness (T-03, T-04, T-05; T-06 decision)."""

import json
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from hunter.domain.cases import CaseClaim, ClaimStatus
from hunter.domain.catalog import Priority, priority_for
from hunter.domain.entities import (
    Channel,
    Contact,
    DatePrecision,
    Evidence,
    SourceType,
)
from hunter.domain.research import (
    BudgetLevel,
    ContactLevel,
    Feature,
    Research,
    Signal,
    parse_research,
)
from hunter.domain.scoring import (
    COMPONENTS,
    OperatorException,
    Score,
    compute_score,
    confirmed_case,
    eligibility,
)

TODAY = date(2026, 10, 18)
NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures" / "research_v1_1_valid.json"
ALIASES = {"E1": "ev-site", "E2": "ev-news", "E3": "ev-call"}
CLAIM = CaseClaim(
    claim_key="auto_crm.experience",
    case_key="auto_crm",
    claim_text="У нас есть опыт разработки CRM для автомобильного бизнеса",
    status=ClaimStatus.OWNER_REPORTED,
    external_allowed=True,
    claim_version=1,
    approved_by="owner",
)
CLAIMS = {CLAIM.id: CLAIM}
EVIDENCE = {
    eid: Evidence(
        id=eid, company_id="c-alpha", source_id=f"s-{eid}", claim="x", excerpt="y", observed_at=NOW
    )
    for eid in ALIASES.values()
}
WEBSITE_ONLY = {f"s-{eid}": SourceType.WEBSITE for eid in ALIASES.values()}
WITH_CALL = {**WEBSITE_ONLY, "s-ev-call": SourceType.CONVERSATION}
PERSON = Contact(
    id="k1",
    company_id="c-alpha",
    channel=Channel.EMAIL,
    normalized_address="ivan.petrov@alpha.ru",
    role="операционный директор",
    verified_at=NOW,
)
INFO = Contact(
    id="k2",
    company_id="c-alpha",
    channel=Channel.EMAIL,
    normalized_address="info@alpha.ru",
    verified_at=NOW,
)


def research(**features) -> Research:
    base = parse_research(
        json.loads(FIXTURE.read_text("utf-8")),
        lead_id="l-alpha",
        company_id="c-alpha",
        evidence_aliases=ALIASES,
        allowed_claim_ids=frozenset({CLAIM.id}),
        today=TODAY,
    )
    merged = dict(base.features)
    for name, value in features.items():
        old = merged[name]
        merged[name] = value if isinstance(value, Feature) else replace(old, value=value)
    return replace(base, features=merged)


def score(r: Research | None = None, **kwargs) -> Score:
    args = dict(
        today=TODAY,
        evidence=EVIDENCE,
        source_types=WEBSITE_ONLY,
        claims=CLAIMS,
        contacts=[PERSON],
    )
    args.update(kwargs)
    return compute_score(r or research(), **args)


def with_signals(*signals: Signal) -> Research:
    return replace(research(current_need_confirmed=None), signals=signals)


def signal(event_date: date | None, precision: DatePrecision = DatePrecision.DAY) -> Signal:
    return Signal("expansion", "Открыли филиал", ("ev-news",), event_date, precision)


# --- The fixture, component by component ---------------------------------------------------


def test_fixture_score_has_a_reason_for_every_component():
    s = score()
    assert set(s.components) == set(COMPONENTS)
    assert all(s.reasons[name] for name in COMPONENTS)
    assert s.rules_version == "scoring_v1"


@pytest.mark.parametrize(
    ("segment", "task", "scale", "points"),
    [
        (None, None, None, 0),
        (True, None, None, 10),
        (True, True, None, 20),
        (True, True, True, 25),
        (True, False, True, 10),
    ],
    ids=["unknown", "segment", "segment-task", "all-three", "task-denied"],
)
def test_icp_fit_levels(segment, task, scale, points):
    r = research(segment_match=segment, task_match=task, scale_match=scale)
    assert score(r).components["icp_fit"] == points


def test_task_without_segment_is_a_contradiction():
    s = score(research(segment_match=None, task_match=True))
    assert s.components["icp_fit"] == 0 and s.needs_review


@pytest.mark.parametrize(
    ("days", "points"),
    [(0, 30), (30, 30), (31, 20), (90, 20), (91, 10), (400, 10)],
    ids=["today", "30-days", "31-days", "90-days", "91-days", "old"],
)
def test_signal_age_levels(days, points):
    event = date.fromordinal(TODAY.toordinal() - days)
    assert score(with_signals(signal(event))).components["signal"] == points


def test_t03_unknown_date_gives_at_most_10():
    unknown = Signal("news", "Упоминание", ("ev-news",), None, DatePrecision.UNKNOWN)
    s = score(with_signals(unknown))
    assert s.components["signal"] == 10 and "неизвестна" in s.reasons["signal"]


def test_month_precision_is_dated_by_its_first_day():
    # 2026-10-01 is 17 days ago, 2026-09-01 is 47: the oldest possible date decides.
    assert (
        score(with_signals(signal(date(2026, 10, 1), DatePrecision.MONTH))).components["signal"]
        == 30
    )
    assert (
        score(with_signals(signal(date(2026, 9, 1), DatePrecision.MONTH))).components["signal"]
        == 20
    )


def test_freshest_signal_counts():
    s = score(with_signals(signal(date(2026, 1, 5)), signal(date(2026, 10, 10))))
    assert s.components["signal"] == 30


def test_no_signal_gives_0():
    assert score(with_signals()).components["signal"] == 0


def test_need_confirmed_in_a_conversation_gives_30():
    old = replace(with_signals(signal(date(2025, 1, 1))), features=research().features)
    assert score(old, source_types=WITH_CALL).components["signal"] == 30


def test_need_from_a_website_does_not_override_the_signal_age():
    old = replace(with_signals(signal(date(2025, 1, 1))), features=research().features)
    assert score(old, source_types=WEBSITE_ONLY).components["signal"] == 10


@pytest.mark.parametrize(
    ("problem", "case_value", "claim", "points"),
    [
        (None, None, None, 10),  # the fixture has a hypothesis
        (True, None, None, 15),
        (True, True, CLAIM, 20),
        (True, True, replace(CLAIM, status=ClaimStatus.PLANNED), 15),
        (True, True, replace(CLAIM, approved_by=None), 15),
        (True, True, replace(CLAIM, status=ClaimStatus.WITHDRAWN), 15),
    ],
    ids=["hypothesis", "confirmed-task", "confirmed-case", "planned", "unapproved", "withdrawn"],
)
def test_problem_fit_levels(problem, case_value, claim, points):
    r = research(problem_confirmed=problem, relevant_case_confirmed=case_value)
    claims = {CLAIM.id: claim} if claim else {}
    assert score(r, claims=claims).components["problem_fit"] == points


def test_no_hypothesis_and_no_task_gives_0():
    r = replace(research(problem_confirmed=None), problem_hypothesis=None)
    assert score(r).components["problem_fit"] == 0


def test_confirmed_case_rule():
    assert confirmed_case(CLAIM)
    assert confirmed_case(replace(CLAIM, status=ClaimStatus.VERIFIED))
    assert not confirmed_case(replace(CLAIM, external_allowed=False))


@pytest.mark.parametrize(
    ("level", "sources", "points"),
    [
        (BudgetLevel.UNKNOWN, WEBSITE_ONLY, 0),
        (BudgetLevel.INDIRECT, WEBSITE_ONLY, 5),
        (BudgetLevel.DIRECTIONAL_SPEND, WEBSITE_ONLY, 10),
        (BudgetLevel.CONFIRMED_RANGE, WEBSITE_ONLY, 10),
        (BudgetLevel.CONFIRMED_RANGE, WITH_CALL, 15),
    ],
    ids=["unknown", "indirect", "spend", "range-on-site", "range-in-call"],
)
def test_budget_levels(level, sources, points):
    feature = Feature(level, ("ev-call",), "бюджет")
    assert (
        score(research(budget_evidence_level=feature), source_types=sources).components["budget"]
        == points
    )


@pytest.mark.parametrize(
    ("contacts", "points"),
    [
        ([PERSON], 10),
        ([INFO], 5),
        ([replace(PERSON, role=None)], 5),
        ([replace(PERSON, verified_at=None)], 5),
        ([], 0),
    ],
    ids=["person-with-role", "shared-mailbox", "no-role", "unverified", "none"],
)
def test_contactability_comes_from_stored_contacts(contacts, points):
    assert score(contacts=contacts).components["contactability"] == points


def test_model_claiming_a_decision_maker_without_one_needs_review():
    r = research(contact_level=Feature(ContactLevel.DECISION_MAKER_CHANNEL, ("ev-site",), "x"))
    s = score(r, contacts=[INFO])
    assert s.components["contactability"] == 5 and s.needs_review


# --- Priority boundaries ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("total", "priority"),
    [
        (39, Priority.SKIP),
        (40, Priority.WATCH),
        (59, Priority.WATCH),
        (60, Priority.WARM),
        (79, Priority.WARM),
        (80, Priority.HOT),
    ],
    ids=["39", "40", "59", "60", "79", "80"],
)
def test_priority_boundaries(total, priority):
    assert priority_for(total) is priority


def _score(icp=0, signal=0, problem=0, budget=0, contact=0) -> Score:
    values = dict(
        icp_fit=icp, signal=signal, problem_fit=problem, budget=budget, contactability=contact
    )
    return Score(components=values, reasons={name: "x" for name in COMPONENTS})


@pytest.mark.parametrize(
    ("built", "total", "priority"),
    [
        (_score(icp=25, problem=10), 35, Priority.SKIP),
        (_score(icp=25, problem=15), 40, Priority.WATCH),
        (_score(icp=25, signal=20, problem=10), 55, Priority.WATCH),
        (_score(icp=25, signal=20, problem=15), 60, Priority.WARM),
        (_score(icp=20, signal=30, problem=10, budget=5, contact=10), 75, Priority.WARM),
        (_score(icp=25, signal=30, problem=15, contact=10), 80, Priority.HOT),
    ],
    ids=["35", "40", "55", "60", "75-doc-example", "80"],
)
def test_reachable_totals_around_the_boundaries(built, total, priority):
    # Every level is a multiple of 5, so 39, 59 and 79 cannot be reached by a score.
    assert (built.total, built.priority) == (total, priority)


@pytest.mark.parametrize(
    "components",
    [
        dict(icp_fit=7, signal=0, problem_fit=0, budget=0, contactability=0),
        dict(icp_fit=0, signal=0, problem_fit=0, budget=0),
        dict(icp_fit=0, signal=40, problem_fit=0, budget=0, contactability=0),
    ],
    ids=["not-a-level", "missing", "above-maximum"],
)
def test_invalid_score_is_not_created(components):
    with pytest.raises(ValueError):
        Score(components=components, reasons={name: "x" for name in components})


def test_score_needs_a_reason_per_component():
    with pytest.raises(ValueError):
        Score(components=_score().components, reasons={name: " " for name in COMPONENTS})


# --- Queue readiness ----------------------------------------------------------------------

HOT = _score(icp=25, signal=30, problem=15, contact=10)
WARM = _score(icp=25, signal=20, problem=15)
WATCH = _score(icp=25, problem=15)


def test_hot_with_everything_goes_to_the_queue():
    result = eligibility(HOT, research(), contacts=[PERSON], suppressed=False)
    assert result.eligible and result.blockers == ()


def test_t05_hot_without_a_confirmed_contact_is_not_queued():
    result = eligibility(HOT, research(), contacts=[replace(PERSON, verified_at=None)],
                         suppressed=False)  # fmt: skip
    assert not result.eligible and "нет подтверждённого контакта" in result.blockers
    assert not result.exception_possible


@pytest.mark.parametrize(
    ("r", "suppressed", "blocker"),
    [
        (research(), True, "запрет контакта"),
        (replace(research(), facts=(), needs_review=True), False, "нет источника"),
        (replace(research(), recommended_product=None), False, "нет подходящего оффера"),
    ],
    ids=["suppressed", "no-facts", "no-offer"],
)
def test_hard_blockers_cannot_be_overridden(r, suppressed, blocker):
    exception = OperatorException("u-operator", "клиент ждёт письма")
    result = eligibility(HOT, r, contacts=[PERSON], suppressed=suppressed, exception=exception)
    assert not result.eligible
    assert any(b.startswith(blocker) for b in result.blockers)


def test_warm_needs_a_recorded_operator_exception():
    without = eligibility(WARM, research(), contacts=[PERSON], suppressed=False)
    assert not without.eligible and without.exception_possible
    exception = OperatorException("u-operator", "свежий сигнал, бюджет обсудим на звонке")
    allowed = eligibility(
        WARM, research(), contacts=[PERSON], suppressed=False, exception=exception
    )
    assert allowed.eligible


def test_watch_and_skip_never_enter_the_queue():
    exception = OperatorException("u-operator", "очень хочется")
    for weak in (WATCH, _score()):
        result = eligibility(weak, research(), contacts=[PERSON], suppressed=False,
                             exception=exception)  # fmt: skip
        assert not result.eligible


def test_t06_clearing_review_does_not_bring_back_a_dropped_fact():
    # Decision of 13.10: the operator may clear needs_review with a reason, but a fact citing
    # an invented evidence id stays out of the result and so out of the draft.
    payload = json.loads(FIXTURE.read_text("utf-8"))
    payload["facts"].append({"text": "Выручка выросла вдвое", "evidence_ids": ["E9"]})
    r = parse_research(
        payload,
        lead_id="l-alpha",
        company_id="c-alpha",
        evidence_aliases=ALIASES,
        allowed_claim_ids=frozenset({CLAIM.id}),
        today=TODAY,
    )
    assert r.needs_review
    blocked = eligibility(HOT, r, contacts=[PERSON], suppressed=False)
    assert not blocked.eligible and blocked.exception_possible
    exception = OperatorException("u-operator", "остальные факты проверил по сайту")
    cleared = eligibility(HOT, r, contacts=[PERSON], suppressed=False, exception=exception)
    assert cleared.eligible
    assert all("Выручка" not in fact.text for fact in r.facts)


@pytest.mark.parametrize("fields", [("", "причина"), ("u-operator", "  ")], ids=["actor", "reason"])
def test_exception_records_who_and_why(fields):
    with pytest.raises(ValueError):
        OperatorException(*fields)
