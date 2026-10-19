"""`hunter eval-research` — run the eval set against the configured model. (S2-09)

Writes a Markdown report (docs/eval/…). The cards are synthetic: nothing about real companies or
people is sent. Exit code 0 when every card passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from hunter.application.errors import ErrorCode, HunterError
from hunter.application.eval_research import CardError, load_cards, report_markdown, run_eval
from hunter.application.prompts import RESEARCH_MAX_OUTPUT_TOKENS
from hunter.domain.cases import registry_from_mapping

DEFAULT_CARDS = "data/eval/research_cards.json"
REGISTRY_FILE = "data/case_claims.json"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("eval-research", help="прогнать eval-набор research на модели")
    parser.add_argument("--cards", default=DEFAULT_CARDS, help="файл карточек")
    parser.add_argument("--out", default=None, help="файл отчёта (по умолчанию docs/eval/…)")
    parser.add_argument("--only", default=None, help="ID карточек через запятую")
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=RESEARCH_MAX_OUTPUT_TOKENS,
        help="лимит ответа модели; думающим моделям мало 3000",
    )
    parser.set_defaults(handler=handle)


def handle(app, args: argparse.Namespace) -> int:
    if app.llm is None:
        raise HunterError(
            ErrorCode.ADAPTER_DISABLED, "модель не подключена: задайте HUNTER_LLM_MODEL"
        )
    try:
        cards = load_cards(json.loads(Path(args.cards).read_text("utf-8")))
        registry = registry_from_mapping(json.loads(Path(REGISTRY_FILE).read_text("utf-8")))
    except (OSError, ValueError, CardError) as error:
        raise HunterError(ErrorCode.INVALID_INPUT, f"не прочитать eval-набор: {error}") from None
    if args.only:
        wanted = {cid.strip() for cid in args.only.split(",") if cid.strip()}
        cards = tuple(card for card in cards if card.card_id in wanted)
    today = date.today()

    def progress(done: int, total: int, result) -> None:
        verdict = "ok" if result.passed else "не прошла"
        print(f"[{done}/{total}] {result.card_id}: {verdict}", flush=True)

    if not 200 <= args.max_output_tokens <= 32000:
        raise HunterError(ErrorCode.INVALID_INPUT, "--max-output-tokens: от 200 до 32000")
    report = run_eval(
        cards,
        app.llm,
        registry,
        today,
        on_result=progress,
        max_output_tokens=args.max_output_tokens,
    )
    out = Path(args.out or f"docs/eval/research-{today.isoformat()}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report_markdown(report), encoding="utf-8")
    passed = sum(1 for result in report.results if result.passed)
    print(f"Прогон: {passed} из {len(report.results)} карточек прошли все проверки. Отчёт: {out}")
    return 0 if passed == len(report.results) else 1
