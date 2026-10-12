"""User entity. (S1-04)

identity_id links a Hunter user to an external identity (doc 10, table users):
"os:<login>" for the local CLI, "telegram:<id>" later for the Telegram panel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hunter.domain.roles import Role

_NAME = re.compile(r"^[\w.\-]{2,64}$")
_IDENTITY = re.compile(r"^(os|telegram):\S{1,256}$")


@dataclass(frozen=True, slots=True)
class User:
    id: str
    identity_id: str
    name: str
    role: Role
    active: bool

    def __post_init__(self) -> None:
        if not _NAME.match(self.name):
            raise ValueError("user name: 2-64 letters, digits, '.', '_' or '-'")
        if not _IDENTITY.match(self.identity_id):
            raise ValueError("identity_id: 'os:<login>' or 'telegram:<id>'")
        if not isinstance(self.role, Role):
            raise ValueError("role must be a Role")
