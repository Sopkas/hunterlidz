"""Smoke: without settings the app exits with code 2 and a clear message, before any network."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"


def run_hunter(*args: str, cwd: Path, extra_env: dict[str, str] | None = None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("HUNTER_")}
    env["PYTHONPATH"] = str(SRC)
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-m", "hunter", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )


def test_without_settings_exits_2(tmp_path):
    result = run_hunter("config", cwd=tmp_path)
    assert result.returncode == 2
    assert "HUNTER_DATABASE_URL" in result.stderr


def test_with_settings_runs_and_hides_password(tmp_path):
    url = "postgresql://hunter:s3cret@localhost:5432/hunter"
    result = run_hunter("config", cwd=tmp_path, extra_env={"HUNTER_DATABASE_URL": url})
    assert result.returncode == 0, result.stderr
    assert "localhost:5432/hunter" in result.stdout
    assert "s3cret" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("url", "secret"),
    [
        ("postgresql://u:123/x@localhost:5432/hunter", "123/x"),
        ("postgresql://u:p/ss@localhost:5432/hunter", "p/ss"),
        ("postgresql://u:s3cret@localhost:99999/hunter", "s3cret"),
    ],
    ids=["slash-numeric", "slash", "bad-port"],
)
def test_bad_url_exits_2_and_hides_password(tmp_path, url, secret):
    result = run_hunter("config", cwd=tmp_path, extra_env={"HUNTER_DATABASE_URL": url})
    assert result.returncode == 2, result.stdout + result.stderr
    assert "HUNTER_DATABASE_URL" in result.stderr
    assert secret not in result.stdout + result.stderr
