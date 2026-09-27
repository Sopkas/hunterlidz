"""Stored form of a validated research result. (H-05)

The payload carries schema_version and real evidence ids; reading it back is strict:
a malformed payload raises instead of producing a half-valid object.
"""

from __future__ import annotations

import json
from datetime import date

from hunter.domain.catalog import Icp, Product
from hunter.domain.entities import DatePrecision
from hunter.domain.research import (
    BOOLEAN_FEATURES,
    SCHEMA_VERSION,
    BudgetLevel,
    ContactLevel,
    Fact,
    Feature,
    Research,
    Signal,
)


def dumps(research: Research) -> str:
    def feature(f: Feature) -> dict:
        value = f.value.value if hasattr(f.value, "value") else f.value
        return {"value": value, "evidence_ids": list(f.evidence_ids), "explanation": f.explanation}

    return json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "icp": research.icp.value,
            "summary": research.summary,
            "facts": [
                {"text": f.text, "evidence_ids": list(f.evidence_ids)} for f in research.facts
            ],
            "signals": [
                {
                    "type": s.type,
                    "text": s.text,
                    "evidence_ids": list(s.evidence_ids),
                    "event_date": s.event_date.isoformat() if s.event_date else None,
                    "date_precision": s.date_precision.value,
                }
                for s in research.signals
            ],
            "problem_hypothesis": research.problem_hypothesis,
            "validation_question": research.validation_question,
            "recommended_product": research.recommended_product.value
            if research.recommended_product
            else None,
            "relevant_case_claim_ids": list(research.relevant_case_claim_ids),
            "scoring_inputs": {k: feature(v) for k, v in research.features.items()},
            "missing_data": list(research.missing_data),
            "offer": {"what_we_sell": research.what_we_sell, "why": research.why},
            "needs_review": research.needs_review,
            "problems": list(research.problems),
        },
        ensure_ascii=False,
    )


def loads(text: str) -> Research:
    data = json.loads(text)
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"stored research has schema {data.get('schema_version')!r}")
    features = {}
    for name, item in data["scoring_inputs"].items():
        raw = item["value"]
        if name in BOOLEAN_FEATURES:
            value = raw
        elif name == "budget_evidence_level":
            value = BudgetLevel(raw)
        else:
            value = ContactLevel(raw)
        features[name] = Feature(value, tuple(item["evidence_ids"]), item["explanation"])
    return Research(
        icp=Icp(data["icp"]),
        summary=data["summary"],
        facts=tuple(Fact(f["text"], tuple(f["evidence_ids"])) for f in data["facts"]),
        signals=tuple(
            Signal(
                type=s["type"],
                text=s["text"],
                evidence_ids=tuple(s["evidence_ids"]),
                event_date=date.fromisoformat(s["event_date"]) if s["event_date"] else None,
                date_precision=DatePrecision(s["date_precision"]),
            )
            for s in data["signals"]
        ),
        problem_hypothesis=data["problem_hypothesis"],
        validation_question=data["validation_question"],
        recommended_product=Product(data["recommended_product"])
        if data["recommended_product"]
        else None,
        relevant_case_claim_ids=tuple(data["relevant_case_claim_ids"]),
        features=features,
        missing_data=tuple(data["missing_data"]),
        what_we_sell=data["offer"]["what_we_sell"],
        why=data["offer"]["why"],
        needs_review=bool(data["needs_review"]),
        problems=tuple(data["problems"]),
    )
