"""Smoke: without settings the app exits with code 2 and a clear message, before any network."""

import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from sqlalchemy import text

from hunter.infrastructure.postgres.db import create_db_engine

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


def test_migrate_without_a_database_exits_1_and_hides_password(tmp_path):
    # Port 1 on this machine: nothing listens and nothing leaves the machine. Windows does not
    # refuse a closed port at once but waits for the timeout, hence the short one.
    url = "postgresql://hunter:s3cret@127.0.0.1:1/hunter?connect_timeout=2"
    result = run_hunter("migrate", cwd=tmp_path, extra_env={"HUNTER_DATABASE_URL": url})
    assert result.returncode == 1, result.stdout + result.stderr
    assert "PROVIDER_UNAVAILABLE" in result.stderr and "127.0.0.1:1/hunter" in result.stderr
    assert "s3cret" not in result.stdout + result.stderr


def test_migrate_brings_an_empty_schema_up_to_date_twice(tmp_path, database_url):
    schema = f"hunter_cli_{uuid.uuid4().hex[:12]}"
    admin = create_db_engine(database_url)
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    url = f"{database_url}?options=-c%20search_path%3D{schema}"
    try:
        for _ in range(2):
            result = run_hunter("migrate", cwd=tmp_path, extra_env={"HUNTER_DATABASE_URL": url})
            assert result.returncode == 0, result.stdout + result.stderr
            assert "ревизия 0001" in result.stdout
    finally:
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


def test_migrate_without_rights_on_the_schema_exits_1_and_hides_password(tmp_path, database_url):
    # Review A on #14: a user who may not create tables gets a message, not a traceback.
    suffix = uuid.uuid4().hex[:12]
    schema, role = f"hunter_noperm_{suffix}", f"hunter_role_{suffix}"
    admin = create_db_engine(database_url)
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
        connection.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD 'pw_s3cret'"))
        # USAGE without CREATE: the role sees the schema and is refused the table.
        connection.execute(text(f"GRANT USAGE ON SCHEMA {schema} TO {role}"))
    parts = urlsplit(database_url)
    base = f"postgresql://{role}:pw_s3cret@{parts.hostname}:{parts.port or 5432}{parts.path}"
    try:
        # A refused right (42501), then a schema that does not exist (3F000).
        for search_path in (schema, f"{schema}_missing"):
            url = f"{base}?options=-c%20search_path%3D{search_path}"
            result = run_hunter("migrate", cwd=tmp_path, extra_env={"HUNTER_DATABASE_URL": url})
            assert result.returncode == 1, result.stdout + result.stderr
            assert "отказала в правах" in result.stderr and "Traceback" not in result.stderr
            assert "pw_s3cret" not in result.stdout + result.stderr
    finally:
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
            connection.execute(text(f"DROP OWNED BY {role}"))
            connection.execute(text(f"DROP ROLE {role}"))
        admin.dispose()
