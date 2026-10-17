"""Case claims registry and the approved-wording rule. (S2-03, T-07)

The rules are tested on a fixed registry, tests/fixtures/case_claims_rules.json, which keeps a
planned case and unconfirmed markers: the real data/case_claims.json changes with the business
(17.10 all four cases are done) and is checked separately at the end of this file.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from hunter.domain.cases import (
    Case,
    CaseClaim,
    CaseRegistry,
    ClaimStatus,
    MentionRule,
    ViolationCode,
    check_case_text,
    claim_from_mapping,
    normalize_text,
    registry_from_mapping,
)

RULES_FILE = Path(__file__).resolve().parent / "fixtures" / "case_claims_rules.json"
DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "case_claims.json"
FILE_REGISTRY = registry_from_mapping(json.loads(RULES_FILE.read_text(encoding="utf-8")))
# The file holds proposals; tests work with the same wordings as if the owner approved them.
REGISTRY = CaseRegistry(
    cases=FILE_REGISTRY.cases,
    claims=tuple(dataclasses.replace(claim, approved_by="owner") for claim in FILE_REGISTRY.claims),
)
BY_KEY = {claim.claim_key: claim for claim in REGISTRY.claims}
ASSISTANT = BY_KEY["claims_assistant.status"]
AUTO_CRM = BY_KEY["auto_crm.experience"]
AUTO_CRM_BUILT = BY_KEY["auto_crm.built"]
CAREERMATE = BY_KEY["careermate.launch"]
NBSP = chr(0x00A0)
NARROW_NBSP = chr(0x202F)
ZERO_WIDTH_SPACE = chr(0x200B)
GREETING = "Здравствуйте, Анна!"
QUESTION = "Как у вас сейчас устроена претензионная работа с должниками?"


def codes(text: str, *attached: CaseClaim, registry: CaseRegistry = REGISTRY) -> list:
    found = check_case_text(text, registry=registry, attached_ids=[c.id for c in attached])
    return [violation.code for violation in found]


# --- Messages written the way docs 05 and the playbook write them pass --------------------

DOC_05_EXISTING_BUSINESS = (
    "Анна, увидел, что «Автодом» открыл ещё два сервиса.\n\n"
    "Стало интересно, как сейчас устроена запись клиентов: есть единая система или часть "
    "операций выполняется вручную?\n\n"
    "У нас есть опыт разработки CRM для автомобильного бизнеса. Можем разобрать процесс и "
    "предложить ограниченный первый этап автоматизации.\n\n"
    "Такой разговор был бы сейчас полезен?"
)
DOC_05_FOUNDER = (
    "Олег, увидел запуск waitlist у «Ромашки». На каком этапе сейчас продукт: проверяете "
    "сценарий или уже собираете первую рабочую версию?\n\n"
    "Мы внешняя продуктовая команда: проектируем MVP, делаем UX/UI и разработку. В CareerMate "
    "прошли путь от идеи до запуска AI-продукта.\n\n"
    "Если техническая реализация сейчас актуальна, готов коротко обсудить минимальный объём "
    "первого запуска."
)
PLAYBOOK_10 = (
    "Сергей, увидел, что вы открываете ещё два сервиса.\n\n"
    "Мы разрабатывали CRM для автомобильного бизнеса, поэтому стало интересно, как у вас "
    "сейчас устроены запись, распределение клиентов и контроль работы менеджеров между "
    "точками.\n\n"
    "Мы небольшой product/dev состав: сами проектируем процесс, делаем UX и разрабатываем "
    "систему, без передачи между несколькими подрядчиками.\n\n"
    "Если вопрос автоматизации сейчас актуален, могу показать, как мы решали похожую задачу."
)
ASSISTANT_AS_WORK_IN_PROGRESS = (
    f"{GREETING}\n\nВижу, что у вас растёт отдел взыскания.\n\n{ASSISTANT.claim_text}\n\n"
    "Если тема актуальна, предложу короткий созвон."
)


@pytest.mark.parametrize(
    ("text", "attached"),
    [
        (DOC_05_EXISTING_BUSINESS, (AUTO_CRM,)),
        (DOC_05_FOUNDER, (CAREERMATE,)),
        (PLAYBOOK_10, (AUTO_CRM_BUILT,)),
        (ASSISTANT_AS_WORK_IN_PROGRESS, (ASSISTANT,)),
    ],
    ids=["doc05-existing-business", "doc05-founder", "playbook-10", "assistant-in-progress"],
)
def test_template_messages_pass(text, attached):
    assert codes(text, *attached) == []


def test_two_cases_in_one_message_pass():
    text = f"{AUTO_CRM.claim_text}. Можем показать процесс.\n\n{ASSISTANT.claim_text}"
    assert codes(text, AUTO_CRM, ASSISTANT) == []


# --- Planned work presented as done is blocked (T-07, review 2) ---------------------------

REVIEW_2_PHRASES = [
    "Помощник претензионщика в разработке, но уже умеет выгружать отчёты в Excel.",
    "Мы делаем помощника для взыскания долгов, он в разработке. "
    "Уже умеет приоритизировать должников по Excel.",
    "Мы разрабатываем помощника претензионщика. Он уже сокращает время юристов вдвое.",
    "У нас есть помощник для взыскания: бот, который ранжирует дебиторов по риску, "
    "в разработке только отчёты.",
]
REVIEW_2_IDS = ["already-exports", "already-prioritises", "next-sentence", "only-reports-left"]


@pytest.mark.parametrize("text", REVIEW_2_PHRASES, ids=REVIEW_2_IDS)
def test_review_2_phrases_are_blocked(text):
    assert ViolationCode.CASE_MENTION in codes(text)
    assert ViolationCode.CASE_MENTION in codes(text, ASSISTANT)


@pytest.mark.parametrize(
    "text",
    [
        f"{ASSISTANT.claim_text.rstrip('.')}, и он уже выгружает отчёты в Excel.",
        f"Вот что у нас уже работает: {ASSISTANT.claim_text}",
        f"Мы уже сделали такое для юристов:\n{ASSISTANT.claim_text}",
        f"Готовый продукт — {ASSISTANT.claim_text}",
    ],
    ids=["same-sentence-addition", "lead-in", "lead-in-on-line-above", "dash-lead-in"],
)
def test_planned_wording_must_stand_alone(text):
    assert ViolationCode.PLANNED_NOT_ALONE in codes(text, ASSISTANT)


@pytest.mark.parametrize(
    "follow_up",
    [
        "Он уже сокращает время юристов вдвое.",
        "Его используют три юридические фирмы.",
        "Этот бот экономит до десяти часов в неделю.",
        "Они уже проверяют должников автоматически.",
        "Уже сокращает время юристов вдвое.",
    ],
    ids=["he", "his", "this-bot", "they", "subject-left-out"],
)
def test_planned_case_cannot_be_continued(follow_up):
    for separator in (" ", "\n\n"):
        assert ViolationCode.CONTINUES_CASE in codes(
            f"{ASSISTANT.claim_text}{separator}{follow_up}", ASSISTANT
        )


@pytest.mark.parametrize(
    "text",
    [
        f"{ASSISTANT.claim_text}\n\nExcel-отчёты уже выгружаются.",
        f"{ASSISTANT.claim_text} Проверка по ИНН работает.",
        f"{GREETING}\n\n{ASSISTANT.claim_text}\n\nДанные Checko подтягиваются автоматически.",
        f"{ASSISTANT.claim_text}\n\nРасчёты по каждому должнику занимают секунды.",
    ],
    ids=["excel-next-paragraph", "inn-next-sentence", "checko", "calculations"],
)
def test_planned_features_cannot_appear_next_to_the_case(text):
    assert ViolationCode.NOT_CONFIRMED in codes(text, ASSISTANT)


def test_unconfirmed_markers_apply_only_when_the_case_is_quoted():
    assert codes("Вижу, что отчёты по должникам у вас в Excel.") == []
    assert codes(f"{AUTO_CRM.claim_text}. Как вы сейчас собираете отчёты?", AUTO_CRM) == []


def test_inn_is_a_whole_word():
    text = f"{ASSISTANT.claim_text}\n\nЛюбим инновации в юридической работе."
    assert codes(text, ASSISTANT) == []


def test_careermate_contest_needs_a_source_first():
    text = f"{CAREERMATE.claim_text} и заняли 20-е место в конкурсе «Стартап как диплом»."
    assert codes(text, CAREERMATE) == [ViolationCode.NOT_CONFIRMED]


def test_changed_planned_wording_is_a_mention_not_a_quote():
    edited = ASSISTANT.claim_text.replace("находятся в разработке", "уже готовы")
    assert codes(edited, ASSISTANT) == [ViolationCode.CASE_MENTION]


# --- Done cases: only the approved wording, nothing invented around it --------------------


@pytest.mark.parametrize(
    "text",
    [
        "Разрабатываем CRM для бизнеса, у нас есть готовый помощник претензионщика.",
        "У нас есть готовый продукт — помощник претензионщика с Excel-отчётом.",
        "Мы сделали сервис проверки должников.",
        "Наш бот для взыскания уже работает у трёх клиентов.",
        "Делали CRM для автосервисов.",
        "Наш кейс — CareerMate, платформа карьерной аналитики.",
    ],
    ids=[
        "review-1-one-sentence",
        "ready-product",
        "made-service",
        "bot-in-production",
        "done-case-paraphrased",
        "case-by-name",
    ],
)
def test_cases_outside_an_approved_wording_are_blocked(text):
    assert codes(text) == [ViolationCode.CASE_MENTION]


def test_done_case_cannot_be_continued():
    text = f"{AUTO_CRM.claim_text}. Она уже ведёт запись клиентов в десяти сетях."
    assert codes(text, AUTO_CRM) == [ViolationCode.CONTINUES_CASE]


def test_another_case_named_next_to_a_wording_is_blocked():
    text = f"{AUTO_CRM.claim_text} и помощника для взыскания долгов."
    found = check_case_text(text, registry=REGISTRY, attached_ids=[AUTO_CRM.id])
    assert [(v.code, v.case_key) for v in found] == [
        (ViolationCode.CASE_MENTION, "claims_assistant")
    ]


def test_quoted_but_not_attached_claim_is_blocked():
    assert codes(ASSISTANT.claim_text) == [ViolationCode.CASE_NOT_ATTACHED]
    assert codes(DOC_05_EXISTING_BUSINESS, ASSISTANT) == [ViolationCode.CASE_NOT_ATTACHED]


# --- Text about the prospect is not about our cases ---------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        QUESTION,
        "У вас в отделе взыскания много ручной сверки по дебиторам — это видно по вакансии.",
        "Можем автоматизировать расстановку приоритетов по должникам за две недели.",
        "Вижу, что претензионной работы стало больше после запуска второго филиала.",
        "Работы по вашему автосервису можно начать с записи клиентов.",
        "Вам пригодилась бы CRM для вашего автосервиса.",
        "Сделаем приложение для партнёров вашей сети.",
    ],
    ids=[
        "question",
        "their-department",
        "offer-without-product",
        "their-growth",
        "raboty-is-not-bot",
        "offer-crm-to-auto-service",
        "partners-are-not-couples",
    ],
)
def test_prospect_context_is_not_blocked(text):
    assert codes(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "Мы разрабатываем помощника юристам. Он уже ранжирует должников.",
        "Мы делаем помощника юристам. Уже умеет ранжировать должников.",
        "Наш помощник\nдля взыскания долгов",
        "Мы делаем вот что:\nбота, который сам ведёт претензионную работу.",
        f"Наш помо{ZERO_WIDTH_SPACE}щник претензионщика готов.",
        "Запустили чат-бот для работы с дебиторкой.",
        "Мы делаем помощника юристам. Который сам ранжирует должников.",
    ],
    ids=[
        "pronoun",
        "subject-left-out",
        "line-break",
        "after-colon",
        "zero-width",
        "hyphen",
        "relative-pronoun",
    ],
)
def test_object_and_topic_split_across_sentences_or_lines_are_blocked(text):
    assert codes(text) == [ViolationCode.CASE_MENTION]


@pytest.mark.parametrize(
    "text",
    [
        "У вас много претензионной работы. Мы делаем MVP и внутренние инструменты для бизнеса.",
        "Мы делаем MVP и внутренние сервисы.\nСудя по вакансии, у вас растёт отдел взыскания.",
        "Они у вас работают с должниками вручную?",
        f"Мы делаем разных помощников\n\n{AUTO_CRM.claim_text}.\n\nДолжники — отдельная тема.",
    ],
    ids=["neighbour-sentences", "neighbour-lines", "pronoun-about-their-team", "across-a-quote"],
)
def test_separate_statements_are_not_glued_together(text):
    assert codes(text, AUTO_CRM) == []


# --- Wording matching ignores typography, fragments show the original text ---------------


@pytest.mark.parametrize(
    "wording",
    [
        ASSISTANT.claim_text.upper(),
        ASSISTANT.claim_text.replace(" ", NBSP),
        ASSISTANT.claim_text.replace(" ", NARROW_NBSP),
        ASSISTANT.claim_text.replace("реализована", "реализована "),
        f"«{ASSISTANT.claim_text}»",
        ASSISTANT.claim_text.rstrip("."),
        ASSISTANT.claim_text.replace("-", "—"),
    ],
    ids=["upper", "nbsp", "narrow-nbsp", "double-space", "quoted", "no-final-dot", "long-dash"],
)
def test_typography_does_not_change_the_wording(wording):
    assert codes(f"{GREETING}\n{wording}\nЕсли актуально, созвонимся?", ASSISTANT) == []


def test_yo_and_ye_are_the_same_wording():
    registry = CaseRegistry(
        cases=(Case("shop", MentionRule(phrases=("учёт для магазина",))),),
        claims=(
            CaseClaim(
                claim_key="shop.done",
                case_key="shop",
                claim_text="Сделали учёт для магазина",
                status=ClaimStatus.VERIFIED,
                external_allowed=True,
                claim_version=1,
                approved_by="owner",
            ),
        ),
    )
    assert codes("Сделали учет для магазина.", registry.claims[0], registry=registry) == []


def test_violation_names_the_case_and_the_statement():
    text = f"{GREETING} Мы сделали сервис проверки должников. {QUESTION}"
    [violation] = check_case_text(text, registry=REGISTRY)
    assert violation.case_key == "claims_assistant"
    assert violation.fragment == "Мы сделали сервис проверки должников."


def test_statement_is_reported_whole_with_the_original_wording():
    text = f"Вот что у нас уже работает: {ASSISTANT.claim_text.replace(' ', NBSP)}"
    [violation] = check_case_text(text, registry=REGISTRY, attached_ids=[ASSISTANT.id])
    assert violation.code is ViolationCode.PLANNED_NOT_ALONE
    assert violation.fragment == text


def test_normalize_text():
    assert normalize_text(f"  «Ёлка»{NBSP}—  ГОТОВО ") == "елка - готово"


# --- Registry -----------------------------------------------------------------------------


def test_rules_fixture_keeps_a_planned_case():
    planned = [c for c in FILE_REGISTRY.claims if c.status is ClaimStatus.PLANNED]
    assert [c.id for c in planned] == ["claims_assistant.status:v1"]
    assert ASSISTANT.status is ClaimStatus.PLANNED


@pytest.mark.parametrize(
    "changes",
    [{"status": ClaimStatus.WITHDRAWN}, {"external_allowed": False}, {"approved_by": None}],
    ids=["withdrawn", "internal-only", "not-approved"],
)
def test_unusable_claim_cannot_be_attached(changes):
    claim = dataclasses.replace(AUTO_CRM, **changes)
    registry = CaseRegistry(cases=REGISTRY.cases, claims=(claim,))
    assert codes(DOC_05_EXISTING_BUSINESS, claim, registry=registry) == [
        ViolationCode.CLAIM_NOT_USABLE
    ]


def test_unknown_or_outdated_claim_id_cannot_be_attached():
    found = check_case_text(QUESTION, registry=REGISTRY, attached_ids=["auto_crm.built:v7"])
    assert [(v.code, v.fragment) for v in found] == [
        (ViolationCode.CLAIM_NOT_USABLE, "auto_crm.built:v7")
    ]


def test_withdrawn_case_still_must_not_be_mentioned():
    withdrawn = dataclasses.replace(ASSISTANT, status=ClaimStatus.WITHDRAWN)
    registry = CaseRegistry(cases=REGISTRY.cases, claims=(withdrawn,))
    assert codes(REVIEW_2_PHRASES[0], registry=registry) == [ViolationCode.CASE_MENTION]


def _registry(*claims: CaseClaim, cases: tuple[Case, ...] = REGISTRY.cases) -> CaseRegistry:
    return CaseRegistry(cases=cases, claims=claims)


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda: _registry(dataclasses.replace(AUTO_CRM, case_key="nope")), "unknown case"),
        (lambda: _registry(AUTO_CRM, AUTO_CRM), "claim id must be unique"),
        (
            lambda: _registry(AUTO_CRM, dataclasses.replace(AUTO_CRM, claim_key="copy")),
            "same wording",
        ),
        (
            lambda: _registry(ASSISTANT, cases=(Case("claims_assistant"),)),
            "planned claim needs markers",
        ),
        (
            lambda: _registry(dataclasses.replace(AUTO_CRM, claim_text="Делали учёт складов")),
            "do not recognise",
        ),
        (lambda: _registry(cases=(Case("x"), Case("x"))), "case_key must be unique"),
        (
            lambda: _registry(
                ASSISTANT,
                cases=(
                    Case(
                        "claims_assistant",
                        REGISTRY.cases[3].mention,
                        not_confirmed=("telegram-бот*",),
                    ),
                ),
            ),
            "names what is not confirmed",
        ),
        (lambda: Case("x", not_confirmed=("ии",)), "at least 3 characters"),
    ],
    ids=[
        "unknown-case",
        "duplicate-id",
        "duplicate-wording",
        "planned-without-markers",
        "markers-miss-wording",
        "duplicate-case",
        "wording-names-unconfirmed",
        "short-unconfirmed-marker",
    ],
)
def test_invalid_registry_is_not_created(build, message):
    with pytest.raises(ValueError, match=message):
        build()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"claim_text": "  "}, "claim text is required"),
        ({"claim_text": "Первая строка.\nВторая строка."}, "one paragraph"),
        ({"claim_version": 0}, "starts at 1"),
        ({"case_key": ""}, "case_key is required"),
        ({"claim_key": " "}, "claim_key is required"),
    ],
    ids=["empty-text", "two-paragraphs", "version-zero", "empty-case", "empty-key"],
)
def test_invalid_claim_is_not_created(changes, message):
    with pytest.raises(ValueError, match=message):
        dataclasses.replace(AUTO_CRM, **changes)


@pytest.mark.parametrize(
    ("rule", "message"),
    [
        ({"objects": ("бот",)}, "give both or neither"),
        ({"topics": ("долг",)}, "give both or neither"),
        ({"phrases": ("ии",)}, "at least 3 characters"),
    ],
    ids=["objects-only", "topics-only", "too-short"],
)
def test_invalid_mention_rule_is_not_created(rule, message):
    with pytest.raises(ValueError, match=message):
        MentionRule(**rule)


CLAIM = {
    "claim_key": "x.done",
    "case_key": "x",
    "claim_text": "Текст",
    "status": "verified",
    "external_allowed": True,
}


@pytest.mark.parametrize(
    ("item", "message"),
    [
        ({k: v for k, v in CLAIM.items() if k != "external_allowed"}, "missing field"),
        ({k: v for k, v in CLAIM.items() if k != "claim_key"}, "missing field"),
        ({**CLAIM, "status": "done"}, "not a valid ClaimStatus"),
        ({**CLAIM, "external_allowed": 1}, "must be true or false"),
    ],
    ids=["no-permission-flag", "no-claim-key", "unknown-status", "flag-not-bool"],
)
def test_malformed_claim_entry_is_rejected(item, message):
    with pytest.raises(ValueError, match=message):
        claim_from_mapping(item)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"cases": [], "claims": [], "extra": 1}, "unknown fields"),
        ({"cases": {}, "claims": []}, "must be a list of objects"),
        ({"cases": [{"case_key": "x", "words": []}], "claims": []}, "unknown fields"),
        ({"cases": [{"mention": {}}], "claims": []}, "missing field"),
        ({"cases": [{"case_key": "x", "mention": {"words": ["a"]}}], "claims": []}, "unknown"),
        ({"cases": [{"case_key": "x", "mention": {"phrases": "текст"}}], "claims": []}, "list"),
        ({"cases": [{"case_key": "x", "not_confirmed": "excel"}], "claims": []}, "list"),
    ],
    ids=[
        "extra-field",
        "cases-not-list",
        "case-extra-field",
        "case-without-key",
        "unknown-marker-kind",
        "markers-not-list",
        "unconfirmed-not-list",
    ],
)
def test_malformed_registry_file_is_rejected(data, message):
    with pytest.raises(ValueError, match=message):
        registry_from_mapping(data)


def test_claim_versions_give_distinct_ids():
    assert claim_from_mapping(CLAIM).id == "x.done:v1"
    assert claim_from_mapping(CLAIM, claim_version=3).id == "x.done:v3"


def test_case_without_markers_is_allowed_for_done_claims():
    registry = registry_from_mapping({"cases": [{"case_key": "x"}], "claims": [CLAIM]})
    assert registry.cases[0].mention.is_empty


# --- The real registry, data/case_claims.json -------------------------------------------------

DATA = registry_from_mapping(json.loads(DATA_FILE.read_text(encoding="utf-8")))
APPROVED_DATA = CaseRegistry(
    cases=DATA.cases,
    claims=tuple(dataclasses.replace(claim, approved_by="owner") for claim in DATA.claims),
)
DATA_BY_KEY = {claim.claim_key: claim for claim in APPROVED_DATA.claims}


def test_data_registry_has_four_done_cases_with_sources():
    assert {case.case_key for case in DATA.cases} == {
        "careermate",
        "couples_app",
        "auto_crm",
        "claims_assistant",
    }
    # Owner, 17.10: the claims assistant is finished, no case is planned any more.
    assert all(claim.status is ClaimStatus.OWNER_REPORTED for claim in DATA.claims)
    assert all(claim.source_ref for claim in DATA.claims)
    assert all(claim.external_allowed for claim in DATA.claims)


def test_contest_claim_keeps_its_primary_source():
    # Doc 01: the contest result is used externally only with the publication attached.
    award = DATA_BY_KEY["careermate.award"]
    assert "https://" in award.source_ref
    assert "20-е место" in award.claim_text


def test_data_registry_names_no_customer():
    # Every customer is under NDA: wordings name the industry, never the company.
    text = " ".join(claim.claim_text for claim in DATA.claims).lower()
    assert "заказчик" not in text.replace("задачу заказчика", "")
    assert "«" not in text.replace("«стартап как диплом»", "")


@pytest.mark.parametrize("claim_key", sorted(DATA_BY_KEY))
def test_every_data_wording_passes_alone(claim_key):
    claim = DATA_BY_KEY[claim_key]
    text = f"{GREETING} {claim.claim_text}. {QUESTION}"
    assert check_case_text(text, registry=APPROVED_DATA, attached_ids=[claim.id]) == ()


@pytest.mark.parametrize(
    ("text", "claim_key"),
    [
        (
            "Здравствуйте, Иван! Увидел, что вы открыли второй автосервис. У нас есть опыт "
            "разработки CRM для автомобильного бизнеса, поэтому стало интересно, как у вас "
            "устроены запись и распределение заявок между мастерами?",
            "auto_crm.experience",
        ),
        (
            "Здравствуйте, Анна! Видел ваш запуск на Product Hunt. В CareerMate прошли путь от "
            "идеи до запуска AI-продукта, поэтому интересно, что сейчас мешает вам расти?",
            "careermate.launch",
        ),
        (
            "Здравствуйте, Анна! Мы сделали и сдали сервис для оценки и приоритизации должников: "
            "он проверяет как одну компанию, так и сразу пачку, принимает данные о долгах и "
            "переговорах и показывает, с кем работать в первую очередь. Как у вас сейчас "
            "устроена работа с дебиторкой?",
            "claims_assistant.scope",
        ),
    ],
    ids=["doc-05-existing-business", "doc-05-founder", "debtors-service"],
)
def test_data_templates_pass(text, claim_key):
    claim = DATA_BY_KEY[claim_key]
    assert check_case_text(text, registry=APPROVED_DATA, attached_ids=[claim.id]) == ()


@pytest.mark.parametrize(
    "text",
    [
        "Наш сервис для оценки должников уже сэкономил клиенту сотни часов.",
        "Делали CRM для автосервисов с онлайн-оплатой.",
        "CareerMate — лучший карьерный сервис в России.",
    ],
    ids=["unmeasured-savings", "paraphrased-crm", "careermate-superlative"],
)
def test_data_registry_still_blocks_free_text_about_cases(text):
    assert check_case_text(f"{GREETING} {text}", registry=APPROVED_DATA) != ()


def test_unmeasured_savings_blocked_next_to_the_quoted_service():
    claim = DATA_BY_KEY["claims_assistant.monitoring"]
    text = f"{claim.claim_text}. Это экономит юристам часы каждый день."
    found = check_case_text(text, registry=APPROVED_DATA, attached_ids=[claim.id])
    assert ViolationCode.NOT_CONFIRMED in {v.code for v in found}


def test_data_wordings_are_approved_by_the_owner():
    # The owner approved every exact text on 17.10.
    assert all(claim.usable_in_messages for claim in DATA.claims)
