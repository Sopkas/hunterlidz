"""The first end-to-end demo from the developer onboarding (page 9), step by step, on the
synthetic company "Тест Авто". No external recipients, no real sending."""

import json
from pathlib import Path

import pytest

from hunter.application import messaging, outreach
from hunter.application.access import set_mode
from hunter.application.case_claims import load_case_claims
from hunter.application.errors import ErrorCode, HunterError
from hunter.application.import_listings import import_listings
from hunter.application.research import enqueue_research, run_research_queue
from hunter.application.scoring import assess
from hunter.domain.catalog import Priority, Stage
from hunter.domain.modes import RunMode
from hunter.domain.replies import ReplyAction, ReplyClass
from hunter.domain.roles import Role
from tests.helpers import FixedClock, add_user, make_deps
from tests.research_support import FakeKnowledge, FakeLlm, config, feature, payload
from tests.test_import import GOOD, TODAY
from tests.test_messaging import GOOD_BODY, draft_answer
from tests.test_outreach import reply_answer

CLAIMS = Path(__file__).resolve().parents[1] / "data" / "case_claims.json"
TEST_AUTO = {**GOOD, "company": "Тест Авто"}


def test_onboarding_demo_end_to_end():
    clock = FixedClock()
    deps = make_deps(clock=clock)
    admin = add_user(deps, "boss", Role.ADMIN)
    op = add_user(deps, "op", Role.OPERATOR)
    load_case_claims(deps, admin, json.loads(CLAIMS.read_text(encoding="utf-8")))

    # 1. Import twice: one company, one active lead, the source is stored.
    kwargs = dict(
        file_name="demo.json", campaign="demo", platform="fl", currency="RUB", today=TODAY
    )
    first = import_listings(deps, op, [TEST_AUTO], **kwargs)
    second = import_listings(deps, op, [TEST_AUTO], **kwargs)
    assert (first.rows[0].result, second.rows[0].result) == ("created", "matched")
    lead = deps.store.lead(first.rows[0].lead_id)
    assert len(deps.store.list_leads()) == 1 and deps.store.sources_for(lead.company_id)

    # 2. Research and score: ICP 25, fresh signal 30, confirmed task 15, budget sign 5,
    #    contact 10 = 85 HOT.
    answer = payload(lead)
    answer["scoring_inputs"]["budget_evidence_level"] = feature("indirect", ["E4"], "косвенно")
    enqueue_research(deps, op, limit=1, min_prescore=0)
    run_research_queue(deps, FakeLlm(answer), FakeKnowledge(), config(), max_jobs=1)
    view = assess(deps, lead.id)
    assert (view.score.total, view.score.priority) == (85, Priority.HOT)

    # 3. Draft v1 approved, edited to v2: approval of v1 conflicts, v2 needs a new approval.
    draft = messaging.create_initial_draft(
        deps, op, lead.id, FakeLlm(draft_answer()), FakeKnowledge(), config(), today=TODAY
    )
    message_id = draft.message_id
    messaging.approve(deps, op, message_id, 1)
    messaging.edit(deps, op, message_id, 1, GOOD_BODY + " Хорошего дня!")
    with pytest.raises(HunterError) as exc:
        messaging.approve(deps, op, message_id, 1)
    assert exc.value.code is ErrorCode.VERSION_CONFLICT
    approval = messaging.approve(deps, op, message_id, 2)

    # 4. Simulated manual send on staging: D4/D9/D25 planned; the same key does not duplicate.
    set_mode(deps, admin, RunMode.MANUAL, "demo on staging")
    send = dict(
        expected_version=2,
        approval_id=approval.id,
        sent_at=clock.now(),
        idempotency_key="demo-send-1",
    )
    outreach.manual_send(deps, op, message_id, **send)
    outreach.manual_send(deps, op, message_id, **send)
    assert deps.store.conn.execute("SELECT COUNT(*) FROM send_attempts").fetchone()[0] == 1
    assert sorted(t.sequence_step for t in deps.store.open_tasks()) == [1, 2, 3]
    assert deps.store.lead(lead.id).stage is Stage.CONTACTED

    # 5. "Не пишите нашей компании": the chain pauses first, then the operator confirms a
    #    company ban, and a new touch is blocked.
    clock.advance(days=1)
    reply = outreach.record_reply(deps, op, lead.id, "Не пишите нашей компании.", clock.now())
    assert reply.tasks_cancelled == 3 and deps.store.open_tasks() == []
    proposal = outreach.classify_reply(
        deps, reply.inbound_id, FakeLlm(reply_answer()), FakeKnowledge(), config()
    )
    assert proposal.reply_class is ReplyClass.OPT_OUT
    outreach.review_reply(
        deps,
        op,
        reply.inbound_id,
        expected_version=1,
        reply_class=ReplyClass.OPT_OUT,
        action=ReplyAction.SUPPRESS,
    )
    assert deps.store.company_suppressed(lead.company_id)
    new_touch = messaging.create_initial_draft(
        deps, op, lead.id, FakeLlm(draft_answer()), FakeKnowledge(), config(), today=TODAY
    )
    assert new_touch.status == "not_eligible"

    # Evidence for the demo: states before/after are in the audit trail.
    actions = [e.action for e in deps.store.audit_for(lead.id)]
    for expected in (
        "create",
        "score:85:HOT",
        "stage:RESEARCHED->APPROVED",
        "stage:APPROVED->CONTACTED",
        "reply_recorded",
        "stage:REPLIED->LOST",
    ):
        assert expected in actions, expected
