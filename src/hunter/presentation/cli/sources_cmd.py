"""`hunter lead`, `hunter sources fetch`, `hunter claims`. (H-04)"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hunter.application import access, research_codec
from hunter.application.case_claims import load_case_claims
from hunter.application.errors import ErrorCode, HunterError
from hunter.application.sources import fetch_company_website
from hunter.domain.roles import Permission
from hunter.presentation.cli.common import resolve_lead_id, short


def register(subparsers: argparse._SubParsersAction) -> None:
    card = subparsers.add_parser("lead", help="карточка лида: источники, evidence, контакты")
    card.add_argument("lead_id")
    card.set_defaults(handler=handle_card)

    sources = subparsers.add_parser("sources", help="источники компании")
    sources_sub = sources.add_subparsers(dest="sources_command", required=True, metavar="действие")
    fetch = sources_sub.add_parser("fetch", help="прочитать сайт компании (если известен домен)")
    fetch.add_argument("lead_id")
    fetch.set_defaults(handler=handle_fetch)

    claims = subparsers.add_parser("claims", help="реестр кейсов (doc 01)")
    claims_sub = claims.add_subparsers(dest="claims_command", required=True, metavar="действие")
    claims_sub.add_parser("list", help="активные утверждения").set_defaults(handler=handle_claims)
    load = claims_sub.add_parser("load", help="загрузить реестр из JSON (admin)")
    load.add_argument("file", type=Path)
    load.set_defaults(handler=handle_load)


def handle_card(app, args) -> int:
    access.require(app.actor(), Permission.READ)
    store = app.store
    lead = store.lead(resolve_lead_id(app, args.lead_id))
    company = store.company(lead.company_id)
    print(f"Лид {lead.id}  ·  стадия {lead.stage}  ·  ICP {lead.icp}  ·  продукт {lead.product}")
    print(f"Компания: {company['name'] or '—'}  ·  домен: {company['normalized_domain'] or '—'}")
    score = "нет (research не проводился)" if lead.score_total is None else lead.score_total
    print(f"Score (doc 04): {score}  ·  предварительный: {lead.prescore_total}")
    prescore = store.lead_prescore(lead.id) or {}
    for name, reason in prescore.get("reasons", {}).items():
        print(f"    {name:<15} {prescore['components'][name]:>3}  {reason}")
    print("Источники:")
    for s in store.sources_for(lead.company_id):
        extra = f" ({s.error_code})" if s.error_code else ""
        print(f"    [{s.source_type}] {s.fetch_status}{extra}  {s.canonical_url}")
    print("Evidence:")
    for e in store.evidence_for(lead.company_id):
        when = e.event_date.isoformat() if e.event_date else "дата неизвестна"
        print(f"    {e.id[:8]}  {e.claim} ({when}): {short(e.excerpt, 80)}")
    print("Контакты:")
    for c in store.contacts_for(lead.company_id):
        kind = "общий канал" if c.company_wide else (c.role or "контакт")
        print(f"    {c.channel:<9} {c.normalized_address}  — {kind}")
    _print_research(store, lead.id)
    return 0


def _print_research(store, lead_id: str) -> None:
    run = store.latest_run(lead_id)
    if run is None:
        print("Research: не проводился.")
        return
    print(
        f"Research: {run.status} · модель {run.model_id} · вызовов {run.llm_calls} · "
        f"токены {run.input_tokens}/{run.output_tokens} · {run.cost} {run.currency}"
        + (f" · ошибка {run.error_code}" if run.error_code else "")
    )
    if not run.result:
        return
    research = research_codec.loads(run.result)
    print(f"  Суть: {research.summary}")
    for fact in research.facts:
        refs = ", ".join(i[:8] for i in fact.evidence_ids)
        print(f"  • {fact.text}  [{refs}]")
    print(f"  Гипотеза: {research.problem_hypothesis or '—'}")
    print(f"  Вопрос: {research.validation_question or '—'}")
    print(f"  Что продаём: {research.what_we_sell or '—'}")
    print(f"  Почему: {research.why or '—'}")
    print(f"  Кейсы: {', '.join(research.relevant_case_claim_ids) or '—'}")
    if research.missing_data:
        print(f"  Не хватает: {'; '.join(research.missing_data)}")
    for problem in research.problems:
        print(f"  ⚠ {problem}")


def handle_fetch(app, args) -> int:
    report = fetch_company_website(
        app.deps, app.actor(), resolve_lead_id(app, args.lead_id), app.fetcher
    )
    if report is None:
        print("Домен компании неизвестен — читать нечего.")
    else:
        extra = f" ({report.error_code})" if report.error_code else ""
        print(f"{report.url}: {report.status}{extra}")
    return 0


def handle_claims(app, args) -> int:
    access.require(app.actor(), Permission.READ)
    claims = app.store.case_claims()
    if not claims:
        print("Реестр пуст. Администратор: hunter claims load data/case_claims.json")
    for c in claims:
        usable = "можно в сообщениях" if c.usable_in_messages else "не для сообщений"
        print(f"  {c.id:<22} [{c.status}] {usable}\n      {c.claim_text}")
    return 0


def handle_load(app, args) -> int:
    try:
        items = json.loads(args.file.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HunterError(ErrorCode.INVALID_INPUT, f"файл не читается: {type(exc).__name__}")
    if not isinstance(items, list):
        raise HunterError(ErrorCode.INVALID_INPUT, "ожидается JSON-список утверждений")
    created, unchanged = load_case_claims(app.deps, app.actor(), items)
    print(f"Реестр кейсов: новых версий {created}, без изменений {unchanged}.")
    return 0
