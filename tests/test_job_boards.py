from datetime import UTC, datetime

import requests

from job_search_automation.config import SearchConfig
from job_search_automation.filters import rejection_reason
from job_search_automation.job_boards import JobicyClient, RemoteOkClient


class Response:
    def __init__(self, data, status=200):
        self.data = data
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError("offline")

    def json(self):
        return self.data


class Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def settings() -> SearchConfig:
    return SearchConfig(
        queries=("Python",), excluded_keywords=(), area_ids=(), experience_ids=(),
        remote_only=True, strict_remote=True, days=7, per_query=20,
        categories=("software",), kinds=("job",), stack_keywords=("Python",),
    )


def test_remoteok_skips_metadata_and_unrelated_roles_and_keeps_attributed_link():
    date = datetime.now(UTC).isoformat()
    session = Session(Response([
        {"last_updated": 1, "legal": "Link back to Remote OK"},
        {"id": "123", "position": "Python Developer", "company": "Example",
         "date": date, "description": "<p>Build a Python API</p>", "location": "Europe",
         "url": "https://remoteOK.com/remote-jobs/python-123", "salary_min": 0,
         "salary_max": 0},
        {"id": "124", "position": "Marketing Manager", "company": "Example",
         "date": date, "description": "Python", "location": "Anywhere",
         "url": "https://remoteok.com/remote-jobs/marketing-124"},
    ]))
    items = RemoteOkClient(session).search(settings())
    assert len(items) == 1
    assert items[0].source == "remoteok"
    assert items[0].url == "https://remoteOK.com/remote-jobs/python-123"
    assert items[0].location_scope == "Europe"
    assert rejection_reason(items[0], settings()) is None
    assert session.calls[0][1]["params"] == {"tag": "dev"}


def test_jobicy_keeps_candidate_location_and_rejects_nonengineering_roles():
    date = datetime.now(UTC).isoformat()
    session = Session(Response({"jobs": [
        {"id": 11, "url": "https://jobicy.com/jobs/11", "jobTitle": "Python Backend Engineer",
         "companyName": "Example", "jobIndustry": ["Software Engineering"],
         "jobType": ["Full-Time"], "jobGeo": "UK", "jobLevel": "Senior",
         "jobDescription": "<p>Python services</p>", "pubDate": date},
        {"id": 12, "url": "https://jobicy.com/jobs/12", "jobTitle": "Marketing Engineer",
         "companyName": "Example", "jobIndustry": ["Marketing"],
         "jobDescription": "Python", "pubDate": date},
    ]}))
    items = JobicyClient(session).search(settings())
    assert len(items) == 1
    assert items[0].source == "jobicy"
    assert items[0].location_scope == "UK"
    assert items[0].employment == "Full-Time"
    assert rejection_reason(items[0], settings()) is None
    assert session.calls[0][1]["params"]["industry"] == "engineering"
