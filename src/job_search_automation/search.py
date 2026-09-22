from __future__ import annotations

from dataclasses import dataclass

from job_search_automation.config import AppConfig
from job_search_automation.filters import rejection_reason
from job_search_automation.hh import HhClient
from job_search_automation.models import Vacancy
from job_search_automation.storage import VacancyStore


@dataclass(frozen=True, slots=True)
class ScanResult:
    new_items: list[Vacancy]
    fetched_count: int
    accepted_count: int
    transport: str


def scan_vacancies(config: AppConfig) -> ScanResult:
    client = HhClient(config.hh)
    fetched = client.search(config.search)
    accepted = [item for item in fetched if rejection_reason(item, config.search) is None]
    with VacancyStore(config.database_path) as store:
        new_items = store.save(accepted)
    return ScanResult(new_items, len(fetched), len(accepted), client.last_transport)
