"""S3-01 contract: messages, immutable versions, version-bound approvals (doc 10, T-11)."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from hunter.domain.entities import Channel
from hunter.domain.messages import (
    APPROVAL_TTL,
    FINAL_STATUSES,
    TRANSITIONS,
    Approval,
    InvalidTransitionError,
    Message,
    MessageKind,
    MessageStatus,
    MessageVersion,
    Recipient,
    check_transition,
    payload_hash,
)

NOW = datetime(2026, 10, 18, 10, 0, tzinfo=timezone.utc)
IVAN = Recipient("k1", Channel.EMAIL, "ivan.petrov@alpha.ru")
BODY = "Анна, увидел, что «Автодом» открыл ещё два сервиса.\n\nКак сейчас устроена запись?"


def version(n: int = 1, body: str = BODY, recipient: Recipient = IVAN) -> MessageVersion:
    return MessageVersion.create(
        message_id="msg1",
        version=n,
        body=body,
        recipient=recipient,
        evidence_ids=("ev-site",),
        case_claim_ids=("auto_crm.experience:v1",),
        created_by="u-operator",
        created_at=NOW,
    )


V1 = version()
APPROVAL = Approval.grant(id="a1", version=V1, actor_id="u-operator", now=NOW)


# --- Payload hash ---------------------------------------------------------------------------


def test_hash_covers_every_part_of_the_payload():
    base = payload_hash("msg1", 1, IVAN, BODY)
    changed = [
        payload_hash("msg2", 1, IVAN, BODY),
        payload_hash("msg1", 2, IVAN, BODY),
        payload_hash("msg1", 1, replace(IVAN, contact_id="k2"), BODY),
        payload_hash("msg1", 1, replace(IVAN, channel=Channel.TELEGRAM), BODY),
        payload_hash("msg1", 1, replace(IVAN, normalized_address="ivan@alpha.ru"), BODY),
        payload_hash("msg1", 1, IVAN, BODY + " "),
        payload_hash("msg1", 1, IVAN, BODY.replace("ё", "е")),
    ]
    assert base not in changed and len(set(changed)) == len(changed)


def test_hash_is_stable():
    assert payload_hash("msg1", 1, IVAN, BODY) == payload_hash("msg1", 1, IVAN, BODY)
    assert len(payload_hash("msg1", 1, IVAN, BODY)) == 64


def test_version_with_a_foreign_hash_is_not_created():
    with pytest.raises(ValueError):
        replace(V1, payload_hash=payload_hash("msg1", 1, IVAN, "другой текст"))
    with pytest.raises(ValueError):
        replace(V1, body="другой текст")  # body changed, hash kept


@pytest.mark.parametrize(
    "change",
    [
        dict(body="   "),
        dict(version=0),
        dict(case_claim_ids=("auto_crm:v1",)),
        dict(case_claim_ids=("auto_crm.experience",)),
        dict(case_claim_ids=["auto_crm.experience:v1"]),
        dict(evidence_ids=("ev-site", "ev-site")),
        dict(evidence_ids=("",)),
        dict(created_at=datetime(2026, 10, 18, 10, 0)),
        dict(created_by=" "),
    ],
    ids=[
        "empty-body", "version", "old-claim-id", "claim-without-version", "claim-list",
        "repeated-evidence", "blank-evidence", "naive-time", "no-author",
    ],
)  # fmt: skip
def test_invalid_version_is_not_created(change):
    fields = dict(
        message_id="msg1",
        version=1,
        body=BODY,
        recipient=IVAN,
        evidence_ids=("ev-site",),
        case_claim_ids=("auto_crm.experience:v1",),
        created_by="u-operator",
        created_at=NOW,
    )
    fields.update(change)
    with pytest.raises(ValueError):
        MessageVersion.create(**fields)


def test_version_is_immutable():
    with pytest.raises(AttributeError):
        V1.body = "правка"


@pytest.mark.parametrize(
    "change",
    [
        dict(contact_id=" "),
        dict(channel="email"),
        dict(normalized_address=" ivan@alpha.ru"),
        dict(normalized_address=""),
    ],
    ids=["no-contact", "string-channel", "untrimmed", "empty"],
)
def test_invalid_recipient_is_not_created(change):
    with pytest.raises(ValueError):
        replace(IVAN, **change)


# --- Approval (T-11) ----------------------------------------------------------------------


def test_approval_is_built_from_the_stored_version():
    assert APPROVAL.payload_hash == V1.payload_hash
    assert APPROVAL.message_version == 1
    assert APPROVAL.expires_at - APPROVAL.approved_at == APPROVAL_TTL == timedelta(hours=24)
    assert APPROVAL.valid_for(V1, NOW)


def test_t11_edited_text_invalidates_the_approval():
    v2 = version(2, body=BODY + "\n\nМожем разобрать процесс.")
    assert not APPROVAL.valid_for(v2, NOW)


def test_changed_recipient_invalidates_the_approval_even_with_the_same_text():
    # Doc 10: a contact change annuls the approval although the text did not change.
    v2 = version(2, recipient=replace(IVAN, normalized_address="i.petrov@alpha.ru"))
    assert v2.body == V1.body
    assert not APPROVAL.valid_for(v2, NOW)


def test_approval_expires_after_24_hours():
    assert APPROVAL.valid_for(V1, NOW + timedelta(hours=23, minutes=59))
    assert not APPROVAL.valid_for(V1, NOW + timedelta(hours=24))


def test_revoked_approval_is_not_valid():
    revoked = replace(APPROVAL, revoked_at=NOW + timedelta(minutes=5))
    assert not revoked.valid_for(V1, NOW + timedelta(minutes=10))


def test_approval_of_another_message_is_not_valid():
    other = MessageVersion.create(
        message_id="msg2",
        version=1,
        body=BODY,
        recipient=IVAN,
        evidence_ids=(),
        case_claim_ids=(),
        created_by="u-operator",
        created_at=NOW,
    )
    assert not APPROVAL.valid_for(other, NOW)


@pytest.mark.parametrize(
    "change",
    [
        dict(payload_hash="client-sent-hash"),
        dict(expires_at=NOW),
        dict(revoked_at=NOW - timedelta(seconds=1)),
        dict(approved_at=datetime(2026, 10, 18, 10, 0)),
        dict(actor_id=" "),
        dict(message_version=0),
    ],
    ids=["not-a-hash", "no-lifetime", "revoked-before", "naive", "no-actor", "version"],
)
def test_invalid_approval_is_not_created(change):
    with pytest.raises(ValueError):
        replace(APPROVAL, **change)


# --- Status transitions -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (MessageStatus.DRAFT, MessageStatus.PENDING_REVIEW),
        (MessageStatus.PENDING_REVIEW, MessageStatus.APPROVED),
        (MessageStatus.PENDING_REVIEW, MessageStatus.REJECTED),
        (MessageStatus.PENDING_REVIEW, MessageStatus.DRAFT),
        (MessageStatus.APPROVED, MessageStatus.SENT),
        (MessageStatus.APPROVED, MessageStatus.DRAFT),
        (MessageStatus.APPROVED, MessageStatus.CANCELLED),
        (MessageStatus.DRAFT, MessageStatus.CANCELLED),
    ],
    ids=[
        "submit", "approve", "reject", "edit-in-review", "manual-send", "edit-after-approve",
        "reply-or-ban", "cancel-draft",
    ],
)  # fmt: skip
def test_allowed_transitions(current, target):
    check_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (MessageStatus.DRAFT, MessageStatus.APPROVED),
        (MessageStatus.DRAFT, MessageStatus.SENT),
        (MessageStatus.PENDING_REVIEW, MessageStatus.SENT),
        (MessageStatus.SENT, MessageStatus.CANCELLED),
        (MessageStatus.SENT, MessageStatus.DRAFT),
        (MessageStatus.REJECTED, MessageStatus.PENDING_REVIEW),
        (MessageStatus.CANCELLED, MessageStatus.DRAFT),
    ],
    ids=[
        "approve-a-draft", "send-a-draft", "send-unapproved", "undo-a-send", "edit-a-send",
        "revive-rejected", "revive-cancelled",
    ],
)  # fmt: skip
def test_forbidden_transitions(current, target):
    with pytest.raises(InvalidTransitionError):
        check_transition(current, target)


def test_final_statuses_lead_nowhere():
    assert all(TRANSITIONS[status] == frozenset() for status in FINAL_STATUSES)
    assert set(TRANSITIONS) == set(MessageStatus)


# --- Message --------------------------------------------------------------------------------

DRAFT = Message(
    id="msg1",
    lead_id="l1",
    kind=MessageKind.INITIAL,
    sequence_step=0,
    status=MessageStatus.DRAFT,
    current_version=1,
)


def test_approved_and_sent_messages_carry_the_approved_version():
    approved = replace(DRAFT, status=MessageStatus.APPROVED, approved_version=1)
    sent = replace(approved, status=MessageStatus.SENT, sent_at=NOW)
    assert sent.approved_version == 1


@pytest.mark.parametrize(
    "change",
    [
        dict(status=MessageStatus.APPROVED),
        dict(approved_version=1),
        dict(status=MessageStatus.APPROVED, approved_version=1, current_version=2),
        dict(status=MessageStatus.SENT, approved_version=1),
        dict(sent_at=NOW),
        dict(sequence_step=1),
        dict(kind=MessageKind.FOLLOW_UP),
        dict(sequence_step=-1, kind=MessageKind.RESPONSE),
        dict(kind="initial"),
        dict(lead_id=" "),
    ],
    ids=[
        "approved-without-version", "draft-with-approved-version", "approved-old-version",
        "sent-without-time", "draft-with-sent-time", "initial-not-step-0", "follow-up-step-0",
        "negative-step", "string-kind", "no-lead",
    ],
)  # fmt: skip
def test_invalid_message_is_not_created(change):
    with pytest.raises(ValueError):
        replace(DRAFT, **change)


def test_follow_up_and_response_steps():
    assert replace(DRAFT, kind=MessageKind.FOLLOW_UP, sequence_step=2).sequence_step == 2
    assert replace(DRAFT, kind=MessageKind.RESPONSE, sequence_step=0).kind is MessageKind.RESPONSE
