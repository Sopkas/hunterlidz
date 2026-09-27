"""Follow-up plan from the actual send time (doc 05, doc 12). (H-09)

D4 / D9 / D25 are v0 settings counted from sent_at of the first message. A task is only a
reminder to prepare a separate draft; nothing is sent without a new approval (FR-11).
"""

from __future__ import annotations

from datetime import datetime, timedelta

FOLLOW_UP_DAYS = (4, 9, 25)


def follow_up_plan(
    sent_at: datetime, days: tuple[int, ...] = FOLLOW_UP_DAYS
) -> list[tuple[int, datetime]]:
    if sent_at.tzinfo is None:
        raise ValueError("sent_at must be timezone-aware")
    if list(days) != sorted(set(days)) or not days or days[0] < 1:
        raise ValueError("follow-up days must be increasing positive numbers")
    return [(step, sent_at + timedelta(days=d)) for step, d in enumerate(days, start=1)]
