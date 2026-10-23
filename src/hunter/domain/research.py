"""research_v1.1 contract and its mechanical checks. (S2-01, CONTRACT)

Doc 11 "Контракт research_v1" and "Механические проверки"; docs/research-contract.md.
research_v1.1 = research_v1 + offer {what_we_sell, why}.

The model sees evidence under short aliases (E1, E2, ...). parse_research() maps them back to
real evidence ids and never turns an untraceable reference into a fact (T-06): the item is
dropped, the problem is recorded and the result goes to review. The code, not the model,
computes the score (S2-08).

Unknown values: providers disagree on null in structured output, so for every nullable field
the model may return null or "", and "unknown" for an unknown boolean or ICP. The parsed result
always uses None.

Boundaries: pure validation; the caller passes `today`, the aliases and the allowed claim ids.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from types import MappingProxyType

from hunter.domain.catalog import Icp, Product
from hunter.domain.entities import DatePrecision

SCHEMA_VERSION = "research_v1.1"

LIMITS = MappingProxyType(
    {
        "summary": 500,
        "fact_text": 500,
        "signal_type": 60,
        "problem_hypothesis": 600,
        "validation_question": 300,
        "what_we_sell": 600,
        "why": 800,
        "explanation": 400,
        "missing_item": 200,
    }
)
MAX_FACTS = 10
MAX_SIGNALS = 3
MAX_CLAIMS = 3
MAX_MISSING = 10
MAX_REFS = 10  # evidence ids of one fact, signal or feature

BOOLEAN_FEATURES = (
    "segment_match",
    "task_match",
    "scale_match",
    "current_need_confirmed",
    "problem_confirmed",
    "relevant_case_confirmed",
)
FEATURES = (*BOOLEAN_FEATURES, "budget_evidence_level", "contact_level")

_TOP_LEVEL = frozenset(
    {
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
    """One scoring input proposed by the model, with real evidence ids.

    value: bool | None for BOOLEAN_FEATURES, BudgetLevel or ContactLevel for the other two.
    For relevant_case_confirmed the proof is a case claim id from the registry (S2-03).
    """

    value: bool | BudgetLevel | ContactLevel | None
    evidence_ids: tuple[str, ...]
    explanation: str

    @property
    def positive(self) -> bool:
        return self.value not in (None, False, BudgetLevel.UNKNOWN, ContactLevel.NONE)


@dataclass(frozen=True, slots=True)
class Fact:
    text: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Signal:
    """event_date is the first day of its period (domain contract, S1-05): a month is stored
    as its 1st, a year as January 1st; None exactly when the precision is unknown."""

    type: str
    text: str
    evidence_ids: tuple[str, ...]
    event_date: date | None
    date_precision: DatePrecision


@dataclass(frozen=True, slots=True)
class Research:
    """A validated research_v1.1 result. `problems` lists what the checks dropped."""

    lead_id: str
    company_id: str
    icp: Icp | None
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
        if set(self.features) != set(FEATURES):
            raise ValueError("features: every scoring input is present exactly once")
        object.__setattr__(self, "features", MappingProxyType(dict(self.features)))
        if self.problems and not self.needs_review:
            raise ValueError("a result with problems always needs review")
        if not self.facts and not self.needs_review:
            raise ValueError("a result without facts always needs review")

    @property
    def insufficient_evidence(self) -> bool:
        """No traceable fact: nothing may be drafted from this result (doc 08, doc 09)."""
        return not self.facts


class SchemaError(ValueError):
    """The payload does not match research_v1.1. The message is shown to the model in the one
    allowed format correction (doc 11), so it names fields and never echoes the payload."""


def parse_research(
    payload: object,
    *,
    lead_id: str,
    company_id: str,
    evidence_aliases: Mapping[str, str],
    allowed_claim_ids: frozenset[str],
    today: date,
) -> Research:
    """Validate a model payload against research_v1.1.

    Raises SchemaError when the shape is wrong (types, enums, limits, extra or missing fields,
    another lead). Problems of meaning do not raise: unknown evidence aliases, claims that are
    not allowed, future events and positive features without proof are dropped, listed in
    `problems`, and the result needs review.

    `evidence_aliases` maps the aliases shown to the model to real evidence ids. Pass only
    evidence that may be used: a rejected fact never returns (S2-08, decision of 13.10).
    `allowed_claim_ids` are the active approved claim ids of the case registry (S2-03).
    """
    if not isinstance(payload, Mapping):
        raise SchemaError("ожидается JSON-объект")
    missing = _TOP_LEVEL - set(payload)
    if missing:
        raise SchemaError(f"нет полей: {sorted(missing)}")
    extra = set(payload) - _TOP_LEVEL
    if extra:
        raise SchemaError(f"лишние поля: {_shown(extra)}")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise SchemaError(f"schema_version: ожидается {SCHEMA_VERSION}")
    if payload["lead_id"] != lead_id or payload["company_id"] != company_id:
        raise SchemaError("lead_id / company_id не совпадают с заданием")

    problems: list[str] = []
    refs = _Refs(evidence_aliases)

    facts: list[Fact] = []
    for i, item in enumerate(_list(payload, "facts", MAX_FACTS)):
        key = f"facts[{i}]"
        item = _object(item, key, {"text", "evidence_ids"})
        text = _text(item, "text", LIMITS["fact_text"], key)
        ids = refs.resolve(item["evidence_ids"], key)
        if ids:
            facts.append(Fact(text, ids))
        elif not item["evidence_ids"]:
            problems.append(f"{key}: факт без evidence удалён")

    signals: list[Signal] = []
    for i, item in enumerate(_list(payload, "signals", MAX_SIGNALS)):
        key = f"signals[{i}]"
        item = _object(item, key, {"type", "text", "evidence_ids", "event_date", "date_precision"})
        signal_type = _text(item, "type", LIMITS["signal_type"], key)
        text = _text(item, "text", LIMITS["fact_text"], key)
        precision = _enum(DatePrecision, item["date_precision"], f"{key}.date_precision")
        event_date = _event_date(item["event_date"], precision, key)
        ids = refs.resolve(item["evidence_ids"], key)
        if not ids:
            if not item["evidence_ids"]:
                problems.append(f"{key}: сигнал без evidence удалён")
            continue
        if event_date is not None and event_date > today:
            # The period has not started yet: the event has not happened (doc 11, check 5).
            problems.append(f"{key}: событие в будущем ({event_date}) не считается произошедшим")
            continue
        signals.append(
            Signal(
                type=signal_type,
                text=text,
                evidence_ids=ids,
                event_date=event_date,
                date_precision=precision if event_date else DatePrecision.UNKNOWN,
            )
        )

    claims = _strings(payload, "relevant_case_claim_ids", MAX_CLAIMS)
    forbidden = [c for c in claims if c not in allowed_claim_ids]
    if forbidden:
        problems.append(f"relevant_case_claim_ids: неразрешённые кейсы удалены {_shown(forbidden)}")

    features = _features(payload["scoring_inputs"], refs, allowed_claim_ids, problems)
    if refs.unknown:
        problems.append(f"ссылки на несуществующие evidence удалены: {_shown(refs.unknown)}")

    offer = _object(payload["offer"], "offer", {"what_we_sell", "why"})
    needs_review = payload["needs_review"]
    if not isinstance(needs_review, bool):
        raise SchemaError("needs_review: ожидается true или false")

    icp_raw = _optional_text(payload, "icp", None, "")
    product_raw = _optional_text(payload, "recommended_product", None, "")
    return Research(
        lead_id=lead_id,
        company_id=company_id,
        icp=None if icp_raw in (None, "unknown") else _enum(Icp, icp_raw, "icp"),
        summary=_text(payload, "summary", LIMITS["summary"], ""),
        facts=tuple(facts),
        signals=tuple(signals),
        problem_hypothesis=_optional_text(
            payload, "problem_hypothesis", LIMITS["problem_hypothesis"], ""
        ),
        validation_question=_optional_text(
            payload, "validation_question", LIMITS["validation_question"], ""
        ),
        recommended_product=None
        if product_raw is None
        else _enum(Product, product_raw, "recommended_product"),
        relevant_case_claim_ids=tuple(dict.fromkeys(c for c in claims if c in allowed_claim_ids)),
        features=features,
        missing_data=tuple(
            _bounded(m, LIMITS["missing_item"], f"missing_data[{i}]")
            for i, m in enumerate(_strings(payload, "missing_data", MAX_MISSING))
        ),
        what_we_sell=_optional_text(offer, "what_we_sell", LIMITS["what_we_sell"], "offer"),
        why=_optional_text(offer, "why", LIMITS["why"], "offer"),
        needs_review=needs_review or bool(problems) or not facts,
        problems=tuple(problems),
    )


class _Refs:
    """Maps aliases to real evidence ids and remembers every unknown reference."""

    def __init__(self, aliases: Mapping[str, str]) -> None:
        self.aliases = aliases
        self.unknown: set[str] = set()

    def resolve(
        self, raw: object, key: str, extra: frozenset[str] = frozenset()
    ) -> tuple[str, ...]:
        """Real ids for `raw`; empty when any alias is unknown, so a half-proved item is not
        kept. `extra` are ids accepted as they are (case claims for relevant_case_confirmed)."""
        if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
            raise SchemaError(f"{key}.evidence_ids: ожидается массив строк")
        if len(raw) > MAX_REFS:
            raise SchemaError(f"{key}.evidence_ids: больше {MAX_REFS} элементов")
        unknown = [x for x in raw if x not in self.aliases and x not in extra]
        if unknown:
            self.unknown.update(unknown)
            return ()
        return tuple(dict.fromkeys(x if x in extra else self.aliases[x] for x in raw))


# Only the real forms of what the model may name: an evidence alias, a case claim id, a field.
# A bare phone number or a login such as ivan.petrov must not pass as an identifier.
_SAFE_TOKEN = re.compile(r"E\d{1,4}|[a-z_]+\.[a-z_]+:v\d+|[a-z_]{1,40}")


def _shown(values) -> str:
    """Values written by the model, safe to store and show: identifiers like E7 or
    auto_crm.experience:v1 are named, anything else is only counted (it may hold a contact)."""
    unique = set(values)
    safe = sorted(v for v in unique if _SAFE_TOKEN.fullmatch(v))
    hidden = len(unique) - len(safe)
    return str(safe) + (f", недопустимых значений: {hidden}" if hidden else "")


def _features(
    raw: object, refs: _Refs, allowed_claim_ids: frozenset[str], problems: list[str]
) -> dict[str, Feature]:
    raw = _object(raw, "scoring_inputs", set(FEATURES))
    features: dict[str, Feature] = {}
    for name in FEATURES:
        key = f"scoring_inputs.{name}"
        item = _object(raw[name], key, {"value", "evidence_ids", "explanation"})
        explanation = _text(item, "explanation", LIMITS["explanation"], key)
        if name in BOOLEAN_FEATURES:
            value = item["value"]
            if value in (None, "", "unknown"):
                value = None
            elif isinstance(value, str) and value in ("true", "false"):
                value = value == "true"
            elif not isinstance(value, bool):
                # Lists, objects and numbers too: a shape error, never a TypeError.
                raise SchemaError(f"{key}.value: true / false / unknown")
        elif name == "budget_evidence_level":
            value = _enum(BudgetLevel, item["value"], f"{key}.value")
        else:
            value = _enum(ContactLevel, item["value"], f"{key}.value")
        claims = allowed_claim_ids if name == "relevant_case_confirmed" else frozenset()
        ids = refs.resolve(item["evidence_ids"], key, claims)
        feature = Feature(value, ids, explanation)
        if feature.positive and not ids:
            # A positive feature without traceable proof counts as unknown (doc 11, check 4).
            problems.append(f"{name}: нет подтверждающего evidence, признак считается неизвестным")
            unknown = {
                "budget_evidence_level": BudgetLevel.UNKNOWN,
                "contact_level": ContactLevel.NONE,
            }
            feature = Feature(unknown.get(name), (), explanation)
        features[name] = feature
    return features


def _object(raw: object, key: str, fields: set[str]) -> Mapping:
    if not isinstance(raw, Mapping):
        raise SchemaError(f"{key}: ожидается объект")
    if set(raw) != fields:
        raise SchemaError(f"{key}: ожидаются поля {sorted(fields)}")
    return raw


def _list(payload: Mapping, key: str, limit: int) -> list:
    value = payload[key]
    if not isinstance(value, list):
        raise SchemaError(f"{key}: ожидается массив")
    if len(value) > limit:
        raise SchemaError(f"{key}: больше {limit} элементов")
    return value


def _strings(payload: Mapping, key: str, limit: int) -> list[str]:
    value = _list(payload, key, limit)
    if not all(isinstance(x, str) and x.strip() for x in value):
        raise SchemaError(f"{key}: ожидается массив непустых строк")
    return [x.strip() for x in value]


def _bounded(value: str, limit: int | None, key: str) -> str:
    if limit is not None and len(value) > limit:
        raise SchemaError(f"{key}: длиннее {limit} символов")
    return value


def _text(payload: Mapping, name: str, limit: int, prefix: str) -> str:
    key = f"{prefix}.{name}" if prefix else name
    value = payload[name]
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{key}: ожидается непустая строка")
    return _bounded(value.strip(), limit, key)


def _optional_text(payload: Mapping, name: str, limit: int | None, prefix: str) -> str | None:
    key = f"{prefix}.{name}" if prefix else name
    value = payload[name]
    if value is None:
        return None
    if not isinstance(value, str):
        raise SchemaError(f"{key}: ожидается строка или null")
    return _bounded(value.strip(), limit, key) or None


def _enum(enum_cls, raw: object, key: str):
    try:
        return enum_cls(raw)
    except ValueError:
        raise SchemaError(f"{key}: недопустимое значение") from None


def _event_date(raw: object, precision: DatePrecision, key: str) -> date | None:
    if raw is not None and not isinstance(raw, str):
        raise SchemaError(f"{key}.event_date: ожидается YYYY-MM-DD, пустая строка или null")
    if precision is DatePrecision.UNKNOWN or not raw:
        return None
    try:
        value = date.fromisoformat(raw)
    except ValueError:
        raise SchemaError(f"{key}.event_date: ожидается YYYY-MM-DD") from None
    # One fact, one spelling: the model may name any day inside a month or a year.
    if precision is DatePrecision.MONTH:
        return value.replace(day=1)
    if precision is DatePrecision.YEAR:
        return value.replace(month=1, day=1)
    return value
