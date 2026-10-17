"""First message draft from a research result. (S3-03, FR-07)

Gates before the model is called (doc 04, doc 08): no confirmed contact — no draft; no traceable
fact — review, not a text made up from nothing. The model gets the evidence the research cited,
the hypothesis, the offer and the case claims the research chose, with their exact wording. Its
answer is parsed (draft_v1, one format correction) and checked (body_blockers, T-07 through the
case registry). A text becomes an immutable MessageVersion (S3-01); whether it may go to the
approval queue is in `status`.

Spend: every outcome carries usage and cost; the daily limit is checked by the budget (S2-07).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from hunter.application.llm import LlmClient, Usage
from hunter.application.llm_calls import call_parsed
from hunter.application.prompts import EvidenceItem, build_draft_request
from hunter.domain.cases import CaseRegistry
from hunter.domain.drafts import FIRST_MESSAGE_LIMIT, body_blockers, parse_draft
from hunter.domain.entities import Contact, Evidence
from hunter.domain.messages import MessageKind, MessageVersion, Recipient
from hunter.domain.research import Research


class DraftStatus(StrEnum):
    READY = "ready"  # a text without blockers: goes to the approval queue (pending_review)
    NEEDS_REVIEW = (
        "needs_review"  # a text with blockers or reasons, or no text: the operator decides
    )
    NO_CONTACT = "no_contact"  # no confirmed contact: nothing to draft for
    FAILED = "failed"  # the model failed or answered twice in a wrong shape


@dataclass(frozen=True, slots=True)
class DraftOutcome:
    status: DraftStatus
    version: MessageVersion | None = None
    blockers: tuple[str, ...] = ()
    review_reasons: tuple[str, ...] = ()
    usage: Usage = Usage(0, 0)
    cost: Decimal | None = Decimal(0)
    corrections: int = 0
    model_id: str | None = None
    error: str | None = None


def prepare_draft(
    *,
    message_id: str,
    company_name: str,
    research: Research,
    evidence: Mapping[str, Evidence],
    contact: Contact | None,
    registry: CaseRegistry,
    client: LlmClient,
    created_by: str,
    now: datetime,
) -> DraftOutcome:
    """`evidence` holds the records the research cites; `contact` is the one to write to, already
    filtered by bans (suppression_key, S1-13)."""
    if contact is None or contact.verified_at is None:
        return DraftOutcome(
            DraftStatus.NO_CONTACT, review_reasons=("нет подтверждённого контакта",)
        )
    if research.insufficient_evidence:
        return DraftOutcome(
            DraftStatus.NEEDS_REVIEW, review_reasons=("нет проверяемых фактов для персонализации",)
        )

    cited = list(dict.fromkeys(i for fact in research.facts for i in fact.evidence_ids))
    aliases = {
        f"E{n}": evidence_id for n, evidence_id in enumerate(cited, 1) if evidence_id in evidence
    }
    items = [
        EvidenceItem(alias, "факт исследования", None, evidence[evidence_id].excerpt)
        for alias, evidence_id in aliases.items()
    ]
    usable = {claim.id: claim for claim in registry.claims if claim.usable_in_messages}
    claims = [usable[c] for c in research.relevant_case_claim_ids if c in usable]
    offer = research.what_we_sell or (
        research.recommended_product.value if research.recommended_product else None
    )
    request = build_draft_request(
        call_id=f"draft:{message_id}:1",
        company_name=company_name,
        contact_role=contact.role,
        channel=contact.channel.value,
        facts=items,
        problem_hypothesis=research.problem_hypothesis,
        offer=offer,
        claims=claims,
        limit=FIRST_MESSAGE_LIMIT,
    )

    call = call_parsed(
        client,
        request,
        lambda data: parse_draft(
            data, evidence_aliases=aliases, allowed_claim_ids=frozenset(c.id for c in claims)
        ),
    )
    draft = call.value
    spent = dict(
        usage=call.usage, cost=call.cost, corrections=call.corrections, model_id=client.model_id
    )
    if draft is None:
        return DraftOutcome(DraftStatus.FAILED, error=call.error, **spent)

    blockers = body_blockers(
        draft.body, kind=MessageKind.INITIAL, registry=registry, case_claim_ids=draft.case_claim_ids
    )
    if draft.body is None:
        return DraftOutcome(
            DraftStatus.NEEDS_REVIEW,
            blockers=blockers,
            review_reasons=draft.review_reasons,
            **spent,
        )
    version = MessageVersion.create(
        message_id=message_id,
        version=1,
        body=draft.body,
        recipient=Recipient(contact.id, contact.channel, contact.normalized_address),
        evidence_ids=draft.evidence_ids,
        case_claim_ids=draft.case_claim_ids,
        created_by=created_by,
        created_at=now,
    )
    ready = not blockers and not draft.needs_review
    return DraftOutcome(
        DraftStatus.READY if ready else DraftStatus.NEEDS_REVIEW,
        version=version,
        blockers=blockers,
        review_reasons=draft.review_reasons,
        **spent,
    )
