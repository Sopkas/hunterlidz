"""Fakes and payload builders for research tests."""

from __future__ import annotations

import copy
from datetime import timezone
from decimal import Decimal

from hunter.application.ports import LlmError, LlmResponse
from hunter.application.research import ResearchConfig
from hunter.domain.budget import Limits, Pricing
from hunter.domain.research import BOOLEAN_FEATURES, SCHEMA_VERSION

MSK = timezone.utc  # tests use UTC as the business zone for readability

FREE = Pricing(Decimal(0), Decimal(0), "USD")


def config(limits: Limits | None = None, pricing: Pricing = FREE) -> ResearchConfig:
    limits = limits or Limits(run_cost_cap=Decimal("1"), daily_cost_cap=Decimal("5"))
    return ResearchConfig(limits=limits, pricing=pricing, business_tz=MSK)


class FakeKnowledge:
    def documents(self, prefixes):
        return {f"{p}-doc.md": f"документ {p}" for p in prefixes}


def feature(value="unknown", ids=(), explanation="—"):
    return {"value": value, "evidence_ids": list(ids), "explanation": explanation}


def payload(lead, **overrides):
    """A valid research_v1.1 answer for an imported GOOD listing (E1 title, E2 description...)."""
    scoring = {name: feature() for name in BOOLEAN_FEATURES}
    scoring.update(
        segment_match=feature("true", ["E2"], "сеть автосервисов"),
        task_match=feature("true", ["E2"], "нужна CRM"),
        scale_match=feature("true", ["E4"], "бюджет 400 000"),
        current_need_confirmed=feature("true", ["E2", "E5"], "объявление 7 дней назад"),
        problem_confirmed=feature("true", ["E2"], "заявки теряются"),
        relevant_case_confirmed=feature("true", ["E2"], "CRM для автосервисов"),
        budget_evidence_level=feature("directional_spend", ["E4"], "бюджет в объявлении"),
        contact_level=feature("decision_maker_channel", ["E6"], "собственник"),
    )
    data = {
        "schema_version": SCHEMA_VERSION,
        "company_id": lead.company_id,
        "lead_id": lead.id,
        "icp": "existing_business",
        "summary": "Сеть автосервисов ищет разработку CRM.",
        "facts": [
            {"text": "Сеть из 4 автосервисов", "evidence_ids": ["E2"]},
            {"text": "Бюджет 400 000 ₽", "evidence_ids": ["E4"]},
        ],
        "signals": [
            {
                "type": "listing",
                "text": "Опубликован запрос на CRM",
                "evidence_ids": ["E5"],
                "event_date": "2026-09-20",
                "date_precision": "day",
            }
        ],
        "problem_hypothesis": "Заявки теряются между филиалами.",
        "validation_question": "Как сейчас распределяются заявки?",
        "recommended_product": "business_automation",
        "relevant_case_claim_ids": ["auto_crm:v1"],
        "scoring_inputs": scoring,
        "missing_data": [],
        "offer": {"what_we_sell": "CRM под процесс записи", "why": "кейс CRM для автосервисов"},
        "needs_review": False,
    }
    data.update(overrides)
    return data


class FakeLlm:
    """Replays scripted answers: dicts become responses, exceptions are raised, callables run."""

    model = "fake-model"

    def __init__(self, *script, input_tokens=1000, output_tokens=500):
        self.script = list(script)
        self.calls: list[tuple[str, str, int]] = []
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def complete_json(self, system, user, schema, max_output_tokens):
        self.calls.append((system, user, max_output_tokens))
        step = self.script.pop(0)
        if callable(step) and not isinstance(step, dict):
            step = step()
        if isinstance(step, LlmError):
            raise step
        if isinstance(step, LlmResponse):
            return step
        return LlmResponse(copy.deepcopy(step), self.input_tokens, self.output_tokens)
