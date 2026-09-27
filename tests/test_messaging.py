import json
import sqlite3
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hunter.application import messaging
from hunter.application.case_claims import load_case_claims
from hunter.application.errors import ErrorCode, HunterError
from hunter.application.import_listings import import_listings
from hunter.application.ports import LlmError, LlmErrorKind
from hunter.application.research import enqueue_research, run_research_queue
from hunter.domain.budget import Limits, Pricing
from hunter.domain.catalog import Stage
from hunter.domain.contacts import Channel
from hunter.domain.entities import Contact
from hunter.domain.messages import MessageStatus, SkipReason
from hunter.domain.roles import Role
from tests.helpers import FixedClock, add_user, make_deps
from tests.research_support import FakeKnowledge, FakeLlm, config, payload
from tests.test_import import GOOD, TODAY

CLAIMS = Path(__file__).resolve().parents[1] / "data" / "case_claims.json"
GOOD_BODY = (
    "Здравствуйте! Увидел, что ваша сеть автосервисов открывает филиалы. Как сейчас "
    "распределяются заявки между мастерами? Мы делали CRM для автосервисов. Обсудим?"
)


def draft_answer(body=GOOD_BODY, **overrides):
    data = {
        "schema_version": "draft_v1",
        "body": body,
        "evidence_ids": ["F1"],
        "case_claim_ids": ["auto_crm:v1"],
        "needs_review": False,
        "review_reasons": [],
    }
    data.update(overrides)
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
    return deps, clock, admin, operator, deps.store.lead(lead.id)


def make_draft(deps, operator, lead, *answers):
    llm = FakeLlm(*(answers or (draft_answer(),)))
    result = messaging.create_initial_draft(
        deps,
        operator,
        lead.id,
        llm,
        FakeKnowledge(),
        config(),
        today=TODAY,
        sleep=lambda s: None,
    )
    return result, llm


def test_draft_created_for_eligible_lead(world):
    deps, _, _, operator, lead = world
    result, llm = make_draft(deps, operator, lead)
    assert result.status == "created"
    message, version = messaging.current_version(deps, result.message_id)
    assert message.status is MessageStatus.PENDING_REVIEW and version.version == 1
    assert version.recipient.channel == "telegram"  # the author's own channel is preferred
    assert version.case_claim_ids == ("auto_crm:v1",) and not version.blockers
    system, user, _ = llm.calls[0]
    assert "документ 05" in system and "Сеть из 4 автосервисов" in user
    assert deps.store.cost_since(deps.clock.now() - timedelta(days=1), "USD") == 0
    usage = deps.store.conn.execute("SELECT purpose, status, llm_calls FROM llm_usage").fetchall()
    assert [tuple(r) for r in usage] == [("draft", "ok", 1)]


def test_second_request_returns_existing_draft(world):
    deps, _, _, operator, lead = world
    first, _ = make_draft(deps, operator, lead)
    again, llm = make_draft(deps, operator, lead)
    assert (again.status, again.message_id) == ("existing", first.message_id)
    assert llm.calls == []


def test_no_draft_without_contact_t05(world):
    deps, _, _, operator, lead = world
    deps.store.conn.execute("UPDATE contacts SET verified_at = NULL")
    result, llm = make_draft(deps, operator, lead)
    assert result.status == "not_eligible" and "контакт" in result.detail and llm.calls == []


def test_model_without_text_gives_unapprovable_draft(world):
    deps, _, _, operator, lead = world
    answer = draft_answer(body="", needs_review=True, review_reasons=["нет имени контакта"])
    result, _ = make_draft(deps, operator, lead, answer)
    message, version = messaging.current_version(deps, result.message_id)
    assert message.status is MessageStatus.DRAFT and "нет текста сообщения" in version.blockers
    with pytest.raises(HunterError) as exc:
        messaging.approve(deps, operator, message.id, 1)
    assert exc.value.code is ErrorCode.INVALID_TRANSITION


def test_invented_fact_reference_is_removed(world):
    deps, _, _, operator, lead = world
    result, _ = make_draft(deps, operator, lead, draft_answer(evidence_ids=["F1", "F42"]))
    _, version = messaging.current_version(deps, result.message_id)
    assert len(version.evidence_ids) == 1 and any("F42" in r for r in version.review_reasons)


def test_planned_case_as_done_blocks_approve_t07(world):
    deps, _, _, operator, lead = world
    body = "У нас есть готовый продукт — помощник претензионщика с Excel-отчётом. Обсудим?"
    result, _ = make_draft(deps, operator, lead, draft_answer(body=body))
    with pytest.raises(HunterError, match="претензионщика"):
        messaging.approve(deps, operator, result.message_id, 1)
    fixed = "Разрабатываем помощника для должников: сейчас готова оболочка бота. Обсудим?"
    v2 = messaging.edit(deps, operator, result.message_id, 1, fixed)
    messaging.approve(deps, operator, result.message_id, v2)


def test_approve_moves_lead_and_expires_in_24h(world):
    deps, clock, _, operator, lead = world
    result, _ = make_draft(deps, operator, lead)
    approval = messaging.approve(deps, operator, result.message_id, 1)
    message, version = messaging.current_version(deps, result.message_id)
    assert message.status is MessageStatus.APPROVED
    assert deps.store.lead(lead.id).stage is Stage.APPROVED
    assert approval.valid_for(version, clock.now())
    assert not approval.valid_for(version, clock.now() + timedelta(hours=24))


def test_observer_cannot_approve_t10(world):
    deps, _, _, operator, lead = world
    observer = add_user(deps, "watcher", Role.OBSERVER)
    result, _ = make_draft(deps, operator, lead)
    with pytest.raises(HunterError) as exc:
        messaging.approve(deps, observer, result.message_id, 1)
    assert exc.value.code is ErrorCode.FORBIDDEN
    message, _ = messaging.current_version(deps, result.message_id)
    assert message.status is MessageStatus.PENDING_REVIEW
    assert deps.store.live_approval(result.message_id) is None


def test_edit_revokes_approval_and_old_version_conflicts_t11(world):
    deps, clock, _, operator, lead = world
    result, _ = make_draft(deps, operator, lead)
    old = messaging.approve(deps, operator, result.message_id, 1)
    v2 = messaging.edit(deps, operator, result.message_id, 1, GOOD_BODY + " Спасибо!")
    assert v2 == 2
    assert deps.store.live_approval(result.message_id) is None
    assert deps.store.lead(lead.id).stage is Stage.RESEARCHED
    _, current = messaging.current_version(deps, result.message_id)
    assert not old.valid_for(current, clock.now())
    with pytest.raises(HunterError) as exc:
        messaging.approve(deps, operator, result.message_id, 1)
    assert exc.value.code is ErrorCode.VERSION_CONFLICT
    messaging.approve(deps, operator, result.message_id, 2)
    history = [e.action for e in deps.store.audit_for(result.message_id)]
    assert history == ["draft:pending_review", "approved", "edited (approval revoked)", "approved"]


def test_recipient_change_creates_new_version(world):
    deps, _, _, operator, lead = world
    result, _ = make_draft(deps, operator, lead)
    messaging.approve(deps, operator, result.message_id, 1)
    other = Contact(
        "c-new",
        lead.company_id,
        Channel.EMAIL,
        "owner@test-auto.example",
        False,
        role="собственник",
        verified_at=deps.clock.now(),
    )
    deps.store.add_contact(other, deps.clock.now())
    v2 = messaging.edit(deps, operator, result.message_id, 1, GOOD_BODY, contact_id="c-new")
    _, version = messaging.current_version(deps, result.message_id)
    assert (v2, version.recipient.normalized_address) == (2, "owner@test-auto.example")
    assert deps.store.live_approval(result.message_id) is None


def test_skip_records_reason(world):
    deps, _, _, operator, lead = world
    result, _ = make_draft(deps, operator, lead)
    messaging.skip(deps, operator, result.message_id, 1, SkipReason.STALE_SIGNAL, "уже нашли")
    message, _ = messaging.current_version(deps, result.message_id)
    assert (message.status, message.skip_reason) == (MessageStatus.REJECTED, "stale_signal")
    with pytest.raises(HunterError):
        messaging.approve(deps, operator, result.message_id, 1)


def test_versions_are_immutable(world):
    deps, _, _, operator, lead = world
    make_draft(deps, operator, lead)
    with pytest.raises(sqlite3.IntegrityError):
        deps.store.conn.execute("UPDATE message_versions SET body = 'x'")


def test_failed_draft_attempt_still_counts_against_daily_budget(world):
    deps, _, _, operator, lead = world
    paid = config(
        Limits(run_cost_cap=Decimal("1"), daily_cost_cap=Decimal("5")),
        Pricing(Decimal("1"), Decimal("1"), "USD"),
    )
    llm = FakeLlm({"bad": 1}, LlmError(LlmErrorKind.QUOTA), input_tokens=100_000 // 10)
    result = messaging.create_initial_draft(
        deps, operator, lead.id, llm, FakeKnowledge(), paid, today=TODAY, sleep=lambda s: None
    )
    assert result.status == "failed"
    spent = deps.store.cost_since(deps.clock.now() - timedelta(days=1), "USD")
    assert spent > 0


@pytest.mark.parametrize(
    "body",
    [
        "Видим лист ожидания [F1, F2]. Обсудим?",
        "Есть опыт [couples_app:v1]. Обсудим?",
        "Мы команда полного цикла [01-company-and-cases.md]. Обсудим?",
    ],
    ids=["facts", "claim", "document"],
)
def test_service_references_in_text_block_approve(body):
    from hunter.domain.messages import MessageKind, body_blockers

    assert any("служебные ссылки" in b for b in body_blockers(body, MessageKind.INITIAL))


def test_plain_brackets_are_allowed():
    from hunter.domain.messages import MessageKind, body_blockers

    assert body_blockers("Сроки [по договорённости] обсудим?", MessageKind.INITIAL) == ()
