import pytest

from hunter.runtime.main import main


@pytest.fixture
def cli(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HUNTER_DB", str(tmp_path / "h.db"))

    def run(actor: str, *argv: str) -> tuple[int, str]:
        monkeypatch.setenv("HUNTER_ACTOR", actor)
        code = main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out + captured.err

    return run


def test_init_users_mode_history(cli):
    assert cli("boss", "init")[0] == 0
    code, out = cli("boss", "users", "add", "anna", "operator")
    assert code == 0 and "anna" in out
    code, out = cli("anna", "mode", "manual", "--reason", "pilot")
    assert code == 1 and "FORBIDDEN" in out  # operator cannot switch modes
    assert cli("boss", "mode", "manual", "--reason", "pilot")[0] == 0
    code, out = cli("anna", "mode")
    assert "manual" in out
    code, out = cli("boss", "history", "run_mode")
    assert "mode:shadow->manual" in out and "pilot" in out


def test_unknown_actor_refused(cli):
    cli("boss", "init")
    code, out = cli("stranger", "users", "list")
    assert code == 1 and "UNAUTHENTICATED" in out
