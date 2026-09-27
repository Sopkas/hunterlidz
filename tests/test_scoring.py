import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from hunter.application.case_claims import load_case_claims
from hunter.application.errors import ErrorCode, HunterError
from hunter.application.import_listings import import_listings
from hunter.application.research import enqueue_research, run_research_queue
from hunter.application.scoring import allow_contact, assess, message_queue, rescore
from hunter.domain.cases import CaseClaim, ClaimStatus
from hunter.domain.catalog import Priority, Stage
from hunter.domain.contacts import Channel
from hunter.domain.entities import Contact, DatePrecision, Evidence, SourceType
from hunter.domain.research import parse_research
from hunter.domain.roles import Role
from hunter.domain.scoring import Score, compute_score, eligibility
from tests.helpers import add_user, make_deps
from tests.research_support import FakeKnowledge, FakeLlm, config, feature, payload
from tests.test_import import GOOD, TODAY

NOW = datetime(2026, 9, 27, tzinfo=UTC)
LEAD = SimpleNamespace(id="lead-1", company_id="co-1")
ALIASES = {f"E{i}": f"ev-{i}" for i in range(1, 8)}
CLAIMS = {
    "auto_crm:v1": CaseClaim("auto_crm:v1", "auto_crm", "CRM", ClaimStatus.OWNER_REPORTED, True, 1),
    "auto_crm:v2": CaseClaim("auto_crm:v2", "auto_crm", "CRM", ClaimStatus.VERIFIED, True, 2),
}
PERSONAL = Contact("c1", "co-1", Channel.TELEGRAM, "@owner", False, role="автор", verified_at=NOW)
PLATFORM = Contact("c2", "co-1", Channel.PLATFORM, "https://p/1", True, verified_at=NOW)


def research(**scoring_patch):
    data = payload(LEAD)
    data["scoring_inputs"].update(scoring_patch)
    return parse_research(
        data,
        lead_id="lead-1",
        company_id="co-1",
        evidence_aliases=ALIASES,
        allowed_claim_ids=frozenset(CLAIMS),
        today=TODAY,
    )


def score(r=None, *, contacts=(PERSONAL,), conversation=(), signals=None):
    r = r or research()
    if signals is not None:
        object.__setattr__(r, "signals", signals)
    evidence = {
        f"ev-{i}": Evidence(
            f"ev-{i}",
            "co-1",
            "s-conv" if f"ev-{i}" in conversation else "s-list",
            "f",
            "c",
            "x",
            NOW,
        )
        for i in range(1, 8)
    }
    return compute_score(
        r,
        today=TODAY,
        evidence=evidence,
        source_types={"s-list": SourceType.LISTING, "s-conv": SourceType.CONVERSATION},
        claims=CLAIMS,
        contacts=list(contacts),
    )


def test_full_listing_research_scores_85_hot():
    s = score()
    assert dict(s.components) == {
        "icp_fit": 25,
        "signal": 30,
        "problem_fit": 15,
        "budget": 10,
        "contactability": 10,
    }
    assert (s.total, s.priority) == (90, Priority.HOT)


@pytest.mark.parametrize(
    ("patch", "expected"),
    [
        ({"segment_match": feature("false")}, 0),
        ({"task_match": feature("unknown")}, 10),
        ({"scale_match": feature("unknown")}, 20),
    ],
    ids=["no-segment", "segment-only", "no-scale"],
)
def test_icp_fit_levels(patch, expected):
    assert score(research(**patch)).components["icp_fit"] == expected


def test_unknown_signal_date_capped_at_10_t03():
    r = research()
    sig = r.signals[0]
    unknown = (type(sig)(sig.type, sig.text, sig.evidence_ids, None, DatePrecision.UNKNOWN),)
    s = score(r, signals=unknown)
    assert s.components["signal"] == 10 and "неизвестна" in s.reasons["signal"]


@pytest.mark.parametrize(
    ("event", "precision", "points"),
    [
        (date(2026, 8, 28), DatePrecision.DAY, 30),  # 30 days
        (date(2026, 8, 27), DatePrecision.DAY, 20),  # 31 days
        (date(2026, 6, 29), DatePrecision.DAY, 20),  # 90 days
        (date(2026, 6, 28), DatePrecision.DAY, 10),  # 91 days
        (date(2026, 9, 15), DatePrecision.MONTH, 30),  # counted from 1 Sep: 26 days
        (date(2026, 8, 30), DatePrecision.MONTH, 20),  # counted from 1 Aug: 57 days
        (date(2026, 9, 1), DatePrecision.YEAR, 10),  # counted from 1 Jan
    ],
    ids=["d30", "d31", "d90", "d91", "month-fresh", "month-conservative", "year"],
)
def test_signal_age_boundaries(event, precision, points):
    r = research()
    sig = r.signals[0]
    s = score(r, signals=(type(sig)(sig.type, sig.text, sig.evidence_ids, event, precision),))
    assert s.components["signal"] == points


def test_no_signal_is_zero():
    assert score(signals=()).components["signal"] == 0


def test_confirmed_range_needs_conversation():
    level = {"budget_evidence_level": feature("confirmed_range", ["E4"], "указан")}
    assert score(research(**level)).components["budget"] == 10
    assert score(research(**level), conversation=("ev-4",)).components["budget"] == 15


def test_problem_fit_20_only_with_verified_case():
    owner = {"relevant_case_confirmed": feature("true", ["auto_crm:v1"], "CRM")}
    verified = {"relevant_case_confirmed": feature("true", ["auto_crm:v2"], "CRM")}
    s = score(research(**owner))
    assert s.components["problem_fit"] == 15 and "не verified" in s.reasons["problem_fit"]
    assert score(research(**verified)).components["problem_fit"] == 20


def test_contactability_from_stored_contacts():
    assert score(contacts=[PERSONAL]).components["contactability"] == 10
    platform_only = score(contacts=[PLATFORM])
    assert platform_only.components["contactability"] == 5
    assert platform_only.contradictions  # the model claimed a decision-maker channel
    assert score(contacts=[]).components["contactability"] == 0


def test_doc04_example_75_is_warm():
    s = Score(
        components={
            "icp_fit": 20,
            "signal": 30,
            "problem_fit": 10,
            "budget": 5,
            "contactability": 10,
        },
        reasons={},
        contradictions=(),
    )
    assert (s.total, s.priority) == (75, Priority.WARM)


def test_hot_without_contact_is_not_queued_t05():
    r = research()
    result = eligibility(
        score(r), r, contacts=[], suppressed=False, needs_review=False, operator_exception=False
    )
    assert not result.eligible and "нет подтверждённого контакта" in result.blockers
    assert not result.exception_possible


def test_eligibility_rules():
    r = research()
    hot = score(r)
    ok = eligibility(
        hot, r, contacts=[PERSONAL], suppressed=False, needs_review=False, operator_exception=False
    )
    assert ok.eligible
    banned = eligibility(
        hot, r, contacts=[PERSONAL], suppressed=True, needs_review=False, operator_exception=True
    )
    assert not banned.eligible and not banned.exception_possible
    warm = score(research(scale_match=feature("unknown"), task_match=feature("unknown")))
    assert warm.priority is Priority.WARM
    pending = eligibility(
        warm, r, contacts=[PERSONAL], suppressed=False, needs_review=False, operator_exception=False
    )
    assert not pending.eligible and pending.exception_possible
    allowed = eligibility(
        warm, r, contacts=[PERSONAL], suppressed=False, needs_review=False, operator_exception=True
    )
    assert allowed.eligible


def test_database_rejects_inconsistent_total():
    deps = make_deps()
    with pytest.raises(sqlite3.IntegrityError):
        deps.store.conn.execute(
            "INSERT INTO scores VALUES "
            "('s', 'l', NULL, 25, 30, 20, 15, 10, 99, '1.0', '{}', '[]', 'x')"
        )


# --- integration with the research pipeline ------------------------------------------------

CLAIMS_FILE = Path(__file__).resolve().parents[1] / "data" / "case_claims.json"


@pytest.fixture
def researched():
    deps = make_deps()
    admin = add_user(deps, "boss", Role.ADMIN)
    operator = add_user(deps, "op", Role.OPERATOR)
    load_case_claims(deps, admin, json.loads(CLAIMS_FILE.read_text(encoding="utf-8")))
    report = import_listings(
        deps,
        operator,
        [GOOD],
        file_name="f",
        campaign="c",
        platform="fl",
        currency="RUB",
        today=TODAY,
    )
    lead = deps.store.lead(report.rows[0].lead_id)
    enqueue_research(deps, operator, limit=5, min_prescore=0)
    return deps, admin, operator, lead


def run(deps, answer):
    return run_research_queue(deps, FakeLlm(answer), FakeKnowledge(), config(), max_jobs=5)


def test_pipeline_scores_and_moves_to_researched(researched):
    deps, _, _, lead = researched
    run(deps, lambda: payload(lead))
    stored = deps.store.lead(lead.id)
    assert (stored.stage, stored.score_total, stored.priority) == (
        Stage.RESEARCHED,
        90,
        Priority.HOT,
    )
    assert any(e.action == "score:90:HOT" for e in deps.store.audit_for(lead.id))
    assert [v.lead.id for v in message_queue(deps)] == [lead.id]


def test_insufficient_research_stays_new(researched):
    deps, _, _, lead = researched
    run(deps, lambda: payload(lead, facts=[]))
    stored = deps.store.lead(lead.id)
    assert stored.stage is Stage.NEW and stored.needs_review and message_queue(deps) == []


def test_warm_exception_flow_and_version_conflict(researched):
    deps, _, operator, lead = researched
    answer = payload(lead)
    answer["scoring_inputs"]["task_match"] = feature("unknown")
    run(deps, lambda: answer)
    view = assess(deps, lead.id)
    assert view.score.priority is Priority.WARM and view.eligibility.exception_possible
    stale = view.lead.version - 1
    with pytest.raises(HunterError) as exc:
        allow_contact(deps, operator, lead.id, stale, "Свежий запрос, контакт — собственник")
    assert exc.value.code is ErrorCode.VERSION_CONFLICT
    allow_contact(
        deps, operator, lead.id, view.lead.version, "Свежий запрос, контакт — собственник"
    )
    assert assess(deps, lead.id).eligibility.eligible


def test_rescore_after_case_verified(researched):
    deps, admin, operator, lead = researched
    answer = payload(lead)
    answer["scoring_inputs"]["relevant_case_confirmed"] = feature("true", ["auto_crm:v1"], "CRM")
    run(deps, lambda: answer)
    assert assess(deps, lead.id).score.components["problem_fit"] == 15
    items = json.loads(CLAIMS_FILE.read_text(encoding="utf-8"))
    for item in items:
        if item["case_key"] == "auto_crm":
            item["status"] = "verified"
    load_case_claims(deps, admin, items)
    # The research cited auto_crm:v1 (owner_reported); verification creates v2, so the old
    # research does not silently gain points — a new research run must cite the verified claim.
    assert rescore(deps, operator, lead.id, TODAY).components["problem_fit"] == 15
