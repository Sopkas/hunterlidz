"""Case claims registry and the verbatim-only rule. (S2-03, T-07)"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hunter.domain.cases import (
    CaseClaim,
    ClaimStatus,
    MentionRule,
    ViolationCode,
    check_case_text,
    claim_from_mapping,
    normalize_text,
)

REGISTRY_FILE = Path(__file__).resolve().parents[1] / "data" / "case_claims.json"
REGISTRY = tuple(
    claim_from_mapping(item) for item in json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
)
BY_KEY = {claim.case_key: claim for claim in REGISTRY}
ASSISTANT = BY_KEY["claims_assistant"]
AUTO_CRM = BY_KEY["auto_crm"]
NBSP = chr(0x00A0)
NARROW_NBSP = chr(0x202F)
GREETING = "Здравствуйте, Анна!"
QUESTION = "Как у вас сейчас устроена претензионная работа с должниками?"


def codes(text: str, *attached: CaseClaim) -> list[ViolationCode]:
    found = check_case_text(text, registry=REGISTRY, attached_ids=[c.id for c in attached])
    return [violation.code for violation in found]


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
def test_review_2_phrases_block_model_text(text):
    assert ViolationCode.CASE_MENTION in codes(text)


@pytest.mark.parametrize("text", REVIEW_2_PHRASES, ids=REVIEW_2_IDS)
def test_review_2_phrases_block_even_with_the_claim_attached(text):
    assert ViolationCode.CASE_MENTION in codes(text, ASSISTANT)


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
def test_own_cases_outside_the_registry_wording_are_blocked(text):
    assert codes(text) == [ViolationCode.CASE_MENTION]


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


def test_allowed_wording_in_its_own_paragraph_passes():
    text = f"{GREETING}\n\n{ASSISTANT.claim_text}\n\n{QUESTION}"
    assert codes(text, ASSISTANT) == []


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
    assert codes(f"{GREETING}\n{wording}", ASSISTANT) == []


def test_yo_and_ye_are_the_same_wording():
    claim = CaseClaim(
        id="shop:v1",
        case_key="shop",
        claim_text="Сделали учёт для магазина.",
        status=ClaimStatus.VERIFIED,
        external_allowed=True,
        claim_version=1,
        mention=MentionRule(phrases=("учёт для магазина",)),
    )
    found = check_case_text("Сделали учет для магазина", registry=[claim], attached_ids=["shop:v1"])
    assert found == ()


def test_wording_with_extra_text_in_the_same_paragraph_is_blocked():
    text = f"{ASSISTANT.claim_text} Он уже сокращает время юристов вдвое."
    assert ViolationCode.CASE_MENTION in codes(text, ASSISTANT)


@pytest.mark.parametrize(
    "follow_up",
    [
        "Он уже сокращает время юристов вдвое.",
        "Его используют три юридические фирмы.",
        "Этот бот экономит до десяти часов в неделю.",
        "Они уже проверяют должников автоматически.",
    ],
    ids=["he", "his", "this-bot", "they"],
)
def test_next_paragraph_cannot_continue_the_case(follow_up):
    text = f"{ASSISTANT.claim_text}\n\n{follow_up}"
    assert ViolationCode.CONTINUES_CASE in codes(text, ASSISTANT)


def test_a_new_thought_after_the_case_passes():
    text = f"{ASSISTANT.claim_text}\n\nЕсли тема актуальна, предложу короткий созвон."
    assert codes(text, ASSISTANT) == []


def test_continuation_rule_applies_only_right_after_a_case():
    text = f"{GREETING}\n\nОни у вас работают в две смены?\n\n{ASSISTANT.claim_text}"
    assert codes(text, ASSISTANT) == []


def test_changed_wording_is_a_mention_not_a_quote():
    edited = ASSISTANT.claim_text.replace("находятся в разработке", "уже готовы")
    assert ViolationCode.CASE_MENTION in codes(edited, ASSISTANT)


def test_quoted_but_not_attached_claim_is_blocked():
    assert codes(ASSISTANT.claim_text) == [ViolationCode.CASE_NOT_ATTACHED]
    assert codes(ASSISTANT.claim_text, AUTO_CRM) == [ViolationCode.CASE_NOT_ATTACHED]


def test_two_cases_each_in_its_own_paragraph_pass():
    text = f"{AUTO_CRM.claim_text}\n\n{ASSISTANT.claim_text}"
    assert codes(text, AUTO_CRM, ASSISTANT) == []


def _variant(claim: CaseClaim, **changes: object) -> CaseClaim:
    fields = {name: getattr(claim, name) for name in CaseClaim.__slots__}
    return CaseClaim(**{**fields, **changes})


@pytest.mark.parametrize(
    "changes",
    [{"status": ClaimStatus.WITHDRAWN}, {"external_allowed": False}],
    ids=["withdrawn", "internal-only"],
)
def test_unusable_claim_cannot_be_attached(changes):
    claim = _variant(AUTO_CRM, **changes)
    found = check_case_text(claim.claim_text, registry=[claim], attached_ids=[claim.id])
    assert [v.code for v in found] == [ViolationCode.CLAIM_NOT_USABLE]


def test_unknown_or_outdated_claim_id_cannot_be_attached():
    found = check_case_text(QUESTION, registry=REGISTRY, attached_ids=["claims_assistant:v7"])
    assert [(v.code, v.fragment) for v in found] == [
        (ViolationCode.CLAIM_NOT_USABLE, "claims_assistant:v7")
    ]


def test_withdrawn_case_still_must_not_be_mentioned():
    withdrawn = _variant(ASSISTANT, status=ClaimStatus.WITHDRAWN)
    found = check_case_text(REVIEW_2_PHRASES[0], registry=[withdrawn])
    assert [v.code for v in found] == [ViolationCode.CASE_MENTION]


def test_violation_names_the_case_and_the_sentence():
    text = f"{GREETING} Мы сделали сервис проверки должников. {QUESTION}"
    [violation] = check_case_text(text, registry=REGISTRY)
    assert violation.case_key == "claims_assistant"
    assert violation.fragment == "Мы сделали сервис проверки должников."


def test_registry_file_matches_doc_01():
    assert {c.case_key for c in REGISTRY} == {
        "careermate",
        "couples_app",
        "auto_crm",
        "claims_assistant",
    }
    assert ASSISTANT.status is ClaimStatus.PLANNED and ASSISTANT.id == "claims_assistant:v1"
    assert ASSISTANT.claim_text == (
        "Разрабатываем помощника для приоритизации проверки должников. Сейчас реализована "
        "оболочка Telegram-бота; обработка данных, интеграции и аналитика находятся в разработке."
    )
    assert all(claim.usable_in_messages and not claim.mention.is_empty for claim in REGISTRY)


def test_planned_claim_requires_markers():
    with pytest.raises(ValueError, match="planned claim needs mention markers"):
        _variant(ASSISTANT, mention=MentionRule())


def test_markers_must_recognise_the_claims_own_wording():
    with pytest.raises(ValueError, match="do not recognise"):
        _variant(ASSISTANT, mention=MentionRule(phrases=("careermate",)))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"claim_text": "  "}, "claim text is required"),
        ({"claim_text": "Первая строка.\nВторая строка."}, "one paragraph"),
        ({"claim_version": 0}, "starts at 1"),
        ({"case_key": ""}, "case_key is required"),
    ],
    ids=["empty-text", "two-paragraphs", "version-zero", "empty-key"],
)
def test_invalid_claim_is_not_created(changes, message):
    with pytest.raises(ValueError, match=message):
        _variant(AUTO_CRM, **changes)


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


@pytest.mark.parametrize(
    ("item", "message"),
    [
        ({"case_key": "x", "claim_text": "Текст.", "status": "verified"}, "missing field"),
        (
            {"case_key": "x", "claim_text": "Текст.", "status": "done", "external_allowed": True},
            "not a valid ClaimStatus",
        ),
        (
            {"case_key": "x", "claim_text": "Текст.", "status": "verified", "external_allowed": 1},
            "must be true or false",
        ),
        (
            {
                "case_key": "x",
                "claim_text": "Текст.",
                "status": "verified",
                "external_allowed": True,
                "mention": {"words": ["текст"]},
            },
            "unknown fields",
        ),
        (
            {
                "case_key": "x",
                "claim_text": "Текст.",
                "status": "verified",
                "external_allowed": True,
                "mention": {"phrases": "текст"},
            },
            "expected a list of strings",
        ),
    ],
    ids=[
        "no-permission-flag",
        "unknown-status",
        "flag-not-bool",
        "unknown-marker-kind",
        "not-a-list",
    ],
)
def test_malformed_registry_entry_is_rejected(item, message):
    with pytest.raises(ValueError, match=message):
        claim_from_mapping(item)


def test_claim_without_markers_is_allowed_for_done_cases():
    claim = claim_from_mapping(
        {"case_key": "x", "claim_text": "Текст.", "status": "verified", "external_allowed": True}
    )
    assert claim.mention.is_empty and claim.id == "x:v1"
    assert (
        claim_from_mapping(
            {
                "case_key": "x",
                "claim_text": "Текст.",
                "status": "verified",
                "external_allowed": True,
            },
            claim_version=3,
        ).id
        == "x:v3"
    )


def test_normalize_text():
    assert normalize_text(f"  «Ёлка»{NBSP}—  ГОТОВО ") == "елка - готово"


ZERO_WIDTH_SPACE = chr(0x200B)


@pytest.mark.parametrize(
    "text",
    [
        "Мы разрабатываем помощника юристам. Он уже ранжирует должников.",
        "Мы делаем помощника юристам. Уже умеет ранжировать должников.",
        "Наш помощник\nдля взыскания долгов",
        "Мы делаем вот что:\nбота, который сам ведёт претензионную работу.",
        f"Наш помо{ZERO_WIDTH_SPACE}щник претензионщика готов.",
        "Запустили чат-бот для работы с дебиторкой.",
    ],
    ids=["pronoun", "subject-left-out", "line-break", "after-colon", "zero-width", "hyphen"],
)
def test_object_and_topic_split_across_sentences_or_lines_are_blocked(text):
    assert codes(text) == [ViolationCode.CASE_MENTION]


@pytest.mark.parametrize(
    "text",
    [
        "У вас много претензионной работы. Мы делаем MVP и внутренние инструменты для бизнеса.",
        "Мы делаем MVP и внутренние сервисы.\nСудя по вакансии, у вас растёт отдел взыскания.",
        "Они у вас работают с должниками вручную?",
    ],
    ids=["neighbour-sentences", "neighbour-lines", "pronoun-about-their-team"],
)
def test_separate_statements_are_not_glued_together(text):
    assert codes(text) == []


def test_statement_is_reported_whole():
    text = "Мы разрабатываем помощника юристам. Он уже ранжирует должников."
    [violation] = check_case_text(text, registry=REGISTRY)
    assert violation.fragment == text


def test_statement_does_not_cross_a_quoted_case():
    text = f"Мы делаем разных помощников\n\n{AUTO_CRM.claim_text}\n\nДолжники — отдельная тема."
    assert codes(text, AUTO_CRM) == []


def test_subject_left_out_after_a_case_is_blocked():
    text = f"{ASSISTANT.claim_text}\n\nУже сокращает время юристов вдвое."
    assert codes(text, ASSISTANT) == [ViolationCode.CONTINUES_CASE]


def test_lead_in_before_a_case_is_blocked():
    text = f"Мы уже сделали такое для юристов:\n{ASSISTANT.claim_text}"
    assert codes(text, ASSISTANT) == [ViolationCode.LEADS_INTO_CASE]


def test_lead_in_rule_looks_only_at_the_paragraph_right_before_the_case():
    text = f"Коротко о нас:\nДелаем MVP за две недели.\n\n{ASSISTANT.claim_text}"
    assert codes(text, ASSISTANT) == []
