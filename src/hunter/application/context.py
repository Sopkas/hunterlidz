"""Per-invocation dependencies shared by all use cases. (H-02)"""

from __future__ import annotations

from dataclasses import dataclass

from hunter.application.ports import AuditEvent, Clock, IdGenerator, Store
from hunter.domain.users import User


@dataclass(frozen=True, slots=True)
class Deps:
    store: Store
    clock: Clock
    ids: IdGenerator
    correlation_id: str

    def audit(
        self,
        actor: User,
        entity_type: str,
        entity_id: str,
        action: str,
        *,
        previous_version: int | None = None,
        new_version: int | None = None,
        reason: str | None = None,
    ) -> None:
        self.store.add_audit(
            AuditEvent(
                entity_type=entity_type,
                entity_id=entity_id,
                action=action,
                actor_id=actor.id,
                at=self.clock.now(),
                correlation_id=self.correlation_id,
                previous_version=previous_version,
                new_version=new_version,
                reason=reason,
            )
        )
