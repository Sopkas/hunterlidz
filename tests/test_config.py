from datetime import timedelta
from pathlib import Path

import pytest

from hunter.runtime.config import ConfigError, load_settings, parse_utc_offset, read_dotenv


def test_missing_actor_is_config_error(tmp_path: Path):
    with pytest.raises(ConfigError, match="HUNTER_ACTOR"):
        load_settings({}, tmp_path / ".env")


def test_process_env_wins_over_dotenv(tmp_path: Path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("HUNTER_ACTOR=from_file\nHUNTER_DB=file.db\n# comment\nEMPTY=\n", "utf-8")
    settings = load_settings({"HUNTER_ACTOR": "from_env"}, dotenv)
    assert settings.actor == "from_env"
    assert settings.db_path == Path("file.db")


def test_dotenv_skips_comments_and_blank_values(tmp_path: Path):
    dotenv = tmp_path / ".env"
    dotenv.write_text('﻿A="quoted"\n#B=x\nC=\n', "utf-8")
    assert read_dotenv(dotenv) == {"A": "quoted"}


@pytest.mark.parametrize(
    ("raw", "hours"), [("+03:00", 3), ("-05:30", -5.5), ("+00:00", 0)], ids=["msk", "neg", "utc"]
)
def test_utc_offset(raw, hours):
    assert parse_utc_offset(raw).utcoffset(None) == timedelta(hours=hours)


@pytest.mark.parametrize("raw", ["3", "+25:00", "abc"])
def test_bad_utc_offset(raw):
    with pytest.raises(ConfigError):
        parse_utc_offset(raw)
