"""Roles and the permission matrix. (H-01)

Source: doc 08 section 3 (roles) and doc 09 "Матрица прав".
The worker is a service identity: it may write validated research and drafts,
but it can never approve or send on behalf of a human.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    OBSERVER = "observer"
    WORKER = "worker"


class Permission(StrEnum):
    READ = "read"
    IMPORT = "import"
    WRITE_RESEARCH = "write_research"
    EDIT_LEAD = "edit_lead"
    APPROVE = "approve"
    MANUAL_SEND = "manual_send"
    RECORD_REPLY = "record_reply"
    SUPPRESS = "suppress"
    LIFT_SUPPRESSION = "lift_suppression"
    EXPORT = "export"
    MANAGE_USERS = "manage_users"
    SET_MODE = "set_mode"


_HUMAN_OPERATIONS = frozenset(
    {
        Permission.READ,
        Permission.IMPORT,
        Permission.WRITE_RESEARCH,
        Permission.EDIT_LEAD,
        Permission.APPROVE,
        Permission.MANUAL_SEND,
        Permission.RECORD_REPLY,
        Permission.SUPPRESS,
        Permission.EXPORT,
    }
)

MATRIX: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.OPERATOR: _HUMAN_OPERATIONS,
    Role.OBSERVER: frozenset({Permission.READ}),
    Role.WORKER: frozenset({Permission.READ, Permission.WRITE_RESEARCH}),
}


def can(role: Role, permission: Permission) -> bool:
    return permission in MATRIX[role]
