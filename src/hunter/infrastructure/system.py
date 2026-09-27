"""System clock and UUID generator. (H-02)"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class UuidGenerator:
    def new_id(self) -> str:
        return str(uuid.uuid4())
