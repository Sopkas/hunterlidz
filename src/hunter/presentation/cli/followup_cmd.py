"""`hunter tick`, `hunter followup`, `hunter task`. (H-10)

`tick` is meant for cron / Windows Task Scheduler every minute (doc 12); it never sends.
"""

from __future__ import annotations

import argparse

from hunter.application import followups
from hunter.application.access import ensure_worker
from hunter.application.errors import ErrorCode, HunterError
from hunter.presentation.cli.outreach_cmd import aware


def register(subparsers: argparse._SubParsersAction) -> None:
    tick = subparsers.add_parser("tick", help="планировщик: разобрать наступившие задачи")
    tick.set_defaults(handler=handle_tick)

    follow = subparsers.add_parser("followup", help="черновик повторного касания по задаче")
    follow.add_argument("task_id")
    follow.set_defaults(handler=handle_followup)

    task = subparsers.add_parser("task", help="закрыть задачу с исходом")
    task.add_argument("task_id")
    task.add_argument(
        "--outcome", choices=["done", "relevant", "not_relevant", "later"], required=True
    )
    task.add_argument("--return-at", type=aware, help="для later: новая дата возврата")
    task.add_argument("--note", default="")
    task.set_defaults(handler=handle_task)


def resolve_task_id(app, prefix: str) -> str:
    ids = [t.id for t in app.store.open_tasks() if t.id.startswith(prefix.strip())]
    if len(ids) != 1:
        raise HunterError(ErrorCode.NOT_FOUND, f"открытая задача {prefix!r} не найдена")
    return ids[0]


def handle_tick(app, args) -> int:
    report = followups.scheduler_tick(app.deps, ensure_worker(app.deps))
    print(
        f"Наступили follow-up: {report.due_follow_ups}; свёрнуто после простоя: "
        f"{report.collapsed_leads} лид(ов); возвраты из NURTURE: {report.due_returns}."
    )
    if report.due_follow_ups or report.collapsed_leads or report.due_returns:
        print("Список: hunter tasks --due")
    return 0


def handle_followup(app, args) -> int:
    result = followups.create_follow_up_draft(
        app.deps,
        app.actor(),
        resolve_task_id(app, args.task_id),
        app.llm(),
        app.knowledge,
        app.research_config(),
        today=app.business_today(),
    )
    detail = f" — {result.detail}" if result.detail else ""
    mid = result.message_id[:8] if result.message_id else "—"
    print(f"Follow-up: {result.status}{detail} (сообщение {mid}). Дальше: hunter review")
    return 0


def handle_task(app, args) -> int:
    followups.complete_task(
        app.deps,
        app.actor(),
        resolve_task_id(app, args.task_id),
        outcome=args.outcome,
        note=args.note,
        return_at=args.return_at,
    )
    print("Задача закрыта.")
    return 0
