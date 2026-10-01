from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Protocol

import requests
from playwright.sync_api import Error as PlaywrightError

from job_search_automation.config import AppConfig, SearchConfig
from job_search_automation.filters import rejection_reason
from job_search_automation.freelancehunt import FreelancehuntClient
from job_search_automation.freelancer import FreelancerClient
from job_search_automation.hh import HhClient
from job_search_automation.job_boards import JobicyClient, RemoteOkClient
from job_search_automation.models import Vacancy
from job_search_automation.public_sources import public_providers
from job_search_automation.storage import VacancyStore
from job_search_automation.superjob import SuperJobClient


@dataclass(frozen=True, slots=True)
class SourceStats:
    name: str
    fetched: int
    accepted: int
    rejected: tuple[tuple[str, int], ...]
    shown: int = 0
    pages: int = 1
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class ScanResult:
    new_items: list[Vacancy]
    fetched_count: int
    accepted_count: int
    transport: str
    errors: tuple[str, ...] = ()
    source_stats: tuple[SourceStats, ...] = ()
    review_items: list[tuple[Vacancy, str]] = field(default_factory=list)


class SearchProvider(Protocol):
    last_transport: str

    def search(self, settings: SearchConfig) -> list[Vacancy]: ...


class SearchError(RuntimeError):
    """Raised when no configured source can be searched."""


_IMPLEMENTATION_TITLE = re.compile(
    r"\b(?:build|develop|implement|integrat\w*|automat\w*|code|script|"
    r"api|backend|frontend|website|web app|saas|agent|bot|crm|"
    r"разработ\w*|созда\w*|напис\w*|интегр\w*|автоматиз\w*|бот|"
    r"розроб\w*|створ\w*)\b",
    re.IGNORECASE,
)


def _reviewable(vacancy: Vacancy, reason: str | None, config: SearchConfig) -> bool:
    return (
        vacancy.kind == "freelance"
        and reason == "title and description do not mention the selected stack"
        and "software" in vacancy.categories
        and bool(_IMPLEMENTATION_TITLE.search(vacancy.title))
        and rejection_reason(vacancy, replace(config, stack_keywords=())) is None
    )


def build_providers(config: AppConfig) -> dict[str, SearchProvider]:
    return {
        "hh": HhClient(config.hh),
        "superjob": SuperJobClient(config.superjob_app_key),
        "freelancer": FreelancerClient(),
        "freelancehunt": FreelancehuntClient(),
        "remoteok": RemoteOkClient(),
        "jobicy": JobicyClient(),
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

    fetched_count = 0
    matched_count = 0
    accepted: list[Vacancy] = []
    transports: list[str] = []
    errors: list[str] = []
    stats: list[SourceStats] = []
    review_items: list[tuple[Vacancy, str]] = []
    for name in requested:
        client = providers[name]
        try:
            items = client.search(config.search)
        except (RuntimeError, OSError, ValueError, requests.RequestException, PlaywrightError) as exc:
            errors.append(f"{name}: {exc}")
            continue
        fetched_count += len(items)
        transports.append(f"{name}:{client.last_transport}")
        decisions = [(item, rejection_reason(item, config.search)) for item in items]
        reasons = Counter(reason for _, reason in decisions if reason)
        matched = [item for item, reason in decisions if reason is None]
        matched_count += len(matched)
        accepted.extend(matched[:config.search.per_query])
        review_items.extend(
            (item, reason) for item, reason in decisions
            if reason is not None and _reviewable(item, reason, config.search)
        )
        stats.append(SourceStats(
            name=name,
            fetched=len(items),
            accepted=len(items) - sum(reasons.values()),
            rejected=tuple(reasons.most_common()),
            shown=min(len(matched), config.search.per_query),
            pages=getattr(client, "last_pages", 1),
            truncated=getattr(client, "last_truncated", False),
        ))
    if not transports:
        raise SearchError("Все источники недоступны: " + "; ".join(errors))

    with VacancyStore(config.database_path) as store:
        new_items = store.save(accepted)
        store.save_review_candidates(review_items)
    return ScanResult(
        new_items, fetched_count, matched_count, ", ".join(transports), tuple(errors),
        tuple(stats), review_items,
    )
