from datetime import timedelta
from pathlib import Path

import pytest

from hunter.runtime.config import (
    ConfigError,
    load_settings,
    parse_utc_offset,
    read_dotenv,
    safe_database_url,
)

URL = "postgresql://hunter:s3cret@localhost:5432/hunter"


def test_missing_database_url_is_config_error(tmp_path: Path):
    with pytest.raises(ConfigError, match="HUNTER_DATABASE_URL"):
        load_settings({}, tmp_path / ".env")


@pytest.mark.parametrize(
    "raw",
    ["sqlite:///hunter.db", "postgresql://localhost", "postgresql:///hunter", "not a url"],
    ids=["sqlite", "no-database", "no-host", "garbage"],
)
def test_bad_database_url(raw, tmp_path: Path):
    with pytest.raises(ConfigError) as exc:
        load_settings({"HUNTER_DATABASE_URL": raw}, tmp_path / ".env")
    assert "s3cret" not in str(exc.value)


def test_password_never_displayed():
    assert safe_database_url(URL) == "postgresql://localhost:5432/hunter"


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("postgresql://u:123/x@localhost:5432/hunter", "123/x"),
        ("postgresql://u:p/ss@localhost:5432/hunter", "p/ss"),
        ("postgresql://u:s3cret@localhost:99999/hunter", "s3cret"),
        ("postgresql://u:p?ss@localhost:5432/hunter", "p?ss"),
        ("postgresql://u:p#ss@localhost:5432/hunter", "p#ss"),
    ],
    ids=["slash-numeric", "slash", "bad-port", "question", "hash"],
)
def test_unencoded_password_or_bad_port_is_config_error(raw, secret, tmp_path: Path):
    with pytest.raises(ConfigError, match="HUNTER_DATABASE_URL") as exc:
        load_settings({"HUNTER_DATABASE_URL": raw}, tmp_path / ".env")
    assert secret not in str(exc.value)


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("postgresql://u:p%2Fss@localhost:5432/hunter", "postgresql://localhost:5432/hunter"),
        ("postgresql://u:s3cret@[::1]:5432/hunter", "postgresql://[::1]:5432/hunter"),
    ],
    ids=["encoded-slash", "ipv6"],
)
def test_valid_url_shown_without_password(raw, shown, tmp_path: Path):
    settings = load_settings({"HUNTER_DATABASE_URL": raw}, tmp_path / ".env")
    assert safe_database_url(settings.database_url) == shown


def test_process_env_wins_over_dotenv(tmp_path: Path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        f"HUNTER_DATABASE_URL={URL}\nHUNTER_DEFAULT_CURRENCY=usd\n# comment\nEMPTY=\n", "utf-8"
    )
    settings = load_settings({"HUNTER_DEFAULT_CURRENCY": "eur"}, dotenv)
    assert settings.database_url == URL
    assert settings.default_currency == "EUR"


def test_dotenv_skips_comments_and_blank_values(tmp_path: Path):
    dotenv = tmp_path / ".env"
    dotenv.write_text('﻿A="quoted"\n#B=x\nC=\n', "utf-8")
    assert read_dotenv(dotenv) == {"A": "quoted"}


@pytest.mark.parametrize(
    "raw", ["RUBL", "12", "", "руб"], ids=["long", "digits", "blank-ish", "cyrillic"]
)
def test_bad_currency(raw, tmp_path: Path):
    env = {"HUNTER_DATABASE_URL": URL, "HUNTER_DEFAULT_CURRENCY": raw or " "}
    with pytest.raises(ConfigError, match="HUNTER_DEFAULT_CURRENCY"):
        load_settings(env, tmp_path / ".env")


@pytest.mark.parametrize(
    ("raw", "hours"), [("+03:00", 3), ("-05:30", -5.5), ("+00:00", 0)], ids=["msk", "neg", "utc"]
)
def test_utc_offset(raw, hours):
    assert parse_utc_offset(raw).utcoffset(None) == timedelta(hours=hours)


@pytest.mark.parametrize("raw", ["3", "+25:00", "abc", "+03:75", "+03:-5"])
def test_bad_utc_offset(raw):
    with pytest.raises(ConfigError):
        parse_utc_offset(raw)
