from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import requests

from job_search_automation.config import SearchConfig
from job_search_automation.models import Vacancy
from job_search_automation.public_sources import SourceError, _plain

# IDs from Freelancehunt's public /v2/skills directory. Keep the list focused on
# the user's coding, automation and AI work; the text filter makes the final call.
SOFTWARE_SKILL_IDS = (22, 28, 86, 99, 169, 175, 180, 182)


class FreelancehuntClient:
    """Read open technical projects from Freelancehunt's public projects API."""

    last_transport = "api"
    endpoint = "https://api.freelancehunt.com/v2/projects"
    max_pages = 10

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    def search(self, settings: SearchConfig) -> list[Vacancy]:
        cutoff = datetime.now(UTC) - timedelta(days=settings.days)
        found: dict[str, Vacancy] = {}
        self.last_pages = 0
        self.last_truncated = False
        for page in range(1, self.max_pages + 1):
            try:
                response = self.session.get(
                    self.endpoint,
                    params={
                        "filter[skill_id]": ",".join(map(str, SOFTWARE_SKILL_IDS)),
                        "page[number]": page,
                    },
                    headers={"Accept-Language": "en"},
                    timeout=25,
                )
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                raise SourceError("Freelancehunt API недоступен") from exc
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise SourceError("Freelancehunt вернул неожиданный формат")
            self.last_pages += 1
            projects = payload["data"]
            for project in projects:
                vacancy = vacancy_from_project(project, cutoff)
                if vacancy is not None:
                    found[vacancy.source_id] = vacancy
            links = payload.get("links")
            has_next = isinstance(links, dict) and bool(links.get("next"))
            if not projects or not has_next:
                break
            if page == self.max_pages:
                self.last_truncated = True
        return list(found.values())


def vacancy_from_project(item: Any, cutoff: datetime) -> Vacancy | None:
    if not isinstance(item, dict) or item.get("type") != "project":
        return None
    project_id = item.get("id")
    attributes = item.get("attributes")
    links = item.get("links")
    if not isinstance(project_id, int) or not isinstance(attributes, dict) or not isinstance(links, dict):
        return None
    status = attributes.get("status")
    if not isinstance(status, dict) or status.get("id") != 11 or attributes.get("is_personal"):
        return None
    # The platform uses is_remote_job for a vacancy-like listing, not for an
    # ordinary remote freelance project. Keep this search focused on orders.
    if attributes.get("is_remote_job"):
        return None
    published = attributes.get("published_at")
    if not isinstance(published, str):
        return None
    try:
        published_at = datetime.fromisoformat(published)
    except ValueError:
        return None
    if published_at.tzinfo is None or published_at < cutoff:
        return None
    self_links = links.get("self")
    url = self_links.get("web") if isinstance(self_links, dict) else None
    try:
        parsed = urlparse(url) if isinstance(url, str) else None
    except ValueError:
        return None
    if parsed is None or parsed.scheme != "https" or parsed.hostname != "freelancehunt.com":
        return None
    title = _plain(str(attributes.get("name") or ""))
    description = _plain(str(attributes.get("description") or ""))
    if not title or not description:
        return None
    skills = attributes.get("skills")
    skill_names = [
        _plain(skill["name"]) for skill in skills
        if isinstance(skill, dict) and isinstance(skill.get("name"), str)
    ] if isinstance(skills, list) else []
    skill_ids = {
        skill.get("id") for skill in skills if isinstance(skill, dict)
    } if isinstance(skills, list) else set()
    if not skill_ids.intersection(SOFTWARE_SKILL_IDS):
        return None
    budget = attributes.get("budget")
    amount = "Не указан"
    budget_max = None
    budget_currency = None
    if isinstance(budget, dict) and isinstance(budget.get("amount"), (int, float)):
        amount = f"{budget['amount']:g} {budget.get('currency') or ''}".strip()
        budget_max = float(budget["amount"])
        budget_currency = budget.get("currency") if isinstance(budget.get("currency"), str) else None
    location = attributes.get("location")
    country = location.get("country") if isinstance(location, dict) else None
    area = _plain(str(country.get("name") or "")) if isinstance(country, dict) else ""
    area = area or "Страна заказчика не указана"
    summary = f"{description}\nНавыки: {', '.join(skill_names)}" if skill_names else description
    return Vacancy(
        source="freelancehunt",
        source_id=str(project_id),
        title=title,
        company="Заказчик Freelancehunt",
        url=url,
        area=area,
        published_at=published_at.isoformat(),
        work_formats=("REMOTE",),
        experience="Не указан",
        employment="Заказ",
        salary_from=None,
        salary_to=None,
        salary_currency=None,
        salary_gross=None,
        summary=summary[:1200],
        query="",
        kind="freelance",
        market="global",
        location_scope=area,
        pay_label=amount,
        categories=("software",),
        budget_max=budget_max,
        budget_currency=budget_currency,
        budget_unit="project" if budget_max is not None else None,
    )
