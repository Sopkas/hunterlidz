"""Loading the case claims registry. (H-04)

Each change of a claim's text, status or permission creates a new claim version and
deactivates the previous one, so drafts keep pointing at the exact version they used.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from hunter.application.access import require
from hunter.application.context import Deps
from hunter.application.errors import ErrorCode, HunterError
from hunter.domain.cases import CaseClaim, ClaimStatus
from hunter.domain.roles import Permission
from hunter.domain.users import User


def load_case_claims(
    deps: Deps, actor: User, items: Sequence[Mapping[str, object]]
) -> tuple[int, int]:
    """Returns (new versions, unchanged)."""
    require(actor, Permission.MANAGE_CLAIMS)
    created = unchanged = 0
    with deps.store.transaction():
        for item in items:
            try:
                key = str(item["case_key"]).strip()
                text = str(item["claim_text"]).strip()
                status = ClaimStatus(str(item["status"]))
                allowed = bool(item.get("external_allowed", False))
            except (KeyError, ValueError) as exc:
                raise HunterError(ErrorCode.INVALID_INPUT, f"кейс: неверное поле {exc}") from None
            current = deps.store.active_case_claim(key)
            same = current and (current.claim_text, current.status, current.external_allowed) == (
                text,
                status,
                allowed,
            )
            if same:
                unchanged += 1
                continue
            version = current.claim_version + 1 if current else 1
            claim = CaseClaim(
                id=f"{key}:v{version}",
                case_key=key,
                claim_text=text,
                status=status,
                external_allowed=allowed,
                claim_version=version,
                source_ref=_opt(item.get("source_ref")),
                approved_by=_opt(item.get("approved_by")),
            )
            deps.store.add_case_claim(claim, deps.clock.now())
            deps.audit(actor, "case_claim", claim.id, f"claim:{status}", new_version=version)
            created += 1
    return created, unchanged


def _opt(value: object) -> str | None:
    return str(value).strip() or None if value is not None else None
