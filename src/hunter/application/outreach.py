"""Manual send, inbound replies and contact bans. (H-09: FR-09, FR-10, FR-12, FR-13)

Manual send (doc 10 "Идемпотентность и гонки"): one transaction locks the message, checks
the version, the live approval of exactly that payload, the ban and a pending reply, then
records one `sent` event and the D4/D9/D25 tasks. The same idempotency key with the same
request returns the previous result (T-12); with a different request it is a conflict (T-13).
The application cannot prevent a second send made outside it — the operator checks the channel.

A reply is stored and the chain is paused first (tasks cancelled, unsent messages and
approvals cancelled — T-14); classification is only a proposal the operator confirms.
A contact ban cancels matching tasks and messages for every lead of the company (T-15).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from hunter.application.access import require, require_mode
from hunter.application.context import Deps
from hunter.application.errors import ErrorCode, HunterError
from hunter.application.followups import after_follow_up_sent
from hunter.application.lead_stage import move_lead
from hunter.application.llm_calls import structured_call
from hunter.application.messaging import current_version
from hunter.application.ports import KnowledgeBase, LlmClient
from hunter.application.prompts import (
    REPLY_DOCUMENTS,
    build_reply_user,
    build_system,
    reply_schema,
)
from hunter.application.research import ResearchConfig, spent_today
from hunter.domain.budget import RunBudget
from hunter.domain.catalog import Stage
from hunter.domain.followup import follow_up_plan
from hunter.domain.messages import MessageKind, MessageStatus
from hunter.domain.modes import Operation
from hunter.domain.replies import ReplyAction, ReplyClass, ReplyProposal, parse_reply
from hunter.domain.roles import Permission
from hunter.domain.users import User

CLOCK_SKEW = timedelta(minutes=5)
MAX_REPLY_CHARS = 20_000


@dataclass(frozen=True, slots=True)
class SendReceipt:
    attempt_id: str
    message_id: str
    version: int
    sent_at: str
    tasks_created: int
    replayed: bool = False


def manual_send(
    deps: Deps,
    actor: User,
    message_id: str,
    *,
    expected_version: int,
    approval_id: str,
    sent_at: datetime,
    idempotency_key: str,
) -> SendReceipt:
    require(actor, Permission.MANUAL_SEND)
    require_mode(deps, Operation.MANUAL_SEND)
    now = deps.clock.now()
    if sent_at.tzinfo is None:
        raise HunterError(ErrorCode.INVALID_INPUT, "время отправки нужно с часовым поясом")
    if sent_at > now + CLOCK_SKEW:
        raise HunterError(ErrorCode.INVALID_INPUT, "время отправки в будущем")
    request = json.dumps(
        [message_id, expected_version, approval_id, sent_at.isoformat()], separators=(",", ":")
    )
    request_hash = hashlib.sha256(request.encode()).hexdigest()
    store = deps.store
    with store.transaction():
        prior = store.idempotent_result(idempotency_key)
        if prior is not None:
            if prior[0] != request_hash:
                raise HunterError(
                    ErrorCode.IDEMPOTENCY_CONFLICT, "этот ключ уже использован для другой отправки"
                )
            return SendReceipt(**{**json.loads(prior[1]), "replayed": True})

        message, version = current_version(deps, message_id)
        if message.current_version != expected_version:
            raise HunterError(ErrorCode.VERSION_CONFLICT, "сообщение обновилось, откройте заново")
        if message.status is not MessageStatus.APPROVED:
            raise HunterError(ErrorCode.INVALID_TRANSITION, f"сообщение в статусе {message.status}")
        approval, live = store.approval(approval_id), store.live_approval(message.id)
        if approval is None or live is None or live.id != approval.id:
            raise HunterError(ErrorCode.VERSION_CONFLICT, "согласование не действует")
        if now >= approval.expires_at:
            raise HunterError(
                ErrorCode.APPROVAL_EXPIRED, "согласование истекло — нужен новый Approve"
            )
        if not approval.valid_for(version, now):
            raise HunterError(ErrorCode.VERSION_CONFLICT, "согласование относится к другой версии")
        lead = store.lead(message.lead_id)
        if store.contact_blocked(lead.company_id, version.recipient.contact_id):
            raise HunterError(ErrorCode.SUPPRESSED, "запрет контакта сильнее согласования")
        if store.unreviewed_inbound(lead.id):
            raise HunterError(
                ErrorCode.INVALID_TRANSITION, "есть неразобранный ответ — цепочка на паузе"
            )

        attempt_id = deps.ids.new_id()
        store.add_send_attempt(
            attempt_id,
            message_id=message.id,
            version=version.version,
            approval_id=approval.id,
            key=idempotency_key,
            sent_at=sent_at,
            actor_id=actor.id,
            at=now,
        )
        if not store.mark_message_sent(message.id, sent_at, now):
            raise HunterError(ErrorCode.VERSION_CONFLICT, "сообщение изменено параллельно")
        created = 0
        if message.kind is MessageKind.INITIAL:
            move_lead(deps, actor, lead.id, Stage.CONTACTED, only_from=frozenset({Stage.APPROVED}))
            for step, due_at in follow_up_plan(sent_at):
                created += store.add_task(
                    deps.ids.new_id(), lead.id, "follow_up", step, due_at, now
                )
        elif message.kind is MessageKind.FOLLOW_UP:
            after_follow_up_sent(deps, actor, lead.id, message.sequence_step)
        deps.audit(
            actor,
            "message",
            message.id,
            "sent_manual",
            new_version=version.version,
            reason=f"sent_at {sent_at.isoformat()}; attempt {attempt_id}",
        )
        receipt = SendReceipt(attempt_id, message.id, version.version, sent_at.isoformat(), created)
        store.save_idempotent(
            idempotency_key, "manual_send", request_hash, json.dumps(_receipt_dict(receipt)), now
        )
        return receipt


def _receipt_dict(receipt: SendReceipt) -> dict:
    return {
        "attempt_id": receipt.attempt_id,
        "message_id": receipt.message_id,
        "version": receipt.version,
        "sent_at": receipt.sent_at,
        "tasks_created": receipt.tasks_created,
    }


@dataclass(frozen=True, slots=True)
class ReplyReceipt:
    inbound_id: str
    created: bool
    tasks_cancelled: int
    messages_cancelled: int


def record_reply(
    deps: Deps,
    actor: User,
    lead_id: str,
    body: str,
    received_at: datetime,
    *,
    contact_id: str | None = None,
    external_event_id: str | None = None,
) -> ReplyReceipt:
    require(actor, Permission.RECORD_REPLY)
    require_mode(deps, Operation.RECORD_REPLY)
    text = body.strip()
    if not text or len(text) > MAX_REPLY_CHARS:
        raise HunterError(ErrorCode.INVALID_INPUT, f"текст ответа: 1–{MAX_REPLY_CHARS} символов")
    if received_at.tzinfo is None:
        raise HunterError(ErrorCode.INVALID_INPUT, "время получения нужно с часовым поясом")
    event_id = (
        external_event_id
        or hashlib.sha256(f"{lead_id}|{received_at.isoformat()}|{text}".encode()).hexdigest()[:32]
    )
    store = deps.store
    with store.transaction():
        existing = store.inbound_by_event("manual", event_id)
        if existing is not None:
            return ReplyReceipt(existing.id, False, 0, 0)
        lead = store.lead(lead_id)
        if lead is None:
            raise HunterError(ErrorCode.NOT_FOUND, "лид не найден")
        now = deps.clock.now()
        inbound_id = deps.ids.new_id()
        store.add_inbound(
            inbound_id,
            lead_id=lead_id,
            contact_id=contact_id,
            channel="manual",
            external_event_id=event_id,
            body=text,
            received_at=received_at,
            at=now,
        )
        # Pause the chain before anything else (T-14).
        tasks = store.cancel_open_tasks(lead_id, now, "reply received")
        messages = store.cancel_chain_messages(lead_id, now)
        move_lead(
            deps,
            actor,
            lead_id,
            Stage.REPLIED,
            only_from=frozenset({Stage.RESEARCHED, Stage.APPROVED, Stage.CONTACTED, Stage.NURTURE}),
            reason="получен ответ",
        )
        deps.audit(
            actor,
            "lead",
            lead_id,
            "reply_recorded",
            reason=f"inbound {inbound_id}; cancelled tasks {tasks}, messages {len(messages)}",
        )
    return ReplyReceipt(inbound_id, True, tasks, len(messages))


def classify_reply(
    deps: Deps,
    inbound_id: str,
    llm: LlmClient,
    knowledge: KnowledgeBase,
    config: ResearchConfig,
    *,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> ReplyProposal | None:
    """Ask the model for a reply_v1 proposal; None when the call failed (operator decides)."""
    store = deps.store
    inbound = store.inbound(inbound_id)
    if inbound is None:
        raise HunterError(ErrorCode.NOT_FOUND, "ответ не найден")
    sent = [
        store.version(m.id, m.current_version).body
        for m in store.messages_for_lead(inbound.lead_id)
        if m.status is MessageStatus.SENT
    ][-2:]
    received_on = inbound.received_at.astimezone(config.business_tz).date()
    budget = RunBudget(config.limits, config.pricing, spent_today=spent_today(deps, config))
    started = monotonic()
    outcome = structured_call(
        llm,
        system=build_system(knowledge.documents(REPLY_DOCUMENTS), []),
        user=build_reply_user(
            body=inbound.body, recent_messages=sent, received_at=inbound.received_at.isoformat()
        ),
        schema=reply_schema(),
        budget=budget,
        validate=lambda data: parse_reply(data, body=inbound.body, received_on=received_on),
        sleep=sleep,
        elapsed=lambda: monotonic() - started,
    )
    with store.transaction():
        store.add_usage(
            deps.ids.new_id(),
            purpose="reply",
            lead_id=inbound.lead_id,
            model_id=llm.model,
            status=outcome.status,
            llm_calls=budget.llm_calls,
            input_tokens=budget.input_tokens,
            output_tokens=budget.output_tokens,
            cost=budget.cost,
            currency=config.pricing.currency,
            at=deps.clock.now(),
        )
        if outcome.status == "ok":
            proposal = outcome.value
            store.set_inbound_proposal(
                inbound_id,
                {
                    "class": proposal.reply_class.value,
                    "supporting_excerpt": proposal.supporting_excerpt,
                    "suggested_action": proposal.suggested_action.value,
                    "return_date": proposal.return_date.isoformat()
                    if proposal.return_date
                    else None,
                    "date_ambiguity": proposal.date_ambiguity,
                    "needs_review": proposal.needs_review,
                    "notes": list(proposal.notes),
                    "model": llm.model,
                },
            )
            return proposal
    return None


def review_reply(
    deps: Deps,
    actor: User,
    inbound_id: str,
    *,
    expected_version: int,
    reply_class: ReplyClass,
    action: ReplyAction,
    return_at: datetime | None = None,
    ban_scope: str = "company",
    note: str = "",
) -> None:
    """The operator's decision; only here does the CRM stage change after a reply."""
    require(actor, Permission.RECORD_REPLY)
    store = deps.store
    with store.transaction():
        inbound = store.inbound(inbound_id)
        if inbound is None:
            raise HunterError(ErrorCode.NOT_FOUND, "ответ не найден")
        now = deps.clock.now()
        lead_id = inbound.lead_id
        if action is ReplyAction.QUALIFY:
            move_lead(deps, actor, lead_id, Stage.QUALIFIED, reason=note or "положительный ответ")
        elif action is ReplyAction.NURTURE:
            if return_at is None or return_at.tzinfo is None or return_at <= now:
                raise HunterError(
                    ErrorCode.INVALID_INPUT,
                    "для NURTURE нужна будущая дата возврата с часовым поясом (FR-12)",
                )
            move_lead(deps, actor, lead_id, Stage.NURTURE, reason=note or "вернуться позже")
            # FR-12: the return creates a research task, never an outgoing message.
            store.add_task(deps.ids.new_id(), lead_id, "nurture_research", 0, return_at, now)
        elif action is ReplyAction.CLOSE:
            move_lead(deps, actor, lead_id, Stage.LOST, loss_reason=note or "not_interested")
        elif action is ReplyAction.SUPPRESS:
            lead = store.lead(lead_id)
            if ban_scope == "contact" and inbound.contact_id:
                scope_type, scope_id = "contact", inbound.contact_id
            else:
                scope_type, scope_id = "company", lead.company_id
            suppress(
                deps, actor, scope_type, scope_id, note or "opt_out", source_event_id=inbound_id
            )
            move_lead(deps, actor, lead_id, Stage.LOST, loss_reason="opt_out")
        else:
            task_type = {
                ReplyAction.ANSWER_QUESTION: "answer_question",
                ReplyAction.REROUTE: "reroute",
                ReplyAction.REVIEW: "review_reply",
            }[action]
            store.add_task(deps.ids.new_id(), lead_id, task_type, 0, now, now, note or None)
        if not store.review_inbound(
            inbound_id,
            reply_class=reply_class.value,
            action=action.value,
            actor_id=actor.id,
            expected_version=expected_version,
            at=now,
        ):
            raise HunterError(ErrorCode.VERSION_CONFLICT, "ответ уже разобран или изменён")
        deps.audit(actor, "inbound", inbound_id, f"reviewed:{reply_class}:{action}", reason=note)


def suppress(
    deps: Deps,
    actor: User,
    scope_type: str,
    scope_id: str,
    reason: str,
    *,
    source_event_id: str | None = None,
) -> str:
    """FR-13: a ban on a contact or a whole company. Stronger than any approval."""
    require(actor, Permission.SUPPRESS)
    require_mode(deps, Operation.SUPPRESS)
    if scope_type not in ("contact", "company") or not reason.strip():
        raise HunterError(
            ErrorCode.INVALID_INPUT, "область: contact или company; причина обязательна"
        )
    store = deps.store
    with store.transaction():
        now = deps.clock.now()
        if scope_type == "contact":
            contact = store.contact(scope_id)
            if contact is None:
                raise HunterError(ErrorCode.NOT_FOUND, "контакт не найден")
            company_id, addresses = contact.company_id, [contact.normalized_address]
        else:
            if store.company(scope_id) is None:
                raise HunterError(ErrorCode.NOT_FOUND, "компания не найдена")
            company_id = scope_id
            addresses = [c.normalized_address for c in store.contacts_for(company_id)]
        company = store.company(company_id)
        suppression_id = deps.ids.new_id()
        store.add_suppression(
            suppression_id,
            group_id=suppression_id,
            scope_type=scope_type,
            scope_id=scope_id,
            match_domain=company["normalized_domain"] if scope_type == "company" else None,
            match_address=addresses[0] if scope_type == "contact" else None,
            reason=reason.strip(),
            source_event_id=source_event_id,
            actor_id=actor.id,
            at=now,
        )
        if scope_type == "company":
            # Future listings with any known address of this company are screened out too.
            for address in addresses:
                store.add_suppression(
                    deps.ids.new_id(),
                    group_id=suppression_id,
                    scope_type="company",
                    scope_id=company_id,
                    match_domain=None,
                    match_address=address,
                    reason=reason.strip(),
                    source_event_id=source_event_id,
                    actor_id=actor.id,
                    at=now,
                )
        cancelled = 0
        for lead in store.leads_of_company(company_id):
            if scope_type == "company" or store.company_suppressed(company_id):
                cancelled += store.cancel_open_tasks(lead.id, now, "suppressed")
                cancelled += len(store.cancel_chain_messages(lead.id, now))
            else:
                cancelled += _cancel_for_contact(deps, lead.id, scope_id, now)
        deps.audit(
            actor,
            "suppression",
            suppression_id,
            f"suppress:{scope_type}",
            reason=f"{reason.strip()}; cancelled {cancelled}",
        )
        return suppression_id


def _cancel_for_contact(deps: Deps, lead_id: str, contact_id: str, now: datetime) -> int:
    """Cancel unsent messages addressed to the banned contact only."""
    cancelled = 0
    for message in deps.store.messages_for_lead(lead_id):
        if message.status not in (
            MessageStatus.DRAFT,
            MessageStatus.PENDING_REVIEW,
            MessageStatus.APPROVED,
        ):
            continue
        version = deps.store.version(message.id, message.current_version)
        if version.recipient.contact_id == contact_id:
            deps.store.revoke_approvals(message.id, now, "suppressed")
            deps.store.set_message_state(
                message.id,
                status=MessageStatus.CANCELLED,
                current_version=message.current_version,
                expected_version=message.current_version,
                at=now,
            )
            cancelled += 1
    return cancelled


def lift_suppression(deps: Deps, actor: User, suppression_id: str, reason: str) -> None:
    """Admin only, with the recorded basis of a new permission (doc 09)."""
    require(actor, Permission.LIFT_SUPPRESSION)
    if len(reason.strip()) < 10:
        raise HunterError(ErrorCode.INVALID_INPUT, "укажите основание нового разрешения")
    with deps.store.transaction():
        if not deps.store.lift_suppression(
            suppression_id, actor.id, reason.strip(), deps.clock.now()
        ):
            raise HunterError(ErrorCode.NOT_FOUND, "активный запрет не найден")
        deps.audit(actor, "suppression", suppression_id, "lift", reason=reason.strip())
