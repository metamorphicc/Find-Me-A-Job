from pathlib import Path

import pytest

from job_search_automation.config import ConfigError, load_config


def test_vps_example_uses_persistent_paths_and_token_file(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1] / "deploy" / "config.vps.example.toml"
    config_path = tmp_path / "config.toml"
    config_path.write_bytes(source.read_bytes())
    token_file = tmp_path / "token"
    token_file.write_text("example-token\n", encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "other-token")

    config = load_config(config_path)

    assert config.telegram.bot_token == "example-token"
    assert config.database_path == tmp_path / "runtime" / "data" / "jobs.db"
    assert config.profile_path == tmp_path / "runtime" / "profile.json"


def test_missing_token_file_reports_configuration_error(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[search]\nqueries = ["Python"]\n', encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_FILE", str(tmp_path / "missing"))

    with pytest.raises(ConfigError, match="Cannot read TELEGRAM_BOT_TOKEN_FILE"):
        load_config(config_path)
