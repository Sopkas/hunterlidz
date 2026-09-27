"""CRM stage transitions (doc 04 "Стадии и критерии перехода"). (H-09)

Main path NEW -> RESEARCHED -> APPROVED -> CONTACTED -> REPLIED -> QUALIFIED -> CALL ->
PROPOSAL -> NEGOTIATION -> WON / LOST, with NURTURE and returns. The operator may skip a stage
with a recorded reason (doc 04), so later commercial stages accept any earlier active stage.
WON is only reachable together with a confirmed order (not part of v0 CLI).
"""

from __future__ import annotations

from hunter.domain.catalog import Stage

S = Stage
ALLOWED: dict[Stage, frozenset[Stage]] = {
    S.NEW: frozenset({S.RESEARCHED, S.LOST, S.NURTURE}),
    S.RESEARCHED: frozenset({S.APPROVED, S.LOST, S.NURTURE, S.REPLIED}),
    S.APPROVED: frozenset({S.RESEARCHED, S.CONTACTED, S.LOST, S.NURTURE, S.REPLIED}),
    S.CONTACTED: frozenset({S.REPLIED, S.NURTURE, S.LOST}),
    S.REPLIED: frozenset({S.QUALIFIED, S.CALL, S.NURTURE, S.LOST}),
    S.QUALIFIED: frozenset({S.CALL, S.PROPOSAL, S.NURTURE, S.LOST}),
    S.CALL: frozenset({S.PROPOSAL, S.NURTURE, S.LOST}),
    S.PROPOSAL: frozenset({S.NEGOTIATION, S.WON, S.NURTURE, S.LOST}),
    S.NEGOTIATION: frozenset({S.WON, S.NURTURE, S.LOST}),
    # Return from NURTURE goes through a fresh check of relevance (doc 04).
    S.NURTURE: frozenset({S.RESEARCHED, S.REPLIED, S.LOST}),
    S.WON: frozenset(),
    S.LOST: frozenset(),
}


def can_move(current: Stage, target: Stage) -> bool:
    return target in ALLOWED[current]
