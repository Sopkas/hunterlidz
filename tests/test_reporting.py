import sqlite3
from decimal import Decimal

import pytest

from hunter.application import outreach
from hunter.application.errors import ErrorCode, HunterError
from hunter.application.import_listings import import_listings
from hunter.application.reporting import export_rows, pilot_metrics, safe_cell, spreadsheet_safe
from hunter.domain.metrics import show_rate
from hunter.domain.roles import Role
from hunter.infrastructure.sqlite.backup import backup, restore
from hunter.infrastructure.sqlite.store import SqliteStore
from tests.helpers import add_user, make_deps
from tests.test_import import GOOD, TODAY
from tests.test_outreach import build_world, send


def test_zero_denominators_are_na_t21():
    deps = make_deps()
    admin = add_user(deps, "boss", Role.ADMIN)
    metrics = pilot_metrics(deps, admin, "USD")
    assert metrics.contacts_reached == 0
    assert show_rate(metrics.reply_rate) == "N/A" and metrics.cost_per_accepted_card is None


def test_metrics_after_send_and_reply():
    deps, clock, admin, operator, lead, message_id, approval = build_world()
    send(deps, operator, message_id, approval)
    outreach.record_reply(deps, operator, lead.id, "Интересно, давайте созвонимся", clock.now())
    m = pilot_metrics(deps, admin, "USD")
    counts = (m.leads_discovered, m.first_messages_sent, m.contacts_reached, m.replies)
    assert counts == (1, 1, 1, 1)
    assert m.research_qualified == 1 and m.approved_cards == 1
    assert m.reply_rate == Decimal("100.0") and m.contacts_in_open_window == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('=HYPERLINK("x")', '\'=HYPERLINK("x")'),
        ("+7 900", "'+7 900"),
        ("@cmd", "'@cmd"),
        ("Обычный текст", "Обычный текст"),
        (42, 42),
    ],
    ids=["formula", "plus", "at", "text", "number"],
)
def test_export_cells_are_never_formulas(value, expected):
    assert safe_cell(value) == expected


def test_export_contains_versions_and_hides_contacts_by_default():
    deps, _, admin, operator, lead, message_id, approval = build_world()
    deps.store.conn.execute(
        "UPDATE companies SET name = '=cmd|calc' WHERE id = ?", (lead.company_id,)
    )
    [row] = export_rows(deps, operator, with_contacts=False)
    assert row["lead_id"] == lead.id and row["message_version"] == 1
    assert "recipient_address" not in row
    [safe] = spreadsheet_safe([row])
    assert (row["company_name"], safe["company_name"]) == ("=cmd|calc", "'=cmd|calc")
    [with_contacts] = export_rows(deps, operator, with_contacts=True)
    assert with_contacts["recipient_address"] == "@test_auto"


def test_observer_cannot_export():
    deps, *_ = build_world()
    observer = add_user(deps, "watcher", Role.OBSERVER)
    with pytest.raises(HunterError) as exc:
        export_rows(deps, observer, with_contacts=False)
    assert exc.value.code is ErrorCode.FORBIDDEN


def test_restore_reapplies_bans_made_after_backup_t24(tmp_path):
    db = tmp_path / "hunter.db"
    store = SqliteStore(db)
    deps = make_deps(store=store)
    operator = add_user(deps, "op", Role.OPERATOR)
    report = import_listings(
        deps,
        operator,
        [GOOD],
        file_name="f",
        campaign="c",
        platform="fl",
        currency="RUB",
        today=TODAY,
    )
    lead = deps.store.lead(report.rows[0].lead_id)
    copy = backup(store.conn, tmp_path / "backups" / "b1.db")
    outreach.suppress(deps, operator, "company", lead.company_id, "просили не писать")
    store.close()

    result = restore(copy, db)
    assert result.bans_reapplied >= 1 and result.integrity == "ok"
    assert result.foreign_key_errors == 0 and result.counts["leads"] == 1
    reopened = SqliteStore(db)
    assert reopened.company_suppressed(lead.company_id)
    reopened.close()


def test_backup_refuses_to_overwrite(tmp_path):
    store = SqliteStore(tmp_path / "h.db")
    target = backup(store.conn, tmp_path / "b.db")
    with pytest.raises(FileExistsError):
        backup(store.conn, target)
    assert sqlite3.connect(target).execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    store.close()


def test_reply_from_not_contacted_lead_is_not_in_reply_rate():
    deps, clock, admin, operator, lead, _, _ = build_world()
    outreach.record_reply(deps, operator, lead.id, "Мы уже нашли подрядчика", clock.now())
    m = pilot_metrics(deps, admin, "USD")
    assert (m.contacts_reached, m.replies) == (0, 0)
    assert show_rate(m.reply_rate) == "N/A"
