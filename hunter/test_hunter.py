"""Проверки Hunter v0 по док. 13 (T-01, T-03–T-08, T-11). Запуск: py test_hunter.py"""
from datetime import date

import hunter as h

TODAY = date(2026, 9, 27)
GOOD = {"id": "A1", "title": "Нужна CRM для сети автосервисов",
        "description": "Сеть из 4 автосервисов, открываем филиалы. Заявки в Excel теряются. Нужна разработка CRM: запись "
                       "клиентов, распределение заявок, отчёты, интеграция с телефонией и сайтом. " * 2,
        "company": "ООО Тест Авто", "budget": "400 000 ₽", "date": "2026-09-20",
        "contact": "@test_auto", "contact_name": "Собственник", "url": "https://fl-demo.example/tasks/A1"}


def fake_llm(body="Здравствуйте! Видим, что вы расширяете сеть. Как сейчас распределяются заявки? Делали CRM для автосервисов. Созвонимся?",
             evidence_ids=("E1", "E2")):
    def llm(system, user):
        assert "игнорируй" not in system.lower()  # данные площадки не попадают в системную инструкцию
        return {"icp": "existing_business", "summary": "Сеть автосервисов ищет CRM.",
                "facts": [{"text": "Сеть из 4 автосервисов", "evidence_ids": list(evidence_ids)}],
                "problem_hypothesis": "Заявки теряются между филиалами.", "validation_question": "Как считаете потери?",
                "recommended_product": "business_automation", "what_we_sell": "CRM под процесс записи",
                "why": "Есть кейс auto_crm", "relevant_case_claim_ids": ["auto_crm", "made_up_case"],
                "missing_data": [], "draft_body": body, "needs_review": False, "review_reasons": []}
    return llm


def db_with(*rows):
    db = h.connect(":memory:")
    res = h.import_rows(db, list(rows), "fl-demo", TODAY)
    return db, res


def test_import_dedup_T01():
    db, res = db_with(GOOD)
    assert res[0]["result"] == "created"
    again = h.import_rows(db, [GOOD, dict(GOOD, id="A2")], "fl-demo", TODAY)
    assert again[0] == {"row": 1, "result": "matched", "id": res[0]["id"], "reason": None}
    assert again[1]["reason"] == "duplicate_text"  # репост под другим id
    assert db.execute("SELECT COUNT(*) FROM listings WHERE status='scored'").fetchone()[0] == 1


def test_junk_filter():
    cases = {"spam": "Заработок без вложений, нужен сайт", "not_a_buyer": "Выполню сайт под ключ недорого, опыт 5 лет",
             "out_of_scope": "Нужно SEO продвижение магазина и отчёты", "no_dev_task": "Нужен бухгалтер на полставки в офис",
             "empty": "", "budget_too_low": "Сделать лендинг для кафе, простой сайт"}
    rows = [{"id": k, "title": k, "description": v, "budget": "5000" if k == "budget_too_low" else None} for k, v in cases.items()]
    rows.append(dict(GOOD, id="old", date="2025-01-01"))
    rows.append(dict(GOOD, id="sup", company="Запрещённая Компания", description=GOOD["description"] + " x"))
    db = h.connect(":memory:")
    res = h.import_rows(db, rows, "fl-demo", TODAY, suppress=["запрещённая компания"])
    got = [r["reason"] for r in res]
    assert got == ["spam", "not_a_buyer", "out_of_scope", "no_dev_task", "empty", "budget_too_low", "stale", "suppressed"], got


def test_scoring_and_unknown_date_T03():
    icp, c, _ = h.score({**{f: None for f in h.ALIASES}, "title": GOOD["title"], "body": GOOD["description"],
                          "budget": 400_000, "posted_at": "2026-09-20", "contact": "@x", "contact_name": "CEO",
                          "url": None}, TODAY)
    assert icp == "existing_business" and c == {"icp_fit": 25, "signal": 30, "problem_fit": 15, "budget": 10,
                                                 "contactability": 10}
    _, c, r = h.score({**{f: None for f in h.ALIASES}, "title": GOOD["title"], "body": GOOD["description"],
                        "budget": None, "posted_at": None, "contact": None, "contact_name": None, "url": None}, TODAY)
    assert c["signal"] == 10 and "неизвестна" in r["signal"] and c["contactability"] == 0 and c["budget"] == 0


def test_priority_boundaries_T04():
    assert [h.priority(x) for x in (39, 40, 59, 60, 79, 80)] == ["SKIP", "WATCH", "WATCH", "WARM", "WARM", "HOT"]


def test_budget_parse():
    assert [h.parse_budget(x) for x in ("400 000 ₽", "до 500к", "400-600 тыс", "договорная", "1,5 млн", 250000)] == \
           [400000, 500000, 600000, None, 1500000, 250000]


def test_card_approve_edit_T11():
    db, res = db_with(GOOD)
    lid = res[0]["id"]
    assert h.analyze(db, today=TODAY, llm=fake_llm())["card"] == 1
    row, card = h.latest_card(db, lid)
    assert card["relevant_case_claim_ids"] == ["auto_crm"] and card["block_reasons"] == []
    h.approve(db, lid, 1, "op")
    assert h.edit(db, lid, 1, "Новый текст: как у вас распределяются заявки между филиалами?", "op") == 2
    try:
        h.approve(db, lid, 1, "op")  # устаревшая версия
        raise AssertionError("approval v1 принят после правки")
    except h.VersionConflict:
        pass
    events = [r[0] for r in db.execute("SELECT action FROM events WHERE listing_id=? ORDER BY id", (lid,))]
    assert events == ["scored", "card_created", "approved", "approval_revoked", "edited"], events
    h.approve(db, lid, 2, "op")
    import tempfile, os, csv
    path = os.path.join(tempfile.mkdtemp(), "out.csv")
    assert h.export(db, path) == 1
    assert list(csv.DictReader(open(path, encoding="utf-8-sig")))[0]["version"] == "2"


def test_invented_evidence_T06():
    db, res = db_with(GOOD)
    h.analyze(db, today=TODAY, llm=fake_llm(evidence_ids=("E1", "E99")))
    _, card = h.latest_card(db, res[0]["id"])
    assert card["facts"] == [] and card["needs_review"] and any("evidence_id" in r for r in card["review_reasons"])


def test_planned_claim_blocked_T07():
    db, res = db_with(GOOD)
    h.analyze(db, today=TODAY, llm=fake_llm(body="У нас есть готовый продукт — помощник претензионщика с Excel-отчётом."))
    try:
        h.approve(db, res[0]["id"], 1, "op")
        raise AssertionError("запланированный кейс прошёл как готовый")
    except h.HunterError as e:
        assert "претензионщика" in str(e)
    ok = "Разрабатываем помощника для должников: сейчас готова оболочка бота, остальное в разработке. Обсудим?"
    assert not h.planned_claim_violation(ok)
    h.approve(db, res[0]["id"], h.edit(db, res[0]["id"], 1, ok, "op"), "op")


def test_no_channel_blocks_approve_T05():
    db, res = db_with(dict(GOOD, contact=None, url=None, id="nc"))
    h.analyze(db, min_score=0, today=TODAY, llm=fake_llm())
    try:
        h.approve(db, res[0]["id"], 1, "op")
        raise AssertionError("approve без канала")
    except h.HunterError as e:
        assert "канала" in str(e)


def test_ai_failure_keeps_queue():
    db, res = db_with(GOOD)

    def boom(system, user):
        raise TimeoutError("LLM недоступен")
    assert h.analyze(db, today=TODAY, llm=boom) == {"ai_error": 1}
    assert h.analyze(db, today=TODAY, llm=fake_llm())["card"] == 1  # повтор подхватывает ai_error


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"{len(tests)} passed")
