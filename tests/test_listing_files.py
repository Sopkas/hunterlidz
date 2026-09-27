import json

import pytest

from hunter.application.errors import HunterError
from hunter.infrastructure.listing_files import read_listing_rows


def test_csv_with_bom_and_aliases(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("﻿ID,Заголовок,Описание,Бюджет\n7,CRM,Текст,100к\n", encoding="utf-8")
    assert read_listing_rows(path) == [
        {"ext_id": "7", "title": "CRM", "body": "Текст", "budget": "100к"}
    ]


def test_json_wrapper_object(tmp_path):
    path = tmp_path / "a.json"
    path.write_text(json.dumps({"items": [{"id": 1, "description": "x"}]}), encoding="utf-8")
    assert read_listing_rows(path) == [{"ext_id": 1, "body": "x"}]


@pytest.mark.parametrize(
    ("name", "content"),
    [("a.xlsx", "x"), ("a.json", "{broken"), ("a.json", '"text"')],
    ids=["type", "broken", "not-list"],
)
def test_rejects_bad_files(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    with pytest.raises(HunterError):
        read_listing_rows(path)
