"""`hunter sent`, `reply`, `replies`, `reply-review`, `suppress`, `suppressions`, `unsuppress`,
`tasks`. (H-09)

"Sent manually" is recorded only after the operator really sent the message in their channel
and confirmed the exact text, recipient and time (doc 12).
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from hunter.application import access, messaging, outreach
from hunter.application.errors import ErrorCode, HunterError
from hunter.domain.replies import ReplyAction, ReplyClass
from hunter.domain.roles import Permission
from hunter.presentation.cli.common import resolve_lead_id, short
from hunter.presentation.cli.review_cmd import resolve_message_id

TASK_TEXT = {
    "follow_up": "follow-up",
    "nurture_research": "вернуться: проверить актуальность",
    "answer_question": "ответить на вопрос",
    "reroute": "найти нужного человека",
    "review_reply": "разобрать ответ",
}


def aware(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("формат: 2026-09-27T12:30+03:00") from None
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("укажите часовой пояс, например 2026-09-27T12:30+03:00")
    return parsed


def register(subparsers: argparse._SubParsersAction) -> None:
    sent = subparsers.add_parser("sent", help="зарегистрировать ручную отправку одобренного текста")
    sent.add_argument("message_id")
    sent.add_argument("--version", type=int, required=True)
    sent.add_argument("--sent-at", type=aware, required=True, help="когда отправили, с поясом")
    sent.add_argument("--key", help="ключ идемпотентности (по умолчанию — от согласования)")
    sent.add_argument("--yes", action="store_true", help="не спрашивать подтверждение")
    sent.set_defaults(handler=handle_sent)

    reply = subparsers.add_parser("reply", help="записать ответ клиента (цепочка встанет на паузу)")
    reply.add_argument("lead_id")
    reply.add_argument("--file", type=Path, required=True, help="текст ответа")
    reply.add_argument("--received-at", type=aware, required=True)
    reply.add_argument("--contact", help="ID контакта, если известен")
    reply.add_argument("--event-id", help="внешний ID сообщения в канале")
    reply.set_defaults(handler=handle_reply)

    replies = subparsers.add_parser("replies", help="ответы, ожидающие разбора")
    replies.set_defaults(handler=handle_replies)

    review = subparsers.add_parser("reply-review", help="подтвердить класс ответа и действие")
    review.add_argument("inbound_id")
    review.add_argument(
        "--class", dest="reply_class", choices=[c.value for c in ReplyClass], required=True
    )
    review.add_argument("--action", choices=[a.value for a in ReplyAction], required=True)
    review.add_argument("--return-at", type=aware, help="для nurture: дата возврата с поясом")
    review.add_argument("--scope", choices=["company", "contact"], default="company")
    review.add_argument("--note", default="")
    review.set_defaults(handler=handle_reply_review)

    ban = subparsers.add_parser("suppress", help="запрет контакта (компания или контакт)")
    target = ban.add_mutually_exclusive_group(required=True)
    target.add_argument("--lead", help="запрет на всю компанию лида")
    target.add_argument("--contact", help="запрет на один контакт")
    ban.add_argument("--reason", required=True)
    ban.set_defaults(handler=handle_suppress)

    bans = subparsers.add_parser("suppressions", help="действующие запреты")
    bans.set_defaults(handler=handle_suppressions)

    lift = subparsers.add_parser("unsuppress", help="снять запрет (только admin, с основанием)")
    lift.add_argument("suppression_id")
    lift.add_argument("--reason", required=True)
    lift.set_defaults(handler=handle_unsuppress)

    tasks = subparsers.add_parser("tasks", help="открытые задачи (следующие действия)")
    tasks.add_argument("--due", action="store_true", help="только наступившие")
    tasks.set_defaults(handler=handle_tasks)


def handle_sent(app, args) -> int:
    actor = app.actor()
    message_id = resolve_message_id(app, args.message_id)
    message, version = messaging.current_version(app.deps, message_id)
    approval = app.store.live_approval(message_id)
    if approval is None:
        raise HunterError(ErrorCode.VERSION_CONFLICT, "у сообщения нет действующего согласования")
    print(f"Адресат: {version.recipient.channel} {version.recipient.normalized_address}")
    print(f"Время отправки: {args.sent_at.isoformat()}")
    print(f"Текст v{version.version}:\n{version.body}\n")
    if not args.yes:
        answer = input("Вы отправили именно этот текст этому адресату? (да/нет): ").strip().lower()
        if answer not in ("да", "yes", "y", "д"):
            print("Отправка не зарегистрирована.")
            return 1
    receipt = outreach.manual_send(
        app.deps,
        actor,
        message_id,
        expected_version=args.version,
        approval_id=approval.id,
        sent_at=args.sent_at,
        idempotency_key=args.key or f"manual-send:{approval.id}",
    )
    again = " (повтор — событие уже было записано)" if receipt.replayed else ""
    print(f"Отправка зарегистрирована{again}. Задач follow-up: {receipt.tasks_created}.")
    return 0


def handle_reply(app, args) -> int:
    lead_id = resolve_lead_id(app, args.lead_id)
    receipt = outreach.record_reply(
        app.deps,
        app.actor(),
        lead_id,
        args.file.read_text(encoding="utf-8-sig"),
        args.received_at,
        contact_id=args.contact,
        external_event_id=args.event_id,
    )
    if not receipt.created:
        print(f"Этот ответ уже записан ({receipt.inbound_id[:8]}).")
        return 0
    print(
        f"Ответ {receipt.inbound_id[:8]} записан. Цепочка на паузе: отменено задач "
        f"{receipt.tasks_cancelled}, неотправленных сообщений {receipt.messages_cancelled}."
    )
    ai, reason = app.llm_or_reason()
    if ai is None:
        print(
            f"AI-классификация пропущена ({reason}). Разберите ответ вручную: hunter reply-review"
        )
        return 0
    llm, config = ai
    proposal = outreach.classify_reply(app.deps, receipt.inbound_id, llm, app.knowledge, config)
    if proposal is None:
        print("AI не смог классифицировать ответ — разберите вручную.")
    else:
        _print_proposal(app.store.inbound(receipt.inbound_id).proposal)
    return 0


def _print_proposal(p: dict) -> None:
    print(
        f"  Предложение AI: класс {p['class']}, действие {p['suggested_action']}, "
        f"возврат {p.get('return_date') or '—'}"
    )
    if p.get("supporting_excerpt"):
        print(f"  Цитата: «{p['supporting_excerpt']}»")
    for note in p.get("notes", []):
        print(f"  ⚠ {note}")
    print("  Решение подтверждает оператор: hunter reply-review <ID> --class … --action …")


def handle_replies(app, args) -> int:
    access.require(app.actor(), Permission.READ)
    items = app.store.unreviewed_inbound()
    if not items:
        print("Неразобранных ответов нет.")
    for item in items:
        print(
            f"{item.id[:8]}  лид {item.lead_id[:8]}  {item.received_at:%Y-%m-%d %H:%M} UTC  "
            f"v{item.version}: {short(item.body, 70)}"
        )
        if item.proposal:
            _print_proposal(item.proposal)
    return 0


def resolve_inbound_id(app, prefix: str) -> str:
    ids = app.store.inbound_ids_with_prefix(prefix.strip())
    if len(ids) != 1:
        raise HunterError(ErrorCode.NOT_FOUND, f"ответ {prefix!r} не найден или неоднозначен")
    return ids[0]


def handle_reply_review(app, args) -> int:
    inbound_id = resolve_inbound_id(app, args.inbound_id)
    inbound = app.store.inbound(inbound_id)
    outreach.review_reply(
        app.deps,
        app.actor(),
        inbound_id,
        expected_version=inbound.version,
        reply_class=ReplyClass(args.reply_class),
        action=ReplyAction(args.action),
        return_at=args.return_at,
        ban_scope=args.scope,
        note=args.note,
    )
    print("Решение записано.")
    return 0


def handle_suppress(app, args) -> int:
    if args.lead:
        lead = app.store.lead(resolve_lead_id(app, args.lead))
        scope_type, scope_id = "company", lead.company_id
    else:
        scope_type, scope_id = "contact", args.contact
    suppression_id = outreach.suppress(app.deps, app.actor(), scope_type, scope_id, args.reason)
    print(f"Запрет {suppression_id[:8]} создан; задачи и неотправленные сообщения отменены.")
    return 0


def handle_suppressions(app, args) -> int:
    access.require(app.actor(), Permission.READ)
    rows = app.store.active_suppressions()
    if not rows:
        print("Действующих запретов нет.")
    for row in rows:
        match = row["match_domain"] or row["match_address"] or ""
        print(f"  {row['id'][:8]}  {row['scope_type']:<8} {match:<32} {short(row['reason'], 40)}")
    return 0


def handle_unsuppress(app, args) -> int:
    matches = [
        r["id"] for r in app.store.active_suppressions() if r["id"].startswith(args.suppression_id)
    ]
    if len(matches) != 1:
        raise HunterError(ErrorCode.NOT_FOUND, "активный запрет не найден или ID неоднозначен")
    outreach.lift_suppression(app.deps, app.actor(), matches[0], args.reason)
    print("Запрет снят.")
    return 0


def handle_tasks(app, args) -> int:
    access.require(app.actor(), Permission.READ)
    due = app.deps.clock.now() if args.due else None
    tasks = app.store.open_tasks(due_before=due)
    if not tasks:
        print("Открытых задач нет.")
    for task in tasks:
        label = TASK_TEXT.get(task.type, task.type)
        step = f" D-шаг {task.sequence_step}" if task.type == "follow_up" else ""
        print(
            f"  {task.id[:8]}  {task.due_at:%Y-%m-%d %H:%M} UTC  лид {task.lead_id[:8]}  "
            f"{label}{step}"
        )
    return 0
