from dataclasses import replace
from datetime import UTC, datetime, timedelta

import requests

from job_search_automation.config import SearchConfig, freelance_search_defaults
from job_search_automation.filters import rejection_reason
from job_search_automation.freelancehunt import FreelancehuntClient, vacancy_from_project


def settings() -> SearchConfig:
    return freelance_search_defaults(SearchConfig(("Python",), (), (), (), True, True, 7, 20))


def project(**changes):
    published = datetime.now(UTC).isoformat()
    item = {
        "id": 1655813,
        "type": "project",
        "attributes": {
            "name": "Разработать Telegram-бота",
            "description": "Нужен бот на Python для обработки заявок.",
            "skills": [{"id": 180, "name": "Bot Development"}],
            "status": {"id": 11, "name": "Open for proposals"},
            "budget": {"amount": 12000, "currency": "UAH"},
            "is_remote_job": False,
            "is_personal": False,
            "location": {"country": {"name": "Ukraine"}},
            "published_at": published,
        },
        "links": {"self": {"web": "https://freelancehunt.com/project/bot/1655813.html"}},
    }
    item["attributes"].update(changes)
    return item


class Response:
    def __init__(self, data, status=200):
        self.data = data
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError("source unavailable")

    def json(self):
        return self.data


class Session:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.pages.pop(0)


def test_public_projects_use_skill_filter_and_keep_client_country():
    session = Session([
        Response({"data": [project()], "links": {"next": "page 2"}}),
        Response({"data": [], "links": {}}),
    ])
    items = FreelancehuntClient(session).search(settings())
    assert len(items) == 1
    assert items[0].source == "freelancehunt"
    assert items[0].kind == "freelance"
    assert items[0].location_scope == "Ukraine"
    assert items[0].pay_label == "12000 UAH"
    assert items[0].url == "https://freelancehunt.com/project/bot/1655813.html"
    assert rejection_reason(items[0], settings()) is None
    assert session.calls[0][1]["params"]["filter[skill_id]"]
    assert session.calls[1][1]["params"]["page[number]"] == 2


def test_closed_old_personal_and_vacancy_listings_are_skipped():
    cutoff = datetime.now(UTC) - timedelta(days=7)
    assert vacancy_from_project(project(status={"id": 21}), cutoff) is None
    assert vacancy_from_project(project(is_personal=True), cutoff) is None
    assert vacancy_from_project(project(is_remote_job=True), cutoff) is None
    assert vacancy_from_project(project(published_at="2020-01-01T00:00:00+00:00"), cutoff) is None
    assert vacancy_from_project(project(skills=[{"id": 43, "name": "Web Design"}]), cutoff) is None


def test_unsafe_link_and_irrelevant_title_are_not_presented_as_matches():
    cutoff = datetime.now(UTC) - timedelta(days=7)
    unsafe = project()
    unsafe["links"]["self"]["web"] = "https://example.com/fake"
    assert vacancy_from_project(unsafe, cutoff) is None
    design = project(name="Создать логотип", description="Нарисовать изображение.")
    item = vacancy_from_project(design, cutoff)
    assert item is not None
    assert rejection_reason(item, settings()) == "title and description do not mention the selected stack"
    assert rejection_reason(replace(item, title="Разработать бота"), settings()) is None
