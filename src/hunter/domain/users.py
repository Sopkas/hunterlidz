"""User entity. (H-02)"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hunter.domain.roles import Role

_NAME = re.compile(r"^[\w.\-]{2,64}$")


@dataclass(frozen=True, slots=True)
class User:
    id: str
    name: str
    role: Role
    active: bool

    def __post_init__(self) -> None:
        if not _NAME.match(self.name):
            raise ValueError("user name: 2-64 letters, digits, '.', '_' or '-'")
        if not isinstance(self.role, Role):
            raise ValueError("role must be a Role")
