"""S2-04: the OpenAI-compatible adapter, contact masking, settings and `hunter llm-check`.

No test reaches the network: the HTTP call goes through a fake transport.
"""

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from hunter.application.errors import ErrorCode, HunterError
from hunter.application.llm import LlmErrorCode, LlmRequest, LlmResult, LlmStatus, Tariff, Usage
from hunter.application.llm_privacy import MaskingClient, mask_contacts
from hunter.application.research_schema import research_json_schema
from hunter.domain.research import FEATURES, parse_research
from hunter.infrastructure.llm.openai_compatible import (
    HttpResponse,
    OpenAICompatibleClient,
    TransportTimeout,
    TransportUnavailable,
)
from hunter.presentation.cli import llm_cmd
from hunter.runtime.config import ConfigError, load_settings
from hunter.runtime.container import build_llm, llm_tariff

KEY = "sk-or-test-key-never-printed"
REQUEST = LlmRequest(
    call_id="run-1:1",
    prompt_version="research_v1",
    system="Правила",
    user="Задание",
    schema={"type": "object"},
    max_output_tokens=300,
)
FREE = Tariff(Decimal(0), Decimal(0), "RUB")
DB = "postgresql://hunter:hunter@localhost:5432/hunter"


class FakeTransport:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, dict(headers), json.loads(body), timeout))
        if self.error:
            raise self.error
        return self.response


def answer(content, finish="stop", usage=(120, 30), status=200, headers=None, **extra):
    payload = {
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": finish}
        ],
        "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]},
        **extra,
    }
    return HttpResponse(status, headers or {}, json.dumps(payload).encode())


def client(transport, **kwargs) -> OpenAICompatibleClient:
    args = dict(
        base_url="https://openrouter.ai/api/v1",
        model="qwen/qwen3-8b:free",
        api_key=KEY,
        tariff=FREE,
        transport=transport,
        provider="openrouter",
    )
    args.update(kwargs)
    return OpenAICompatibleClient(**args)


# --- Request ------------------------------------------------------------------------------


def test_request_is_an_openai_chat_completion_with_strict_schema():
    transport = FakeTransport(answer('{"ok": true}'))
    client(transport).complete_json(REQUEST)
    url, headers, body, timeout = transport.calls[0]
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert body["model"] == "qwen/qwen3-8b:free"
    assert body["messages"] == [
        {"role": "system", "content": "Правила"},
        {"role": "user", "content": "Задание"},
    ]
    assert body["max_tokens"] == 300 and body["temperature"] == 0
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert timeout == 60.0


def test_json_object_mode_for_models_without_schemas():
    transport = FakeTransport(answer('{"ok": true}'))
    client(transport, structured="json_object").complete_json(REQUEST)
    assert transport.calls[0][2]["response_format"] == {"type": "json_object"}


# --- Success ------------------------------------------------------------------------------


def test_success_carries_data_usage_and_cost():
    result = client(FakeTransport(answer('{"ok": true}'))).complete_json(REQUEST)
    assert result.status is LlmStatus.SUCCESS
    assert result.data == {"ok": True}
    assert result.usage == Usage(120, 30)
    assert (result.cost, result.currency) == (Decimal(0), "RUB")
    assert result.model_id == "openrouter:qwen/qwen3-8b:free"


def test_json_in_a_code_fence_is_accepted():
    result = client(FakeTransport(answer('```json\n{"ok": true}\n```'))).complete_json(REQUEST)
    assert result.status is LlmStatus.SUCCESS and result.data == {"ok": True}


# --- Provider failures: quota, timeout, auth and the rest ----------------------------------


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (400, LlmErrorCode.BAD_REQUEST, False),
        (401, LlmErrorCode.AUTH, False),
        (402, LlmErrorCode.QUOTA, False),
        (403, LlmErrorCode.REFUSAL, False),
        (408, LlmErrorCode.TIMEOUT, True),
        (429, LlmErrorCode.RATE_LIMITED, True),
        (500, LlmErrorCode.UNAVAILABLE, True),
        (503, LlmErrorCode.UNAVAILABLE, True),
        (504, LlmErrorCode.TIMEOUT, True),
        (418, LlmErrorCode.UNKNOWN, False),
    ],
    ids=[
        "bad-request", "auth", "quota", "moderation", "408", "rate-limit", "500", "503", "504",
        "418",
    ],
)  # fmt: skip
def test_http_errors_are_normalized(status, code, retryable):
    body = json.dumps({"error": {"code": status, "message": f"provider text with {KEY}"}})
    response = HttpResponse(status, {}, body.encode())
    result = client(FakeTransport(response)).complete_json(REQUEST)
    assert (result.error_code, result.retryable) == (code, retryable)
    assert result.data is None
    assert KEY not in repr(result)


def test_rate_limit_passes_retry_after():
    response = HttpResponse(429, {"Retry-After": "30"}, b"{}")
    result = client(FakeTransport(response)).complete_json(REQUEST)
    assert result.retry_after_s == 30


def test_retry_after_is_dropped_for_a_failure_that_is_not_retried():
    response = HttpResponse(402, {"Retry-After": "30"}, b"{}")
    assert client(FakeTransport(response)).complete_json(REQUEST).retry_after_s is None


def test_timeout_is_a_result_not_an_exception():
    result = client(FakeTransport(error=TransportTimeout())).complete_json(REQUEST)
    assert result.status is LlmStatus.TIMEOUT and result.usage_unknown and result.retryable


def test_unreachable_provider_is_unavailable():
    result = client(FakeTransport(error=TransportUnavailable())).complete_json(REQUEST)
    assert result.error_code is LlmErrorCode.UNAVAILABLE and result.retryable


def test_upstream_error_inside_a_200_answer():
    # OpenRouter may answer 200 with an error object from the upstream provider.
    body = json.dumps({"error": {"code": 429, "message": "upstream rate limited"}}).encode()
    result = client(FakeTransport(HttpResponse(200, {}, body))).complete_json(REQUEST)
    assert result.error_code is LlmErrorCode.RATE_LIMITED


# --- Broken answers -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (answer('{"ok": tru'), LlmErrorCode.NOT_JSON),
        (answer("Вот исследование: всё хорошо."), LlmErrorCode.NOT_JSON),
        (answer('["not", "an", "object"]'), LlmErrorCode.NOT_JSON),
        (answer(None), LlmErrorCode.NOT_JSON),
        (answer('{"ok": tr', finish="length"), LlmErrorCode.TRUNCATED),
        (answer("", finish="content_filter"), LlmErrorCode.REFUSAL),
    ],
    ids=["cut-json", "prose", "array", "no-content", "length", "content-filter"],
)
def test_broken_answers(response, code):
    result = client(FakeTransport(response)).complete_json(REQUEST)
    assert result.error_code is code
    assert result.usage == Usage(120, 30)  # billed tokens are kept for the budget


def test_refusal_field_is_a_refusal():
    payload = {
        "choices": [{"message": {"content": None, "refusal": "Не могу"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
    }
    response = HttpResponse(200, {}, json.dumps(payload).encode())
    assert client(FakeTransport(response)).complete_json(REQUEST).error_code is LlmErrorCode.REFUSAL


@pytest.mark.parametrize(
    "body",
    [
        b"<html>Bad gateway</html>",
        b'{"id": "x"}',
        b'{"choices": []}',
        b'{"choices": [{"text": "x"}]}',
    ],
    ids=["html", "no-choices", "empty-choices", "legacy-shape"],
)
def test_changed_answer_shape_is_unknown_error(body):
    result = client(FakeTransport(HttpResponse(200, {}, body))).complete_json(REQUEST)
    assert result.error_code is LlmErrorCode.UNKNOWN and not result.retryable


def test_bad_usage_counts_are_ignored():
    response = answer('{"ok": true}', usage=(-1, "x"))
    assert client(FakeTransport(response)).complete_json(REQUEST).usage == Usage(0, 0)


@pytest.mark.parametrize(
    "change",
    [
        dict(base_url="http://openrouter.ai/api/v1"),
        dict(model=" "),
        dict(structured="xml"),
        dict(timeout_s=0),
    ],
    ids=["plain-http", "no-model", "structured", "timeout"],
)
def test_invalid_client_is_not_created(change):
    with pytest.raises(ValueError):
        client(FakeTransport(), **change)


# --- Contacts never reach the model -------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        ("Пишите ivan.petrov@alpha.ru", "Пишите [e-mail]"),
        ("Адрес sales@пример.рф", "Адрес [e-mail]"),
        ("Звоните +7 916 123-45-67", "Звоните [телефон]"),
        ("8 (495) 123-45-67 доб. 12", "[телефон]"),
        ("79161234567", "[телефон]"),
        ("+44 20 7946 0958", "[телефон]"),
        ("Telegram @ivan_petrov", "Telegram [мессенджер]"),
        ("https://t.me/ivan_petrov и wa.me/79161234567", "[мессенджер] и [мессенджер]"),
    ],
)
def test_contacts_are_masked(text, masked):
    assert mask_contacts(text) == masked


@pytest.mark.parametrize(
    "text",
    [
        "ИНН 7707083893, ОГРН 1027700132195",
        "ИНН 770708389301",
        "Открыли филиал 2026-10-01, 12 точек, рост на 25%",
        "Бюджет 200 000 – 700 000 ₽",
        "В 2024 году 40 сотрудников",
        "Сайт https://alpha-auto.ru/contacts",
    ],
)
def test_company_data_is_kept(text):
    assert mask_contacts(text) == text


def test_masking_client_masks_before_the_call():
    seen = []

    class Inner:
        model_id = "fake:model"
        tariff = FREE

        def complete_json(self, request):
            seen.append(request)
            return LlmResult.success(request, self.model_id, {"ok": True}, Usage(1, 1), None)

    request = LlmRequest(
        call_id="c1",
        prompt_version="p1",
        system="Контакт ЛПР: anna@alpha.ru",
        user="Позвоните +7 916 123-45-67 или напишите @anna_alpha",
        schema={},
        max_output_tokens=10,
    )
    result = MaskingClient(Inner()).complete_json(request)
    assert result.status is LlmStatus.SUCCESS
    sent = seen[0]
    assert "anna@alpha.ru" not in sent.system and "916" not in sent.user
    assert "@anna_alpha" not in sent.user
    assert sent.call_id == "c1" and sent.prompt_version == "p1"


# --- Settings (owner's rules of 17.10) ----------------------------------------------------


def settings(tmp_path: Path, env: dict, dotenv: str = ""):
    path = tmp_path / ".env"
    path.write_text(dotenv, "utf-8")
    return load_settings({"HUNTER_DATABASE_URL": DB, **env}, path)


def test_no_model_means_no_llm(tmp_path):
    loaded = settings(tmp_path, {})
    assert loaded.llm is None and build_llm(loaded) is None


def test_free_openrouter_model(tmp_path):
    loaded = settings(
        tmp_path, {"HUNTER_LLM_MODEL": "qwen/qwen3-8b:free", "HUNTER_LLM_API_KEY": KEY}
    )
    assert loaded.llm.base_url == "https://openrouter.ai/api/v1"
    assert loaded.llm.provider == "openrouter"
    assert llm_tariff(loaded.llm, "RUB") == FREE
    built = build_llm(loaded)
    assert isinstance(built, MaskingClient)
    assert built.model_id == "openrouter:qwen/qwen3-8b:free"


@pytest.mark.parametrize(
    ("env", "dotenv", "message"),
    [
        ({"HUNTER_LLM_MODEL": "google/gemini-2.5-flash:free", "HUNTER_LLM_API_KEY": KEY}, "",
         "Gemini"),
        ({"HUNTER_LLM_MODEL": "qwen/qwen3-235b", "HUNTER_LLM_API_KEY": KEY}, "", ":free"),
        ({"HUNTER_LLM_MODEL": "qwen/qwen3-8b:free"}, "", "HUNTER_LLM_API_KEY"),
        ({"HUNTER_LLM_MODEL": "qwen/qwen3-8b:free"}, f"HUNTER_LLM_API_KEY={KEY}\n", ".env"),
        ({"HUNTER_LLM_MODEL": "m:free", "HUNTER_LLM_API_KEY": KEY,
          "HUNTER_LLM_BASE_URL": "http://example.com/v1"}, "", "HUNTER_LLM_BASE_URL"),
        ({"HUNTER_LLM_MODEL": "m:free", "HUNTER_LLM_API_KEY": KEY,
          "HUNTER_LLM_STRUCTURED": "xml"}, "", "HUNTER_LLM_STRUCTURED"),
        ({"HUNTER_LLM_MODEL": "m:free", "HUNTER_LLM_API_KEY": KEY,
          "HUNTER_LLM_TIMEOUT_S": "0"}, "", "HUNTER_LLM_TIMEOUT_S"),
        ({"HUNTER_LLM_MODEL": "m", "HUNTER_LLM_API_KEY": KEY,
          "HUNTER_LLM_BASE_URL": "https://api.example.com/v1",
          "HUNTER_LLM_PRICE_INPUT_PER_M": "дорого"}, "", "PRICE"),
    ],
    ids=["gemini", "paid-on-openrouter", "no-key", "key-in-dotenv", "plain-http-remote",
         "structured", "timeout", "bad-price"],
)  # fmt: skip
def test_invalid_llm_settings(tmp_path, env, dotenv, message):
    with pytest.raises(ConfigError, match=message):
        settings(tmp_path, env, dotenv)


def test_error_never_shows_the_key(tmp_path):
    with pytest.raises(ConfigError) as error:
        settings(tmp_path, {"HUNTER_LLM_MODEL": "x/y"}, f"HUNTER_LLM_API_KEY={KEY}\n")
    assert KEY not in str(error.value)


def test_local_open_source_server_needs_no_key(tmp_path):
    loaded = settings(
        tmp_path,
        {"HUNTER_LLM_MODEL": "qwen3:8b", "HUNTER_LLM_BASE_URL": "http://127.0.0.1:11434/v1"},
    )
    assert loaded.llm.api_key == "" and loaded.llm.provider == "127.0.0.1"
    assert llm_tariff(loaded.llm, "RUB") is None  # price unknown until configured


def test_paid_model_elsewhere_needs_both_prices_for_a_tariff(tmp_path):
    env = {
        "HUNTER_LLM_MODEL": "deepseek-v3",
        "HUNTER_LLM_API_KEY": KEY,
        "HUNTER_LLM_BASE_URL": "https://api.example.com/v1",
        "HUNTER_LLM_PRICE_INPUT_PER_M": "40",
        "HUNTER_LLM_PRICE_OUTPUT_PER_M": "120",
    }
    loaded = settings(tmp_path, env)
    assert llm_tariff(loaded.llm, "RUB") == Tariff(Decimal(40), Decimal(120), "RUB")


# --- `hunter llm-check` -------------------------------------------------------------------


def test_llm_check_prints_status_and_usage_without_the_key(capsys):
    transport = FakeTransport(answer('{"ok": true, "segment": "existing_business"}'))
    app = SimpleNamespace(llm=MaskingClient(client(transport)))
    assert llm_cmd.handle(app, None) == 0
    out = capsys.readouterr().out
    assert "success" in out and "existing_business" in out and "120 вход" in out
    assert KEY not in out
    assert transport.calls[0][2]["response_format"]["json_schema"]["schema"] == llm_cmd.CHECK_SCHEMA


def test_llm_check_reports_a_failure(capsys):
    app = SimpleNamespace(llm=client(FakeTransport(HttpResponse(402, {}, b"{}"))))
    assert llm_cmd.handle(app, None) == 1
    assert "quota" in capsys.readouterr().out


def test_llm_check_without_a_model_is_adapter_disabled():
    with pytest.raises(HunterError) as error:
        llm_cmd.handle(SimpleNamespace(llm=None), None)
    assert error.value.code is ErrorCode.ADAPTER_DISABLED


# --- Provider schema of research_v1.1 -----------------------------------------------------


def _objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for value in node.values():
            yield from _objects(value)
    elif isinstance(node, list):
        for value in node:
            yield from _objects(value)


def test_schema_is_strict_everywhere():
    for obj in _objects(research_json_schema()):
        assert obj["additionalProperties"] is False
        assert obj["required"] == list(obj["properties"])


def test_schema_lists_every_feature():
    features = research_json_schema()["properties"]["scoring_inputs"]["properties"]
    assert set(features) == set(FEATURES)


def test_an_answer_written_by_the_schema_parses():
    # The provider's strict output uses null for unknowns; parse_research accepts it.
    feature = {"value": None, "evidence_ids": [], "explanation": "нет данных"}
    payload = {
        "schema_version": "research_v1.1",
        "company_id": "c1",
        "lead_id": "l1",
        "icp": "unknown",
        "summary": "Нет данных о компании",
        "facts": [],
        "signals": [],
        "problem_hypothesis": None,
        "validation_question": None,
        "recommended_product": None,
        "relevant_case_claim_ids": [],
        "scoring_inputs": {
            **{name: feature for name in FEATURES[:6]},
            "budget_evidence_level": {**feature, "value": "unknown"},
            "contact_level": {**feature, "value": "none"},
        },
        "missing_data": ["сайт компании"],
        "offer": {"what_we_sell": None, "why": None},
        "needs_review": True,
    }
    assert set(payload) == set(research_json_schema()["properties"])
    result = parse_research(
        payload, lead_id="l1", company_id="c1", evidence_aliases={}, allowed_claim_ids=frozenset(),
        today=__import__("datetime").date(2026, 10, 18),
    )  # fmt: skip
    assert result.insufficient_evidence
