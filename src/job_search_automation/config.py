from __future__ import annotations

import json
import os
import tomllib
from dataclasses import asdict
from dataclasses import dataclass as dc
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when the local search configuration is invalid."""


SOURCE_IDS = frozenset({"hh", "superjob", "remotive", "wwr", "fl"})


@dc(frozen=True, slots=True)
class SearchConfig:
    queries: tuple[str, ...]
    excluded_keywords: tuple[str, ...]
    area_ids: tuple[str, ...]
    experience_ids: tuple[str, ...]
    remote_only: bool
    strict_remote: bool
    days: int
    per_query: int
    sources: tuple[str, ...] = ("hh",)
    kinds: tuple[str, ...] = ("job", "freelance")


@dc(frozen=True, slots=True)
class HhConfig:
    base_url: str
    user_agent: str
    timeout_seconds: int
    browser_fallback: bool = True


@dc(frozen=True, slots=True)
class TelegramConfig:
    bot_token: str
    allowed_user_ids: tuple[int, ...]
    page_size: int


@dc(frozen=True, slots=True)
class AppConfig:
    search: SearchConfig
    hh: HhConfig
    database_path: Path
    reports_dir: Path
    telegram: TelegramConfig
    profile_path: Path
    superjob_app_key: str = ""


def _strings(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{field} must be a list of strings")
    return tuple(item.strip() for item in value if item.strip())


def _bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ConfigError(f"{field} must be an integer between {minimum} and {maximum}")
    return value


def search_settings_path(database_path: Path) -> Path:
    return database_path.parent / "search-settings.json"


def _validate_search(search: SearchConfig) -> SearchConfig:
    if not search.sources or set(search.sources) - SOURCE_IDS:
        raise ConfigError("search.sources contains no source or an unknown source")
    if not search.kinds or set(search.kinds) - {"job", "freelance"}:
        raise ConfigError("search.kinds must include job or freelance")
    return search


def load_search_settings(base: SearchConfig, path: Path) -> SearchConfig:
    if not path.is_file():
        return base
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"Cannot read saved search settings: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("Saved search settings must be a JSON object")
    queries = _strings(raw.get("queries"), "saved search.queries")
    if not queries:
        raise ConfigError("Saved search queries must not be empty")
    remote_only = raw.get("remote_only")
    strict_remote = raw.get("strict_remote")
    if not isinstance(remote_only, bool) or not isinstance(strict_remote, bool):
        raise ConfigError("Saved remote filters must be true or false")
    return _validate_search(SearchConfig(
        queries=queries,
        excluded_keywords=_strings(raw.get("excluded_keywords"), "saved excluded_keywords"),
        area_ids=_strings(raw.get("area_ids"), "saved area_ids"),
        experience_ids=_strings(raw.get("experience_ids"), "saved experience_ids"),
        remote_only=remote_only,
        strict_remote=strict_remote,
        days=_bounded_int(raw.get("days"), "saved days", 1, 30),
        per_query=_bounded_int(raw.get("per_query"), "saved per_query", 1, 500),
        sources=_strings(raw.get("sources", list(base.sources)), "saved sources"),
        kinds=_strings(raw.get("kinds", list(base.kinds)), "saved kinds"),
    ))


def save_search_settings(settings: SearchConfig, path: Path) -> None:
    _validate_search(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(
            f"Config not found: {config_path}. Copy config.example.toml to config.toml first."
        )

    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {config_path}: {exc}") from exc

    search_raw = raw.get("search", {})
    hh_raw = raw.get("hh", {})
    storage_raw = raw.get("storage", {})
    telegram_raw = raw.get("telegram", {})
    if not all(
        isinstance(value, dict) for value in (search_raw, hh_raw, storage_raw, telegram_raw)
    ):
        raise ConfigError("search, hh, storage, and telegram must be TOML tables")

    queries = _strings(search_raw.get("queries"), "search.queries")
    if not queries:
        raise ConfigError("search.queries must contain at least one query")

    root = config_path.resolve().parent
    database_path = root / str(storage_raw.get("database", "data/jobs.db"))
    reports_dir = root / str(storage_raw.get("reports_dir", "reports"))
    allowed_ids = telegram_raw.get("allowed_user_ids", [])
    if not isinstance(allowed_ids, list) or any(
        not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0
        for user_id in allowed_ids
    ):
        raise ConfigError("telegram.allowed_user_ids must be a list of positive numbers")

    search = SearchConfig(
        queries=queries,
        excluded_keywords=_strings(
            search_raw.get("excluded_keywords", []), "search.excluded_keywords"
        ),
        area_ids=_strings(search_raw.get("area_ids", ["113"]), "search.area_ids"),
        experience_ids=_strings(search_raw.get("experience_ids", []), "search.experience_ids"),
        remote_only=bool(search_raw.get("remote_only", True)),
        strict_remote=bool(search_raw.get("strict_remote", True)),
        days=_bounded_int(search_raw.get("days", 7), "search.days", 1, 30),
        per_query=_bounded_int(search_raw.get("per_query", 50), "search.per_query", 1, 500),
        sources=_strings(search_raw.get("sources", ["hh"]), "search.sources"),
        kinds=_strings(search_raw.get("kinds", ["job", "freelance"]), "search.kinds"),
    )
    _validate_search(search)
    return AppConfig(
        search=load_search_settings(search, search_settings_path(database_path)),
        hh=HhConfig(
            base_url=str(hh_raw.get("base_url", "https://api.hh.ru")).rstrip("/"),
            user_agent=str(hh_raw.get("user_agent", "JobSearchAutomation/0.1")),
            timeout_seconds=_bounded_int(
                hh_raw.get("timeout_seconds", 20), "hh.timeout_seconds", 1, 120
            ),
            browser_fallback=bool(hh_raw.get("browser_fallback", True)),
        ),
        database_path=database_path,
        reports_dir=reports_dir,
        telegram=TelegramConfig(
            bot_token=(
                os.environ.get("TELEGRAM_BOT_TOKEN") or str(telegram_raw.get("bot_token", ""))
            ).strip(),
            allowed_user_ids=tuple(allowed_ids),
            page_size=_bounded_int(telegram_raw.get("page_size", 5), "telegram.page_size", 1, 10),
        ),
        profile_path=root / str(telegram_raw.get("profile", "profile.json")),
        superjob_app_key=os.environ.get("SUPERJOB_APP_KEY", "").strip(),
    )
