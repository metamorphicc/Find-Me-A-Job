from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import requests
from playwright.sync_api import Error as PlaywrightError

from job_search_automation.config import AppConfig, SearchConfig
from job_search_automation.filters import rejection_reason
from job_search_automation.hh import HhClient
from job_search_automation.models import Vacancy
from job_search_automation.public_sources import public_providers
from job_search_automation.storage import VacancyStore
from job_search_automation.superjob import SuperJobClient


@dataclass(frozen=True, slots=True)
class ScanResult:
    new_items: list[Vacancy]
    fetched_count: int
    accepted_count: int
    transport: str
    errors: tuple[str, ...] = ()


class SearchProvider(Protocol):
    last_transport: str

    def search(self, settings: SearchConfig) -> list[Vacancy]: ...


class SearchError(RuntimeError):
    """Raised when no configured source can be searched."""


def build_providers(config: AppConfig) -> dict[str, SearchProvider]:
    return {
        "hh": HhClient(config.hh),
        "superjob": SuperJobClient(config.superjob_app_key),
        **public_providers(),
    }


def scan_vacancies(config: AppConfig) -> ScanResult:
    return scan_with_providers(config, build_providers(config))


def scan_with_providers(
    config: AppConfig, providers: dict[str, SearchProvider]
) -> ScanResult:
    requested = tuple(dict.fromkeys(config.search.sources))
    unknown = set(requested) - set(providers)
    if unknown:
        raise SearchError(f"Неизвестные источники: {', '.join(sorted(unknown))}")
    if not requested:
        raise SearchError("Включите хотя бы один источник поиска")

    fetched: list[Vacancy] = []
    transports: list[str] = []
    errors: list[str] = []
    for name in requested:
        client = providers[name]
        try:
            items = client.search(config.search)
        except (RuntimeError, OSError, ValueError, requests.RequestException, PlaywrightError) as exc:
            errors.append(f"{name}: {exc}")
            continue
        fetched.extend(items)
        transports.append(f"{name}:{client.last_transport}")
    if not transports:
        raise SearchError("Все источники недоступны: " + "; ".join(errors))

    accepted = [item for item in fetched if rejection_reason(item, config.search) is None]
    with VacancyStore(config.database_path) as store:
        new_items = store.save(accepted)
    return ScanResult(new_items, len(fetched), len(accepted), ", ".join(transports), tuple(errors))
