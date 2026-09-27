"""Export (FR-15) and pilot metrics (doc 07). (H-11)

Export carries stable ids and versions. Contact addresses are exported only on request.
For spreadsheet output every text cell that could be read as a formula is prefixed with an
apostrophe, so nothing from a lead or a model is ever evaluated when the file is opened
("Как мы работаем": text in output spreadsheets is written as text). JSON keeps raw values.
"""

from __future__ import annotations

from datetime import timedelta

from hunter.application.access import require
from hunter.application.context import Deps
from hunter.domain.metrics import REPLY_WINDOW_DAYS, PilotMetrics
from hunter.domain.roles import Permission
from hunter.domain.users import User

EXPORT_FIELDS = (
    "lead_id",
    "lead_version",
    "company_id",
    "company_name",
    "company_domain",
    "campaign",
    "stage",
    "priority",
    "icp",
    "product",
    "score_total",
    "rules_version",
    "prescore_total",
    "prescore_version",
    "loss_reason",
    "contact_banned",
    "message_id",
    "message_status",
    "message_version",
    "sent_at",
    "message_body",
)
CONTACT_FIELDS = ("recipient_channel", "recipient_address")
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: object) -> object:
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


def spreadsheet_safe(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [{k: safe_cell(v) for k, v in row.items()} for row in rows]


def export_rows(deps: Deps, actor: User, *, with_contacts: bool) -> list[dict[str, object]]:
    require(actor, Permission.EXPORT)
    fields = EXPORT_FIELDS + (CONTACT_FIELDS if with_contacts else ())
    rows = []
    for source in deps.store.export_rows():
        row = {f: source[f] for f in fields}
        row["contact_banned"] = bool(source["contact_banned"])
        if with_contacts and row["contact_banned"]:
            row["recipient_address"] = None  # a banned address is never exported
        rows.append(row)
    deps.audit(
        actor,
        "export",
        "leads",
        "export",
        reason=f"rows {len(rows)}; contacts {'yes' if with_contacts else 'no'}",
    )
    return rows


def pilot_metrics(deps: Deps, actor: User, currency: str) -> PilotMetrics:
    require(actor, Permission.READ)
    window_start = deps.clock.now() - timedelta(days=REPLY_WINDOW_DAYS)
    counts = deps.store.metric_counts(window_start)
    return PilotMetrics(ai_cost=deps.store.ai_cost(currency), cost_currency=currency, **counts)
