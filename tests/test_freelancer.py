from datetime import UTC, datetime, timedelta

import pytest
import requests

from job_search_automation.config import SearchConfig
from job_search_automation.freelancer import FreelancerClient, vacancy_from_project
from job_search_automation.public_sources import SourceError


def project(**overrides):
    item = {
        "id": 123,
        "status": "active",
        "seo_url": "python/Build-Python-Service",
        "title": "Build Python service",
        "submitdate": int(datetime.now(UTC).timestamp()),
        "preview_description": "Build an API",
        "type": "fixed",
        "local": False,
        "currency": {"code": "EUR"},
        "budget": {"minimum": 100, "maximum": 500},
        "location": {"country": {"name": "Germany"}},
    }
    item.update(overrides)
    return item


class Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError("unavailable")

    def json(self):
        return self.payload


class Session:
    def __init__(self, response):
        self.response = response
        self.params = []

    def get(self, _url, *, params, timeout):
        assert timeout == 25
        self.params.append(params)
        return self.response


def settings():
    return SearchConfig(("Python",), (), (), (), True, True, 7, 20)


def test_freelancer_maps_public_project_and_safe_link():
    session = Session(Response({"status": "success", "result": {"projects": [project()]}}))
    items = FreelancerClient(session).search(settings())
    assert session.params[0]["query"] == "Python"
    assert len(items) == 1
    assert items[0].kind == "freelance"
    assert items[0].is_remote()
    assert items[0].pay_label == "100–500 EUR"
    assert items[0].location_scope == "Germany"
    assert items[0].url == "https://www.freelancer.com/projects/python/Build-Python-Service/details"


def test_freelancer_rejects_old_local_and_unsafe_projects():
    cutoff = datetime.now(UTC) - timedelta(days=7)
    assert vacancy_from_project(project(submitdate=1), "Python", cutoff) is None
    assert vacancy_from_project(project(seo_url="//malicious.test/path"), "Python", cutoff) is None
    assert vacancy_from_project(project(status="closed"), "Python", cutoff) is None
    local = vacancy_from_project(project(local=True), "Python", cutoff)
    assert local is not None and not local.is_remote()


def test_freelancer_reports_api_errors():
    session = Session(Response({"status": "error"}))
    with pytest.raises(SourceError, match="ошибку"):
        FreelancerClient(session).search(settings())


def test_freelancer_category_mode_requests_taxonomy_jobs():
    selected = project(jobs=[{"id": 13, "name": "Python"}])
    session = Session(Response({"status": "success", "result": {"projects": [selected]}}))
    focused = SearchConfig((), (), (), (), True, True, 7, 20, categories=("software",))
    items = FreelancerClient(session).search(focused)
    assert len(items) == 1
    assert items[0].categories == ("software",)
    assert "query" not in session.params[0]
    assert 13 in session.params[0]["jobs[]"]
