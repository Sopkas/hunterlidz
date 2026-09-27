"""`hunter score`, `hunter allow-contact`, `hunter queue`. (H-06)"""

from __future__ import annotations

import argparse

from hunter.application import access
from hunter.application.scoring import allow_contact, assess, message_queue, rescore
from hunter.domain.roles import Permission
from hunter.presentation.cli.common import resolve_lead_id

LABELS = {
    "icp_fit": "ICP Fit",
    "signal": "Сигнал",
    "problem_fit": "Problem Fit",
    "budget": "Бюджет",
    "contactability": "Контакт",
}


def register(subparsers: argparse._SubParsersAction) -> None:
    score = subparsers.add_parser("score", help="пересчитать score лида по последнему research")
    score.add_argument("lead_id")
    score.set_defaults(handler=handle_score)

    allow = subparsers.add_parser("allow-contact", help="исключение оператора для WARM-лида")
    allow.add_argument("lead_id")
    allow.add_argument("--reason", required=True)
    allow.set_defaults(handler=handle_allow)

    queue = subparsers.add_parser("queue", help="лиды, готовые к подготовке сообщения")
    queue.add_argument("--limit", type=int, default=50)
    queue.set_defaults(handler=handle_queue)


def print_score(view) -> None:
    score = view.score
    if score is None:
        print("Score (doc 04): нет — research не завершён.")
        return
    print(f"Score (doc 04): {score.total}/100 · {score.priority} · правила {score.rules_version}")
    for name, label in LABELS.items():
        print(f"    {label:<12} {score.components[name]:>3}  {score.reasons[name]}")
    for item in score.contradictions:
        print(f"    ⚠ противоречие: {item}")
    if view.eligibility is not None:
        if view.eligibility.eligible:
            print("Очередь сообщений: готов")
        else:
            print("Очередь сообщений: нет — " + "; ".join(view.eligibility.blockers))
            if view.eligibility.exception_possible:
                print("    Оператор может разрешить: hunter allow-contact <ID> --reason …")


def handle_score(app, args) -> int:
    lead_id = resolve_lead_id(app, args.lead_id)
    rescore(app.deps, app.actor(), lead_id, app.business_today())
    print_score(assess(app.deps, lead_id))
    return 0


def handle_allow(app, args) -> int:
    lead_id = resolve_lead_id(app, args.lead_id)
    lead = app.store.lead(lead_id)
    allow_contact(app.deps, app.actor(), lead_id, lead.version, args.reason)
    print("Исключение записано.")
    print_score(assess(app.deps, lead_id))
    return 0


def handle_queue(app, args) -> int:
    access.require(app.actor(), Permission.READ)
    views = message_queue(app.deps, args.limit)
    if not views:
        print("Готовых к сообщению лидов нет.")
    for view in views:
        title = app.store.listing_title(view.lead.id) or ""
        print(f"  {view.lead.id[:8]}  {view.score.total:>3} {view.score.priority:<5} {title[:60]}")
    return 0
