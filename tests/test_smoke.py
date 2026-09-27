"""Smoke: without settings the app exits with code 2 and a clear message, before any network."""

import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def run_hunter(*args: str, cwd: Path, extra_env: dict[str, str] | None = None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HUNTER_", "ANTHROPIC_"))}
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
    assert "HUNTER_ACTOR" in result.stderr


def test_with_settings_runs(tmp_path):
    result = run_hunter("config", cwd=tmp_path, extra_env={"HUNTER_ACTOR": "tester"})
    assert result.returncode == 0, result.stderr
    assert "tester" in result.stdout
