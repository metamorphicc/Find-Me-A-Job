from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import requests

from job_search_automation.categories import technical_role_title
from job_search_automation.config import SearchConfig
from job_search_automation.models import Vacancy
from job_search_automation.public_sources import SourceError, _plain, _query, _recent


def _board_url(value: Any, host: str) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    return value if parsed.scheme == "https" and parsed.hostname in {host, f"www.{host}"} else None


class RemoteOkClient:
    last_transport = "api"
    endpoint = "https://remoteok.com/api"

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    def search(self, settings: SearchConfig) -> list[Vacancy]:
        try:
            response = self.session.get(
                self.endpoint,
                params={"tag": "dev"},
                headers={"User-Agent": "JobSearchAutomation/0.1 (remote job discovery)"},
                timeout=25,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise SourceError("Remote OK API недоступен") from exc
        if not isinstance(payload, list):
            raise SourceError("Remote OK вернул неожиданный формат")
        result = []
        for item in payload:
            if not isinstance(item, dict) or "id" not in item:
                continue  # The first entry contains feed metadata and attribution terms.
            title = _plain(str(item.get("position") or ""))
            summary = _plain(str(item.get("description") or ""))
            url = _board_url(item.get("url"), "remoteok.com")
            published = item.get("date")
            if (
                not str(item.get("id") or "").isdigit()
                or not title or not technical_role_title(title) or not url
                or not isinstance(published, str) or not _recent(published, settings.days)
            ):
                continue
            query = "" if settings.categories else _query(title, summary, settings.queries)
            if query is None:
                continue
            location = _plain(str(item.get("location") or "")) or "Не указана"
            low, high = item.get("salary_min"), item.get("salary_max")
            pay = (
                f"{low:g}–{high:g} (валюта/период уточнить)"
                if isinstance(low, (int, float)) and isinstance(high, (int, float)) and high > 0
                else "Не указана"
            )
            result.append(Vacancy(
                source="remoteok", source_id=str(item["id"]), title=title,
                company=_plain(str(item.get("company") or "Не указана")), url=url,
                area=location, published_at=published, work_formats=("REMOTE",),
                experience="Не указан", employment="Не указан", salary_from=None,
                salary_to=None, salary_currency=None, salary_gross=None,
                summary=summary[:1200], query=query, kind="job", market="global",
                location_scope=location, pay_label=pay, categories=("software",),
            ))
            if len(result) >= settings.per_query:
                break
        return result


class JobicyClient:
    last_transport = "api"
    endpoint = "https://jobicy.com/api/v2/remote-jobs"

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    def search(self, settings: SearchConfig) -> list[Vacancy]:
        try:
            response = self.session.get(
                self.endpoint,
                params={"count": min(200, settings.per_query), "industry": "engineering"},
                timeout=25,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise SourceError("Jobicy API недоступен") from exc
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs, list):
            raise SourceError("Jobicy вернул неожиданный формат")
        result = []
        for item in jobs:
            if not isinstance(item, dict):
                continue
            title = _plain(str(item.get("jobTitle") or ""))
            summary = _plain(str(item.get("jobDescription") or ""))
            url = _board_url(item.get("url"), "jobicy.com")
            published = item.get("pubDate")
            industries = item.get("jobIndustry")
            if (
                not str(item.get("id") or "").isdigit()
                or not title or not technical_role_title(title) or not url
                or not isinstance(published, str) or not _recent(published, settings.days)
                or not isinstance(industries, list)
                or not any("engineering" in str(name).casefold() for name in industries)
            ):
                continue
            query = "" if settings.categories else _query(title, summary, settings.queries)
            if query is None:
                continue
            location = _plain(str(item.get("jobGeo") or "")) or "Не указана"
            job_types = item.get("jobType")
            employment = ", ".join(str(value) for value in job_types) if isinstance(job_types, list) else ""
            result.append(Vacancy(
                source="jobicy", source_id=str(item.get("id") or ""), title=title,
                company=_plain(str(item.get("companyName") or "Не указана")), url=url,
                area=location, published_at=published, work_formats=("REMOTE",),
                experience=_plain(str(item.get("jobLevel") or "Не указан")),
                employment=employment or "Не указан", salary_from=None,
                salary_to=None, salary_currency=None, salary_gross=None,
                summary=summary[:1200], query=query, kind="job", market="global",
                location_scope=location, pay_label="Не указана", categories=("software",),
            ))
        return result
