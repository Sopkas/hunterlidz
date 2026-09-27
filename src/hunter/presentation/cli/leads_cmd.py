"""`hunter import`, `hunter leads`. (H-03)"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from hunter.application import access
from hunter.application.import_listings import import_listings
from hunter.domain.catalog import Priority, Stage
from hunter.domain.roles import Permission
from hunter.infrastructure.listing_files import read_listing_rows

REASON_TEXT = {
    "empty": "пусто",
    "spam": "спам",
    "suppressed": "запрет контакта",
    "not_a_buyer": "исполнитель, а не заказчик",
    "out_of_scope": "не наш профиль (SEO, SMM, тексты…)",
    "no_dev_task": "нет задачи разработки",
    "budget_too_low": "бюджет ниже 30 000 ₽",
    "stale": "старше 180 дней",
    "duplicate_text": "повтор текста под другим ID",
}


def register(subparsers: argparse._SubParsersAction) -> None:
    imp = subparsers.add_parser("import", help="загрузить объявления CSV/JSON")
    imp.add_argument("file", type=Path)
    imp.add_argument("--campaign", required=True, help="название кампании (когорты)")
    imp.add_argument("--platform", default="unknown", help="площадка, если её нет в файле")
    imp.add_argument("--today", type=date.fromisoformat, help="дата отсчёта свежести YYYY-MM-DD")
    imp.set_defaults(handler=handle_import)

    leads = subparsers.add_parser("leads", help="очередь лидов")
    leads.add_argument("--stage", choices=[s.value for s in Stage])
    leads.add_argument("--priority", choices=[p.value for p in Priority])
    leads.add_argument("--limit", type=int, default=30)
    leads.set_defaults(handler=handle_leads)


def handle_import(app, args) -> int:
    rows = read_listing_rows(args.file)
    report = import_listings(
        app.deps,
        app.actor(),
        rows,
        file_name=args.file.name,
        campaign=args.campaign,
        platform=args.platform,
        currency=app.settings.default_currency,
        today=args.today or app.business_today(),
    )
    counts = report.counts
    print(
        f"Импорт {report.import_id[:8]}: создано {counts['created']}, "
        f"совпало {counts['matched']}, отклонено {counts['rejected']}."
    )
    for reason, n in report.reasons.most_common():
        label = REASON_TEXT.get(reason, reason)
        print(f"  отклонено — {label}: {n}")
    return 0


def handle_leads(app, args) -> int:
    access.require(app.actor(), Permission.READ)
    leads = app.store.list_leads(
        stage=Stage(args.stage) if args.stage else None,
        priority=Priority(args.priority) if args.priority else None,
        limit=args.limit,
    )
    if not leads:
        print("Лидов нет.")
        return 0
    print(f"  {'ID':<8}  {'стадия':<10} {'score':>5} {'пред.':>5}  {'ICP':<18} объявление")
    for lead in leads:
        score = "—" if lead.score_total is None else str(lead.score_total)
        title = (app.store.listing_title(lead.id) or "")[:50]
        print(
            f"  {lead.id[:8]:<8}  {lead.stage:<10} {score:>5} {lead.prescore_total or 0:>5}"
            f"  {lead.icp:<18} {title}"
        )
    return 0
