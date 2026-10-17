"""Run modes from doc 12 "Режимы работы". (S1-05, CONTRACT)

assisted_send is part of the spec but has no send adapter in v0, so it is not a value here;
it is added together with the adapter (H-13). Which operations each mode allows — S1-08.
"""

from __future__ import annotations

from enum import StrEnum


class RunMode(StrEnum):
    SHADOW = "shadow"
    MANUAL = "manual"
    PAUSED = "paused"
