"""Drafts, versions and approval: create / approve / edit / skip. (H-07, FR-07, FR-08)

Doc 12 panel actions and doc 10 state machine for manual mode:
draft -> pending_review -> approved (-> sent in H-09); rejected / cancelled.
- A draft is created only for a lead that passes eligibility (FR-06, T-05).
- Approve checks role, current version, recomputed payload hash, text blockers and the
  contact ban; it never says "sent" (doc 12).
- Edit creates a new immutable version, revokes live approvals and returns an APPROVED lead
  to RESEARCHED while the first message is not sent (doc 04); a stale version is a
  VERSION_CONFLICT (T-11).
- Skip records one of the doc 12 reasons.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from hunter.application.access import require, require_mode
from hunter.application.context import Deps
from hunter.application.errors import ErrorCode, HunterError
from hunter.application.lead_stage import move_lead
from hunter.application.llm_calls import structured_call
from hunter.application.ports import KnowledgeBase, LlmClient
from hunter.application.prompts import (
    DRAFT_DOCUMENTS,
    build_draft_user,
    build_system,
    draft_schema,
)
from hunter.application.research import ResearchConfig, spent_today
from hunter.application.scoring import assess
from hunter.domain.budget import RunBudget
from hunter.domain.catalog import Stage
from hunter.domain.drafts import parse_draft
from hunter.domain.entities import Contact
from hunter.domain.messages import (
    APPROVAL_TTL,
    FIRST_MESSAGE_LIMIT,
    Approval,
    Message,
    MessageKind,
    MessageStatus,
    MessageVersion,
    Recipient,
    SkipReason,
    body_blockers,
    payload_hash,
)
from hunter.domain.modes import Operation
from hunter.domain.roles import Permission
from hunter.domain.users import User


@dataclass(frozen=True, slots=True)
class DraftResult:
    message_id: str | None
    status: str  # created / existing / not_eligible / budget_exceeded / failed
    detail: str | None = None


def create_initial_draft(
    deps: Deps,
    actor: User,
    lead_id: str,
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
    existing = store.active_message(lead_id, MessageKind.INITIAL, 0)
    if existing is not None:
        return DraftResult(existing.id, "existing")
    if store.unreviewed_inbound(lead_id):
        return DraftResult(None, "not_eligible", "получен ответ — сначала разберите его")
    view = assess(deps, lead_id)
    if view.eligibility is None or not view.eligibility.eligible:
        blockers = "; ".join(view.eligibility.blockers) if view.eligibility else "нет research"
        return DraftResult(None, "not_eligible", blockers)
    lead, research = view.lead, view.research
    contact = pick_contact(store.contacts_for(lead.company_id))
    company = store.company(lead.company_id)

    claims = [
        c
        for c in store.case_claims()
        if c.id in research.relevant_case_claim_ids and c.usable_in_messages
    ]
    fact_aliases: dict[str, str] = {}
    facts = []
    for i, fact in enumerate(research.facts, start=1):
        alias = f"F{i}"
        fact_aliases[alias] = fact.evidence_ids[0]
        facts.append((alias, fact.text))
    system = build_system(knowledge.documents(DRAFT_DOCUMENTS), claims)
    user = build_draft_user(
        contact_role=contact.role or "представителя",
        contact_name=contact.name,
        company_name=(company["name"] if company else None) or "компании",
        channel=contact.channel.value,
        limit=FIRST_MESSAGE_LIMIT,
        facts=facts,
        problem_hypothesis=research.problem_hypothesis or "",
        offer=research.what_we_sell or "",
        claims=claims,
    )
    return generate_message(
        deps,
        actor,
        lead_id,
        kind=MessageKind.INITIAL,
        step=0,
        system=system,
        user=user,
        fact_aliases=fact_aliases,
        claims=claims,
        contact=contact,
        llm=llm,
        config=config,
        sleep=sleep,
        monotonic=monotonic,
    )


def generate_message(
    deps: Deps,
    actor: User,
    lead_id: str,
    *,
    kind: MessageKind,
    step: int,
    system: str,
    user: str,
    fact_aliases: dict[str, str],
    claims: list,
    contact: Contact,
    llm: LlmClient,
    config: ResearchConfig,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
) -> DraftResult:
    """One draft_v1 call under the budget, spend recorded, message v1 stored for review."""
    store = deps.store
    budget = RunBudget(config.limits, config.pricing, spent_today=spent_today(deps, config))
    started = monotonic()
    outcome = structured_call(
        llm,
        system=system,
        user=user,
        schema=draft_schema(),
        budget=budget,
        validate=lambda data: parse_draft(
            data,
            evidence_aliases=fact_aliases,
            allowed_claim_ids=frozenset(c.id for c in claims),
        ),
        sleep=sleep,
        elapsed=lambda: monotonic() - started,
    )
    generation = {
        "model": llm.model,
        "status": outcome.status,
        "llm_calls": budget.llm_calls,
        "input_tokens": budget.input_tokens,
        "output_tokens": budget.output_tokens,
        "cost": str(budget.cost),
        "currency": config.pricing.currency,
    }
    with store.transaction():
        store.add_usage(
            deps.ids.new_id(),
            purpose="draft" if kind is MessageKind.INITIAL else f"draft_{kind}",
            lead_id=lead_id,
            model_id=llm.model,
            status=outcome.status,
            llm_calls=budget.llm_calls,
            input_tokens=budget.input_tokens,
            output_tokens=budget.output_tokens,
            cost=budget.cost,
            currency=config.pricing.currency,
            at=deps.clock.now(),
        )
    if outcome.status != "ok":
        return DraftResult(None, outcome.status, outcome.error_code)
    draft = outcome.value
    blockers = body_blockers(draft.body, kind)
    status = MessageStatus.PENDING_REVIEW if draft.body else MessageStatus.DRAFT
    message_id = deps.ids.new_id()
    with store.transaction():
        if store.active_message(lead_id, kind, step) is not None:
            raise HunterError(ErrorCode.VERSION_CONFLICT, "черновик уже создан параллельно")
        now = deps.clock.now()
        store.add_message(Message(message_id, lead_id, kind, step, status, 1), now)
        store.add_version(
            _version(
                message_id,
                1,
                draft.body,
                contact,
                actor,
                now,
                evidence_ids=draft.evidence_ids,
                claim_ids=draft.case_claim_ids,
                reasons=draft.review_reasons,
                blockers=blockers,
            ),
            generation,
        )
        deps.audit(actor, "message", message_id, f"draft:{status}", new_version=1)
        deps.audit(actor, "lead", lead_id, f"draft_created:{kind}:{step}", reason=message_id)
    return DraftResult(message_id, "created")


def approve(deps: Deps, actor: User, message_id: str, expected_version: int) -> Approval:
    require(actor, Permission.APPROVE)
    require_mode(deps, Operation.APPROVE)
    store = deps.store
    with store.transaction():
        message, version = _current(deps, message_id, expected_version)
        if message.status is not MessageStatus.PENDING_REVIEW:
            raise HunterError(ErrorCode.INVALID_TRANSITION, f"сообщение в статусе {message.status}")
        # Re-check the text with the current rules, not only those stored at creation time.
        blockers = body_blockers(version.body, message.kind)
        if blockers:
            raise HunterError(ErrorCode.INVALID_INPUT, "Approve недоступен: " + "; ".join(blockers))
        recipient = store.contact(version.recipient.contact_id)
        if (
            recipient is None
            or recipient.normalized_address != version.recipient.normalized_address
        ):
            raise HunterError(ErrorCode.VERSION_CONFLICT, "адресат изменился — нужна новая версия")
        _check_contact_allowed(deps, message.lead_id, recipient)
        expected_hash = payload_hash(message.id, version.version, version.recipient, version.body)
        if expected_hash != version.payload_hash:
            raise HunterError(ErrorCode.VERSION_CONFLICT, "содержимое версии не совпадает с hash")
        now = deps.clock.now()
        store.revoke_approvals(message.id, now, "superseded")
        approval = Approval(
            id=deps.ids.new_id(),
            message_id=message.id,
            message_version=version.version,
            payload_hash=version.payload_hash,
            actor_id=actor.id,
            approved_at=now,
            expires_at=now + APPROVAL_TTL,
        )
        store.add_approval(approval)
        _set_state(deps, message, MessageStatus.APPROVED, version.version)
        if message.kind is MessageKind.INITIAL:
            move_lead(
                deps,
                actor,
                message.lead_id,
                Stage.APPROVED,
                only_from=frozenset({Stage.RESEARCHED}),
            )
        deps.audit(
            actor,
            "message",
            message.id,
            "approved",
            new_version=version.version,
            reason=f"approval {approval.id}",
        )
        return approval


def edit(
    deps: Deps,
    actor: User,
    message_id: str,
    expected_version: int,
    body: str,
    *,
    contact_id: str | None = None,
) -> int:
    """New text and/or recipient -> new version; returns the new version number."""
    require(actor, Permission.APPROVE)
    store = deps.store
    with store.transaction():
        message, version = _current(deps, message_id, expected_version)
        if message.status not in (
            MessageStatus.DRAFT,
            MessageStatus.PENDING_REVIEW,
            MessageStatus.APPROVED,
        ):
            raise HunterError(ErrorCode.INVALID_TRANSITION, f"сообщение в статусе {message.status}")
        lead = store.lead(message.lead_id)
        contact = store.contact(contact_id or version.recipient.contact_id)
        if contact is None or contact.company_id != lead.company_id:
            raise HunterError(ErrorCode.INVALID_INPUT, "адресат не относится к компании лида")
        now = deps.clock.now()
        new_number = version.version + 1
        new = _version(
            message.id,
            new_number,
            body.strip(),
            contact,
            actor,
            now,
            evidence_ids=version.evidence_ids,
            claim_ids=version.case_claim_ids,
            reasons=(),
            blockers=body_blockers(body.strip(), message.kind),
        )
        store.add_version(new)
        revoked = store.revoke_approvals(message.id, now, f"edited to v{new_number}")
        _set_state(deps, message, MessageStatus.PENDING_REVIEW, new_number)
        if message.kind is MessageKind.INITIAL:
            move_lead(
                deps,
                actor,
                message.lead_id,
                Stage.RESEARCHED,
                only_from=frozenset({Stage.APPROVED}),
            )
        deps.audit(
            actor,
            "message",
            message.id,
            "edited" + (" (approval revoked)" if revoked else ""),
            previous_version=version.version,
            new_version=new_number,
        )
        return new_number


def skip(
    deps: Deps, actor: User, message_id: str, expected_version: int, reason: SkipReason, note: str
) -> None:
    require(actor, Permission.APPROVE)
    store = deps.store
    with store.transaction():
        message, version = _current(deps, message_id, expected_version)
        if message.status not in (MessageStatus.DRAFT, MessageStatus.PENDING_REVIEW):
            raise HunterError(ErrorCode.INVALID_TRANSITION, f"сообщение в статусе {message.status}")
        _set_state(deps, message, MessageStatus.REJECTED, version.version, skip_reason=reason)
        deps.audit(
            actor,
            "message",
            message.id,
            f"skipped:{reason}",
            new_version=version.version,
            reason=note.strip() or None,
        )


def current_version(deps: Deps, message_id: str) -> tuple[Message, MessageVersion]:
    message = deps.store.message(message_id)
    if message is None:
        raise HunterError(ErrorCode.NOT_FOUND, "сообщение не найдено")
    return message, deps.store.version(message.id, message.current_version)


# --- helpers ---------------------------------------------------------------------------------


def _current(deps: Deps, message_id: str, expected_version: int):
    message, version = current_version(deps, message_id)
    if message.current_version != expected_version:
        raise HunterError(
            ErrorCode.VERSION_CONFLICT,
            f"сообщение обновилось: актуальная версия v{message.current_version}, "
            f"а не v{expected_version}. Откройте актуальную карточку.",
        )
    return message, version


def _set_state(deps, message: Message, status: MessageStatus, new_version: int, **extra) -> None:
    ok = deps.store.set_message_state(
        message.id,
        status=status,
        current_version=new_version,
        expected_version=message.current_version,
        at=deps.clock.now(),
        **extra,
    )
    if not ok:
        raise HunterError(ErrorCode.VERSION_CONFLICT, "сообщение изменено параллельно")


def pick_contact(contacts: list[Contact]) -> Contact:
    personal = [c for c in contacts if not c.company_wide and c.verified_at]
    shared = [c for c in contacts if c.verified_at]
    if not (personal or shared):
        raise HunterError(ErrorCode.MISSING_EVIDENCE, "нет подтверждённого контакта")
    return (personal or shared)[0]


def _check_contact_allowed(deps: Deps, lead_id: str, contact: Contact) -> None:
    """A contact ban is stronger than an approval; an unreviewed reply pauses the chain."""
    if deps.store.contact_blocked(contact.company_id, contact.id):
        raise HunterError(ErrorCode.SUPPRESSED, "запрет контакта сильнее согласования")
    if deps.store.unreviewed_inbound(lead_id):
        raise HunterError(
            ErrorCode.INVALID_TRANSITION, "получен ответ — цепочка на паузе до разбора"
        )


def _version(
    message_id: str,
    number: int,
    body: str,
    contact: Contact,
    actor: User,
    now,
    *,
    evidence_ids,
    claim_ids,
    reasons,
    blockers,
) -> MessageVersion:
    recipient = Recipient(contact.id, contact.channel.value, contact.normalized_address)
    return MessageVersion(
        message_id=message_id,
        version=number,
        body=body,
        recipient=recipient,
        payload_hash=payload_hash(message_id, number, recipient, body),
        evidence_ids=tuple(evidence_ids),
        case_claim_ids=tuple(claim_ids),
        review_reasons=tuple(reasons),
        blockers=tuple(blockers),
        created_by=actor.id,
        created_at=now,
    )
