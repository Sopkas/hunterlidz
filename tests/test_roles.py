import pytest

from hunter.domain.roles import Permission, Role, can


@pytest.mark.parametrize("permission", [Permission.APPROVE, Permission.MANUAL_SEND])
def test_worker_and_observer_cannot_approve_or_send(permission):
    assert not can(Role.WORKER, permission)
    assert not can(Role.OBSERVER, permission)
    assert can(Role.OPERATOR, permission)


def test_only_admin_manages_users_modes_and_lifts_suppression():
    for permission in (Permission.MANAGE_USERS, Permission.SET_MODE, Permission.LIFT_SUPPRESSION):
        assert [role for role in Role if can(role, permission)] == [Role.ADMIN]


def test_worker_writes_research_only():
    assert can(Role.WORKER, Permission.WRITE_RESEARCH)
    assert not can(Role.WORKER, Permission.IMPORT)
