"""`hunter draft`, `hunter review`, `hunter message …` — operator panel in the terminal. (H-07)

The card follows doc 12 "Карточка для оператора" plus "what we sell and why". Approve never
reports "sent": sending is a separate manual action (H-09).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from hunter.application import access, messaging
from hunter.application.errors import ErrorCode, HunterError
from hunter.application.scoring import assess, message_queue
from hunter.domain.messages import MessageStatus, SkipReason
from hunter.domain.roles import Permission
from hunter.presentation.cli.common import resolve_lead_id, short
from hunter.presentation.cli.sources_cmd import handle_card

SKIP_TEXT = {
    SkipReason.WEAK_FIT: "слабый fit",
    SkipReason.NO_DATA: "нет данных",
    SkipReason.WRONG_CONTACT: "неверный контакт",
    SkipReason.STALE_SIGNAL: "неактуальный сигнал",
    SkipReason.OTHER: "другое",
}
PRODUCT_TEXT = {
    "mvp_launch": "MVP Launch",
    "business_automation": "Business Automation",
    "external_product_team": "External Product Team / white-label",
}


def register(subparsers: argparse._SubParsersAction) -> None:
    draft = subparsers.add_parser("draft", help="подготовить черновики первого сообщения")
    draft.add_argument("lead_id", nargs="?", help="ID лида; без него — вся очередь")
    draft.add_argument("--limit", type=int, default=10)
    draft.set_defaults(handler=handle_draft)

    review = subparsers.add_parser("review", help="проверка черновиков: Approve / Edit / Skip")
    review.set_defaults(handler=handle_review)

    message = subparsers.add_parser("message", help="действия с конкретным сообщением")
    sub = message.add_subparsers(dest="message_command", required=True, metavar="действие")
    show = sub.add_parser("show", help="показать карточку сообщения")
    show.add_argument("message_id")
    show.set_defaults(handler=handle_show)
    for name, helptext in (("approve", "одобрить версию"), ("skip", "отклонить версию")):
        cmd = sub.add_parser(name, help=helptext)
        cmd.add_argument("message_id")
        cmd.add_argument("--version", type=int, required=True)
        if name == "skip":
            cmd.add_argument("--reason", choices=[r.value for r in SkipReason], required=True)
            cmd.add_argument("--note", default="")
        cmd.set_defaults(handler=handle_approve if name == "approve" else handle_skip)
    edit = sub.add_parser("edit", help="новая версия текста из файла")
    edit.add_argument("message_id")
    edit.add_argument("--version", type=int, required=True)
    edit.add_argument("--file", type=Path, required=True)
    edit.set_defaults(handler=handle_edit)


def resolve_message_id(app, prefix: str) -> str:
    ids = app.store.message_ids_with_prefix(prefix.strip())
    if len(ids) != 1:
        problem = "не найдено" if not ids else "неоднозначно"
        raise HunterError(ErrorCode.NOT_FOUND, f"сообщение {prefix!r}: {problem}")
    return ids[0]


def handle_draft(app, args) -> int:
    actor = app.actor()
    llm, config = app.llm(), app.research_config()
    if args.lead_id:
        lead_ids = [resolve_lead_id(app, args.lead_id)]
    else:
        lead_ids = [v.lead.id for v in message_queue(app.deps, args.limit)]
    if not lead_ids:
        print("Нет лидов, готовых к сообщению (hunter queue).")
    for lead_id in lead_ids:
        result = messaging.create_initial_draft(
            app.deps, actor, lead_id, llm, app.knowledge, config, today=app.business_today()
        )
        detail = f" — {result.detail}" if result.detail else ""
        mid = result.message_id[:8] if result.message_id else "—"
        print(f"  лид {lead_id[:8]}: {result.status}{detail} (сообщение {mid})")
    return 0


def render(app, message_id: str) -> str:
    store = app.store
    message, version = messaging.current_version(app.deps, message_id)
    view = assess(app.deps, message.lead_id)
    lead, research, score = view.lead, view.research, view.score
    company = store.company(lead.company_id)
    contact = store.contact(version.recipient.contact_id)
    signal = research.signals[0] if research and research.signals else None
    sources = store.sources_for(lead.company_id)
    claims = {c.id: c for c in store.case_claims(active_only=False)}
    claim_text = "; ".join(
        f"{cid} [{claims[cid].status}]" for cid in version.case_claim_ids if cid in claims
    )
    lines = [
        "=" * 76,
        f"Компания: {company['name'] or '—'}   (лид {lead.id[:8]}, сообщение {message.id[:8]})",
        f"ICP: {lead.icp} · Score: {score.total if score else '—'}/100 · "
        f"{score.priority if score else '—'}",
        f"Сигнал: {signal.text if signal else '—'}",
        f"Дата события: {signal.event_date if signal and signal.event_date else 'неизвестна'}",
        f"Источник: {sources[0].canonical_url if sources else '—'}",
        "",
        f"Гипотеза: {research.problem_hypothesis if research else '—'}",
        f"Оффер: {PRODUCT_TEXT.get(lead.product or '', lead.product or '—')}",
        f"Что продаём: {research.what_we_sell if research else '—'}",
        f"Почему: {research.why if research else '—'}",
        f"Кейс: {claim_text or '—'}",
        f"Контакт: {(contact.role or 'контакт') if contact else '—'} · "
        f"{version.recipient.channel} {version.recipient.normalized_address}",
        "",
        f"Черновик v{version.version} ({len(version.body)} симв., статус {message.status}):",
        version.body or "(текста нет — модель запросила проверку; допишите через Edit)",
    ]
    lines += [f"⚠ {r}" for r in version.review_reasons]
    lines += [f"⛔ {b}" for b in version.blockers]
    live = store.live_approval(message.id)
    if message.status is MessageStatus.APPROVED and live:
        lines.append(f"Одобрено до {live.expires_at:%Y-%m-%d %H:%M} UTC. Отправка — вручную.")
    lines.append("=" * 76)
    return "\n".join(lines)


def read_multiline() -> str:
    print("Новый текст. Закончите строкой из одной точки «.»")
    lines = []
    while True:
        line = input()
        if line.strip() == ".":
            return "\n".join(lines).strip()
        lines.append(line)


def ask_skip_reason() -> tuple[SkipReason, str]:
    options = list(SkipReason)
    for i, reason in enumerate(options, start=1):
        print(f"  {i}. {SKIP_TEXT[reason]}")
    choice = input("Причина (номер): ").strip()
    valid = choice.isdigit() and 0 < int(choice) <= len(options)
    reason = options[int(choice) - 1] if valid else SkipReason.OTHER
    note = input("Комментарий (можно пусто): ").strip()
    return reason, note


def handle_review(app, args) -> int:
    actor = app.actor()
    access.require(actor, Permission.APPROVE)
    queue = app.store.review_queue()
    if not queue:
        print("Проверять нечего. Сначала: hunter research → hunter draft.")
        return 0
    for n, queued in enumerate(queue, start=1):
        while True:
            message, version = messaging.current_version(app.deps, queued.id)
            if message.status not in (MessageStatus.PENDING_REVIEW, MessageStatus.DRAFT):
                break
            print(render(app, message.id))
            print(
                f"[{n}/{len(queue)}]  a — Approve · e — Edit · s — Skip · c — карточка · n — позже"
            )
            print("q — выход")
            command = input("> ").strip().lower()
            try:
                if command == "a":
                    messaging.approve(app.deps, actor, message.id, version.version)
                    print(
                        f"✔ Одобрена версия v{version.version}. Это не отправка: отправьте "
                        "вручную и сверьте последнюю переписку в канале."
                    )
                    break
                if command == "e":
                    body = read_multiline()
                    if body:
                        new = messaging.edit(app.deps, actor, message.id, version.version, body)
                        print(f"Сохранено как v{new}. Прежнее согласование отозвано.")
                    continue
                if command == "s":
                    reason, note = ask_skip_reason()
                    messaging.skip(app.deps, actor, message.id, version.version, reason, note)
                    print(f"✖ Пропущено: {SKIP_TEXT[reason]}")
                    break
                if command == "c":
                    handle_card(app, argparse.Namespace(lead_id=message.lead_id))
                    continue
                if command == "n":
                    break
                if command == "q":
                    return 0
            except HunterError as exc:
                print(f"! {exc}")
    return 0


def handle_show(app, args) -> int:
    access.require(app.actor(), Permission.READ)
    print(render(app, resolve_message_id(app, args.message_id)))
    return 0


def handle_approve(app, args) -> int:
    approval = messaging.approve(
        app.deps, app.actor(), resolve_message_id(app, args.message_id), args.version
    )
    print(f"Одобрено (approval {approval.id[:8]}, до {approval.expires_at:%Y-%m-%d %H:%M} UTC).")
    print("Это не отправка: отправьте вручную и зарегистрируйте отправку.")
    return 0


def handle_edit(app, args) -> int:
    body = args.file.read_text(encoding="utf-8-sig")
    new = messaging.edit(
        app.deps, app.actor(), resolve_message_id(app, args.message_id), args.version, body
    )
    print(f"Сохранено как v{new}; нужен повторный Approve.")
    return 0


def handle_skip(app, args) -> int:
    messaging.skip(
        app.deps,
        app.actor(),
        resolve_message_id(app, args.message_id),
        args.version,
        SkipReason(args.reason),
        args.note,
    )
    print(f"Пропущено: {SKIP_TEXT[SkipReason(args.reason)]} — {short(args.note, 60)}")
    return 0
