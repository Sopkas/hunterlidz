import sqlite3

import pytest

from hunter.application import access
from hunter.application.errors import ErrorCode, HunterError
from hunter.domain.modes import Operation, RunMode
from hunter.domain.roles import Role
from hunter.infrastructure.sqlite.db import migrate
from hunter.infrastructure.sqlite.store import SqliteStore
from tests.helpers import add_user, make_deps


def test_migrations_apply_to_empty_and_existing_db(tmp_path):
    path = tmp_path / "h.db"
    first = SqliteStore(path)
    assert first.applied_migrations  # empty database got the schema
    first.close()
    second = SqliteStore(path)
    assert second.applied_migrations == []  # re-applying is a no-op
    assert migrate(second.conn) == []


def test_audit_is_append_only():
    deps = make_deps()
    admin = access.bootstrap_admin(deps, "boss")
    with pytest.raises(sqlite3.IntegrityError):
        deps.store.conn.execute("UPDATE audit_events SET reason = 'x'")
    with pytest.raises(sqlite3.IntegrityError):
        deps.store.conn.execute("DELETE FROM audit_events")
    assert [e.action for e in deps.store.audit_for(admin.id)] == ["bootstrap_admin"]


def test_transaction_rolls_back_on_error():
    deps = make_deps()
    with pytest.raises(RuntimeError):
        with deps.store.transaction():
            add_user(deps, "ghost", Role.OPERATOR)
            raise RuntimeError("boom")
    assert deps.store.user_by_name("ghost") is None


def test_bootstrap_admin_only_once():
    deps = make_deps()
    assert access.bootstrap_admin(deps, "boss").role is Role.ADMIN
    assert access.bootstrap_admin(deps, "other") is None


def test_unknown_actor_is_unauthenticated():
    with pytest.raises(HunterError) as exc:
        access.authenticate(make_deps(), "nobody")
    assert exc.value.code is ErrorCode.UNAUTHENTICATED


def test_operator_cannot_manage_users_or_mode():
    deps = make_deps()
    operator = add_user(deps, "op", Role.OPERATOR)
    for call in (
        lambda: access.add_user(deps, operator, "x1", Role.ADMIN),
        lambda: access.set_mode(deps, operator, RunMode.MANUAL, "try"),
    ):
        with pytest.raises(HunterError) as exc:
            call()
        assert exc.value.code is ErrorCode.FORBIDDEN
    assert deps.store.get_mode() is RunMode.SHADOW
    assert deps.store.user_by_name("x1") is None


def test_mode_switch_is_audited_and_enforced():
    deps = make_deps()
    admin = access.bootstrap_admin(deps, "boss")
    access.require_mode(deps, Operation.RESEARCH)  # shadow allows research
    with pytest.raises(HunterError):
        access.require_mode(deps, Operation.MANUAL_SEND)  # shadow forbids sending
    access.set_mode(deps, admin, RunMode.PAUSED, "incident")
    with pytest.raises(HunterError):
        access.require_mode(deps, Operation.RESEARCH)
    access.require_mode(deps, Operation.RECORD_REPLY)  # replies still recorded when paused
    [event] = deps.store.audit_for("run_mode")
    assert event.action == "mode:shadow->paused" and event.reason == "incident"


def test_mode_switch_requires_reason():
    deps = make_deps()
    admin = access.bootstrap_admin(deps, "boss")
    with pytest.raises(HunterError) as exc:
        access.set_mode(deps, admin, RunMode.MANUAL, "  ")
    assert exc.value.code is ErrorCode.INVALID_INPUT
