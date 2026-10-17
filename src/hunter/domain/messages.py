"""Messages, immutable versions and version-bound approvals. (S3-01, CONTRACT)

Doc 10 "messages", "message_versions", "approvals", "Состояния сообщения", "Пример
согласования"; doc 12 (APPROVAL_TTL_HOURS=24). An approval belongs to an exact payload: the
hash covers message id, version, recipient (contact, channel, normalized address) and the exact
bytes of the body, and the server computes it itself. Editing the text or changing the
recipient makes a new version, so an approval of an older version no longer matches.

Case claims: a version refers to approved registry claims by id (claim_key:vN, S2-03); its text
is checked with hunter.domain.cases.check_case_text before approval (S3-03, S3-04).

Boundaries: pure values and rules; the caller passes `now`.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from hunter.domain.entities import Channel, _id, _utc, _version

APPROVAL_TTL = timedelta(hours=24)  # doc 10 v1 setting, doc 12 APPROVAL_TTL_HOURS
_CLAIM_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+:v[1-9]\d*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class MessageKind(StrEnum):
    INITIAL = "initial"
    FOLLOW_UP = "follow_up"
    RESPONSE = "response"


class MessageStatus(StrEnum):
    """Doc 10, manual mode: draft → pending_review → approved → sent, plus rejected and
    cancelled. The send-adapter states (queued, sending, failed, send_unknown) arrive with the
    adapter, like assisted_send in RunMode (S1-05)."""

    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    SENT = "sent"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


ACTIVE_STATUSES = frozenset(
    {MessageStatus.DRAFT, MessageStatus.PENDING_REVIEW, MessageStatus.APPROVED}
)
FINAL_STATUSES = frozenset({MessageStatus.SENT, MessageStatus.REJECTED, MessageStatus.CANCELLED})

# Doc 10: an edit makes a new draft version and revokes approval; a reply or a ban cancels
# what has not been sent; a confirmed send is never undone by changing the status.
TRANSITIONS: dict[MessageStatus, frozenset[MessageStatus]] = {
    MessageStatus.DRAFT: frozenset(
        {MessageStatus.DRAFT, MessageStatus.PENDING_REVIEW, MessageStatus.CANCELLED}
    ),
    MessageStatus.PENDING_REVIEW: frozenset(
        {
            MessageStatus.DRAFT,
            MessageStatus.APPROVED,
            MessageStatus.REJECTED,
            MessageStatus.CANCELLED,
        }
    ),
    MessageStatus.APPROVED: frozenset(
        {MessageStatus.DRAFT, MessageStatus.SENT, MessageStatus.CANCELLED}
    ),
    MessageStatus.SENT: frozenset(),
    MessageStatus.REJECTED: frozenset(),
    MessageStatus.CANCELLED: frozenset(),
}


class InvalidTransitionError(ValueError):
    """The status change is not allowed (error code INVALID_TRANSITION, doc 10)."""


def check_transition(current: MessageStatus, target: MessageStatus) -> None:
    if target not in TRANSITIONS[current]:
        raise InvalidTransitionError(f"{current} → {target} is not allowed")


@dataclass(frozen=True, slots=True)
class Recipient:
    """Snapshot of the addressee at the moment of the version (doc 10 recipient_snapshot)."""

    contact_id: str
    channel: Channel
    normalized_address: str

    def __post_init__(self) -> None:
        _id(self.contact_id, "contact_id")
        if not isinstance(self.channel, Channel):
            raise ValueError("channel must be a Channel")
        if (
            not self.normalized_address
            or self.normalized_address != self.normalized_address.strip()
        ):
            raise ValueError("normalized_address must be non-empty and trimmed")


def payload_hash(message_id: str, version: int, recipient: Recipient, body: str) -> str:
    """sha256 of the canonical payload: what the operator approves is exactly what is sent."""
    canonical = json.dumps(
        {
            "message_id": message_id,
            "version": version,
            "contact_id": recipient.contact_id,
            "channel": recipient.channel.value,
            "normalized_address": recipient.normalized_address,
            "body": body,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MessageVersion:
    """An immutable snapshot (doc 10: composite key message_id + version)."""

    message_id: str
    version: int
    body: str
    recipient: Recipient
    payload_hash: str
    evidence_ids: tuple[str, ...]
    case_claim_ids: tuple[str, ...]
    created_by: str
    created_at: datetime

    def __post_init__(self) -> None:
        _id(self.message_id, "message_id")
        _version(self.version)
        if not self.body.strip():
            raise ValueError("a version has a body; a message without text stays a draft")
        if not isinstance(self.recipient, Recipient):
            raise ValueError("recipient must be a Recipient")
        for name in ("evidence_ids", "case_claim_ids"):
            value = getattr(self, name)
            if not isinstance(value, tuple) or not all(isinstance(x, str) and x for x in value):
                raise ValueError(f"{name} must be a tuple of ids")
            if len(set(value)) != len(value):
                raise ValueError(f"{name} must not repeat")
        bad = [c for c in self.case_claim_ids if not _CLAIM_ID.match(c)]
        if bad:
            raise ValueError("case_claim_ids: registry claim ids like auto_crm.experience:v1")
        _id(self.created_by, "created_by")
        if self.created_at is None:
            raise ValueError("created_at is required")
        _utc(self.created_at, "created_at")
        expected = payload_hash(self.message_id, self.version, self.recipient, self.body)
        if self.payload_hash != expected:
            raise ValueError("payload_hash does not match the version content")

    @classmethod
    def create(
        cls,
        *,
        message_id: str,
        version: int,
        body: str,
        recipient: Recipient,
        evidence_ids: tuple[str, ...],
        case_claim_ids: tuple[str, ...],
        created_by: str,
        created_at: datetime,
    ) -> MessageVersion:
        return cls(
            message_id=message_id,
            version=version,
            body=body,
            recipient=recipient,
            payload_hash=payload_hash(message_id, version, recipient, body),
            evidence_ids=evidence_ids,
            case_claim_ids=case_claim_ids,
            created_by=created_by,
            created_at=created_at,
        )


@dataclass(frozen=True, slots=True)
class Approval:
    """Approval of one exact payload by an acting user, valid for APPROVAL_TTL."""

    id: str
    message_id: str
    message_version: int
    payload_hash: str
    actor_id: str
    approved_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.id, "id"),
            (self.message_id, "message_id"),
            (self.actor_id, "actor_id"),
        ):
            _id(value, name)
        _version(self.message_version)
        if not _SHA256.match(self.payload_hash):
            raise ValueError("payload_hash is a sha256 hex digest")
        for value, name in ((self.approved_at, "approved_at"), (self.expires_at, "expires_at")):
            if value is None:
                raise ValueError(f"{name} is required")
            _utc(value, name)
        _utc(self.revoked_at, "revoked_at")
        if self.expires_at <= self.approved_at:
            raise ValueError("expires_at must be after approved_at")
        if self.revoked_at is not None and self.revoked_at < self.approved_at:
            raise ValueError("revoked_at cannot precede approved_at")

    @classmethod
    def grant(cls, *, id: str, version: MessageVersion, actor_id: str, now: datetime) -> Approval:
        """The server builds the approval from the stored version: a client hash is no proof."""
        return cls(
            id=id,
            message_id=version.message_id,
            message_version=version.version,
            payload_hash=version.payload_hash,
            actor_id=actor_id,
            approved_at=now,
            expires_at=now + APPROVAL_TTL,
        )

    def valid_for(self, version: MessageVersion, now: datetime) -> bool:
        """Still binds this exact version: not revoked, not expired, same version and hash.
        Reply, ban and actor rights are checked by the use case right before sending (S3-04)."""
        return (
            self.revoked_at is None
            and now < self.expires_at
            and self.message_id == version.message_id
            and self.message_version == version.version
            and self.payload_hash == version.payload_hash
        )


@dataclass(frozen=True, slots=True)
class Message:
    """Doc 10 messages: the current state of one outgoing message of a lead."""

    id: str
    lead_id: str
    kind: MessageKind
    sequence_step: int
    status: MessageStatus
    current_version: int
    approved_version: int | None = None
    sent_at: datetime | None = None

    def __post_init__(self) -> None:
        _id(self.id)
        _id(self.lead_id, "lead_id")
        if not isinstance(self.kind, MessageKind):
            raise ValueError("kind must be a MessageKind")
        if not isinstance(self.status, MessageStatus):
            raise ValueError("status must be a MessageStatus")
        if self.sequence_step < 0:
            raise ValueError("sequence_step starts at 0 (the first message)")
        if self.kind is MessageKind.INITIAL and self.sequence_step != 0:
            raise ValueError("the initial message is step 0")
        if self.kind is MessageKind.FOLLOW_UP and self.sequence_step < 1:
            raise ValueError("a follow-up is step 1 or later")
        _version(self.current_version)
        approved = self.status in (MessageStatus.APPROVED, MessageStatus.SENT)
        if approved != (self.approved_version is not None):
            raise ValueError("approved_version is set exactly when approved or sent")
        if self.approved_version is not None and self.approved_version != self.current_version:
            raise ValueError("the approved version is the current one; an edit makes a draft")
        _utc(self.sent_at, "sent_at")
        if (self.status is MessageStatus.SENT) != (self.sent_at is not None):
            raise ValueError("sent_at is set exactly for a sent message")
