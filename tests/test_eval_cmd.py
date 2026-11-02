"""CLI selection must not turn a misspelled card ID into a successful eval."""

import json
from types import SimpleNamespace

import pytest

from hunter.application.errors import ErrorCode, HunterError
from hunter.presentation.cli.eval_cmd import handle


class NoCalls:
    model_id = "test:no-calls"

    def complete_json(self, request):
        pytest.fail("invalid selection must not call the model")


@pytest.mark.parametrize("only", ["typo", "fnd-01,typo", "", " , "])
def test_invalid_selection_does_not_call_model_or_overwrite_report(tmp_path, only):
    out = tmp_path / "report.md"
    out.write_text("previous report", encoding="utf-8")
    args = SimpleNamespace(
        cards="data/eval/research_cards.json", only=only, out=str(out), max_output_tokens=8000
    )
    with pytest.raises(HunterError) as caught:
        handle(SimpleNamespace(llm=NoCalls()), args)
    assert caught.value.code is ErrorCode.INVALID_INPUT
    assert out.read_text("utf-8") == "previous report"


def test_empty_card_file_is_rejected_before_creating_report(tmp_path):
    cards = tmp_path / "cards.json"
    cards.write_text(json.dumps({"cards": []}), encoding="utf-8")
    out = tmp_path / "report.md"
    args = SimpleNamespace(cards=str(cards), only=None, out=str(out), max_output_tokens=8000)
    with pytest.raises(HunterError) as caught:
        handle(SimpleNamespace(llm=NoCalls()), args)
    assert caught.value.code is ErrorCode.INVALID_INPUT
    assert not out.exists()
