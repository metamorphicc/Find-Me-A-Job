from __future__ import annotations

import html
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlencode

import requests
from playwright.sync_api import Locator, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from job_search_automation.categories import HH_ROLES, hh_role_ids
from job_search_automation.config import HhConfig, SearchConfig
from job_search_automation.models import Vacancy


class HhApiError(RuntimeError):
    """Raised when HeadHunter cannot return a usable vacancy response."""


def _clean_html(value: str | None) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()


def _salary(item: dict[str, Any]) -> tuple[int | None, int | None, str | None, bool | None]:
    value = item.get("salary_range") or item.get("salary") or {}
    if not isinstance(value, dict):
        return None, None, None, None
    return value.get("from"), value.get("to"), value.get("currency"), value.get("gross")


def vacancy_from_hh(
    item: dict[str, Any], query: str, selected_categories: tuple[str, ...] = ()
) -> Vacancy:
    work_formats = tuple(
        str(value.get("id"))
        for value in (item.get("work_format") or [])
        if isinstance(value, dict) and value.get("id")
    )
    schedule = item.get("schedule") or {}
    if not work_formats and isinstance(schedule, dict) and schedule.get("id") == "remote":
        work_formats = ("REMOTE",)

    salary_from, salary_to, salary_currency, salary_gross = _salary(item)
    snippet = item.get("snippet") or {}
    summary = _clean_html(
        " ".join((str(snippet.get("requirement") or ""), str(snippet.get("responsibility") or "")))
    )
    employment = item.get("employment_form") or item.get("employment") or {}
    roles = item.get("professional_roles") or []
    role_ids = {str(role.get("id")) for role in roles if isinstance(role, dict)}
    categories = tuple(
        category for category, ids in HH_ROLES.items() if role_ids.intersection(ids)
    ) or selected_categories

    return Vacancy(
        source="hh",
        source_id=str(item["id"]),
        title=str(item.get("name") or "Без названия"),
        company=str((item.get("employer") or {}).get("name") or "Не указана"),
        url=str(item.get("alternate_url") or item.get("url") or ""),
        area=str((item.get("area") or {}).get("name") or "Не указана"),
        published_at=str(item.get("published_at") or ""),
        work_formats=work_formats,
        experience=str((item.get("experience") or {}).get("name") or "Не указан"),
        employment=str(employment.get("name") or "Не указана"),
        salary_from=salary_from,
        salary_to=salary_to,
        salary_currency=salary_currency,
        salary_gross=salary_gross,
        summary=summary,
        query=query,
        categories=categories,
    )


def _locator_text(parent: Locator, selector: str) -> str:
    locator = parent.locator(selector)
    return _clean_html(locator.first.inner_text()) if locator.count() else ""


class HhClient:
    def __init__(self, config: HhConfig) -> None:
        self.config = config
        self.last_transport = "api"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": config.user_agent,
                "HH-User-Agent": config.user_agent,
                "Accept": "application/json",
            }
        )
        retries = Retry(
            total=3,
            backoff_factor=0.8,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

    def search(self, search: SearchConfig) -> list[Vacancy]:
        try:
            self.last_transport = "api"
            return self._search_api(search)
        except HhApiError as api_error:
            if not self.config.browser_fallback:
                raise
            try:
                self.last_transport = "browser"
                return self._search_browser(search)
            except HhApiError as browser_error:
                raise HhApiError(
                    f"API failed ({api_error}); browser fallback failed ({browser_error})"
                ) from browser_error

    def _search_api(self, search: SearchConfig) -> list[Vacancy]:
        unique: dict[str, Vacancy] = {}
        for query in ("",) if search.categories else search.queries:
            for vacancy in self._search_query(query, search):
                unique.setdefault(vacancy.source_id, vacancy)
        return list(unique.values())

    def _search_browser(self, search: SearchConfig) -> list[Vacancy]:
        unique: dict[str, Vacancy] = {}
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(locale="ru-RU")
                try:
                    for query in ("",) if search.categories else search.queries:
                        page_number = 0
                        query_count = 0
                        while query_count < search.per_query:
                            params: list[tuple[str, str | int]] = [
                                ("page", page_number),
                                ("period", search.days),
                                ("order_by", "publication_time"),
                            ]
                            if query:
                                params.append(("text", query))
                            params.extend(
                                ("professional_role", role) for role in hh_role_ids(search.categories)
                            )
                            params.extend(("area", value) for value in search.area_ids)
                            params.extend(("experience", value) for value in search.experience_ids)
                            if search.remote_only:
                                params.append(("work_format", "REMOTE"))
                            url = f"https://hh.ru/search/vacancy?{urlencode(params)}"
                            response = page.goto(
                                url,
                                wait_until="domcontentloaded",
                                timeout=self.config.timeout_seconds * 1000,
                            )
                            if response is not None and response.status >= 400:
                                raise HhApiError(f"HH search page returned HTTP {response.status}")
                            page.wait_for_timeout(1200)
                            titles = page.locator('[data-qa="serp-item__title"]')
                            card_count = titles.count()
                            if card_count == 0:
                                body = page.locator("body").inner_text().casefold()
                                if "captcha" in body or "капча" in body:
                                    raise HhApiError("HH search page requested CAPTCHA")
                                break

                            for index in range(card_count):
                                vacancy = self._vacancy_from_card(
                                    titles.nth(index), query, search.categories
                                )
                                unique.setdefault(vacancy.source_id, vacancy)
                                query_count += 1
                                if query_count >= search.per_query:
                                    break
                            if card_count < 20:
                                break
                            page_number += 1
                finally:
                    browser.close()
        except (PlaywrightTimeoutError, OSError) as exc:
            raise HhApiError(f"browser search failed: {exc}") from exc
        return list(unique.values())

    def _vacancy_from_card(
        self, title: Locator, query: str, categories: tuple[str, ...] = ()
    ) -> Vacancy:
        card = title.locator("xpath=ancestor::div[@id][1]")
        url = str(title.get_attribute("href") or "")
        source_id = str(card.get_attribute("id") or "")
        if not source_id:
            match = re.search(r"/vacancy/(\d+)", url)
            if match is None:
                raise HhApiError(f"Could not identify vacancy card: {url}")
            source_id = match.group(1)

        card_text = _clean_html(card.inner_text())
        folded = card_text.casefold()
        has_remote_label = card.locator('[data-qa="vacancy-label-work-schedule-remote"]').count()
        work_formats = ["REMOTE"] if has_remote_label else []
        if "гибрид" in folded:
            work_formats.append("HYBRID")
        if "на месте работодателя" in folded:
            work_formats.append("ON_SITE")
        if "разъезд" in folded:
            work_formats.append("FIELD_WORK")

        return Vacancy(
            source="hh",
            source_id=source_id,
            title=_clean_html(title.inner_text()),
            company=_locator_text(card, '[data-qa="vacancy-serp__vacancy-employer-text"]')
            or "Не указана",
            url=url,
            area=_locator_text(card, '[data-qa="vacancy-serp__vacancy-address"]') or "Не указана",
            published_at="",
            work_formats=tuple(work_formats),
            experience=_locator_text(card, '[data-qa^="vacancy-serp__vacancy-work-experience"]')
            or "Не указан",
            employment="Не указана",
            salary_from=None,
            salary_to=None,
            salary_currency=None,
            salary_gross=None,
            summary=card_text,
            query=query,
            categories=categories,
        )

    def _search_query(self, query: str, search: SearchConfig) -> Iterable[Vacancy]:
        per_page = min(100, search.per_query)
        page = 0
        yielded = 0
        while yielded < search.per_query:
            params: list[tuple[str, str | int | bool]] = [
                ("page", page),
                ("per_page", min(per_page, search.per_query - yielded)),
                ("period", search.days),
                ("order_by", "publication_time"),
                ("no_magic", True),
                ("locale", "RU"),
                ("host", "hh.ru"),
            ]
            if query:
                params.append(("text", query))
            params.extend(
                ("professional_role", role) for role in hh_role_ids(search.categories)
            )
            params.extend(("area", area_id) for area_id in search.area_ids)
            params.extend(("experience", value) for value in search.experience_ids)
            if search.remote_only:
                params.append(("work_format", "REMOTE"))

            try:
                response = self.session.get(
                    f"{self.config.base_url}/vacancies",
                    params=params,
                    timeout=self.config.timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                detail = str(exc)
                if isinstance(exc, requests.HTTPError) and exc.response is not None:
                    detail = exc.response.text[:300] or detail
                    if exc.response.status_code == 403:
                        detail += (
                            "; access was forbidden—check the hh.user_agent contact "
                            "and whether api.hh.ru is reachable from this network"
                        )
                raise HhApiError(f"HeadHunter request failed: {detail}") from exc

            items = payload.get("items", [])
            for item in items:
                yield vacancy_from_hh(item, query, search.categories)
                yielded += 1
                if yielded >= search.per_query:
                    return

            page += 1
            if not items or page >= int(payload.get("pages", 0)):
                return
