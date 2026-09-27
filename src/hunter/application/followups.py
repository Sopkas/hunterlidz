"""Follow-up drafts, the scheduler tick and task completion. (H-10: FR-11, FR-12, T-23)

- A due D4 / D9 / D25 task lets the operator create a separate follow-up draft; it needs its
  own approval and manual send (FR-11). The model is told that there was no reply only from
  the database (doc 11), never guesses it.
- After a downtime the scheduler never releases a batch of missed touches: several overdue
  follow-ups of a lead collapse into one "check relevance" task (doc 12, T-23).
- A due NURTURE return is a research task; the operator decides whether the lead is relevant
  again (back to RESEARCHED with a fresh research job) or not (doc 04, FR-12).
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from hunter.application.access import require, require_mode
from hunter.application.context import Deps
from hunter.application.errors import ErrorCode, HunterError
from hunter.application.lead_stage import move_lead
from hunter.application.messaging import DraftResult, generate_message, pick_contact
from hunter.application.ports import KnowledgeBase, LlmClient
from hunter.application.prompts import DRAFT_DOCUMENTS, build_follow_up_user, build_system
from hunter.application.research import JOB_KIND, ResearchConfig
from hunter.application.scoring import assess
from hunter.domain.catalog import Stage
from hunter.domain.followup import FOLLOW_UP_DAYS
from hunter.domain.messages import FOLLOW_UP_LIMIT, MessageKind, MessageStatus
from hunter.domain.modes import Operation
from hunter.domain.roles import Permission
from hunter.domain.users import User

RELEVANCE_TASK = "relevance_check"


def create_follow_up_draft(
    deps: Deps,
    actor: User,
    task_id: str,
    llm: LlmClient,
    knowledge: KnowledgeBase,
    config: ResearchConfig,
    *,
    today: date,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> DraftResult:
    require(actor, Permission.WRITE_RESEARCH)
    require_mode(deps, Operation.DRAFT)
    store = deps.store
    task = store.task(task_id)
    if task is None or task.type != "follow_up" or task.status != "open":
        raise HunterError(ErrorCode.NOT_FOUND, "открытая задача follow-up не найдена")
    if task.due_at > deps.clock.now():
        raise HunterError(ErrorCode.INVALID_TRANSITION, "срок касания ещё не наступил")
    lead = store.lead(task.lead_id)
    if lead.stage is not Stage.CONTACTED:
        return DraftResult(None, "not_eligible", f"стадия лида {lead.stage}")
    if store.unreviewed_inbound(lead.id):
        return DraftResult(None, "not_eligible", "есть неразобранный ответ")
    if store.company_suppressed(lead.company_id):
        return DraftResult(None, "not_eligible", "запрет контакта")
    existing = store.active_message(lead.id, MessageKind.FOLLOW_UP, task.sequence_step)
    if existing is not None:
        return DraftResult(existing.id, "existing")

    sent = [m for m in store.messages_for_lead(lead.id) if m.status is MessageStatus.SENT]
    if not sent:
        return DraftResult(None, "not_eligible", "первое сообщение не отправлено")
    first = store.version(sent[0].id, sent[0].current_version)
    contact = store.contact(first.recipient.contact_id) or pick_contact(
        store.contacts_for(lead.company_id)
    )
    if store.contact_blocked(lead.company_id, contact.id):
        return DraftResult(None, "not_eligible", "запрет контакта")
    research = assess(deps, lead.id).research
    research_facts = research.facts if research else ()
    facts = [(f"F{i}", f.text) for i, f in enumerate(research_facts, start=1)]
    aliases = {f"F{i}": f.evidence_ids[0] for i, f in enumerate(research_facts, start=1)}
    claims = [
        c
        for c in store.case_claims()
        if research and c.id in research.relevant_case_claim_ids and c.usable_in_messages
    ]
    company = store.company(lead.company_id)
    user = build_follow_up_user(
        step=task.sequence_step,
        contact_role=contact.role or "представителя",
        contact_name=contact.name,
        company_name=(company["name"] if company else None) or "компании",
        channel=contact.channel.value,
        limit=FOLLOW_UP_LIMIT,
        today=today,
        previous=[store.version(m.id, m.current_version).body for m in sent],
        facts=facts,
        claims=claims,
    )
    return generate_message(
        deps,
        actor,
        lead.id,
        kind=MessageKind.FOLLOW_UP,
        step=task.sequence_step,
        system=build_system(knowledge.documents(DRAFT_DOCUMENTS), claims),
        user=user,
        fact_aliases=aliases,
        claims=claims,
        contact=contact,
        llm=llm,
        config=config,
        sleep=sleep,
        monotonic=monotonic,
    )


@dataclass(frozen=True, slots=True)
class TickReport:
    due_follow_ups: int
    collapsed_leads: int
    due_returns: int


def scheduler_tick(deps: Deps, actor: User, now: datetime | None = None) -> TickReport:
    """Run by cron / Task Scheduler every minute (doc 12). Never sends anything."""
    require(actor, Permission.WRITE_RESEARCH)
    store = deps.store
    now = now or deps.clock.now()
    with store.transaction():
        due = store.open_tasks(due_before=now)
        follow_ups: dict[str, list] = defaultdict(list)
        for task in due:
            if task.type == "follow_up":
                follow_ups[task.lead_id].append(task)
        collapsed = 0
        for lead_id, tasks in follow_ups.items():
            if len(tasks) < 2:
                continue
            for task in tasks:
                store.close_task(task.id, "cancelled", now, "missed during downtime")
            store.add_task(
                deps.ids.new_id(),
                lead_id,
                RELEVANCE_TASK,
                0,
                now,
                now,
                f"пропущено касаний: {len(tasks)} — проверить актуальность, выбрать одно действие",
            )
            deps.audit(actor, "lead", lead_id, "followups_collapsed", reason=f"{len(tasks)} missed")
            collapsed += 1
        returns = sum(1 for t in due if t.type == "nurture_research")
    return TickReport(
        due_follow_ups=sum(len(v) for v in follow_ups.values() if len(v) == 1),
        collapsed_leads=collapsed,
        due_returns=returns,
    )


def complete_task(
    deps: Deps,
    actor: User,
    task_id: str,
    *,
    outcome: str,
    note: str = "",
    return_at: datetime | None = None,
) -> None:
    """Outcomes: done (any task); for nurture_research / relevance_check also relevant,
    not_relevant (LOST) or later (a new return date)."""
    require(actor, Permission.EDIT_LEAD)
    store = deps.store
    with store.transaction():
        task = store.task(task_id)
        if task is None or task.status != "open":
            raise HunterError(ErrorCode.NOT_FOUND, "открытая задача не найдена")
        now = deps.clock.now()
        lead_id = task.lead_id
        if outcome == "relevant" and task.type == "nurture_research":
            move_lead(deps, actor, lead_id, Stage.RESEARCHED, reason=note or "снова актуально")
            store.add_job(deps.ids.new_id(), JOB_KIND, lead_id, now)  # fresh research first
        elif outcome == "not_relevant":
            move_lead(deps, actor, lead_id, Stage.LOST, loss_reason=note or "не актуально")
            store.cancel_open_tasks(lead_id, now, "lead closed")
        elif outcome == "later":
            if return_at is None or return_at.tzinfo is None or return_at <= now:
                raise HunterError(ErrorCode.INVALID_INPUT, "нужна будущая дата возврата с поясом")
            move_lead(deps, actor, lead_id, Stage.NURTURE, reason=note or "вернуться позже")
            store.close_task(task.id, "done", now, "rescheduled")
            store.add_task(deps.ids.new_id(), lead_id, "nurture_research", 0, return_at, now)
        elif outcome != "done":
            raise HunterError(ErrorCode.INVALID_INPUT, f"неизвестный исход {outcome!r}")
        store.close_task(task.id, "done", now, note or outcome)
        deps.audit(actor, "task", task.id, f"task:{task.type}:{outcome}", reason=note or None)


def after_follow_up_sent(deps: Deps, actor: User, lead_id: str, step: int) -> None:
    """Called inside the manual-send transaction for a follow-up message."""
    store, now = deps.store, deps.clock.now()
    for task in store.open_tasks(lead_id):
        if task.type == "follow_up" and task.sequence_step == step:
            store.close_task(task.id, "done", now, "follow-up sent")
    if step == len(FOLLOW_UP_DAYS):
        # Doc 05: after D25 the lead goes to NURTURE without a new automatic chain.
        move_lead(
            deps,
            actor,
            lead_id,
            Stage.NURTURE,
            only_from=frozenset({Stage.CONTACTED}),
            reason="цепочка D4/D9/D25 завершена",
        )
