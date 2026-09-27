"""research_v1 contract and its mechanical checks. (H-05)

Doc 11 "Контракт research_v1" and "Механические проверки". The model sees evidence under short
aliases (E1, E2, ...); this module maps aliases back to real evidence ids and rejects anything
it cannot trace. A reference to an unknown alias never becomes a fact (T-06): the item is
dropped and the result goes to review. The code, not the model, decides the score (H-06).

Provider-facing encoding: structured-output schemas of different providers disagree on null,
so the model returns "" for an unknown string or date and "unknown" for an unknown boolean.
parse_research() turns them back into None.

Extension: schema "research_v1.1" = research_v1 + offer.what_we_sell / offer.why, the
explanation the sales team asked for ("что мы можем продать и почему").
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from types import MappingProxyType

from hunter.domain.catalog import Icp, Product
from hunter.domain.entities import DatePrecision

SCHEMA_VERSION = "research_v1.1"

LIMITS = {
    "summary": 500,
    "fact_text": 500,
    "problem_hypothesis": 600,
    "validation_question": 300,
    "what_we_sell": 600,
    "why": 800,
    "explanation": 400,
}
MAX_FACTS = 10
MAX_SIGNALS = 3
MAX_CLAIMS = 3
MAX_MISSING = 10

BOOLEAN_FEATURES = (
    "segment_match",
    "task_match",
    "scale_match",
    "current_need_confirmed",
    "problem_confirmed",
    "relevant_case_confirmed",
)


class BudgetLevel(StrEnum):
    UNKNOWN = "unknown"
    INDIRECT = "indirect"
    DIRECTIONAL_SPEND = "directional_spend"
    CONFIRMED_RANGE = "confirmed_range"


class ContactLevel(StrEnum):
    NONE = "none"
    COMPANY_CHANNEL = "company_channel"
    DECISION_MAKER_CHANNEL = "decision_maker_channel"


@dataclass(frozen=True, slots=True)
class Feature:
    """One scoring feature proposed by the model: value, real evidence ids, explanation."""

    value: object  # bool | None for boolean features, BudgetLevel / ContactLevel otherwise
    evidence_ids: tuple[str, ...]
    explanation: str


@dataclass(frozen=True, slots=True)
class Fact:
    text: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Signal:
    type: str
    text: str
    evidence_ids: tuple[str, ...]
    event_date: date | None
    date_precision: DatePrecision


@dataclass(frozen=True, slots=True)
class Research:
    icp: Icp
    summary: str
    facts: tuple[Fact, ...]
    signals: tuple[Signal, ...]
    problem_hypothesis: str | None
    validation_question: str | None
    recommended_product: Product | None
    relevant_case_claim_ids: tuple[str, ...]
    features: Mapping[str, Feature]
    missing_data: tuple[str, ...]
    what_we_sell: str | None
    why: str | None
    needs_review: bool
    problems: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        object.__setattr__(self, "features", MappingProxyType(dict(self.features)))

    @property
    def insufficient(self) -> bool:
        """No traceable fact: nothing may be drafted from this result (doc 08)."""
        return not self.facts


class SchemaError(ValueError):
    """The payload does not match research_v1.1; the message is shown to the model once."""


def _str(payload: Mapping, key: str, limit: int | None = None) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise SchemaError(f"{key}: ожидается строка")
    value = value.strip()
    if limit and len(value) > limit:
        raise SchemaError(f"{key}: длиннее {limit} символов")
    return value


def _list(payload: Mapping, key: str, limit: int) -> list:
    value = payload.get(key)
    if not isinstance(value, list):
        raise SchemaError(f"{key}: ожидается массив")
    if len(value) > limit:
        raise SchemaError(f"{key}: больше {limit} элементов")
    return value


def _enum(enum_cls, raw: object, key: str):
    try:
        return enum_cls(raw)
    except ValueError:
        raise SchemaError(f"{key}: недопустимое значение {raw!r}") from None


class _Refs:
    """Maps aliases to real evidence ids and records every unknown reference."""

    def __init__(self, aliases: Mapping[str, str]) -> None:
        self.aliases = aliases
        self.unknown: set[str] = set()

    def resolve(self, raw: object, key: str) -> tuple[str, ...] | None:
        if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
            raise SchemaError(f"{key}.evidence_ids: ожидается массив строк")
        missing = [x for x in raw if x not in self.aliases]
        if missing:
            self.unknown.update(missing)
            return None
        return tuple(self.aliases[x] for x in raw)


def _date(raw: str, precision: DatePrecision, key: str) -> date | None:
    if precision is DatePrecision.UNKNOWN or not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise SchemaError(f"{key}.event_date: ожидается YYYY-MM-DD или пустая строка") from None


def parse_research(
    payload: object,
    *,
    lead_id: str,
    company_id: str,
    evidence_aliases: Mapping[str, str],
    allowed_claim_ids: frozenset[str],
    today: date,
) -> Research:
    """Validate a model payload. SchemaError: malformed (one correction allowed).
    Semantic problems (unknown evidence, forbidden claims, future dates) do not raise:
    the offending items are dropped and listed in `problems`, and needs_review is set."""
    if not isinstance(payload, Mapping):
        raise SchemaError("ожидается JSON-объект")
    expected_keys = {
        "schema_version",
        "company_id",
        "lead_id",
        "icp",
        "summary",
        "facts",
        "signals",
        "problem_hypothesis",
        "validation_question",
        "recommended_product",
        "relevant_case_claim_ids",
        "scoring_inputs",
        "missing_data",
        "offer",
        "needs_review",
    }
    extra = set(payload) - expected_keys
    if extra:
        raise SchemaError(f"лишние поля: {sorted(extra)}")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise SchemaError(f"schema_version должен быть {SCHEMA_VERSION}")
    problems: list[str] = []
    if payload.get("lead_id") != lead_id or payload.get("company_id") != company_id:
        raise SchemaError("lead_id / company_id не совпадают с заданием")

    refs = _Refs(evidence_aliases)
    facts = []
    for i, item in enumerate(_list(payload, "facts", MAX_FACTS)):
        if not isinstance(item, Mapping):
            raise SchemaError(f"facts[{i}]: ожидается объект")
        ids = refs.resolve(item.get("evidence_ids"), f"facts[{i}]")
        text = _str(item, "text", LIMITS["fact_text"])
        if ids and text:
            facts.append(Fact(text, ids))

    signals = []
    for i, item in enumerate(_list(payload, "signals", MAX_SIGNALS)):
        if not isinstance(item, Mapping):
            raise SchemaError(f"signals[{i}]: ожидается объект")
        precision = _enum(DatePrecision, item.get("date_precision"), f"signals[{i}].date_precision")
        event_date = _date(_str(item, "event_date"), precision, f"signals[{i}]")
        ids = refs.resolve(item.get("evidence_ids"), f"signals[{i}]")
        if ids is None:
            continue
        if event_date and event_date > today:
            problems.append(f"сигнал с будущей датой {event_date} не считается произошедшим")
            continue
        signals.append(
            Signal(
                type=_str(item, "type", 60),
                text=_str(item, "text", LIMITS["fact_text"]),
                evidence_ids=ids,
                event_date=event_date,
                date_precision=precision if event_date else DatePrecision.UNKNOWN,
            )
        )

    claims = [
        c for c in _list(payload, "relevant_case_claim_ids", MAX_CLAIMS) if isinstance(c, str)
    ]
    forbidden = [c for c in claims if c not in allowed_claim_ids]
    if forbidden:
        problems.append(f"неразрешённые кейсы удалены: {forbidden}")

    features = _features(payload.get("scoring_inputs"), refs, problems, allowed_claim_ids)
    if refs.unknown:
        problems.append(f"ссылки на несуществующие evidence удалены: {sorted(refs.unknown)}")

    offer = payload.get("offer")
    if not isinstance(offer, Mapping):
        raise SchemaError("offer: ожидается объект {what_we_sell, why}")
    product_raw = _str(payload, "recommended_product")
    missing = [m.strip() for m in _list(payload, "missing_data", MAX_MISSING) if isinstance(m, str)]
    needs_review = payload.get("needs_review")
    if not isinstance(needs_review, bool):
        raise SchemaError("needs_review: ожидается boolean")

    return Research(
        icp=_enum(Icp, payload.get("icp"), "icp"),
        summary=_str(payload, "summary", LIMITS["summary"]),
        facts=tuple(facts),
        signals=tuple(signals),
        problem_hypothesis=_str(payload, "problem_hypothesis", LIMITS["problem_hypothesis"])
        or None,
        validation_question=_str(payload, "validation_question", LIMITS["validation_question"])
        or None,
        recommended_product=None
        if product_raw in ("", "none")
        else _enum(Product, product_raw, "recommended_product"),
        relevant_case_claim_ids=tuple(c for c in claims if c in allowed_claim_ids),
        features=features,
        missing_data=tuple(m for m in missing if m),
        what_we_sell=_str(offer, "what_we_sell", LIMITS["what_we_sell"]) or None,
        why=_str(offer, "why", LIMITS["why"]) or None,
        needs_review=needs_review or bool(problems) or not facts,
        problems=tuple(problems),
    )


def _features(
    raw: object, refs: _Refs, problems: list[str], allowed_claim_ids: frozenset[str]
) -> dict[str, Feature]:
    if not isinstance(raw, Mapping):
        raise SchemaError("scoring_inputs: ожидается объект")
    features: dict[str, Feature] = {}
    specs = [(name, "boolean") for name in BOOLEAN_FEATURES]
    specs += [("budget_evidence_level", BudgetLevel), ("contact_level", ContactLevel)]
    for name, kind in specs:
        item = raw.get(name)
        if not isinstance(item, Mapping):
            raise SchemaError(f"scoring_inputs.{name}: ожидается объект")
        explanation = _str(item, "explanation", LIMITS["explanation"])
        if kind == "boolean":
            value = {"true": True, "false": False, "unknown": None}.get(item.get("value"), "bad")
            if value == "bad":
                raise SchemaError(f"scoring_inputs.{name}.value: true / false / unknown")
        else:
            value = _enum(kind, item.get("value"), f"scoring_inputs.{name}.value")
        raw_ids = item.get("evidence_ids")
        if name == "relevant_case_confirmed" and isinstance(raw_ids, list):
            # The proof of a relevant case is the registry claim itself, cited by its id.
            claim_ids = tuple(x for x in raw_ids if x in allowed_claim_ids)
            raw_ids = [x for x in raw_ids if x not in allowed_claim_ids]
        else:
            claim_ids = ()
        ids = refs.resolve(raw_ids, f"scoring_inputs.{name}")
        if ids is not None:
            ids = claim_ids + ids
        positive = value is True or value not in (
            None,
            False,
            BudgetLevel.UNKNOWN,
            ContactLevel.NONE,
        )
        if positive and not ids:
            # A positive feature without traceable evidence counts as unknown (doc 11, check 4).
            problems.append(f"{name}: нет подтверждающего evidence — признак считается неизвестным")
            value = None if kind == "boolean" else kind(list(kind)[0])
            ids = ()
        features[name] = Feature(value=value, evidence_ids=ids or (), explanation=explanation)
    return features
