from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import requests

from job_search_automation.config import SearchConfig
from job_search_automation.models import Vacancy
from job_search_automation.public_sources import SourceError, _plain


class SuperJobClient:
    last_transport = "api"
    endpoint = "https://api.superjob.ru/2.0/vacancies/"

    def __init__(self, app_key: str, session: requests.Session | None = None) -> None:
        self.app_key = app_key
        self.session = session or requests.Session()

    def search(self, settings: SearchConfig) -> list[Vacancy]:
        if settings.categories:
            raise SourceError("SuperJob пока не поддерживает фильтр профессиональных категорий")
        if not self.app_key:
            raise SourceError("Для SuperJob укажите SUPERJOB_APP_KEY")
        found: dict[str, Vacancy] = {}
        for query in settings.queries:
            try:
                response = self.session.get(
                    self.endpoint,
                    params={"keyword": query, "count": min(settings.per_query, 100), "period": settings.days},
                    headers={"X-Api-App-Id": self.app_key},
                    timeout=25,
                )
                response.raise_for_status()
                objects = response.json().get("objects")
            except (requests.RequestException, ValueError, AttributeError) as exc:
                raise SourceError("SuperJob API недоступен; проверьте ключ и сеть") from exc
            if not isinstance(objects, list):
                raise SourceError("SuperJob вернул неожиданный формат")
            for item in objects:
                vacancy = vacancy_from_superjob(item, query)
                if vacancy is not None:
                    found[vacancy.source_id] = vacancy
        return list(found.values())


def vacancy_from_superjob(item: Any, query: str) -> Vacancy | None:
    if not isinstance(item, dict):
        return None
    source_id = str(item.get("id") or "")
    url = str(item.get("link") or "")
    if not source_id.isdigit() or urlparse(url).hostname not in {"superjob.ru", "www.superjob.ru"}:
        return None
    place = item.get("place_of_work") or {}
    place_label = str(place.get("title") or "") if isinstance(place, dict) else str(place)
    remote = "удал" in place_label.casefold() or "remote" in place_label.casefold()
    town = item.get("town") or {}
    area = str(town.get("title") or "Россия") if isinstance(town, dict) else "Россия"
    published = item.get("date_published")
    if isinstance(published, int):
        published_at = datetime.fromtimestamp(published, UTC).isoformat()
    else:
        published_at = ""
    return Vacancy(
        source="superjob",
        source_id=source_id,
        title=_plain(str(item.get("profession") or "Вакансия")),
        company=_plain(str(item.get("firm_name") or "Не указана")),
        url=url,
        area=area,
        published_at=published_at,
        work_formats=("REMOTE",) if remote else ("ON_SITE",),
        experience="Не указан",
        employment="Не указана",
        salary_from=item.get("payment_from") or None,
        salary_to=item.get("payment_to") or None,
        salary_currency=str(item.get("currency") or "RUR"),
        salary_gross=None,
        summary=_plain(str(item.get("candidat") or item.get("work") or ""))[:1200],
        query=query,
        market="ru",
        location_scope=place_label or area,
    )
