"""JSON schema of research_v1.1 for the provider's structured output. (S2-04)

The same contract as hunter.domain.research (S2-01, docs/research-contract.md), written as a
schema the provider can enforce. Strict structured output wants every property required and no
extra ones, so unknown values are null. The provider's check is a convenience: parse_research
checks the payload again and is the only judge (doc 11 mechanical checks).
"""

from __future__ import annotations

from typing import Any

from hunter.domain.catalog import Icp, Product
from hunter.domain.entities import DatePrecision
from hunter.domain.research import (
    BOOLEAN_FEATURES,
    LIMITS,
    MAX_CLAIMS,
    MAX_FACTS,
    MAX_MISSING,
    MAX_REFS,
    MAX_SIGNALS,
    SCHEMA_VERSION,
    BudgetLevel,
    ContactLevel,
)


def _text(limit: int) -> dict[str, Any]:
    return {"type": "string", "minLength": 1, "maxLength": limit}


def _nullable(limit: int) -> dict[str, Any]:
    return {"type": ["string", "null"], "maxLength": limit}


def _refs() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}, "maxItems": MAX_REFS}


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _enum(values: list[Any]) -> dict[str, Any]:
    """Strict providers (OpenAI, Azure) want a type next to every enum; null rides as a type."""
    types = sorted(
        {"null" if v is None else "boolean" if isinstance(v, bool) else "string" for v in values}
    )
    return {"type": types[0] if len(types) == 1 else types, "enum": values}


def _feature(value: dict[str, Any]) -> dict[str, Any]:
    return _object(
        {"value": value, "evidence_ids": _refs(), "explanation": _text(LIMITS["explanation"])}
    )


def research_json_schema() -> dict[str, Any]:
    boolean = {"type": ["boolean", "null"]}
    features = {name: _feature(boolean) for name in BOOLEAN_FEATURES}
    features["budget_evidence_level"] = _feature(_enum([level.value for level in BudgetLevel]))
    features["contact_level"] = _feature(_enum([level.value for level in ContactLevel]))
    return _object(
        {
            "schema_version": _enum([SCHEMA_VERSION]),
            "company_id": {"type": "string"},
            "lead_id": {"type": "string"},
            "icp": _enum([*(icp.value for icp in Icp), "unknown"]),
            "summary": _text(LIMITS["summary"]),
            "facts": {
                "type": "array",
                "maxItems": MAX_FACTS,
                "items": _object({"text": _text(LIMITS["fact_text"]), "evidence_ids": _refs()}),
            },
            "signals": {
                "type": "array",
                "maxItems": MAX_SIGNALS,
                "items": _object(
                    {
                        "type": _text(LIMITS["signal_type"]),
                        "text": _text(LIMITS["fact_text"]),
                        "evidence_ids": _refs(),
                        "event_date": {
                            "type": ["string", "null"],
                            "pattern": r"^\d{4}-\d{2}-\d{2}$",
                        },
                        "date_precision": _enum([p.value for p in DatePrecision]),
                    }
                ),
            },
            "problem_hypothesis": _nullable(LIMITS["problem_hypothesis"]),
            "validation_question": _nullable(LIMITS["validation_question"]),
            "recommended_product": _enum([*(p.value for p in Product), None]),
            "relevant_case_claim_ids": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": MAX_CLAIMS,
            },
            "scoring_inputs": _object(features),
            "missing_data": {
                "type": "array",
                "items": _text(LIMITS["missing_item"]),
                "maxItems": MAX_MISSING,
            },
            "offer": _object(
                {
                    "what_we_sell": _nullable(LIMITS["what_we_sell"]),
                    "why": _nullable(LIMITS["why"]),
                }
            ),
            "needs_review": {"type": "boolean"},
        }
    )
