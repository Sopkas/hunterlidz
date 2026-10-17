"""S3-03: first message draft — draft_v1, text checks, gates and the MessageVersion it makes."""

import json
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from hunter.application.drafting import DraftStatus, prepare_draft
from hunter.application.llm import LlmErrorCode, LlmResult, Tariff, Usage
from hunter.application.prompts import DRAFT_PROMPT_VERSION
from hunter.domain.cases import registry_from_mapping
from hunter.domain.drafts import body_blockers, parse_draft
from hunter.domain.entities import Channel, Contact, Evidence
from hunter.domain.messages import MessageKind
from hunter.domain.research import SchemaError, parse_research

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = registry_from_mapping(
    json.loads((ROOT / "data" / "case_claims.json").read_text("utf-8"))
)
CLAIM = "auto_crm.experience:v1"
CLAIM_TEXT = "У нас есть опыт разработки CRM для автомобильного бизнеса"
NOW = datetime(2026, 10, 18, 10, 0, tzinfo=timezone.utc)
FREE = Tariff(Decimal(0), Decimal(0), "RUB")
ALIASES = {"E1": "ev-site", "E2": "ev-news", "E3": "ev-vacancy"}
EVIDENCE = {
    eid: Evidence(
        id=eid,
        company_id="c-alpha",
        source_id=f"s-{eid}",
        claim="x",
        excerpt=f"Цитата {eid}",
        observed_at=NOW,
    )  # fmt: skip
    for eid in ALIASES.values()
}
PERSON = Contact(
    id="k1",
    company_id="c-alpha",
    channel=Channel.EMAIL,
    normalized_address="anna@alpha.example",
    role="операционный директор",
    verified_at=NOW,
)
BODY = (
    "Анна, увидел, что «Автодом» открыл ещё два сервиса.\n\n"
    "Стало интересно, как сейчас устроена запись клиентов: есть единая система "
    "или часть операций выполняется вручную?\n\n"
    f"{CLAIM_TEXT}. Можем разобрать процесс и показать варианты. Удобно созвониться на 20 минут?"
)


def research():
    payload = json.loads(
        (ROOT / "tests" / "fixtures" / "research_v1_1_valid.json").read_text("utf-8")
    )
    return parse_research(
        payload,
        lead_id="l-alpha",
        company_id="c-alpha",
        evidence_aliases=ALIASES,
        allowed_claim_ids=frozenset({CLAIM}),
        today=date(2026, 10, 18),
    )


def answer(body=BODY, evidence=("E1",), claims=(CLAIM,), needs_review=False, reasons=()):
    return {
        "schema_version": "draft_v1",
        "body": body,
        "evidence_ids": list(evidence),
        "case_claim_ids": list(claims),
        "needs_review": needs_review,
        "review_reasons": list(reasons),
    }


class Scripted:
    model_id = "scripted:draft"
    tariff = FREE

    def __init__(self, *answers):
        self.answers, self.requests = list(answers), []

    def complete_json(self, request):
        self.requests.append(request)
        data = self.answers.pop(0)
        if isinstance(data, LlmErrorCode):
            return LlmResult.failure(request, self.model_id, data, Usage(50, 0), FREE)
        return LlmResult.success(request, self.model_id, data, Usage(800, 200), FREE)


def draft(client, **overrides):
    args = dict(
        message_id="msg1",
        company_name="Автодом Сервис",
        research=research(),
        evidence=EVIDENCE,
        contact=PERSON,
        registry=REGISTRY,
        client=client,
        created_by="worker",
        now=NOW,
    )
    args.update(overrides)
    return prepare_draft(**args)


# --- Gates --------------------------------------------------------------------------------


def test_no_contact_no_draft_and_no_model_call():
    client = Scripted()
    for contact in (None, replace(PERSON, verified_at=None)):
        outcome = draft(client, contact=contact)
        assert outcome.status is DraftStatus.NO_CONTACT and outcome.version is None
    assert client.requests == []


def test_no_traceable_fact_goes_to_review_without_a_text():
    client = Scripted()
    outcome = draft(client, research=replace(research(), facts=(), needs_review=True))
    assert outcome.status is DraftStatus.NEEDS_REVIEW and client.requests == []


# --- The happy path -----------------------------------------------------------------------


def test_good_draft_becomes_a_version_ready_for_review():
    client = Scripted(answer())
    outcome = draft(client)
    assert outcome.status is DraftStatus.READY and outcome.blockers == ()
    version = outcome.version
    assert version.body == BODY
    assert version.recipient.normalized_address == "anna@alpha.example"
    assert version.case_claim_ids == (CLAIM,)
    assert version.evidence_ids == ("ev-site",)
    assert (outcome.usage, outcome.cost) == (Usage(800, 200), Decimal(0))


def test_request_carries_facts_hypothesis_offer_and_exact_claim():
    client = Scripted(answer())
    draft(client)
    request = client.requests[0]
    assert request.prompt_version == DRAFT_PROMPT_VERSION
    assert "операционный директор компании Автодом Сервис" in request.user
    assert "Цитата ev-site" in request.user
    assert f"{CLAIM}: {CLAIM_TEXT}" in request.user
    assert "до 900 символов" in request.user
    assert request.schema["properties"]["schema_version"] == {"const": "draft_v1"}


def test_only_the_claims_research_chose_are_offered():
    client = Scripted(answer(claims=()))
    draft(client, research=replace(research(), relevant_case_claim_ids=()))
    assert "нет разрешённых кейсов" in client.requests[0].user


# --- Blockers (doc 11 checks 6–7, T-07) ---------------------------------------------------


@pytest.mark.parametrize(
    ("body", "blocker"),
    [
        ("x" * 901, "длиннее 900"),
        ("Здравствуйте, {contact_name}! Видели ваш сайт.", "незаполненные поля"),
        ("Здравствуйте, [Имя]! Видели ваш сайт.", "незаполненные поля"),
        ("Видели новость E2 о вашем филиале.", "служебные ссылки"),
        (f"{CLAIM_TEXT} ({CLAIM}).", "служебные ссылки"),
        ("По данным 01-company-and-cases.md у нас есть опыт.", "служебные ссылки"),
        ("Делали CRM для автосервисов, можем показать.", "case_mention"),
        (f"{CLAIM_TEXT} на 300 автосалонов.", "not_confirmed"),
        (f"{CLAIM_TEXT}. Она уже окупилась за 3 месяца.", "continues_case"),
        ("   ", "нет текста"),
    ],
    ids=[
        "too-long", "placeholder", "bracket-name", "evidence-id", "claim-id", "doc-name",
        "paraphrased-case", "number-next-to-case", "continued-case", "blank",
    ],
)  # fmt: skip
def test_text_problems_block_approval(body, blocker):
    problems = body_blockers(
        body, kind=MessageKind.INITIAL, registry=REGISTRY, case_claim_ids=(CLAIM,)
    )
    assert any(blocker in p for p in problems), problems


def test_quoted_case_without_its_claim_id_is_blocked():
    problems = body_blockers(BODY, kind=MessageKind.INITIAL, registry=REGISTRY, case_claim_ids=())
    assert any("case_not_attached" in p for p in problems)


def test_draft_with_blockers_needs_review_but_keeps_the_version():
    outcome = draft(Scripted(answer(body="Делали CRM для автосервисов. Созвонимся?")))
    assert outcome.status is DraftStatus.NEEDS_REVIEW
    assert outcome.version is not None and outcome.blockers


def test_good_text_passes_every_check():
    assert (
        body_blockers(BODY, kind=MessageKind.INITIAL, registry=REGISTRY, case_claim_ids=(CLAIM,))
        == ()
    )


# --- draft_v1 parsing ---------------------------------------------------------------------


def test_model_asking_for_review_without_a_text():
    outcome = draft(Scripted(answer(body=None, needs_review=True, reasons=["нет роли ЛПР"])))
    assert outcome.status is DraftStatus.NEEDS_REVIEW and outcome.version is None
    assert outcome.review_reasons == ("нет роли ЛПР",)


def test_unoffered_references_are_dropped_and_need_review():
    parsed = parse_draft(
        answer(evidence=("E1", "E9"), claims=(CLAIM, "careermate.award:v1")),
        evidence_aliases={"E1": "ev-site"},
        allowed_claim_ids=frozenset({CLAIM}),
    )
    assert parsed.evidence_ids == ("ev-site",) and parsed.case_claim_ids == (CLAIM,)
    assert parsed.needs_review and len(parsed.review_reasons) == 2
    assert "E9" not in " ".join(parsed.review_reasons)  # counted, not echoed


@pytest.mark.parametrize(
    "change",
    [
        lambda a: a.update(schema_version="draft_v0"),
        lambda a: a.update(extra=1),
        lambda a: a.pop("review_reasons"),
        lambda a: a.update(body=["текст"]),
        lambda a: a.update(body="x" * 1501),
        lambda a: a.update(evidence_ids="E1"),
        lambda a: a.update(needs_review="no"),
        lambda a: a.update(review_reasons=["r"] * 6),
    ],
    ids=[
        "schema", "extra", "missing", "body-type", "hard-limit", "ids-type", "review-type",
        "reasons",
    ],
)  # fmt: skip
def test_malformed_draft_is_a_schema_error(change):
    payload = answer()
    change(payload)
    with pytest.raises(SchemaError):
        parse_draft(payload, evidence_aliases=ALIASES, allowed_claim_ids=frozenset({CLAIM}))


def test_one_format_correction_then_a_draft():
    client = Scripted({"oops": 1}, answer())
    outcome = draft(client)
    assert outcome.status is DraftStatus.READY and outcome.corrections == 1
    assert "не прошёл проверку формы" in client.requests[1].user
    assert outcome.usage == Usage(1600, 400)  # both calls are counted for the budget


def test_two_broken_answers_fail():
    outcome = draft(Scripted({"oops": 1}, {"oops": 2}))
    assert outcome.status is DraftStatus.FAILED and outcome.version is None


def test_provider_failure_is_an_outcome():
    outcome = draft(Scripted(LlmErrorCode.RATE_LIMITED))
    assert outcome.status is DraftStatus.FAILED and "rate_limited" in outcome.error
    assert outcome.usage == Usage(50, 0)
