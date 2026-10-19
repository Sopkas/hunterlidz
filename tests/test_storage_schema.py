"""PostgreSQL schema of S1-07: migrations, constraints of doc 10, append-only audit.

Runs against HUNTER_TEST_DATABASE_URL (README, «База»); skipped without it, required in CI on
Linux. Every test works in a transaction that is rolled back (fixture db).
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from hunter.domain.catalog import Icp, Priority, Product, Stage
from hunter.domain.entities import (
    Channel,
    DatePrecision,
    FetchStatus,
    SourceType,
    VerificationStatus,
)
from hunter.domain.modes import RunMode
from hunter.domain.roles import Role
from hunter.infrastructure.postgres.db import create_db_engine, current_revision, downgrade, upgrade
from hunter.infrastructure.postgres.users import PostgresUserDirectory

TABLES = {
    "alembic_version",
    "audit_events",
    "campaigns",
    "companies",
    "contacts",
    "evidence",
    "leads",
    "settings",
    "sources",
    "users",
}


def new_id() -> str:
    return str(uuid.uuid4())


def insert(db, table: str, **values) -> str:
    values.setdefault("id", new_id())
    columns = ", ".join(values)
    params = ", ".join(f":{name}" for name in values)
    db.execute(text(f"INSERT INTO {table} ({columns}) VALUES ({params})"), values)
    return values["id"]


def company(db, **values) -> str:
    return insert(db, "companies", **{"name": "Альфа", **values})


def campaign(db, **values) -> str:
    return insert(db, "campaigns", **{"name": f"c-{new_id()}", "icp_version": "icp_v1", **values})


def source(db, **values) -> str:
    defaults = {"canonical_url": f"https://alpha.ru/{new_id()}", "source_type": "website"}
    return insert(db, "sources", **{**defaults, "fetch_status": "fetched", **values})


def lead(db, company_id: str, campaign_id: str, **values) -> str:
    return insert(db, "leads", company_id=company_id, campaign_id=campaign_id, **values)


def rejected(db, statement: str, params: dict | None = None) -> DBAPIError:
    """Run a statement that the database must refuse; the savepoint keeps the test going."""
    with pytest.raises(DBAPIError) as caught, db.begin_nested():
        db.execute(text(statement), params or {})
    return caught.value


def tables(engine) -> set[str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = :s"),
            {"s": connection.execute(text("SELECT current_schema()")).scalar_one()},
        )
        return {row.table_name for row in rows}


# --- Migrations --------------------------------------------------------------------------


@pytest.fixture
def empty_schema(database_url):
    schema = f"hunter_empty_{uuid.uuid4().hex[:12]}"
    admin = create_db_engine(database_url)
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_db_engine(database_url, connect_args={"options": f"-c search_path={schema}"})
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


def test_migrations_apply_to_an_empty_database_and_again(empty_schema):
    assert tables(empty_schema) == set()
    assert upgrade(empty_schema) == "0001"
    assert tables(empty_schema) == TABLES
    assert upgrade(empty_schema) == "0001"  # a second run changes nothing
    assert tables(empty_schema) == TABLES


def test_downgrade_to_base_leaves_nothing_and_upgrade_works_again(empty_schema):
    upgrade(empty_schema)
    downgrade(empty_schema, "base")
    assert current_revision(empty_schema) is None
    assert tables(empty_schema) == {"alembic_version"}
    assert upgrade(empty_schema) == "0001"


def test_new_database_starts_in_shadow_mode(db):
    mode = db.execute(text("SELECT value FROM settings WHERE key = 'run_mode'")).scalar_one()
    assert RunMode(mode) is RunMode.SHADOW


# --- Value lists follow the domain -------------------------------------------------------


def _with_value(db, table: str, column: str, value: str) -> None:
    comp = company(db)
    camp = campaign(db)
    src = source(db)
    rows = {
        "users": lambda: insert(
            db, "users", identity_id=f"os:{new_id()}", name=f"u{new_id()[:8]}", role=value
        ),
        "sources": lambda: source(
            db, **{column: value}, **({"error_code": "x"} if value in BAD_FETCH else {})
        ),
        "contacts": lambda: insert(
            db, "contacts", company_id=comp, channel=value, normalized_address=new_id()
        ),
        "evidence": lambda: insert(
            db,
            "evidence",
            company_id=comp,
            source_id=src,
            claim="c",
            excerpt="e",
            observed_at="2026-10-18T09:00:00+00:00",
            **(
                {"date_precision": value, "event_at": None if value == "unknown" else "2026-01-01"}
                if column == "date_precision"
                else {column: value}
            ),
        ),
        "leads": lambda: lead(
            db, comp, camp, **{column: value}, **({"loss_reason": "r"} if value == "LOST" else {})
        ),
    }
    rows[table]()


BAD_FETCH = {"denied", "unavailable", "error"}

DOMAIN_LISTS = [
    ("users", "role", Role),
    ("sources", "source_type", SourceType),
    ("sources", "fetch_status", FetchStatus),
    ("contacts", "channel", Channel),
    ("evidence", "date_precision", DatePrecision),
    ("evidence", "verification_status", VerificationStatus),
    ("leads", "icp", Icp),
    ("leads", "product", Product),
    ("leads", "stage", Stage),
    ("leads", "priority", Priority),
]


@pytest.mark.parametrize(("table", "column", "enum"), DOMAIN_LISTS)
def test_every_domain_value_is_accepted_and_others_are_not(db, table, column, enum):
    # The migration writes the lists out; this fails when the domain and the CHECK drift apart.
    for member in enum:
        with db.begin_nested():
            _with_value(db, table, column, member.value)
    with pytest.raises(IntegrityError), db.begin_nested():
        _with_value(db, table, column, "not_a_value")


def test_run_mode_values_follow_the_domain(db):
    for mode in RunMode:
        db.execute(text("UPDATE settings SET value = :v WHERE key = 'run_mode'"), {"v": mode})
    error = rejected(db, "UPDATE settings SET value = 'assisted_send' WHERE key = 'run_mode'")
    assert isinstance(error, IntegrityError)


# --- Audit is append-only ----------------------------------------------------------------


def test_audit_can_be_added_but_never_changed_or_removed(db):
    event = insert(
        db,
        "audit_events",
        entity_type="settings",
        entity_id="run_mode",
        actor_id="os:admin",
        action="set_mode",
        correlation_id=new_id(),
    )
    for statement in (
        "UPDATE audit_events SET reason = 'changed' WHERE id = :id",
        "DELETE FROM audit_events WHERE id = :id",
        "TRUNCATE audit_events",
    ):
        error = rejected(db, statement, {"id": event})
        assert "append-only" in str(error.orig)
    count = db.execute(text("SELECT count(*) FROM audit_events WHERE id = :id"), {"id": event})
    assert count.scalar_one() == 1


def test_audit_needs_a_correlation_id(db):
    rejected(
        db,
        "INSERT INTO audit_events (entity_type, entity_id, actor_id, action, correlation_id)"
        " VALUES ('lead', 'x', 'os:a', 'edit', '')",
    )


# --- Companies, leads, contacts, sources, evidence ---------------------------------------


def test_active_domain_is_unique_but_a_merged_record_keeps_it(db):
    first = company(db, normalized_domain="alpha.ru")
    rejected(
        db,
        "INSERT INTO companies (id, name, normalized_domain) VALUES (:id, 'Б', 'alpha.ru')",
        {"id": new_id()},
    )
    target = company(db, name="Альфа Групп")
    db.execute(
        text("UPDATE companies SET merged_into_id = :t WHERE id = :id"), {"t": target, "id": first}
    )
    company(db, normalized_domain="alpha.ru")  # the old record is merged: the key is free


@pytest.mark.parametrize(
    "domain", ["Alpha.ru", "www.alpha.ru", "alpha.ru.", "alpha", "xn--e1afmkfd.рф", "a/b.ru"]
)
def test_domain_has_one_form(db, domain):
    # Domain contract S1-05: ASCII / punycode, lower case, no www and no trailing dot.
    rejected(
        db,
        "INSERT INTO companies (id, name, normalized_domain) VALUES (:id, 'А', :d)",
        {"id": new_id(), "d": domain},
    )


def test_company_cannot_be_merged_into_itself_or_have_inverted_size(db):
    same = new_id()
    rejected(
        db, "INSERT INTO companies (id, name, merged_into_id) VALUES (:id, 'А', :id)", {"id": same}
    )
    rejected(
        db,
        "INSERT INTO companies (id, name, size_min, size_max) VALUES (:id, 'А', 50, 10)",
        {"id": new_id()},
    )


def test_one_active_lead_per_company_and_campaign(db):
    comp, camp = company(db), campaign(db)
    first = lead(db, comp, camp)
    rejected(
        db,
        "INSERT INTO leads (id, company_id, campaign_id) VALUES (:id, :c, :k)",
        {"id": new_id(), "c": comp, "k": camp},
    )
    lead(db, comp, campaign(db))  # another campaign is another opportunity
    db.execute(text("UPDATE leads SET stage = 'WON' WHERE id = :id"), {"id": first})
    lead(db, comp, camp)  # the closed one stays as history


@pytest.mark.parametrize(
    ("stage", "loss_reason"),
    [("LOST", None), ("LOST", "  "), ("NEW", "нет бюджета"), ("WON", "нет бюджета")],
)
def test_loss_reason_is_set_exactly_for_lost(db, stage, loss_reason):
    # Review A on #14: the S1-05 invariant of Lead is held by the database too.
    with pytest.raises(IntegrityError), db.begin_nested():
        lead(db, company(db), campaign(db), stage=stage, loss_reason=loss_reason)


def test_lost_lead_keeps_its_reason(db):
    lead(db, company(db), campaign(db), stage="LOST", loss_reason="нет бюджета")


def test_contact_address_is_unique_per_channel(db):
    comp = company(db)
    insert(db, "contacts", company_id=comp, channel="email", normalized_address="a@alpha.ru")
    insert(db, "contacts", company_id=comp, channel="telegram", normalized_address="a@alpha.ru")
    rejected(
        db,
        "INSERT INTO contacts (id, company_id, channel, normalized_address)"
        " VALUES (:id, :c, 'email', 'a@alpha.ru')",
        {"id": new_id(), "c": comp},
    )


def test_a_source_record_is_a_snapshot_of_one_fetch(db):
    # Review of #14 and #16: a refetch with new content is a new record; the same content of the
    # same page twice is a duplicate; failed attempts (no content_hash) never collide.
    url = "https://alpha.ru/about"
    source(db, canonical_url=url, content_hash="sha256:aaa")
    source(db, canonical_url=url, content_hash="sha256:bbb")
    with pytest.raises(IntegrityError), db.begin_nested():
        source(db, canonical_url=url, content_hash="sha256:aaa")
    for _ in range(2):
        source(db, canonical_url=url, fetch_status="error", error_code="timeout")
    source(db, canonical_url=url, source_type="manual", content_hash="sha256:aaa")


def test_source_error_code_matches_fetch_status(db):
    # Doc 10: a URL does not prove the content was received.
    with pytest.raises(IntegrityError), db.begin_nested():
        source(db, fetch_status="fetched", error_code="timeout")
    with pytest.raises(IntegrityError), db.begin_nested():
        source(db, fetch_status="error")
    source(db, fetch_status="error", error_code="timeout")


@pytest.mark.parametrize(
    ("precision", "event_at"),
    [
        ("month", date(2026, 3, 15)),
        ("year", date(2026, 3, 1)),
        ("unknown", date(2026, 1, 1)),
        ("day", None),
    ],
)
def test_event_date_is_the_first_day_of_its_period(db, precision, event_at):
    comp, src = company(db), source(db)
    with pytest.raises(IntegrityError), db.begin_nested():
        insert(
            db,
            "evidence",
            company_id=comp,
            source_id=src,
            claim="c",
            excerpt="e",
            observed_at="2026-10-18T09:00:00+00:00",
            date_precision=precision,
            event_at=event_at,
        )


def test_ids_are_uuids(db):
    error = rejected(db, "INSERT INTO companies (id, name) VALUES ('c1', 'Альфа')")
    assert "uuid" in str(error.orig).lower()


def test_updated_at_is_kept_by_the_database(db):
    comp = company(db)
    db.execute(
        text("UPDATE companies SET updated_at = '2000-01-01', industry = 'it' WHERE id = :id"),
        {"id": comp},
    )
    row = db.execute(
        text("SELECT updated_at, now() AS now FROM companies WHERE id = :id"), {"id": comp}
    ).one()
    assert row.updated_at == row.now


# --- UserDirectory -----------------------------------------------------------------------


def test_user_directory_finds_a_user_by_identity(db):
    user_id = insert(db, "users", identity_id="os:maxim", name="maxim", role="operator")
    user = PostgresUserDirectory(db).user_by_identity("os:maxim")
    assert user is not None
    assert (user.id, user.name, user.role, user.active) == (user_id, "maxim", Role.OPERATOR, True)
    assert PostgresUserDirectory(db).user_by_identity("os:nobody") is None


def test_identity_and_telegram_id_are_unique(db):
    insert(db, "users", identity_id="os:maxim", name="maxim", role="operator", telegram_user_id=100)
    rejected(
        db,
        "INSERT INTO users (id, identity_id, name, role) VALUES (:id, 'os:maxim', 'm2',"
        " 'observer')",
        {"id": new_id()},
    )
    rejected(
        db,
        "INSERT INTO users (id, identity_id, name, role, telegram_user_id)"
        " VALUES (:id, 'os:other', 'm3', 'observer', 100)",
        {"id": new_id()},
    )
    rejected(
        db,
        "INSERT INTO users (id, identity_id, name, role) VALUES (:id, 'maxim', 'm4', 'observer')",
        {"id": new_id()},
    )
