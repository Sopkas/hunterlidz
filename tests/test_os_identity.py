"""The OS identity ignores environment variables (manual probe for T-10 as a test)."""

from hunter.infrastructure.os_identity import OsIdentity
from tests.test_smoke import run_hunter


def test_environment_does_not_change_identity(monkeypatch):
    real = OsIdentity().current_identity()
    for name in ("HUNTER_ACTOR", "USERNAME", "USER", "LOGNAME"):
        monkeypatch.setenv(name, "boss-from-env")
    assert OsIdentity().current_identity() == real
    assert real.startswith("os:") and real != "os:boss-from-env"


def test_whoami_ignores_spoofed_environment(tmp_path):
    spoof = {
        "HUNTER_DATABASE_URL": "postgresql://h@localhost:5432/hunter",
        "HUNTER_ACTOR": "boss-from-env",
        "USERNAME": "boss-from-env",
        "USER": "boss-from-env",
        "LOGNAME": "boss-from-env",
    }
    result = run_hunter("whoami", cwd=tmp_path, extra_env=spoof)
    assert result.returncode == 0, result.stderr
    assert f"Учётная запись: {OsIdentity().current_identity()}" in result.stdout
    assert "boss-from-env" not in result.stdout
