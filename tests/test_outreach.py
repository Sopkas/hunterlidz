import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from hunter.application import messaging, outreach
from hunter.application.access import set_mode
from hunter.application.case_claims import load_case_claims
from hunter.application.errors import ErrorCode, HunterError
from hunter.application.import_listings import import_listings
from hunter.application.research import enqueue_research, run_research_queue
from hunter.domain.catalog import Stage
from hunter.domain.messages import MessageStatus
from hunter.domain.modes import RunMode
from hunter.domain.replies import ReplyAction, ReplyClass, ambiguous_return, parse_reply
from hunter.domain.roles import Role
from hunter.domain.screening import RejectReason
from tests.helpers import FixedClock, add_user, make_deps
from tests.research_support import FakeKnowledge, FakeLlm, config, payload
from tests.test_import import GOOD, TODAY
from tests.test_messaging import draft_answer

CLAIMS = Path(__file__).resolve().parents[1] / "data" / "case_claims.json"


def reply_answer(cls="question", action="answer_question", return_date="", **extra):
    data = {
        "schema_version": "reply_v1",
        "class": cls,
        "supporting_excerpt": "",
        "suggested_action": action,
        "return_date": return_date,
        "date_ambiguity": False,
        "needs_review": False,
    }
    data.update(extra)
    return data


@pytest.fixture
def world():
    clock = FixedClock()
    deps = make_deps(clock=clock)
    admin = add_user(deps, "boss", Role.ADMIN)
    operator = add_user(deps, "op", Role.OPERATOR)
    load_case_claims(deps, admin, json.loads(CLAIMS.read_text(encoding="utf-8")))
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
    run_research_queue(deps, FakeLlm(lambda: payload(lead)), FakeKnowledge(), config(), max_jobs=5)
    draft = messaging.create_initial_draft(
        deps, operator, lead.id, FakeLlm(draft_answer()), FakeKnowledge(), config(), today=TODAY
    )
    approval = messaging.approve(deps, operator, draft.message_id, 1)
    set_mode(deps, admin, RunMode.MANUAL, "pilot")
    return deps, clock, admin, operator, lead, draft.message_id, approval


def send(deps, operator, message_id, approval, *, key="k-1", sent_at=None):
    return outreach.manual_send(
        deps,
        operator,
        message_id,
        expected_version=1,
        approval_id=approval.id,
        sent_at=sent_at or deps.clock.now(),
        idempotency_key=key,
    )


def test_manual_send_records_event_and_plans_followups(world):
    deps, clock, _, operator, lead, message_id, approval = world
    receipt = send(deps, operator, message_id, approval)
    message, _ = messaging.current_version(deps, message_id)
    assert message.status is MessageStatus.SENT and receipt.tasks_created == 3
    assert deps.store.lead(lead.id).stage is Stage.CONTACTED
    due = sorted((t.sequence_step, (t.due_at - clock.now()).days) for t in deps.store.open_tasks())
    assert due == [(1, 4), (2, 9), (3, 25)]


def test_double_confirmation_is_one_event_t12(world):
    deps, _, _, operator, _, message_id, approval = world
    first = send(deps, operator, message_id, approval)
    again = send(deps, operator, message_id, approval)
    assert again.replayed and again.attempt_id == first.attempt_id
    assert deps.store.conn.execute("SELECT COUNT(*) FROM send_attempts").fetchone()[0] == 1
    assert len(deps.store.open_tasks()) == 3


def test_same_key_other_payload_conflicts_t13(world):
    deps, clock, _, operator, _, message_id, approval = world
    send(deps, operator, message_id, approval)
    with pytest.raises(HunterError) as exc:
        send(deps, operator, message_id, approval, sent_at=clock.now() - timedelta(hours=1))
    assert exc.value.code is ErrorCode.IDEMPOTENCY_CONFLICT
    assert deps.store.conn.execute("SELECT COUNT(*) FROM send_attempts").fetchone()[0] == 1


def test_shadow_mode_forbids_manual_send(world):
    deps, _, admin, operator, _, message_id, approval = world
    set_mode(deps, admin, RunMode.SHADOW, "back to shadow")
    with pytest.raises(HunterError) as exc:
        send(deps, operator, message_id, approval)
    assert exc.value.code is ErrorCode.FORBIDDEN


def test_expired_approval_is_refused(world):
    deps, clock, _, operator, _, message_id, approval = world
    clock.advance(hours=25)
    with pytest.raises(HunterError) as exc:
        send(deps, operator, message_id, approval, sent_at=clock.now())
    assert exc.value.code is ErrorCode.APPROVAL_EXPIRED


def test_naive_or_future_send_time_is_refused(world):
    deps, clock, _, operator, _, message_id, approval = world
    for bad in (clock.now().replace(tzinfo=None), clock.now() + timedelta(hours=1)):
        with pytest.raises(HunterError) as exc:
            send(deps, operator, message_id, approval, sent_at=bad)
        assert exc.value.code is ErrorCode.INVALID_INPUT


def test_reply_pauses_chain_before_classification_t14(world):
    deps, clock, _, operator, lead, message_id, approval = world
    send(deps, operator, message_id, approval)
    clock.advance(days=2)
    receipt = outreach.record_reply(
        deps, operator, lead.id, "Интересно, расскажите подробнее", clock.now()
    )
    assert receipt.created and receipt.tasks_cancelled == 3
    assert deps.store.open_tasks() == [] and deps.store.lead(lead.id).stage is Stage.REPLIED
    again = outreach.record_reply(
        deps, operator, lead.id, "Интересно, расскажите подробнее", clock.now()
    )
    assert not again.created and again.inbound_id == receipt.inbound_id


def test_reply_cancels_unsent_approved_message_t14(world):
    deps, clock, _, operator, lead, message_id, _ = world
    outreach.record_reply(deps, operator, lead.id, "Уже нашли подрядчика", clock.now())
    message, _ = messaging.current_version(deps, message_id)
    assert message.status is MessageStatus.CANCELLED
    assert deps.store.live_approval(message_id) is None


def test_do_not_write_becomes_company_ban_t15(world):
    deps, clock, _, operator, lead, message_id, approval = world
    send(deps, operator, message_id, approval)
    receipt = outreach.record_reply(
        deps, operator, lead.id, "Не пишите нашей компании.", clock.now()
    )
    proposal = outreach.classify_reply(
        deps, receipt.inbound_id, FakeLlm(reply_answer("question")), FakeKnowledge(), config()
    )
    assert proposal.reply_class is ReplyClass.OPT_OUT  # enforced by code, not by the model
    assert proposal.suggested_action is ReplyAction.SUPPRESS
    outreach.review_reply(
        deps,
        operator,
        receipt.inbound_id,
        expected_version=1,
        reply_class=ReplyClass.OPT_OUT,
        action=ReplyAction.SUPPRESS,
    )
    assert deps.store.company_suppressed(lead.company_id)
    stored = deps.store.lead(lead.id)
    assert (stored.stage, stored.loss_reason) == (Stage.LOST, "opt_out")
    # A new listing of the same company is screened out at import.
    other = {**GOOD, "ext_id": "NEW-1", "url": "https://fl-demo.example/tasks/NEW-1"}
    other["body"] = "Снова: " + GOOD["body"]
    report = import_listings(
        deps,
        operator,
        [other],
        file_name="f",
        campaign="c2",
        platform="fl",
        currency="RUB",
        today=TODAY,
    )
    assert report.rows[0].reason == RejectReason.SUPPRESSED


def test_ban_is_stronger_than_approval(world):
    deps, _, _, operator, lead, message_id, approval = world
    outreach.suppress(deps, operator, "company", lead.company_id, "запрос клиента")
    message, _ = messaging.current_version(deps, message_id)
    assert message.status is MessageStatus.CANCELLED
    with pytest.raises(HunterError):
        send(deps, operator, message_id, approval)


def test_month_without_year_gives_no_date_t16():
    proposal = parse_reply(
        reply_answer("later", "nurture", return_date="2026-11-01"),
        body="Вернитесь в ноябре, сейчас не до этого",
        received_on=date(2026, 9, 27),
    )
    assert proposal.return_date is None and proposal.date_ambiguity and proposal.needs_review
    assert not ambiguous_return("Вернитесь в ноябре 2026 года")


def test_nurture_needs_date_and_creates_research_task_fr12(world):
    deps, clock, _, operator, lead, message_id, approval = world
    send(deps, operator, message_id, approval)
    receipt = outreach.record_reply(deps, operator, lead.id, "Напишите в ноябре 2026", clock.now())
    with pytest.raises(HunterError):
        outreach.review_reply(
            deps,
            operator,
            receipt.inbound_id,
            expected_version=1,
            reply_class=ReplyClass.LATER,
            action=ReplyAction.NURTURE,
        )
    back = clock.now() + timedelta(days=40)
    outreach.review_reply(
        deps,
        operator,
        receipt.inbound_id,
        expected_version=1,
        reply_class=ReplyClass.LATER,
        action=ReplyAction.NURTURE,
        return_at=back,
    )
    assert deps.store.lead(lead.id).stage is Stage.NURTURE
    [task] = deps.store.open_tasks()
    assert (task.type, task.due_at) == ("nurture_research", back)
    active = [
        m
        for m in deps.store.messages_for_lead(lead.id)
        if m.status in (MessageStatus.PENDING_REVIEW, MessageStatus.APPROVED)
    ]
    assert active == []  # nurture never prepares an outgoing message by itself


def test_second_review_of_same_reply_conflicts(world):
    deps, clock, _, operator, lead, _, _ = world
    receipt = outreach.record_reply(deps, operator, lead.id, "Вопрос по срокам?", clock.now())
    outreach.review_reply(
        deps,
        operator,
        receipt.inbound_id,
        expected_version=1,
        reply_class=ReplyClass.QUESTION,
        action=ReplyAction.ANSWER_QUESTION,
    )
    with pytest.raises(HunterError) as exc:
        outreach.review_reply(
            deps,
            operator,
            receipt.inbound_id,
            expected_version=1,
            reply_class=ReplyClass.QUESTION,
            action=ReplyAction.ANSWER_QUESTION,
        )
    assert exc.value.code is ErrorCode.VERSION_CONFLICT


def test_only_admin_lifts_ban_with_basis(world):
    deps, _, admin, operator, lead, _, _ = world
    ban = outreach.suppress(deps, operator, "company", lead.company_id, "запрос клиента")
    with pytest.raises(HunterError) as exc:
        outreach.lift_suppression(deps, operator, ban, "клиент написал сам и попросил связаться")
    assert exc.value.code is ErrorCode.FORBIDDEN
    outreach.lift_suppression(deps, admin, ban, "клиент написал сам и попросил связаться")
    assert not deps.store.contact_blocked(lead.company_id, "any")


def test_paused_mode_still_records_replies(world):
    deps, clock, admin, operator, lead, message_id, approval = world
    set_mode(deps, admin, RunMode.PAUSED, "incident")
    outreach.record_reply(deps, operator, lead.id, "Ответ во время паузы", clock.now())
    with pytest.raises(HunterError):
        send(deps, operator, message_id, approval)
