"""Lead stage changes with doc 04 transition rules and an audit record. (H-09)"""

from __future__ import annotations

from hunter.application.context import Deps
from hunter.application.errors import ErrorCode, HunterError
from hunter.domain.catalog import Stage
from hunter.domain.stages import can_move
from hunter.domain.users import User


def move_lead(
    deps: Deps,
    actor: User,
    lead_id: str,
    target: Stage,
    *,
    only_from: frozenset[Stage] | None = None,
    reason: str | None = None,
    loss_reason: str | None = None,
) -> bool:
    """Move the lead; returns False (no change) when its stage is outside `only_from`.
    A transition doc 04 does not allow is an INVALID_TRANSITION error. Call inside a transaction."""
    lead = deps.store.lead(lead_id)
    if lead is None:
        raise HunterError(ErrorCode.NOT_FOUND, "лид не найден")
    if only_from is not None and lead.stage not in only_from:
        return False
    if lead.stage is target:
        return False
    if not can_move(lead.stage, target):
        raise HunterError(ErrorCode.INVALID_TRANSITION, f"переход {lead.stage} → {target} запрещён")
    if target is Stage.LOST and not loss_reason:
        raise HunterError(ErrorCode.INVALID_INPUT, "для LOST нужна причина")
    if not deps.store.set_lead_stage(lead_id, target.value, lead.version, deps.clock.now()):
        raise HunterError(ErrorCode.VERSION_CONFLICT, "карточка лида изменена параллельно")
    if loss_reason:
        deps.store.set_loss_reason(lead_id, loss_reason)
    deps.audit(
        actor,
        "lead",
        lead_id,
        f"stage:{lead.stage}->{target}",
        previous_version=lead.version,
        new_version=lead.version + 1,
        reason=reason or loss_reason,
    )
    return True
