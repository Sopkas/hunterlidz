"""`hunter research` — queue the best leads and run the research pipeline. (H-05)"""

from __future__ import annotations

import argparse
from collections import Counter

from hunter.application.research import enqueue_research, run_research_queue

STATUS_TEXT = {
    "completed": "готово",
    "needs_review": "готово, нужна проверка",
    "insufficient_evidence": "недостаточно данных",
    "budget_exceeded": "остановлено: исчерпан бюджет",
    "failed": "ошибка",
    "cancelled": "отменено",
    "lease_lost": "перехвачено другим обработчиком",
}


def register(subparsers: argparse._SubParsersAction) -> None:
    research = subparsers.add_parser("research", help="AI-исследование лучших лидов")
    research.add_argument("--limit", type=int, default=20, help="сколько лидов поставить в очередь")
    research.add_argument(
        "--min-prescore", type=int, default=60, help="порог предварительного score"
    )
    research.add_argument("--max-jobs", type=int, default=None, help="сколько заданий обработать")
    research.set_defaults(handler=handle_research)


def handle_research(app, args) -> int:
    llm, config = app.llm(), app.research_config()  # configuration errors exit 2 before any call
    queued = enqueue_research(
        app.deps, app.actor(), limit=args.limit, min_prescore=args.min_prescore
    )
    print(f"В очередь поставлено: {len(queued)} (модель {llm.model}).")
    outcomes = run_research_queue(
        app.deps, llm, app.knowledge, config, max_jobs=args.max_jobs or max(len(queued), 1)
    )
    for outcome in outcomes:
        detail = f" ({outcome.error_code})" if outcome.error_code else ""
        print(f"  {outcome.lead_id[:8]}  {STATUS_TEXT.get(outcome.status, outcome.status)}{detail}")
    summary = Counter(o.status for o in outcomes)
    print("Итого: " + ", ".join(f"{STATUS_TEXT.get(k, k)} — {v}" for k, v in summary.items()))
    return 0
