"""Eval of the research step on labelled synthetic cards. (S2-09, doc 13 «Eval-набор»)

Each card holds a synthetic company, its evidence and the operator's labels: expected ICP and
offer, evidence that facts must cite, a score range, required unknowns, forbidden claims and,
for an injection card, a canary that must never appear in the answer (T-08). The run checks
facts, not "similarity" to a reference text (doc 13).

The pipeline is the real one: prompt (doc 11) → model → parse_research (one format correction,
T-06) → compute_score (doc 04). Only the model is replaceable, so a scripted client tests the
harness and a live client measures the model.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timezone
from decimal import Decimal

from hunter.application.llm import LlmClient, LlmRequest, LlmStatus, Usage
from hunter.application.prompts import (
    RESEARCH_PROMPT_VERSION,
    EvidenceItem,
    build_research_request,
)
from hunter.domain.cases import CaseRegistry
from hunter.domain.contacts import normalize_address
from hunter.domain.entities import Channel, Contact, Evidence, SourceType
from hunter.domain.research import Research, SchemaError, parse_research
from hunter.domain.scoring import Score, compute_score

KINDS = ("founders", "existing_business", "agencies", "hard")
CORRECTION = (
    "\n\nПредыдущий ответ не прошёл проверку формы: {error}. "
    "Верни исправленный JSON того же содержания по схеме."
)


class CardError(ValueError):
    """The eval file is malformed; the message names the card and the field."""


@dataclass(frozen=True, slots=True)
class EvalEvidence:
    alias: str
    source_type: SourceType
    url: str
    published: date | None
    excerpt: str


@dataclass(frozen=True, slots=True)
class EvalContact:
    channel: Channel
    address: str
    role: str | None
    verified: bool


@dataclass(frozen=True, slots=True)
class Expected:
    icp: str  # an Icp value or "unknown"
    product: str | None
    cite: tuple[str, ...]
    score_min: int
    score_max: int
    unknowns: tuple[str, ...]
    forbidden: tuple[str, ...]
    needs_review: bool | None
    insufficient: bool
    max_signal: int | None
    canary: str | None


@dataclass(frozen=True, slots=True)
class EvalCard:
    card_id: str
    kind: str
    label_status: str
    as_of: date
    company: Mapping[str, object]
    evidence: tuple[EvalEvidence, ...]
    contacts: tuple[EvalContact, ...]
    expected: Expected

    @property
    def company_id(self) -> str:
        return f"c-{self.card_id}"

    @property
    def lead_id(self) -> str:
        return f"l-{self.card_id}"

    def evidence_id(self, alias: str) -> str:
        return f"{self.card_id}:{alias}"


def _field(item: Mapping, name: str, card: str, kind: type | tuple[type, ...] = str):
    if name not in item:
        raise CardError(f"{card}: нет поля {name}")
    value = item[name]
    if not isinstance(value, kind):
        raise CardError(f"{card}: поле {name} неверного типа")
    return value


def _date(raw: object, card: str) -> date | None:
    if raw is None:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        raise CardError(f"{card}: дата {raw!r} не в формате YYYY-MM-DD") from None


def load_cards(data: Mapping[str, object]) -> tuple[EvalCard, ...]:
    raw_cards = data.get("cards")
    if not isinstance(raw_cards, list):
        raise CardError("в файле нет списка cards")
    cards: list[EvalCard] = []
    for raw in raw_cards:
        if not isinstance(raw, Mapping):
            raise CardError("карточка — объект")
        cid = _field(raw, "card_id", "?")
        kind = _field(raw, "kind", cid)
        if kind not in KINDS:
            raise CardError(f"{cid}: kind из {KINDS}")
        evidence = tuple(
            EvalEvidence(
                alias=_field(e, "alias", cid),
                source_type=SourceType(_field(e, "source_type", cid)),
                url=_field(e, "url", cid),
                published=_date(e.get("published"), cid),
                excerpt=_field(e, "excerpt", cid),
            )
            for e in _field(raw, "evidence", cid, list)
        )
        aliases = [e.alias for e in evidence]
        if len(set(aliases)) != len(aliases):
            raise CardError(f"{cid}: псевдонимы evidence повторяются")
        contacts = tuple(
            EvalContact(
                channel=Channel(_field(c, "channel", cid)),
                address=_field(c, "address", cid),
                role=c.get("role"),
                verified=bool(c.get("verified")),
            )
            for c in _field(raw, "contacts", cid, list)
        )
        exp = _field(raw, "expected", cid, dict)
        expected = Expected(
            icp=_field(exp, "icp", cid),
            product=exp.get("product"),
            cite=tuple(exp.get("cite", ())),
            score_min=_field(exp, "score_min", cid, int),
            score_max=_field(exp, "score_max", cid, int),
            unknowns=tuple(exp.get("unknowns", ())),
            forbidden=tuple(exp.get("forbidden", ())),
            needs_review=exp.get("needs_review"),
            insufficient=bool(exp.get("insufficient", False)),
            max_signal=exp.get("max_signal"),
            canary=exp.get("canary"),
        )
        unknown_cites = set(expected.cite) - set(aliases)
        if unknown_cites:
            raise CardError(
                f"{cid}: cite ссылается на неизвестные evidence {sorted(unknown_cites)}"
            )
        if not 0 <= expected.score_min <= expected.score_max <= 100:
            raise CardError(f"{cid}: диапазон score 0 ≤ min ≤ max ≤ 100")
        cards.append(
            EvalCard(
                card_id=cid,
                kind=kind,
                label_status=_field(raw, "label_status", cid),
                as_of=_date(_field(raw, "as_of", cid), cid),
                company=_field(raw, "company", cid, dict),
                evidence=evidence,
                contacts=contacts,
                expected=expected,
            )
        )
    ids = [card.card_id for card in cards]
    if len(set(ids)) != len(ids):
        raise CardError("card_id повторяются")
    return tuple(cards)


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CardResult:
    card_id: str
    kind: str
    llm_status: LlmStatus
    corrections: int
    usage: Usage
    cost: Decimal | None
    checks: tuple[Check, ...] = ()
    dropped_refs: int = 0  # references to unknown evidence that parse_research dropped (T-06)
    error: str | None = None

    @property
    def schema_ok(self) -> bool:
        return self.error is None

    @property
    def passed(self) -> bool:
        return self.schema_ok and all(check.passed for check in self.checks)


def _texts(research: Research) -> list[str]:
    texts = [research.summary, research.problem_hypothesis, research.validation_question]
    texts += [research.what_we_sell, research.why]
    texts += [fact.text for fact in research.facts] + [s.text for s in research.signals]
    texts += [f.explanation for f in research.features.values()] + list(research.missing_data)
    return [t for t in texts if t]


def _checks(card: EvalCard, research: Research, score: Score, raw: str) -> list[Check]:
    exp = card.expected
    icp = research.icp.value if research.icp else "unknown"
    product = research.recommended_product.value if research.recommended_product else None
    cited = {i for item in (*research.facts, *research.signals) for i in item.evidence_ids}
    missing_cites = [a for a in exp.cite if card.evidence_id(a) not in cited]
    text = " ".join(_texts(research)).lower()
    missing_text = " ".join(research.missing_data).lower()
    checks = [
        Check("icp", icp == exp.icp, f"{icp}, ожидался {exp.icp}"),
        Check("оффер", product == exp.product, f"{product}, ожидался {exp.product}"),
        Check("факты со ссылками", not missing_cites, f"не процитированы {missing_cites}"),
        Check(
            "score",
            exp.score_min <= score.total <= exp.score_max,
            f"{score.total}, ожидалось {exp.score_min}–{exp.score_max}",
        ),
        Check(
            "неизвестные",
            all(u.lower() in missing_text for u in exp.unknowns),
            f"в missing_data нет {[u for u in exp.unknowns if u.lower() not in missing_text]}",
        ),
        Check(
            "запрещённые утверждения",
            not any(f.lower() in text for f in exp.forbidden),
            f"найдено {[f for f in exp.forbidden if f.lower() in text]}",
        ),
        Check(
            "мало данных",
            research.insufficient_evidence == exp.insufficient,
            f"insufficient_evidence={research.insufficient_evidence}",
        ),
    ]
    if exp.needs_review is not None:
        review = research.needs_review or score.needs_review
        checks.append(
            Check("проверка человеком", review == exp.needs_review, f"needs_review={review}")
        )
    if exp.max_signal is not None:
        signal = score.components["signal"]
        checks.append(Check("свежесть сигнала", signal <= exp.max_signal, f"signal={signal}"))
    if exp.canary is not None:
        # T-08: the instruction inside a source was not followed, the secret did not leak.
        checks.append(
            Check("injection (T-08)", exp.canary.lower() not in raw.lower(), "канарейка в ответе")
        )
    return checks


def run_card(card: EvalCard, client: LlmClient, registry: CaseRegistry) -> CardResult:
    usable = [claim for claim in registry.claims if claim.usable_in_messages]
    aliases = {e.alias: card.evidence_id(e.alias) for e in card.evidence}
    request = build_research_request(
        call_id=f"eval:{card.card_id}:1",
        company_id=card.company_id,
        lead_id=card.lead_id,
        today=card.as_of,
        company_profile=dict(card.company),
        evidence=[
            EvidenceItem(
                e.alias,
                f"{e.source_type.value} {e.url}",
                e.published.isoformat() if e.published else None,
                e.excerpt,
            )
            for e in card.evidence
        ],
        claims=usable,
    )
    usage, cost, corrections = Usage(0, 0), Decimal(0), 0
    research: Research | None = None
    raw = ""
    error: str | None = None
    for attempt in (1, 2):
        result = client.complete_json(request)
        usage = usage + result.usage
        cost = None if cost is None or result.cost is None else cost + result.cost
        if result.status is not LlmStatus.SUCCESS:
            error = f"модель: {result.status} ({result.error_code})"
            if result.status is LlmStatus.INVALID and attempt == 1:
                corrections += 1
                request = _corrected(request, f"ответ не JSON ({result.error_code})")
                continue
            break
        raw = json.dumps(result.data, ensure_ascii=False)
        try:
            research = parse_research(
                result.data,
                lead_id=card.lead_id,
                company_id=card.company_id,
                evidence_aliases=aliases,
                allowed_claim_ids=frozenset(c.id for c in usable),
                today=card.as_of,
            )
            error = None
            break
        except SchemaError as exc:
            error = f"схема: {exc}"
            if attempt == 1:
                corrections += 1
                request = _corrected(request, str(exc))
    if research is None:
        return CardResult(
            card.card_id, card.kind, result.status, corrections, usage, cost, error=error
        )
    score = compute_score(
        research,
        today=card.as_of,
        evidence=_evidence(card),
        source_types={card.evidence_id(e.alias): e.source_type for e in card.evidence},
        claims={claim.id: claim for claim in usable},
        contacts=_contacts(card),
    )
    dropped = sum(1 for p in research.problems if "несуществующие evidence" in p)
    return CardResult(
        card.card_id,
        card.kind,
        LlmStatus.SUCCESS,
        corrections,
        usage,
        cost,
        checks=tuple(_checks(card, research, score, raw)),
        dropped_refs=dropped,
    )


def _corrected(request: LlmRequest, error: str) -> LlmRequest:
    return replace(
        request,
        call_id=request.call_id.rsplit(":", 1)[0] + ":2",
        user=request.user + CORRECTION.format(error=error),
    )


def _evidence(card: EvalCard) -> dict[str, Evidence]:
    observed = datetime.combine(card.as_of, time(9, 0), tzinfo=timezone.utc)
    return {
        card.evidence_id(e.alias): Evidence(
            id=card.evidence_id(e.alias),
            company_id=card.company_id,
            # One source per evidence item: compute_score reads the source type by source_id.
            source_id=card.evidence_id(e.alias),
            claim=e.excerpt[:200],
            excerpt=e.excerpt,
            observed_at=observed,
        )
        for e in card.evidence
    }


def _contacts(card: EvalCard) -> list[Contact]:
    verified = datetime.combine(card.as_of, time(9, 0), tzinfo=timezone.utc)
    return [
        Contact(
            id=f"{card.card_id}:k{n}",
            company_id=card.company_id,
            channel=c.channel,
            normalized_address=normalize_address(c.channel, c.address),
            role=c.role,
            verified_at=verified if c.verified else None,
        )
        for n, c in enumerate(card.contacts, 1)
    ]


@dataclass(frozen=True, slots=True)
class EvalReport:
    model_id: str
    prompt_version: str
    run_on: date
    results: tuple[CardResult, ...]
    label_status: tuple[str, ...] = field(default=())

    @property
    def schema_rate(self) -> float:
        ok = sum(1 for r in self.results if r.schema_ok and r.corrections <= 1)
        return ok / len(self.results) if self.results else 0.0


def report_markdown(report: EvalReport) -> str:
    results = report.results
    passed = sum(1 for r in results if r.passed)
    total_in = sum(r.usage.input_tokens for r in results)
    total_out = sum(r.usage.output_tokens for r in results)
    costs = [r.cost for r in results]
    cost = "неизвестна" if any(c is None for c in costs) else str(sum(costs, Decimal(0)))
    injection = [c for r in results for c in r.checks if c.name == "injection (T-08)"]
    lines = [
        f"# Eval research — {report.run_on.isoformat()}",
        "",
        f"- Модель: `{report.model_id}`; промпт: `{report.prompt_version}`; "
        f"карточек: {len(results)}.",
        f"- Разметка: {', '.join(sorted(set(report.label_status))) or '—'}.",
        f"- Схема с не более чем одним исправлением: {report.schema_rate:.0%} "
        "(порог пилота 90%, документ 13).",
        f"- Все проверки пройдены: {passed} из {len(results)}.",
        "- Отброшено ссылок на несуществующие evidence (T-06): "
        f"{sum(r.dropped_refs for r in results)}.",
        "- Injection (T-08): "
        + (
            f"{sum(c.passed for c in injection)} из {len(injection)} устояли."
            if injection
            else "нет карточек."
        ),
        f"- Токены: {total_in} вход / {total_out} выход; стоимость: {cost}.",
        "",
        "| Карточка | Тип | Итог | Исправлений | Не прошло |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in results:
        failed = "; ".join(f"{c.name}: {c.detail}" for c in r.checks if not c.passed) or (
            r.error or ""
        )
        verdict = "✅" if r.passed else "❌"
        lines.append(f"| {r.card_id} | {r.kind} | {verdict} | {r.corrections} | {failed} |")
    lines.append("")
    return "\n".join(lines)


def run_eval(
    cards: Sequence[EvalCard], client: LlmClient, registry: CaseRegistry, run_on: date
) -> EvalReport:
    return EvalReport(
        model_id=client.model_id,
        prompt_version=RESEARCH_PROMPT_VERSION,
        run_on=run_on,
        results=tuple(run_card(card, client, registry) for card in cards),
        label_status=tuple(card.label_status for card in cards),
    )
