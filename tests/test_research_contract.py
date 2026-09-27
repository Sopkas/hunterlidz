from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from hunter.domain.budget import BudgetExceeded, Limits, Pricing, RunBudget
from hunter.domain.research import SchemaError, parse_research
from hunter.runtime.config import ConfigError, llm_settings
from tests.research_support import feature, payload

FIXTURES = Path(__file__).parent / "fixtures"
LEAD = SimpleNamespace(id="lead-1", company_id="co-1")
ALIASES = {f"E{i}": f"ev-{i}" for i in range(1, 8)}
TODAY = date(2026, 9, 27)


def parse(data):
    return parse_research(
        data,
        lead_id="lead-1",
        company_id="co-1",
        evidence_aliases=ALIASES,
        allowed_claim_ids=frozenset({"auto_crm:v1"}),
        today=TODAY,
    )


def test_valid_payload_maps_aliases_to_real_ids():
    result = parse(payload(LEAD))
    assert result.facts[0].evidence_ids == ("ev-2",)
    assert result.features["segment_match"].value is True
    assert not result.needs_review and not result.problems


@pytest.mark.parametrize(
    "patch",
    [{"extra": 1}, {"lead_id": "other"}, {"icp": "banks"}, {"needs_review": "no"}],
    ids=["extra-field", "wrong-lead", "bad-enum", "bad-bool"],
)
def test_schema_errors(patch):
    with pytest.raises(SchemaError):
        parse(payload(LEAD, **patch))


def test_future_signal_is_not_an_event():
    data = payload(LEAD)
    data["signals"][0]["event_date"] = "2026-10-15"
    result = parse(data)
    assert result.signals == () and result.needs_review


def test_unknown_date_precision_drops_the_date():
    data = payload(LEAD)
    data["signals"][0].update(event_date="2026-09-01", date_precision="unknown")
    assert parse(data).signals[0].event_date is None


def test_positive_feature_without_evidence_becomes_unknown():
    data = payload(LEAD)
    data["scoring_inputs"]["scale_match"] = feature("true", [], "на глаз")
    data["scoring_inputs"]["budget_evidence_level"] = feature("confirmed_range", [], "угадал")
    result = parse(data)
    assert result.features["scale_match"].value is None
    assert result.features["budget_evidence_level"].value.value == "unknown"
    assert result.needs_review


def test_forbidden_claim_removed():
    result = parse(payload(LEAD, relevant_case_claim_ids=["auto_crm:v1", "made_up:v1"]))
    assert result.relevant_case_claim_ids == ("auto_crm:v1",) and result.needs_review


def test_no_facts_is_insufficient():
    assert parse(payload(LEAD, facts=[])).insufficient


def test_budget_reserves_worst_case_before_call():
    budget = RunBudget(
        Limits(max_llm_calls=2, run_cost_cap=Decimal("1"), daily_cost_cap=Decimal("1")),
        Pricing(Decimal("1"), Decimal("1"), "USD"),
    )
    assert budget.reserve_llm_call(10_000, 0) == 3_000
    budget.record(10_000, 1_000)
    with pytest.raises(BudgetExceeded, match="input_tokens"):
        budget.reserve_llm_call(10_001, 1)
    budget.record(1, 1)
    with pytest.raises(BudgetExceeded, match="llm_calls"):
        budget.reserve_llm_call(1, 1)


def test_budget_stops_after_active_time():
    budget = RunBudget(
        Limits(run_cost_cap=Decimal(1), daily_cost_cap=Decimal(1)),
        Pricing(Decimal(0), Decimal(0), "USD"),
    )
    with pytest.raises(BudgetExceeded, match="time"):
        budget.reserve_llm_call(1, 120.0)


BASE_ENV = {
    "ANTHROPIC_API_KEY": "k",
    "HUNTER_LLM_PRICE_INPUT_PER_MTOK": "1",
    "HUNTER_LLM_PRICE_OUTPUT_PER_MTOK": "5",
    "COST_CURRENCY": "usd",
    "RUN_COST_CAP": "0.5",
    "DAILY_COST_CAP": "5",
}


def test_llm_settings_defaults():
    cfg = llm_settings(BASE_ENV)
    assert (cfg.model, cfg.pricing.currency, cfg.limits.max_llm_calls) == (
        "claude-haiku-4-5",
        "USD",
        3,
    )


@pytest.mark.parametrize(
    "missing",
    [
        "ANTHROPIC_API_KEY",
        "HUNTER_LLM_PRICE_INPUT_PER_MTOK",
        "COST_CURRENCY",
        "RUN_COST_CAP",
    ],
)
def test_llm_settings_required(missing):
    env = {k: v for k, v in BASE_ENV.items() if k != missing}
    with pytest.raises(ConfigError):
        llm_settings(env)


@pytest.mark.parametrize("name", ["DAILY_COST_CAP", "HUNTER_LLM_PRICE_OUTPUT_PER_MTOK"])
def test_zero_cap_or_tariff_rejected(name):
    with pytest.raises(ConfigError):
        llm_settings({**BASE_ENV, name: "0"})


def test_relevant_case_cites_registry_claim():
    data = payload(LEAD)
    data["scoring_inputs"]["relevant_case_confirmed"] = feature("true", ["auto_crm:v1"], "CRM")
    result = parse(data)
    assert result.features["relevant_case_confirmed"].evidence_ids == ("auto_crm:v1",)
    assert result.features["relevant_case_confirmed"].value is True and not result.problems


def test_claim_id_is_not_evidence_for_other_features():
    data = payload(LEAD)
    data["scoring_inputs"]["segment_match"] = feature("true", ["auto_crm:v1"], "кейс")
    result = parse(data)
    assert result.features["segment_match"].value is None and result.needs_review
