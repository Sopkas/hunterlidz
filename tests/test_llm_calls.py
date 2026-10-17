"""The single format correction shared by eval, research and drafts (llm_calls)."""

from decimal import Decimal

from hunter.application.llm import LlmErrorCode, LlmRequest, LlmResult, LlmStatus, Tariff, Usage
from hunter.application.llm_calls import call_parsed
from hunter.domain.research import SchemaError

REQUEST = LlmRequest("task:1", "p1", "s", "u", {}, 100)
PRICED = Tariff(Decimal("1000000"), Decimal("0"), "RUB")  # 1 RUB per input token


class Scripted:
    model_id = "scripted"

    def __init__(self, *answers, tariff=PRICED):
        self.answers, self.requests, self.tariff = list(answers), [], tariff

    def complete_json(self, request):
        self.requests.append(request)
        data = self.answers.pop(0)
        if isinstance(data, LlmErrorCode):
            return LlmResult.failure(request, "scripted", data, Usage(1, 0), self.tariff)
        return LlmResult.success(request, "scripted", data, Usage(2, 0), self.tariff)


def strict(data):
    if data != {"ok": True}:
        raise SchemaError("ok: ожидается true")
    return "parsed"


def test_first_answer_parses():
    result = call_parsed(Scripted({"ok": True}), REQUEST, strict)
    assert (result.value, result.corrections, result.usage) == ("parsed", 0, Usage(2, 0))


def test_one_correction_with_the_reason_and_a_new_call_id():
    client = Scripted({"ok": False}, {"ok": True})
    result = call_parsed(client, REQUEST, strict)
    assert result.value == "parsed" and result.corrections == 1
    second = client.requests[1]
    assert second.call_id == "task:2"
    assert second.user.endswith(
        "Ответ не прошёл проверку формы: ok: ожидается true. Верни JSON по схеме."
    )
    assert result.usage == Usage(4, 0) and result.cost == Decimal(4)  # both calls are paid


def test_not_json_gets_the_correction_too():
    result = call_parsed(Scripted(LlmErrorCode.NOT_JSON, {"ok": True}), REQUEST, strict)
    assert result.value == "parsed" and result.corrections == 1


def test_second_wrong_shape_gives_up():
    result = call_parsed(Scripted({"ok": "x"}, {"ok": "y"}), REQUEST, strict)
    assert result.value is None and result.corrections == 1 and "схема" in result.error


def test_provider_failure_is_not_corrected():
    client = Scripted(LlmErrorCode.QUOTA)
    result = call_parsed(client, REQUEST, strict)
    assert result.value is None and result.status is LlmStatus.DENIED and len(client.requests) == 1


def test_unknown_tariff_makes_the_cost_unknown():
    result = call_parsed(Scripted({"ok": True}, tariff=None), REQUEST, strict)
    assert result.cost is None
