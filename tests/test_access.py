"""T-10 and the team lead's probe from the review of the H-01..H-11 branch (blocker 4):

    HUNTER_ACTOR=op   hunter mode manual  -> FORBIDDEN
    HUNTER_ACTOR=boss hunter mode manual  -> mode switched   (the bug)

The actor must come from the OS account, so naming the administrator in the environment
must not change who acts.
"""

import pytest

from hunter.application import access
from hunter.application.errors import ErrorCode, HunterError
from hunter.domain.roles import MATRIX, Permission, Role
from hunter.domain.users import User

OPERATOR = User(id="u1", identity_id="os:op", name="op", role=Role.OPERATOR, active=True)
ADMIN = User(id="u2", identity_id="os:boss", name="boss", role=Role.ADMIN, active=True)
RETIRED = User(id="u3", identity_id="os:old", name="old", role=Role.ADMIN, active=False)


class FakeIdentity:
    def __init__(self, identity_id: str) -> None:
        self.identity_id = identity_id

    def current_identity(self) -> str:
        return self.identity_id


class FakeUsers:
    def __init__(self, *users: User) -> None:
        self.by_identity = {u.identity_id: u for u in users}

    def user_by_identity(self, identity_id: str) -> User | None:
        return self.by_identity.get(identity_id)


USERS = FakeUsers(OPERATOR, ADMIN, RETIRED)


def test_review_probe_operator_naming_admin_is_still_operator(monkeypatch):
    for name in ("HUNTER_ACTOR", "USERNAME", "USER", "LOGNAME"):
        monkeypatch.setenv(name, "boss")
    actor = access.authenticate(FakeIdentity("os:op"), USERS)
    assert actor == OPERATOR
    with pytest.raises(HunterError) as exc:
        access.require(actor, Permission.SET_MODE)
    assert exc.value.code is ErrorCode.FORBIDDEN


@pytest.mark.parametrize("permission", sorted(MATRIX[Role.ADMIN] - MATRIX[Role.OPERATOR]), ids=str)
def test_t10_operator_gets_forbidden_for_admin_actions(permission):
    with pytest.raises(HunterError) as exc:
        access.require(OPERATOR, permission)
    assert exc.value.code is ErrorCode.FORBIDDEN


@pytest.mark.parametrize("identity_id", ["os:stranger", "os:old"], ids=["unknown", "inactive"])
def test_unknown_or_inactive_account_is_unauthenticated(identity_id):
    with pytest.raises(HunterError) as exc:
        access.authenticate(FakeIdentity(identity_id), USERS)
    assert exc.value.code is ErrorCode.UNAUTHENTICATED


def test_admin_is_allowed():
    actor = access.authenticate(FakeIdentity("os:boss"), USERS)
    access.require(actor, Permission.SET_MODE)  # no exception


@pytest.mark.parametrize(
    "identity_id",
    ["boss", "os:", "os:with space", "ldap:boss"],
    ids=["bare", "empty", "space", "x"],
)
def test_identity_id_format_is_checked(identity_id):
    with pytest.raises(ValueError):
        User(id="u", identity_id=identity_id, name="boss", role=Role.ADMIN, active=True)
