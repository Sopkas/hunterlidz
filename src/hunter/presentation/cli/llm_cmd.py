"""`hunter llm-check` — one synthetic call to the configured model. (S2-04)

The owner's live check for the test period: the prompt is synthetic and has no data about real
companies or people. Prints status, usage and cost, never the key or the provider's text.
"""

from __future__ import annotations

import argparse
import uuid

from hunter.application.errors import ErrorCode, HunterError
from hunter.application.llm import LlmRequest, LlmStatus

CHECK_PROMPT_VERSION = "llm_check_v1"
CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "segment": {"type": "string", "enum": ["founders", "existing_business", "agencies"]},
    },
    "required": ["ok", "segment"],
    "additionalProperties": False,
}
CHECK_USER = (
    "Синтетическая карточка для проверки формата. Компания «Пример Авто» (вымышленная) "
    "открыла третий автосервис и ищет администратора CRM. Определи сегмент: founders, "
    'existing_business или agencies. Верни JSON {"ok": true, "segment": "…"}.'
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "llm-check", help="проверить подключение модели одним синтетическим запросом"
    )
    parser.set_defaults(handler=handle)


def handle(app, args: argparse.Namespace) -> int:
    if app.llm is None:
        raise HunterError(
            ErrorCode.ADAPTER_DISABLED, "модель не подключена: задайте HUNTER_LLM_MODEL"
        )
    request = LlmRequest(
        call_id=f"llm-check:{uuid.uuid4()}",
        prompt_version=CHECK_PROMPT_VERSION,
        system="Ты отвечаешь только JSON по заданной схеме.",
        user=CHECK_USER,
        schema=CHECK_SCHEMA,
        max_output_tokens=200,
    )
    result = app.llm.complete_json(request)
    cost = f"{result.cost} {result.currency}" if result.cost is not None else "тариф неизвестен"
    print(f"модель:   {result.model_id}")
    print(f"статус:   {result.status}" + (f" ({result.error_code})" if result.error_code else ""))
    print(f"токены:   {result.usage.input_tokens} вход / {result.usage.output_tokens} выход")
    print(f"стоимость: {cost}")
    if result.retry_after_s is not None:
        print(f"повторить через: {result.retry_after_s} с")
    if result.status is LlmStatus.SUCCESS:
        segment = result.data.get("segment") if isinstance(result.data, dict) else None
        print(f"ответ:    сегмент {segment!r}")
        return 0
    return 1
