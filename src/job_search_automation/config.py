from __future__ import annotations

import tomllib
from dataclasses import dataclass as dc
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when the local search configuration is invalid."""


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


@dc(frozen=True, slots=True)
class HhConfig:
    base_url: str
    user_agent: str
    timeout_seconds: int
    browser_fallback: bool = True


@dc(frozen=True, slots=True)
class AppConfig:
    search: SearchConfig
    hh: HhConfig
    database_path: Path
    reports_dir: Path


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
    if not all(isinstance(value, dict) for value in (search_raw, hh_raw, storage_raw)):
        raise ConfigError("search, hh, and storage must be TOML tables")

    queries = _strings(search_raw.get("queries"), "search.queries")
    if not queries:
        raise ConfigError("search.queries must contain at least one query")

    root = config_path.resolve().parent
    database_path = root / str(storage_raw.get("database", "data/jobs.db"))
    reports_dir = root / str(storage_raw.get("reports_dir", "reports"))

    return AppConfig(
        search=SearchConfig(
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
        ),
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
    )
