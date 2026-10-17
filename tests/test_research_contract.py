"""research_v1.1 contract (S2-01): examples in tests/fixtures and doc 11 mechanical checks."""

import copy
import json
from datetime import date
from pathlib import Path

import pytest

from hunter.domain.catalog import Icp, Product
from hunter.domain.entities import DatePrecision
from hunter.domain.research import (
    FEATURES,
    MAX_CLAIMS,
    MAX_FACTS,
    MAX_SIGNALS,
    BudgetLevel,
    ContactLevel,
    Feature,
    Research,
    SchemaError,
    parse_research,
)

FIXTURES = Path(__file__).parent / "fixtures"
ALIASES = {"E1": "ev-site", "E2": "ev-news", "E3": "ev-vacancy"}
CLAIMS = frozenset({"auto_crm.experience:v1", "careermate.about:v1"})
TODAY = date(2026, 10, 18)


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"research_v1_1_{name}.json").read_text("utf-8"))


def parse(payload, **overrides) -> Research:
    args = dict(
        lead_id="l-alpha",
        company_id="c-alpha",
        evidence_aliases=ALIASES,
        allowed_claim_ids=CLAIMS,
        today=TODAY,
    )
    args.update(overrides)
    return parse_research(payload, **args)


# --- Contract examples ---------------------------------------------------------------------


def test_valid_example_parses_without_review():
    research = parse(load("valid"))
    assert research.needs_review is False
    assert research.problems == ()
    assert research.icp is Icp.EXISTING_BUSINESS
    assert research.recommended_product is Product.BUSINESS_AUTOMATION
    assert [f.evidence_ids for f in research.facts] == [("ev-site",), ("ev-site", "ev-news")]
    assert research.relevant_case_claim_ids == ("auto_crm.experience:v1",)
    assert research.what_we_sell.startswith("Первый этап")
    assert not research.insufficient_evidence


def test_valid_example_maps_aliases_to_real_evidence_ids():
    features = parse(load("valid")).features
    assert features["segment_match"] == Feature(True, ("ev-site",), "Действующий бизнес, 12 точек")
    assert features["budget_evidence_level"].value is BudgetLevel.INDIRECT
    assert features["contact_level"].value is ContactLevel.COMPANY_CHANNEL
    # The proof of a relevant case is the registry claim itself (S2-03).
    assert features["relevant_case_confirmed"].evidence_ids == ("auto_crm.experience:v1",)


def test_month_precision_is_stored_as_the_first_day():
    signal = parse(load("valid")).signals[1]
    assert signal.event_date == date(2026, 8, 1)
    assert signal.date_precision is DatePrecision.MONTH


def test_needs_review_example_drops_what_it_cannot_trace():
    research = parse(load("needs_review"))
    assert research.needs_review is True
    assert len(research.facts) == 2  # the fact citing E9 is gone (T-06)
    assert [s.type for s in research.signals] == ["hiring"]  # the future event is gone
    assert research.relevant_case_claim_ids == ("auto_crm.experience:v1",)
    assert research.features["problem_confirmed"].value is None
    problems = " ".join(research.problems)
    for expected in ("E9", "будущем", "invented.case:v1", "problem_confirmed"):
        assert expected in problems


def test_unknowns_example_turns_every_blank_into_none():
    research = parse(load("unknowns"))
    assert research.icp is None
    assert research.problem_hypothesis is None and research.validation_question is None
    assert research.recommended_product is None
    assert research.what_we_sell is None and research.why is None
    assert research.signals[0].event_date is None
    assert research.signals[0].date_precision is DatePrecision.UNKNOWN
    assert all(research.features[name].value is None for name in FEATURES[:6])
    assert research.features["budget_evidence_level"].value is BudgetLevel.UNKNOWN
    assert research.features["contact_level"].value is ContactLevel.NONE


def test_no_traceable_fact_is_insufficient_evidence():
    research = parse(load("unknowns"))
    assert research.insufficient_evidence
    assert research.needs_review


def test_model_saying_no_review_does_not_hide_problems():
    payload = load("needs_review")
    assert payload["needs_review"] is False
    assert parse(payload).needs_review is True


# --- T-06: invented evidence ---------------------------------------------------------------


@pytest.mark.parametrize("section", ["facts", "signals"])
def test_invented_evidence_id_drops_the_item(section):
    payload = load("valid")
    payload[section][0]["evidence_ids"] = ["E1", "E42"]
    research = parse(payload)
    assert len(getattr(research, section)) == len(load("valid")[section]) - 1
    assert research.needs_review and "E42" in " ".join(research.problems)


def test_real_evidence_id_instead_of_alias_is_unknown():
    payload = load("valid")
    payload["facts"][0]["evidence_ids"] = ["ev-site"]
    assert "ev-site" in " ".join(parse(payload).problems)


def test_fact_without_evidence_is_dropped():
    payload = load("valid")
    payload["facts"][0]["evidence_ids"] = []
    research = parse(payload)
    assert len(research.facts) == 1 and research.needs_review


def test_positive_feature_with_invented_evidence_is_unknown():
    payload = load("valid")
    payload["scoring_inputs"]["task_match"]["evidence_ids"] = ["E77"]
    feature = parse(payload).features["task_match"]
    assert feature.value is None and feature.evidence_ids == ()


@pytest.mark.parametrize(
    ("name", "value", "unknown"),
    [
        ("budget_evidence_level", "confirmed_range", BudgetLevel.UNKNOWN),
        ("contact_level", "decision_maker_channel", ContactLevel.NONE),
    ],
)
def test_positive_level_without_proof_falls_back_to_unknown(name, value, unknown):
    payload = load("valid")
    payload["scoring_inputs"][name] = {"value": value, "evidence_ids": [], "explanation": "x"}
    assert parse(payload).features[name].value is unknown


def test_negative_feature_needs_no_proof():
    payload = load("valid")
    payload["scoring_inputs"]["scale_match"] = {
        "value": False,
        "evidence_ids": [],
        "explanation": "Две точки",
    }
    research = parse(payload)
    assert research.features["scale_match"].value is False and research.problems == ()


def test_case_claim_is_proof_only_for_relevant_case():
    payload = load("valid")
    payload["scoring_inputs"]["task_match"]["evidence_ids"] = ["auto_crm.experience:v1"]
    assert parse(payload).features["task_match"].value is None


def test_rejected_evidence_is_not_offered_to_the_model():
    # The caller leaves rejected evidence out of the aliases, so citing it is like inventing it.
    payload = load("valid")
    research = parse(payload, evidence_aliases={"E1": "ev-site", "E3": "ev-vacancy"})
    assert "E2" in " ".join(research.problems)


# --- Dates (doc 11, check 5) ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "precision", "stored"),
    [
        ("2026-03-15", "day", date(2026, 3, 15)),
        ("2026-03-15", "month", date(2026, 3, 1)),
        ("2026-03-15", "year", date(2026, 1, 1)),
        ("2026-03-15", "unknown", None),
        ("", "day", None),
    ],
)
def test_event_date_is_one_spelling_per_period(raw, precision, stored):
    payload = load("valid")
    payload["signals"][0].update(event_date=raw, date_precision=precision)
    assert parse(payload).signals[0].event_date == stored


def test_current_month_is_not_in_the_future():
    payload = load("valid")
    payload["signals"][0].update(event_date="2026-10-30", date_precision="month")
    assert parse(payload).signals[0].event_date == date(2026, 10, 1)


def test_next_month_is_in_the_future():
    payload = load("valid")
    payload["signals"][0].update(event_date="2026-11-01", date_precision="month")
    assert [s.type for s in parse(payload).signals] == ["hiring"]


# --- Shape errors: one format correction ---------------------------------------------------


def _broken(change):
    payload = load("valid")
    change(payload)
    return payload


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(schema_version="research_v1"),
        lambda p: p.update(lead_id="l-other"),
        lambda p: p.update(company_id="c-other"),
        lambda p: p.update(extra="x"),
        lambda p: p.pop("offer"),
        lambda p: p.update(icp="startups"),
        lambda p: p.update(recommended_product="consulting"),
        lambda p: p.update(summary=""),
        lambda p: p.update(summary="x" * 501),
        lambda p: p.update(facts=[{"text": "x", "evidence_ids": ["E1"]}] * (MAX_FACTS + 1)),
        lambda p: p.update(signals=p["signals"] * MAX_SIGNALS),
        lambda p: p.update(relevant_case_claim_ids=["auto_crm.experience:v1"] * (MAX_CLAIMS + 1)),
        lambda p: p.update(missing_data=[1]),
        lambda p: p.update(needs_review="no"),
        lambda p: p["facts"][0].update(source="x"),
        lambda p: p["facts"][0].update(evidence_ids="E1"),
        lambda p: p["signals"][0].update(date_precision="week"),
        lambda p: p["signals"][0].update(event_date="14.09.2026"),
        lambda p: p["scoring_inputs"].pop("contact_level"),
        lambda p: p["scoring_inputs"]["segment_match"].update(value="yes"),
        lambda p: p["scoring_inputs"]["budget_evidence_level"].update(value="big"),
        lambda p: p["offer"].update(price="100"),
    ],
    ids=[
        "old-schema",
        "other-lead",
        "other-company",
        "extra-field",
        "no-offer",
        "bad-icp",
        "bad-product",
        "empty-summary",
        "long-summary",
        "too-many-facts",
        "too-many-signals",
        "too-many-claims",
        "missing-not-string",
        "review-not-bool",
        "fact-extra-field",
        "ids-not-list",
        "bad-precision",
        "bad-date",
        "missing-feature",
        "bad-boolean",
        "bad-budget",
        "offer-extra-field",
    ],
)
def test_malformed_payload_is_a_schema_error(change):
    with pytest.raises(SchemaError):
        parse(_broken(change))


@pytest.mark.parametrize("name", FEATURES[:6])
@pytest.mark.parametrize(
    "value", [[], {}, 1, 1.5, "yes"], ids=["list", "object", "int", "float", "word"]
)
def test_boolean_feature_of_another_type_is_a_schema_error(name, value):
    # Review A on #8: unhashable values used to raise TypeError instead of SchemaError.
    payload = load("valid")
    payload["scoring_inputs"][name]["value"] = value
    with pytest.raises(SchemaError):
        parse(payload)


def test_too_many_evidence_refs_is_a_schema_error():
    payload = load("valid")
    payload["facts"][0]["evidence_ids"] = ["E1"] * 11
    with pytest.raises(SchemaError):
        parse(payload)


def test_extra_field_names_written_by_the_model_are_not_echoed():
    contact = "ivan.petrov@mail.ru +79161234567"
    with pytest.raises(SchemaError) as exc:
        parse(_broken(lambda p: p.update({contact: 1})))
    assert "ivan.petrov" not in str(exc.value) and "недопустимых значений: 1" in str(exc.value)


def test_problems_do_not_echo_invented_references():
    payload = load("valid")
    payload["facts"][0]["evidence_ids"] = ["Иван Петров +7 916 123-45-67", "E9"]
    payload["relevant_case_claim_ids"] = ["auto_crm.experience:v1", "Позвоните Ивану"]
    problems = " ".join(parse(payload).problems)
    assert "Иван" not in problems and "916" not in problems
    assert "E9" in problems


def test_not_an_object_is_a_schema_error():
    with pytest.raises(SchemaError):
        parse(["research"])


def test_schema_error_does_not_echo_the_payload():
    secret = "ivan.petrov@example.com"
    with pytest.raises(SchemaError) as exc:
        parse(_broken(lambda p: p.update(icp=secret)))
    assert secret not in str(exc.value)


# --- Research invariants -------------------------------------------------------------------


def test_research_requires_every_feature():
    research = parse(load("valid"))
    features = dict(research.features)
    features.pop("contact_level")
    with pytest.raises(ValueError):
        Research(**{**_fields(research), "features": features})


def test_research_with_problems_cannot_skip_review():
    research = parse(load("needs_review"))
    with pytest.raises(ValueError):
        Research(**{**_fields(research), "needs_review": False})


def test_parsed_result_is_read_only():
    research = parse(load("valid"))
    with pytest.raises(TypeError):
        research.features["segment_match"] = None


def test_parsing_does_not_change_the_payload():
    payload = load("needs_review")
    before = copy.deepcopy(payload)
    parse(payload)
    assert payload == before


def _fields(research: Research) -> dict:
    return {name: getattr(research, name) for name in Research.__dataclass_fields__}
