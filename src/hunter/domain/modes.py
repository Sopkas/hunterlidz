"""Run modes from doc 12 "Режимы работы". (H-02)

assisted_send is part of the spec but has no adapter in v0, so it is not selectable.
"""

from __future__ import annotations

from enum import StrEnum


class RunMode(StrEnum):
    SHADOW = "shadow"
    MANUAL = "manual"
    PAUSED = "paused"


class Operation(StrEnum):
    IMPORT = "import"
    RESEARCH = "research"
    DRAFT = "draft"
    APPROVE = "approve"
    MANUAL_SEND = "manual_send"
    RECORD_REPLY = "record_reply"
    SUPPRESS = "suppress"


_ALLOWED: dict[RunMode, frozenset[Operation]] = {
    RunMode.SHADOW: frozenset(Operation) - {Operation.MANUAL_SEND},
    RunMode.MANUAL: frozenset(Operation),
    # Paused: reading, inbound replies and contact bans only; nothing new starts.
    RunMode.PAUSED: frozenset({Operation.RECORD_REPLY, Operation.SUPPRESS}),
}


def allows(mode: RunMode, operation: Operation) -> bool:
    return operation in _ALLOWED[mode]
