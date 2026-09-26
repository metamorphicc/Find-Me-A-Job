from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from job_search_automation.categories import freelancer_categories, freelancer_job_ids
from job_search_automation.config import SearchConfig
from job_search_automation.models import Vacancy
from job_search_automation.public_sources import SourceError, _plain


class FreelancerClient:
    """Read public active projects without logging in or placing bids."""

    last_transport = "api"
    endpoint = "https://www.freelancer.com/api/projects/0.1/projects/active/"

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    def search(self, settings: SearchConfig) -> list[Vacancy]:
        found: dict[str, Vacancy] = {}
        cutoff = datetime.now(UTC) - timedelta(days=settings.days)
        for query in ("",) if settings.categories else settings.queries:
            params: dict[str, object] = {
                "limit": 100 if settings.title_keywords else min(settings.per_query, 100),
                "sort_field": "time_updated",
                "sort_order": "desc",
            }
            if settings.categories:
                params["jobs[]"] = freelancer_job_ids(settings.categories)
                params["job_details"] = "true"
            else:
                params["query"] = query
            try:
                response = self.session.get(
                    self.endpoint,
                    params=params,
                    timeout=25,
                )
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                raise SourceError("Freelancer.com API недоступен") from exc
            if not isinstance(payload, dict) or payload.get("status") != "success":
                raise SourceError("Freelancer.com вернул ошибку")
            result = payload.get("result")
            projects = result.get("projects") if isinstance(result, dict) else None
            if not isinstance(projects, list):
                raise SourceError("Freelancer.com вернул неожиданный формат")
            for project in projects:
                vacancy = vacancy_from_project(project, query, cutoff)
                if vacancy is not None and (
                    not settings.categories
                    or set(vacancy.categories).intersection(settings.categories)
                ) and (
                    not settings.title_keywords
                    or any(word.casefold() in vacancy.title.casefold() for word in settings.title_keywords)
                ):
                    found[vacancy.source_id] = vacancy
        return list(found.values())


def vacancy_from_project(item: Any, query: str, cutoff: datetime) -> Vacancy | None:
    if not isinstance(item, dict) or item.get("status") != "active":
        return None
    project_id = item.get("id")
    slug = item.get("seo_url")
    submitted = item.get("submitdate")
    if (
        not isinstance(project_id, int)
        or project_id <= 0
        or not isinstance(slug, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_-]+", slug)
        or not isinstance(submitted, int)
    ):
        return None
    try:
        published = datetime.fromtimestamp(submitted, UTC)
    except (OverflowError, OSError, ValueError):
        return None
    if published < cutoff:
        return None
    budget = item.get("budget")
    currency = item.get("currency")
    amount = "Не указан"
    if isinstance(budget, dict):
        low, high = budget.get("minimum"), budget.get("maximum")
        code = currency.get("code") if isinstance(currency, dict) else None
        if isinstance(low, (int, float)) and isinstance(high, (int, float)):
            amount = f"{low:g}–{high:g} {code or ''}".strip()
    location = item.get("location")
    country = location.get("country") if isinstance(location, dict) else None
    country_name = country.get("name") if isinstance(country, dict) else None
    area = str(country_name or "География не указана")
    categories = freelancer_categories(item.get("jobs"))
    return Vacancy(
        source="freelancer",
        source_id=str(project_id),
        title=_plain(str(item.get("title") or "Заказ")),
        company="Заказчик Freelancer.com",
        url=f"https://www.freelancer.com/projects/{slug}/details",
        area=area,
        published_at=published.isoformat(),
        work_formats=("ON_SITE",) if item.get("local") or item.get("type") == "local" else ("REMOTE",),
        experience="Не указан",
        employment=str(item.get("type") or "project"),
        salary_from=None,
        salary_to=None,
        salary_currency=None,
        salary_gross=None,
        summary=_plain(str(item.get("preview_description") or ""))[:1200],
        query=query,
        kind="freelance",
        market="global",
        location_scope=area,
        pay_label=amount,
        categories=categories,
    )
