from datetime import UTC, datetime
from email.utils import format_datetime

import pytest
import requests

from job_search_automation.config import SearchConfig
from job_search_automation.public_sources import RemotiveClient, RssClient, SourceError


class Response:
    def __init__(self, *, data=None, content=b"", status=200):
        self.data = data
        self.content = content
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError("source unavailable")

    def json(self):
        return self.data


class Session:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def get(self, *_args, **_kwargs):
        self.calls += 1
        return self.response


def settings():
    return SearchConfig(("Python",), (), (), (), True, True, 7, 20)


def test_remotive_fetches_once_and_preserves_location_restriction():
    session = Session(
        Response(
            data={
                "jobs": [
                    {
                        "id": 123,
                        "url": "https://remotive.com/remote-jobs/dev/python-123",
                        "title": "Python engineer",
                        "company_name": "Example",
                        "publication_date": datetime.now(UTC).isoformat(),
                        "candidate_required_location": "Europe",
                        "job_type": "contract",
                        "salary": "€100/day",
                        "description": "<p>Build software</p>",
                    }
                ]
            }
        )
    )
    items = RemotiveClient(session).search(settings())
    assert session.calls == 1
    assert items[0].kind == "freelance"
    assert items[0].location_scope == "Europe"
    assert items[0].pay_label == "€100/day"


@pytest.mark.parametrize(
    ("source", "url", "market", "kind"),
    [
        ("wwr", "https://weworkremotely.com/remote-jobs/python", "global", "freelance"),
        ("fl", "https://www.fl.ru/projects/123", "ru", "freelance"),
    ],
)
def test_rss_projects_have_stable_ids_and_source_links(source, url, market, kind):
    published = format_datetime(datetime.now(UTC))
    feed = f"""
    <rss><channel><item>
      <title>Example: Python project</title><link>{url}</link>
      <description>Build a service</description><pubDate>{published}</pubDate>
      <region>Europe</region><type>Contract</type>
    </item></channel></rss>
    """.encode()
    session = Session(Response(content=feed))
    first = RssClient(source, "https://example.test/feed", market, session=session).search(settings())
    second = RssClient(source, "https://example.test/feed", market, session=session).search(settings())
    assert first[0].source_id == second[0].source_id
    assert first[0].kind == kind
    assert first[0].url == url


def test_unavailable_rss_is_reported_not_parsed():
    source = RssClient("fl", "https://example.test/feed", "ru", session=Session(Response(status=403)))
    with pytest.raises(SourceError, match="недоступен"):
        source.search(settings())
