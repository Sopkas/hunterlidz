"""Regression tests for the bugs the reviewer reproduced on the prototype. Each test is the
reviewer's probe; it must keep failing on the old behaviour."""

from datetime import date

import pytest

from hunter.application import messaging, outreach
from hunter.application.errors import ErrorCode, HunterError
from hunter.application.reporting import export_rows
from hunter.application.research import run_research_queue
from hunter.domain.cases import presents_planned_as_done
from hunter.domain.listings import NARROW_NBSP, parse_budget, parse_listing
from hunter.domain.messages import MessageStatus, SkipReason
from hunter.domain.privacy import mask_contacts
from hunter.domain.screening import screen
from hunter.infrastructure.listing_files import read_listing_rows
from tests.research_support import FakeKnowledge, FakeLlm, config, payload
from tests.test_outreach import build_world

TODAY = date(2026, 9, 27)


def test_query_parameters_identify_different_listings():
    first = parse_listing({"title": "t", "url": "https://fl.example/task.php?id=1"}, "fl", "RUB")
    second = parse_listing({"title": "t", "url": "https://fl.example/task.php?id=2"}, "fl", "RUB")
    tracked = parse_listing(
        {"title": "t", "url": "https://fl.example/task.php?utm_source=x&id=1#top"}, "fl", "RUB"
    )
    assert first.source_key != second.source_key
    assert tracked.source_key == first.source_key


def test_narrow_nbsp_in_amount():
    assert parse_budget(f"400{NARROW_NBSP}000 ₽", "RUB").amount == 400_000


def test_dropdown_is_not_spam():
    listing = parse_listing(
        {"title": "Нужен разработчик", "body": "Веб-интерфейс с дропдаун меню, личный кабинет"},
        "fl",
        "RUB",
    )
    assert screen(listing, TODAY) is None


def test_t07_bypass_in_one_sentence_is_blocked():
    assert presents_planned_as_done(
        "Разрабатываем CRM для бизнеса, у нас есть готовый помощник претензионщика."
    )


def test_russian_excel_csv(tmp_path):
    path = tmp_path / "excel.csv"
    content = "id;Заголовок;Описание;Бюджет\r\n7;CRM;Нужна CRM для сети;400 000 руб.\r\n"
    path.write_bytes(content.encode("cp1251"))
    [row] = read_listing_rows(path)
    assert (row["ext_id"], row["title"], row["budget"]) == ("7", "CRM", "400 000 руб.")


def test_company_row_from_fr01_is_accepted():
    row = parse_listing({"company": "ООО Ромашка", "website": "romashka.example"}, "manual", "RUB")
    assert screen(row, TODAY) is None


# --- behaviour that needs the whole flow -----------------------------------------------------


def test_approve_after_skip_and_double_approve_are_refused():
    deps, _, _, operator, lead, message_id, _ = build_world()  # message already approved once
    with pytest.raises(HunterError) as exc:
        messaging.approve(deps, operator, message_id, 1)
    assert exc.value.code is ErrorCode.INVALID_TRANSITION
    approvals = [e for e in deps.store.audit_for(message_id) if e.action == "approved"]
    assert len(approvals) == 1
    v2 = messaging.edit(deps, operator, message_id, 1, "Здравствуйте! Как сейчас идут заявки?")
    messaging.skip(deps, operator, message_id, v2, SkipReason.NO_DATA, "")
    with pytest.raises(HunterError):
        messaging.approve(deps, operator, message_id, v2)
    assert messaging.current_version(deps, message_id)[0].status is MessageStatus.REJECTED


def test_banned_lead_cannot_be_approved_and_export_hides_address():
    deps, _, _, operator, lead, message_id, _ = build_world()
    outreach.suppress(deps, operator, "company", lead.company_id, "просили не писать")
    with pytest.raises(HunterError):
        messaging.approve(deps, operator, message_id, 1)
    [row] = export_rows(deps, operator, with_contacts=True)
    assert row["contact_banned"] is True and row["recipient_address"] is None
    assert row["message_status"] == "cancelled"


def test_contacts_never_reach_the_model():
    deps, _, _, operator, lead, _, _ = build_world()
    llm = FakeLlm(lambda: payload(lead))
    deps.store.conn.execute("UPDATE leads SET stage = 'NEW'")
    deps.store.add_job("job-x", "research", lead.id, deps.clock.now())
    run_research_queue(deps, llm, FakeKnowledge(), config(), max_jobs=1)
    system, user, _ = llm.calls[0]
    assert "@test_auto" not in user + system and "[telegram скрыт]" in user


def test_mask_contacts():
    text = "Пишите ops@test.example, @test_owner, t.me/test_owner или +7 (900) 000-00-00"
    masked = mask_contacts(text)
    assert "@" not in masked.replace("[email скрыт]", "") and "900" not in masked


def test_t08_injected_instructions_cannot_act():
    """What T-08 can honestly check without a real model: the model has no tools to act with,
    the key is not in its context, and an answer that follows the injection (a score, an
    approval, an extra field) is rejected by the schema check instead of being applied."""
    deps, _, _, operator, lead, _, _ = build_world()
    obeyed = payload(lead)
    obeyed["score"] = 100
    obeyed["approve"] = True
    deps.store.conn.execute("UPDATE leads SET stage = 'NEW'")
    deps.store.add_job("job-y", "research", lead.id, deps.clock.now())
    [outcome] = run_research_queue(
        deps, FakeLlm(obeyed, obeyed), FakeKnowledge(), config(), max_jobs=1
    )
    assert (outcome.status, outcome.error_code) == ("failed", "invalid_schema")
    assert deps.store.lead(lead.id).score_total == 90  # still the code's score, not the model's 100
