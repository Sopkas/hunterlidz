"""`hunter export`, `hunter metrics`, `hunter backup`, `hunter restore`. (H-11)"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from hunter.application import access
from hunter.application.reporting import (
    CONTACT_FIELDS,
    EXPORT_FIELDS,
    export_rows,
    pilot_metrics,
    spreadsheet_safe,
)
from hunter.domain.metrics import REPLY_WINDOW_DAYS, show_rate
from hunter.domain.roles import Permission


def register(subparsers: argparse._SubParsersAction) -> None:
    export = subparsers.add_parser("export", help="выгрузка лидов и сообщений (CSV или JSON)")
    export.add_argument("file", type=Path)
    export.add_argument("--with-contacts", action="store_true", help="добавить адреса получателей")
    export.set_defaults(handler=handle_export)

    metrics = subparsers.add_parser("metrics", help="метрики пилота (doc 07)")
    metrics.set_defaults(handler=handle_metrics)

    save = subparsers.add_parser("backup", help="резервная копия базы (admin)")
    save.add_argument("file", type=Path, nargs="?")
    save.set_defaults(handler=handle_backup)

    load = subparsers.add_parser("restore", help="восстановить базу из копии (admin)")
    load.add_argument("file", type=Path)
    load.add_argument("--yes", action="store_true", help="подтверждаю замену текущей базы")
    load.set_defaults(handler=handle_restore)


def handle_export(app, args) -> int:
    rows = export_rows(app.deps, app.actor(), with_contacts=args.with_contacts)
    fields = EXPORT_FIELDS + (CONTACT_FIELDS if args.with_contacts else ())
    args.file.parent.mkdir(parents=True, exist_ok=True)
    if args.file.suffix.lower() == ".json":
        args.file.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        with args.file.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(spreadsheet_safe(rows))
    print(f"Выгружено строк: {len(rows)} → {args.file}")
    if args.with_contacts:
        print("Файл содержит контакты: храните его как данные клиентов, не в репозитории.")
    return 0


def handle_metrics(app, args) -> int:
    currency = app.settings.env.get("COST_CURRENCY", "USD").upper()
    m = pilot_metrics(app.deps, app.actor(), currency)
    cost_card = m.cost_per_accepted_card
    lines = [
        f"Найдено лидов (после дедупликации):   {m.leads_discovered}",
        f"Отклонено при импорте:                {m.rejected_at_import}",
        f"Исследовано (есть score):             {m.researched}",
        f"Допущено к outreach (research-qual.): {m.research_qualified}",
        f"Первых сообщений / follow-up:          {m.first_messages_sent} / {m.follow_ups_sent}",
        f"Адресатов первого сообщения:          {m.contacts_reached}"
        f" (из них в незакрытом {REPLY_WINDOW_DAYS}-дневном окне: {m.contacts_in_open_window})",
        f"Человеческих ответов / автоответов:   {m.replies} / {m.auto_replies}",
        f"Положительных ответов:                {m.positive_replies}",
        f"Запретов контакта:                    {m.opt_outs}",
        f"Reply rate:                           {show_rate(m.reply_rate)}",
        f"Positive reply rate:                  {show_rate(m.positive_reply_rate)}",
        f"Затраты AI:                           {m.ai_cost} {m.cost_currency}",
        "Стоимость принятой карточки:          "
        + ("N/A" if cost_card is None else f"{cost_card} {m.cost_currency}"),
    ]
    print("\n".join(lines))
    if m.contacts_in_open_window:
        print("Окно ответа не закрыто у части контактов — сравнивайте сопоставимые когорты.")
    return 0


def handle_backup(app, args) -> int:
    access.require(app.actor(), Permission.BACKUP)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = args.file or Path("backups") / f"hunter-{stamp}.db"
    app.backup(target)
    print(f"Резервная копия: {target}")
    return 0


def handle_restore(app, args) -> int:
    access.require(app.actor(), Permission.BACKUP)
    if not args.yes:
        print("Восстановление заменит текущую базу. Повторите с --yes.")
        return 1
    report = app.restore(args.file)
    print(f"Восстановлено из {args.file}. Повторно применено запретов: {report.bans_reapplied}.")
    print(
        f"Проверка целостности: {report.integrity}; ошибок внешних ключей: "
        f"{report.foreign_key_errors}"
    )
    print("Сверка количеств: " + ", ".join(f"{k} {v}" for k, v in report.counts.items()))
    return 0 if report.integrity == "ok" and not report.foreign_key_errors else 1
