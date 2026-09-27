from datetime import timedelta

import pytest

from hunter.application import followups, messaging, outreach
from hunter.application.errors import ErrorCode, HunterError
from hunter.domain.catalog import Stage
from hunter.domain.messages import MessageKind, MessageStatus
from hunter.domain.replies import ReplyAction, ReplyClass
from tests.research_support import FakeKnowledge, FakeLlm, config
from tests.test_import import TODAY
from tests.test_messaging import draft_answer
from tests.test_outreach import build_world, send

FOLLOW_BODY = "Здравствуйте! Дополню вопрос: можно начать с одного процесса — записи. Обсудим?"


@pytest.fixture
def sent():
    deps, clock, admin, operator, lead, message_id, approval = build_world()
    send(deps, operator, message_id, approval)
    return deps, clock, operator, lead


def step_task(deps, step):
    return next(t for t in deps.store.open_tasks() if t.sequence_step == step)


def draft_follow_up(deps, operator, task, body=FOLLOW_BODY):
    llm = FakeLlm(draft_answer(body=body, case_claim_ids=[]))
    result = followups.create_follow_up_draft(
        deps, operator, task.id, llm, FakeKnowledge(), config(), today=TODAY
    )
    return result, llm


def test_follow_up_is_a_separate_draft_with_its_own_approval_fr11(sent):
    deps, clock, operator, lead = sent
    clock.advance(days=4)
    result, llm = draft_follow_up(deps, operator, step_task(deps, 1))
    assert result.status == "created"
    message, _ = messaging.current_version(deps, result.message_id)
    assert (message.kind, message.sequence_step, message.status) == (
        MessageKind.FOLLOW_UP,
        1,
        MessageStatus.PENDING_REVIEW,
    )
    user = llm.calls[0][1]
    assert "Ответа на предыдущие сообщения нет" in user and "D4" in user
    approval = messaging.approve(deps, operator, message.id, 1)
    outreach.manual_send(
        deps,
        operator,
        message.id,
        expected_version=1,
        approval_id=approval.id,
        sent_at=clock.now(),
        idempotency_key="f-1",
    )
    assert [t.sequence_step for t in deps.store.open_tasks()] == [2, 3]
    assert deps.store.lead(lead.id).stage is Stage.CONTACTED


def test_follow_up_not_before_due(sent):
    deps, _, operator, _ = sent
    with pytest.raises(HunterError) as exc:
        draft_follow_up(deps, operator, step_task(deps, 1))
    assert exc.value.code is ErrorCode.INVALID_TRANSITION


def test_last_follow_up_moves_lead_to_nurture(sent):
    deps, clock, operator, lead = sent
    clock.advance(days=25)
    task = step_task(deps, 3)
    result, _ = draft_follow_up(
        deps, operator, task, "Похоже, сейчас вопрос не в приоритете. На этом остановлю сообщения."
    )
    approval = messaging.approve(deps, operator, result.message_id, 1)
    outreach.manual_send(
        deps,
        operator,
        result.message_id,
        expected_version=1,
        approval_id=approval.id,
        sent_at=clock.now(),
        idempotency_key="f-3",
    )
    assert deps.store.lead(lead.id).stage is Stage.NURTURE


def test_downtime_collapses_missed_touches_t23(sent):
    deps, clock, operator, lead = sent
    clock.advance(days=30)
    report = followups.scheduler_tick(deps, operator)
    assert (report.collapsed_leads, report.due_follow_ups) == (1, 0)
    [task] = deps.store.open_tasks()
    assert task.type == followups.RELEVANCE_TASK
    sent_count = deps.store.conn.execute("SELECT COUNT(*) FROM send_attempts").fetchone()[0]
    assert sent_count == 1  # only the original first message; nothing was sent by the tick


def test_single_due_touch_is_left_for_the_operator(sent):
    deps, clock, operator, _ = sent
    clock.advance(days=5)
    report = followups.scheduler_tick(deps, operator)
    assert (report.due_follow_ups, report.collapsed_leads) == (1, 0)
    assert len(deps.store.open_tasks()) == 3


def test_reply_blocks_follow_up_draft(sent):
    deps, clock, operator, lead = sent
    clock.advance(days=4)
    task = step_task(deps, 1)
    outreach.record_reply(deps, operator, lead.id, "Вопрос по срокам?", clock.now())
    with pytest.raises(HunterError):  # the task was cancelled by the reply
        draft_follow_up(deps, operator, task)


def test_nurture_return_is_a_research_task_fr12(sent):
    deps, clock, operator, lead = sent
    reply = outreach.record_reply(deps, operator, lead.id, "Напишите в ноябре 2026", clock.now())
    back = clock.now() + timedelta(days=40)
    outreach.review_reply(
        deps,
        operator,
        reply.inbound_id,
        expected_version=1,
        reply_class=ReplyClass.LATER,
        action=ReplyAction.NURTURE,
        return_at=back,
    )
    clock.advance(days=41)
    assert followups.scheduler_tick(deps, operator).due_returns == 1
    [task] = deps.store.open_tasks()
    followups.complete_task(deps, operator, task.id, outcome="relevant", note="снова ищут")
    assert deps.store.lead(lead.id).stage is Stage.RESEARCHED
    assert deps.store.active_jobs("research") == 1  # a fresh research, not a message


def test_not_relevant_closes_lead(sent):
    deps, clock, operator, lead = sent
    clock.advance(days=30)
    followups.scheduler_tick(deps, operator)
    [task] = deps.store.open_tasks()
    followups.complete_task(deps, operator, task.id, outcome="not_relevant", note="нашли других")
    stored = deps.store.lead(lead.id)
    assert (stored.stage, stored.loss_reason) == (Stage.LOST, "нашли других")
