"""Messages, immutable versions and approvals. (H-07, FR-07, FR-08)

Doc 10: an approval belongs to an exact payload. The hash covers message id, version,
recipient (contact, channel, normalized address) and the exact bytes of the body; the server
recomputes it, a hash sent by a client proves nothing. Editing the text or changing the
recipient creates a new version and revokes approvals of older versions. An approval is valid
for 24 hours (doc 10, v1 setting).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from hunter.domain.cases import presents_planned_as_done

APPROVAL_TTL = timedelta(hours=24)
FIRST_MESSAGE_LIMIT = 900  # doc 11 personalization prompt
FOLLOW_UP_LIMIT = 900
MESSAGE_HARD_LIMIT = 1_500  # doc 09 save_draft tool limit

_PLACEHOLDER = re.compile(r"\{[^{}\n]{1,40}\}")
# Service references leaked into the text: [F1], [E3], [auto_crm:v1], [01-company-and-cases.md]
_REFERENCE = re.compile(r"\[[^\]\n]*(?:\b[FE]\d+\b|\w:v\d+|\.md)[^\]\n]*\]")


class MessageKind(StrEnum):
    INITIAL = "initial"
    FOLLOW_UP = "follow_up"
    RESPONSE = "response"


class MessageStatus(StrEnum):
    DRAFT = "draft"  # no approvable text yet (the model asked for review without a body)
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    SENT = "sent"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


ACTIVE_STATUSES = frozenset(
    {MessageStatus.DRAFT, MessageStatus.PENDING_REVIEW, MessageStatus.APPROVED}
)


class SkipReason(StrEnum):
    """Doc 12 panel: weak fit, no data, wrong contact, stale signal, other."""

    WEAK_FIT = "weak_fit"
    NO_DATA = "no_data"
    WRONG_CONTACT = "wrong_contact"
    STALE_SIGNAL = "stale_signal"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Message:
    id: str
    lead_id: str
    kind: MessageKind
    sequence_step: int
    status: MessageStatus
    current_version: int
    skip_reason: str | None = None
    sent_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Recipient:
    contact_id: str
    channel: str
    normalized_address: str


@dataclass(frozen=True, slots=True)
class MessageVersion:
    message_id: str
    version: int
    body: str
    recipient: Recipient
    payload_hash: str
    evidence_ids: tuple[str, ...]
    case_claim_ids: tuple[str, ...]
    review_reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    created_by: str
    created_at: datetime

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("versions start at 1")
        if self.payload_hash != payload_hash(
            self.message_id, self.version, self.recipient, self.body
        ):
            raise ValueError("payload hash does not match the version content")


@dataclass(frozen=True, slots=True)
class Approval:
    id: str
    message_id: str
    message_version: int
    payload_hash: str
    actor_id: str
    approved_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None

    def valid_for(self, version: MessageVersion, now: datetime) -> bool:
        return (
            self.revoked_at is None
            and now < self.expires_at
            and self.message_version == version.version
            and self.payload_hash == version.payload_hash
        )


def payload_hash(message_id: str, version: int, recipient: Recipient, body: str) -> str:
    canonical = json.dumps(
        {
            "message_id": message_id,
            "version": version,
            "contact_id": recipient.contact_id,
            "channel": recipient.channel,
            "normalized_address": recipient.normalized_address,
            "body": body,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def body_blockers(body: str, kind: MessageKind) -> tuple[str, ...]:
    """Problems that forbid approval until the text is fixed (doc 11 checks 6-7, doc 05)."""
    problems = []
    limit = FIRST_MESSAGE_LIMIT if kind is MessageKind.INITIAL else FOLLOW_UP_LIMIT
    if not body.strip():
        problems.append("нет текста сообщения")
    if len(body) > limit:
        problems.append(f"текст длиннее {limit} символов ({len(body)})")
    if _PLACEHOLDER.search(body):
        problems.append("в тексте остались незаполненные поля {…}")
    if _REFERENCE.search(body):
        problems.append("в тексте остались служебные ссылки [F1], [кейс:v1], [*.md]")
    if presents_planned_as_done(body):
        problems.append("помощник претензионщика описан как готовый продукт")
    return tuple(problems)
