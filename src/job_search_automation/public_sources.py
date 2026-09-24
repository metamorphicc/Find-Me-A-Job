from __future__ import annotations

import hashlib
import html
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import requests

from job_search_automation.categories import (
    REMOTIVE_CATEGORIES,
    WWR_CATEGORIES,
    technical_role_title,
)
from job_search_automation.config import SearchConfig
from job_search_automation.models import Vacancy


class SourceError(RuntimeError):
    """Raised for an unavailable or malformed public opportunity feed."""


def _plain(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", value))).strip()


def _query(title: str, summary: str, queries: tuple[str, ...]) -> str | None:
    haystack = f"{title} {summary}".casefold()
    for query in queries:
        words = re.findall(r"[^\W_]{3,}", query.casefold())
        if words and any(word in haystack for word in words):
            return query
    return None


def _recent(value: str, days: int, *, rss: bool = False) -> bool:
    try:
        date = parsedate_to_datetime(value) if rss else datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    if date.tzinfo is None:
        date = date.replace(tzinfo=UTC)
    return date >= datetime.now(UTC) - timedelta(days=days)


def _id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]


def _item_text(item: ET.Element, name: str) -> str:
    return (item.findtext(name) or "").strip()


class RemotiveClient:
    last_transport = "api"
    url = "https://remotive.com/api/remote-jobs"

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    def search(self, settings: SearchConfig) -> list[Vacancy]:
        # Remotive asks consumers to fetch at most a few times per day, so fetch once.
        try:
            response = self.session.get(self.url, timeout=25)
            response.raise_for_status()
            jobs = response.json().get("jobs")
        except (requests.RequestException, ValueError, AttributeError) as exc:
            raise SourceError("Remotive API недоступен") from exc
        if not isinstance(jobs, list):
            raise SourceError("Remotive вернул неожиданный формат")
        result = []
        for item in jobs:
            if not isinstance(item, dict):
                continue
            title = _plain(str(item.get("title") or ""))
            summary = _plain(str(item.get("description") or ""))
            category = REMOTIVE_CATEGORIES.get(str(item.get("category") or ""))
            if settings.categories:
                if category not in settings.categories:
                    continue
                query = ""
            else:
                query = _query(title, summary, settings.queries)
            published = str(item.get("publication_date") or "")
            url = str(item.get("url") or "")
            if query is None or not _recent(published, settings.days) or not url.startswith("https://remotive.com/"):
                continue
            job_type = str(item.get("job_type") or "").casefold()
            result.append(
                Vacancy(
                    source="remotive",
                    source_id=str(item.get("id") or _id(url)),
                    title=title,
                    company=_plain(str(item.get("company_name") or "Не указана")),
                    url=url,
                    area=str(item.get("candidate_required_location") or "Не указана"),
                    published_at=published,
                    work_formats=("REMOTE",),
                    experience="Не указан",
                    employment=job_type or "Не указан",
                    salary_from=None,
                    salary_to=None,
                    salary_currency=None,
                    salary_gross=None,
                    summary=summary[:1200],
                    query=query,
                    kind="freelance" if job_type in {"freelance", "contract"} else "job",
                    market="global",
                    location_scope=str(item.get("candidate_required_location") or "Не указана"),
                    pay_label=str(item.get("salary") or "Не указана"),
                    categories=(category,) if category else (),
                )
            )
            limit = settings.per_query if settings.categories else settings.per_query * len(settings.queries)
            if len(result) >= limit:
                break
        return result


class RssClient:
    last_transport = "rss"

    def __init__(
        self, source: str, url: str, market: str, *, session: requests.Session | None = None
    ) -> None:
        self.source = source
        self.url = url
        self.market = market
        self.session = session or requests.Session()

    def search(self, settings: SearchConfig) -> list[Vacancy]:
        if self.source == "fl" and settings.categories:
            raise SourceError("FL.ru RSS не содержит проверяемых профессиональных категорий")
        try:
            response = self.session.get(self.url, timeout=25)
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except (requests.RequestException, ET.ParseError) as exc:
            raise SourceError(f"RSS {self.source} недоступен") from exc
        items = root.findall("./channel/item")
        if root.find("./channel") is None:
            raise SourceError(f"RSS {self.source} вернул неожиданный формат")
        result = []
        for item in items:
            title = _plain(_item_text(item, "title"))
            summary = _plain(_item_text(item, "description"))
            category = WWR_CATEGORIES.get(_item_text(item, "category")) if self.source == "wwr" else None
            if settings.categories:
                if category not in settings.categories or not technical_role_title(title):
                    continue
                query = ""
            else:
                query = _query(title, summary, settings.queries)
            published = _item_text(item, "pubDate")
            url = _item_text(item, "link")
            host = urlparse(url).hostname or ""
            expected = "weworkremotely.com" if self.source == "wwr" else "fl.ru"
            if (
                query is None
                or not _recent(published, settings.days, rss=True)
                or urlparse(url).scheme != "https"
                or (host != expected and not host.endswith("." + expected))
            ):
                continue
            if self.source == "wwr":
                company, _, role = title.partition(": ")
                company = company if role else "Не указана"
                title = role or title
                location = _item_text(item, "region") or "Не указана"
                job_type = _item_text(item, "type")
                kind = "freelance" if job_type.casefold() in {"contract", "freelance"} else "job"
            else:
                company = "Заказчик FL.ru"
                location = "Удалённо; условия уточнить"
                job_type = "Заказ"
                kind = "freelance"
            result.append(
                Vacancy(
                    source=self.source,
                    source_id=_id(url),
                    title=title,
                    company=company,
                    url=url,
                    area=location,
                    published_at=published,
                    work_formats=("REMOTE",),
                    experience="Не указан",
                    employment=job_type,
                    salary_from=None,
                    salary_to=None,
                    salary_currency=None,
                    salary_gross=None,
                    summary=summary[:1200],
                    query=query,
                    kind=kind,
                    market=self.market,
                    location_scope=location,
                    categories=(category,) if category else (),
                )
            )
            limit = settings.per_query if settings.categories else settings.per_query * len(settings.queries)
            if len(result) >= limit:
                break
        return result


def public_providers() -> dict[str, Any]:
    return {
        "remotive": RemotiveClient(),
        "wwr": RssClient("wwr", "https://weworkremotely.com/remote-jobs.rss", "global"),
        "fl": RssClient("fl", "https://www.fl.ru/rss/all.xml", "ru"),
    }
